"""Aşama 5: fiziksel numune, stok etkisi, kalite kararı kapsamı, yetki ve eş zamanlılık."""
import threading
import uuid
from decimal import Decimal as D

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts import services as acc
from accounts.models import Role, User
from catalog import customers as cs
from core.exceptions import BusinessRuleError
from core.models import QualityStatus
from core.tests.factories import PASSWORD, World
from samples import services as smp
from samples.models import CriterionUnit, MeasurementResult, PhysicalStatus, QualityDecision, SampleEvent, SampleKind
from warehouse import services as wh
from warehouse.models import MovementKind, PalletMovement


def quality_user():
    return User.objects.filter(personnel_no="9951").first() or User.objects.create_user(
        "9951", PASSWORD, full_name="Kalite", role=Role.QUALITY)


def internal(w, boxes=2, kind=SampleKind.RETAINED, unit="kutu"):
    return smp.create_sample(w.depot, kind=kind, customer=w.cust_a, product=w.prod_a, quantity=D(boxes), unit=unit,
                             room="Lab", shelf="R1")


class StockEffectTests(TestCase):
    def setUp(self):
        self.w = World()
        self.pallet = self.w.stock_pallet(boxes=40)

    def sample_moves(self):
        return PalletMovement.objects.filter(kind=MovementKind.SAMPLE_OUT).count()

    def test_allocate_send_return_destroy_single_movement(self):
        s = internal(self.w, 2, SampleKind.TO_CUSTOMER)
        smp.allocate_from_pallet(self.w.depot, s, self.pallet, 2, "Müşteri numunesi")
        self.pallet.refresh_from_db()
        self.assertEqual((self.pallet.box_count, self.sample_moves()), (38, 1))
        with self.assertRaisesMessage(BusinessRuleError, "ikinci kez"):
            smp.allocate_from_pallet(self.w.depot, s, self.pallet, 2)
        smp.send(self.w.depot, s, counterpart="Kurgu Kargo 123")
        smp.receive_back(self.w.depot, s, note="Müşteri geri yolladı")
        smp.destroy(self.w.depot, s, "Saklama süresi doldu")
        self.pallet.refresh_from_db()
        self.assertEqual((self.pallet.box_count, self.sample_moves()), (38, 1))  # iki kez düşmez, geri de eklenmez
        s.refresh_from_db()
        self.assertEqual(s.physical_status, PhysicalStatus.DESTROYED)
        self.assertEqual(s.quality_decision, QualityDecision.PENDING)  # fiziksel durum ≠ kalite kararı

    def test_customer_reference_never_touches_stock(self):
        ref = smp.create_sample(self.w.sales if False else self.w.depot, kind=SampleKind.CUSTOMER_REFERENCE,
                                customer=self.w.cust_a, product=None, quantity=D("1"), unit="adet",
                                counterpart="Müşteri temsilcisi")
        with self.assertRaisesMessage(BusinessRuleError, "firma stoğundan düşülmez"):
            smp.allocate_from_pallet(self.w.depot, ref, self.pallet, 1)
        self.assertEqual(self.sample_moves(), 0)

    def test_reserved_boxes_and_unit_rules(self):
        order = self.w.order(qty=D("39"))
        wh.reserve(self.w.depot, self.pallet, order.lines.get(), 39)
        s = internal(self.w, 2)
        with self.assertRaisesMessage(BusinessRuleError, "serbest"):
            smp.allocate_from_pallet(self.w.depot, s, self.pallet, 2)
        kg = internal(self.w, 1, unit="kg")
        with self.assertRaisesMessage(BusinessRuleError, "birim dönüşümü"):
            smp.allocate_from_pallet(self.w.depot, kg, self.pallet, 1)
        other = self.w.stock_pallet(boxes=5, product=self.w.prod_b)
        with self.assertRaisesMessage(BusinessRuleError, "ürününe ait değil"):
            smp.allocate_from_pallet(self.w.depot, internal(self.w, 1), other, 1)

    def test_double_submit_allocation(self):
        s = internal(self.w, 2)
        self.client.force_login(self.w.depot)
        url = reverse("samples:sample_action", args=[s.pk, "stok"])
        for _ in range(2):
            self.client.post(url, {"idem_key": "same", "pallet_code": self.pallet.code, "boxes": "2"})
        self.assertEqual(self.sample_moves(), 1)


