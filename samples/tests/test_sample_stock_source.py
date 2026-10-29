"""Demo son düzeltme 3: firma stoğundan gelen numune, gönderim/imha öncesinde stok hareketi tamamlanmadan
kapanamaz. Müşteri referansı stoğa dokunmaz; daha önce başka işlemle stoktan çıkmış numune tekrar düşülmez;
gönderim/geri dönüş/imha ikinci düşüm yaratmaz; adet/rulo/kg numune tamamlanmış stok işlemi gibi gösterilmez."""
import threading
import uuid
from decimal import Decimal as D

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from core.exceptions import BusinessRuleError
from core.models import QualityStatus
from core.tests.factories import World
from samples import services as smp
from samples.models import PhysicalStatus, Sample, SampleKind, StockSource
from warehouse import services as wh
from warehouse.models import MovementKind, PalletMovement


def sample(w, pallet=None, boxes=2, kind=SampleKind.TO_CUSTOMER, unit="kutu", stock_source=None):
    return smp.create_sample(w.depot, kind=kind, customer=w.cust_a, product=w.prod_a, pallet=pallet,
                             quantity=D(boxes), unit=unit, room="Numune odası", stock_source=stock_source)


def outflows():
    return PalletMovement.objects.filter(quantity__lt=0).count()


class StockSourceTests(TestCase):
    def setUp(self):
        self.w = World()
        self.pallet = self.w.stock_pallet(boxes=40)

    def boxes(self):
        self.pallet.refresh_from_db()
        return self.pallet.box_count

    def test_send_and_destroy_need_allocation_first(self):
        s = sample(self.w, self.pallet, stock_source=StockSource.FROM_STOCK)
        for fn in (lambda: smp.send(self.w.depot, s, counterpart="Kurgu Kargo"),
                   lambda: smp.destroy(self.w.depot, s, "Deneme")):
            with self.assertRaisesMessage(BusinessRuleError, "Stoktan ayır"):
                fn()
        s.refresh_from_db()
        self.assertEqual((s.physical_status, self.boxes(), outflows()), (PhysicalStatus.STORED, 40, 0))
        smp.allocate_from_pallet(self.w.depot, s, self.pallet, 2)
        smp.send(self.w.depot, s, counterpart="Kurgu Kargo")
        smp.receive_back(self.w.depot, s)
        smp.destroy(self.w.depot, s, "Saklama süresi doldu")
        self.assertEqual((self.boxes(), outflows()), (38, 1))

    def test_default_for_internal_box_sample_is_from_stock(self):
        s = sample(self.w, self.pallet)
        self.assertEqual(s.stock_source, StockSource.FROM_STOCK)
        with self.assertRaisesMessage(BusinessRuleError, "Stoktan ayır"):
            smp.send(self.w.depot, s, counterpart="Kurgu Kargo")

    def test_not_from_stock_is_explicit_and_has_no_effect(self):
        s = sample(self.w, self.pallet, stock_source=StockSource.NOT_FROM_STOCK)
        smp.send(self.w.depot, s, counterpart="Kurgu Kargo")
        self.assertEqual((self.boxes(), outflows()), (40, 0))
        other = sample(self.w, self.pallet, stock_source=StockSource.NOT_FROM_STOCK)
        with self.assertRaisesMessage(BusinessRuleError, "stok dışı"):
            smp.allocate_from_pallet(self.w.depot, other, self.pallet, 2)

    def test_partial_units_cannot_be_from_stock(self):
        with self.assertRaisesMessage(BusinessRuleError, "birim dönüşümü"):
            sample(self.w, self.pallet, boxes=1, unit="kg", stock_source=StockSource.FROM_STOCK)
        kg = sample(self.w, self.pallet, boxes=1, unit="kg")
        self.assertEqual(kg.stock_source, StockSource.NOT_FROM_STOCK)
        self.assertIn("desteklenmiyor", smp.stock_effect(kg))

    def test_customer_reference_never_touches_stock(self):
        ref = smp.create_sample(self.w.depot, kind=SampleKind.CUSTOMER_REFERENCE, customer=self.w.cust_a,
                                quantity=D("1"), unit="adet", stock_source=StockSource.FROM_STOCK)
        self.assertEqual(ref.stock_source, "")
        smp.send(self.w.depot, ref, counterpart="Müşteriye iade")
        self.assertEqual((self.boxes(), outflows()), (40, 0))

    def test_prior_movement_linked_once_without_second_deduction(self):
        damaged = wh.separate_damaged(self.w.depot, self.pallet, 2, "Kurgu: ezik kutular numuneye ayrıldı")
        mv = PalletMovement.objects.get(pallet=self.pallet, kind=MovementKind.DAMAGE_OUT)
        s = sample(self.w, self.pallet, stock_source=StockSource.ALREADY_OUT)
        with self.assertRaisesMessage(BusinessRuleError, "Stoktan ayır"):
            smp.send(self.w.depot, s, counterpart="Kurgu Kargo")
        smp.link_prior_movement(self.w.depot, s, mv)
        smp.send(self.w.depot, s, counterpart="Kurgu Kargo")
        self.assertEqual((self.boxes(), outflows()), (38, 1))
        again = sample(self.w, self.pallet, stock_source=StockSource.ALREADY_OUT)
        with self.assertRaisesMessage(BusinessRuleError, "başka numuneye bağlı"):
            smp.link_prior_movement(self.w.depot, again, mv)
        wrong = sample(self.w, self.pallet, boxes=3, stock_source=StockSource.ALREADY_OUT)
        with self.assertRaisesMessage(BusinessRuleError, "kutu"):
            smp.link_prior_movement(self.w.depot, wrong, PalletMovement.objects.filter(
                pallet=damaged, kind=MovementKind.DAMAGE_IN).first())

    def test_blocked_pallet_cannot_be_sent_to_customer(self):
        s = sample(self.w, self.pallet)
        smp.allocate_from_pallet(self.w.depot, s, self.pallet, 2)
        wh.set_pallet_quality(self.w.admin, self.pallet, QualityStatus.BLOCKED, "Kurgu kalite şüphesi")
        with self.assertRaisesMessage(BusinessRuleError, "blokeli"):
            smp.send(self.w.depot, s, counterpart="Kurgu Kargo")

    def test_double_submit_send(self):
        s = sample(self.w, self.pallet, stock_source=StockSource.NOT_FROM_STOCK)
        self.client.force_login(self.w.depot)
        url = reverse("samples:sample_action", args=[s.pk, "gonder"])
        for _ in range(2):
            self.client.post(url, {"idem_key": "ayni-gonderim", "counterpart": "Kurgu Kargo", "note": ""})
        self.assertEqual(s.events.filter(kind="gonderim").count(), 1)

    def test_legacy_unknown_source_must_be_declared(self):
        s = sample(self.w, self.pallet)
        Sample.objects.filter(pk=s.pk).update(stock_source="")  # eski kayıt
        s.refresh_from_db()
        with self.assertRaisesMessage(BusinessRuleError, "Stok kaynağını"):
            smp.send(self.w.depot, s, counterpart="Kurgu Kargo")
        smp.set_stock_source(self.w.depot, s, StockSource.NOT_FROM_STOCK, "Hattan alındı (kurgusal)")
        smp.send(self.w.depot, s, counterpart="Kurgu Kargo")


