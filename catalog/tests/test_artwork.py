"""Aşama 3: baskı teknik bilgileri, görsel sürümleri, iç teknik / müşteri onayı ayrımı ve iş emrine sabitleme."""
import io
import tempfile
import uuid
from decimal import Decimal as D

import segno
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, Shift, User, UserMachine
from catalog import artwork as art
from catalog import customers as cs
from catalog import services as cat
from catalog.models import ArtworkApprovalKind as K
from catalog.tests.test_spec import GUSSET_PRINT
from core.exceptions import BusinessRuleError
from core.tests.factories import PASSWORD, World
from production import instructions, lifecycle
from production import services as prod

MEDIA = tempfile.mkdtemp(prefix="mimcor-test-media-")


def png(text="KURGU BASKI"):
    buf = io.BytesIO()
    segno.make(text).save(buf, kind="png", scale=3)
    return SimpleUploadedFile("baski.png", buf.getvalue(), content_type="image/png")


@override_settings(MEDIA_ROOT=MEDIA, PRINT_REQUIRE_CUSTOMER_APPROVAL=True)
class ArtworkTests(TestCase):
    def setUp(self):
        self.w = World()
        self.rev = cat.create_revision(self.w.planner, self.w.prod_a, GUSSET_PRINT, self.w.mix(), "Baskılı")
        self.line = self.w.approved_line()

    def valid_art(self, text="v1"):
        a = art.upload_artwork(self.w.sales, self.w.prod_a, png(text))
        art.approve(self.w.planner, a, K.TECHNICAL)
        art.approve(self.w.sales, a, K.CUSTOMER, evidence="12.11.2026 e-posta", customer_contact="Kurgu Kişi")
        return a

    def test_sales_customer_approval_is_not_technical(self):
        a = art.upload_artwork(self.w.sales, self.w.prod_a, png())
        with self.assertRaises(Exception):
            art.approve(self.w.sales, a, K.TECHNICAL)
        with self.assertRaisesMessage(BusinessRuleError, "kanıt"):
            art.approve(self.w.sales, a, K.CUSTOMER)
        art.approve(self.w.sales, a, K.CUSTOMER, evidence="Prova 7")
        self.assertFalse(art.approval_state(a, self.rev)["valid"])  # müşteri onayı tek başına üretim onayı değil
        art.approve(self.w.planner, a, K.TECHNICAL)
        self.assertTrue(art.approval_state(a, self.rev)["valid"])

    def test_release_requires_valid_artwork_and_pins_version(self):
        wo = prod.create_work_order(self.w.planner, order_line=self.line, machines=[self.w.ext, self.w.prn],
                                    target_quantity=self.line.quantity, unit="kutu", foreman=self.w.foreman)
        with self.assertRaisesMessage(BusinessRuleError, "baskı görseli gerekir"):
            lifecycle.release(self.w.planner, wo)
        v1 = self.valid_art("v1")
        lifecycle.release(self.w.planner, wo)
        wo.refresh_from_db()
        self.assertEqual(wo.artwork, v1)
        print_op = wo.operations.get(machine=self.w.prn)
        ins = instructions.issue_for_print(self.w.foreman, print_op)
        self.assertEqual(ins.snapshot["teknik"]["gorsel"]["surum"], 1)
        self.assertEqual(ins.snapshot["teknik"]["plate_code"], "KL-KURGU-01")
        self.assertNotIn("karisim", ins.snapshot["teknik"])  # matbaa talimatında karışım yok
        v2 = self.valid_art("v2")  # yeni görsel eski iş emrini değiştirmez
        wo.refresh_from_db()
        ins.refresh_from_db()
        self.assertEqual((wo.artwork, ins.snapshot["teknik"]["gorsel"]["surum"]), (v1, 1))
        self.assertEqual(art.valid_artwork(self.w.prod_a, self.rev), v2)

    def test_critical_change_requires_new_approvals(self):
        v1 = self.valid_art()
        rev2 = cat.create_revision(self.w.planner, self.w.prod_a, {**GUSSET_PRINT, "print_repeat_mm": D("560")},
                                   self.w.mix(), "Tekrar boyu değişti")
        st = art.approval_state(v1, rev2)
        self.assertFalse(st["valid"])
        self.assertEqual(len(st["stale"]), 2)
        rev3 = cat.create_revision(self.w.planner, self.w.prod_a, {**GUSSET_PRINT, "print_repeat_mm": D("560"),
                                                                    "process_notes": "Hız 120"}, self.w.mix(), "Not")
        art.approve(self.w.planner, v1, K.TECHNICAL)
        art.approve(self.w.sales, v1, K.CUSTOMER, evidence="Yeni prova")
        self.assertTrue(art.approval_state(v1, rev3)["valid"])  # proses notu baskı onayını etkilemez

    def test_upload_type_check_and_double_submit(self):
        with self.assertRaisesMessage(BusinessRuleError, "uyuşmuyor"):
            art.upload_artwork(self.w.sales, self.w.prod_a, SimpleUploadedFile("x.png", b"<svg/>"))
        with self.assertRaisesMessage(BusinessRuleError, "Yalnız PDF"):
            art.upload_artwork(self.w.sales, self.w.prod_a, SimpleUploadedFile("x.svg", b"<svg/>"))
        self.client.force_login(self.w.sales)
        url = reverse("catalog:artwork_upload", args=[self.w.prod_a.pk])
        for _ in range(2):
            self.client.post(url, {"idem_key": "same", "file": png(), "note": "x"})
        self.assertEqual(self.w.prod_a.artworks.count(), 1)

    def test_file_access_scoped(self):
        a = self.valid_art()
        other = User.objects.create_user("9931", PASSWORD, full_name="B satış", role=Role.SALES)
        cs.grant_access(self.w.admin, self.w.cust_b, other, timezone.localdate(), None, "Yalnız B")
        file_url = reverse("catalog:artwork_file", args=[a.pk])
        list_url = reverse("catalog:artwork_list", args=[self.w.prod_a.pk])
        for u, code in ((self.w.sales, 200), (self.w.planner, 200), (other, 404), (self.w.depot, 404)):
            self.client.force_login(u)
            self.assertEqual(self.client.get(file_url).status_code, code, u.personnel_no)
            self.assertEqual(self.client.get(list_url).status_code, code, u.personnel_no)
        self.client.force_login(other)
        r = self.client.post(reverse("catalog:artwork_approve", args=[a.pk, "musteri"]),
                             {"idem_key": uuid.uuid4().hex, "evidence": "x"})
        self.assertEqual(r.status_code, 404)
        self.client.force_login(self.w.sales)
        resp = self.client.get(file_url + "?onizleme=1")
        self.assertEqual((resp["Content-Disposition"].split(";")[0], resp["X-Content-Type-Options"]), ("inline", "nosniff"))
        self.assertIn("sandbox", resp["Content-Security-Policy"])

    def test_operator_sees_pinned_artwork_only_while_assigned(self):
        a = self.valid_art()
        wo = self.w.work_order(line=self.line, machines=[self.w.ext, self.w.prn])
        op = wo.operations.get(machine=self.w.prn)
        v1 = Shift.objects.get(code="V1")
        operator = User.objects.create_user("9932", PASSWORD, full_name="Matbaacı", role=Role.OPERATOR, shift=v1)
        UserMachine.objects.create(user=operator, machine=self.w.prn)
        lifecycle.schedule_operation(self.w.foreman, op, planned_date=timezone.localdate(), shift=v1,
                                     machine=self.w.prn, queue_position=1)
        self.client.force_login(operator)
        self.assertEqual(self.client.get(reverse("catalog:artwork_file", args=[a.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("catalog:artwork_list", args=[self.w.prod_a.pk])).status_code, 404)
        other_art = art.upload_artwork(self.w.sales, self.w.prod_a, png("taslak"))  # sabitlenmemiş taslak
        self.assertEqual(self.client.get(reverse("catalog:artwork_file", args=[other_art.pk])).status_code, 404)
        lifecycle.cancel(self.w.planner, wo, "Plan değişti")
        self.assertEqual(self.client.get(reverse("catalog:artwork_file", args=[a.pk])).status_code, 404)


class ArtworkPermissionMigrationTests(TransactionTestCase):
    def test_unmodified_roles_get_artwork_actions_customised_are_reported(self):
        import importlib
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        mig = importlib.import_module("accounts.migrations.0008_artwork_permissions")
        before = [("accounts", "0007_operator_machine_shift")]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        R, RP = apps.get_model("accounts", "AccessRole"), apps.get_model("accounts", "RolePermission")
        for code in ("satis", "planlama"):
            role, _ = R.objects.get_or_create(code=code, defaults={"name": code, "status": "aktif", "is_template": True})
            RP.objects.filter(role=role).delete()
            old = dict(mig.CHANGES[code][0])
            if code == "planlama":
                old.pop("stocklevel.manage")  # yönetici tarafından özelleştirilmiş rol
            RP.objects.bulk_create([RP(role=role, action=a, scope=s) for a, s in old.items()])
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        from accounts.models import RolePermission
        from core.models import AuditLog
        sales = dict(RolePermission.objects.filter(role__code="satis").values_list("action", "scope"))
        self.assertEqual((sales.get("artwork.view"), sales.get("artwork.customer_approval"), sales.get("artwork.approve")),
                         ("customer", "customer", None))
        planning = dict(RolePermission.objects.filter(role__code="planlama").values_list("action", "scope"))
        self.assertNotIn("artwork.approve", planning)  # özelleştirilmiş rol yeniden yazılmadı
        self.assertTrue(AuditLog.objects.filter(action="role.template_drift", object_code="planlama").exists())
