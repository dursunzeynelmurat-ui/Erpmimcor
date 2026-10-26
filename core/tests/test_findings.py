"""Aşama 3 regresyonları: inceleme bulguları 1, 2, 3, 6, 8, 9, 11 ve uygun palete onaysız içerik eklenmesi.
Her test önce düzeltme öncesi kodda başarısız olduğu gösterilerek yazıldı (bkz. HANDOFF.md)."""
import threading
from decimal import Decimal as D

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from catalog import services as cat
from catalog.models import Machine, Material, MaterialKind, Unit
from core.exceptions import BusinessRuleError
from core.models import QualityStatus
from accounts.models import Role, User
from core.tests.factories import PASSWORD, World, load


def production_manager():
    """Üretim yetkilisi şablonu: üretim kaydı + reçete dışı malzeme sapması yetkisi."""
    return User.objects.filter(personnel_no="9431").first() or User.objects.create_user(
        "9431", PASSWORD, full_name="Test Üretim Yetkilisi", role=Role.PRODUCTION_MANAGER)
from orders import services as orders
from orders.models import LineStatus
from production import lifecycle
from production import services as prod
from production.selectors import produced_quantity_for_line
from warehouse import services as wh


def stock_pallet_with_revision(w, revision, boxes=40):
    """Belirli teknik revizyonla müşteri stoğu için üretilmiş, kabul edilmiş, uygun palet."""
    wo = prod.create_work_order(w.planner, product=revision.recipe.product, revision=revision,
                                machines=[w.free_extruder()], target_quantity=D(boxes), unit=Unit.BOX, foreman=w.foreman)
    lifecycle.release(w.planner, wo)
    prod.start_work_order(w.foreman, wo)
    coil = w.coil(wo)
    pallet = wh.create_pallet_from_production(w.foreman, wo, boxes, coils=[coil])
    wh.accept_pallet(w.depot, pallet, {"gross_kg": D("125")})
    wh.set_pallet_quality(w.foreman, pallet, QualityStatus.OK, "Kontrol edildi")
    pallet.refresh_from_db()
    return pallet


class Finding1RevisionCompatibilityTests(TestCase):
    """30 µ onaylı kaleme 70 µ stok paleti ayrılamaz / sevk edilemez; yalnız yetkili istisnayla."""

    def setUp(self):
        self.w = World()
        self.line = self.w.approved_line(qty=D("10"))  # Rev.1 = 30 µ
        self.rev70 = cat.create_revision(self.w.planner, self.w.prod_a, self.w.spec(thickness_micron=D("70")),
                                         self.w.mix(), reason="Kalın seri (kurgusal)")
        self.pallet70 = stock_pallet_with_revision(self.w, self.rev70)

    def test_incompatible_pallet_cannot_be_reserved(self):
        with self.assertRaisesMessage(BusinessRuleError, "teknik"):
            wh.reserve(self.w.depot, self.pallet70, self.line, 10)

    def test_same_signature_new_revision_number_is_compatible(self):
        rev30b = cat.create_revision(self.w.planner, self.w.prod_a, self.w.spec(), self.w.mix(), reason="Tekrar 30 µ")
        pallet = stock_pallet_with_revision(self.w, rev30b)
        wh.reserve(self.w.depot, pallet, self.line, 10)

    def test_exception_needs_authority_and_reason_then_allows(self):
        with self.assertRaises(Exception):
            orders.approve_compatibility(self.w.sales, self.line, self.rev70, "Müşteri kabul etti")
        with self.assertRaisesMessage(BusinessRuleError, "gerekçe"):
            orders.approve_compatibility(self.w.planner, self.line, self.rev70, "")
        orders.approve_compatibility(self.w.planner, self.line, self.rev70, "Müşteri yazılı olarak 70 µ kabul etti")
        res = wh.reserve(self.w.depot, self.pallet70, self.line, 10)
        shipment = wh.create_shipment(self.w.depot, self.line.order)
        wh.add_shipment_line(self.w.depot, shipment, res, 10)
        wh.confirm_shipment(self.w.depot, load(self.w.depot, shipment))

    def test_transfer_between_different_signatures_rejected(self):
        pallet30 = stock_pallet_with_revision(self.w, self.w.rev_a)
        with self.assertRaisesMessage(BusinessRuleError, "teknik"):
            wh.transfer_boxes(self.w.depot, self.pallet70, 5, "Birleştirme", target=pallet30)


