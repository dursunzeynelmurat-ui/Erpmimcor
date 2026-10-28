"""Kabul ortamı salt okunur doğrulaması (yalnız GET; senaryo durumunu tüketmez). Parola .env'den, yazdırılmaz.

Kullanım (sunucu açıkken):
  KABUL_ENV=<kabul-calisma-dizini>/.env KABUL_QR_TOKEN=<PLT-2026-00002 qr_token> python3 -I kabul/ortam_dogrula.py
QR token yalnız salt okunur sorguyla alınır:
  psql -At -d mimcor_kabul_kopya -c "select qr_token from warehouse_pallet where code='PLT-2026-00002'"
"""
import os, sys
from dotenv import dotenv_values
import segno
from playwright.sync_api import sync_playwright

ENV = dotenv_values(os.environ.get("KABUL_ENV", ".env"))
URL, PW = ENV["APP_BASE_URL"].rstrip("/"), ENV["KABUL_TEST_PASSWORD"]
CHROME = os.environ.get("CHROMIUM_PATH") or None
res = []
def ok(n, c, d=""):
    res.append(c); print(("TAMAM " if c else "HATA  ") + n + (f" – {d}" if d else ""))

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, headless=True, args=["--no-sandbox"])  # CHROME yoksa Playwright Chromium
    def login(no):
        pg = b.new_context().new_page()
        pg.goto(URL + "/giris/")
        pg.locator("[name=personnel_no]").fill(no)
        pg.locator("[name=password]").fill(PW)
        pg.get_by_role("button", name="Giriş yap").click()
        pg.wait_for_url(URL + "/")
        return pg
    for no in ("8101", "8201", "8202", "8301", "8401", "8402", "8403", "8404", "8501", "8502"):
        try:
            login(no).context.close(); ok(f"{no} giriş", True)
        except Exception as e:  # noqa
            ok(f"{no} giriş", False, type(e).__name__)
    a = login("8201")
    a.goto(URL + "/siparisler/")
    body = a.locator("tbody").inner_text()
    ok("8201 listesinde SIP-2026-00002 (KBL-YETKILI) ve SIP-2026-00004 (KBL-SURELI)", "SIP-2026-00002" in body and "SIP-2026-00004" in body)
    ok("8201 listesinde SIP-2026-00005 (KBL-YETKISIZ) yok", "SIP-2026-00005" not in body)
    ok("8201 /siparisler/5/ → 404", a.goto(URL + "/siparisler/5/").status == 404)
    bb = login("8202")
    ok("8202 /siparisler/5/ → 200", bb.goto(URL + "/siparisler/5/").status == 200)
    ok("8202 /siparisler/2/ → 404", bb.goto(URL + "/siparisler/2/").status == 404)
    s = login("8502")
    r = s.goto(URL + "/paletler/2/etiket/")
    html = s.context.request.get(URL + "/paletler/2/etiket/").text()  # ham sunucu yanıtı (DOM değil)
    token = os.environ["KABUL_QR_TOKEN"]  # palet PLT-2026-00002 kaydından (salt okunur sorgu)
    expected = segno.make(f"{URL}/q/{token}/", error="m").svg_inline(scale=4, dark="#000") if token else ""
    ok("Etiket sayfası 200 ve APP_BASE_URL uyarısı yok", r.status == 200 and "APP_BASE_URL" not in html)
    ok("QR içeriği APP_BASE_URL/q/<token>/ (SVG birebir aynı)", bool(token) and expected in html, f"{URL}/q/…/")
    r2 = s.goto(f"{URL}/q/{token}/")
    ok("QR adresi oturum açıkken palet sayfasına gider", r2.status == 200 and s.url.endswith("/paletler/2/"), s.url.replace(token or "", "…"))
    # Senaryo önkoşulları (yalnız GET)
    pre = [("8402", "/duzeltmeler/yeni/malzeme_cikis/8/", 200, None), ("8403", "/talimat/1/", 200, "KBL-LDPE"),
           ("8404", "/talimat/1/", 404, None), ("8403", "/is-emirleri/3/malzeme-cikis/", 200, None),
           ("8301", "/urun/4/recete/", 200, "KBL-SLIP"), ("8403", "/urun/4/recete/", 403, None),
           ("8201", "/siparisler/kalem/2/degistir/", 200, None)]
    for no, path, code, text in pre:
        pg = login(no)
        r = pg.goto(URL + path)
        good = r.status == code and (text is None or text in pg.content())
        ok(f"{no} GET {path} → {code}" + (f" ve '{text}'" if text else ""), good, f"HTTP {r.status}")
        pg.context.close()
    for no, want in (("8501", 0), ("8502", 1)):
        pg = login(no)
        pg.goto(URL + "/sevkiyatlar/1/")
        n = pg.locator("form[action='/sevkiyatlar/1/onayla/'] button").count()
        ok(f"{no} SVK-2026-00001 'Sevk et' düğmesi {'var' if want else 'yok'}", n == want)
        pg.context.close()
    b.close()
print(f"{sum(res)}/{len(res)} kontrol geçti.")
sys.exit(0 if all(res) else 1)