class DecisionScopeTests(TestCase):
    def setUp(self):
        self.w = World()
        self.q = quality_user()

    def test_ok_decision_does_not_release_any_pallet(self):
        covered = self.w.stock_pallet(boxes=10, quality_ok=False)
        unrelated = self.w.stock_pallet(boxes=10, quality_ok=False)
        wh.set_pallet_quality(self.w.foreman, unrelated, QualityStatus.BLOCKED, "Şüpheli")
        s = internal(self.w, 1)
        smp.decide(self.q, s, QualityDecision.OK, "Ölçümler uygun", pallets=[covered])
        covered.refresh_from_db()
        unrelated.refresh_from_db()
        self.assertEqual(covered.quality_status, QualityStatus.PENDING)  # kapsamda ama serbest bırakılmadı
        self.assertEqual(unrelated.quality_status, QualityStatus.BLOCKED)
        self.assertEqual(list(s.decision_pallets.all()), [covered])
        with self.assertRaisesMessage(BusinessRuleError, "ürününe ait değil"):
            smp.decide(self.q, s, QualityDecision.OK, "x", pallets=[self.w.stock_pallet(boxes=1, product=self.w.prod_b)])

    def test_sample_decision_right_is_not_release_right(self):
        role = acc.create_role(self.w.admin, code="numune_karar", name="Numune karar", description="",
                               hr_assignable=False, perms={"sample.view": "all", "sample.decide": "all"})
        u = User.objects.create_user("9952", PASSWORD, full_name="Numune kararcı")
        acc.grant_role(self.w.admin, u, role, "Test")
        blocked = self.w.stock_pallet(boxes=5, quality_ok=False)
        wh.set_pallet_quality(self.w.foreman, blocked, QualityStatus.BLOCKED, "Şüpheli")
        smp.decide(u, internal(self.w, 1), QualityDecision.OK, "Uygun")
        with self.assertRaises(Exception):
            wh.set_pallet_quality(u, blocked, QualityStatus.OK, "Numune uygundu")
        with self.assertRaises(Exception):
            smp.allocate_from_pallet(u, internal(self.w, 1), blocked, 1)

    def test_criteria_change_keeps_old_measurement(self):
        c1 = smp.create_criterion(self.q, name="Kalınlık", unit=CriterionUnit.MICRON, min_value=D("28"),
                                  max_value=D("32"))
        s = internal(self.w, 1)
        m1 = smp.add_measurement(self.q, s, c1, value=D("33"))
        self.assertEqual(m1.result, MeasurementResult.OUT)
        c2 = smp.create_criterion(self.q, name="Kalınlık", unit=CriterionUnit.MICRON, min_value=D("27"),
                                  max_value=D("34"), replaces=c1)
        m1.refresh_from_db()
        self.assertEqual((m1.result, m1.max_value), (MeasurementResult.OUT, D("32")))  # yeniden yorumlanmaz
        with self.assertRaisesMessage(BusinessRuleError, "Pasif kriter"):
            smp.add_measurement(self.q, s, c1, value=D("30"))
        self.assertEqual(smp.add_measurement(self.q, s, c2, value=D("33")).result, MeasurementResult.IN)
        with self.assertRaises(Exception):
            smp.create_criterion(self.w.sales, name="x", unit="mm")


class SampleAccessTests(TestCase):
    def setUp(self):
        self.w = World()
        self.s = internal(self.w, 1)
        self.s.notes = "İç not: hat 2 hız 120"
        self.s.shared_note = "Numune hazır"
        self.s.save()
        smp.decide(quality_user(), self.s, QualityDecision.NOT_OK, "Kalınlık sınır dışı")

    def test_sales_sees_shareable_only_and_records_feedback(self):
        self.client.force_login(self.w.sales)
        page = self.client.get(reverse("samples:sample_detail", args=[self.s.pk]))
        self.assertContains(page, "Numune hazır")
        self.assertNotContains(page, "İç not")
        self.assertNotContains(page, "Kalınlık sınır dışı")
        self.assertNotContains(page, "LDPE")
        self.client.post(reverse("samples:sample_action", args=[self.s.pk, "geri_bildirim"]),
                         {"idem_key": uuid.uuid4().hex, "feedback": "onayladi", "note": "Renk uygun"})
        self.s.refresh_from_db()
        self.assertEqual((self.s.customer_feedback, self.s.quality_decision), ("onayladi", QualityDecision.NOT_OK))
        r = self.client.post(reverse("samples:sample_action", args=[self.s.pk, "imha"]),
                             {"idem_key": uuid.uuid4().hex, "reason": "x"})
        self.assertEqual(r.status_code, 403)

    def test_other_customer_url_and_post_404(self):
        other = User.objects.create_user("9953", PASSWORD, full_name="B satış", role=Role.SALES)
        cs.grant_access(self.w.admin, self.w.cust_b, other, timezone.localdate(), None, "B")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("samples:sample_detail", args=[self.s.pk])).status_code, 404)
        r = self.client.post(reverse("samples:sample_action", args=[self.s.pk, "geri_bildirim"]),
                             {"idem_key": uuid.uuid4().hex, "feedback": "reddetti"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.client.get(reverse("samples:qr_open", args=[self.s.qr_token])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("samples:sample_list")), self.s.code)

    def test_qr_open_changes_nothing(self):
        self.client.force_login(self.w.depot)
        before = (SampleEvent.objects.count(), PalletMovement.objects.count())
        r = self.client.get(reverse("samples:qr_open", args=[self.s.qr_token]))
        self.assertRedirects(r, reverse("samples:sample_detail", args=[self.s.pk]))
        self.assertEqual((SampleEvent.objects.count(), PalletMovement.objects.count()), before)
        self.assertContains(self.client.get(reverse("samples:sample_label", args=[self.s.pk])), "<svg")


class ConcurrentAllocationTests(TransactionTestCase):
    def test_parallel_allocations_respect_free_boxes(self):
        w = World()
        pallet = w.stock_pallet(boxes=3)
        samples = [internal(w, 2), internal(w, 2)]
        barrier, out = threading.Barrier(2), []

        def run(s):
            try:
                barrier.wait()
                smp.allocate_from_pallet(w.depot, s, pallet, 2)
                out.append("ok")
            except BusinessRuleError as exc:
                out.append(exc.message)
            finally:
                connection.close()
        ts = [threading.Thread(target=run, args=(s,)) for s in samples]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(out.count("ok"), 1, out)
        pallet.refresh_from_db()
        self.assertEqual(pallet.box_count, 1)
