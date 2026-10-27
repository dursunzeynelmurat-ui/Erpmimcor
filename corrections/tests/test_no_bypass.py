"""Fiziksel stok miktarını değiştiren sayım/düzeltme yalnız talep → başka yetkilinin onayı ile.
Doğrudan URL, değiştirilmiş POST veya servis çağrısıyla aynı kontrol aşılamaz."""
import uuid
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from catalog import services as cat
from catalog.models import LotMovementKind
from core.exceptions import BusinessRuleError
from core.tests.factories import PASSWORD, World
from corrections import services as cs
from corrections.models import CorrectionKind, CorrectionStatus


def mk(no, role, name="Test"):
    return User.objects.filter(personnel_no=no).first() or User.objects.create_user(no, PASSWORD, full_name=name, role=role)


class DirectAdjustmentClosedTests(TestCase):
    def setUp(self):
        self.w = World()
        self.pallet = self.w.stock_pallet(boxes=40)
        self.store_mgr = mk("9801", Role.WAREHOUSE_MANAGER, "Ambar")
        self.operator = mk("9802", Role.OPERATOR, "Operatör")

    def post(self, user, url, data):
        self.client.force_login(user)
        return self.client.post(url, {"idem_key": uuid.uuid4().hex, **data})

    def test_pallet_count_endpoint_cannot_change_stock(self):
        for user in (self.w.depot, self.store_mgr, self.operator, self.w.admin):
            self.post(user, f"/paletler/{self.pallet.pk}/sayim/", {"counted": "30", "reason": "Sayım"})
            self.pallet.refresh_from_db()
            self.assertEqual(self.pallet.box_count, 40, user.personnel_no)

    def test_lot_page_cannot_change_lot_quantity(self):
        url = reverse("catalog:lot_detail", args=[self.w.lot.pk])
        for user in (self.w.depot, self.store_mgr, self.w.admin):
            self.post(user, url, {"counted": "900", "reason": "Sayım"})
            self.w.lot.refresh_from_db()
            self.assertEqual(self.w.lot.quantity, D("1000"), user.personnel_no)

    def test_service_level_adjustment_paths_are_closed(self):
        from warehouse import services as wh
        self.assertFalse(hasattr(wh, "adjust_count"))
        self.assertFalse(hasattr(cat, "adjust_lot_to"))
        with self.assertRaises((PermissionDenied, BusinessRuleError)):
            cat.move_lot(self.w.admin, self.w.lot, LotMovementKind.ADJUST, D("-10"), reason="Doğrudan")
        with self.assertRaises((PermissionDenied, BusinessRuleError)):
            cat.move_lot(self.w.admin, self.w.lot, LotMovementKind.RECEIPT, D("10"), reason="Sahte kabul")
        self.w.lot.refresh_from_db()
        self.assertEqual(self.w.lot.quantity, D("1000"))

    def test_lot_receipt_requires_supplier_reference(self):
        with self.assertRaisesMessage(BusinessRuleError, "tedarikçi"):
            cat.receive_lot(self.w.depot, self.w.ldpe, D("10"), supplier_lot="")

    def test_depot_without_request_right_is_told_to_escalate(self):
        self.client.force_login(self.w.depot)
        page = self.client.get(reverse("warehouse:pallet_detail", args=[self.pallet.pk]))
        self.assertContains(page, "yetkilisine bildirin")
        self.assertNotContains(page, 'name="counted"')

    def test_lot_count_goes_through_request_and_other_approver(self):
        manager = mk("9803", Role.PRODUCTION_MANAGER, "Üretim yetkilisi")
        cr = cs.request_correction(manager, CorrectionKind.LOT_COUNT, self.w.lot, D("990"), "Sayımda 990 kg")
        self.w.lot.refresh_from_db()
        self.assertEqual(self.w.lot.quantity, D("1000"))
        with self.assertRaisesMessage(BusinessRuleError, "Kendi"):
            cs.approve(manager, cr)
        cs.approve(self.store_mgr, cr)
        self.w.lot.refresh_from_db()
        self.assertEqual(self.w.lot.quantity, D("990"))
        mv = self.w.lot.movements.filter(kind=LotMovementKind.ADJUST).get()
        self.assertIn(cr.code, mv.reason)

    def test_lot_count_rejected_when_lot_changed_before_approval(self):
        from production import services as prod
        manager = mk("9804", Role.PRODUCTION_MANAGER, "Üretim yetkilisi")
        cr = cs.request_correction(manager, CorrectionKind.LOT_COUNT, self.w.lot, D("990"), "Sayım")
        wo = self.w.started_wo(machines=[self.w.free_extruder()])
        prod.issue_material(self.w.foreman, wo, self.w.lot, D("5"))
        cs.approve(self.store_mgr, cr)
        cr.refresh_from_db()
        self.w.lot.refresh_from_db()
        self.assertEqual(cr.status, CorrectionStatus.REJECTED)
        self.assertEqual(self.w.lot.quantity, D("995"))

    def test_default_request_templates_unchanged(self):
        from accounts.catalog import TEMPLATES
        requesters = {c for c, (_, _, perms) in TEMPLATES.items() if "correction.request" in perms}
        approvers = {c for c, (_, _, perms) in TEMPLATES.items() if "correction.approve" in perms}
        self.assertEqual(requesters, {"yonetici", "satis", "planlama", "uretim_yetkilisi"})
        self.assertEqual(approvers, {"yonetici", "uretim_yetkilisi", "ambar_yetkilisi"})
        self.assertEqual(TEMPLATES["satis"][2]["correction.request"], "customer")
        for code in ("depo", "ambar_yetkilisi", "ustabasi", "operator", "kalite"):
            self.assertNotIn("correction.request", TEMPLATES[code][2])
            self.assertNotIn("lot.adjust", TEMPLATES[code][2])
