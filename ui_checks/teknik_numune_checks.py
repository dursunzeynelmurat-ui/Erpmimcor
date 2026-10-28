"""Teknik sipariş / reçete / baskı / repete / numune — gerçek LiveServer + PostgreSQL test DB + Chromium.
1440 px masaüstü ve 390 px mobil **emülasyonu**; fiziksel telefon testi ve gerçek kullanıcı gözlemi değildir.

1. Satış körüklü, baskılı ürün için 3 adımlı sipariş açar; teknik özellikler ve onaylı görsel otomatik gelir.
2. Planlama teknik onay ekranında aynı özellikleri ve geçerli görseli görür.
3. Planlama reçete taslağında %97 ile onay alamaz (açık mesaj), %100'de onaylar; satır eklenebilir.
4. Satış revizyonlu repete açar; planlama eski/yeni değerleri yan yana görür.
5. Depo numune kaydeder, stoktan tam kutu ayırır (tek hareket); QR etiketi açılır; satış yalnız paylaşılabilir görünümü görür.
"""
import os
from pathlib import Path
from unittest.mock import patch

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.db import connections
from django.test import override_settings
from playwright.sync_api import expect, sync_playwright

from catalog.models import Customer, Product
from orders.models import LineChangeRequest, SalesOrder
from samples.models import Sample
from ui_checks import browser_checks as demo
from ui_checks import support
from warehouse.models import MovementKind, Pallet, PalletMovement