class StockSourceRaceTests(TransactionTestCase):
    def test_allocate_and_send_race_never_sends_without_movement(self):
        w = World()
        pallet = w.stock_pallet(boxes=40)
        s = sample(w, pallet)
        barrier, out = threading.Barrier(2), {}

        def run(name, fn):
            try:
                barrier.wait()
                fn()
                out[name] = "ok"
            except BusinessRuleError as exc:
                out[name] = exc.message
            finally:
                connection.close()
        ts = [threading.Thread(target=run, args=("stok", lambda: smp.allocate_from_pallet(w.depot, s, pallet, 2))),
              threading.Thread(target=run, args=("gonder", lambda: smp.send(w.depot, s, counterpart=f"K{uuid.uuid4()}")))]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        s.refresh_from_db()
        if s.physical_status == PhysicalStatus.SENT:
            self.assertIsNotNone(s.stock_movement_id, out)
        self.assertEqual(out["stok"], "ok", out)
        self.assertEqual(PalletMovement.objects.filter(kind=MovementKind.SAMPLE_OUT).count(), 1)


class StockSourceViewTests(TestCase):
    def setUp(self):
        self.w = World()
        self.pallet = self.w.stock_pallet(boxes=40)
        self.client.force_login(self.w.depot)

    def test_form_requires_explicit_source_and_detail_warns(self):
        data = {"idem_key": "yeni-1", "kind": SampleKind.TO_CUSTOMER, "customer": self.w.cust_a.pk,
                "product": self.w.prod_a.pk, "pallet_code": self.pallet.code, "occurred_at": "2026-10-30T10:00",
                "quantity": "2", "unit": "kutu"}
        resp = self.client.post(reverse("samples:sample_create"), data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "stok kaynağını seçin")
        self.assertFalse(Sample.objects.exists())
        resp = self.client.post(reverse("samples:sample_create"), {**data, "idem_key": "yeni-2",
                                                                    "stock_source": StockSource.FROM_STOCK})
        s = Sample.objects.get()
        detail = self.client.get(reverse("samples:sample_detail", args=[s.pk]))
        self.assertContains(detail, "stok işlemi tamamlanmadı")
        resp = self.client.post(reverse("samples:sample_action", args=[s.pk, "gonder"]),
                                {"idem_key": "g-1", "counterpart": "Kurgu Kargo"}, follow=True)
        self.assertContains(resp, "Stoktan ayır")
        s.refresh_from_db()
        self.assertEqual(s.physical_status, PhysicalStatus.STORED)
