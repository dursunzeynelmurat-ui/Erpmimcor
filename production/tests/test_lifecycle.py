"""Aşama 2: iş emri yaşam döngüsü, ustabaşı planlaması, tek aktif iş, duruş/devir, makine talimatı ve reçete
erişim sınırı."""
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, Shift, User, UserMachine
from catalog.models import Unit
from core.exceptions import BusinessRuleError
from core.tests.factories import PASSWORD, World
from production import instructions, lifecycle
from production import services as prod
from production.models import InstructionStatus, OperationStatus, WorkOrderStatus


class ReleaseWithdrawTests(TestCase):
    def setUp(self):
        self.w = World()
        self.line = self.w.approved_line()

    def draft(self, foreman=True):
        return prod.create_work_order(self.w.planner, order_line=self.line, machines=[self.w.ext, self.w.prn],
                                      target_quantity=D("50"), unit=Unit.BOX,
                                      foreman=self.w.foreman if foreman else None)

    def test_new_work_order_is_draft_and_cannot_start(self):
        wo = self.draft()
        self.assertEqual(wo.status, WorkOrderStatus.DRAFT)
        with self.assertRaisesMessage(BusinessRuleError, "taslak"):
            prod.start_work_order(self.w.foreman, wo)

    def test_release_requires_foreman(self):
        wo = self.draft(foreman=False)
        with self.assertRaisesMessage(BusinessRuleError, "ustabaşı"):
            lifecycle.release(self.w.planner, wo)

    def test_withdraw_edit_and_rerelease_invalidates_printed_instruction(self):
        wo = lifecycle.release(self.w.planner, self.draft())
        op = wo.operations.get(seq=1)
        ins = instructions.issue_for_print(self.w.foreman, op)
        lifecycle.withdraw(self.w.planner, wo, "Müşteri miktarı değiştirdi")
        ins.refresh_from_db()
        self.assertEqual(ins.status, InstructionStatus.CANCELLED)
        lifecycle.update_draft(self.w.planner, wo, target_quantity=D("40"), planned_start=None, notes="",
                               foreman=self.w.foreman, machines=[self.w.ext, self.w.prn], reason="Miktar 40")
        with self.assertRaisesMessage(BusinessRuleError, "rota"):
            lifecycle.update_draft(self.w.planner, wo, target_quantity=D("40"), planned_start=None, notes="",
                                   foreman=self.w.foreman, machines=[self.w.ext], reason="")
        wo = lifecycle.release(self.w.planner, wo)
        self.assertEqual((wo.status, wo.target_quantity), (WorkOrderStatus.PLANNED, D("40")))
        new = instructions.issue_for_print(self.w.foreman, wo.operations.get(seq=1))
        self.assertEqual(new.version, 2)

    def test_started_work_order_cannot_be_withdrawn_or_edited(self):
        wo = lifecycle.release(self.w.planner, self.draft())
        prod.start_work_order(self.w.foreman, wo)
        with self.assertRaises(BusinessRuleError):
            lifecycle.withdraw(self.w.planner, wo, "x")
        with self.assertRaises(BusinessRuleError):
            lifecycle.update_draft(self.w.planner, wo, target_quantity=D("1"), planned_start=None, notes="",
                                   foreman=self.w.foreman, machines=[self.w.ext, self.w.prn])

    def test_revision_change_on_line_is_shown_not_silently_applied(self):
        from catalog import services as cat
        from orders import services as orders
        wo = self.draft()
        new_rev = cat.create_revision(self.w.planner, self.w.prod_a, self.w.spec(thickness_micron=D("35")), self.w.mix(),
                                     reason="Müşteri 35 µ istedi")
        # Taslak iş emri varken kalem yeni revizyonla yeniden onaylanabilir; iş emri revizyonu değişmez, çelişki görünür.
        orders.approve_line(self.w.planner, self.line, new_rev)
        wo.refresh_from_db()
        self.assertNotEqual(wo.recipe_revision_id, new_rev.pk)
        self.assertIn("Rev.", lifecycle.revision_conflict(wo))
        with self.assertRaisesMessage(BusinessRuleError, "Rev."):
            lifecycle.release(self.w.planner, wo)
        lifecycle.update_draft(self.w.planner, wo, target_quantity=D("50"), planned_start=None, notes="",
                               foreman=self.w.foreman, machines=[self.w.ext, self.w.prn], sync_revision=True,
                               reason="Yeni revizyona eşitlendi")
        wo.refresh_from_db()
        self.assertEqual(wo.recipe_revision_id, new_rev.pk)
        lifecycle.release(self.w.planner, wo)


class CancelArchiveTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_cancel_unstarted_then_archive_hides_from_list(self):
        wo = self.w.work_order()
        with self.assertRaisesMessage(BusinessRuleError, "gerekçe"):
            lifecycle.cancel(self.w.planner, wo, "")
        lifecycle.cancel(self.w.planner, wo, "Sipariş iptal")
        lifecycle.archive(self.w.planner, wo)
        self.client.force_login(self.w.planner)
        self.assertNotContains(self.client.get(reverse("production:workorder_list") + "?durum=tumu"), wo.code)
        self.assertContains(self.client.get(reverse("production:workorder_list") + "?durum=arsiv"), wo.code)

    def test_started_cancel_blocked_until_material_returned_and_coils_resolved(self):
        wo = self.w.started_wo()
        prod.issue_material(self.w.foreman, wo, self.w.lot, D("20"))
        op = wo.operations.get(seq=1)
        with self.assertRaisesMessage(BusinessRuleError, "devam eden operasyon"):
            lifecycle.cancel(self.w.planner, wo, "Müşteri vazgeçti")
        lifecycle.stop_operation(self.w.foreman, op, "diger", "İptal kararı")
        with self.assertRaisesMessage(BusinessRuleError, "iade edilmedi"):
            lifecycle.cancel(self.w.planner, wo, "Müşteri vazgeçti")
        prod.return_material(self.w.foreman, wo, self.w.lot, D("20"))
        lifecycle.cancel(self.w.planner, wo, "Müşteri vazgeçti")
        wo.refresh_from_db()
        self.assertEqual(wo.status, WorkOrderStatus.CANCELLED)
        self.w.lot.refresh_from_db()
        self.assertEqual(self.w.lot.quantity, D("1000"))  # malzeme sahipsiz kalmadı

    def test_archive_requires_final_state(self):
        with self.assertRaises(BusinessRuleError):
            lifecycle.archive(self.w.planner, self.w.work_order())


class MachineAndScheduleTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_single_active_job_per_machine_and_stop_frees_it(self):
        a = self.w.work_order(machines=[self.w.ext])
        b = self.w.work_order(machines=[self.w.ext])
        prod.start_work_order(self.w.foreman, a)
        with self.assertRaisesMessage(BusinessRuleError, "devam ediyor"):
            prod.start_work_order(self.w.foreman, b)
        op_a = a.operations.get()
        lifecycle.stop_operation(self.w.foreman, op_a, "ariza", "Motor")
        prod.start_work_order(self.w.foreman, b)
        with self.assertRaisesMessage(BusinessRuleError, "devam ediyor"):
            lifecycle.resume_operation(self.w.foreman, op_a)
        self.assertEqual(op_a.stops.get().reason, "ariza")

    def test_schedule_validates_machine_type_and_lists_affected(self):
        v1 = Shift.objects.get(code="V1")
        a, b = self.w.work_order(machines=[self.w.ext]), self.w.work_order(machines=[self.w.ext])
        today = timezone.localdate()
        lifecycle.schedule_operation(self.w.foreman, a.operations.get(), planned_date=today, shift=v1,
                                     machine=self.w.ext, queue_position=1)
        _, affected = lifecycle.schedule_operation(self.w.foreman, b.operations.get(), planned_date=today, shift=v1,
                                                   machine=self.w.ext, queue_position=2)
        self.assertEqual([o.work_order_id for o in affected], [a.pk])
        with self.assertRaisesMessage(BusinessRuleError, "Extruder"):
            lifecycle.schedule_operation(self.w.foreman, a.operations.get(), planned_date=today, shift=v1,
                                         machine=self.w.prn, queue_position=1)

    def test_foreman_cannot_schedule_unassigned_work_order(self):
        other = User.objects.create_user("9411", PASSWORD, full_name="Diğer Usta", role=Role.PRODUCTION)
        wo = self.w.work_order()
        with self.assertRaises(Exception):
            lifecycle.schedule_operation(other, wo.operations.get(seq=1), planned_date=None, shift=None,
                                         machine=self.w.ext, queue_position=None)