class Finding2OverReservationTests(TestCase):
    def setUp(self):
        self.w = World()
        self.line = self.w.approved_line(qty=D("10"))
        self.pallet = self.w.stock_pallet(boxes=40)

    def test_cannot_reserve_more_than_line_quantity(self):
        with self.assertRaisesMessage(BusinessRuleError, "kalem miktarı"):
            wh.reserve(self.w.depot, self.pallet, self.line, 20)
        wh.reserve(self.w.depot, self.pallet, self.line, 6)
        with self.assertRaisesMessage(BusinessRuleError, "kalem miktarı"):
            wh.reserve(self.w.depot, self.w.stock_pallet(boxes=40), self.line, 5)

    def test_authorised_over_delivery_allowance(self):
        with self.assertRaises(Exception):
            orders.allow_over_delivery(self.w.depot, self.line, 2, "Fazla teslim")
        orders.allow_over_delivery(self.w.planner, self.line, 2, "Müşteri +2 kutu kabul ediyor (kurgusal)")
        res = wh.reserve(self.w.depot, self.pallet, self.line, 12)
        shipment = wh.create_shipment(self.w.depot, self.line.order)
        wh.add_shipment_line(self.w.depot, shipment, res, 12)
        wh.confirm_shipment(self.w.depot, load(self.w.depot, shipment))
        self.line.refresh_from_db()
        self.assertEqual(self.line.status, LineStatus.SHIPPED)


class Finding2ConcurrencyTests(TransactionTestCase):
    """İki depocu aynı anda farklı paletlerden 10'ar kutu ayırır; 10 kutuluk kaleme yalnız biri geçer."""

    def test_parallel_reservations_do_not_exceed_line(self):
        w = World()
        line = w.approved_line(qty=D("10"))
        p1, p2 = w.stock_pallet(boxes=40), w.stock_pallet(boxes=40)
        barrier, results = threading.Barrier(2), []

        def worker(pallet):
            try:
                barrier.wait()
                wh.reserve(w.depot, pallet, line, 10)
                results.append("ok")
            except BusinessRuleError as exc:
                results.append(exc.message)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(p,)) for p in (p1, p2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count("ok"), 1, results)
        self.assertEqual(sum(r.open_quantity for r in line.reservations.all()), 10)


class Finding3CloseIsNotFulfilmentTests(TestCase):
    def test_short_production_marks_line_short_not_produced(self):
        w = World()
        line = w.approved_line(qty=D("50"))
        wo = w.started_wo(line=line, machines=[w.ext])
        prod.issue_material(w.foreman, wo, w.lot, D("100"))
        coil = w.coil(wo, kg=D("97"))
        wh.create_pallet_from_production(w.foreman, wo, 20, coils=[coil])
        prod.complete_operation(w.foreman, wo.operations.get())
        prod.close_work_order(w.foreman, wo, scrap_kg=D("3"), remaining_confirmed=True)
        line.refresh_from_db()
        self.assertEqual(line.status, LineStatus.PRODUCED_SHORT)


class Finding6RouteSkipTests(TestCase):
    def setUp(self):
        self.w = World()
        self.wo = self.w.started_wo(machines=[self.w.ext, self.w.prn, self.w.slit])
        self.op1, self.op2, self.op3 = self.wo.operations.order_by("seq")
        self.c1 = self.w.coil(self.wo, kg=D("100"))
        prod.start_operation(self.w.foreman, self.op2)  # matbaa başladı ama 1. op. bobini doğrudan dilimlemeye gidiyor
        prod.start_operation(self.w.foreman, self.op3)

    def test_source_must_come_from_previous_operation(self):
        with self.assertRaisesMessage(BusinessRuleError, "bir önceki"):
            prod.record_coil(self.w.foreman, self.op3, weight_kg=D("50"), sources=[(self.c1, D("50"))])

    def test_route_exception_with_reason(self):
        with self.assertRaisesMessage(BusinessRuleError, "gerekçe"):
            prod.allow_route_skip(self.w.planner, self.op3, "")
        prod.allow_route_skip(self.w.planner, self.op3, "Matbaa arızalı; baskısız sevk müşteri onaylı (kurgusal)")
        prod.record_coil(self.w.foreman, self.op3, weight_kg=D("50"), sources=[(self.c1, D("50"))])


