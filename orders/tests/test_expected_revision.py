"""Demo son düzeltme 2: repete kaleminin teknik onayında beklenen revizyon.
Normal repetede kaynak kalemin (repete anındaki) onaylı revizyonu, revizyonlu repetede karara bağlanan isteğin sonuç
revizyonu beklenir. Varsayılan seçim ve kart bu revizyondur; başka revizyon yalnız gerekçeyle ve açık doğrulamayla
seçilebilir (sunucu kontrolü; eski form ve doğrudan POST dahil)."""
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from catalog import recipe_drafts as rd
from catalog import services as cat
from core.exceptions import BusinessRuleError
from core.tests.factories import World
from orders import repeat
from orders import services as orders
from orders.models import OrderLine

from .test_repeat import shipped_source


class ExpectedRevisionTests(TestCase):
    def setUp(self):
        self.w = World()
        self.src_order, self.src = shipped_source(self.w)
        self.rev600 = self.src.approved_revision

    def new_rev(self, width):
        return cat.create_revision(self.w.planner, self.w.prod_a, self.w.spec(width_mm=D(width)), self.w.mix(),
                                   reason=f"Başka iş için {width} mm")

    def revised_repeat_650(self):
        order = repeat.create_repeat_order(
            self.w.sales, customer=self.w.cust_a, purpose="ticari", delivery_confirmed=True,
            items=[{"source_line": self.src, "quantity": D("5"), "changes": {"width_mm": D("650")},
                    "reason": "Müşteri 650 mm istedi (kurgusal)"}])
        line = order.lines.get()
        rev650 = rd.approve_draft(self.w.planner, repeat.start_new_revision(self.w.planner, line.change_requests.get()))
        rev800 = self.new_rev("800")  # aynı ürüne sonradan başka iş için
        orders.approve_order(self.w.sales, order)
        return OrderLine.objects.get(pk=line.pk), rev650, rev800

    def form_page(self, line):
        self.client.force_login(self.w.planner)
        resp = self.client.get(reverse("orders:line_approve", args=[line.pk]))
        self.assertEqual(resp.status_code, 200)
        return resp

    def post(self, line, revision, **extra):
        self.client.force_login(self.w.planner)
        return self.client.post(reverse("orders:line_approve", args=[line.pk]),
                                {"idem_key": f"k-{revision.pk}-{len(extra)}", "revision": revision.pk, "note": "",
                                 **extra})

    def test_revised_repeat_defaults_to_decided_revision(self):
        line, rev650, rev800 = self.revised_repeat_650()
        self.assertEqual(self.w.prod_a.current_revision.pk, rev800.pk)
        resp = self.form_page(line)
        self.assertEqual(resp.context["form"]["revision"].value(), rev650.pk)
        self.assertEqual(resp.context["revision"].pk, rev650.pk)  # gösterilen kart = varsayılan seçim
        self.assertContains(resp, "650 mm")

    def test_other_revision_needs_reason_server_side(self):
        line, rev650, rev800 = self.revised_repeat_650()
        with self.assertRaisesMessage(BusinessRuleError, f"Rev.{rev650.rev_no}"):
            orders.approve_line(self.w.planner, line, rev800)
        self.post(line, rev800)  # eski açık form / doğrudan POST
        line.refresh_from_db()
        self.assertIsNone(line.approved_revision)
        self.post(line, rev800, deviation_confirmed="on", deviation_reason="Müşteri 800 mm'yi yazılı kabul etti")
        line.refresh_from_db()
        self.assertEqual(line.approved_revision, rev800)
        from core.models import AuditLog
        log = AuditLog.objects.filter(action="order.line_approve", object_id=str(line.pk)).latest("pk")
        self.assertIn("yazılı kabul", log.reason)

    def test_expected_revision_approves_without_reason(self):
        line, rev650, _ = self.revised_repeat_650()
        self.post(line, rev650)
        line.refresh_from_db()
        self.assertEqual(line.approved_revision, rev650)

    def test_plain_repeat_keeps_source_snapshot(self):
        base = self.w.order(qty=D("10"))
        base_line = orders.approve_line(self.w.planner, base.lines.get(), self.rev600)
        order = repeat.create_repeat_order(
            self.w.sales, customer=self.w.cust_a, purpose="ticari", delivery_confirmed=True,
            items=[{"source_line": base_line, "quantity": D("8")}])
        rev800 = self.new_rev("800")
        orders.approve_line(self.w.planner, base_line, rev800)  # kaynak sipariş sonradan değiştirildi
        orders.approve_order(self.w.sales, order)
        line = order.lines.get()
        self.assertEqual(repeat.expected_revision(line), self.rev600)
        self.assertEqual(self.form_page(line).context["form"]["revision"].value(), self.rev600.pk)
        with self.assertRaisesMessage(BusinessRuleError, "gerekçe"):
            orders.approve_line(self.w.planner, line, rev800)
        self.src.refresh_from_db()
        self.assertEqual(self.src.approved_revision, self.rev600)  # sevk edilmiş eski sipariş değişmez

    def test_rejected_request_expects_base_revision(self):
        order = repeat.create_repeat_order(
            self.w.sales, customer=self.w.cust_a, purpose="ticari", delivery_confirmed=True,
            items=[{"source_line": self.src, "quantity": D("5"), "changes": {"width_mm": D("650")},
                    "reason": "Müşteri 650 mm istedi (kurgusal)"}])
        line = order.lines.get()
        repeat.reject(self.w.planner, line.change_requests.get(), "Makine 650 mm çalışmıyor (kurgusal)")
        rev800 = self.new_rev("800")
        orders.approve_order(self.w.sales, order)
        line = OrderLine.objects.get(pk=line.pk)
        self.assertEqual(repeat.expected_revision(line), self.rev600)
        with self.assertRaisesMessage(BusinessRuleError, "reddedildi"):
            orders.approve_line(self.w.planner, line, rev800)
        self.assertEqual(orders.approve_line(self.w.planner, line, self.rev600).approved_revision, self.rev600)
