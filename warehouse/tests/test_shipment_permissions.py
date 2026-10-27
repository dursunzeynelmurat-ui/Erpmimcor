"""Sevk onayı, çıkış öncesi geri alma ve fiziksel çıkış kaydı ayrı yetkiler; doğrudan POST ile aşılamaz;
şablon değişikliği yalnız değiştirilmemiş şablon rollerine uygulanır."""
import uuid
from decimal import Decimal as D

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from accounts import services as acc
from accounts.catalog import TEMPLATES
from accounts.models import AccessRole, RolePermission, Role, User
from core.exceptions import BusinessRuleError
from core.tests.factories import PASSWORD, World, load
from warehouse import services as wh
from warehouse.models import ShipmentStatus


class SplitPermissionTests(TestCase):
    def setUp(self):
        self.w = World()
        pallet = self.w.stock_pallet(boxes=20)
        order = self.w.order(qty=D("10"))
        res = wh.reserve(self.w.depot, pallet, order.lines.get(), 10)
        self.shipment = wh.create_shipment(self.w.depot, order)
        wh.add_shipment_line(self.w.depot, self.shipment, res, 10)
        load(self.w.depot, self.shipment)

    def post(self, user, name, data=None):
        self.client.force_login(user)
        return self.client.post(reverse(f"warehouse:{name}", args=[self.shipment.pk]),
                                {"idem_key": uuid.uuid4().hex, "reason": "x", **(data or {})})

    def test_templates(self):
        self.assertNotIn("shipment.approve", TEMPLATES["depo"][2])
        for code in ("ambar_yetkilisi", "yonetici"):
            for a in ("shipment.approve", "shipment.undo", "shipment.exit"):
                self.assertIn(a, TEMPLATES[code][2])

    def test_depot_cannot_approve_undo_or_exit_by_direct_post(self):
        self.assertEqual(self.post(self.w.depot, "shipment_confirm").status_code, 403)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, ShipmentStatus.PREPARED)
        wh.confirm_shipment(self.w.store, self.shipment)
        self.assertEqual(self.post(self.w.depot, "shipment_undo").status_code, 403)
        self.assertEqual(self.post(self.w.depot, "shipment_exit").status_code, 403)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, ShipmentStatus.SHIPPED)

    def test_each_permission_is_independent(self):
        role = acc.create_role(self.w.admin, code="sadece_onay", name="Yalnız sevk onayı", description="",
                               hr_assignable=False, perms={"shipment.view": "all", "shipment.approve": "all"})
        u = User.objects.create_user("9851", PASSWORD, full_name="Onaylayıcı")
        acc.grant_role(self.w.admin, u, role, "test")
        wh.confirm_shipment(u, self.shipment)
        with self.assertRaises(Exception):
            wh.undo_shipment(u, self.shipment, "x")
        with self.assertRaises(Exception):
            wh.mark_exited(u, self.shipment)

    def test_exited_shipment_goes_to_return_not_undo(self):
        wh.confirm_shipment(self.w.store, self.shipment)
        wh.mark_exited(self.w.store, self.shipment)
        with self.assertRaisesMessage(BusinessRuleError, "iade"):
            wh.undo_shipment(self.w.store, self.shipment, "Geç kaldı")

    def test_role_list_shows_drift_and_split_note(self):
        depo = AccessRole.objects.get(code="depo")
        RolePermission.objects.create(role=depo, action="shipment.approve", scope="all")
        self.client.force_login(self.w.admin)
        page = self.client.get(reverse("accounts:role_list"))
        self.assertContains(page, "Şablondan farklı")
        self.assertContains(page, "kendiliğinden verilmedi")
        acc.apply_template(self.w.admin, depo, "Yeni şablona geç")
        self.assertFalse(depo.permissions.filter(action="shipment.approve").exists())


class TemplateMigrationTests(TransactionTestCase):
    def test_unmodified_updated_customized_untouched(self):
        before = [("accounts", "0005_remove_direct_lot_adjust")]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        Role_ = apps.get_model("accounts", "AccessRole")
        RP = apps.get_model("accounts", "RolePermission")
        import importlib
        mig = importlib.import_module("accounts.migrations.0006_shipment_permission_split")
        for code, perms in (("depo", mig.DEPO_OLD), ("ambar_yetkilisi", mig.AMBAR_OLD)):
            r, _ = Role_.objects.get_or_create(code=code, defaults={"name": code, "status": "aktif", "is_template": True})
            RP.objects.filter(role=r).delete()
            RP.objects.bulk_create([RP(role=r, action=a, scope=s) for a, s in perms.items()])
        ambar = Role_.objects.get(code="ambar_yetkilisi")
        RP.objects.create(role=ambar, action="order.change", scope="all")  # yönetici özelleştirmesi
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        depo = set(RolePermission.objects.filter(role__code="depo").values_list("action", flat=True))
        ambar = set(RolePermission.objects.filter(role__code="ambar_yetkilisi").values_list("action", flat=True))
        self.assertNotIn("shipment.approve", depo)
        self.assertIn("shipment.approve", ambar)
        self.assertNotIn("shipment.undo", ambar)  # özelleştirilmiş rol yeniden yazılmadı
        self.assertIn("order.change", ambar)
        from core.models import AuditLog
        self.assertTrue(AuditLog.objects.filter(action="role.template_drift", object_code="ambar_yetkilisi").exists())


class TemplateSyncNoopTests(TestCase):
    def test_role_already_on_new_template_is_not_reported_as_drift(self):
        from django.apps import apps
        from accounts.bootstrap import install_defaults
        from accounts.template_sync import sync_template_role
        from core.models import AuditLog
        import importlib
        install_defaults()  # güncel şablonla oluşturulur
        mig = importlib.import_module("accounts.migrations.0006_shipment_permission_split")
        self.assertEqual(sync_template_role(apps, "depo", mig.DEPO_OLD, mig.DEPO_NEW, "test"), "guncel")
        self.assertFalse(AuditLog.objects.filter(action="role.template_drift").exists())
