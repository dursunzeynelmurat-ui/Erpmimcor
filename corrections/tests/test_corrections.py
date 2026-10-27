"""Düzeltme talebi/onayı: talep stoğu değiştirmez, onayda yeniden kontrol, geçmişi koruyan uygulama,
rol şablonu sınırları ve kendi talebini onaylama yasağı."""
import uuid
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Role, User
from catalog.models import LotMovementKind
from core.exceptions import BusinessRuleError
from core.tests.factories import PASSWORD, World
from corrections import services as cs
from corrections.models import CorrectionKind, CorrectionRequest, CorrectionStatus
from production import services as prod
from warehouse import services as wh
from warehouse.models import MovementKind


def mk(no, role, name):
    return User.objects.filter(personnel_no=no).first() or User.objects.create_user(no, PASSWORD, full_name=name, role=role)


class CorrectionFlowTests(TestCase):
    def setUp(self):
        self.w = World()
        self.manager = mk("9501", Role.PRODUCTION_MANAGER, "Üretim Yetkilisi")
        self.manager2 = mk("9502", Role.PRODUCTION_MANAGER, "İkinci Üretim Yetkilisi")
        self.store_mgr = mk("9503", Role.WAREHOUSE_MANAGER, "Ambar Yetkilisi")
        self.wo = self.w.started_wo()
        self.mv = prod.issue_material(self.w.foreman, self.wo, self.w.lot, D("100"))
        self.coil = self.w.coil(self.wo, kg=D("97"))

    def test_request_changes_nothing_until_approved(self):
        cr = cs.request_correction(self.manager, CorrectionKind.COIL_WEIGHT, self.coil, D("79"), "Terazi yanlış okundu")
        self.coil.refresh_from_db()
        self.assertEqual(self.coil.weight_kg, D("97"))
        self.assertEqual(cr.status, CorrectionStatus.REQUESTED)
        with self.assertRaisesMessage(BusinessRuleError, "Kendi"):
            cs.approve(self.manager, cr)
        cs.approve(self.manager2, cr)
        self.coil.refresh_from_db()
        self.assertEqual((self.coil.weight_kg, self.coil.remaining_kg), (D("79"), D("79")))
        cr.refresh_from_db()
        self.assertEqual((cr.status, cr.old_value), (CorrectionStatus.APPLIED, D("97")))

    @override_settings(CORRECTION_SELF_APPROVAL=True)
    def test_self_approval_is_a_setting(self):
        cr = cs.request_correction(self.manager, CorrectionKind.COIL_WEIGHT, self.coil, D("96"), "x")
        cs.approve(self.manager, cr)

    def test_operator_and_quality_cannot_request(self):
        for no, role in (("9504", Role.OPERATOR), ("9505", Role.QUALITY)):
            with self.assertRaises(PermissionDenied):
                cs.request_correction(mk(no, role, "x"), CorrectionKind.COIL_WEIGHT, self.coil, D("90"), "x")

    def test_material_issue_correction_uses_compensating_movement(self):
        cr = cs.request_correction(self.manager, CorrectionKind.MATERIAL_ISSUE, self.mv, D("80"), "100 değil 80 kg verildi")
        self.w.lot.refresh_from_db()
        self.assertEqual(self.w.lot.quantity, D("900"))
        cs.approve(self.store_mgr, cr)
        self.w.lot.refresh_from_db()
        self.mv.refresh_from_db()
        self.assertEqual(self.w.lot.quantity, D("920"))
        self.assertEqual(self.mv.quantity, D("-100"))  # orijinal hareket aynen duruyor
        self.assertEqual(prod.issued_by_lot(self.wo)[self.w.lot], D("80"))
        self.assertTrue(self.wo.lot_movements.filter(kind=LotMovementKind.RETURN, reason__contains=cr.code).exists())
        # Aynı hareket için ikinci düzeltme önceki onaylı düzeltmeyi baz alır
        cr2 = cs.request_correction(self.manager, CorrectionKind.MATERIAL_ISSUE, self.mv, D("85"), "Tekrar sayıldı")
        self.assertEqual(cr2.old_value, D("80"))

    def test_changed_target_is_rejected_at_approval(self):
        pallet = self.w.stock_pallet(boxes=40)
        cr = cs.request_correction(self.manager, CorrectionKind.COUNT, pallet, D("38"), "Sayımda 38 çıktı")
        wh.separate_damaged(self.w.depot, pallet, 1, "Ezik kutu ayrıldı")  # talep sonrası palet değişti
        cs.approve(self.store_mgr, cr)
        cr.refresh_from_db()
        pallet.refresh_from_db()
        self.assertEqual(cr.status, CorrectionStatus.REJECTED)
        self.assertIn("değişmiş", cr.decision_reason)
        self.assertEqual(pallet.box_count, 39)

    def test_pallet_box_correction_counts_in_production(self):
        pallet = wh.create_pallet_from_production(self.w.foreman, self.wo, 20, coils=[self.coil])
        mv = pallet.movements.get(kind=MovementKind.PRODUCTION)
        cr = cs.request_correction(self.manager, CorrectionKind.PALLET_BOXES, mv, D("18"), "2 kutu sayılmamış (eksik)")
        cs.approve(self.store_mgr, cr)
        pallet.refresh_from_db()
        self.assertEqual(pallet.box_count, 18)
        self.assertEqual(prod.closure_summary(self.wo)["boxes"], 18)
        from production.selectors import produced_quantity_for_line
        self.assertEqual(produced_quantity_for_line(self.wo.order_line)["quantity"], 18)

    def test_weighing_correction_creates_new_weighing(self):
        pallet = self.w.stock_pallet(boxes=10)
        old = pallet.current_weighing
        cr = cs.request_correction(self.manager, CorrectionKind.WEIGHING, old, D("130"), "Brüt yanlış yazılmış")
        cs.approve(self.store_mgr, cr)
        pallet.refresh_from_db()
        self.assertEqual(pallet.current_weighing.gross_kg, D("130"))
        old.refresh_from_db()
        self.assertFalse(old.is_current)
        self.assertEqual(pallet.weighings.count(), 2)

    def test_reject_requires_reason_and_keeps_record(self):
        cr = cs.request_correction(self.manager, CorrectionKind.COIL_WEIGHT, self.coil, D("90"), "x")
        with self.assertRaisesMessage(BusinessRuleError, "gerekçe"):
            cs.reject(self.store_mgr, cr, "")
        cs.reject(self.store_mgr, cr, "Tartım fişi uyumlu; değişiklik gerekmiyor")
        self.coil.refresh_from_db()
        self.assertEqual(self.coil.weight_kg, D("97"))

    def test_coil_weight_cannot_drop_below_consumed(self):
        self.wo2 = self.w.started_wo(machines=[self.w.free_extruder(), self.w.prn])
        c = self.w.coil(self.wo2, kg=D("100"))
        prod.start_operation(self.w.foreman, self.wo2.operations.get(seq=2))
        self.w.coil(self.wo2, kg=D("60"), op_seq=2, sources=[(c, D("60"))])
        cr = cs.request_correction(self.manager, CorrectionKind.COIL_WEIGHT, c, D("50"), "Yanlış")
        with self.assertRaisesMessage(BusinessRuleError, "tüketilmiş"):
            cs.approve(self.manager2, cr)

    def test_sales_request_is_customer_scoped(self):
        pallet_a = self.w.stock_pallet(boxes=10)
        cs.request_correction(self.w.sales, CorrectionKind.COUNT, pallet_a, D("9"), "Müşteri 9 kutu saydı")
        outsider = mk("9506", Role.SALES, "Erişimsiz Satış")
        with self.assertRaises(PermissionDenied):
            cs.request_correction(outsider, CorrectionKind.COUNT, pallet_a, D("8"), "x")


