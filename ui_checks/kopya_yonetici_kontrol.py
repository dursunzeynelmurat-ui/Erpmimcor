"""Kopya veritabanında yükseltme sonrası yönetici kontrolü (gerçek tarayıcı, gerçek sunucu).

Yalnız adı "kopya" içeren veritabanında çalışır; gerçek demo/UAT veritabanına dokunmaz. Parola DEMO_USER_PASSWORD
ortam değişkeninden (.env) okunur, yazdırılmaz. Kullanım (kopyaya bağlı yerel sunucu açıkken):

  POSTGRES_DB=kabul_mimcor_uat_kopya python manage.py runserver 127.0.0.1:8765 --noreload &
  POSTGRES_DB=kabul_mimcor_uat_kopya python ui_checks/kopya_yonetici_kontrol.py http://127.0.0.1:8765

Akış: (hazırlık, servislerle) kalem planlama onayı + depo personelinin hazırladığı/okuttuğu sevkiyat →
satışçı ve depo personeli oturum açıp sayfa/formu açık bırakır → yönetici tarayıcıdan (1) müşteri erişim
incelemesinde satışçının M-DENEME erişimini kaldırır, (2) depo rolüne "Önerilen şablonu uygula" der → açık
oturumdaki satışçının eski formu ve sayfası, depo personelinin açık sevk onayı formu reddedilir.
"""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mimcor.settings")
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")  # Playwright sync API kendi olay döngüsünü açar

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from ui_checks.support import launch  # noqa: E402

ADMIN, SALES, PLANNER, DEPOT = "1001", "2001", "3001", "5001"
RESULTS = []


def ok(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("TAMAM " if cond else "HATA  ") + name + (f" – {detail}" if detail else ""))


def prepare():
    """Kopyada sevk onayına hazır bir sevkiyat (servislerle, gerçek kullanıcılar adına)."""
    from accounts.models import User
    from core.tests.factories import load
    from orders import services as orders
    from orders.models import LineStatus, SalesOrder
    from warehouse import services as wh
    from warehouse.models import Pallet, Shipment, ShipmentStatus
    U = lambda no: User.objects.get(personnel_no=no)  # noqa: E731
    order = SalesOrder.objects.filter(customer__code="M-DENEME").order_by("pk").first()
    line = order.lines.order_by("pk").first()
    if line.status == LineStatus.WAITING_PLANNING:
        orders.approve_line(U(PLANNER), line, line.product.current_revision)
    s = Shipment.objects.filter(order=order, status=ShipmentStatus.PREPARED).first()
    if s is None:
        pallet = Pallet.objects.filter(product=line.product, box_count__gte=5).order_by("pk").first()
        res = wh.reserve(U(DEPOT), pallet, line, 5)
        s = wh.create_shipment(U(DEPOT), order)
        wh.add_shipment_line(U(DEPOT), s, res, 5)
        load(U(DEPOT), s)
    return order, line, s


def login(browser, url, no, password):
    page = browser.new_context().new_page()
    page.goto(url + "/giris/")
    page.locator("[name=personnel_no]").fill(no)
    page.locator("[name=password]").fill(password)
    page.get_by_role("button", name="Giriş yap").click()
    page.wait_for_url(url + "/")
    return page


