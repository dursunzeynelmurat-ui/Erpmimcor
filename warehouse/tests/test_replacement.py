"""İade → yerine gönderim bağlantısı; ilk sevk, iade, yerine gönderim ve müşteride kalan net ayrı izlenir;
iade/yerine gönderim miktar sınırlarını dolaylı aşmanın yolu değildir."""
import threading
import uuid
from decimal import Decimal as D

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from core.exceptions import BusinessRuleError
from core.models import QualityStatus
from core.tests.factories import World, load
from orders.models import OrderStatus
from warehouse import services as wh


def shipped_line(w, qty=10, boxes=40):
    pallet = w.stock_pallet(boxes=boxes)
    order = w.order(qty=D(qty))
    line = order.lines.get()
    res = wh.reserve(w.depot, pallet, line, qty)
    s = wh.create_shipment(w.depot, order)
    wh.add_shipment_line(w.depot, s, res, qty)
    wh.confirm_shipment(w.store, load(w.depot, s))
    wh.mark_exited(w.store, s)
    return order, line, s.lines.get(), pallet


class ReplacementTests(TestCase):
    def setUp(self):
        self.w = World()
        self.order, self.line, self.sl, self.pallet = shipped_line(self.w)

    def test_full_chain_and_net(self):
        receipt = wh.receive_return(self.w.depot, self.sl, 3, "3 kutu ezik")
        self.assertEqual(receipt.pallet.quality_status, QualityStatus.PENDING)
        repl = wh.create_replacement_line(self.w.sales, receipt, 3, "Müşteriye 3 kutu yeniden gönderilecek")
        self.assertEqual((repl.replacement_for, repl.order_id, repl.approved_revision_id),
                         (receipt, self.order.pk, self.line.approved_revision_id))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PARTIAL)  # yerine gönderim bekliyor
        f = wh.line_fulfilment(self.line)
        self.assertEqual((f["shipped"], f["returned"], f["replacement_shipped"], f["net"]), (10, 3, 0, 7))
        res = wh.reserve(self.w.depot, self.pallet, repl, 3)
        s = wh.create_shipment(self.w.depot, self.order)
        wh.add_shipment_line(self.w.depot, s, res, 3)
        wh.confirm_shipment(self.w.store, load(self.w.depot, s))
        f = wh.line_fulfilment(self.line)
        self.assertEqual((f["replacement_shipped"], f["net"]), (3, 10))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.DONE)

    def test_limits(self):
        receipt = wh.receive_return(self.w.depot, self.sl, 3, "Ezik")
        with self.assertRaisesMessage(BusinessRuleError, "en fazla 3"):
            wh.create_replacement_line(self.w.sales, receipt, 4, "Fazla")
        wh.create_replacement_line(self.w.sales, receipt, 2, "İlk")
        with self.assertRaisesMessage(BusinessRuleError, "en fazla 1"):
            wh.create_replacement_line(self.w.sales, receipt, 2, "Tekrar")
        with self.assertRaisesMessage(BusinessRuleError, "en fazla 7"):
            wh.receive_return(self.w.depot, self.sl, 8, "Sevkten fazla iade")
        repl = receipt.replacement_lines.get()
        with self.assertRaisesMessage(BusinessRuleError, "kalem miktarı"):  # yerine gönderim kalemi de sınırlı
            wh.reserve(self.w.depot, self.pallet, repl, 3)

    def test_permission_and_pages(self):
        receipt = wh.receive_return(self.w.depot, self.sl, 3, "Ezik")
        with self.assertRaises(Exception):
            wh.create_replacement_line(self.w.depot, receipt, 3, "x")
        self.client.force_login(self.w.sales)
        self.client.post(reverse("warehouse:replacement_create", args=[receipt.pk]),
                         {"idem_key": uuid.uuid4().hex, "quantity": "3", "reason": "Yerine gönder"})
        page = self.client.get(reverse("orders:order_detail", args=[self.order.pk]))
        self.assertContains(page, "Yerine gönderim · " + receipt.code)
        self.assertContains(page, "Müşteride kalan net")
        self.assertContains(page, "Yeni satış değildir")


class ConcurrentReturnTests(TransactionTestCase):
    def test_parallel_returns_do_not_exceed_shipped(self):
        w = World()
        _, _, sl, _ = shipped_line(w)
        barrier, out = threading.Barrier(2), []

        def run():
            try:
                barrier.wait()
                wh.receive_return(w.depot, sl, 6, "Paralel iade")
                out.append("ok")
            except BusinessRuleError as exc:
                out.append(exc.message)
            finally:
                connection.close()
        ts = [threading.Thread(target=run) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(out.count("ok"), 1, out)
        self.assertEqual(wh.returnable_quantity(sl), 4)
