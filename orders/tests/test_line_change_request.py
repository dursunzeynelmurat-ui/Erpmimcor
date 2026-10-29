"""Demo son düzeltme 7: normal yeni siparişte ve taslak/planlama bekleyen kalemde satışın teknik talebi.
Mevcut ürün/reçete değişmez; istek mevcut değişiklik isteği modeliyle planlamaya gider; karar verilene kadar teknik
onay ve iş emri engellidir; reçetesi olmayan yeni üründe müşteri talebi kaybolmaz ve üretime onaylı sayılmaz."""
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog import recipe_drafts as rd
from catalog import services as cat
from catalog.models import Product
from core.exceptions import BusinessRuleError
from core.tests.factories import World
from orders import repeat
from orders import services as orders
from orders.models import ChangeStatus, OrderLine, SalesOrder


class NewOrderChangeRequestTests(TestCase):
    def setUp(self):
        self.w = World()
        self.client.force_login(self.w.sales)

    def wizard(self, product, idem, **spec):
        data = {"adim": "3", "git": "kaydet", "idem_key": idem, "customer": self.w.cust_a.pk, "product": product.pk,
                "quantity": "10", "unit": "kutu", "purpose": "ticari", "order_date": timezone.localdate().isoformat(),
                "delivery_confirmed": "on"}
        if spec:
            data.update({"want_change": "on", "change_reason": "Müşteri farklı ölçü istedi (kurgusal)"})
            data.update({f"s_{k}": v for k, v in spec.items()})
        return self.client.post(reverse("orders:order_create"), data)

    def test_wizard_request_goes_to_planning_without_changing_product(self):
        resp = self.wizard(self.w.prod_a, "w-1", width_mm="650")
        self.assertEqual(resp.status_code, 302, resp.content.decode()[:300])
        order = SalesOrder.objects.get()
        line = order.lines.get()
        cr = line.change_requests.get()
        self.assertEqual((cr.status, cr.base_revision, cr.requested), (ChangeStatus.PENDING, self.w.rev_a,
                                                                       {"width_mm": "650"}))
        self.w.prod_a.refresh_from_db()
        self.assertEqual(self.w.prod_a.current_revision, self.w.rev_a)  # ürün/reçete değişmedi
        orders.approve_order(self.w.sales, order)
        with self.assertRaisesMessage(BusinessRuleError, "karara bağlanmadan"):
            orders.approve_line(self.w.planner, line, self.w.rev_a)
        rows = {r["label"]: (r["old"], r["new"]) for r in repeat.comparison(cr)}
        self.assertEqual(rows["En"], ("600 mm", "650 mm"))
        rev = rd.approve_draft(self.w.planner, repeat.start_new_revision(self.w.planner, cr))
        line = OrderLine.objects.get(pk=line.pk)
        self.assertEqual(repeat.expected_revision(line), rev)
        self.assertEqual(orders.approve_line(self.w.planner, line, rev).approved_revision.width_mm, D("650"))

    def test_wizard_without_reason_or_difference_is_rejected(self):
        resp = self.client.post(reverse("orders:order_create"), {
            "adim": "2", "git": "ileri", "idem_key": "w-2", "customer": self.w.cust_a.pk,
            "product": self.w.prod_a.pk, "quantity": "10", "unit": "kutu", "purpose": "ticari",
            "order_date": timezone.localdate().isoformat(), "want_change": "on", "s_width_mm": "600"})
        self.assertContains(resp, "gerekçe")
        self.assertContains(resp, "farklı")
        self.assertFalse(SalesOrder.objects.exists())

    def test_product_without_recipe_keeps_customer_request(self):
        new = cat.create_definition(self.w.admin, Product, {"code": "A-YENI", "customer": self.w.cust_a,
                                                            "name": "Yeni poşet (kurgusal)"})
        resp = self.wizard(new, "w-3", width_mm="500", thickness_micron="40", color="Beyaz")
        self.assertEqual(resp.status_code, 302)
        line = SalesOrder.objects.get().lines.get()
        cr = line.change_requests.get()
        self.assertIsNone(cr.base_revision)
        self.assertEqual(cr.requested, {"width_mm": "500", "thickness_micron": "40", "color": "Beyaz"})
        self.assertIsNone(line.approved_revision)
        draft = repeat.start_new_revision(self.w.planner, cr)
        self.assertIsNone(draft.based_on)
        self.assertEqual(rd.spec_from_json(draft.spec_values)["width_mm"], D("500"))

    def test_step2_help_text_and_no_mix_for_sales(self):
        resp = self.client.post(reverse("orders:order_create"), {
            "adim": "1", "git": "ileri", "customer": self.w.cust_a.pk, "product": self.w.prod_a.pk})
        html = resp.content.decode()
        self.assertFalse("kaydettikten sonra planlamaya değişiklik isteği iletin" in html)
        self.assertIn("Farklı teknik özellik istiyorum", html)
        self.assertFalse("LDPE" in html or "karisim" in html)


class LineChangeRequestTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_draft_line_request_and_lifecycle_guards(self):
        order = self.w.order(qty=D("10"), draft=True)
        line = order.lines.get()
        self.client.force_login(self.w.sales)
        url = reverse("orders:line_change_request", args=[line.pk])
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("LDPE", page.content.decode())
        self.client.post(url, {"idem_key": "k-1", "change_reason": "Müşteri 35 µ istedi (kurgusal)",
                               "s_thickness_micron": "35"})
        cr = line.change_requests.get()
        self.assertEqual(cr.requested, {"thickness_micron": "35"})
        with self.assertRaisesMessage(BusinessRuleError, "açık bir teknik değişiklik isteği"):
            repeat.request_line_change(self.w.sales, line, {"color": "Mavi"}, "İkinci istek")
        with self.assertRaisesMessage(BusinessRuleError, "değiştirilemez"):
            repeat.request_line_change(self.w.sales, line, {"karisim": "LDPE 100"}, "x")

    def test_cancelled_or_approved_line_rejected(self):
        order = self.w.order(qty=D("10"))
        line = orders.approve_line(self.w.planner, order.lines.get(), self.w.rev_a)
        with self.assertRaisesMessage(BusinessRuleError, "planlama onayı bekleyen"):
            repeat.request_line_change(self.w.sales, line, {"width_mm": D("700")}, "Geç talep")
        other = self.w.order(qty=D("5"))
        cline = orders.cancel_line(self.w.sales, other.lines.get(), "Müşteri vazgeçti")
        with self.assertRaisesMessage(BusinessRuleError, "planlama onayı bekleyen"):
            repeat.request_line_change(self.w.sales, cline, {"width_mm": D("700")}, "x")

    def test_other_customer_sales_cannot_request(self):
        from accounts.models import Role, User
        from core.tests.factories import PASSWORD
        outsider = User.objects.create_user("9961", PASSWORD, full_name="Başka satış", role=Role.SALES)
        line = self.w.order(qty=D("10"), draft=True).lines.get()
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(reverse("orders:line_change_request", args=[line.pk])).status_code, 404)

    def test_work_order_blocked_while_request_open(self):
        from orders.models import LineChangeRequest
        line = self.w.approved_line()
        # Eski veri / yarış: onaylı kalemde açık istek kalmışsa iş emri açılmaz.
        LineChangeRequest.objects.create(line=line, base_revision=self.w.rev_a, requested={"width_mm": "700"},
                                         reason="Kurgusal eski istek", requested_by=self.w.sales)
        with self.assertRaisesMessage(BusinessRuleError, "teknik talebi karara bağlanmadı"):
            self.w.work_order(line=line, machines=[self.w.free_extruder()])
