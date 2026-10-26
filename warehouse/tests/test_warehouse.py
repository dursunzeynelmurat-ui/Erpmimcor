from decimal import Decimal as D

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Unit
from core.exceptions import BusinessRuleError
from core.models import AuditLog, QualityStatus, ValueSource
from core.tests.factories import World
from orders import services as orders
from orders.models import LineStatus
from warehouse import services as wh
from warehouse.models import (MovementKind, Pallet, PalletMovement, PalletStatus, ReservationStatus, ShipmentStatus,
                              WeightStatus)


class SingleProductPalletTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_cannot_add_other_product_to_pallet(self):
        pallet = self.w.stock_pallet(product=self.w.prod_a)
        wo_b = self.w.started_wo(product=self.w.prod_b, stock=True)
        coil = self.w.coil(wo_b)
        with self.assertRaises(BusinessRuleError):
            wh.add_production_to_pallet(self.w.foreman, pallet, wo_b, 5, coils=[coil])
        pallet.refresh_from_db()
        self.assertEqual(pallet.box_count, 40)
        coil.refresh_from_db()
        self.assertEqual(coil.status, "kullanilabilir")  # bobin de palete bağlanmadı (atomik geri alma)

    def test_cannot_transfer_to_pallet_of_other_product(self):
        pa = self.w.stock_pallet(product=self.w.prod_a)
        pb = self.w.stock_pallet(product=self.w.prod_b)
        with self.assertRaises(BusinessRuleError):
            wh.transfer_boxes(self.w.depot, pa, 5, "Birleştirme", target=pb)
        pa.refresh_from_db()
        pb.refresh_from_db()
        self.assertEqual((pa.box_count, pb.box_count), (40, 40))
        self.assertFalse(PalletMovement.objects.filter(kind__startswith="aktarim").exists())

    def test_box_count_not_editable_directly(self):
        self.assertFalse(Pallet._meta.get_field("box_count").editable)


