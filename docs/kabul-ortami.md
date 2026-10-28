# Manuel kabul ortamı — kabul sürümü `63a5105` (28 Ekim 2026)

Bu belge yalnız kabul ortamını ve somut manuel senaryoyu anlatır. Uygulama kodu değişmedi; bu branch
(`kabul/mimcor-ortam-63a5105`) 63a5105 üzerine yalnız bu belgeyi ve `kabul/` altındaki iki hazırlık/doğrulama betiğini
ekler. Merge, PR veya canlı yayın yok. İşletme kararı bekleyen dört konunun (ambar/depo düzeltme talep hakkı, tedarikçi
parti/irsaliye zorunluluğu, belgesi beklenen mal kabulü, geciken devir) davranışı değiştirilmedi
(`docs/kabul-denetimi.md` bölüm 6).

> **Önemli erişim sınırı.** Ortam bir bulut oturum konteynerinde kuruldu. Uygulama yalnız konteynerin içinden
> `http://127.0.0.1:8770` adresinde çalışıyor; sizin bilgisayarınızın tarayıcısı veya telefonunuz bu adrese **erişemez**
> ve telefonda `127.0.0.1`/`localhost` zaten telefonun kendisini gösterir. Güvenli bir telefon erişim yolu hazır değil;
> tünel, port yönlendirme veya internete açık yayın **yapılmadı**. Telefonla QR ve etiket testi (T6) için ortamın
> fabrika yerel ağındaki bir makinede kurulması gerekir (bölüm 6). Bu ortamdaki doğrulamalar konteyner içindeki
> Chromium ile yapıldı; **gerçek telefon testi yapılmadı**.

## 1. Sürüm ve çalışma dizini

| | |
|---|---|
| Kabul commit'i | `63a5105` (uzak `fix/mimcor-kabul-denetimi` başı ile aynı; daha yeni değişiklik yoktu) |
| Çalışma dizini | `git worktree add --detach /workspace/mimcor-kabul-63a5105 63a5105` (ana çalışma kopyasından ayrı) |
| Ayarlar | Çalışma dizinindeki `.env` (git dışı, yalnız sahibi okuyabilir — `chmod 600`). `POSTGRES_DB=mimcor_kabul_kopya`, `DJANGO_DEBUG=1`, `DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost`, `APP_BASE_URL=http://127.0.0.1:8770`, yeni `DJANGO_SECRET_KEY`, `KABUL_TEST_PASSWORD`. Demo parolası (`DEMO_USER_PASSWORD`) bu dosyaya alınmadı. |
| Neden DEBUG=1 | `DJANGO_DEBUG=0` iken oturum ve CSRF çerezleri `Secure` olur; HTTPS olmayan yerel adreste giriş çalışmaz. Statik dosyaları da yalnız geliştirme sunucusu (DEBUG=1) sunar. Bu kabul ortamı içindir; canlı kurulum HTTPS ve ayrı statik sunum ister. |

Başlatma (konteyner yeniden başlarsa):

```
service postgresql start
cd /workspace/mimcor-kabul-63a5105
python manage.py runserver 127.0.0.1:8770 --noreload
```

## 2. Kabul veritabanı

| | |
|---|---|
| Kaynak | `mimcor_uat` (değiştirilmedi) |
| Kopya | `mimcor_kabul_kopya`, `createdb -O mimcor -T mimcor_uat` ile, 2026-10-28T07:49:38Z (kaynakta açık bağlantı yokken) |
| Kopyada uygulanan migration'lar (19) | accounts.0005_remove_direct_lot_adjust, accounts.0006_shipment_permission_split, accounts.0007_operator_machine_shift, catalog.0006_alter_material_unit, catalog.0007_product_package, catalog.0008_access_source, warehouse.0002_lifecycle, production.0005_lifecycle, production.0006_route_and_deviation, corrections.0001_initial, corrections.0002_lot_count_kind, warehouse.0003_shipment_stages_returns_labels, orders.0003_lifecycle, orders.0004_exceptions, orders.0005_compat_scope, orders.0006_over_delivery_history, orders.0007_replacement_link, production.0007_closure_review, production.0008_responsible_shift |
| Kontrol | Kopyada uygulanmamış migration 0. Kaynak `mimcor_uat` hâlâ 19, `mimcor` hâlâ 28 uygulanmamış (dokunulmadı). |

