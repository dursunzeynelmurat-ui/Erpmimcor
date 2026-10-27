"""Fazla teslim: varsayılan 0; yalnız kaleme verilen gerekçeli, miktarı belirli izinle; izin değişikliği açık
rezervasyon/sevkiyatı dikkate alır; paralel işlemler toplam sınırı aşamaz."""
import threading
from decimal import Decimal as D

from django.db import connection
from django.test import TestCase, TransactionTestCase

from core.exceptions import BusinessRuleError
from core.tests.factories import World, load
from orders import services as orders
from warehouse import services as wh


class AllowanceLifecycleTests(TestCase):
    def setUp(self):
        self.w = World()
        self.line = self.w.approved_line(qty=D("10"))
        self.pallet = self.w.stock_pallet(boxes=40)

    def test_default_zero_and_permission(self):
        self.assertEqual(orders.line_limit(self.line), D("10"))
        with self.assertRaises(Exception):
            orders.set_over_delivery(self.w.depot, self.line, 2, "x")
        with self.assertRaisesMessage(BusinessRuleError, "gerekçe"):
            orders.set_over_delivery(self.w.planner, self.line, 2, "")

    def test_increase_decrease_remove_respect_commitments(self):
        orders.set_over_delivery(self.w.planner, self.line, 3, "Müşteri +3 kabul")
        res = wh.reserve(self.w.depot, self.pallet, self.line, 12)
        with self.assertRaisesMessage(BusinessRuleError, "ayrılmış 12"):
            orders.set_over_delivery(self.w.planner, self.line, 1, "Azalt")
        orders.set_over_delivery(self.w.planner, self.line, 2, "Tam 12'ye indir")
        self.assertEqual(orders.line_limit(self.line), D("12"))
        wh.reduce_reservation(self.w.depot, res, 10, "Müşteri 10 alacak")
        orders.set_over_delivery(self.w.planner, self.line, 0, "İzin kaldırıldı")
        self.assertEqual(orders.line_limit(self.line), D("10"))
        self.assertEqual(self.line.over_delivery.count(), 3)  # geçmiş korunur
        self.assertEqual(self.line.over_delivery.filter(superseded_at__isnull=True).count(), 1)

    def test_shipped_quantity_blocks_reduction(self):
        orders.set_over_delivery(self.w.planner, self.line, 2, "+2")
        res = wh.reserve(self.w.depot, self.pallet, self.line, 12)
        shipment = wh.create_shipment(self.w.depot, self.line.order)
        wh.add_shipment_line(self.w.depot, shipment, res, 12)
        wh.confirm_shipment(self.w.store, load(self.w.depot, shipment))
        with self.assertRaisesMessage(BusinessRuleError, "sevk edilmiş"):
            orders.set_over_delivery(self.w.planner, self.line, 0, "Geri al")


class AllowanceConcurrencyTests(TransactionTestCase):
    def race(self, *fns):
        barrier, out = threading.Barrier(len(fns)), []

        def run(fn):
            try:
                barrier.wait()
                fn()
                out.append("ok")
            except BusinessRuleError as exc:
                out.append(exc.message)
            finally:
                connection.close()
        threads = [threading.Thread(target=run, args=(f,)) for f in fns]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return out

    def test_reduce_and_reserve_in_parallel_never_exceed(self):
        w = World()
        line = w.approved_line(qty=D("10"))
        orders.set_over_delivery(w.planner, line, 2, "+2")
        p1 = w.stock_pallet(boxes=40)
        self.race(lambda: wh.reserve(w.depot, p1, line, 12),
                  lambda: orders.set_over_delivery(w.planner, line, 0, "Kaldır"))
        self.assertLessEqual(orders.committed_quantity(line), orders.line_limit(line))

    def test_parallel_reservations_with_allowance(self):
        w = World()
        line = w.approved_line(qty=D("10"))
        orders.set_over_delivery(w.planner, line, 2, "+2")
        p1, p2 = w.stock_pallet(boxes=40), w.stock_pallet(boxes=40)
        out = self.race(lambda: wh.reserve(w.depot, p1, line, 7), lambda: wh.reserve(w.depot, p2, line, 7))
        self.assertEqual(out.count("ok"), 1, out)
        self.assertLessEqual(orders.committed_quantity(line), 12)
