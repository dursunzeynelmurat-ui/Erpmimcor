"""Saha senaryoları — gerçek LiveServer + PostgreSQL test DB + Chromium (1440 px masaüstü ve 390 px mobil emülasyonu).
Çalıştırma: manage.py test ui_checks.field_scenarios_checks. Ekran emülasyonudur; fiziksel telefon testi değildir.

1. Müşteri değiştirdi: onaylı kalemde miktar 20 → 15; etkilenen iş emri gösterilir, iş emri kendiliğinden değişmez;
   planlama iş emrini geri çeker, hedefi düzeltir, yeniden serbest bırakır.
2. Usta yanlış girdi: bobin 97 kg yerine 79 kg olmalı; üretim yetkilisi talep açar, ambar yetkilisi onaylar.
3. Vardiya bitti: ustabaşı devri kaydeder, başka yetkili teslim alır; iş emri ve kalan iş sürer.
4. Ürün iade geldi: çıkışı yapılmış sevkiyattan 3 kutu iade; ayrı palete kontrol bekliyor girer, kalite karar verir.
Önkoşullar servislerle (kurgusal seed_demo üzerine) kurulur; senaryonun kendisi tarayıcı formlarıyla yapılır.
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

from accounts.models import Shift, User
from catalog.models import Machine, MaterialLot, Product, Unit
from core.models import QualityStatus
from orders import services as orders
from production import lifecycle
from production import services as prod
from production.models import WorkOrder, WorkOrderStatus
from ui_checks import browser_checks as demo
from warehouse import services as wh
from warehouse.models import Pallet


def U(no):
    return User.objects.get(personnel_no=no)


@override_settings(DEBUG=True, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
class FieldScenarioBrowserTests(StaticLiveServerTestCase):
    password = demo.DemoBrowserTests.password
    page_for = demo.DemoBrowserTests.page_for
    go = demo.DemoBrowserTests.go
    submit = demo.DemoBrowserTests.submit

    def setUp(self):
        with patch.dict(os.environ, {"DEMO_USER_PASSWORD": self.password}):
            call_command("seed_demo", verbosity=0)
        guard = patch.dict(os.environ, {"DJANGO_ALLOW_ASYNC_UNSAFE": "true"})  # yalnız test süreci
        guard.start()
        self.addCleanup(guard.stop)
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH", "/usr/bin/chromium"),
                                               headless=True, args=["--no-sandbox"])
        self.contexts = {}
        self.screens = Path(os.getenv("SCENARIO_SCREENSHOT_DIR", "/tmp/mimcor-senaryo"))
        self.screens.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.browser.close()
        connections.close_all()
        self.pw.stop()

    def shot(self, page, name):
        page.evaluate("window.scrollTo(0, 0)")
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), self.width, name)
        page.screenshot(path=str(self.screens / f"{name}-{self.width}.png"), full_page=True)

    # --- önkoşullar (servis) ---------------------------------------------------------------
    def started_work_order(self, machine="EXT-02", product_code="ORN-PF-030", qty=D("20")):
        product = Product.objects.get(code=product_code)
        order = orders.create_order(U("2001"), customer=product.customer, order_date=timezone.localdate(),
                                    customer_ref=f"SAHA-{WorkOrder.objects.count()}",
                                    lines=[{"product": product, "quantity": qty, "unit": Unit.BOX}])
        orders.approve_order(U("2001"), order)
        line = orders.approve_line(U("3001"), order.lines.get(), product.current_revision)
        wo = prod.create_work_order(U("3001"), order_line=line, machines=[Machine.objects.get(code=machine)],
                                    target_quantity=qty, unit=Unit.BOX, foreman=U("4001"))
        lifecycle.release(U("3001"), wo)
        prod.start_work_order(U("4001"), wo)
        prod.issue_material(U("4001"), wo, MaterialLot.objects.filter(material__code="HM-LDPE").first(), D("100"))
        return wo

    # --- senaryolar -------------------------------------------------------------------------
    def customer_changed(self):
        sales, planner = self.page_for("2001"), self.page_for("3001")
        product = Product.objects.get(code="ORN-PF-030")
        order = orders.create_order(U("2001"), customer=product.customer, order_date=timezone.localdate(),
                                    customer_ref=f"DEGISIM-{self.width}",
                                    lines=[{"product": product, "quantity": D("20"), "unit": Unit.BOX}])
        orders.approve_order(U("2001"), order)
        line = orders.approve_line(U("3001"), order.lines.get(), product.current_revision)
        wo = prod.create_work_order(U("3001"), order_line=line, machines=[Machine.objects.get(code="EXT-01")],
                                    target_quantity=D("20"), unit=Unit.BOX, foreman=U("4001"))
        lifecycle.release(U("3001"), wo)
        self.go(sales, f"/siparisler/kalem/{line.pk}/degistir/")
        expect(sales.locator("main")).to_contain_text(wo.code)
        sales.locator("[name=quantity]").fill("15")
        sales.locator("[name=reason]").fill("Müşteri telefonla 15 kutuya düşürdü (kurgusal)")
        sales.get_by_role("button", name="Kaydet").click()
        expect(sales.locator(".msg-error")).to_contain_text("etkiler")
        expect(sales.locator(".field.has-error")).to_contain_text(wo.code)
        sales.locator("[name=confirm_impacts]").check()
        self.submit(sales, "Kaydet")
        line.refresh_from_db()
        wo.refresh_from_db()
        self.assertEqual((line.quantity, wo.target_quantity), (D("15"), D("20")))  # iş emri sessizce değişmez
        self.go(planner, f"/is-emirleri/{wo.pk}/")
        expect(planner.locator(".conflict")).to_contain_text("15")
        self.shot(planner, "senaryo1-celiski")
        planner.locator("summary", has_text="Düzenleme için geri çek").click()
        planner.locator("details[open] textarea[name=reason]").fill("Müşteri miktarı 15'e düşürdü")
        self.submit(planner, "Düzenleme için geri çek", planner.locator("details[open] form"))
        planner.get_by_role("link", name="Taslağı düzenle").click()
        planner.locator("[name=target_quantity]").fill("15")
        planner.locator("[name=reason]").fill("Kalem değişikliği")
        self.submit(planner, "Değişiklikleri kaydet")
        self.submit(planner, "Serbest bırak")
        wo.refresh_from_db()
        self.assertEqual((wo.status, wo.target_quantity), (WorkOrderStatus.PLANNED, D("15")))
        expect(planner.locator(".conflict")).to_have_count(0)

    def wrong_entry(self):
        wo = self.started_work_order()
        coil = prod.record_coil(U("4001"), wo.operations.get(), weight_kg=D("97"))
        manager, approver = self.page_for("4002"), self.page_for("5002")
        self.go(manager, f"/bobinler/{coil.pk}/")
        manager.get_by_role("link", name="Ağırlık düzeltme talebi").click()
        expect(manager.locator("main")).to_contain_text("Etkilenecek kayıtlar")
        manager.locator("[name=new_value]").fill("79")
        manager.locator("[name=reason]").fill("Terazi 97 gösterdi, yeniden tartımda 79 kg (kurgusal)")
        self.submit(manager, "Talebi gönder")
        coil.refresh_from_db()
        self.assertEqual(coil.weight_kg, D("97"))  # onaya kadar değişmez
        expect(manager.locator("main")).not_to_contain_text("Onayla ve uygula")
        self.go(approver, "/duzeltmeler/")
        approver.get_by_role("link", name="DZT-").first.click()
        self.shot(approver, "senaryo2-duzeltme-onay")
        self.submit(approver, "Onayla ve uygula")
        coil.refresh_from_db()
        self.assertEqual((coil.weight_kg, coil.remaining_kg), (D("79"), D("79")))

    def shift_ended(self):
        wo = self.started_work_order(machine="EXT-01")  # EXT-02'de 2. senaryonun işi sürüyor (tek aktif iş)
        prod.record_coil(U("4001"), wo.operations.get(), weight_kg=D("60"))
        foreman, night = self.page_for("4001"), self.page_for("4002")
        self.go(foreman, f"/is-emirleri/{wo.pk}/")
        foreman.get_by_role("link", name="Vardiya devri").click()
        foreman.locator("[name=to_shift]").select_option(str(Shift.objects.get(code="V2").pk))
        foreman.locator("[name=remaining_note]").fill("1 bobin daha, yaklaşık 40 kg")
        foreman.locator("[name=machine_side_material]").fill("LDPE ~15 kg makine başında")
        self.submit(foreman, "Devri kaydet")
        self.go(night, f"/is-emirleri/{wo.pk}/#devirler")
        expect(night.locator("#devirler")).to_contain_text("1 bobin daha")
        self.shot(night, "senaryo3-devir")
        self.submit(night, "Devri teslim aldım")
        h = wo.operations.get().handovers.get()
        self.assertEqual(h.acknowledged_by.personnel_no, "4002")
        self.assertEqual(len(h.open_coils), 1)
        wo.refresh_from_db()
        self.assertEqual(wo.status, WorkOrderStatus.STARTED)

    def product_returned(self):
        stock = Pallet.objects.get(product__code="DEN-PF-030", work_order__isnull=False, return_receipts__isnull=True,
                                   status="depoda")
        product = stock.product
        order = orders.create_order(U("2001"), customer=product.customer, order_date=timezone.localdate(),
                                    customer_ref=f"IADE-{self.width}",
                                    lines=[{"product": product, "quantity": D("5"), "unit": Unit.BOX}])
        orders.approve_order(U("2001"), order)
        res = wh.reserve(U("5001"), stock, order.lines.get(), 5)
        shipment = wh.create_shipment(U("5001"), order, "34 KRG 99")
        wh.add_shipment_line(U("5001"), shipment, res, 5)
        wh.prepare_shipment(U("5001"), shipment)
        wh.scan_pallet(U("5001"), shipment, stock.qr_token)
        wh.confirm_shipment(U("5002"), shipment)  # son onay ve çıkış: ambar yetkilisi
        wh.mark_exited(U("5002"), shipment)
        depot, quality = self.page_for("5001"), self.page_for("4004")
        self.go(depot, f"/sevkiyatlar/{shipment.pk}/")
        depot.get_by_role("link", name="İade al").click()
        depot.locator("[name=quantity]").fill("3")
        depot.locator("[name=reason]").fill("3 kutu ezik geldi (kurgusal)")
        self.submit(depot, "İadeyi kaydet")
        returned = Pallet.objects.filter(return_receipts__shipment_line__shipment=shipment).get()
        self.assertEqual((returned.box_count, returned.quality_status), (3, QualityStatus.PENDING))
        expect(depot.locator("main")).to_contain_text("Müşteri iadesi paleti")
        self.shot(depot, "senaryo4-iade")
        self.go(quality, f"/paletler/{returned.pk}/")
        section = quality.locator("details.card").filter(has=quality.locator("summary", has_text="Kalite durumu"))
        section.locator("summary").click()
        section.locator("[name=quality_status]").select_option("blokeli")
        section.locator("[name=reason]").fill("Ezik kutular; müşteriye yeniden gönderilmeyecek")
        self.submit(quality, "Kaydet", section)
        returned.refresh_from_db()
        self.assertEqual(returned.quality_status, QualityStatus.BLOCKED)

    def run_all(self, width):
        self.width = width
        self.customer_changed()
        self.wrong_entry()
        self.shift_ended()
        self.product_returned()

    def test_field_scenarios_desktop(self):
        self.run_all(1440)

    def test_field_scenarios_mobile_emulation(self):
        self.run_all(390)