## 3. Yetki denetimi ve kopyada yapılanlar

`python manage.py yetki_denetimi` (salt okunur) **öncesi**: KURAL 1 — 5001 depo rolünden `shipment.approve`;
İNCELE 1 — 2001'in M-DENEME, M-KURGU, M-ORNEK toplu taşınan erişimleri; BİLGİ 1 — depo rolü şablondan farklı.

Hazırlık betiği `kabul/kabul_ortami_hazirla.py` (yalnız adı "kabul" içeren veritabanında çalışır; servisler üzerinden,
işlem geçmişine yazarak) kopyada şunları yaptı:

- **Depo rolü:** 8101 adına, gerekçeli rol düzenlemesiyle **yalnız** `shipment.approve` kaldırıldı. Şablon toplu
  uygulanmadı; rolde eksik olan `customer.document.view` eklenmedi (aynı eksik `uretim_yetkilisi` rolünde de var; ikisi
  BİLGİ olarak kaldı). Rol ortak olduğu için UAT kullanıcısı 5001'i de etkiler; 5001'in parolası/ataması değişmedi.
- **Sevk/çıkış yetkileri, kabul kuralıyla karşılaştırma** (sonrası):

  | Rol | Mevcut sevk yetkileri | Kural | Uygun |
  |---|---|---|---|
  | depo | view, manage | view, manage | ✔ |
  | ambar_yetkilisi | view, manage, approve, undo, exit | aynı | ✔ |
  | yonetici | view, manage, approve, undo, exit | aynı | ✔ |
  | planlama, satis, uretim_yetkilisi | view | yalnız görüntüleme | ✔ |
  | ik, kalite, operator, ustabasi | — | yalnız görüntüleme veya hiç | ✔ |

- **Test hesapları** (yalnız kabul kopyasında; UAT kullanıcılarının parolası, rolü, erişimi değişmedi):

  | Personel no | Ad | Rol | Vardiya | Makine / müşteri erişimi |
  |---|---|---|---|---|
  | 8101 | Kabul Yönetici | yonetici | — | — |
  | 8201 | Kabul Satış A | satis | — | KBL-YETKILI (kalıcı), KBL-SURELI (süreli, bitiş 2026-11-04) |
  | 8202 | Kabul Satış B | satis | — | KBL-YETKISIZ (kalıcı) |
  | 8301 | Kabul Planlama | planlama | — | — |
  | 8401 | Kabul Ustabaşı | ustabasi | V1 | İE-2026-00002/00003'ün sorumlu ustabaşısı |
  | 8402 | Kabul Üretim Yetkilisi | uretim_yetkilisi | V1 | — |
  | 8403 | Kabul Operatör V1 | operator | V1 | KBL-EXT1 |
  | 8404 | Kabul Operatör V2 | operator | V2 | KBL-EXT1 |
  | 8501 | Kabul Depo | depo | — | — |
  | 8502 | Kabul Ambar Yetkilisi | ambar_yetkilisi | — | — |

  Parola: tüm test hesaplarında aynı rastgele değer, yalnız çalışma dizinindeki `.env` dosyasında
  (`KABUL_TEST_PASSWORD`). Belgeye, Git'e veya loglara yazılmadı. Kendi değerinizi kullanmak için `.env`'deki değeri
  değiştirip çalışma dizininde `python <repo>/kabul/kabul_ortami_hazirla.py --parola-yenile` çalıştırın (yalnız 8xxx
  test hesaplarını günceller).

