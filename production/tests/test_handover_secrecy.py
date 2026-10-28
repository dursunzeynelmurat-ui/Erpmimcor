"""Kabul denetimi: devir teslimden sonra reçete gizliliği.

Devir teslim alınınca eski vardiya operatörü iş emri / operasyon sayfaları, üretim kaydı formları, talimat
görüntüleme, yazdırma ve teslim adresleri, reçete sayfası üzerinden karışım bileşenine, oranına veya teknik ölçüye
ulaşamaz; red sunucudadır (doğrudan URL ve POST). İşlem geçmişi (iş emri sayfası) açık kalır. Yeni vardiya yalnız
atandığı makinenin talimatını görür; planlama ve yönetici erişimi sürer."""
import uuid

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, Shift, User, UserMachine
from catalog import services as cat
from catalog.models import Machine, MachineType
from core.tests.factories import PASSWORD, World
from production import instructions, lifecycle
from production import services as prod
from production.models import WorkOrderOperation

SECRETS = ("LDPE", "SLIP", "600 mm", "30 µ", "En (mm)", "Kalınlık")


class HandoverSecrecyTests(TestCase):
    def setUp(self):
        self.w = World()
        self.v1, self.v2 = Shift.objects.get(code="V1"), Shift.objects.get(code="V2")
        self.day = User.objects.create_user("9881", PASSWORD, full_name="Gündüz Op.", role=Role.OPERATOR, shift=self.v1)
        self.night = User.objects.create_user("9882", PASSWORD, full_name="Gece Op.", role=Role.OPERATOR, shift=self.v2)
        for u in (self.day, self.night):
            UserMachine.objects.create(user=u, machine=self.w.ext)
        self.e2 = cat.create_definition(self.w.admin, Machine, {"code": "E8", "name": "Ext 8",
                                                                "machine_type": MachineType.EXTRUDER})
        today = timezone.localdate()
        wo = self.w.work_order(machines=[self.w.ext])
        op = wo.operations.get()
        lifecycle.schedule_operation(self.w.foreman, op, planned_date=today, shift=self.v1, machine=self.w.ext,
                                     queue_position=1)
        prod.start_work_order(self.w.foreman, wo)
        self.wo = wo
        self.op = WorkOrderOperation.objects.get(pk=op.pk)
        self.coil = self.w.coil(wo, kg=50)
        self.ins = instructions.issue_for_print(self.w.foreman, self.op)
        # Başka makinedeki (E8) gece işi: gece operatörü bu makineye atanmadı.
        other = self.w.work_order(machines=[self.e2])
        oop = other.operations.get()
        lifecycle.schedule_operation(self.w.foreman, oop, planned_date=today, shift=self.v2, machine=self.e2,
                                     queue_position=1)
        self.other_op = WorkOrderOperation.objects.get(pk=oop.pk)
        self.other_ins = instructions.issue_for_print(self.w.foreman, self.other_op)

    def handover(self):
        h = lifecycle.create_handover(self.w.foreman, self.op, to_shift=self.v2, remaining_note="Kalan 20 kutu")
        lifecycle.acknowledge_handover(self.night, h)

    def get_pages(self):
        wo, op, ins = self.wo.pk, self.op.pk, self.ins.pk
        return {
            "is_emri": reverse("production:workorder_detail", args=[wo]),
            "malzeme_cikis": reverse("production:material_issue", args=[wo]),
            "malzeme_iade": reverse("production:material_return", args=[wo]),
            "bobin": reverse("production:coil_create", args=[op]),
            "palet": reverse("production:pallet_from_production", args=[wo]),
            "kapanis": reverse("production:workorder_close", args=[wo]),
            "devir": reverse("production:handover_create", args=[op]),
            "plan": reverse("production:operation_schedule", args=[op]),
            "talimat": reverse("production:instruction_view", args=[ins]),
            "talimat_yazdir": reverse("production:instruction_view", args=[ins]) + "?yazdir=1",
            "recete": reverse("catalog:recipe_detail", args=[self.wo.product_id]),
            "bobin_detay": reverse("production:coil_detail", args=[self.coil.pk]),
        }

    def test_before_handover_day_operator_has_access(self):
        self.client.force_login(self.day)
        pages = self.get_pages()
        for name in ("malzeme_cikis", "bobin", "palet", "kapanis", "talimat"):
            self.assertEqual(self.client.get(pages[name]).status_code, 200, name)
        self.assertContains(self.client.get(pages["talimat"]), "LDPE")

    def test_old_operator_after_handover_sees_no_recipe_anywhere(self):
        self.handover()
        self.client.force_login(self.day)
        allowed = {"is_emri", "bobin_detay"}  # işlem geçmişi açık kalır
        for name, url in self.get_pages().items():
            resp = self.client.get(url)
            if name in allowed:
                self.assertEqual(resp.status_code, 200, name)
            else:
                self.assertIn(resp.status_code, (403, 404), f"{name}: {resp.status_code}")
            body = resp.content.decode()
            for secret in SECRETS:
                self.assertNotIn(secret, body, f"{name} yanıtında '{secret}' görünüyor")
        detail = self.client.get(self.get_pages()["is_emri"])
        self.assertContains(detail, self.coil.code)  # geçmiş: kendi bobin kaydı görünür

    def test_old_operator_post_actions_refused(self):
        self.handover()
        self.client.force_login(self.day)
        k = lambda: uuid.uuid4().hex  # noqa: E731
        posts = {
            "talimat_bas": (reverse("production:instruction_print", args=[self.op.pk]), {"idem_key": k()}),
            "talimat_teslim": (reverse("production:instruction_ack", args=[self.ins.pk, "teslim"]), {"idem_key": k()}),
            "bobin": (reverse("production:coil_create", args=[self.op.pk]),
                      {"idem_key": k(), "weight_kg": "10", "weight_source": "terazi"}),
            "malzeme": (reverse("production:material_issue", args=[self.wo.pk]),
                        {"idem_key": k(), "lot": self.w.lot.pk, "quantity": "5"}),
        }
        coils, movements = self.wo.coils.count(), self.wo.lot_movements.count()
        prints = self.op.instructions.count()
        for name, (url, data) in posts.items():
            resp = self.client.post(url, data)
            self.assertIn(resp.status_code, (403, 404), f"{name}: {resp.status_code}")
        self.assertEqual((self.wo.coils.count(), self.wo.lot_movements.count(), self.op.instructions.count()),
                         (coils, movements, prints))

    def test_new_shift_sees_only_assigned_machine(self):
        self.handover()
        self.client.force_login(self.night)
        resp = self.client.get(reverse("production:instruction_view", args=[self.ins.pk]))
        self.assertContains(resp, "LDPE")
        self.assertEqual(self.client.get(reverse("production:material_issue", args=[self.wo.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("production:instruction_view",
                                                 args=[self.other_ins.pk])).status_code, 404)

    def test_planning_and_admin_keep_access(self):
        self.handover()
        for u in (self.w.planner, self.w.admin):
            self.client.force_login(u)
            self.assertContains(self.client.get(reverse("production:instruction_view", args=[self.ins.pk])), "LDPE")
            self.assertContains(self.client.get(reverse("catalog:recipe_detail", args=[self.wo.product_id])), "SLIP")
            self.assertContains(self.client.get(reverse("production:workorder_detail", args=[self.wo.pk])), "600 mm")