class CorrectionViewTests(TestCase):
    def test_request_and_approve_through_pages(self):
        w = World()
        manager = mk("9511", Role.PRODUCTION_MANAGER, "Üretim Yetkilisi")
        approver = mk("9512", Role.WAREHOUSE_MANAGER, "Ambar Yetkilisi")
        wo = w.started_wo()
        coil = w.coil(wo, kg=D("97"))
        self.client.force_login(manager)
        url = reverse("corrections:correction_create", args=["bobin_agirlik", coil.pk])
        self.assertContains(self.client.get(url), "Etkilenecek kayıtlar")
        resp = self.client.post(url, {"idem_key": uuid.uuid4().hex, "new_value": "95", "reason": "Yanlış okuma"})
        cr = CorrectionRequest.objects.get()
        self.assertRedirects(resp, reverse("corrections:correction_detail", args=[cr.pk]))
        self.assertNotContains(self.client.get(resp.url), "Onayla ve uygula")
        self.client.force_login(approver)
        self.assertContains(self.client.get(reverse("corrections:correction_list")), cr.code)
        self.client.post(reverse("corrections:correction_approve", args=[cr.pk]), {"idem_key": uuid.uuid4().hex})
        coil.refresh_from_db()
        self.assertEqual(coil.weight_kg, D("95"))
        self.client.force_login(mk("9513", Role.OPERATOR, "Op"))
        self.assertEqual(self.client.get(url).status_code, 403)
