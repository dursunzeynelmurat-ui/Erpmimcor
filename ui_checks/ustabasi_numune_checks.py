"""Ustabaşı numune kapsamı: kendisine atanmış iş emri için arayüzden numune (kurgusal seed_demo verisi).

1440 px ve 390 px Chromium emülasyonu; gerçek telefon testi değildir.
"""
import os
import re
from pathlib import Path

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import expect

from production.models import WorkOrder
from samples.models import Sample
from ui_checks import browser_checks as demo
from ui_checks import demo_son_checks as final_demo
from warehouse.models import PalletMovement

FOREMAN = "4001"


@override_settings(DEBUG=True, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
class ForemanSampleBrowserTests(StaticLiveServerTestCase):
    password = demo.DemoBrowserTests.password
    page_for = demo.DemoBrowserTests.page_for
    setUp = final_demo.DemoFinalFixesBrowserTests.setUp
    tearDown = final_demo.DemoFinalFixesBrowserTests.tearDown
    shot = final_demo.DemoFinalFixesBrowserTests.shot

    def other_work_order(self, wo):
        """Başka müşterinin, bu ustabaşına atanmamış kurgusal iş emri (kapsam dışı denemeler için)."""
        from decimal import Decimal

        from accounts.models import User
        from catalog.models import Machine, Product, Unit
        from production import services as prod
        product = Product.objects.exclude(customer=wo.product.customer).filter(code="KRG-BS-050").get()
        other = prod.create_work_order(User.objects.get(personnel_no="3001"), product=product,
                                       revision=product.current_revision, machines=[Machine.objects.get(code="EXT-02")],
                                       target_quantity=Decimal("10"), unit=Unit.BOX)
        self.assertIsNone(other.assigned_foreman_id)
        return WorkOrder.objects.select_related("product__customer").get(pk=other.pk)

    def exercise(self, width):
        self.width = width
        self.screens = Path(os.getenv("FOREMAN_SCREENSHOT_DIR", "docs/screenshots/ustabasi-numune"))
        self.screens.mkdir(parents=True, exist_ok=True)
        mine = WorkOrder.objects.filter(assigned_foreman__personnel_no=FOREMAN)
        wo = mine.filter(pallets__isnull=False).select_related("product__customer").first()
        self.assertIsNotNone(wo, "seed_demo: ustabaşına atanmış, paleti olan iş emri bekleniyor")
        other = self.other_work_order(wo)
        page = self.page_for(FOREMAN)
        base = self.live_server_url

        # 1) Liste → Yeni numune: müşteri seçenekleri yalnız atanmış iş emirlerinden; ürün listesi aynı kuraldan.
        page.goto(base + "/numuneler/")
        page.get_by_role("link", name="Yeni numune").first.click()
        expect(page.locator("h1")).to_contain_text("Yeni numune")
        options = page.locator("[name=customer] option").evaluate_all("(n) => n.map(o => o.value).filter(Boolean)")
        expected = sorted(str(pk) for pk in mine.values_list("product__customer_id", flat=True).distinct())
        self.assertEqual(sorted(options), expected)
        self.assertNotIn(str(other.product.customer_id), options)
        page.locator("[name=customer]").select_option(str(wo.product.customer_id))
        expect(page.locator("[name=product]")).to_be_enabled()
        products = page.locator("[name=product] option").evaluate_all("(n) => n.map(o => o.value).filter(Boolean)")
        self.assertIn(str(wo.product_id), products)
        self.assertTrue(set(products) <= {str(pk) for pk in mine.values_list("product_id", flat=True)})
        self.shot(page, "ustabasi-yeni-numune")

        # 2) İş emri kartından başlat: bilinen bağlantılar dolu; kayıt stok hareketi yaratmaz.
        before = PalletMovement.objects.count()
        page.goto(base + f"/is-emirleri/{wo.pk}/")
        page.get_by_role("link", name="Bu iş emrinden numune başlat").click()
        expect(page.locator("[name=work_order_code]")).to_have_value(wo.code)
        expect(page.locator("[name=customer]")).to_have_value(str(wo.product.customer_id))
        expect(page.locator("[name=product]")).to_have_value(str(wo.product_id))
        page.locator("[name=kind]").select_option("uretim_kontrol")
        page.locator("[name=quantity]").fill("2")
        page.locator("[name=unit]").select_option("rulo")
        page.locator("[name=stock_source]").select_option("stok_disi")
        page.locator("[name=room]").fill(f"Kurgu hat dolabı {width}")
        # Değiştirilmiş iş emri kimliği sunucuda reddedilir; diğer bilgiler korunur.
        if other is not None:
            page.locator("[name=work_order_code]").fill(other.code)
            page.get_by_role("button", name="Numuneyi kaydet", exact=True).click()
            expect(page.locator(".form-errors")).to_be_visible()
            expect(page.locator("[name=room]")).to_have_value(f"Kurgu hat dolabı {width}")
            self.assertFalse(Sample.objects.filter(room=f"Kurgu hat dolabı {width}").exists())
            page.locator("[name=customer]").select_option(str(wo.product.customer_id))
            expect(page.locator("[name=product]")).to_be_enabled()
            page.locator("[name=product]").select_option(str(wo.product_id))
            page.locator("[name=work_order_code]").fill(wo.code)
        self.shot(page, "ustabasi-is-emrinden-numune")
        page.get_by_role("button", name="Numuneyi kaydet", exact=True).click()
        expect(page.locator(".msg-success")).to_contain_text("Numune kaydedildi")
        sample = Sample.objects.get(room=f"Kurgu hat dolabı {width}")
        self.assertEqual((sample.work_order_id, sample.product_id), (wo.pk, wo.product_id))
        self.assertEqual(PalletMovement.objects.count(), before)
        self.shot(page, "ustabasi-numune-kaydedildi")

        # 3) Kendi paletinden kısayol çalışır; kapsam dışı kısayol ve ürün sorgusu 404.
        pallet = wo.pallets.first()
        page.goto(base + f"/paletler/{pallet.pk}/")
        page.get_by_role("link", name="Bu paletten numune başlat").click()
        expect(page.locator("[name=pallet_code]")).to_have_value(pallet.code)
        expect(page.locator("[name=work_order_code]")).to_have_value(wo.code)
        if other is not None:
            for path in (f"/numuneler/yeni/?is_emri={other.pk}",
                         f"/numuneler/musteri/{other.product.customer_id}/urunler/",
                         f"/musteriler/{wo.product.customer_id}/"):
                status = page.request.get(base + path).status
                self.assertIn(status, (403, 404), path)
        # Stok ayırma ve kalite kararı ustabaşına açılmadı.
        page.goto(base + f"/numuneler/{sample.pk}/")
        expect(page.get_by_role("button", name=re.compile("Stoktan ayır"))).to_have_count(0)

    def test_desktop(self):
        self.exercise(1440)

    def test_mobile_emulation(self):
        self.exercise(390)
