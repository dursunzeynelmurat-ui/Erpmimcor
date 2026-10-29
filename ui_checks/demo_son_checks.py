"""Demo öncesi son düzeltmeler — gerçek LiveServer + PostgreSQL test DB + Chromium (1440 px masaüstü, 390 px mobil
**emülasyonu**; fiziksel telefon, yazıcı testi ve gerçek kullanıcı gözlemi değildir). Tüm yazmalar tarayıcı formlarından.

1. Demo zinciri: satış yeni siparişte farklı teknik özellik ister (en 400 → 420 mm) → planlama değişiklik isteğinden
   yeni revizyon taslağı açar ve onaylar → kalem teknik onayında varsayılan = beklenen revizyon (başka revizyon
   seçilince kart değişir) → en değiştiği için baskı görseli onayları yenilenir (iç teknik + müşteri) → iş emri
   (extruder + matbaa) → bobinler → palet → depo kabulü → kalite → ayırma → sevkiyat. Satış ekranlarında iç proses
   notu yok; iş emri talimatında ürüne özel ve müşteri genel paketleme başlıkları ayrı.
2. Repete: kaynak Rev.1 ile onaylı; ürünün güncel revizyonu Rev.2. Teknik onayda varsayılan Rev.1; Rev.2 seçilince
   gerekçesiz onay reddedilir, girilenler korunur; gerekçeyle onaylanır.
3. Numune: firma stoğundan 2 kutu numune; stoktan ayırmadan gönderim reddedilir (girilen bilgi ve bölüm korunur);
   ayırma → gönderim tek stok hareketi.
"""
import os
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.db import connections
from django.test import override_settings
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from accounts.models import User
from catalog.models import Customer, Machine, Product
from orders import services as orders
from orders.models import LineChangeRequest, SalesOrder
from production.models import Coil, WorkOrder
from samples.models import Sample
from ui_checks import browser_checks as demo
from ui_checks import support
from warehouse.models import MovementKind, Pallet, PalletMovement

SECRET_NOTE = "Kurgusal proses notu"  # seed_demo'daki iç proses notu: satış yanıtında olmamalı


