from decimal import Decimal as D

from django.core.exceptions import PermissionDenied
from django.db.models import ProtectedError
from django.test import TestCase

from catalog import services as cat
from catalog.models import LotMovementKind, Product, RecipeRevision
from core.exceptions import BusinessRuleError
from core.models import AuditLog
from core.tests.factories import World


class RecipeRevisionTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_change_creates_new_revision_and_keeps_old(self):
        rev2 = cat.create_revision(self.w.planner, self.w.prod_a, self.w.spec(thickness_micron=D("35")), self.w.mix(),
                                   reason="Kalınlık artışı")
        self.assertEqual(rev2.rev_no, 2)
        old = RecipeRevision.objects.get(pk=self.w.rev_a.pk)
        self.assertEqual(old.thickness_micron, D("30"))
        self.assertEqual(self.w.prod_a.current_revision, rev2)
        self.assertTrue(AuditLog.objects.filter(action="recipe.revision_create", reason="Kalınlık artışı").exists())

    def test_revision_is_immutable(self):
        self.w.rev_a.thickness_micron = D("99")
        with self.assertRaises(ValueError):
            self.w.rev_a.save()

    def test_new_revision_requires_reason(self):
        with self.assertRaises(BusinessRuleError):
            cat.create_revision(self.w.planner, self.w.prod_a, self.w.spec(), self.w.mix(), reason="")

    def test_components_must_sum_to_100(self):
        with self.assertRaises(BusinessRuleError):
            cat.create_revision(self.w.planner, self.w.prod_a, self.w.spec(), [(self.w.ldpe, D("90"))], reason="x")

    def test_warehouse_cannot_create_revision(self):
        with self.assertRaises(PermissionDenied):
            cat.create_revision(self.w.depot, self.w.prod_a, self.w.spec(), self.w.mix(), reason="x")


class DefinitionLifecycleTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_used_definitions_cannot_be_deleted_only_deactivated(self):
        with self.assertRaises(ProtectedError):
            self.w.prod_a.delete()
        with self.assertRaises(ProtectedError):
            self.w.ldpe.delete()
        cat.set_active(self.w.admin, self.w.prod_a, False, "Ürün devreden çıktı")
        self.assertFalse(Product.objects.get(pk=self.w.prod_a.pk).is_active)

    def test_deactivate_requires_reason(self):
        with self.assertRaises(BusinessRuleError):
            cat.set_active(self.w.admin, self.w.prod_a, False, "")

    def test_product_customer_cannot_change(self):
        with self.assertRaises(BusinessRuleError):
            cat.update_definition(self.w.admin, self.w.prod_a, {"customer": self.w.cust_b})

    def test_same_technical_product_has_separate_codes_per_customer(self):
        self.assertNotEqual(self.w.prod_a.code, self.w.prod_b.code)
        self.assertNotEqual(self.w.prod_a.customer, self.w.prod_b.customer)

    def test_machines_are_records_not_constants(self):
        from catalog.models import Machine, MachineType
        before = Machine.objects.filter(machine_type=MachineType.EXTRUDER).count()
        cat.create_definition(self.w.planner, Machine, {"code": "E9", "name": "Yeni ext", "machine_type": MachineType.EXTRUDER})
        self.assertEqual(Machine.objects.filter(machine_type=MachineType.EXTRUDER).count(), before + 1)
        with self.assertRaises(PermissionDenied):
            cat.create_definition(self.w.sales, Machine, {"code": "E8", "name": "x", "machine_type": MachineType.EXTRUDER})


class LotMovementTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_lot_cannot_go_negative(self):
        with self.assertRaises(BusinessRuleError):
            cat.move_lot(self.w.foreman, self.w.lot, LotMovementKind.ISSUE, D("-1000.001"))
        self.w.lot.refresh_from_db()
        self.assertEqual(self.w.lot.quantity, D("1000"))
        self.assertEqual(self.w.lot.movements.count(), 1)

    def test_adjust_only_through_approved_correction(self):
        # Doğrudan sayım düzeltmesi kaldırıldı; talep + başka yetkilinin onayı (corrections/tests/test_no_bypass).
        from accounts.models import Role, User
        from core.tests.factories import PASSWORD
        from corrections import services as corr
        from corrections.models import CorrectionKind
        mgr = User.objects.create_user("9811", PASSWORD, full_name="Üretim yetkilisi", role=Role.PRODUCTION_MANAGER)
        appr = User.objects.create_user("9812", PASSWORD, full_name="Ambar", role=Role.WAREHOUSE_MANAGER)
        with self.assertRaises(BusinessRuleError):
            corr.request_correction(mgr, CorrectionKind.LOT_COUNT, self.w.lot, D("990"), "")
        corr.approve(appr, corr.request_correction(mgr, CorrectionKind.LOT_COUNT, self.w.lot, D("990"), "Sayım farkı"))
        self.w.lot.refresh_from_db()
        self.assertEqual(self.w.lot.quantity, D("990"))
        self.assertEqual(self.w.lot.movements.first().quantity, D("-10"))