class NegativeStockTests(TestCase):
    def setUp(self):
        self.w = World()
        self.pallet = self.w.stock_pallet()

    def test_transfer_more_than_available_rejected_atomically(self):
        target = self.w.stock_pallet()
        with self.assertRaises(BusinessRuleError):
            wh.transfer_boxes(self.w.depot, self.pallet, 41, "x", target=target)
        self.pallet.refresh_from_db()
        target.refresh_from_db()
        self.assertEqual((self.pallet.box_count, target.box_count), (40, 40))

    def test_database_rejects_negative_box_count(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Pallet.objects.filter(pk=self.pallet.pk).update(box_count=-1)

    def test_transfer_moves_both_sides(self):
        target = wh.transfer_boxes(self.w.depot, self.pallet, 15, "Yeni palete bölme")
        self.pallet.refresh_from_db()
        self.assertEqual((self.pallet.box_count, target.box_count), (25, 15))
        out = self.pallet.movements.get(kind=MovementKind.TRANSFER_OUT)
        inn = target.movements.get(kind=MovementKind.TRANSFER_IN)
        self.assertEqual(out.group, inn.group)

    def test_reserved_boxes_cannot_be_transferred(self):
        line = self.w.order(qty=D("30")).lines.get()
        wh.reserve(self.w.sales, self.pallet, line, 30)
        with self.assertRaises(BusinessRuleError):
            wh.transfer_boxes(self.w.depot, self.pallet, 11, "x")

    def test_count_adjust_requires_reason(self):
        with self.assertRaises(BusinessRuleError):
            wh.adjust_count(self.w.depot, self.pallet, 38, "")
        wh.adjust_count(self.w.depot, self.pallet, 38, "Sayım farkı")
        self.pallet.refresh_from_db()
        self.assertEqual(self.pallet.box_count, 38)

    def test_damage_separation_creates_blocked_pallet(self):
        damaged = wh.separate_damaged(self.w.depot, self.pallet, 3, "Forklift hasarı")
        self.assertEqual(damaged.quality_status, QualityStatus.BLOCKED)
        self.assertEqual(damaged.box_count, 3)
        self.assertEqual(damaged.product, self.pallet.product)


class ReservationAndShipmentTests(TestCase):
    """Demo B: 40 kutulu stok paletinden yeni siparişe 10 kutu ayrılır ve sevk edilir."""

    def setUp(self):
        self.w = World()
        self.pallet = self.w.stock_pallet(boxes=40)
        self.order = self.w.order(qty=D("10"))
        self.line = self.order.lines.get()

    def test_reservation_does_not_reduce_physical_stock(self):
        res = wh.reserve(self.w.sales, self.pallet, self.line, 10)
        self.pallet.refresh_from_db()
        self.assertEqual(self.pallet.box_count, 40)
        self.assertEqual(self.pallet.available_boxes, 30)
        self.assertEqual(res.status, ReservationStatus.ACTIVE)

    def test_reservation_product_must_match(self):
        other = self.w.order(product=self.w.prod_b, qty=D("5")).lines.get()
        with self.assertRaises(BusinessRuleError):
            wh.reserve(self.w.sales, self.pallet, other, 5)

    def test_partial_shipment_keeps_pallet_identity(self):
        res = wh.reserve(self.w.sales, self.pallet, self.line, 10)
        shipment = wh.create_shipment(self.w.depot, self.order)
        sl = wh.add_shipment_line(self.w.depot, shipment, res, 10)
        wh.confirm_shipment(self.w.depot, shipment)
        pallet = Pallet.objects.get(pk=self.pallet.pk)
        self.assertEqual(pallet.code, self.pallet.code)
        self.assertEqual(pallet.box_count, 30)
        self.assertEqual(pallet.status, PalletStatus.IN_STOCK)
        mv = pallet.movements.get(kind=MovementKind.SHIPMENT)
        self.assertEqual((mv.quantity, mv.balance_after, mv.shipment_line), (-10, 30, sl))
        res.refresh_from_db()
        self.assertEqual(res.status, ReservationStatus.SHIPPED)
        self.line.refresh_from_db()
        self.assertEqual(self.line.status, LineStatus.SHIPPED)
        self.assertEqual(Pallet.objects.count(), 1)
        self.assertTrue(AuditLog.objects.filter(action="pallet.sevk", object_code=pallet.code, user=self.w.depot).exists())
        self.assertEqual(pallet.weight_status, WeightStatus.REWEIGH)

    def test_duplicate_shipment_confirm_rejected(self):
        res = wh.reserve(self.w.sales, self.pallet, self.line, 10)
        shipment = wh.create_shipment(self.w.depot, self.order)
        wh.add_shipment_line(self.w.depot, shipment, res, 10)
        wh.confirm_shipment(self.w.depot, shipment)
        with self.assertRaises(BusinessRuleError):
            wh.confirm_shipment(self.w.depot, shipment)
        self.assertEqual(PalletMovement.objects.filter(kind=MovementKind.SHIPMENT).count(), 1)
        # aynı rezervasyon ikinci bir sevkiyata da eklenemez
        s2 = wh.create_shipment(self.w.depot, self.order)
        with self.assertRaises(BusinessRuleError):
            wh.add_shipment_line(self.w.depot, s2, res, 10)

    def test_duplicate_confirm_request_is_idempotent(self):
        res = wh.reserve(self.w.sales, self.pallet, self.line, 10)
        shipment = wh.create_shipment(self.w.depot, self.order)
        wh.add_shipment_line(self.w.depot, shipment, res, 10)
        self.client.force_login(self.w.depot)
        url = reverse("warehouse:shipment_confirm", args=[shipment.pk])
        self.client.post(url, {"idem_key": "ship-1"})
        resp = self.client.post(url, {"idem_key": "ship-1"}, follow=True)
        self.assertContains(resp, "daha önce kaydedilmişti")
        self.assertEqual(PalletMovement.objects.filter(kind=MovementKind.SHIPMENT).count(), 1)

    def test_same_reservation_cannot_be_overbooked_in_drafts(self):
        res = wh.reserve(self.w.sales, self.pallet, self.line, 10)
        s1 = wh.create_shipment(self.w.depot, self.order)
        wh.add_shipment_line(self.w.depot, s1, res, 6)
        s2 = wh.create_shipment(self.w.depot, self.order)
        with self.assertRaises(BusinessRuleError):
            wh.add_shipment_line(self.w.depot, s2, res, 5)

    def test_non_ok_quality_cannot_ship(self):
        res = wh.reserve(self.w.sales, self.pallet, self.line, 10)
        shipment = wh.create_shipment(self.w.depot, self.order)
        wh.add_shipment_line(self.w.depot, shipment, res, 10)
        wh.set_pallet_quality(self.w.depot, self.pallet, QualityStatus.BLOCKED, "Şikâyet incelemesi")
        with self.assertRaises(BusinessRuleError):
            wh.confirm_shipment(self.w.depot, shipment)
        self.pallet.refresh_from_db()
        self.assertEqual(self.pallet.box_count, 40)
        shipment.refresh_from_db()
        self.assertEqual(shipment.status, ShipmentStatus.DRAFT)

    def test_pending_quality_cannot_ship(self):
        pallet = self.w.stock_pallet(quality_ok=False)
        res = wh.reserve(self.w.sales, pallet, self.line, 10)
        shipment = wh.create_shipment(self.w.depot, self.order)
        wh.add_shipment_line(self.w.depot, shipment, res, 10)
        with self.assertRaises(BusinessRuleError):
            wh.confirm_shipment(self.w.depot, shipment)

    def test_cannot_reserve_more_than_available(self):
        wh.reserve(self.w.sales, self.pallet, self.line, 10)
        other = self.w.order(qty=D("31")).lines.get()
        with self.assertRaises(BusinessRuleError):
            wh.reserve(self.w.sales, self.pallet, other, 31)

    def test_permissions(self):
        with self.assertRaises(PermissionDenied):
            wh.reserve(self.w.foreman, self.pallet, self.line, 1)
        with self.assertRaises(PermissionDenied):
            wh.create_shipment(self.w.sales, self.order)
        with self.assertRaises(PermissionDenied):
            wh.transfer_boxes(self.w.sales, self.pallet, 1, "x")
        with self.assertRaises(PermissionDenied):
            wh.set_pallet_quality(self.w.depot, self.pallet, QualityStatus.OK, "depo uygun yapamaz")


class WeighingTests(TestCase):
    def setUp(self):
        self.w = World()
        wo = self.w.started_wo(stock=True)
        self.coil = self.w.coil(wo, D("110"))
        self.pallet = wh.create_pallet_from_production(self.w.foreman, wo, 40, coils=[self.coil])

    def _labels(self, weighing):
        return {label: (value, src) for label, value, src in wh.weight_breakdown(weighing, self.pallet)}

    def test_gross_required_on_acceptance(self):
        with self.assertRaises(BusinessRuleError):
            wh.accept_pallet(self.w.depot, self.pallet, {"gross_kg": None, "pallet_tare_kg": D("20")})
        self.pallet.refresh_from_db()
        self.assertEqual(self.pallet.status, PalletStatus.AWAITING_ACCEPTANCE)

    def test_gross_only_shows_no_net(self):
        wh.accept_pallet(self.w.depot, self.pallet, {"gross_kg": D("135")})
        labels = self._labels(self.pallet.current_weighing)
        self.assertEqual(labels["Brüt ağırlık"], (D("135"), ValueSource.MEASURED))
        self.assertFalse(any("hariç" in k or "Net" in k for k in labels))
        self.assertEqual(labels["Bobin kayıtlarından ürün ağırlığı"][1], ValueSource.ESTIMATED)

    def test_only_pallet_tare_is_not_net(self):
        wh.accept_pallet(self.w.depot, self.pallet, {"gross_kg": D("135"), "pallet_tare_kg": D("22.5")})
        labels = self._labels(self.pallet.current_weighing)
        key = [k for k in labels if k.startswith("Palet hariç ağırlık")][0]
        self.assertEqual(labels[key], (D("112.5"), ValueSource.CALCULATED))
        self.assertFalse(any(k.startswith("Net") for k in labels))

    def test_missing_tares_not_counted_as_zero(self):
        wh.accept_pallet(self.w.depot, self.pallet, {"gross_kg": D("135"), "pallet_tare_kg": D("20"),
                                                     "box_tare_kg": D("0.25")})
        labels = self._labels(self.pallet.current_weighing)
        key = [k for k in labels if "hariç" in k][0]
        self.assertIn("masura darası bilinmiyor", key)
        self.assertEqual(labels[key][0], D("105"))  # 135 - 20 - 0.25*40

    def test_all_tares_give_net_and_explicit_zero_is_valid(self):
        wh.accept_pallet(self.w.depot, self.pallet, {"gross_kg": D("135"), "pallet_tare_kg": D("20"),
                                                     "box_tare_kg": D("0.25"), "core_tare_kg": D("0"),
                                                     "roll_weights": [D("2.5"), D("2.6")]})
        labels = self._labels(self.pallet.current_weighing)
        self.assertEqual(labels["Net ürün ağırlığı"], (D("105"), ValueSource.CALCULATED))
        self.assertEqual(labels["Tekil rulo ağırlıkları toplamı (2 rulo)"][0], D("5.1"))

    def test_tares_cannot_exceed_gross(self):
        with self.assertRaises(BusinessRuleError):
            wh.accept_pallet(self.w.depot, self.pallet, {"gross_kg": D("20"), "pallet_tare_kg": D("25")})

    def test_content_change_keeps_old_weighing_and_marks_reweigh(self):
        wh.accept_pallet(self.w.depot, self.pallet, {"gross_kg": D("135"), "pallet_tare_kg": D("20")})
        first = self.pallet.current_weighing
        wh.adjust_count(self.w.depot, self.pallet, 39, "Eksik kutu")
        self.pallet.refresh_from_db()
        self.assertEqual(self.pallet.weight_status, WeightStatus.REWEIGH)
        self.assertIsNone(self.pallet.current_weighing)
        first.refresh_from_db()
        self.assertEqual(first.gross_kg, D("135"))  # geçmişte korunuyor
        wh.reweigh_pallet(self.w.depot, self.pallet, {"gross_kg": D("132")})
        self.pallet.refresh_from_db()
        self.assertEqual(self.pallet.weight_status, WeightStatus.CURRENT)
        self.assertEqual(self.pallet.weighings.count(), 2)


class LabelAndQrTests(TestCase):
    def setUp(self):
        self.w = World()
        self.pallet = self.w.stock_pallet()

    def test_reprint_does_not_create_pallet(self):
        before = Pallet.objects.count()
        wh.record_label_print(self.w.depot, self.pallet)
        lp = wh.record_label_print(self.w.depot, self.pallet)
        self.assertTrue(lp.is_reprint)
        self.assertEqual(Pallet.objects.count(), before)
        self.assertEqual(self.pallet.label_prints.count(), 2)

    def test_qr_token_is_unguessable_and_opens_record_without_movement(self):
        self.assertGreaterEqual(len(self.pallet.qr_token), 30)
        self.assertNotIn(self.pallet.code, self.pallet.qr_token)
        movements = PalletMovement.objects.count()
        self.client.force_login(self.w.depot)
        resp = self.client.get(reverse("warehouse:qr_open", args=[self.pallet.qr_token]))
        self.assertRedirects(resp, reverse("warehouse:pallet_detail", args=[self.pallet.pk]))
        self.assertEqual(PalletMovement.objects.count(), movements)

    def test_qr_requires_login(self):
        resp = self.client.get(reverse("warehouse:qr_open", args=[self.pallet.qr_token]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/giris/", resp["Location"])

    def test_wrong_token_404(self):
        self.client.force_login(self.w.depot)
        self.assertEqual(self.client.get(reverse("warehouse:qr_open", args=["tahmin-edilen"])).status_code, 404)

    def test_qr_payload_contains_only_link(self):
        from warehouse.views import qr_payload
        self.client.force_login(self.w.depot)
        resp = self.client.get(reverse("warehouse:pallet_label", args=[self.pallet.pk]))
        payload = qr_payload(resp.wsgi_request, self.pallet)
        self.assertEqual(payload, f"http://testserver/q/{self.pallet.qr_token}/")


class WarehousePagesTests(TestCase):
    def test_pages_render_for_all_roles(self):
        w = World()
        pallet = w.stock_pallet()
        order = w.order(qty=D("10"))
        res = wh.reserve(w.sales, pallet, order.lines.get(), 10)
        shipment = wh.create_shipment(w.depot, order)
        wh.add_shipment_line(w.depot, shipment, res, 10)
        wo = pallet.work_order
        coil = wo.coils.first()
        urls = [reverse("warehouse:pallet_list"), reverse("warehouse:pallet_detail", args=[pallet.pk]),
                reverse("warehouse:pallet_label", args=[pallet.pk]), reverse("warehouse:shipment_list"),
                reverse("warehouse:shipment_detail", args=[shipment.pk]), reverse("orders:order_detail", args=[order.pk]),
                reverse("production:workorder_detail", args=[wo.pk]), reverse("production:coil_detail", args=[coil.pk]),
                reverse("orders:progress_list")]
        for u in (w.admin, w.sales, w.planner, w.foreman, w.depot):
            self.client.force_login(u)
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 200, f"{u.role} {url}")

    def test_sales_progress_shows_produced_and_shipped(self):
        from orders.selectors import line_progress
        from production import services as prod
        w = World()
        line = w.approved_line(qty=D("20"))
        wo = w.started_wo(line=line)
        coil = w.coil(wo)
        pallet = wh.create_pallet_from_production(w.foreman, wo, 20, coils=[coil])
        p = line_progress(line)
        self.assertEqual(p["produced"], D("20"))
        self.assertIn(wo.code, p["stage"])
        self.assertGreaterEqual(p["last_update"], pallet.created_at)
        with self.assertRaises(PermissionDenied):
            prod.record_coil(w.sales, wo.operations.get(), weight_kg=D("1"))
