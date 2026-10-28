"""Sipariş girişinde müşteriye göre ürün filtresi: sunucu listesi, ürün uç noktası ve doğrudan POST koruması."""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog import services as cat
from catalog.models import Customer, Product, Unit
from core.tests.factories import World
from orders.forms import LineAddForm, OrderCreateForm
from orders.models import SalesOrder


class ProductFilterTests(TestCase):
    def setUp(self):
        self.w = World()
        a = self.w.admin
        self.prod_a2 = cat.create_definition(a, Product, {"code": "A-POSET", "customer": self.w.cust_a,
                                                          "name": "Poşet", "unit": Unit.BOX})
        self.inactive_b = cat.create_definition(a, Product, {"code": "B-ESKI", "customer": self.w.cust_b,
                                                             "name": "Eski", "unit": Unit.BOX})
        cat.set_active(a, self.inactive_b, False, "Üretimden kalktı")
        self.cust_c = cat.create_definition(a, Customer, {"code": "C-C", "name": "Kurgu C (ürünsüz)"})
        from catalog import customers as cs
        cs.grant_access(a, self.cust_c, self.w.sales, timezone.localdate(), None, "Test")
        self.url = reverse("orders:order_create")

    def products_url(self, customer):
        return reverse("orders:customer_products", args=[customer.pk])

    def post(self, key, **over):
        data = {"idem_key": key, "customer": self.w.cust_a.pk, "order_date": timezone.localdate().isoformat(),
                "product": self.w.prod_a.pk, "quantity": "10", "unit": "kutu", "purpose": "ticari", "delivery_confirmed": "on", **over}
        return self.client.post(self.url, data)

    # --- Uç nokta -----------------------------------------------------------------------
    def test_endpoint_requires_login(self):
        resp = self.client.get(self.products_url(self.w.cust_a))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/giris/", resp["Location"])

    def test_endpoint_requires_order_permission(self):
        for user in (self.w.depot, self.w.foreman, self.w.planner):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.products_url(self.w.cust_a)).status_code, 403, user.personnel_no)

    def test_endpoint_lists_only_customers_active_products(self):
        self.client.force_login(self.w.sales)
        resp = self.client.get(self.products_url(self.w.cust_a))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Cache-Control"], "no-store")
        ids = {p["id"] for p in resp.json()["products"]}
        self.assertEqual(ids, {self.w.prod_a.pk, self.prod_a2.pk})
        ids_b = {p["id"] for p in self.client.get(self.products_url(self.w.cust_b)).json()["products"]}
        self.assertEqual(ids_b, {self.w.prod_b.pk})  # pasif ürün yok, A'nın ürünleri yok

    def test_endpoint_empty_and_missing_customers(self):
        self.client.force_login(self.w.sales)
        self.assertEqual(self.client.get(self.products_url(self.cust_c)).json(), {"products": []})
        cat.set_active(self.w.admin, self.cust_c, False, "Çalışmıyoruz")
        self.assertEqual(self.client.get(self.products_url(self.cust_c)).status_code, 404)
        self.assertEqual(self.client.get(reverse("orders:customer_products", args=[999999])).status_code, 404)
        self.assertEqual(self.client.post(self.products_url(self.w.cust_a)).status_code, 405)

    # --- Form ---------------------------------------------------------------------------
    def test_product_locked_until_customer_selected(self):
        self.client.force_login(self.w.sales)
        resp = self.client.get(self.url)
        form = resp.context["form"]
        self.assertEqual(list(form.fields["product"].queryset), [])
        self.assertIn('disabled', str(form["product"]))
        self.assertContains(resp, "Önce müşteri seçin.")

    def test_bound_form_lists_only_selected_customers_products(self):
        form = OrderCreateForm(data={"customer": self.w.cust_b.pk}, user=self.w.sales)
        self.assertEqual(list(form.fields["product"].queryset), [self.w.prod_b])
        form_c = OrderCreateForm(data={"customer": self.cust_c.pk}, user=self.w.sales)
        self.assertEqual(form_c.fields["product"].help_text, "Bu müşteriye ait aktif ürün bulunmuyor.")

    def test_direct_post_of_other_customers_product_rejected(self):
        self.client.force_login(self.w.sales)
        resp = self.post("k-other", product=self.w.prod_b.pk)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["form"].errors.get("product"))
        resp = self.post("k-inactive", customer=self.w.cust_b.pk, product=self.inactive_b.pk)
        self.assertTrue(resp.context["form"].errors.get("product"))
        self.assertFalse(SalesOrder.objects.exists())

    def test_service_still_rejects_mismatch_without_form(self):
        from core.exceptions import BusinessRuleError
        from orders import services
        with self.assertRaises(BusinessRuleError):
            services.create_order(self.w.sales, customer=self.w.cust_a, order_date=timezone.localdate(),
                                  lines=[{"product": self.w.prod_b, "quantity": 1, "unit": Unit.BOX}])

    def test_validation_error_keeps_customer_and_product(self):
        self.client.force_login(self.w.sales)
        resp = self.post("k-invalid", product=self.prod_a2.pk, quantity="0")
        self.assertEqual(resp.status_code, 200)
        form = resp.context["form"]
        self.assertTrue(form.errors.get("quantity"))
        html = resp.content.decode()
        self.assertEqual(resp.context["step"], 2)  # hata miktar adımında gösterilir
        # Müşteri ve ürün kaybolmaz: gizli alanlarda taşınır, "Düzenle" ile seçili döner
        self.assertIn(f'name="customer" value="{self.w.cust_a.pk}"', html)
        self.assertIn(f'name="product" value="{self.prod_a2.pk}"', html)
        back = self.client.post(self.url, {**{k: v for k, v in resp.wsgi_request.POST.items()}, "git": "1"})
        html = back.content.decode()
        self.assertIn(f'<option value="{self.w.cust_a.pk}" selected>', html)
        self.assertIn(f'<option value="{self.prod_a2.pk}" selected>', html)
        form = back.context["form"]
        self.assertEqual(set(form.fields["product"].queryset), {self.w.prod_a, self.prod_a2})
        self.assertNotIn(self.w.prod_b.code, str(form["product"]))

    def test_valid_post_still_creates_single_order(self):
        self.client.force_login(self.w.sales)
        self.post("k-ok")
        self.post("k-ok")  # mükerrer gönderim
        self.assertEqual(SalesOrder.objects.count(), 1)

    # --- Kalem ekleme ----------------------------------------------------------------------
    def test_line_add_filtered_by_order_customer(self):
        order = self.w.order(product=self.w.prod_a)
        form = LineAddForm(order)
        self.assertEqual(set(form.fields["product"].queryset), {self.w.prod_a, self.prod_a2})
        self.client.force_login(self.w.sales)
        resp = self.client.post(reverse("orders:order_detail", args=[order.pk]),
                                {"idem_key": "k-line", "product": self.w.prod_b.pk, "quantity": "1", "unit": "kutu"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(order.lines.count(), 1)

    def test_line_add_empty_state(self):
        order = SalesOrder.objects.create(code="SIP-TEST-C", customer=self.cust_c, order_date=timezone.localdate(),
                                          created_by=self.w.sales)
        self.client.force_login(self.w.sales)
        resp = self.client.get(reverse("orders:order_detail", args=[order.pk]))
        self.assertContains(resp, "Bu müşteriye ait aktif ürün bulunmuyor.")
