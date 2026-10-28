from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Unit
from core.exceptions import BusinessRuleError
from core.models import IdempotencyKey
from core.tests.factories import World
from orders import services as orders
from orders.models import LineStatus, SalesOrder


class OrderTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_order_code_assigned_on_first_save_and_unique(self):
        o1 = self.w.order()
        o2 = self.w.order()
        self.assertTrue(o1.code.startswith("SIP-"))
        self.assertNotEqual(o1.code, o2.code)
        self.assertNotEqual(o1.lines.get().pk, o2.lines.get().pk)

    def test_product_of_other_customer_rejected(self):
        with self.assertRaises(BusinessRuleError):
            orders.create_order(self.w.sales, customer=self.w.cust_a, order_date=timezone.localdate(),
                                lines=[{"product": self.w.prod_b, "quantity": D("5"), "unit": Unit.BOX}])
        self.assertFalse(SalesOrder.objects.exists())  # tüm işlem geri alındı

    def test_planning_approval_pins_revision(self):
        line = self.w.approved_line()
        self.assertEqual(line.status, LineStatus.APPROVED)
        self.assertEqual(line.approved_revision, self.w.rev_a)

    def test_revision_of_other_product_rejected(self):
        line = self.w.order().lines.get()
        with self.assertRaises(BusinessRuleError):
            orders.approve_line(self.w.planner, line, self.w.rev_b)


class OrderViewTests(TestCase):
    def setUp(self):
        self.w = World()
        self.client.force_login(self.w.sales)

    def post_order(self, key):
        return self.client.post(reverse("orders:order_create"), {
            "idem_key": key, "customer": self.w.cust_a.pk, "order_date": timezone.localdate().isoformat(),
            "product": self.w.prod_a.pk, "quantity": "40", "unit": "kutu", "purpose": "ticari", "delivery_confirmed": "on"})

    def test_double_submit_creates_single_order(self):
        r1 = self.post_order("same-key")
        r2 = self.post_order("same-key")
        self.assertEqual(SalesOrder.objects.count(), 1)
        self.assertEqual(r1["Location"], r2["Location"])
        self.assertEqual(IdempotencyKey.objects.count(), 1)

    def test_failed_submit_does_not_claim_success(self):
        resp = self.client.post(reverse("orders:order_create"), {
            "idem_key": "k-fail", "customer": self.w.cust_a.pk, "order_date": timezone.localdate().isoformat(),
            "product": self.w.prod_b.pk, "quantity": "40", "unit": "kutu", "purpose": "ticari", "delivery_confirmed": "on"}, follow=True)
        self.assertContains(resp, "Kaydedilmedi")
        self.assertFalse(SalesOrder.objects.exists())
        self.assertFalse(IdempotencyKey.objects.exists())  # anahtar da geri alındı; düzeltip tekrar gönderilebilir