- **Kurgusal kayıtlar:**

  | Kayıt | Kod (sayfa) | Durum |
  |---|---|---|
  | Müşteriler | KBL-YETKILI (`/musteriler/4/`), KBL-SURELI (`/musteriler/5/`), KBL-YETKISIZ (`/musteriler/6/`) | Kartlar kurgusal bilgiyle tamam |
  | Ürünler / reçete | KBL-FILM-A (`/urun/4/recete/`), KBL-FILM-S, KBL-FILM-B; Rev.1 640 mm / 35 µ, KBL-LDPE %97 + KBL-SLIP %3 | — |
  | Makineler | KBL-EXT1 (operatörler atanmış), KBL-EXT2 | — |
  | Lot | LOT-2026-00005 KBL-LDPE 1000 kg, tedarikçi parti KURGU-IRS-0001 | 100 kg İE-2026-00002'ye verildi |
  | Sipariş — sevk zinciri | SIP-2026-00002 (`/siparisler/2/`), kalem SIP-2026-00002/01, KBL-YETKILI, 10 kutu | Onaylı |
  | Sipariş — üretim | SIP-2026-00003, KBL-YETKILI, 20 kutu | İE-2026-00003'e bağlı |
  | Sipariş — süreli | SIP-2026-00004 (`/siparisler/4/`), KBL-SURELI, 5 kutu | Onaylı |
  | Sipariş — yetkisiz | SIP-2026-00005 (`/siparisler/5/`), KBL-YETKISIZ, 5 kutu, 8202 açtı | Onaylı |
  | İş emri — devir | İE-2026-00003 (`/is-emirleri/3/`), operasyon IE-2026-00003/OP1 (id 4), KBL-EXT1, bugün V1 | Başladı; talimat sürüm 1 (`/talimat/1/`) basıldı; devir yok |
  | İş emri — kapalı | İE-2026-00002 (`/is-emirleri/2/`), KBL-EXT2; verilen 100 kg (lot hareketi 8), bobin BOB-2026-00004 95 kg, fire 5 kg | Kapatıldı, fark 0 |
  | Palet | PLT-2026-00002 (`/paletler/2/`), 40 kutu, KBL-A-01 | Kabul + kalite uygun; 10 kutu SIP-2026-00002/01'e ayrıldı |
  | Sevkiyat | SVK-2026-00001 (`/sevkiyatlar/1/`), satır id 1, 10 kutu | Hazırlandı ve okutuldu; sevk onayı bekliyor |

