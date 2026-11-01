"""Kurgusal verilerle 1440/390 px Chromium; gerçek telefon testi değildir."""
import os
from pathlib import Path

from playwright.sync_api import expect
from ui_checks import demo_son_checks as final_demo, browser_checks as demo
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from catalog.models import Product
from samples.models import Sample
from warehouse.models import Pallet, PalletMovement


@override_settings(DEBUG=True, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
class UsabilityBrowserTests(StaticLiveServerTestCase):
    password = demo.DemoBrowserTests.password
    page_for = demo.DemoBrowserTests.page_for
    setUp = final_demo.DemoFinalFixesBrowserTests.setUp
    tearDown = final_demo.DemoFinalFixesBrowserTests.tearDown
    shot = final_demo.DemoFinalFixesBrowserTests.shot

    def exercise(self, width):
        self.width = width
        self.screens = Path(os.getenv('USABILITY_SCREENSHOT_DIR', 'docs/screenshots/kullanim-kolayligi'))
        self.screens.mkdir(parents=True, exist_ok=True)
        sales = self.page_for('2001')
        product = Product.objects.get(code='ORN-KB-045')
        sales.goto(self.live_server_url + '/siparisler/yeni/')
        sales.locator('[name=customer]').select_option(str(product.customer_id))
        expect(sales.locator('[name=product]')).to_be_enabled()
        sales.locator('[name=product]').select_option(str(product.pk))
        sales.get_by_role('button', name='İleri: özellikler ve miktar →').click()
        expect(sales.locator('[name=quantity]')).to_be_visible()
        self.shot(sales, 'sonra-siparis-adim2')
        sales.locator('[name=quantity]').fill('12,5')
        sales.locator('[name=purpose]').select_option('ticari')
        sales.get_by_role('button', name='İleri: teslimat ve kontrol →').click()
        expect(sales.locator('[name=quantity]')).to_have_value('12,5')
        expect(sales.locator('.form-errors')).to_be_visible()
        sales.locator('details[data-spec-request] summary').click()
        sales.locator('[name=want_change]').check()
        sales.locator('[name=s_gusset_type]').select_option('yan')
        expect(sales.locator('[name=s_gusset_depth_mm]')).to_be_visible()
        sales.locator('[name=s_gusset_depth_mm]').fill('40')
        sales.locator('[name=s_gusset_type]').select_option('yok')
        expect(sales.locator('[name=s_gusset_depth_mm]')).to_be_hidden()
        expect(sales.locator('[name=s_gusset_depth_mm]')).to_be_disabled()
        self.assertNotIn('Kurgusal proses notu', sales.content())
        self.shot(sales, 'sonra-siparis-teknik-hata')
        quality = self.page_for('4004')
        pallet = Pallet.objects.select_related('product').get(product__code='DEN-PF-030')
        before = PalletMovement.objects.count()
        quality.goto(self.live_server_url + f'/paletler/{pallet.pk}/')
        quality.get_by_role('link', name='Bu paletten numune başlat').click()
        expect(quality.locator('[name=pallet_code]')).to_have_value(pallet.code)
        expect(quality.locator('[name=product]')).to_have_value(str(pallet.product_id))
        self.assertEqual(PalletMovement.objects.count(), before)
        quality.locator('[name=notes]').fill('Kurgusal uzun açıklama. ' * 35)
        quality.locator('[name=room]').fill('Kurgusal kalite odası')
        quality.get_by_role('button', name='Numuneyi kaydet', exact=True).click()
        expect(quality.locator('[name=room]')).to_have_value('Kurgusal kalite odası')
        expect(quality.locator('.form-errors')).to_be_visible()
        quality.locator('[name=kind]').select_option('sahit')
        quality.locator('[name=quantity]').fill('1')
        quality.locator('[name=unit]').select_option('adet')
        quality.locator('[name=stock_source]').select_option('stok_disi')
        self.shot(quality, 'sonra-numune-kaynak')
        quality.get_by_role('button', name='Numuneyi kaydet', exact=True).click()
        expect(quality.locator('.msg-success')).to_contain_text('Numune kaydedildi')
        sample = Sample.objects.get(room='Kurgusal kalite odası')
        self.assertEqual(sample.pallet_id, pallet.pk)
        self.assertEqual(PalletMovement.objects.count(), before)
        self.shot(quality, 'sonra-numune-durum')
        quality.goto(self.live_server_url + '/numuneler/?q=OLMAYAN-KURGU')
        expect(quality.get_by_role('link', name='Filtreleri temizle')).to_be_visible()
        self.shot(quality, 'sonra-numune-bos')
        planner = self.page_for('3001')
        planner.goto(self.live_server_url + f'/urun/{product.pk}/recete/yeni-revizyon/')
        if planner.get_by_role('button', name='Taslak aç', exact=True).count():
            planner.get_by_role('button', name='Taslak aç', exact=True).click()
        planner.get_by_role('button', name='+ Malzeme ekle').click()
        self.assertEqual(planner.locator('[data-row]').count(), int(planner.locator('[data-row-count]').input_value()))
        self.shot(planner, 'sonra-recete')
        for no, path in [('4001','/is-emirleri/'), ('4003','/is-emirleri/'), ('5001',f'/paletler/{pallet.pk}/')]:
            page = self.page_for(no)
            page.goto(self.live_server_url + path)
            self.shot(page, 'sonra-rol-' + no)
        # GET denial must remain server-side; no recipe body is returned to sales.
        response = sales.request.get(self.live_server_url + f'/urun/{product.pk}/recete/')
        self.assertEqual(response.status, 403)
        self.assertNotIn('Kurgusal proses notu', response.text())

    def test_desktop(self):
        self.exercise(1440)

    def test_mobile_emulation(self):
        self.exercise(390)