class HandoverTests(TestCase):
    def setUp(self):
        self.w = World()
        self.wo = self.w.started_wo()
        self.op = self.wo.operations.get(seq=1)
        self.coil = self.w.coil(self.wo, kg=D("70"))

    def test_handover_keeps_coils_and_requires_other_person(self):
        v2 = Shift.objects.get(code="V2")
        h = lifecycle.create_handover(self.w.foreman, self.op, to_shift=v2, remaining_note="1 bobin daha, ~70 kg",
                                      machine_side_material="LDPE ~15 kg makine başında")
        self.assertEqual([c["code"] for c in h.open_coils], [self.coil.code])
        with self.assertRaisesMessage(BusinessRuleError, "kendi devrini"):
            lifecycle.acknowledge_handover(self.w.foreman, h)
        night = User.objects.create_user("9412", PASSWORD, full_name="Gece Ustası", role=Role.PRODUCTION_MANAGER)
        lifecycle.acknowledge_handover(night, h)
        h.refresh_from_db()
        self.assertEqual(h.acknowledged_by, night)
        self.wo.refresh_from_db()
        self.assertEqual(self.wo.status, WorkOrderStatus.STARTED)  # iş emri ve kalan hedef aynen sürer


class InstructionAndRecipeAccessTests(TestCase):
    def setUp(self):
        self.w = World()
        self.wo = self.w.work_order(machines=[self.w.ext, self.w.prn])
        self.op1, self.op2 = self.wo.operations.order_by("seq")
        self.operator = User.objects.create_user("9413", PASSWORD, full_name="Matbaa Ustası", role=Role.OPERATOR)
        UserMachine.objects.create(user=self.operator, machine=self.w.prn)

    def test_reprint_same_content_reuses_version_change_supersedes(self):
        a = instructions.issue_for_print(self.w.foreman, self.op1)
        b = instructions.issue_for_print(self.w.foreman, self.op1)
        self.assertEqual((a.pk, a.prints.count()), (b.pk, 2))
        lifecycle.schedule_operation(self.w.foreman, self.op1, planned_date=timezone.localdate(),
                                     shift=Shift.objects.get(code="V1"), machine=self.w.ext, queue_position=1)
        a.refresh_from_db()
        self.assertEqual(a.status, InstructionStatus.SUPERSEDED)
        c = instructions.issue_for_print(self.w.foreman, self.op1)
        self.assertEqual(c.version, 2)
        self.assertEqual(c.snapshot["vardiya"], str(Shift.objects.get(code="V1")))
        with self.assertRaisesMessage(BusinessRuleError, "geçersiz"):
            instructions.acknowledge(self.w.foreman, a, "teslim_aldim")
        instructions.acknowledge(self.w.foreman, c, "teslim_aldim")

    def test_technical_subset_per_operation(self):
        self.assertIn("karisim", instructions.technical_info(self.op1))
        self.assertNotIn("karisim", instructions.technical_info(self.op2))

    def test_operator_sees_only_own_operation_and_never_full_recipe(self):
        self.client.force_login(self.operator)
        resp = self.client.get(reverse("production:workorder_detail", args=[self.wo.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "LDPE %98")  # extruder karışımı matbaa ustasına gösterilmez
        self.assertContains(resp, "Bu operasyonun teknik bilgisi")
        self.assertEqual(self.client.get(reverse("catalog:recipe_detail", args=[self.w.prod_a.pk])).status_code, 403)
        ins = instructions.issue_for_print(self.w.foreman, self.op1)
        self.assertEqual(self.client.get(reverse("production:instruction_view", args=[ins.pk])).status_code, 404)

    def test_access_to_instruction_closes_with_work_order(self):
        ins = instructions.issue_for_print(self.w.foreman, self.op1)
        self.client.force_login(self.w.foreman)
        self.assertEqual(self.client.get(reverse("production:instruction_view", args=[ins.pk])).status_code, 200)
        lifecycle.cancel(self.w.planner, self.wo, "Sipariş iptal")
        self.assertEqual(self.client.get(reverse("production:instruction_view", args=[ins.pk])).status_code, 403)
        ins.refresh_from_db()
        self.assertEqual(ins.status, InstructionStatus.CANCELLED)
        self.client.force_login(self.w.planner)  # reçete yetkilisi kapanmış işte de görür (geçersiz damgalı)
        self.assertContains(self.client.get(reverse("production:instruction_view", args=[ins.pk])), "GEÇERSİZ")

    def test_product_history_hides_recipe_entries_without_recipe_permission(self):
        self.client.force_login(self.w.sales)
        resp = self.client.get(reverse("catalog:definition_edit", args=["urunler", self.w.prod_a.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Reçete ve revizyonlar")
        self.assertNotContains(resp, "%98")

    def test_print_and_ack_through_views(self):
        import uuid
        self.client.force_login(self.w.foreman)
        resp = self.client.post(reverse("production:instruction_print", args=[self.op1.pk]),
                                {"idem_key": uuid.uuid4().hex})
        ins = self.op1.instructions.get()
        self.assertRedirects(resp, reverse("production:instruction_view", args=[ins.pk]) + "?yazdir=1")
        page = self.client.get(reverse("production:instruction_view", args=[ins.pk]))
        self.assertContains(page, self.wo.code)
        self.assertContains(page, "Rev.")
        self.client.post(reverse("production:instruction_ack", args=[ins.pk, "teslim_aldim"]),
                         {"idem_key": uuid.uuid4().hex})
        self.assertEqual(ins.acks.get().user, self.w.foreman)
        # usta/operatör şablonunda basım yetkisi yok (403); ustabaşı başka ustabaşının işini göremez (404)
        self.client.force_login(self.operator)
        self.assertEqual(self.client.post(reverse("production:instruction_print", args=[self.op1.pk]),
                                          {"idem_key": uuid.uuid4().hex}).status_code, 403)
        other = User.objects.create_user("9414", PASSWORD, full_name="Diğer Usta", role=Role.PRODUCTION)
        self.client.force_login(other)
        self.assertEqual(self.client.post(reverse("production:instruction_print", args=[self.op1.pk]),
                                          {"idem_key": uuid.uuid4().hex}).status_code, 404)


class PageRenderTests(TestCase):
    """Yeni ekranlar yetkili kullanıcıda açılır, yetkisiz kullanıcıda kapalıdır."""

    def test_pages(self):
        w = World()
        draft = prod.create_work_order(w.planner, order_line=w.approved_line(), machines=[w.ext, w.prn],
                                       target_quantity=D("50"), unit=Unit.BOX, foreman=w.foreman)
        started = w.started_wo(machines=[w.free_extruder()])
        op = started.operations.get(seq=1)
        line = draft.order_line
        cases = [
            (w.planner, reverse("production:workorder_detail", args=[draft.pk]), "Serbest bırak"),
            (w.planner, reverse("production:workorder_edit", args=[draft.pk]), "Değişiklikleri kaydet"),
            (w.foreman, reverse("production:workorder_detail", args=[started.pk]), "Vardiya devri"),
            (w.foreman, reverse("production:operation_schedule", args=[draft.operations.get(seq=1).pk]), "Planı kaydet"),
            (w.foreman, reverse("production:handover_create", args=[op.pk]), "Devri kaydet"),
            (w.foreman, reverse("production:floor_plan"), "Fiilî durum"),
            (w.sales, reverse("orders:line_change", args=[line.pk]), "Etkilenecek kayıtlar"),
            (w.sales, reverse("orders:order_detail", args=[line.order.pk]), "Tekrar sipariş"),
        ]
        for user, url, text in cases:
            self.client.force_login(user)
            self.assertContains(self.client.get(url), text, msg_prefix=url)
        self.client.force_login(w.sales)
        self.assertEqual(self.client.get(reverse("production:workorder_edit", args=[draft.pk])).status_code, 403)
        self.client.force_login(w.depot)
        self.assertEqual(self.client.get(reverse("production:handover_create", args=[op.pk])).status_code, 403)