**Sonrası** denetim: KURAL 0, ENGEL 0; İNCELE 1 (2001'in taşınan erişimleri — işletme kararı, dokunulmadı);
BİLGİ 3 (depo ve uretim_yetkilisi rollerinde eksik `customer.document.view`).

## 4. Uygulama ve QR doğrulaması (konteyner içinden)

`kabul/ortam_dogrula.py` (yalnız GET; senaryo durumunu değiştirmez) **27/27** geçti:

- 10 test hesabının hepsi giriş yapabiliyor.
- 8201 listesinde SIP-2026-00002 ve SIP-2026-00004 var, SIP-2026-00005 yok; `/siparisler/5/` 404. 8202 için tersi.
- Etiket sayfası `/paletler/2/etiket/` 200, "APP_BASE_URL ayarlı değil" uyarısı yok. QR içeriği
  `http://127.0.0.1:8770/q/<palet anahtarı>/` (sayfadaki SVG, bu adresle üretilen SVG ile birebir aynı). Bu adres
  oturum açıkken `/paletler/2/` sayfasına gidiyor.
- Senaryo önkoşulları: 8402 düzeltme formu açılıyor; 8403 talimatı görüyor (KBL-LDPE), 8404 göremiyor (404); 8403
  malzeme çıkışı formunu açabiliyor (devirden önce); 8301 reçeteyi görüyor, 8403 göremiyor (403); 8501'de "Sevk et"
  düğmesi yok, 8502'de var.

`APP_BASE_URL=http://127.0.0.1:8770` yalnız bu konteyner için doğru adrestir; telefon için geçerli değildir (bölüm 6).

## 5. Somut manuel senaryo

Sıra önemlidir (T2 → T5 aynı sevkiyatı kullanır). Adres önekleri `http://127.0.0.1:8770`. Her adımda beklenen
sağlanmazsa **şunları kaydedin:** senaryo ve adım no (ör. T1.4), personel no, tam adres, kayıt kodu, saat, görülen
mesaj veya HTTP durumu (404/403/500), ekran görüntüsü ve varsa beklenmeyen değişiklik (miktar, durum). Hata
sonrası ortamı "düzeltmeye" çalışmayın; bir sonraki senaryoya geçmeden not alın.

### T1 — Satışçı izolasyonu (ilk test, ~5 dk)

| # | Hesap | Sayfa / kayıt | İşlem | Beklenen |
|---|---|---|---|---|
| 1 | 8201 | `/siparisler/` | Listeye bakın. | SIP-2026-00002, -00003 (KBL-YETKILI) ve -00004 (KBL-SURELI) var; SIP-2026-00005 yok. |
| 2 | 8201 | `/siparisler/?q=SIP-2026-00005` | Arama. | "Sipariş yok." (arama kutusunda yazdığınız metin görünür, sonuç görünmez). |
| 3 | 8201 | `/siparisler/5/` ve `/musteriler/6/` | Adresleri doğrudan açın. | İkisi de "Bulunamadı" (404). |
| 4 | 8201 | `/musteriler/5/` | KBL-SURELI kartında erişim bilgisine bakın. | Erişim var; süreli (bitiş 04.11.2026). Bitiş sonrası kendiliğinden düşer (bugün test edilemez). |
| 5 | 8202 | `/siparisler/5/` sonra `/siparisler/2/` | Açın. | 5 açılır; 2 "Bulunamadı". |
| 6 | 8201 | `/siparisler/kalem/2/degistir/` | Formu açın, **göndermeyin**, sekmeyi açık bırakın. | Form açılır. |
| 7 | 8101 | `/musteri-erisimleri/?durum=tumu&satisci=8201&musteri=KBL-YETKILI` | Satırı seçin → İşlem "Erişimi kaldır" → gerekçe → Önizle → uygula. | "1 erişim işlendi." |
| 8 | 8201 | 6. adımdaki açık form | Yeni miktar 9, gerekçe yazıp Kaydet. | "Bulunamadı" (404); `/siparisler/` listesinde SIP-2026-00002/-00003 yok; kalem miktarı 10 kalır. |
| 9 | 8101 | `/musteriler/4/` → Erişim → yeni erişim | 8201'e KBL-YETKILI erişimini kalıcı olarak yeniden verin (T5 için gerekli). | 8201 yeniden SIP-2026-00002'yi görür. |

### T2 — Depo yetkileri (~4 dk)

| # | Hesap | Sayfa / kayıt | İşlem | Beklenen |
|---|---|---|---|---|
| 1 | 8501 | `/sevkiyatlar/1/` (SVK-2026-00001) | Sayfaya bakın. | "Sevk et" yok; "Son sevk onayını ambar yetkilisi verir." yazar; "Araç çıktı" ve "Sevki geri al" yok. |
| 2 | 8502 | `/sevkiyatlar/1/` | "Sevk et". | "SVK-2026-00001 sevk edildi; stoktan düşüldü."; PLT-2026-00002 30 kutu. |
| 3 | 8501 | `/sevkiyatlar/1/` | Satırda "İade al" arayın. | Görünmez (araç çıkışı yapılmadı; iade yalnız çıkıştan sonra). |
| 4 | 8501 | `/sevkiyatlar/1/` | "Araç çıktı" düğmesini arayın. | Yok. |
| 5 | 8502 | `/sevkiyatlar/1/` | "Araç çıktı". | Durum "Çıkış yapıldı". |

### T3 — Vardiya devri sonrası reçete erişimi (~6 dk)

| # | Hesap | Sayfa / kayıt | İşlem | Beklenen |
|---|---|---|---|---|
| 1 | 8403 (V1) | `/talimat/1/`, `/is-emirleri/3/` | Açın. | Talimatta KBL-LDPE %97, KBL-SLIP %3, 640 mm / 35 µ. İş emri başlığında ölçüler görünür. |
| 2 | 8404 (V2) | `/talimat/1/` | Açın. | "Bulunamadı" (404) — sorumluluk henüz V1'de. |
| 3 | 8401 | `/operasyon/4/devir/` | Devralan vardiya "2. vardiya", kalan iş "Kalan 20 kutu (kurgusal)" → kaydet. | "Vardiya devri kaydedildi." |
| 4 | 8404 | `/is-emirleri/3/#devirler` | "Teslim al". | "Devir teslim alındı."; `/talimat/1/` artık açılır. |
| 5 | 8403 | `/talimat/1/`, `/talimat/1/?yazdir=1`, `/is-emirleri/3/malzeme-cikis/`, `/operasyon/4/bobin/`, `/is-emirleri/3/kapat/`, `/urun/4/recete/` | Her adresi açın. | Talimat ve yazdırma 404; malzeme çıkışı, bobin, kapanış 403; reçete 403. |
| 6 | 8403 | `/is-emirleri/3/` | Açın. | Sayfa açılır; başlıkta yalnız "Rev.1", ölçü ve karışım yok; işlem geçmişi ve devir kaydı görünür. |
| 7 | 8301 ve 8101 | `/talimat/1/`, `/urun/4/recete/` | Açın. | İkisi de karışımı görür. |

Not: 8403'ün daha önce bastığı veya ekran görüntüsünü aldığı talimat geri alınamaz; sistem yalnız sonraki erişimi kapatır.

### T4 — Düzeltme talep / onayı (~5 dk)

| # | Hesap | Sayfa / kayıt | İşlem | Beklenen |
|---|---|---|---|---|
| 1 | 8402 | `/duzeltmeler/yeni/malzeme_cikis/8/` (İE-2026-00002, lot hareketi 8, 100 kg) | Yeni değer 110, gerekçe "Fişte 110 kg yazıyor (kurgusal)". | Talep "onay bekliyor". |
| 2 | 8402 | Talep sayfası (`/duzeltmeler/` listesinden) | "Onayla"yı deneyin. | Kendi talebini onaylayamaz (düğme yok veya ret mesajı). |
| 3 | 8502 | Aynı talep | Onaylayın. | "Uygulandı". |
| 4 | 8402 | `/is-emirleri/2/` | Kapanış bölümüne bakın. | "İlk kapanış": verilen 100, fark 0 (değişmedi). "Düzeltmeler sonrası güncel": verilen 110, fark 10 kg; sınır 3,3 kg (110 × %3) aşıldığı için "yeniden değerlendirme bekliyor". |
| 5 | 8402 | `/is-emirleri/2/` | "Güncel dengeyi değerlendir" → gerekçe yazıp kaydedin. | Uyarı kalkar; ilk kapanış yine 100/0. |
| 6 | 8501 | `/duzeltmeler/` | Talep açmayı deneyin. | Depo personelinin talep hakkı yok (karar bekleyen konu; davranış değiştirilmedi). |

### T5 — İade ve yerine gönderim zinciri (~8 dk; T2 sonrası)

| # | Hesap | Sayfa / kayıt | İşlem | Beklenen |
|---|---|---|---|---|
| 1 | 8501 | `/sevkiyatlar/1/` → satırda "İade al" (`/sevkiyat-satiri/1/iade/`) | 2 kutu, neden "2 kutu ezik (kurgusal)". 11 kutu denemesi önce. | 11 reddedilir ("en fazla 10"); 2 kabul: yeni iade kodu (IAD-…) ve yeni iade paleti "kontrol bekliyor". |
| 2 | 8201 | `/siparisler/2/` | İade satırında "Yerine gönderim oluştur": 2 kutu. | Yeni kalem SIP-2026-00002/02 "Yerine gönderim · IAD-…"; "Yeni satış değildir". |
| 3 | 8201 | Aynı iade | Tekrar 1 kutu yerine gönderim deneyin. | Reddedilir ("en fazla 0"). |
| 4 | 8201 | SIP-2026-00002/02 kalem değişikliği | Miktarı 3 yapmayı deneyin. | Reddedilir ("en fazla 2"). |
| 5 | 8201 | `/siparisler/2/` → "Tekrar sipariş (kopya)" | Kopyayı açın. | Yalnız KBL-FILM-A 10 kutu (yerine gönderim kopyalanmaz); kopya taslak kalır. |
| 6 | 8501 | PLT-2026-00002 | /02 kalemi için 2 kutu ayırın; SIP-2026-00002 için yeni sevkiyat, hazırlayın, paleti okutun. | Hazırlandı. İade paleti (kontrol bekliyor) ayırmada/sevkte kullanılamaz. |
| 7 | 8502 | Yeni sevkiyat | "Sevk et", "Araç çıktı". | Tamam. |
| 8 | 8201 | `/siparisler/2/` | Kalem kartına bakın. | İlk sevk 10, iade 2, yerine gönderim 2 sevk, **müşteride net 10**; asıl kalem miktarı 10. İade paleti hâlâ "kontrol bekliyor" (kendiliğinden açılmadı). |

### T6 — Telefonla QR açma ve etiket yazdırma (ayrı manuel adım)

**Bu ortamda yapılamaz** (bölüm başındaki erişim sınırı). Yapılacak yer: fabrika yerel ağındaki bir bilgisayar;
internete açık yayın ve port yönlendirme yok.

Kurulum (o bilgisayarda): 63a5105'i ayrı dizine alın; `mimcor_uat`'tan yeni bir kabul kopyası oluşturup migrate edin ve
`kabul/kabul_ortami_hazirla.py`'yi çalıştırın (veya bu kopyanın `pg_dump` yedeğini yükleyin); `.env`:
`APP_BASE_URL=http://<bilgisayarın yerel IP'si>:8000`, `DJANGO_ALLOWED_HOSTS=<IP>,127.0.0.1`,
`DJANGO_CSRF_TRUSTED_ORIGINS=http://<IP>:8000`, `DJANGO_DEBUG=1` (HTTP olduğu için); başlatma
`python manage.py runserver 0.0.0.0:8000 --noreload` — yalnız yerel ağ, güvenlik duvarında yalnız bu ağa açık.

| # | Hesap / cihaz | İşlem | Beklenen | Kaydedilecek |
|---|---|---|---|---|
| 1 | 8501, bilgisayar | `/paletler/2/etiket/` → "Yazdır" → etiket yazıcısında basın. | Basım kaydı oluşur; ikinci basım "yeniden basım" olarak kaydedilir; etikette ürün, palet kodu, QR okunaklı. | Yazıcı modeli, etiket boyutu, baskı kalitesi fotoğrafı. |
| 2 | Telefon (oturum kapalı) | Etiketteki QR'ı kamera ile okutun. | `http://<IP>:8000/q/…/` açılır; giriş sayfası gelir; 8501 ile girişten sonra PLT-2026-00002 sayfası. Stok hareketi oluşmaz. | Telefon modeli/işletim sistemi/tarayıcı, okutma mesafesi, açılan adres. |
| 3 | Telefon, 8403 | Aynı QR. | 8403'te stok görme yetkisi yok: erişim reddi. | Görülen mesaj. |
| 4 | Telefon, 8501 | `/sevkiyatlar/<yeni>/okut/` ekranında QR ile palet okutma. | Doğru palet kabul, başka sipariş paleti ret. | Sonuç ve mesaj. |

## 6. Kalan sınırlar

- Uygulama yalnız konteyner içinden erişilebilir; kullanıcı tarayıcısı ve telefon erişimi yok (T6 ayrı kurulum ister).
- Gerçek telefon ve etiket yazıcısı testi yapılmadı.
- Konteyner yeniden başlatılırsa PostgreSQL ve sunucu yeniden başlatılmalı (bölüm 1); oturum kapatılırsa ortam
  kaybolur — gerekirse `pg_dump mimcor_kabul_kopya` alınmalı.
- Otomatik doğrulamaların geçmesi işletme onayı değildir; kabul kararı manuel senaryo sonuçlarına göre verilir.
