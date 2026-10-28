"""Kabul denetimi: iade ve yerine gönderim uçtan uca.

10 kutu sevk → 2 iade → 2 yerine gönderim: ilk satış 10 kalır, müşteride net 10 olur. İkinci veya eş zamanlı yerine
gönderim, kalem değişikliği veya sipariş kopyası hakkı aşamaz; iptal hakkı bir kez serbest bırakır; iade yalnız
araç çıkışı yapılmış (gerçekten teslim edilmiş) ve daha önce iade alınmamış miktar için alınır."""
import threading
import uuid
from decimal import Decimal as D

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from core.exceptions import BusinessRuleError
from core.models import QualityStatus
from core.tests.factories import World, load
from orders import services as orders
from warehouse import services as wh
from warehouse.models import ReturnReceipt
from warehouse.tests.test_replacement import shipped_line


def ship(w, line, pallet, qty, exit_=True):
    res = wh.reserve(w.depot, pallet, line, qty)
    s = wh.create_shipment(w.depot, line.order)
    wh.add_shipment_line(w.depot, s, res, qty)
    wh.confirm_shipment(w.store, load(w.depot, s))
    if exit_:
        wh.mark_exited(w.store, s)
    return s


class ReturnReplacementAcceptanceTests(TestCase):
    def setUp(self):
        self.w = World()
        self.order, self.line, self.sl, self.pallet = shipped_line(self.w)

    def test_10_2_2_original_sale_stays_net_ten(self):
        receipt = wh.receive_return(self.w.depot, self.sl, 2, "2 kutu ezik")
        repl = wh.create_replacement_line(self.w.sales, receipt, 2, "Yerine gönder")
        with self.assertRaisesMessage(BusinessRuleError, "en fazla 0"):
            wh.create_replacement_line(self.w.sales, receipt, 1, "İkinci kez")
        ship(self.w, repl, self.pallet, 2)
        self.line.refresh_from_db()
        self.assertEqual(self.line.quantity, 10)  # ilk satış değişmez
        f = wh.line_fulfilment(self.line)
        self.assertEqual((f["shipped"], f["returned"], f["replacement_planned"], f["replacement_shipped"], f["net"]),
                         (10, 2, 2, 2, 10))

    def test_return_only_after_vehicle_exit(self):
        pallet = self.w.stock_pallet(boxes=10)
        order = self.w.order(qty=D("5"))
        s = ship(self.w, order.lines.get(), pallet, 5, exit_=False)  # sevk onaylı, araç çıkmadı
        with self.assertRaisesMessage(BusinessRuleError, "araç çıkışı"):
            wh.receive_return(self.w.depot, s.lines.get(), 2, "Henüz teslim edilmedi")

    def test_undo_refused_when_return_exists(self):
        """Eski sürümde çıkış öncesi alınmış iade (yükseltilmiş veri) varsa sevk geri alma stoğu ikinci kez artırmaz."""
        pallet = self.w.stock_pallet(boxes=10)
        order = self.w.order(qty=D("5"))
        s = ship(self.w, order.lines.get(), pallet, 5, exit_=False)
        sl = s.lines.get()
        ret_pallet = self.w.stock_pallet(boxes=2, accept=False, quality_ok=False)
        ReturnReceipt.objects.create(code="IAD-ESKI", shipment_line=sl, quantity=2, reason="Eski kayıt",
                                     pallet=ret_pallet, created_by=self.w.depot)
        with self.assertRaisesMessage(BusinessRuleError, "iade"):
            wh.undo_shipment(self.w.store, s, "Geri al")

    def test_change_line_cannot_exceed_return_entitlement(self):
        receipt = wh.receive_return(self.w.depot, self.sl, 2, "Ezik")
        repl = wh.create_replacement_line(self.w.sales, receipt, 1, "Önce 1")
        with self.assertRaisesMessage(BusinessRuleError, "en fazla 2"):
            orders.change_line(self.w.sales, repl, quantity=D("3"), due_date=repl.due_date, reason="Arttır",
                               confirm_impacts=True)
        orders.change_line(self.w.sales, repl, quantity=D("2"), due_date=repl.due_date, reason="Hak kadar",
                           confirm_impacts=True)
        repl.refresh_from_db()
        self.assertEqual(repl.quantity, 2)

    def test_copy_order_does_not_turn_replacement_into_sale(self):
        receipt = wh.receive_return(self.w.depot, self.sl, 2, "Ezik")
        wh.create_replacement_line(self.w.sales, receipt, 2, "Yerine")
        new = orders.copy_order(self.w.sales, self.order)
        self.assertEqual([(ln.product_id, ln.quantity) for ln in new.lines.all()], [(self.line.product_id, 10)])
        self.assertFalse(new.lines.filter(replacement_for__isnull=False).exists())

    def test_cancel_frees_entitlement_once(self):
        receipt = wh.receive_return(self.w.depot, self.sl, 2, "Ezik")
        repl = wh.create_replacement_line(self.w.sales, receipt, 2, "Yerine")
        orders.cancel_line(self.w.sales, repl, "Müşteri istemedi")
        with self.assertRaises(BusinessRuleError):
            orders.cancel_line(self.w.sales, repl, "Tekrar iptal")
        wh.create_replacement_line(self.w.sales, receipt, 2, "Yeniden")
        with self.assertRaisesMessage(BusinessRuleError, "en fazla 0"):
            wh.create_replacement_line(self.w.sales, receipt, 1, "Fazla")

    def test_every_stage_counts_against_entitlement(self):
        """Ayrılmış, hazırlanmış, sevk onaylı, çıkışlı ve sevki geri alınmış yerine gönderim kalemi hakkı tüketir;
        yalnız iptal edilen kalem hakkı geri verir. Müşteride net yalnız sevk edilen yerine gönderimi sayar."""
        receipt = wh.receive_return(self.w.depot, self.sl, 5, "Ezik")
        lines = [wh.create_replacement_line(self.w.sales, receipt, 1, f"Parça {i}") for i in range(5)]
        reserved, prepared, shipped, exited, undone = lines
        wh.reserve(self.w.depot, self.pallet, reserved, 1)
        res = wh.reserve(self.w.depot, self.pallet, prepared, 1)
        s = wh.create_shipment(self.w.depot, self.order)
        wh.add_shipment_line(self.w.depot, s, res, 1)
        load(self.w.depot, s)
        ship(self.w, shipped, self.pallet, 1, exit_=False)
        ship(self.w, exited, self.pallet, 1)
        su = ship(self.w, undone, self.pallet, 1, exit_=False)
        wh.undo_shipment(self.w.store, su, "Yanlış araç")
        self.assertEqual(wh.replaced_quantity(receipt), 5)
        with self.assertRaisesMessage(BusinessRuleError, "en fazla 0"):
            wh.create_replacement_line(self.w.sales, receipt, 1, "Fazla")
        f = wh.line_fulfilment(self.line)
        self.assertEqual((f["returned"], f["replacement_planned"], f["replacement_shipped"], f["net"]), (5, 5, 2, 7))

    def test_return_of_replacement_reduces_net(self):
        receipt = wh.receive_return(self.w.depot, self.sl, 2, "Ezik")
        repl = wh.create_replacement_line(self.w.sales, receipt, 2, "Yerine")
        s = ship(self.w, repl, self.pallet, 2)
        wh.receive_return(self.w.depot, s.lines.get(), 1, "Yerine gönderilen de ezik")
        self.assertEqual(wh.line_fulfilment(self.line)["net"], 9)

    def test_blocked_return_pallet_not_released_by_replacement(self):
        receipt = wh.receive_return(self.w.depot, self.sl, 2, "Ezik")
        wh.set_pallet_quality(self.w.foreman, receipt.pallet, QualityStatus.BLOCKED, "Hasarlı")
        repl = wh.create_replacement_line(self.w.sales, receipt, 2, "Yerine")
        ship(self.w, repl, self.pallet, 2)
        receipt.pallet.refresh_from_db()
        self.assertEqual(receipt.pallet.quality_status, QualityStatus.BLOCKED)

    def test_replacement_product_and_customer_fixed(self):
        receipt = wh.receive_return(self.w.depot, self.sl, 2, "Ezik")
        repl = wh.create_replacement_line(self.w.sales, receipt, 2, "Yerine")
        self.client.force_login(self.w.sales)
        self.client.post(reverse("orders:line_change", args=[repl.pk]),
                         {"idem_key": uuid.uuid4().hex, "quantity": "2", "reason": "x", "confirm_impacts": "on",
                          "product": self.w.prod_b.pk, "customer": self.w.prod_b.customer_id})
        repl.refresh_from_db()
        self.assertEqual((repl.product_id, repl.order.customer_id), (self.line.product_id, self.order.customer_id))
        self.assertEqual(repl.replacement_for, receipt)


class ConcurrentReplacementTests(TransactionTestCase):
    def test_parallel_replacements_do_not_exceed_return(self):
        w = World()
        _, _, sl, _ = shipped_line(w)
        receipt = wh.receive_return(w.depot, sl, 2, "Ezik")
        barrier, out = threading.Barrier(2), []

        def run():
            try:
                barrier.wait()
                wh.create_replacement_line(w.sales, receipt, 2, "Paralel")
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
        self.assertEqual(wh.replaced_quantity(receipt), 2)