class Finding8MaterialUnitTests(TestCase):
    def setUp(self):
        self.w = World()
        self.ink = cat.create_definition(self.w.admin, Material, {"code": "MUR", "name": "Mürekkep",
                                                                  "kind": MaterialKind.ADDITIVE, "unit": "lt"})
        self.ink_lot = cat.receive_lot(self.w.admin, self.ink, D("50"))

    def test_unit_choices_and_unit_frozen_after_movement(self):
        with self.assertRaises(BusinessRuleError):
            cat.create_definition(self.w.admin, Material, {"code": "X", "name": "X", "kind": MaterialKind.RAW,
                                                           "unit": "kilo"})
        with self.assertRaisesMessage(BusinessRuleError, "birim"):
            cat.update_definition(self.w.admin, self.ink, {"unit": "kg"}, reason="düzeltme")

    def test_non_kg_material_not_added_to_kg_balance(self):
        wo = self.w.started_wo(machines=[self.w.ext])
        prod.issue_material(self.w.foreman, wo, self.w.lot, D("100"))
        prod.issue_material(production_manager(), wo, self.ink_lot, D("5"), deviation_reason="Baskı denemesi (kurgusal)")
        s = prod.closure_summary(wo)
        self.assertEqual(s["issued"], D("100"))
        self.assertEqual(s["other_units"], {"lt": D("5")})


class Finding9OffRecipeMaterialTests(TestCase):
    def setUp(self):
        self.w = World()
        self.other = cat.create_definition(self.w.admin, Material, {"code": "HDPE", "name": "HDPE",
                                                                    "kind": MaterialKind.RAW, "unit": "kg"})
        self.other_lot = cat.receive_lot(self.w.admin, self.other, D("200"))
        self.wo = self.w.started_wo(machines=[self.w.ext])

    def test_off_recipe_issue_requires_justified_deviation(self):
        with self.assertRaisesMessage(BusinessRuleError, "reçete"):
            prod.issue_material(self.w.foreman, self.wo, self.other_lot, D("10"))
        with self.assertRaises(Exception):  # ustabaşı şablonunda sapma yetkisi yok
            prod.issue_material(self.w.foreman, self.wo, self.other_lot, D("10"), deviation_reason="Stok bitti")
        prod.issue_material(production_manager(), self.wo, self.other_lot, D("10"),
                            deviation_reason="LDPE bitti; HDPE onaylı")
        self.assertEqual([d.material.code for d in prod.closure_summary(self.wo)["deviations"]], ["HDPE"])


class Finding11UnitSafeProgressTests(TestCase):
    def test_roll_line_is_not_counted_from_boxes(self):
        w = World()
        order = orders.create_order(w.sales, customer=w.cust_a, order_date=timezone.localdate(),
                                    lines=[{"product": w.prod_a, "quantity": D("8"), "unit": Unit.ROLL}])
        orders.approve_order(w.sales, order)
        line = order.lines.get()
        orders.approve_line(w.planner, line, w.rev_a)
        wo = prod.create_work_order(w.planner, order_line=line, machines=[w.free_extruder()], target_quantity=D("8"),
                                    unit=Unit.ROLL, foreman=w.foreman)
        lifecycle.release(w.planner, wo)
        prod.start_work_order(w.foreman, wo)
        wh.create_pallet_from_production(w.foreman, wo, 30, coils=[w.coil(wo)])
        self.assertIsNone(produced_quantity_for_line(line)["quantity"])