@override_settings(DEBUG=True, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
class TechnicalSampleRepeatBrowserTests(StaticLiveServerTestCase):
    password = demo.DemoBrowserTests.password
    page_for = demo.DemoBrowserTests.page_for
    go = demo.DemoBrowserTests.go

    def setUp(self):
        with patch.dict(os.environ, {"DEMO_USER_PASSWORD": self.password}):
            call_command("seed_demo", verbosity=0)
        guard = patch.dict(os.environ, {"DJANGO_ALLOW_ASYNC_UNSAFE": "true"})  # yalnız test süreci
        guard.start()
        self.addCleanup(guard.stop)
        self.pw = sync_playwright().start()
        self.browser = support.launch(self.pw)
        self.contexts = {}
        self.screens = Path(os.getenv("TNR_SCREENSHOT_DIR", "docs/screenshots/teknik-numune-repete"))
        self.screens.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        support.finish_contexts(self, [p.context for p in self.contexts.values()])
        self.browser.close()
        connections.close_all()
        self.pw.stop()

    def shot(self, page, name):
        page.evaluate("window.scrollTo(0, 0)")
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), self.width, name)
        ids = page.locator("[id]").evaluate_all("(nodes) => nodes.map(n => n.id)")
        self.assertEqual(len(ids), len(set(ids)), name + ": yinelenen HTML kimliği")
        keys = page.locator("input[name=idem_key]").evaluate_all("(nodes) => nodes.map(n => n.value)")
        self.assertEqual(len(keys), len(set(keys)), name + ": yinelenen işlem anahtarı")
        page.screenshot(path=str(self.screens / f"{name}-{self.width}.png"), full_page=True)

    def click(self, page, name):
        page.get_by_role("button", name=name, exact=True).click()
        page.wait_for_load_state("networkidle")

    def run_flow(self, width):
        self.width = width
        product = Product.objects.get(code="ORN-KB-045")
        customer = Customer.objects.get(code="M-ORNEK")

        # 1) Satış: 3 adımlı sipariş, teknik özellikler otomatik
        sales = self.page_for("2001")
        self.go(sales, "/siparisler/yeni/")
        sales.locator("[name=customer]").select_option(str(customer.pk))
        sales.locator("[name=product]").select_option(str(product.pk))
        self.shot(sales, "tnr-1-siparis-adim1")
        self.click(sales, "İleri: özellikler ve miktar →")
        card = sales.locator(".spec-card")
        for text in ("Yan körük", "Kapalı en", "KL-ORN-01", "550 mm", "Onaylı sürüm 1", "Belirtilmemiş"):
            expect(card).to_contain_text(text)
        expect(sales.locator("body")).not_to_contain_text("HM-LDPE")  # karışım satışa sızmaz
        sales.locator("[name=quantity]").fill("12,5")
        sales.locator("[name=purpose]").select_option("numune")
        sales.locator("[name=charge]").select_option("ucretsiz")
        self.click(sales, "İleri: teslimat ve kontrol →")
        expect(sales.locator(".form-errors")).to_be_visible()  # kutuda ondalık: alan yanında Türkçe hata
        expect(sales.locator("[name=quantity]")).to_have_value("12,5")
        self.shot(sales, "tnr-1-siparis-hata")
        sales.locator("[name=quantity]").fill("10")
        self.click(sales, "İleri: teslimat ve kontrol →")
        self.shot(sales, "tnr-1-siparis-adim3")
        sales.locator("[name=delivery_confirmed]").check()
        self.click(sales, "Siparişi kaydet")
        expect(sales.locator(".msg-success")).to_contain_text("Sıradaki adım")
        order = SalesOrder.objects.filter(customer=customer, purpose="numune").latest("pk")
        self.click(sales, "Siparişi onayla")
        line = order.lines.get()

        # 2) Planlama: teknik onay ekranında özellikler ve geçerli görsel
        planner = self.page_for("3001")
        self.go(planner, f"/planlama/kalem/{line.pk}/onay/")
        expect(planner.locator(".spec-card").first).to_contain_text("Yan körük")
        expect(planner.locator(".spec-card").first).to_contain_text("Onaylı sürüm 1")
        self.shot(planner, "tnr-2-planlama-onay")
        self.go(planner, f"/urun/{product.pk}/baski-gorselleri/")
        expect(planner.locator(".artwork").first).to_contain_text("Geçerli")
        self.shot(planner, "tnr-2-baski-gorselleri")

        # 3) Planlama: reçete taslağı %97 → onay yok, %100 → onay
        self.go(planner, f"/urun/{product.pk}/recete/yeni-revizyon/")
        self.click(planner, "Taslak aç")
        rows_before = planner.locator("[data-row]").count()
        planner.get_by_role("button", name="+ Malzeme ekle").click()
        self.assertEqual(planner.locator("[data-row]").count(), rows_before + 1)
        first_pct = planner.locator("input[name=pct_0]")
        first_pct.fill("57")
        expect(planner.locator("[data-total]")).to_contain_text("Karışım toplamı %97. Onaylamak için %100 olmalı.")
        planner.locator("[name=change_reason]").fill("Kurgusal deneme")
        self.click(planner, "Onayla ve revizyon oluştur")
        expect(planner.locator(".msg-error")).to_contain_text("Onaylamak için %100 olmalı")
        self.shot(planner, "tnr-3-recete-eksik")
        planner.locator("input[name=pct_0]").fill("60")
        self.click(planner, "Onayla ve revizyon oluştur")
        expect(planner.locator(".msg-success")).to_contain_text("Reçete onaylandı")
        self.shot(planner, "tnr-3-recete-onaylandi")

        # 4) Satış: revizyonlu repete; planlama yan yana görür
        self.go(sales, f"/siparisler/tekrar/?musteri={customer.pk}&kaynak={order.pk}")
        sel = sales.locator(f"[name=sel_{line.pk}]")
        expect(sel).to_be_checked()
        sales.locator(f"[name=qty_{line.pk}]").fill("5")
        sales.locator("details", has_text="Teknik değişiklik iste").first.locator("summary").click()
        sales.locator(f"[name=chg_{line.pk}]").check()
        sales.locator(f"[name=reason_{line.pk}]").fill("Müşteri 420 mm en istedi (kurgusal)")
        sales.locator(f"[name=s{line.pk}_width_mm]").fill("420")
        self.shot(sales, "tnr-4-repete-kalemler")
        self.click(sales, "İleri: teslimat ve kontrol →")
        expect(sales.locator("body")).to_contain_text("420 mm")
        sales.locator("[name=purpose]").select_option("ticari")
        sales.locator("[name=delivery_confirmed]").check()
        self.shot(sales, "tnr-4-repete-kontrol")
        self.click(sales, "Tekrar siparişi kaydet")
        expect(sales.locator(".msg-success")).to_contain_text("Revizyonlu repete")
        cr = LineChangeRequest.objects.latest("pk")
        self.go(planner, f"/planlama/degisiklik/{cr.pk}/")
        expect(planner.locator("table")).to_contain_text("400 mm")
        expect(planner.locator("table")).to_contain_text("420 mm")
        self.shot(planner, "tnr-4-planlama-yan-yana")

        # 5) Depo: numune, stoktan tam kutu ayırma (tek hareket), QR etiketi; satış kısıtlı görünüm
        depot = self.page_for("5001")
        self.go(depot, "/numuneler/yeni/")
        deneme = Customer.objects.get(code="M-DENEME")
        den = Product.objects.get(code="DEN-PF-030")
        depot.locator("[name=kind]").select_option("sahit")
        depot.locator("[name=customer]").select_option(str(deneme.pk))
        depot.locator("[name=product]").select_option(str(den.pk))
        depot.locator("[name=quantity]").fill("2")
        depot.locator("[name=unit]").select_option("kutu")
        depot.locator("[name=room]").fill("Numune odası")
        self.click(depot, "Numuneyi kaydet")
        expect(depot.locator(".msg-success")).to_contain_text("Numune kaydedildi")
        sample = Sample.objects.latest("pk")
        pallet = Pallet.objects.get(code="PLT-2026-00001")
        before = pallet.box_count
        depot.locator("summary", has_text="Stoktan ayır").click()
        depot.locator("#id_stock_pallet_code").fill(pallet.code)
        self.click(depot, "Kutuları stoktan ayır")
        expect(depot.locator(".msg-success")).to_contain_text("tek stok hareketi")
        pallet.refresh_from_db()
        self.assertEqual(pallet.box_count, before - 2)
        self.assertEqual(PalletMovement.objects.filter(kind=MovementKind.SAMPLE_OUT, pallet=pallet).count(), 1)
        self.shot(depot, "tnr-5-numune-ayrinti")
        self.go(depot, f"/numuneler/{sample.pk}/etiket/")
        expect(depot.locator(".label-qr svg")).to_be_visible()
        self.shot(depot, "tnr-5-numune-etiket")
        self.go(sales, f"/numuneler/{sample.pk}/")
        expect(sales.locator("body")).not_to_contain_text("Hareket geçmişi")
        expect(sales.locator("body")).not_to_contain_text("Kontrol ve kalite kararı")
        self.shot(sales, "tnr-5-numune-satis")

    def test_desktop(self):
        self.run_flow(1440)

    def test_mobile_emulation(self):
        self.run_flow(390)
