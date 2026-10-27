"""Kapalı iş emrinde düzeltme sonrası güncel denge.

Sayısal örnek: verilen 100 kg, sağlam 95 kg, fire 5 kg → kapanış farkı 0. Sonra çıkış 100 → 110 kg olarak
düzeltilir ve onaylanır: ilk kapanış kaydı 100/0 kalır; güncel verilen 110, güncel fark 10 kg. Tolerans %3
(110 × 0,03 = 3,3 kg) aşıldığı için kapanış "yeniden değerlendirme bekliyor" olur; yetkili değerlendirince kapanır.
"""
import threading
import uuid
from decimal import Decimal as D

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from accounts.models import Role, User
from catalog.models import LotMovementKind
from core.exceptions import BusinessRuleError
from core.tests.factories import PASSWORD, World
from corrections import services as cs
from corrections.models import CorrectionKind, CorrectionStatus
from production import services as prod
from warehouse import services as wh


def users():
    mgr = User.objects.filter(personnel_no="9861").first() or User.objects.create_user(
        "9861", PASSWORD, full_name="Üretim yetkilisi", role=Role.PRODUCTION_MANAGER)
    appr = User.objects.filter(personnel_no="9862").first() or User.objects.create_user(
        "9862", PASSWORD, full_name="Ambar yetkilisi", role=Role.WAREHOUSE_MANAGER)
    return mgr, appr


def closed_work_order(w):
    wo = w.started_wo(machines=[w.free_extruder()])
    mv = prod.issue_material(w.foreman, wo, w.lot, D("100"))
    coil = w.coil(wo, kg=D("95"))
    pallet = wh.create_pallet_from_production(w.foreman, wo, 10, coils=[coil])
    prod.complete_operation(w.foreman, wo.operations.get())
    prod.close_work_order(w.foreman, wo, scrap_kg=D("5"), remaining_confirmed=True)
    wo.refresh_from_db()
    return wo, mv, coil, pallet


class ClosureStatusTests(TestCase):
    def setUp(self):
        self.w = World()
        self.mgr, self.appr = users()
        self.wo, self.mv, self.coil, self.pallet = closed_work_order(self.w)

    def test_numeric_example_original_kept_current_shown_review_required(self):
        st = prod.closure_status(self.wo)
        self.assertFalse(st["changed"])
        cr = cs.request_correction(self.mgr, CorrectionKind.MATERIAL_ISSUE, self.mv, D("110"), "Fişte 110 kg yazıyor")
        cs.approve(self.appr, cr)
        self.wo.refresh_from_db()
        st = prod.closure_status(self.wo)
        self.assertEqual((st["original"]["issued"], st["original"]["difference"]), (D("100"), D("0")))
        self.assertEqual((st["current"]["issued"], st["current"]["difference"]), (D("110"), D("10")))
        self.assertTrue(st["needs_review"])
        self.assertEqual([c.code for c in st["corrections"]], [cr.code])
        self.wo.closure.refresh_from_db()
        self.assertEqual(self.wo.closure.issued_kg, D("100"))  # ilk kapanış üzerine yazılmadı
        with self.assertRaises(Exception):
            prod.review_closure(self.w.depot, self.wo, "x")
        prod.review_closure(self.mgr, self.wo, "Fire tartımı eksik girilmiş; fark açıklandı")
        self.assertFalse(prod.closure_status(self.wo)["needs_review"])

    def test_page_shows_both_states(self):
        cr = cs.request_correction(self.mgr, CorrectionKind.MATERIAL_ISSUE, self.mv, D("110"), "Fiş")
        cs.approve(self.appr, cr)
        self.client.force_login(self.mgr)
        page = self.client.get(reverse("production:workorder_detail", args=[self.wo.pk]))
        self.assertContains(page, "İlk kapanış")
        self.assertContains(page, "Düzeltmeler sonrası güncel")
        self.assertContains(page, "yeniden değerlendirme bekliyor")
        self.assertContains(page, cr.code)
        self.client.post(reverse("production:closure_review", args=[self.wo.pk]),
                         {"idem_key": uuid.uuid4().hex, "reason": "Açıklandı"})
        self.assertFalse(prod.closure_status(self.wo)["needs_review"])

    def test_double_approval_single_movement(self):
        cr = cs.request_correction(self.mgr, CorrectionKind.MATERIAL_ISSUE, self.mv, D("110"), "Fiş")
        cs.approve(self.appr, cr)
        with self.assertRaisesMessage(BusinessRuleError, "artık onay beklemiyor"):
            cs.approve(self.appr, cr)
        self.assertEqual(self.wo.lot_movements.filter(reason__contains=cr.code).count(), 1)

    def test_palletized_coil_correction_marks_reweigh(self):
        wh.accept_pallet(self.w.depot, self.pallet, {"gross_kg": D("120")})
        cr = cs.request_correction(self.mgr, CorrectionKind.COIL_WEIGHT, self.coil, D("93"), "Terazi")
        cs.approve(self.appr, cr)
        self.pallet.refresh_from_db()
        self.assertEqual(self.pallet.weight_status, "yeniden_tartim_bekliyor")

    def test_shipped_pallet_coil_correction_rejected(self):
        from core.models import QualityStatus
        from core.tests.factories import load
        wh.accept_pallet(self.w.depot, self.pallet, {"gross_kg": D("120")})
        wh.set_pallet_quality(self.w.foreman, self.pallet, QualityStatus.OK, "Kontrol")
        order = self.w.order(qty=D("10"))
        res = wh.reserve(self.w.depot, self.pallet, order.lines.get(), 10)
        s = wh.create_shipment(self.w.depot, order)
        wh.add_shipment_line(self.w.depot, s, res, 10)
        wh.confirm_shipment(self.w.store, load(self.w.depot, s))
        cr = cs.request_correction(self.mgr, CorrectionKind.COIL_WEIGHT, self.coil, D("93"), "Terazi")
        with self.assertRaisesMessage(BusinessRuleError, "sevk edilmiş"):
            cs.approve(self.appr, cr)
        self.coil.refresh_from_db()
        self.assertEqual(self.coil.weight_kg, D("95"))


class ConcurrentApprovalTests(TransactionTestCase):
    def test_two_approvers_at_once_one_movement(self):
        w = World()
        mgr, appr = users()
        appr2 = User.objects.create_user("9863", PASSWORD, full_name="İkinci ambar", role=Role.WAREHOUSE_MANAGER)
        wo, mv, *_ = closed_work_order(w)
        cr = cs.request_correction(mgr, CorrectionKind.MATERIAL_ISSUE, mv, D("110"), "Fiş")
        barrier, out = threading.Barrier(2), []

        def run(u):
            try:
                barrier.wait()
                cs.approve(u, cr)
                out.append("ok")
            except BusinessRuleError as exc:
                out.append(exc.message)
            finally:
                connection.close()
        ts = [threading.Thread(target=run, args=(u,)) for u in (appr, appr2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(out.count("ok"), 1, out)
        self.assertEqual(wo.lot_movements.filter(kind=LotMovementKind.ISSUE, reason__contains=cr.code).count(), 1)
        cr.refresh_from_db()
        self.assertEqual(cr.status, CorrectionStatus.APPLIED)
