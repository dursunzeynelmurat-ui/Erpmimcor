# Demo öncesi son işlevsel düzeltmeler — 30 Ekim 2026

Branch `fix/mimcor-demo-son-duzeltmeler`, taban `c316506` (`feature/mimcor-teknik-numune-repete`; uzakta yeni commit
yoktu). Merge, PR, force push veya canlı yayın yapılmadı. Kapsamlı UI/UX değişikliği yok (Astra ile ayrıca yürütülecek).
`design/mimcor-demo-2026-10-30` dalı `ee86192`'den ayrılmış eski bir hattır, `c316506`'yı içermez; dokunulmadı.

Gerçek `mimcor`, `mimcor_uat` ve kabul veritabanları (`mimcor_kabul_kopya`, `mimcor_kabul_tnr_kopya`) değiştirilmedi.
Geliştirme `mimcor_demo_dev`, testler Django'nun geçici test veritabanlarında, yükseltme denemeleri yeni kopyalarda
(`demo_yukseltme_uat_kopya`, `demo_yukseltme_tnr_kopya`; denemeden sonra silindi).

Her bulgu için önce mevcut davranışı yakalayan regresyon testi PostgreSQL'de **başarısız** çalıştırıldı (çıktı
notları aşağıda), sonra düzeltildi; her bulgu ayrı commit.

## Bulgular

### 1. Satış ekranında iç proses notu (`ef08d3e`)
- **Önceki:** `spec.card()` `process_notes`'u her kartta üretiyordu; satışın sipariş ayrıntısında ve yeni sipariş
  2. adımında not HTML'de okunuyordu (test: 2 başarısız).
- **Düzeltme:** `catalog/spec.py` `INTERNAL = {"process_notes"}`; `card(..., internal=False)` iç alanı hiç üretmez.
  `spec_card` etiketi bunu `recipe.view` yetkisine bağlar. Karışım ve uygulama notu satış yanıtlarında zaten yoktu;
  testle sabitlendi. Planlama/yönetici ve üretim talimatı görmeye devam eder. JSON uçları (`customer_products`) yalnız
  id/etiket döndürür.
- **Test:** `orders/tests/test_internal_notes.py` (sipariş ayrıntısı onaysız/onaylı, yeni sipariş 2. adım, repete,
  planlama görür).
- **Kalan sınır:** İşlem geçmişi ekranlarında alan etiketleri genel kalır; reçete geçmişi zaten yalnız `recipe.view`.

### 2. Repete revizyonunun yanlış seçilmesi (`21ff76c`)
- **Önceki:** 650 mm revizyonlu repete kararı sonrası ürüne 800 mm eklenince onay formu varsayılan olarak en yeni
  800 mm'yi seçiyor, servis kabul ediyordu (test: varsayılan pk 15 ≠ 14, servis hata vermedi).
