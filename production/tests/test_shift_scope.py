"""Makine + vardiya kapsamı gerçek rollerle: ustabaşı, iki operatör (V1 gündüz, V2 gece), makine ve vardiya
atamaları, devir teslim ve çoklu rol genişlemesi."""
import datetime

from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts import services as acc
from accounts.models import Role, Shift, User, UserMachine
from core.tests.factories import PASSWORD, World
from production import instructions, lifecycle
from production.models import WorkOrderOperation


class MachineShiftScopeTests(TestCase):
    def setUp(self):
        self.w = World()
        self.v1, self.v2 = Shift.objects.get(code="V1"), Shift.objects.get(code="V2")
        self.day = User.objects.create_user("9871", PASSWORD, full_name="Gündüz Op.", role=Role.OPERATOR, shift=self.v1)
        self.night = User.objects.create_user("9872", PASSWORD, full_name="Gece Op.", role=Role.OPERATOR, shift=self.v2)
        for u in (self.day, self.night):
            UserMachine.objects.create(user=u, machine=self.w.ext)
        self.e2 = self.w.free_extruder()
        if self.e2 == self.w.ext:
            from catalog import services as cat
            from catalog.models import Machine, MachineType
            self.e2 = cat.create_definition(self.w.admin, Machine, {"code": "E9", "name": "Ext 9",
                                                                    "machine_type": MachineType.EXTRUDER})
        today = timezone.localdate()
        # E1 / V1 işi, E1 / V2 (gece, tarih = başladığı gün) işi, E2 / V1 işi
        self.e1_v1 = self.scheduled(self.w.ext, self.v1, today)
        self.e1_v2 = self.scheduled(self.w.ext, self.v2, today)
        self.e2_v1 = self.scheduled(self.e2, self.v1, today)

    def scheduled(self, machine, shift, day):
        wo = self.w.work_order(machines=[machine])
        op = wo.operations.get()
        lifecycle.schedule_operation(self.w.foreman, op, planned_date=day, shift=shift, machine=machine, queue_position=1)
        return WorkOrderOperation.objects.get(pk=op.pk)

    def tech(self, user, op):
        return instructions.can_view_technical(user, op)

    def test_machine_and_shift_both_required(self):
        self.assertTrue(self.tech(self.day, self.e1_v1))
        self.assertFalse(self.tech(self.day, self.e1_v2))  # aynı makine, başka vardiya
        self.assertFalse(self.tech(self.day, self.e2_v1))  # aynı vardiya, atanmamış makine
        self.assertTrue(self.tech(self.night, self.e1_v2))
        self.assertFalse(self.tech(self.night, self.e1_v1))

    def test_night_shift_has_no_clock_rule(self):
        """Gece vardiyası (19–07) bir önceki günün tarihiyle planlıdır; erişim saat/tarih kontrolüne bağlı değildir:
        ertesi sabah 03:00'te de gece operatörü işi görür, kimse saat yüzünden kilitlenmez."""
        yesterday = timezone.localdate() - datetime.timedelta(days=1)
        lifecycle.schedule_operation(self.w.foreman, self.e1_v2, planned_date=yesterday, shift=self.v2,
                                     machine=self.w.ext, queue_position=1)
        self.e1_v2.refresh_from_db()
        self.assertTrue(self.tech(self.night, self.e1_v2))

    def test_history_visible_by_machine_scope(self):
        self.client.force_login(self.day)
        resp = self.client.get(reverse("production:workorder_detail", args=[self.e1_v2.work_order_id]))
        self.assertEqual(resp.status_code, 200)  # geçmiş/iş emri görünümü makine kapsamında
        self.assertNotContains(resp, "Bu operasyonun teknik bilgisi")  # aktif teknik bilgi yok
        self.assertEqual(self.client.get(reverse("production:workorder_detail",
                                                 args=[self.e2_v1.work_order_id])).status_code, 404)

    def test_handover_moves_responsibility(self):
        from production import services as prod
        wo = self.e1_v1.work_order
        prod.start_work_order(self.w.foreman, wo)
        op = WorkOrderOperation.objects.get(pk=self.e1_v1.pk)
        h = lifecycle.create_handover(self.w.foreman, op, to_shift=self.v2, remaining_note="1 bobin daha")
        op.refresh_from_db()
        self.assertTrue(self.tech(self.day, op))  # devir teslim alınana kadar eski vardiya devam eder
        self.assertFalse(self.tech(self.night, op))
        other_day = User.objects.create_user("9875", PASSWORD, full_name="Gündüz 2", role=Role.OPERATOR, shift=self.v1)
        UserMachine.objects.create(user=other_day, machine=self.e2)
        with self.assertRaises(Exception):  # devralan vardiyada/makinede olmayan kişi teslim alamaz
            lifecycle.acknowledge_handover(other_day, h)
        lifecycle.acknowledge_handover(self.night, h)  # devralan gece operatörü teslim alır
        op.refresh_from_db()
        self.assertEqual(op.responsible_shift, self.v2)
        self.assertTrue(self.tech(self.night, op))  # yeni sorumlu görür
        self.assertFalse(self.tech(self.day, op))   # eski vardiyanın aktif teknik erişimi kapandı
        self.client.force_login(self.day)  # geçmiş kayıt görünmeye devam eder
        self.assertEqual(self.client.get(reverse("production:workorder_detail", args=[wo.pk])).status_code, 200)

    def test_operator_without_shift_has_no_technical_access(self):
        u = User.objects.create_user("9874", PASSWORD, full_name="Vardiyasız", role=Role.OPERATOR)
        UserMachine.objects.create(user=u, machine=self.w.ext)
        self.assertFalse(self.tech(u, self.e1_v1))

    def test_multiple_roles_widen_with_or(self):
        """Bir kullanıcıya ikinci bir rol (ör. vardiya kapsamlı talimat görme) verilirse kapsamlar VEYA ile
        birleşir: aynı vardiyadaki başka makinelerin talimatı da açılır. Bu bilinçli kuraldır ve rol ataması
        sırasında yöneticinin kararıdır; tek rol içinde makine+vardiya her zaman VE'dir."""
        role = acc.create_role(self.w.admin, code="vardiya_talimat", name="Vardiya talimat görme", description="",
                               hr_assignable=False, perms={"instruction.view": "shift"})
        self.assertFalse(self.tech(self.day, self.e2_v1))
        acc.grant_role(self.w.admin, self.day, role, "Vardiya sorumlusu yardımcısı")
        self.assertTrue(self.tech(self.day, self.e2_v1))
        self.assertFalse(self.tech(self.day, self.e1_v2))  # başka vardiya yine kapalı


class OperatorTemplateMigrationTests(TransactionTestCase):
    def test_unmodified_operator_template_moves_to_machine_shift(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        import importlib
        mig = importlib.import_module("accounts.migrations.0007_operator_machine_shift")
        before = [("accounts", "0006_shipment_permission_split")]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        R = apps.get_model("accounts", "AccessRole")
        RP = apps.get_model("accounts", "RolePermission")
        role, _ = R.objects.get_or_create(code="operator", defaults={"name": "Op", "status": "aktif", "is_template": True})
        RP.objects.filter(role=role).delete()
        RP.objects.bulk_create([RP(role=role, action=a, scope=s) for a, s in mig.OLD.items()])
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        from accounts.models import RolePermission
        got = dict(RolePermission.objects.filter(role__code="operator").values_list("action", "scope"))
        self.assertEqual(got, mig.NEW)
