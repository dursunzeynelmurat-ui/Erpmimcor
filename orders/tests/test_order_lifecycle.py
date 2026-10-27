"""Aşama 2: sipariş taslak/onay, taslak silme, kalem değişikliği (etkilenen kayıtlar), kontrollü kopya, iptal,
üst durum, arşiv; taslak/iptal sevkiyatın listeden düşmesi."""
import uuid
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from catalog.models import Unit
from core.models import AuditLog
from core.exceptions import BusinessRuleError
from core.tests.factories import PASSWORD, World, load
from orders import services as orders
from orders.models import LineStatus, OrderStatus, SalesOrder


class DraftApproveTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_draft_not_visible_to_planning_and_cannot_be_approved_technically(self):
        order = self.w.order(draft=True)
        line = order.lines.get()
        self.client.force_login(self.w.planner)
        self.assertNotContains(self.client.get(reverse("orders:planning_queue")), line.code)
        with self.assertRaises(BusinessRuleError):
            orders.approve_line(self.w.planner, line, self.w.rev_a)
        orders.approve_order(self.w.sales, order)
        self.assertContains(self.client.get(reverse("orders:planning_queue")), line.code)

    def test_approve_view_and_scope(self):
        order = self.w.order(draft=True)
        outsider = User.objects.create_user("9421", PASSWORD, full_name="Başka Satış", role=Role.SALES)
        self.client.force_login(outsider)
        self.assertEqual(self.client.post(reverse("orders:order_approve", args=[order.pk]),
                                          {"idem_key": uuid.uuid4().hex}).status_code, 404)
        self.client.force_login(self.w.sales)
        self.client.post(reverse("orders:order_approve", args=[order.pk]), {"idem_key": uuid.uuid4().hex})
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.OPEN)

    def test_delete_draft_keeps_audit_and_approved_cannot_be_deleted(self):
        order = self.w.order(draft=True)
        code = order.code
        orders.delete_draft(self.w.sales, order, "Yanlış müşteriye açıldı")
        self.assertFalse(SalesOrder.objects.filter(code=code).exists())
        log = AuditLog.objects.get(action="order.delete_draft")
        self.assertEqual(log.object_code, code)
        self.assertIn("A-FILM", str(log.changes))
        with self.assertRaisesMessage(BusinessRuleError, "taslak"):
            orders.delete_draft(self.w.sales, self.w.order(), "x")

    def test_discrete_units_require_integer(self):
        with self.assertRaisesMessage(BusinessRuleError, "tam sayı"):
            orders.create_order(self.w.sales, customer=self.w.cust_a, order_date=timezone.localdate(),
                                lines=[{"product": self.w.prod_a, "quantity": D("10.5"), "unit": Unit.BOX}])


class ChangeCopyCancelTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_change_shows_impacts_and_never_changes_work_order(self):
        line = self.w.approved_line()
        wo = self.w.work_order(line=line)
        with self.assertRaisesMessage(BusinessRuleError, wo.code):
            orders.change_line(self.w.sales, line, quantity=D("40"), due_date=None, reason="Müşteri azalttı")
        with self.assertRaisesMessage(BusinessRuleError, "gerekçe"):
            orders.change_line(self.w.sales, line, quantity=D("40"), due_date=None, reason="", confirm_impacts=True)
        orders.change_line(self.w.sales, line, quantity=D("40"), due_date=None, reason="Müşteri azalttı",
                           confirm_impacts=True)
        wo.refresh_from_db()
        self.assertEqual(wo.target_quantity, D("50"))
        from production import lifecycle
        self.assertIn("40", lifecycle.quantity_conflict(wo))

    def test_copy_requires_review_and_new_technical_approval(self):
        src = self.w.order()
        orders.approve_line(self.w.planner, src.lines.get(), self.w.rev_a)
        new = orders.copy_order(self.w.sales, src)
        line = new.lines.get()
        self.assertEqual((new.status, new.copied_from_id, line.due_date, line.approved_revision_id, line.needs_review),
                         (OrderStatus.DRAFT, src.pk, None, None, True))
        with self.assertRaisesMessage(BusinessRuleError, "onaylanmalı"):
            orders.approve_order(self.w.sales, new)
        orders.change_line(self.w.sales, line, quantity=line.quantity, due_date=None, reason="")
        orders.approve_order(self.w.sales, new)
        line.refresh_from_db()
        self.assertEqual(line.status, LineStatus.WAITING_PLANNING)

    def test_cancel_line_blocked_by_open_work_order_then_order_cancel(self):
        line = self.w.approved_line()
        wo = self.w.work_order(line=line)
        with self.assertRaisesMessage(BusinessRuleError, wo.code):
            orders.cancel_line(self.w.sales, line, "Müşteri iptal etti")
        from production import lifecycle
        lifecycle.cancel(self.w.planner, wo, "Sipariş iptal")
        orders.cancel_order(self.w.sales, line.order, "Müşteri iptal etti")
        line.order.refresh_from_db()
        self.assertEqual(line.order.status, OrderStatus.CANCELLED)
        orders.archive_order(self.w.sales, line.order)
        self.client.force_login(self.w.sales)
        self.assertNotContains(self.client.get(reverse("orders:order_list")), line.order.code)
        self.assertContains(self.client.get(reverse("orders:order_list") + "?durum=arsiv"), line.order.code)

    def test_cancel_releases_reservations_and_full_shipment_completes_order(self):
        from warehouse import services as wh
        pallet = self.w.stock_pallet(boxes=40)
        order = self.w.order(qty=D("10"))
        line = order.lines.get()
        wh.reserve(self.w.depot, pallet, line, 10)
        other = self.w.order(qty=D("5"))
        res = wh.reserve(self.w.depot, pallet, other.lines.get(), 5)
        orders.cancel_order(self.w.sales, other, "Vazgeçildi")
        res.refresh_from_db()
        self.assertEqual(res.status, "iptal")
        shipment = wh.create_shipment(self.w.depot, order, "34 KRG 01", "")
        wh.add_shipment_line(self.w.depot, shipment, line.reservations.get(), 10)
        wh.confirm_shipment(self.w.store, load(self.w.depot, shipment))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.DONE)


class ShipmentCancelTests(TestCase):
    def test_cancelled_shipment_leaves_active_list(self):
        from warehouse import services as wh
        w = World()
        order = w.order(qty=D("10"))
        shipment = wh.create_shipment(w.depot, order, "34 KRG 02", "")
        self.client.force_login(w.depot)
        self.client.post(reverse("warehouse:shipment_cancel", args=[shipment.pk]),
                         {"idem_key": uuid.uuid4().hex, "reason": "Araç gelmedi"})
        shipment.refresh_from_db()
        self.assertEqual(shipment.status, "iptal")
        self.assertNotContains(self.client.get(reverse("warehouse:shipment_list")), shipment.code)
        self.assertContains(self.client.get(reverse("warehouse:shipment_list") + "?durum=tumu"), shipment.code)
