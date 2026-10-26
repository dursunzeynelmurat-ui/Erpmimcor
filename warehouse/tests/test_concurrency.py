"""Gerçek eş zamanlılık testleri: ayrı thread = ayrı PostgreSQL bağlantısı.

Sıra zamanlamaya değil koşullara dayanır: A thread'i hedef satırı kilitleyip bekler; ana thread, B'nin
PostgreSQL'de gerçekten kilit beklediğini pg_stat_activity'den görene kadar (süre sınırlı) bekler; sonra A devam eder.
Beklenen iş kuralı hataları (BusinessRuleError) ile teknik hatalar (kilitlenme, IntegrityError vb.) ayrı doğrulanır.
"""
import threading
import time
from decimal import Decimal as D

from django.db import connection, transaction
from django.test import TransactionTestCase

from core.exceptions import BusinessRuleError
from core.models import QualityStatus
from core.services import perform_once
from core.tests.factories import World, load
from production import services as prod
from production.models import Coil, CoilLink, WorkOrder
from warehouse import services as wh
from warehouse.models import MovementKind, Pallet, PalletMovement, Shipment, ShipmentStatus

TIMEOUT = 20  # saniye; hiçbir bekleme bunu aşamaz


class Worker(threading.Thread):
    def __init__(self, name, fn):
        super().__init__(name=name, daemon=True)
        self.fn = fn
        self.result = None
        self.error = None

    def run(self):
        try:
            with connection.cursor() as cur:
                # Kilit sonsuza kadar beklenmez; aşılırsa teknik hata olarak raporlanır.
                cur.execute("SET lock_timeout = '10s'")
            self.result = self.fn()
        except BaseException as exc:  # noqa: BLE001 - ana teste aktarılır
            self.error = exc
        finally:
            connection.close()


class ConcurrencyTestCase(TransactionTestCase):
    def setUp(self):
        self.a_locked = threading.Event()
        self.b_waiting = threading.Event()

    def wait_event(self, event, what):
        if not event.wait(TIMEOUT):
            raise RuntimeError(f"Süre aşıldı: {what}")

    def wait_for_lock_waiter(self):
        """B'nin veritabanında kilit beklediğini doğrula (koşul beklemesi, süre sınırlı)."""
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            with connection.cursor() as cur:
                cur.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                            "AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()")
                if cur.fetchone()[0] > 0:
                    return
            time.sleep(0.005)  # yalnız yoklama aralığı; doğruluk koşula bağlı
        self.fail("İkinci işlem kilit beklemesine girmedi (süre aşıldı).")

    def run_race(self, a_fn, b_fn):
        a = Worker("A", a_fn)
        b = Worker("B", lambda: (self.wait_event(self.a_locked, "A kilidi"), b_fn())[1])
        a.start()
        b.start()
        try:
            if not self.a_locked.wait(TIMEOUT):
                self.fail(f"A kilidi alamadı: {a.error!r}")
            self.wait_for_lock_waiter()
        finally:
            self.b_waiting.set()
            a.join(TIMEOUT)
            b.join(TIMEOUT)
        self.assertFalse(a.is_alive() or b.is_alive(), "Thread süre içinde bitmedi (olası kilitlenme).")
        return a, b

    def assert_one_success_one_rule_error(self, a, b, message):
        for w in (a, b):
            if w.error is not None and not isinstance(w.error, BusinessRuleError):
                self.fail(f"{w.name} teknik hata verdi (iş kuralı hatası bekleniyordu): {w.error!r}")
        self.assertIsNone(a.error, f"A başarısız: {a.error!r}")
        self.assertIsInstance(b.error, BusinessRuleError, "B iş kuralı hatası ile reddedilmeliydi.")
        self.assertIn(message, b.error.message)


