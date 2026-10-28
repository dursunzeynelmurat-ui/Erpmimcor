"""Kabul ortamı (c316506) salt okunur doğrulaması: yalnız GET; senaryo durumunu tüketmez. Parola .env'den okunur,
yazdırılmaz. Kullanım (sunucu açıkken):
  KABUL_ENV=<kabul-calisma-dizini>/.env KABUL_SAMPLE_TOKEN=<NUM-2026-00001 qr_token> python3 -I kabul/ortam_dogrula.py
Token salt okunur sorguyla alınır:
  psql -At -d mimcor_kabul_tnr_kopya -c "select qr_token from samples_sample where code='NUM-2026-00001'"
"""
import os
import sys

import segno
from dotenv import dotenv_values
from playwright.sync_api import sync_playwright

ENV = dotenv_values(os.environ.get("KABUL_ENV", ".env"))
URL, PW = ENV["APP_BASE_URL"].rstrip("/"), ENV["KABUL_TEST_PASSWORD"]
CHROME = os.environ.get("CHROMIUM_PATH") or None
USERS = ("8101", "8201", "8202", "8301", "8401", "8402", "8403", "8404", "8405", "8501", "8502", "8601")
res = []


def ok(name, cond, detail=""):
    res.append(bool(cond))
    print(("TAMAM " if cond else "HATA  ") + name + (f" – {detail}" if detail else ""))


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, headless=True, args=["--no-sandbox"])
    pages = {}

    def login(no):
        if no not in pages:
            pg = b.new_context().new_page()
            pg.goto(URL + "/giris/")
            pg.locator("[name=personnel_no]").fill(no)
            pg.locator("[name=password]").fill(PW)
            pg.get_by_role("button", name="Giriş yap").click()
            pg.wait_for_url(URL + "/")
            pages[no] = pg
        return pages[no]

    def get(no, path, code, *, has=(), hasnt=()):
        pg = login(no)
        r = pg.goto(URL + path)
        body = pg.content() if r.status == 200 else ""
        miss = [t for t in has if t not in body] + [f"!{t}" for t in hasnt if t in body]
        ok(f"{no} GET {path} → {code}" + (f" {list(has)}" if has else "") + (f" yok {list(hasnt)}" if hasnt else ""),
           r.status == code and not miss, f"HTTP {r.status} {miss if miss else ''}".strip())

    for no in USERS:
        try:
            login(no)
            ok(f"{no} giriş", True)
        except Exception as e:  # noqa: BLE001
            ok(f"{no} giriş", False, type(e).__name__)
    # Satışçı izolasyonu ve teknik özellikler
    get("8201", "/siparisler/", 200, has=("SIP-2026-00002", "SIP-2026-00004"), hasnt=("SIP-2026-00005",))
    get("8201", "/siparisler/5/", 404)
    get("8202", "/siparisler/2/", 404)
    get("8201", "/siparisler/2/", 200, has=("Yan körük", "Kapalı en", "KL-KBL-01", "Onaylı sürüm 1"),
        hasnt=("KBL-LDPE", "KBL-MAVI"))
    get("8201", "/siparisler/3/", 200, has=("Belirtilmemiş",))
    get("8201", "/siparisler/yeni/", 200)
    get("8201", "/siparisler/tekrar/?musteri=4&kaynak=2", 200, has=("SIP-2026-00002/01",))
    get("8201", "/urun/4/recete/", 403)
    get("8201", "/urun/4/baski-gorselleri/", 200, has=("Geçerli",))
    get("8202", "/urun/4/baski-gorselleri/", 404)
    get("8202", "/baski-gorseli/1/dosya/", 404)
    # Planlama
    get("8301", "/urun/4/recete/", 200, has=("KBL-MAVI", "Renklendirici"))
    get("8301", "/planlama/kalem/2/onay/", 200, has=("Yan körük", "Onaylı sürüm 1"))
    # Üretim: matbaa talimatında görsel ve baskı bilgisi, karışım yok; extruder talimatında karışım
    get("8405", "/talimat/2/", 200, has=("KL-KBL-01", "Sürüm 1"), hasnt=("KBL-LDPE",))
    get("8405", "/baski-gorseli/1/dosya/?onizleme=1", 200)
    get("8403", "/talimat/1/", 200, has=("KBL-LDPE", "KBL-MAVI"))
    get("8404", "/talimat/1/", 404)
    get("8405", "/urun/4/baski-gorselleri/", 404)
    # Numune
    get("8601", "/numuneler/", 200, has=("NUM-2026-00001",))
    get("8601", "/numuneler/kriterler/", 200, has=("Kalınlık", "Görünüm"))
    get("8501", "/numuneler/yeni/", 200)
    get("8201", "/numuneler/1/", 200, has=("Referans numune alındı.",), hasnt=("Hareket geçmişi", "Kalite lab"))
    get("8202", "/numuneler/1/", 404)
    token = os.environ.get("KABUL_SAMPLE_TOKEN")
    if token:
        pg = login("8601")
        html = pg.context.request.get(URL + "/numuneler/1/etiket/").text()
        expected = segno.make(f"{URL}/numune/q/{token}/", error="m").svg_inline(scale=4, dark="#000")
        ok("Numune QR içeriği APP_BASE_URL/numune/q/<anahtar>/ (SVG birebir)", expected in html and "APP_BASE_URL" not in html)
        r = pg.goto(f"{URL}/numune/q/{token}/")
        ok("Numune QR adresi oturum açıkken numune sayfasına gider", r.status == 200 and pg.url.endswith("/numuneler/1/"))
    b.close()
print(f"{sum(res)}/{len(res)} kontrol geçti.")
sys.exit(0 if all(res) else 1)