class PalletReevaluationTests(TestCase):
    def test_new_content_returns_ok_pallet_to_pending(self):
        w = World()
        pallet = w.stock_pallet(boxes=20)
        self.assertEqual(pallet.quality_status, QualityStatus.OK)
        wo = w.started_wo(stock=True)
        wh.add_production_to_pallet(w.foreman, pallet, wo, 10, coils=[w.coil(wo)])
        pallet.refresh_from_db()
        self.assertEqual(pallet.quality_status, QualityStatus.PENDING)

    def test_release_of_block_requires_release_permission_and_reason(self):
        w = World()
        pallet = w.stock_pallet(boxes=20)
        wh.set_pallet_quality(w.depot, pallet, QualityStatus.BLOCKED, "Ezik kutu")
        with self.assertRaises(Exception):  # depo blokeleyebilir ama blokajı kaldıramaz
            wh.set_pallet_quality(w.depot, pallet, QualityStatus.OK, "Düzeldi")
        wh.set_pallet_quality(w.planner, pallet, QualityStatus.OK, "Kutular değiştirildi, kontrol edildi")


class RecipeValidationTests(TestCase):
    def test_dimensions_and_ratios(self):
        w = World()
        with self.assertRaisesMessage(BusinessRuleError, "En sıfırdan"):
            cat.create_revision(w.planner, w.prod_a, w.spec(width_mm=D("0")), w.mix(), reason="x")
        with self.assertRaisesMessage(BusinessRuleError, "Kalınlık"):
            cat.create_revision(w.planner, w.prod_a, w.spec(thickness_micron=D("-1")), w.mix(), reason="x")
        with self.assertRaisesMessage(BusinessRuleError, "0 ile 100"):
            cat.create_revision(w.planner, w.prod_a, w.spec(), [(w.ldpe, D("120")), (w.slip, D("-20"))], reason="x")
        with self.assertRaisesMessage(BusinessRuleError, "%100"):
            cat.create_revision(w.planner, w.prod_a, w.spec(), [(w.ldpe, D("90")), (w.slip, D("2"))], reason="x")


class StockSummaryTests(TestCase):
    def test_breakdown_categories_sum_to_physical(self):
        from django.urls import reverse
        from warehouse.selectors import stock_breakdown
        w = World()
        ok = w.stock_pallet(boxes=40)
        blocked = w.stock_pallet(boxes=10)
        wh.set_pallet_quality(w.depot, blocked, QualityStatus.BLOCKED, "Ezik")
        pending = w.stock_pallet(boxes=5, quality_ok=False)
        wh.reserve(w.depot, ok, w.approved_line(qty=D("12")), 12)
        row = next(r for r in stock_breakdown(w.sales) if r["product"] == w.prod_a)
        self.assertEqual((row["physical"], row["reserved"], row["blocked"], row["pending"], row["shippable"]),
                         (55, 12, 10, 5, 28))
        self.assertEqual(row["physical"], row["pending"] + row["blocked"] + row["held"] + row["reserved"] + row["shippable"])
        self.client.force_login(w.sales)
        self.assertContains(self.client.get(reverse("warehouse:stock_summary")), "Sevke uygun")
        self.assertTrue(pending)


class ExceptionPageTests(TestCase):
    def test_exception_pages_for_planner_only(self):
        import uuid
        from django.urls import reverse
        w = World()
        line = w.approved_line(qty=D("10"))
        self.client.force_login(w.planner)
        url = reverse("orders:line_exception", args=[line.pk, "fazla-teslim"])
        self.assertContains(self.client.get(url), "Fazla teslim izni")
        self.client.post(url, {"idem_key": uuid.uuid4().hex, "extra": "2", "reason": "Müşteri kabul"})
        self.assertEqual(orders.line_limit(line), D("12"))
        self.client.force_login(w.depot)
        self.assertEqual(self.client.get(url).status_code, 403)


class Finding5ReapprovalTests(TestCase):
    def test_different_revision_reapproval_blocked_while_released_work_order_exists(self):
        w = World()
        line = w.approved_line()
        wo = w.work_order(line=line)  # serbest bırakılmış (planlandi)
        rev2 = cat.create_revision(w.planner, w.prod_a, w.spec(thickness_micron=D("35")), w.mix(), reason="35 µ")
        with self.assertRaises(BusinessRuleError):
            orders.approve_line(w.planner, line, rev2)
        line.refresh_from_db()
        self.assertEqual(line.approved_revision, w.rev_a)
        lifecycle.withdraw(w.planner, wo, "Revizyon değişecek")  # geri çekilmiş taslakta yeniden onay mümkün
        orders.approve_line(w.planner, line, rev2)
