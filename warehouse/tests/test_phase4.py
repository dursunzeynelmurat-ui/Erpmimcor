"""Aşama 4: depo yeri geçmişi, rezervasyon azaltma, sevkiyat aşamaları (hazırlık → QR yükleme kontrolü → sevk onayı
→ çıkış), çıkış öncesi geri alma, iade, etiket basım talebi/sonucu, stok seviyesi ihtiyacı ve iş listeleri."""
import uuid
from decimal import Decimal as D

from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from accounts.models import Role, User
from core.exceptions import BusinessRuleError
from core.models import QualityStatus
from core.tests.factories import PASSWORD, World, load
from orders.models import LineStatus, OrderStatus
from warehouse import services as wh
from warehouse.models import (LabelKind, MovementKind, Pallet, PalletMovement, PrintStatus, ReservationStatus,
                              ScanResult, ShipmentStatus)


class LocationAndReservationTests(TestCase):
    def setUp(self):
        self.w = World()
        self.pallet = self.w.stock_pallet(boxes=40)

    def test_location_change_keeps_history(self):
        wh.change_location(self.w.depot, self.pallet, "B-07", "Raf düzeni")
        wh.change_location(self.w.depot, self.pallet, "C-01")
        self.pallet.refresh_from_db()
        self.assertEqual(self.pallet.location, "C-01")
        self.assertEqual([c.new_location for c in self.pallet.location_changes.order_by("at", "id")], ["B-07", "C-01"])
        with self.assertRaisesMessage(BusinessRuleError, "zaten"):
            wh.change_location(self.w.depot, self.pallet, "C-01")

    def test_reduce_reservation_respects_shipped_and_prepared(self):
        line = self.w.approved_line(qty=D("20"))
        res = wh.reserve(self.w.depot, self.pallet, line, 20)
        shipment = wh.create_shipment(self.w.depot, line.order)
        wh.add_shipment_line(self.w.depot, shipment, res, 8)
        with self.assertRaisesMessage(BusinessRuleError, "arasında"):
            wh.reduce_reservation(self.w.depot, res, 5, "Müşteri azalttı")
        wh.reduce_reservation(self.w.depot, res, 12, "Müşteri azalttı")
        res.refresh_from_db()
        self.assertEqual((res.quantity, res.status), (12, ReservationStatus.ACTIVE))
        self.assertEqual(self.pallet.available_boxes, 28)


