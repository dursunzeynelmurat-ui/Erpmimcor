"""Gerçek PostgreSQL test DB + Chromium. Çalıştırma: manage.py test ui_checks.browser_checks.
Ekran emülasyonudur; fiziksel telefon testi değildir. Yalnız kurgusal seed_demo kullanır.
"""
import os
from pathlib import Path
from unittest.mock import patch

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.test import override_settings
from django.db import connections
from playwright.sync_api import sync_playwright, expect

from accounts.models import User
from catalog.models import Product, Customer, Machine, MaterialLot
from orders.models import SalesOrder
from production.models import WorkOrder, Coil
from warehouse.models import Pallet, PalletMovement, Shipment


@override_settings(DEBUG=True, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
class DemoBrowserTests(StaticLiveServerTestCase):
    password = 'Browser-Demo-2026!'

    def setUp(self):
        with patch.dict(os.environ, {'DEMO_USER_PASSWORD': self.password}):
            call_command('seed_demo', verbosity=0)
        # Playwright sync API owns an event loop; assertions here execute serially.
        # This opt-out is confined to this test process and never application settings.
        guard = patch.dict(os.environ, {'DJANGO_ALLOW_ASYNC_UNSAFE': 'true'})
        guard.start()
        self.addCleanup(guard.stop)
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH', '/usr/bin/chromium'),
                                             headless=True, args=['--no-sandbox'])
        self.contexts = {}
        self.screens = Path('docs/screenshots')
        self.screens.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.browser.close()
        connections.close_all()
        self.pw.stop()

    def page_for(self, no):
        if no not in self.contexts:
            context = self.browser.new_context(viewport={'width': self.width, 'height': 900 if self.width > 600 else 844})
            page = context.new_page()
            page.set_default_timeout(10000)
            page.goto(self.live_server_url + '/giris/')
            page.locator('[name=personnel_no]').fill(no)
            page.locator('[name=password]').fill(self.password)
            page.get_by_role('button', name='Giriş yap').click()
            expect(page.locator('h1')).to_contain_text('Merhaba')
            self.contexts[no] = page
        return self.contexts[no]

    def go(self, page, path):
        response = page.goto(self.live_server_url + path)
        self.assertEqual(response.status, 200, path)

    def shot(self, page, name):
        page.evaluate("window.scrollTo(0, 0)")
        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), self.width, name)
        ids = page.locator('[id]').evaluate_all('(nodes) => nodes.map(n => n.id)')
        self.assertEqual(len(ids), len(set(ids)), name + ': yinelenen HTML kimliği')
        keys = page.locator('input[name=idem_key]').evaluate_all('(nodes) => nodes.map(n => n.value)')
        self.assertEqual(len(keys), len(set(keys)), name + ': yinelenen işlem anahtarı')
        page.screenshot(path=str(self.screens / f'{name}-{self.width}.png'), full_page=False)

    def submit(self, page, name, scope=None):
        (scope or page).get_by_role('button', name=name, exact=True).click()
        if page.locator('dialog[open]').count():
            page.get_by_role('button', name='Onayla ve kaydet').click()
        page.wait_for_load_state('networkidle')
        self.assertEqual(page.locator('.msg-error').count(), 0, page.locator('body').inner_text()[-2500:])

    def section(self, page, title):
        section = page.locator('details.card').filter(has=page.locator('summary', has_text=title))
        section.locator('summary').click()
        return section

    def order(self, page, customer, product, qty, reference):
        self.go(page, '/siparisler/yeni/')
        page.locator('[name=customer]').select_option(str(Customer.objects.get(code=customer).pk))
        page.locator('[name=product]').select_option(str(Product.objects.get(code=product).pk))
        page.locator('[name=customer_ref]').fill(reference)
        page.locator('[name=due_date]').fill('2026-10-30')
        page.locator('[name=quantity]').fill('0')
        page.get_by_role('button', name='Siparişi kaydet').click()
        expect(page.locator('.form-errors')).to_be_visible()
        expect(page.locator('[name=customer_ref]')).to_have_value(reference)
        page.locator('[name=quantity]').fill(str(qty))
        self.submit(page, 'Siparişi kaydet')
        return SalesOrder.objects.get(customer_ref=reference)

    def shipment(self, depot, order, quantity):
        self.go(depot, f'/sevkiyatlar/siparis/{order.pk}/yeni/')
        depot.locator('[name=vehicle]').fill('DEMO 2026')
        self.submit(depot, 'Oluştur')
        shipment = Shipment.objects.filter(order=order).latest('pk')
        reservation = depot.locator('[name=reservation] option').nth(1).get_attribute('value')
        depot.locator('[name=reservation]').select_option(reservation)
        depot.locator('[name=quantity]').fill(str(quantity))
        self.submit(depot, 'Ekle')
        self.shot(depot, 'sevkiyat-onay')
        form = depot.locator(f'form[action="/sevkiyatlar/{shipment.pk}/onayla/"]')
        payload = form.locator('input').evaluate_all('(nodes) => Object.fromEntries(nodes.map(n => [n.name,n.value]))')
        self.submit(depot, 'Sevk et')
        # Kaydedilmiş POST'u aynı anahtarla yeniden gönder; stok yalnız bir kere azalmalı.
        response = depot.request.post(self.live_server_url + f'/sevkiyatlar/{shipment.pk}/onayla/', form=payload)
        self.assertEqual(response.status, 200)
        self.assertEqual(PalletMovement.objects.filter(shipment_line__shipment=shipment).count(), 1)
        return shipment

    def run_demo(self, width):
        self.width = width
        sales, planner, foreman, depot, admin = [self.page_for(n) for n in ['2001', '3001', '4001', '5001', '1001']]
        self.shot(admin, 'ana-ekran')
        # Beş rol: okuma ekranları ve sunucuda yetkisiz POST engeli.
        lists = ['/siparisler/', '/is-emirleri/', '/paletler/', '/sevkiyatlar/']
        # Ustabaşı şablonu yalnız kendine atanmış iş emirlerini ve paletleri görür; sipariş/sevkiyat 403.
        allowed = {id(foreman): {'/is-emirleri/', '/paletler/'}}
        for page in [sales, planner, foreman, depot, admin]:
            for path in lists:
                if path in allowed.get(id(page), set(lists)):
                    self.go(page, path)
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
                else:
                    self.assertEqual(page.request.get(self.live_server_url + path).status, 403, path)
        for page in [sales, planner, depot]:
            token = page.locator('[name=csrfmiddlewaretoken]').first.input_value()
            response = page.request.post(self.live_server_url + '/is-emirleri/1/baslat/',
                                         form={'csrfmiddlewaretoken': token, 'idem_key': f'denied-{width}-{id(page)}'})
            self.assertEqual(response.status, 403)
        self.assertEqual(sales.request.get(self.live_server_url + '/planlama/').status, 403)
        self.assertEqual(foreman.request.get(self.live_server_url + '/kullanicilar/').status, 403)
        self.go(admin, '/kullanicilar/')
        # Demo A — tüm yazmalar tarayıcı formlarından.
        order = self.order(sales, 'M-ORNEK', 'ORN-PF-030', 20, f'Demo-A-{width}')
        self.shot(sales, 'siparis')
        line = order.lines.get()
        self.go(planner, '/planlama/')
        self.shot(planner, 'planlama')
        self.go(planner, f'/planlama/kalem/{line.pk}/onay/')
        self.submit(planner, 'Teknik bilgileri ve reçeteyi onayla')
        self.go(planner, f'/is-emirleri/kalem/{line.pk}/yeni/')
        planner.locator('[name=machine_1]').select_option(str(Machine.objects.get(code='EXT-01').pk))
        planner.locator('[name=machine_2]').select_option(str(Machine.objects.get(code='PER-01').pk))
        planner.locator('[name=foreman]').select_option(str(User.objects.get(personnel_no='4001').pk))
        self.submit(planner, 'İş emrini oluştur')
        wo = WorkOrder.objects.get(order_line=line)
        op1, op2 = list(wo.operations.order_by('seq'))
        self.go(foreman, f'/is-emirleri/{wo.pk}/')
        self.submit(foreman, 'Üretimi başlat')
        self.shot(foreman, 'is-emri')
        self.go(foreman, f'/is-emirleri/{wo.pk}/malzeme-cikis/')
        foreman.locator('[name=lot]').select_option(str(MaterialLot.objects.filter(material__code='HM-LDPE').first().pk))
        foreman.locator('[name=quantity]').fill('70')
        self.submit(foreman, 'Çıkışı kaydet')
        self.go(foreman, f'/operasyon/{op1.pk}/bobin/')
        foreman.locator('[name=weight_kg]').fill('68')
        self.submit(foreman, 'Bobini kaydet')
        source = Coil.objects.get(work_order=wo)
        self.submit(foreman, 'Operasyonu başlat')
        first_op = foreman.locator('.op').first
        self.submit(foreman, 'Operasyonu tamamla', first_op)
        self.go(foreman, f'/operasyon/{op2.pk}/bobin/')
        foreman.locator('[name=source_1]').select_option(str(source.pk))
        foreman.locator('[name=consumed_1]').fill('68')
        foreman.locator('[name=weight_kg]').fill('70')
        foreman.get_by_role('button', name='Bobini kaydet').click()
        expect(foreman.locator('.form-errors')).to_be_visible()
        expect(foreman.locator('[name=consumed_1]')).to_have_value('68')
        expect(foreman.locator('[name=source_1]')).to_have_value(str(source.pk))
        foreman.locator('[name=weight_kg]').fill('67.5')
        self.shot(foreman, 'bobin-formu')
        self.submit(foreman, 'Bobini kaydet')
        self.go(foreman, f'/is-emirleri/{wo.pk}/palet/')
        foreman.locator('[name=box_count]').fill('20')
        foreman.locator('[name=coils]').check()
        self.submit(foreman, 'Kaydet')
        pallet = Pallet.objects.get(work_order=wo)
        self.shot(foreman, 'palet-uretim')
        self.go(foreman, f'/is-emirleri/{wo.pk}/')
        self.submit(foreman, 'Operasyonu tamamla')
        self.go(foreman, f'/is-emirleri/{wo.pk}/kapat/')
        foreman.locator('[name=scrap_kg]').fill('2.5')
        foreman.locator('[name=remaining_confirmed]').check()
        self.submit(foreman, 'İş emrini kapat')
        self.go(depot, f'/paletler/{pallet.pk}/')
        accept = self.section(depot, 'Depo kabulü ve tartım')
        accept.locator('[name=gross_kg]').fill('89.4')
        accept.locator('[name=pallet_tare_kg]').fill('21')
        self.submit(depot, 'Kabul et', accept)
        expect(depot.locator('main')).to_contain_text('Palet hariç ağırlık')
        self.go(foreman, f'/paletler/{pallet.pk}/')
        quality = self.section(foreman, 'Kalite durumu')
        quality.locator('[name=quality_status]').select_option('uygun')
        quality.locator('[name=reason]').fill('Demo: görsel ve ölçü kontrolü tamamlandı')
        self.submit(foreman, 'Kaydet', quality)
        self.go(sales, f'/paletler/{pallet.pk}/')
        reserve = self.section(sales, 'Siparişe ayır')
        reserve.locator('[name=order_line]').select_option(str(line.pk))
        reserve.locator('[name=quantity]').fill('20')
        self.submit(sales, 'Ayır', reserve)
        self.shipment(depot, order, 20)
        self.go(sales, f'/siparisler/{order.pk}/')
        expect(sales.locator('.progress-card')).to_contain_text('20 Kutu')
        self.shot(sales, 'siparis-tamamlandi')
        # Demo B: aynı paletten 40 → ayır 10 → sevk 10 → kalan 30.
        stock = Pallet.objects.get(product__code='DEN-PF-030')
        order_b = self.order(sales, 'M-DENEME', 'DEN-PF-030', 10, f'Demo-B-{width}')
        line_b = order_b.lines.get()
        self.go(sales, f'/paletler/{stock.pk}/')
        reserve = self.section(sales, 'Siparişe ayır')
        reserve.locator('[name=order_line]').select_option(str(line_b.pk))
        reserve.locator('[name=quantity]').fill('10')
        self.submit(sales, 'Ayır', reserve)
        stock.refresh_from_db()
        self.assertEqual((stock.box_count, stock.reserved_boxes, stock.available_boxes), (40, 10, 30))
        self.shipment(depot, order_b, 10)
        stock.refresh_from_db()
        self.assertEqual(stock.box_count, 30)
        self.assertEqual(stock.weight_status, 'yeniden_tartim_bekliyor')
        self.go(depot, f'/paletler/{stock.pk}/')
        expect(depot.locator('main')).to_contain_text('İçerik değişti; güncel ağırlık yeniden tartım bekliyor.')
        self.shot(depot, 'palet-kismi-sevk')
        # Hata, geri düzenleme, aynı anahtar ve geçerli bilgilerin korunması.
        transfer = self.section(depot, 'Başka palete aktar')
        transfer.locator('[name=quantity]').fill('999')
        transfer.locator('[name=reason]').fill('Demo kontrol gerekçesi')
        key = transfer.locator('[name=idem_key]').input_value()
        transfer.get_by_role('button', name='Aktar', exact=True).click()
        expect(depot.get_by_role('dialog')).to_contain_text('999')
        depot.get_by_role('button', name='Düzenlemeye dön').click()
        expect(transfer.locator('[name=quantity]')).to_have_value('999')
        transfer.get_by_role('button', name='Aktar', exact=True).click()
        depot.get_by_role('button', name='Onayla ve kaydet').click()
        expect(depot.locator('.form-errors')).to_be_visible()
        expect(depot.locator('main [name=quantity]')).to_have_value('999')
        expect(depot.locator('[name=reason]')).to_have_value('Demo kontrol gerekçesi')
        expect(depot.locator('main [name=idem_key]')).to_have_value(key)
        self.shot(depot, 'form-hatasi')
        stock.refresh_from_db()
        self.assertEqual(stock.box_count, 30)
        # Sunucu kaydettikten sonra yanıt kaybı: aynı form anahtarıyla tekrar tek hareket.
        self.go(depot, f'/paletler/{stock.pk}/')
        transfer = self.section(depot, 'Başka palete aktar')
        transfer.locator('[name=quantity]').fill('1')
        transfer.locator('[name=reason]').fill('Kurgusal bağlantı kaybı kontrolü')
        retry_payload = transfer.locator('input, textarea').evaluate_all(
            '(nodes) => Object.fromEntries(nodes.map(n => [n.name,n.value]))')
        transfer_url = self.live_server_url + f'/paletler/{stock.pk}/aktarim/'
        handled = []
        def lose_response(route):
            result = route.fetch(max_redirects=0)
            handled.append(result.status)
            route.abort('connectionfailed')
        depot.route(transfer_url, lose_response)
        transfer.get_by_role('button', name='Aktar', exact=True).click()
        depot.get_by_role('button', name='Onayla ve kaydet').click()
        depot.wait_for_load_state('load')
        self.assertEqual(handled, [302])
        depot.unroute(transfer_url, lose_response)
        response = depot.request.post(transfer_url, form=retry_payload)
        self.assertEqual(response.status, 200)
        stock.refresh_from_db()
        self.assertEqual(stock.box_count, 29)
        self.assertEqual(stock.movements.filter(kind='aktarim_cikis').count(), 1)
        # QR yalnız kayıt açar, anonim oturumu girişe yönlendirir.
        movements = PalletMovement.objects.count()
        self.go(depot, f'/q/{stock.qr_token}/')
        self.assertEqual(PalletMovement.objects.count(), movements)
        anonymous = self.browser.new_page()
        anonymous.goto(self.live_server_url + f'/q/{stock.qr_token}/')
        self.assertIn('/giris/', anonymous.url)
        anonymous.close()
        # Mobil gezinme ve görünür odak.
        if width == 390:
            depot.locator('.menu summary').click()
            expect(depot.locator('.menu nav')).to_be_visible()
            depot.keyboard.press('Escape')
            expect(depot.locator('.menu nav')).not_to_be_visible()
        self.go(sales, '/siparisler/?q=bulunamayan-kurgusal-kayit')
        expect(sales.locator('main')).to_contain_text('Sipariş yok.')
        self.shot(sales, 'bos-arama')

    def test_demo_desktop(self):
        self.run_demo(1440)

    def test_demo_mobile_emulation(self):
        self.run_demo(390)
