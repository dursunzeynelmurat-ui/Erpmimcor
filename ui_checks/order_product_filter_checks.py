"""Sipariş formunda müşteriye göre ürün filtresi — gerçek LiveServer + PostgreSQL test DB + Chromium.
Çalıştırma: manage.py test ui_checks.order_product_filter_checks. Ekran emülasyonudur; fiziksel telefon testi değildir.
Yalnız kurgusal seed_demo verisi ve testte eklenen kurgusal ürünsüz müşteri kullanılır.
"""
import os
import re
from unittest.mock import patch

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.db import connections
from django.test import override_settings
from playwright.sync_api import expect, sync_playwright

from catalog.models import Customer, Product
from orders.models import SalesOrder

NO_CUSTOMER = "Önce müşteri seçin."
EMPTY = "Bu müşteriye ait aktif ürün bulunmuyor."
ERROR = "Ürün listesi yüklenemedi"


@override_settings(DEBUG=True, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
class OrderProductFilterBrowserTests(StaticLiveServerTestCase):
    password = "Browser-Demo-2026!"

    def setUp(self):
        with patch.dict(os.environ, {"DEMO_USER_PASSWORD": self.password}):
            call_command("seed_demo", verbosity=0)
        self.empty_customer = Customer.objects.create(code="M-BOS", name="Ürünsüz Müşteri (kurgusal)")
        from accounts.models import User
        from catalog.models import CustomerAccess
        from django.utils import timezone
        CustomerAccess.objects.create(customer=self.empty_customer, user=User.objects.get(personnel_no="2001"),
                                      valid_from=timezone.localdate(), reason="Test")
        guard = patch.dict(os.environ, {"DJANGO_ALLOW_ASYNC_UNSAFE": "true"})  # yalnız test süreci
        guard.start()
        self.addCleanup(guard.stop)
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH", "/usr/bin/chromium"),
                                               headless=True, args=["--no-sandbox"])

    def tearDown(self):
        self.browser.close()
        connections.close_all()
        self.pw.stop()

    def ids(self, code):
        customer = Customer.objects.get(code=code)
        product = Product.objects.filter(customer=customer).first()
        return str(customer.pk), (str(product.pk) if product else None)

    def products_url(self, customer_id):
        return re.compile(rf".*/siparisler/musteri/{customer_id}/urunler/$")

    def option_values(self, page):
        return page.locator("[name=product] option").evaluate_all("(os) => os.map(o => o.value).filter(Boolean)")

    def run_flow(self, width):
        context = self.browser.new_context(viewport={"width": width, "height": 900 if width > 600 else 844})
        page = context.new_page()
        page.set_default_timeout(10000)
        page.goto(self.live_server_url + "/giris/")
        page.locator("[name=personnel_no]").fill("2001")
        page.locator("[name=password]").fill(self.password)
        page.get_by_role("button", name="Giriş yap").click()
        expect(page.locator("h1")).to_contain_text("Merhaba")
        page.goto(self.live_server_url + "/siparisler/yeni/")

        product, customer = page.locator("[name=product]"), page.locator("[name=customer]")
        help_text = page.locator("#id_product_helptext")
        retry = page.get_by_role("button", name="Tekrar dene")
        a_cust, a_prod = self.ids("M-ORNEK")
        b_cust, b_prod = self.ids("M-DENEME")

        # 1) Müşteri seçilmeden ürün kilitli
        expect(product).to_be_disabled()
        expect(help_text).to_have_text(NO_CUSTOMER)
        self.assertEqual(self.option_values(page), [])

        # 2) A → yalnız A'nın aktif ürünleri
        customer.select_option(a_cust)
        expect(product).to_be_enabled()
        self.assertEqual(self.option_values(page), [a_prod])
        product.select_option(a_prod)

        # 3) B'ye geçiş: önceki seçim temizlenir, yalnız B'nin ürünleri
        customer.select_option(b_cust)
        expect(product).to_be_enabled()
        self.assertEqual(self.option_values(page), [b_prod])
        self.assertEqual(product.input_value(), "")

        # 4) Ürünsüz müşteri
        customer.select_option(str(self.empty_customer.pk))
        expect(help_text).to_have_text(EMPTY)
        expect(product).to_be_disabled()
        self.assertEqual(self.option_values(page), [])

        # 5) Yükleme hatası → eski/tüm ürün yok, hata + Tekrar dene → başarılı
        page.route(self.products_url(a_cust), lambda route: route.fulfill(status=500, body="hata"))
        customer.select_option(a_cust)
        expect(help_text).to_contain_text(ERROR)
        expect(retry).to_be_visible()
        expect(product).to_be_disabled()
        self.assertEqual(self.option_values(page), [])
        page.unroute(self.products_url(a_cust))
        retry.click()
        expect(product).to_be_enabled()
        expect(retry).to_be_hidden()
        self.assertEqual(self.option_values(page), [a_prod])

        # 6) Hızlı değişim: A'nın yanıtı bekletilir, B seçilir; A'nın geç yanıtı B listesini ezmez
        held = []
        page.route(self.products_url(a_cust), lambda route: held.append(route))
        customer.select_option(a_cust)
        expect(product).to_be_disabled()
        customer.select_option(b_cust)
        expect(product).to_be_enabled()
        page.wait_for_function("() => true")
        for route in held:
            try:
                route.continue_()
            except Exception:  # noqa: BLE001 - istek tarayıcıda iptal edilmiş olabilir
                pass
        page.unroute(self.products_url(a_cust))
        page.wait_for_load_state("networkidle")
        self.assertEqual(self.option_values(page), [b_prod])
        self.assertEqual(customer.input_value(), b_cust)

        # 7) Doğrulama hatasında müşteri ve ürün seçimi korunur
        product.select_option(b_prod)
        page.locator("[name=quantity]").fill("0")
        page.get_by_role("button", name="Siparişi kaydet").click()
        page.wait_for_load_state("networkidle")
        expect(page.locator(".msg-error")).to_contain_text("Kaydedilmedi")
        self.assertEqual(customer.input_value(), b_cust)
        self.assertEqual(product.input_value(), b_prod)
        expect(product).to_be_enabled()
        self.assertEqual(self.option_values(page), [b_prod])
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)

        # 8) Geçerli kayıt
        before = SalesOrder.objects.count()
        page.locator("[name=quantity]").fill("5")
        page.get_by_role("button", name="Siparişi kaydet").click()
        expect(page.locator(".msg-success")).to_contain_text("Sipariş oluşturuldu")
        self.assertEqual(SalesOrder.objects.count(), before + 1)
        self.assertEqual(SalesOrder.objects.latest("created_at").lines.get().product_id, int(b_prod))
        context.close()

    def test_filter_desktop(self):
        self.run_flow(1440)

    def test_filter_mobile_emulation(self):
        self.run_flow(390)