def main(url):
    db = settings.DATABASES["default"]["NAME"]
    if "kopya" not in db:
        sys.exit(f"Güvenlik: yalnız kopya veritabanında çalışır (şu an: {db}).")
    password = os.environ.get("DEMO_USER_PASSWORD")
    if not password:
        sys.exit("DEMO_USER_PASSWORD tanımlı değil.")
    order, line, shipment = prepare()
    from accounts.models import AccessRole
    from catalog.models import CustomerAccess
    print(f"Veritabanı: {db} · sipariş {order.code} · sevkiyat {shipment.code} ({shipment.status})")
    depo = AccessRole.objects.get(code="depo")
    old_approve = depo.permissions.filter(action="shipment.approve").exists()
    if not old_approve:
        print("BİLGİ depo rolünde eski sevk onayı yetkisi yok (zaten şablonda); depo adımları uygulanmaz.")
    with sync_playwright() as pw:
        browser = launch(pw)
        sales = login(browser, url, SALES, password)
        depot = login(browser, url, DEPOT, password)
        admin = login(browser, url, ADMIN, password)

        r = sales.goto(f"{url}/siparisler/{order.pk}/")
        ok("Satışçı önce siparişi görür", r.status == 200)
        r = sales.goto(f"{url}/siparisler/kalem/{line.pk}/degistir/")
        ok("Satışçı kalem değişiklik formunu açar (açık bırakılır)", r.status == 200)
        r = depot.goto(f"{url}/sevkiyatlar/{shipment.pk}/")
        confirm = depot.locator(f"form[action='/sevkiyatlar/{shipment.pk}/onayla/'] button")
        if old_approve:
            ok("Depo personeli eski yetkiyle sevk onayı düğmesini görür (açık bırakılır)",
               r.status == 200 and confirm.count() == 1)
        else:
            ok("Depo personeli sevk onayı düğmesini görmez", r.status == 200 and confirm.count() == 0)

        # Yönetici: müşteri erişimini daraltır
        admin.goto(f"{url}/musteri-erisimleri/?satisci={SALES}&musteri=M-DENEME")
        admin.locator("input[name=ids]").first.check()
        admin.locator("select[name=islem]").select_option("kaldir")
        admin.locator("input[name=reason]").fill("Kabul denetimi: bölge değişikliği (kopya)")
        admin.get_by_role("button", name="Önizle").click()
        admin.locator("button[type=submit]", has_text="uygula").click()
        access = CustomerAccess.objects.filter(user__personnel_no=SALES, customer__code="M-DENEME").order_by("-pk").first()
        ok("Yönetici inceleme ekranından M-DENEME erişimini kaldırdı", access.revoked_at is not None)

        # Yönetici: depo rolüne güncel şablonu uygular
        admin.goto(f"{url}/roller/")
        row = admin.locator("tr", has=admin.locator(f"form[action='/roller/{depo.pk}/sablon/']"))
        if old_approve:
            row.locator("summary", has_text="Önerilen şablonu uygula").click()
            row.locator("textarea[name=reason]").fill("Kabul denetimi: son sevk onayı ambar yetkilisine ait (kopya)")
            row.locator("button[type=submit]").click()
        ok("Depo rolünden shipment.approve kaldırıldı",
           not depo.permissions.filter(action="shipment.approve").exists())

        # Açık oturumlar
        with sales.expect_response(lambda resp: resp.request.method == "POST") as info:
            sales.locator("[name=quantity]").fill("50")
            sales.locator("[name=reason]").fill("Eski açık form")
            sales.get_by_role("button", name="Kaydet").click()
        line.refresh_from_db()
        ok("Satışçının açık formu reddedildi", info.value.status == 404 and line.quantity != 50,
           f"HTTP {info.value.status}")
        r = sales.goto(f"{url}/siparisler/{order.pk}/")
        ok("Satışçı aynı oturumda siparişi artık göremez", r.status == 404, f"HTTP {r.status}")
        sales.goto(f"{url}/siparisler/?q={order.code}")
        ok("Satışçı aramada siparişi bulamaz", sales.locator("tbody td", has_text=order.code).count() == 0)
        if old_approve:
            with depot.expect_response(lambda resp: resp.request.method == "POST") as info:
                confirm.click()
            shipment.refresh_from_db()
            ok("Depo personelinin açık sevk onayı formu reddedildi",
               info.value.status == 403 and shipment.status == "hazirlandi", f"HTTP {info.value.status}")
        browser.close()
    bad = [n for n, c, _ in RESULTS if not c]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} kontrol geçti." + (f" Başarısız: {bad}" if bad else ""))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8765")