@override_settings(DEBUG=True, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
class DemoFinalFixesBrowserTests(StaticLiveServerTestCase):
    password = demo.DemoBrowserTests.password
    page_for = demo.DemoBrowserTests.page_for
    go = demo.DemoBrowserTests.go
    submit = demo.DemoBrowserTests.submit
    section = demo.DemoBrowserTests.section
    shipment = demo.DemoBrowserTests.shipment

    def setUp(self):
        with patch.dict(os.environ, {"DEMO_USER_PASSWORD": self.password}):
            call_command("seed_demo", verbosity=0)
        guard = patch.dict(os.environ, {"DJANGO_ALLOW_ASYNC_UNSAFE": "true"})  # yalnız test süreci
        guard.start()
        self.addCleanup(guard.stop)
        self.pw = sync_playwright().start()
        self.browser = support.launch(self.pw)
        self.contexts = {}
        self.screens = Path(os.getenv("DSD_SCREENSHOT_DIR", "docs/screenshots/demo-son-duzeltmeler"))
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

    def no_internal(self, page, where):
        body = page.content()
        self.assertFalse(SECRET_NOTE in body, f"{where}: iç proses notu satış yanıtında")
        self.assertFalse("HM-LDPE" in body, f"{where}: karışım satış yanıtında")

    # --- 1. Demo zinciri --------------------------------------------------------------
    def run_chain(self, width):
        self.width = width
        product = Product.objects.get(code="ORN-KB-045")
        customer = Customer.objects.get(code="M-ORNEK")
        base_rev = product.current_revision
        sales, planner = self.page_for("2001"), self.page_for("3001")

        self.go(sales, "/siparisler/yeni/")
        sales.locator("[name=customer]").select_option(str(customer.pk))
        sales.locator("[name=product]").select_option(str(product.pk))
        sales.get_by_role("button", name="İleri: özellikler ve miktar →").click()
        self.no_internal(sales, "yeni sipariş 2. adım")
        sales.locator("[name=quantity]").fill("10")
        sales.locator("[name=purpose]").select_option("ticari")
        sales.locator("[name=customer_ref]").fill(f"DSD-{width}")
        req = sales.locator("details[data-spec-request]")
        req.locator("summary").click()
        expect(req.locator("[name=s_width_mm]")).to_have_value("400")  # mevcut değer otomatik
        req.locator("[name=want_change]").check()
        req.locator("[name=s_width_mm]").fill("420")
        self.shot(sales, "dsd-1-talep-adim2")
        sales.get_by_role("button", name="İleri: teslimat ve kontrol →").click()
        expect(sales.locator(".form-errors")).to_be_visible()  # gerekçe yok
        expect(sales.locator("[name=s_width_mm]")).to_have_value("420")
        sales.locator("[name=change_reason]").fill("Müşteri 420 mm istedi (kurgusal)")
        sales.get_by_role("button", name="İleri: teslimat ve kontrol →").click()
        expect(sales.locator("main")).to_contain_text("420 mm")
        expect(sales.locator("main")).to_contain_text("Planlama kararı gerekir")
        sales.locator("[name=delivery_confirmed]").check()
        self.shot(sales, "dsd-1-talep-ozet")
        self.submit(sales, "Siparişi kaydet")
        expect(sales.locator(".msg-success")).to_contain_text("Teknik talebiniz planlamaya iletildi")
        self.submit(sales, "Siparişi onayla")
        order = SalesOrder.objects.get(customer_ref=f"DSD-{width}")
        line = order.lines.get()
        cr = LineChangeRequest.objects.get(line=line)
        self.no_internal(sales, "sipariş ayrıntısı")
        self.shot(sales, "dsd-1-siparis")

        # Planlama: talep beklerken onay yok → yeni revizyon taslağı → onay
        self.go(planner, f"/planlama/kalem/{line.pk}/onay/")
        expect(planner.locator("main")).to_contain_text("karara bağlanmadan onay verilemez")
        self.go(planner, f"/planlama/degisiklik/{cr.pk}/")
        expect(planner.locator("table").first).to_contain_text("420 mm")
        self.shot(planner, "dsd-2-degisiklik-istegi")
        self.submit(planner, "Aynı ürün koduna yeni teknik revizyon")
        expect(planner.locator("[name=width_mm]")).to_have_value("420")
        if not planner.locator("[name=change_reason]").input_value():
            planner.locator("[name=change_reason]").fill("Satış talebi: 420 mm (kurgusal)")
        self.submit(planner, "Onayla ve revizyon oluştur")
        expect(planner.locator(".msg-success")).to_contain_text("Reçete onaylandı")
        product.refresh_from_db()
        new_rev = product.current_revision
        self.assertNotEqual(new_rev.pk, base_rev.pk)

        # Teknik onay: varsayılan = beklenen; başka revizyon seçilince kart değişir
        self.go(planner, f"/planlama/kalem/{line.pk}/onay/")
        select = planner.locator("[name=revision]")
        expect(select).to_have_value(str(new_rev.pk))
        expect(planner.locator(f'[data-revision-card="{new_rev.pk}"]')).to_be_visible()
        select.select_option(str(base_rev.pk))
        expect(planner.locator(f'[data-revision-card="{base_rev.pk}"]')).to_be_visible()
        expect(planner.locator(f'[data-revision-card="{new_rev.pk}"]')).to_be_hidden()
        expect(planner.locator(f'[data-revision-card="{base_rev.pk}"]')).to_contain_text("400 mm")
        self.shot(planner, "dsd-3-teknik-onay-fark")
        select.select_option(str(new_rev.pk))
        self.submit(planner, "Teknik bilgileri ve reçeteyi onayla")
        line.refresh_from_db()
        self.assertEqual(line.approved_revision, new_rev)

        # Baskı: en değişti → onaylar yenilenir
        self.go(planner, f"/urun/{product.pk}/baski-gorselleri/")
        art = planner.locator("article.artwork").first
        expect(art).to_contain_text("Onay geçersiz")
        self.submit(planner, "İç teknik onay ver", art)
        self.go(sales, f"/urun/{product.pk}/baski-gorselleri/")
        art = sales.locator("article.artwork").first
        art.locator("summary", has_text="Müşteri onayını kaydet").click()
        art.locator("[name=customer_contact]").fill("Örnek Gıda Satın Alma (kurgusal)")
        art.locator("[name=evidence]").fill("Kurgusal e-posta onayı 420 mm")
        self.submit(sales, "Müşteri onayını kaydet", art)
        expect(sales.locator("article.artwork").first).to_contain_text("Geçerli")
        self.shot(sales, "dsd-4-baski-gecerli")

        # İş emri: extruder + matbaa; serbest bırakmada görsel sabitlenir
        self.go(planner, f"/is-emirleri/kalem/{line.pk}/yeni/")
        planner.locator("[name=machine_1]").select_option(str(Machine.objects.get(code="EXT-02").pk))
        planner.locator("[name=machine_2]").select_option(str(Machine.objects.get(code="MAT-01").pk))
        planner.locator("[name=foreman]").select_option(str(User.objects.get(personnel_no="4001").pk))
        self.submit(planner, "İş emrini oluştur")
        self.submit(planner, "Serbest bırak")
        wo = WorkOrder.objects.get(order_line=line)
        self.assertEqual(wo.recipe_revision, new_rev)
        self.assertIsNotNone(wo.artwork_id)
        op1, op2 = list(wo.operations.order_by("seq"))
        foreman = self.page_for("4001")
        self.go(foreman, f"/is-emirleri/{wo.pk}/")
        self.submit(foreman, "Üretimi başlat")
        foreman.locator("summary", has_text="Bu operasyonun teknik bilgisi").last.click()
        expect(foreman.locator("main")).to_contain_text("Müşteri genel paketleme talimatı")
        expect(foreman.locator("main")).to_contain_text("Ürüne özel ambalaj / etiket notu")
        self.shot(foreman, "dsd-5-is-emri")
        self.go(foreman, f"/operasyon/{op1.pk}/bobin/")
        foreman.locator("[name=weight_kg]").fill("60")
        self.submit(foreman, "Bobini kaydet")
        source = Coil.objects.get(work_order=wo, operation=op1)
        self.submit(foreman, "Operasyonu başlat")
        self.submit(foreman, "Operasyonu tamamla", foreman.locator(".op").first)
        self.go(foreman, f"/operasyon/{op2.pk}/bobin/")
        foreman.locator("[name=source_1]").select_option(str(source.pk))
        foreman.locator("[name=consumed_1]").fill("60")
        foreman.locator("[name=weight_kg]").fill("59")
        self.submit(foreman, "Bobini kaydet")
        self.go(foreman, f"/is-emirleri/{wo.pk}/palet/")
        foreman.locator("[name=box_count]").fill("10")
        foreman.locator("[name=coils]").check()
        self.submit(foreman, "Kaydet")
        pallet = Pallet.objects.get(work_order=wo)

        depot = self.page_for("5001")
        self.go(depot, f"/paletler/{pallet.pk}/")
        accept = self.section(depot, "Depo kabulü ve tartım")
        accept.locator("[name=gross_kg]").fill("80")
        accept.locator("[name=pallet_tare_kg]").fill("21")
        self.submit(depot, "Kabul et", accept)
        self.go(foreman, f"/paletler/{pallet.pk}/")
        quality = self.section(foreman, "Kalite durumu")
        quality.locator("[name=quality_status]").select_option("uygun")
        quality.locator("[name=reason]").fill("Demo: 420 mm ölçü ve baskı kontrolü uygun (kurgusal)")
        self.submit(foreman, "Kaydet", quality)
        self.go(sales, f"/paletler/{pallet.pk}/")
        reserve = self.section(sales, "Siparişe ayır")
        reserve.locator("[name=order_line]").select_option(str(line.pk))
        reserve.locator("[name=quantity]").fill("10")
        self.submit(sales, "Ayır", reserve)
        self.shipment(depot, order, 10)
        self.go(sales, f"/siparisler/{order.pk}/")
        self.no_internal(sales, "sevk sonrası sipariş")
        self.shot(sales, "dsd-6-sevk-sonrasi")
        self.assertEqual(PalletMovement.objects.filter(pallet=pallet, kind=MovementKind.SHIPMENT).count(), 1)

    def test_demo_chain_desktop(self):
        self.run_chain(1440)

    def test_demo_chain_mobile_emulation(self):
        self.run_chain(390)

    # --- 2. Repete ve 3. numune --------------------------------------------------------
    def run_repeat_and_sample(self, width):
        self.width = width
        product = Product.objects.get(code="ORN-PF-030")
        rev1 = product.recipe.revisions.get(rev_no=1)
        rev2 = product.current_revision
        src = orders.create_order(User.objects.get(personnel_no="2001"), customer=product.customer,
                                  order_date=timezone.localdate(), customer_ref=f"DSD-KAYNAK-{width}",
                                  lines=[{"product": product, "quantity": D("10"), "unit": "kutu"}])
        orders.approve_order(User.objects.get(personnel_no="2001"), src)
        src_line = orders.approve_line(User.objects.get(personnel_no="3001"), src.lines.get(), rev1)
        sales, planner = self.page_for("2001"), self.page_for("3001")
        self.go(sales, f"/siparisler/tekrar/?musteri={product.customer_id}&kaynak={src.pk}")
        sales.locator(f"[name=qty_{src_line.pk}]").fill("6")
        self.submit(sales, "İleri: teslimat ve kontrol →")
        sales.locator("[name=purpose]").select_option("ticari")
        sales.locator("[name=delivery_confirmed]").check()
        self.submit(sales, "Tekrar siparişi kaydet")
        self.submit(sales, "Siparişi onayla")
        line = SalesOrder.objects.filter(copied_from=src).latest("pk").lines.get()
        self.go(planner, f"/planlama/kalem/{line.pk}/onay/")
        expect(planner.locator("[name=revision]")).to_have_value(str(rev1.pk))
        expect(planner.locator("main")).to_contain_text(f"Rev.{rev1.rev_no}")
        planner.locator("[name=revision]").select_option(str(rev2.pk))
        expect(planner.locator(f'[data-revision-card="{rev2.pk}"]')).to_contain_text("Beklenen Rev.1 ile farklar")
        planner.locator("[name=note]").fill("DSD planlama notu")
        planner.get_by_role("button", name="Teknik bilgileri ve reçeteyi onayla").click()
        expect(planner.locator(".form-errors")).to_be_visible()
        expect(planner.locator("[name=note]")).to_have_value("DSD planlama notu")
        self.shot(planner, "dsd-7-repete-sapma-reddi")
        planner.locator("[name=deviation_confirmed]").check()
        planner.locator("[name=deviation_reason]").fill("Müşteri Rev.2'yi yazılı kabul etti (kurgusal)")
        self.submit(planner, "Teknik bilgileri ve reçeteyi onayla")
        line.refresh_from_db()
        self.assertEqual(line.approved_revision, rev2)

        # Numune: stoktan ayırmadan gönderim engeli
        depot = self.page_for("5001")
        stock = Pallet.objects.get(code="PLT-2026-00001")
        self.go(depot, "/numuneler/yeni/")
        depot.locator("[name=kind]").select_option("musteriye_giden")
        depot.locator("[name=customer]").select_option(str(stock.product.customer_id))
        depot.locator("[name=product]").select_option(str(stock.product_id))
        depot.locator("[name=pallet_code]").fill(stock.code)
        depot.locator("[name=quantity]").fill("2")
        depot.locator("[name=unit]").select_option("kutu")
        depot.locator("[name=stock_source]").select_option("stoktan")
        depot.locator("[name=room]").fill("Numune odası")
        self.submit(depot, "Numuneyi kaydet")
        sample = Sample.objects.latest("pk")
        expect(depot.locator("#stok-eksik")).to_be_visible()
        depot.locator('details[data-action="gonder"] summary').click()
        depot.locator("#id_send_counterpart").fill("Kurgu Kargo 42")
        depot.get_by_role("button", name="Gönderimi kaydet").click()
        expect(depot.locator('details[data-action="gonder"]')).to_have_attribute("open", "")
        expect(depot.locator("#id_send_counterpart")).to_have_value("Kurgu Kargo 42")
        expect(depot.locator('details[data-action="gonder"] .form-errors')).to_contain_text("Stoktan ayır")
        stock.refresh_from_db()
        self.assertEqual(stock.box_count, 40)
        self.shot(depot, "dsd-8-numune-gonderim-engeli")
        depot.locator('details[data-action="stok"] summary').click()
        depot.locator("#id_stock_pallet_code").fill(stock.code)
        self.submit(depot, "Kutuları stoktan ayır")
        depot.locator('details[data-action="gonder"] summary').click()
        depot.locator("#id_send_counterpart").fill("Kurgu Kargo 42")
        self.submit(depot, "Gönderimi kaydet")
        stock.refresh_from_db()
        sample.refresh_from_db()
        self.assertEqual((stock.box_count, sample.physical_status), (38, "gonderildi"))
        self.assertEqual(sample.revision, stock.work_order.recipe_revision)  # fiziksel kaynağın revizyonu
        self.shot(depot, "dsd-9-numune-gonderildi")

    def test_repeat_and_sample_desktop(self):
        self.run_repeat_and_sample(1440)

    def test_repeat_and_sample_mobile_emulation(self):
        self.run_repeat_and_sample(390)
