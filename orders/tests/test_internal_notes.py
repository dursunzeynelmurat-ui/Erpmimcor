"""Demo son düzeltme 1: reçetenin iç proses notu, karışım ve uygulama notları satış yanıtlarına hiç girmez;
planlama/yönetici görmeye devam eder (CSS ile gizleme değil, sunucu üretmez)."""
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from catalog import services as cat
from core.tests.factories import World
from orders import services as orders

SECRET = "GIZLI-PROSES-7Q4 kazan 212C"
NOTE = "UYGNOT-KATKI-9X2"


class InternalNotesTests(TestCase):
    def setUp(self):
        self.w = World()
        self.rev = cat.create_revision(self.w.planner, self.w.prod_a, self.w.spec(process_notes=SECRET),
                                       self.w.mix(), reason="İç not denemesi", notes={self.w.slip.pk: NOTE})
        self.order = self.w.order(qty=D("10"))
        self.line = self.order.lines.get()

    def get(self, user, url, **params):
        self.client.force_login(user)
        resp = self.client.get(url, params)
        self.assertEqual(resp.status_code, 200, url)
        return resp.content.decode()

    def assert_hidden(self, html, where):
        for text in (SECRET, NOTE, "Proses notu"):
            self.assertFalse(text in html, f"{where}: {text!r} satış yanıtında")

    def test_sales_order_detail_before_and_after_approval(self):
        url = reverse("orders:order_detail", args=[self.order.pk])
        html = self.get(self.w.sales, url)
        self.assertIn("Teknik özellikleri göster", html)
        self.assert_hidden(html, "sipariş ayrıntısı (onaysız)")
        orders.approve_line(self.w.planner, self.line, self.rev)
        self.assert_hidden(self.get(self.w.sales, url), "sipariş ayrıntısı (onaylı)")

    def test_sales_new_order_spec_step(self):
        self.client.force_login(self.w.sales)
        resp = self.client.post(reverse("orders:order_create"), {
            "adim": "1", "git": "ileri", "customer": self.w.cust_a.pk, "product": self.w.prod_a.pk})
        html = resp.content.decode()
        self.assertIn("spec-card", html)
        self.assert_hidden(html, "yeni sipariş 2. adım")

    def test_sales_repeat_wizard(self):
        orders.approve_line(self.w.planner, self.line, self.rev)
        html = self.get(self.w.sales, reverse("orders:repeat_order"),
                        musteri=self.w.cust_a.pk, kaynak=self.order.pk)
        self.assert_hidden(html, "repete sihirbazı")

    def test_planning_still_sees_internal_note(self):
        html = self.get(self.w.planner, reverse("orders:line_approve", args=[self.line.pk]))
        self.assertIn(SECRET, html)
        html = self.get(self.w.planner, reverse("catalog:recipe_detail", args=[self.w.prod_a.pk]))
        self.assertIn(SECRET, html)
        self.assertIn(NOTE, html)
        html = self.get(self.w.planner, reverse("orders:order_detail", args=[self.order.pk]))
        self.assertIn(SECRET, html)