class ShipmentStageTests(TestCase):
    def setUp(self):
        self.w = World()
        self.pallet = self.w.stock_pallet(boxes=40)
        self.other = self.w.stock_pallet(boxes=10)
        self.order = self.w.order(qty=D("10"))
        self.line = self.order.lines.get()
        self.res = wh.reserve(self.w.depot, self.pallet, self.line, 10)
        self.shipment = wh.create_shipment(self.w.depot, self.order, "34 KRG 10")
        wh.add_shipment_line(self.w.depot, self.shipment, self.res, 10)

    def test_confirm_requires_prepare_and_complete_load_check(self):
        with self.assertRaisesMessage(BusinessRuleError, "hazırlandı"):
            wh.confirm_shipment(self.w.depot, self.shipment)
        wh.prepare_shipment(self.w.depot, self.shipment)
        with self.assertRaises(BusinessRuleError):
            wh.add_shipment_line(self.w.depot, self.shipment, self.res, 1)
        self.assertEqual(wh.scan_pallet(self.w.depot, self.shipment, self.other.qr_token).result, ScanResult.WRONG)
        self.assertEqual(wh.scan_pallet(self.w.depot, self.shipment, "bilinmeyen-qr").result, ScanResult.UNKNOWN)
        with self.assertRaisesMessage(BusinessRuleError, self.pallet.code):
            wh.confirm_shipment(self.w.depot, self.shipment)
        url = "https://ornek.local" + reverse("warehouse:qr_open", args=[self.pallet.qr_token])
        self.assertEqual(wh.scan_pallet(self.w.depot, self.shipment, url).result, ScanResult.OK)
        self.assertEqual(wh.scan_pallet(self.w.depot, self.shipment, self.pallet.code).result, ScanResult.DUPLICATE)
        check = wh.load_check(self.shipment)
        self.assertEqual((len(check["missing"]), len(check["wrong"]), len(check["duplicates"])), (0, 2, 1))
        self.pallet.refresh_from_db()
        self.assertEqual(self.pallet.box_count, 40)  # okutma stok değiştirmez
        wh.confirm_shipment(self.w.depot, self.shipment)
        self.pallet.refresh_from_db()
        self.assertEqual(self.pallet.box_count, 30)

    def test_reopen_invalidates_previous_scans(self):
        load(self.w.depot, self.shipment)
        wh.reopen_shipment(self.w.depot, self.shipment, "Palet değişecek")
        wh.prepare_shipment(self.w.depot, self.shipment)
        self.assertEqual(len(wh.load_check(self.shipment)["missing"]), 1)

    def test_undo_before_exit_restores_stock_reservation_and_status(self):
        wh.confirm_shipment(self.w.depot, load(self.w.depot, self.shipment))
        self.line.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual((self.line.status, self.order.status), (LineStatus.SHIPPED, OrderStatus.DONE))
        with self.assertRaisesMessage(BusinessRuleError, "gerekçe"):
            wh.undo_shipment(self.w.depot, self.shipment, "")
        wh.undo_shipment(self.w.depot, self.shipment, "Araç arızalandı, yük indirildi")
        self.pallet.refresh_from_db()
        self.res.refresh_from_db()
        self.line.refresh_from_db()
        self.order.refresh_from_db()
        self.shipment.refresh_from_db()
        self.assertEqual(self.pallet.box_count, 40)
        self.assertEqual((self.res.status, self.res.shipped_quantity), (ReservationStatus.ACTIVE, 0))
        self.assertNotEqual(self.line.status, LineStatus.SHIPPED)
        self.assertEqual(self.order.status, OrderStatus.OPEN)
        self.assertEqual(self.shipment.status, ShipmentStatus.PREPARED)
        kinds = list(self.pallet.movements.order_by("at", "id").values_list("kind", flat=True))
        self.assertEqual(kinds[-2:], [MovementKind.SHIPMENT, MovementKind.SHIPMENT_UNDO])  # geçmiş korunur
        wh.confirm_shipment(self.w.depot, load(self.w.depot, self.shipment))  # yeniden okutma ile tekrar sevk
        wh.mark_exited(self.w.depot, self.shipment)
        with self.assertRaisesMessage(BusinessRuleError, "iade"):
            wh.undo_shipment(self.w.depot, self.shipment, "Geç kaldı")

    def test_return_goes_to_new_pending_pallet_linked_to_shipment(self):
        wh.confirm_shipment(self.w.depot, load(self.w.depot, self.shipment))
        wh.mark_exited(self.w.depot, self.shipment)
        sl = self.shipment.lines.get()
        with self.assertRaisesMessage(BusinessRuleError, "en fazla"):
            wh.receive_return(self.w.depot, sl, 11, "Ezik")
        receipt = wh.receive_return(self.w.depot, sl, 3, "3 kutu ezik geldi")
        p = receipt.pallet
        self.assertEqual((p.box_count, p.quality_status, p.product_id), (3, QualityStatus.PENDING, self.pallet.product_id))
        self.assertEqual(receipt.shipment_line, sl)
        self.assertEqual(wh.returnable_quantity(sl), 7)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.DONE)  # sevk geçmişi değişmez
        other_order = self.w.order(qty=D("3"))
        res = wh.reserve(self.w.depot, p, other_order.lines.get(), 3)
        s2 = wh.create_shipment(self.w.depot, other_order)
        wh.add_shipment_line(self.w.depot, s2, res, 3)
        with self.assertRaisesMessage(BusinessRuleError, "uygun"):
            wh.confirm_shipment(self.w.depot, load(self.w.depot, s2))  # kalite kararı olmadan iade sevk edilemez

    def test_pages_render_each_stage(self):
        self.client.force_login(self.w.depot)
        url = reverse("warehouse:shipment_detail", args=[self.shipment.pk])
        self.assertContains(self.client.get(url), "Hazırlandı – yükleme kontrolüne geç")
        self.client.post(reverse("warehouse:shipment_prepare", args=[self.shipment.pk]), {"idem_key": uuid.uuid4().hex})
        self.assertContains(self.client.get(url), "Yükleme kontrolü")
        self.client.post(reverse("warehouse:shipment_scan", args=[self.shipment.pk]),
                         {"idem_key": uuid.uuid4().hex, "raw": self.pallet.code})
        self.assertContains(self.client.get(url), "Eksik yok")
        self.client.post(reverse("warehouse:shipment_confirm", args=[self.shipment.pk]), {"idem_key": uuid.uuid4().hex})
        self.assertContains(self.client.get(url), "Araç çıktı")
        self.client.post(reverse("warehouse:shipment_exit", args=[self.shipment.pk]), {"idem_key": uuid.uuid4().hex})
        page = self.client.get(url)
        self.assertContains(page, "İade al")