- **Düzeltme:** `OrderLine.expected_revision` (repete anında kaynağın onaylı revizyonu; veri migration'ı mevcut repete
  kalemlerini kaynağın o anki onaylı revizyonuyla doldurur). `repeat.expected_revision()`: karara bağlanan isteğin
  sonuç revizyonu → reddedilmişte temel revizyon → sabitlenen kaynak revizyonu. `approve_line(deviation_reason=)`:
  beklenen dışı revizyon yalnız gerekçeyle (sunucu; eski form/doğrudan POST dahil), sapma işlem geçmişine yazılır.
  Form: varsayılan = beklenen, "farkları kontrol ettim" + gerekçe. Ekran: her revizyonun kartı ve beklenene göre fark
  tablosu; gösterilen kart seçimle aynı (JS anında, JS'siz `?rev=`). Onaylı eski kalemler ve başlamış iş emirleri
  değişmez (aktif iş emri kontrolü korunur).
- **Test:** `orders/tests/test_expected_revision.py` (650/800, doğrudan POST, gerekçeli kabul + audit, normal
  repetede kaynak sonradan değişse de beklenen sabit, ret sonrası temel revizyon).
- **Kalan sınır:** Migration öncesi açılmış repete kalemlerinde "repete anındaki" değer kaydedilmemişti; en iyi bilgi
  olarak kaynağın migration anındaki onaylı revizyonu yazıldı.

### 3. Numune gönderiminde stok ayırmanın atlanması (`e0203e6`)
- **Önceki:** 40 kutuluk palete bağlı 2 kutu firma içi numune stoktan ayrılmadan "gönderildi" oluyor, palet 40 kalıyordu
  (geliştirme DB'sinde geri alınan işlemle yeniden üretildi).
- **Düzeltme:** `Sample.stock_source` — firma stoğundan tam kutu / stok dışı / daha önce başka işlemle stoktan çıktı
  (boş: müşteri referansı veya eski kayıt). Formda firma içi numunede seçim zorunlu; servis varsayılanı tam kutuda
  "stoktan" (güvenli). `send()`/`destroy()`: stoktan gelende hareket yoksa veya kaynak belirtilmemişse anlaşılır
  mesajla red + "Stoktan ayır" yönlendirmesi. Önceden çıkmış kutular `link_prior_movement()` ile mevcut çıkış
  hareketine bağlanır (aynı ürün, çıkış yönlü, kutu sayısı eşit, bir hareket tek numuneye) — ikinci düşüm yok.
  Müşteri referansı stoğa dokunmaz. Gönderim/geri dönüş/imha ikinci hareket yaratmaz. Adet/rulo/kg numunede "stoktan"
  reddedilir, stok etkisi "desteklenmiyor" diye yazılır (dönüşüm yok). Müşteriye gönderim kalite blokeli palet veya
  ilişkili blokeli bobinde reddedilir; ayırma rezervasyon dışı serbest kutudan (mevcut kural).
- **Test:** `samples/tests/test_sample_stock_source.py` (40 kutu senaryosu, varsayılan, stok dışı, kısmi birim,
  referans, önceki hareket bağlama, blokeli palet, çift gönderim, eski kayıt, form + ayrıntı uyarısı,
  `TransactionTestCase` ile ayırma × gönderim yarışı).
- **Kalan sınır:** Önceki çıkış bağlama yalnız hasar ayırma, sayım/düzeltme ve numune ayırma hareketlerini kabul eder;
  bunların "numune için" yapıldığını sistem bilemez, kullanıcı beyanıdır (işlem geçmişine yazılır).

### 4. Numunenin yanlış teknik revizyona bağlanması (`93ee610`)
- **Önceki:** 30 µ revizyonla üretilmiş palete bağlı numune, ürüne 70 µ revizyon eklenince güncel 70 µ'yu alıyordu
  (test: Rev.2 ≠ Rev.1).
- **Düzeltme:** Revizyon önceliği bobin/palet üretim revizyonu → iş emri → sipariş kalemi → kullanıcı seçimi; ürünün
  güncel revizyonu yalnız hiçbir fiziksel/üretim bağlantısı yoksa ve dayanağı yazılarak. `pallet_sources()`: palet ve
  aktarım/hasar ayırmayla kutu aldığı paletlerin iş emirleri/bobinleri. Birden fazla revizyon veya kayıtsız köken:
  tahmin yok — revizyon seçimi ya da "Revizyon kesin bilinmiyor". Çelişkiler reddedilir (bobin palette değil, palet iş
  emrinden gelmiyor, iş emri başka kaleme ait, açık revizyon fiziksel revizyonla çelişiyor); başka paletten ayırmada
  köken yeniden kontrol edilir. `Sample.revision_basis` ayrıntıda gösterilir; eski kayıtlar "belirtilmemiş".
- **Test:** `samples/tests/test_sample_revision.py`.
- **Kalan sınır:** Karışık palet ancak teknik imzası aynı revizyonlarla oluşabilir (aktarım kuralı); bu durumda
  seçim/belirsizlik zorunludur.

### 5. Üretim çıktısında eksik talimatlar (`ba16fdf`)
- **Önceki:** Talimatta bileşen uygulama notu ve ürüne özel ambalaj notu yoktu (test: KeyError `not`, `urun_ambalaj`).
- **Düzeltme:** Extruder karışım satırında uygulama notu (yalnız doluysa). Son operasyonda "Ürüne özel ambalaj /
  etiket notu" ile "Müşteri genel paketleme/etiket talimatı" ayrı başlık (basılı talimat ve iş emri ayrıntısı).
  Matbaa ve diğer operasyonlara karışım girmez. Anlık görüntü/sürüm mantığı korunur: eski basılı talimat değişmez,
  içerik değişince yeni sürüm; notsuz işlerde içerik özeti değişmediği için gereksiz yeni sürüm açılmaz.
- **Test:** `production/tests/test_instruction_notes.py` (servis, HTML talimat, iş emri ayrıntısı, sürüm).
- **Kalan sınır:** Fiziksel yazıcı çıktısı denenmedi (yalnız HTML ve tarayıcı yazdırma görünümü).

### 6. Numune formu hatasında bilgi kaybı (`3bad787`)
- **Önceki:** Her işlem formu hatada yönlendiriyor, girilenler ve alan hataları kayboluyordu (8/8 test: 302).
- **Düzeltme:** Hata durumunda ayrıntı sayfası bağlı formla 400 ile yeniden çizilir: aynı alan kimlikleri, girilen
  değerler, alan yanında hata, hata özetine odak (mevcut JS), ilgili `<details>` açık. Diğer formlar yeni işlem
  anahtarı alır; hatalı form kendi anahtarını korur. İmha da form bileşeniyle. Ek yüklemede "Dosyayı yeniden seçin"
  uyarısı (tarayıcı güvenliği; dosya korunmuş gibi gösterilmez).
- **Test:** `samples/tests/test_sample_form_errors.py` (konum, stok, gönderim alan/kural hatası, imha, ölçüm, karar, ek).

### 7. Normal yeni siparişte teknik talep (`42068fc`, `550bc9c`)
- **Önceki:** Sihirbaz özellikleri salt okunur gösterip "kaydettikten sonra planlamaya değişiklik isteği iletin"
  diyordu; normal sipariş için böyle bir yol yoktu.
- **Düzeltme:** `repeat.request_line_change()` mevcut `LineChangeRequest` modelini kullanır. Sihirbaz 2. adımı:
  mevcut özellikler otomatik dolu "Farklı teknik özellik istiyorum" bölümü (ölçü, mikron, körük, renk, baskı,
  paketleme; karışım ve iç not yok), gerekçe zorunlu, mevcutla aynıysa red; 3. adımda mevcut/istenen tablosu. Sipariş
  ayrıntısında taslak/planlama bekleyen kalem için "Farklı teknik özellik iste". Reçetesi olmayan üründe talep temel
  revizyonsuz saklanır; planlama ilk reçete taslağını bundan açar; üretime onaylı sayılmaz. Planlama mevcut ekranda
  yan yana görür ve yeni revizyon / yeni ürün kodu / ret kararını verir. Açık talep varken teknik onay, iş emri açma ve
  serbest bırakma reddedilir. İptal, onaylı veya iş emri olan kalemde bu yol kapalı. Yalnız gönderilen alanlar talep
  sayılır. Tarayıcı provasında bulunan "2. adımda alanlar boş" hatası `550bc9c` ile düzeltildi.
- **Test:** `orders/tests/test_line_change_request.py`.
- **Kalan sınır:** Onaylanmış kalemde teknik değişiklik için planlamayla görüşülür (otomatik onay geri alma yok).

## Doğrulama
| Kontrol | Sonuç |
|---|---|
| Tam Django takımı (PostgreSQL, `--parallel 1`) | 426 test, OK (taban `c316506`: 378, OK) |
| `makemigrations --check` | Değişiklik yok; yalnız eklemeli migration: `orders.0010`, `samples.0002`, `samples.0003` |
| Mevcut tarayıcı modülleri (`browser_checks`, `order_product_filter_checks`, `field_scenarios_checks`, `teknik_numune_checks`) | 8 test, OK |
| Yeni `ui_checks/demo_son_checks.py` (1440 / 390 emülasyon) | 4 test, OK; CI tarayıcı işine eklendi |
| Temiz kurulum: migrate + `seed_demo` ×2 + `migrate --check` | OK |
| Yükseltme: `mimcor_uat` kopyası (32 migration) ve `mimcor_kabul_tnr_kopya` kopyası (3 migration) | OK; repete kalemlerinde beklenen revizyon dolduruldu, eski numuneler "belirtilmemiş" |
| GitHub Actions | Son commit sonucu teslim mesajında |

Ekran görüntüleri: `docs/screenshots/demo-son-duzeltmeler/` (`dsd-1…9`, 1440 ve 390).

## Demo senaryoları (çalışan, tarayıcıyla prova edildi)
1. **Sipariş → teknik talep → … → sevkiyat** (2001 satış, 3001 planlama, 4001 ustabaşı, 5001 depo, 5002 ambar):
   ORN-KB-045 için yeni sipariş, 2. adımda en 400 → 420 mm talebi + gerekçe; 3. adımda fark tablosu; kaydet ve onayla.
   Planlama: teknik onay "karara bağlanmadan" engeli → değişiklik isteği → "Aynı ürün koduna yeni teknik revizyon" →
   taslak (420 mm, karışım aynı) → onay. Teknik onay varsayılanı yeni revizyon (eskisini seçince kart ve farklar
   değişir). En değiştiği için baskı görseli onayı yenilenir (planlama iç teknik, satış müşteri onayı). İş emri
   EXT-02 + MAT-01 → serbest bırak (görsel sabitlenir) → başlat → bobin → bobin → palet 10 kutu → depo kabulü →
   kalite uygun → siparişe ayır → sevkiyat → son sevk onayı (ambar).
2. **Repete:** Kaynak Rev.1 ile onaylı ORN-PF-030; tekrar sipariş; teknik onayda varsayılan Rev.1, Rev.2 seçilince
   gerekçesiz onay reddedilir (girilen not korunur), gerekçeyle onaylanır.
3. **Numune:** DEN-PF-030, PLT-2026-00001, 2 kutu "firma stoğundan"; stoktan ayırmadan gönderim engeli (bölüm açık,
   değer korunur) → stoktan ayır → gönder; palet 38, tek hareket; numune paletin üretim revizyonunda.

## Sunum ortamı erişimi — açık demo engeli
Bu çalışma bulut oturum konteynerinde yapıldı. Uygulama yalnız konteyner içindeki `127.0.0.1` adresinde
çalıştırıldı; bu adres kullanıcının bilgisayarından veya telefondan **erişilebilir bir demo adresi değildir**.
Erişilebilir bir sunum kurulumu yok: bu **açık bir demo engelidir**. Tünel veya internete açık yayın oluşturulmadı.
Yerel kurulum için 63a5105 kabul yedek paketindeki `KURULUM-WINDOWS.md` yolu bu sürüme uyarlanmalıdır (kod
`fix/mimcor-demo-son-duzeltmeler`, temiz kurulum + `seed_demo`). **Gerçek telefon ve fiziksel yazıcı denenmedi**;
390 px yalnız Chromium emülasyonudur. Gerçek kullanıcı gözlemi yapılmadı; testlerin geçmesi işletme onayı değildir.

## Astra için UI devri
**Değişen ekranlar**
- Yeni sipariş sihirbazı 2. adım ("Farklı teknik özellik istiyorum" bölümü) ve 3. adım (talep fark tablosu).
- Kalem kartı: "Farklı teknik özellik iste" bağlantısı; yeni sayfa `/siparisler/kalem/<id>/teknik-talep/`.
- Teknik onay `/planlama/kalem/<id>/onay/`: beklenen revizyon satırı, revizyon başına kart + fark tablosu, sapma
  onay kutusu ve gerekçesi.
- Numune oluştur: teknik revizyon seçimi, "Revizyon kesin bilinmiyor", stok kaynağı. Numune ayrıntısı: revizyon
  dayanağı, stok kaynağı, stok etkisi metni, eksik stok uyarısı (`#stok-eksik`), "Stok kaynağını belirt / düzelt",
  "Önceki stok çıkışını bağla", imha formu; hatalı form yeniden gösterimi.
- Basılı makine talimatı ve iş emri ayrıntısı: uygulama notu, ürüne özel / müşteri genel paketleme başlıkları.

**Korunması gereken alanlar ve davranışlar**
- Form alan adları: `want_change`, `change_reason`, `s_<teknik alan>`; `revision`, `deviation_confirmed`,
  `deviation_reason`; `stock_source`, `revision_uncertain`; `movement`. Numune işlem formlarının alan kimliği önekleri
  (`id_move_`, `id_stock_`, `id_send_`, `id_dest_`…) ve `details[data-action]` + `open`; `data-revision-card`,
  `form[data-revision-select]`, `details[data-spec-request]`.
- Her formun kendi `idem_key`'i; hatalı form gönderdiği anahtarı korur.
- 2. adımda gizli taşınan alanlar (`field.as_hidden`) ve "1. adımdan gelişte mevcut değerle doldurma" davranışı.

**Yetki sınırları (sunucuda; arayüz gizlemesi yeterli değil)**
- Proses notu ve karışım yalnız `recipe.view` (planlama/yönetici) ve ilgili makine talimatında; satış yanıtında hiç
  üretilmez.
- Teknik talep: `order.create` + müşteri kapsamı (başka müşteri 404); karar `planning.approve`.
- Sapmalı teknik onay: `planning.approve` + gerekçe. Numune stok işlemleri: `sample.stock`; kaynak belirtme
  `sample.manage`.

## Karar bekleyen / desteklenmeyen
Önceki turun karar konuları değişmedi (klişe kazan aralığı terimi, körüklüde en ölçümü, baskıda müşteri onayı şartı
— varsayılan gerekir, geri dönen numunenin stoğa alınması — desteklenmiyor). Yeni: adet/rulo/kg numunede stok
düşümü desteklenmez (birim dönüşümü yok); onaylanmış kalemde teknik değişiklik bu yoldan açılmaz.