class ConcurrentShipmentTests(ConcurrencyTestCase):
    def setUp(self):
        super().setUp()
        self.w = World()
        self.pallet = self.w.stock_pallet(boxes=40)
        self.order = self.w.order(qty=D("10"))
        res = wh.reserve(self.w.sales, self.pallet, self.order.lines.get(), 10)
        self.shipment = wh.create_shipment(self.w.depot, self.order)
        wh.add_shipment_line(self.w.depot, self.shipment, res, 10)
        load(self.w.depot, self.shipment)  # hazırlandı + yükleme kontrolü; yarış yalnız sevk onayında

    def assert_single_shipment(self):
        self.assertEqual(PalletMovement.objects.filter(kind=MovementKind.SHIPMENT).count(), 1)
        self.assertEqual(Pallet.objects.get(pk=self.pallet.pk).box_count, 30)
        self.assertEqual(Shipment.objects.get(pk=self.shipment.pk).status, ShipmentStatus.SHIPPED)

    def test_same_shipment_confirmed_twice_concurrently(self):
        def a():
            with transaction.atomic():
                Shipment.objects.select_for_update().get(pk=self.shipment.pk)
                self.a_locked.set()
                self.wait_event(self.b_waiting, "B beklemesi")
                return wh.confirm_shipment(self.w.depot, self.shipment)

        a, b = self.run_race(a, lambda: wh.confirm_shipment(self.w.depot, self.shipment))
        self.assert_one_success_one_rule_error(a, b, "zaten sevk edildi")
        self.assert_single_shipment()

    def test_same_form_key_submitted_twice_concurrently(self):
        def confirm():
            return wh.confirm_shipment(self.w.depot, self.shipment)

        def a_fn():
            self.a_locked.set()  # anahtar satırı eklendi, transaction açık
            self.wait_event(self.b_waiting, "B beklemesi")
            return confirm()

        a, b = self.run_race(
            lambda: perform_once(user=self.w.depot, key="ayni-anahtar", action="shipment.confirm", fn=a_fn),
            lambda: perform_once(user=self.w.depot, key="ayni-anahtar", action="shipment.confirm", fn=confirm))
        self.assertIsNone(a.error, repr(a.error))
        self.assertIsNone(b.error, repr(b.error))
        self.assertFalse(a.result.duplicate)
        self.assertTrue(b.result.duplicate)
        self.assert_single_shipment()

    def test_coil_block_and_shipment_race(self):
        """Blokaj kilitleri önce alırsa sevk bekler ve blokajı görerek reddedilir; stok değişmez."""
        coil = self.pallet.pallet_coils.get().coil

        def a():
            with transaction.atomic():
                prod.set_coil_quality(self.w.foreman, coil, QualityStatus.BLOCKED, "Eş zamanlı blokaj")
                self.a_locked.set()
                self.wait_event(self.b_waiting, "B beklemesi")

        a, b = self.run_race(a, lambda: wh.confirm_shipment(self.w.depot, self.shipment))
        self.assert_one_success_one_rule_error(a, b, "bobin blokeli")
        self.assertEqual(PalletMovement.objects.filter(kind=MovementKind.SHIPMENT).count(), 0)
        self.assertEqual(Pallet.objects.get(pk=self.pallet.pk).box_count, 40)
        self.assertEqual(Shipment.objects.get(pk=self.shipment.pk).status, ShipmentStatus.PREPARED)


class ConcurrentCoilConsumptionTests(ConcurrencyTestCase):
    def setUp(self):
        super().setUp()
        self.w = World()
        self.wo = self.w.started_wo(machines=[self.w.ext, self.w.slit], stock=True)
        self.src = self.w.coil(self.wo, D("100"))
        self.op = self.wo.operations.get(seq=2)
        prod.start_operation(self.w.foreman, self.op)

    def consume(self):
        return prod.record_coil(self.w.foreman, self.op, weight_kg=D("60"), sources=[(self.src, D("60"))])

    def test_competing_consumption_of_same_source(self):
        def a():
            with transaction.atomic():
                # Servisin ilk aldığı kilit (iş emri) tutulur; B aynı sırayla kilit almaya çalışıp bekler.
                WorkOrder.objects.select_for_update().get(pk=self.wo.pk)
                Coil.objects.select_for_update().get(pk=self.src.pk)
                self.a_locked.set()
                self.wait_event(self.b_waiting, "B beklemesi")
                return self.consume()

        a, b = self.run_race(a, self.consume)
        self.assert_one_success_one_rule_error(a, b, "kalan miktarı 40")
        self.src.refresh_from_db()
        self.assertEqual(self.src.remaining_kg, D("40"))
        self.assertEqual(CoilLink.objects.filter(source=self.src).count(), 1)
        self.assertEqual(Coil.objects.filter(operation=self.op).count(), 1)