class LabelTests(TestCase):
    def setUp(self):
        self.w = World()
        self.pallet = self.w.stock_pallet(boxes=12)

    def test_print_request_is_not_success_and_creates_no_stock(self):
        pallets, moves = Pallet.objects.count(), PalletMovement.objects.count()
        lp = wh.request_label_print(self.w.depot, self.pallet, LabelKind.PACKAGE, 12)
        again = wh.request_label_print(self.w.depot, self.pallet, LabelKind.PACKAGE, 12)
        self.assertEqual((lp.status, again.is_reprint), (PrintStatus.REQUESTED, True))
        self.assertEqual((Pallet.objects.count(), PalletMovement.objects.count()), (pallets, moves))
        wh.confirm_label_print(self.w.depot, lp, printed=False)
        lp.refresh_from_db()
        self.assertEqual(lp.status, PrintStatus.FAILED)
        with self.assertRaises(BusinessRuleError):
            wh.confirm_label_print(self.w.depot, lp, printed=True)

    @override_settings(APP_BASE_URL="https://uretim.mimcor.example")
    def test_bulk_package_labels_and_configured_qr_base(self):
        self.client.force_login(self.w.depot)
        url = reverse("warehouse:package_labels", args=[self.pallet.pk])
        resp = self.client.post(url, {"idem_key": uuid.uuid4().hex, "copies": "12"}, follow=True)
        self.assertContains(resp, "12/12")
        self.assertContains(resp, "1/12")
        self.assertNotContains(resp, "APP_BASE_URL")
        from warehouse.views import qr_payload
        self.assertTrue(qr_payload(None, self.pallet).startswith("https://uretim.mimcor.example/q/"))

    def test_missing_base_url_warns(self):
        self.client.force_login(self.w.depot)
        self.assertContains(self.client.get(reverse("warehouse:pallet_label", args=[self.pallet.pk])), "APP_BASE_URL")


class StockLevelAndTaskTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_needs_list_subtracts_free_stock_and_open_stock_orders(self):
        self.w.stock_pallet(boxes=40)  # müşteri stoğu iş emri başlamış/açık durumda (hedef 40)
        wh.set_stock_level(self.w.planner, self.w.prod_a, 50, 120)
        with self.assertRaises(BusinessRuleError):
            wh.set_stock_level(self.w.planner, self.w.prod_a, 50, 10)
        row = wh.planning_needs(self.w.planner)[0]
        self.assertEqual((row["available"], row["planned"], row["need"]), (40, 40, 40))
        self.client.force_login(self.w.planner)
        self.assertContains(self.client.get(reverse("warehouse:stock_needs")), "40 kutu")
        self.assertContains(self.client.get(reverse("core:home")), "Alt stok seviyesinin altındaki ürünler")

    def test_task_lists_follow_permissions(self):
        from corrections import services as cs
        manager = User.objects.create_user("9601", PASSWORD, full_name="Üretim Yetkilisi", role=Role.PRODUCTION_MANAGER)
        approver = User.objects.create_user("9602", PASSWORD, full_name="Ambar Yetkilisi", role=Role.WAREHOUSE_MANAGER)
        pallet = self.w.stock_pallet(boxes=10)
        cs.request_correction(manager, "sayim", pallet, D("9"), "Eksik kutu")
        self.client.force_login(approver)
        self.assertContains(self.client.get(reverse("core:home")), "Onay bekleyen düzeltme talepleri")
        self.client.force_login(self.w.sales)
        self.assertNotContains(self.client.get(reverse("core:home")), "Onay bekleyen düzeltme talepleri")


class OrderLineActionsRenderTests(TestCase):
    def test_line_actions_render_without_nested_links(self):
        w = World()
        line = w.approved_line()
        self.client.force_login(w.admin)
        html = self.client.get(reverse("orders:order_detail", args=[line.order.pk])).content.decode()
        self.assertIn("Miktar / termin değiştir</a>", html)
        self.assertIn("Teknik uygunluk istisnası</a>", html)
        self.assertNotIn('">{% if', html)
        self.assertNotIn('<a class="btn" href="/siparisler/kalem/{}/degistir/">\n'.format(line.pk), html)


class ShippedMigrationTests(TransactionTestCase):
    """Önceki sürümdeki 'sevk edildi' kayıtlar fiziksel olarak çıkmış sayılır (geri alınamaz)."""

    def test_old_shipped_become_exited(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        before = [("warehouse", "0002_lifecycle"), ("accounts", "0004_remove_legacy_role"),
                  ("catalog", "0007_product_package"), ("orders", "0004_exceptions")]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        user = apps.get_model("accounts", "User").objects.create(personnel_no="9701", full_name="Eski", password="x")
        cust = apps.get_model("catalog", "Customer").objects.create(code="ESK", name="Eski Kurgu")
        order = apps.get_model("orders", "SalesOrder").objects.create(code="SIP-ESKI", customer=cust, order_date="2026-01-01",
                                                                     status="acik", created_by=user)
        apps.get_model("warehouse", "Shipment").objects.create(code="SVK-ESKI", order=order, status="sevk_edildi",
                                                              created_by=user, shipped_by=user,
                                                              shipped_at="2026-01-02T10:00:00Z")
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        from warehouse.models import Shipment
        s = Shipment.objects.get(code="SVK-ESKI")
        self.assertEqual(s.status, ShipmentStatus.EXITED)
        self.assertIsNotNone(s.exited_at)
