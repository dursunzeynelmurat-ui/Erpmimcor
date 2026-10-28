# Teknik sipariş, reçete, baskı, numune ve repete — 28 Ekim 2026

Branch `feature/mimcor-teknik-numune-repete`, taban `63a5105` (uzak `fix/mimcor-kabul-denetimi` başı; daha yeni
uygulama değişikliği yoktu; `kabul/mimcor-ortam-63a5105` yalnız belge branch'idir ve taban alınmadı). Merge, PR, force
push veya canlı yayın yapılmadı. `mimcor`, `mimcor_uat` ve `mimcor_kabul_kopya` veritabanları değiştirilmedi; geliştirme
`mimcor_tnr_dev`, temiz kurulum `mimcor_tnr_temiz`, yükseltme denetimi `tnr_yukseltme_*_kopya` veritabanlarında yapıldı.

> **Önemli:** Testlerin geçmesi işletme onayı değildir. Kullanılabilirlik hakkında **gerçek kullanıcı gözlemi
> yapılmadı**; ekranlar 1440 px ve 390 px Chromium **emülasyonuyla** doğrulandı, gerçek telefonla test yapılmadı.
> Bölüm 7'deki konular işletme kararı bekler; kod onları onaylanmış saymaz, varsayılan davranışı açıkça yazar.

## 1. Tek doğruluk kaynağı

Teknik özellikler yeni bir modelde değil, mevcut değişmez **teknik revizyonda** (`RecipeRevision`) tutulur. Alan
listesi, birimler, açıklamalar ve görünürlük koşulları tek yerde tanımlıdır (`catalog/spec.py`); sipariş formu, özet
kartı, teknik imza (sevk uyumluluğu) ve repete değişiklik isteği aynı tanımı kullanır. Sipariş kalemi onaylandığı
revizyona bağlı kalır (`approved_revision`), iş emri kendi revizyonuna (`recipe_revision`) ve sabitlendiği baskı görseli
sürümüne (`artwork`). Mevcut alanlar yeniden modellenmedi: `width_mm` (en), `thickness_micron` (hedef kalınlık),
`length_m` (rulo uzunluğu), `color` (renk adı), `print_spec` / `perforation_spec` (serbest not olarak), bileşenler.

## 2. Değişen ekranlar

| Ekran | Kim | Ne değişti |
|---|---|---|
| Yeni satış siparişi (`/siparisler/yeni/`) | Satış | 3 adım: 1) Müşteri ve ürün, 2) Özellikler ve miktar (onaylı teknik özellik kartı otomatik gelir), 3) Teslimat ve kontrol (okunur özet, bölüm başına "Düzenle", müşteri kartından teslimat bilgisi ve doğrulama kutusu). Hatada girilenler korunur; başarıda kod + sıradaki adım. |
| Sipariş ayrıntısı | Satış, planlama | Kalem kartında "Teknik özellikleri göster" (onaylı revizyon; onay yoksa ürünün güncel revizyonu, açıkça belirtilerek); amaç/yöntem/ücret; repete kaynağı; değişiklik isteği durumu. "Tekrar sipariş (repete)" sihirbaza gider. |
| Teknik onay (planlama) | Planlama | Teknik özellik kartı + karışım; repete kaynağı ve uyarılar; bekleyen değişiklik isteği varsa onay verilemez. |
| Reçete ve reçete taslağı | Planlama, yönetici | Taslak düzenleyici: "+ Malzeme ekle", malzeme kartı seçimi (kimliksiz serbest metin yok), tür, oran, uygulama notu, canlı toplam, parti kg hesabı, yuvarlama farkı, teknik özellik bölümleri (baskı/körük/perfore seçimine göre açılır), "Taslağı kaydet" / "Onayla ve revizyon oluştur" / "Taslaktan vazgeç". Mobilde satırlar kart. |
| Baskı görselleri (`/urun/<id>/baski-gorselleri/`) | Satış (yükleme, müşteri onayı), planlama (iç teknik onay) | Sürüm listesi, durum (taslak / iç onaylı – müşteri bekliyor / geçerli / baskı özellikleri değiştiği için geçersiz), önizleme (PNG/JPG), yetkili indirme, onay ve geri alma. |
| Matbaa talimatı | Matbaa operatörü (atanmış makine+vardiya), planlama | Klişe, kazan çevresi, tekrar boyu, boşluk, renkler, yüz, yön, en, ürün boyu, görsel sürümü ve küçük önizleme; karışım yok. |
| Tekrar sipariş (`/siparisler/tekrar/`) | Satış | Müşteri → eski kalemler (kaynak sipariş, ürün kodu, revizyon, uyarılar) → yeni miktar/termin, isteğe bağlı teknik değişiklik + gerekçe → teslimat ve kontrol. |
| Değişiklik isteği (`/planlama/degisiklik/<id>/`) | Planlama | Eski/yeni değerler yan yana; "Aynı ürün koduna yeni teknik revizyon", "Yeni ürün kodu aç", "Reddet". |
| Numuneler (`/numuneler/`) | Kalite, depo, ambar, planlama, üretim; satış kısıtlı | Liste/süzgeç, yeni numune, ayrıntı (durum üçlüsü, bilgiler, konum, stok etkisi, işlemler, ölçüm, karar, ekler, hareket geçmişi), QR etiketi, kontrol kriterleri. |

## 3. İş kuralları

**Teknik özellikler (Aşama 1).** Seçimli alanlarda boş = **Belirtilmemiş**; "Yok" ayrı ve açık seçimdir. Eski
revizyonlarda yeni alanlar boş kalır (0 veya "yok" yazılmadı). Ürün/torba boyu (mm), rulo uzunluğu (m) ve perfore
aralığı (mm) ayrı alanlardır. Körüklüde "En ölçüm tanımı" (kapalı en / açılmış toplam en) ve körük derinliği revizyon
onayı için zorunludur; sistem ikisi arasında dönüşüm yapmaz. Ekran önizleme rengi fiziksel renk onayı değildir.
"Baskı yok", "Körük yok", "Perfore yok" seçildiğinde ilgisiz alanlar ekranda gizlenir **ve sunucuda kaydedilmez**.
Ondalık girişte "12,5" ve "12.5" kabul edilir; "1.250" gibi binlik/ondalık belirsiz yazım ve hem nokta hem virgül
reddedilir — 12,5 asla 125 olmaz. Kutu biriminde ondalık miktar 2. adımda alan yanında reddedilir. Başka müşterinin
ürünü listede yoktur, sunucu da reddeder. Sipariş değişikliği ürün kartını veya eski siparişleri değiştirmez; teknik
değişiklik yalnız planlamanın yeni revizyonu / yeni ürün koduyla olur. Teknik imza yeni alanları kapsar (eski
revizyonlar arasında fark üretmez); farklı özellikteki palet ancak eşdeğerlik onayıyla ayrılabilir.

**Reçete (Aşama 2).** Oranlar **nihai karışımın yüzdesidir** (toplam %100). Kod daha önce de bu yöntemi kullanıyordu;
"100 kg ana malzemeye ilave katkı" yöntemi kullanılmadığından eski reçeteler yeniden yorumlanmadı. Eksik tarif taslak
olarak kaydedilir; toplam tam %100,000 değilse onay verilmez ("Karışım toplamı %97. Onaylamak için %100 olmalı.").
Negatif / %100 üstü oran, 3'ten fazla ondalık, aynı malzemenin iki kez kullanımı, pasif malzeme ve kg dışı birimli
malzeme (litre/adet) reddedilir. kg hesabı Decimal; 0,001 kg'ye ROUND_HALF_UP; yuvarlanmış toplam parti büyüklüğünden
farklıysa fark ayrıca yazılır (bir satıra gizlice eklenmez). Renklendirici yeni malzeme türüdür; siparişteki istenen renk
(Görünüm) ile renklendirici malzemesi/dozajı ayrıdır. Planlanan oranlar iş emrindeki gerçek malzeme çıkışlarından
ayrıdır. Onay her zaman yeni, değişmez revizyon oluşturur; başlamış iş emirleri ve onaylı kalemler eski revizyonu korur.
Ürün başına tek açık taslak; eş zamanlı iki onay tek revizyon üretir. Kullanılmış malzeme kartları silinmez, pasife
alınır (mevcut malzeme yönetimi).

**Baskı (Aşama 3).** Kazan çevresi, baskı tekrar boyu ve iki baskı arası boşluk ayrı mm alanlarıdır, birbirine
eşitlenmez; bilinmeyenler zorunlu değildir. Görsel her yüklemede yeni sürüm olur (yükleyen, tarih, dosya, not). İç
teknik onay (planlama) ile müşteri onayı (kişi, tarih, kanıt/referans) ayrı kayıttır; satışın müşteri onayı kaydetmesi
üretim onayı vermez. Onay, onaylandığı andaki **baskı imzasına** (en, ürün boyu ve bütün baskı alanları) bağlıdır; bu
alanlardan biri değişen revizyonda onaylar geçersiz sayılır ve yeniden gerekir (proses notu gibi alanlar etkilemez).
Baskılı iş emri serbest bırakılırken o anki geçerli sürüme sabitlenir; sonraki yükleme ve onaylar eski iş emrini ve
talimatını değiştirmez. Dosyalar PDF/PNG/JPG (imza ve 10 MB kontrolü), herkese açık MEDIA adresinden yayınlanmaz;
indirme yetkili görünümle, `nosniff` ve `sandbox` başlıklarıyla. Matbaa operatörü yalnız kendi açık iş emrine sabitlenmiş
sürümü görür.

**Repete (Aşama 4).** Sipariş **amacı** (ticari / numune / deneme), **oluşturma yöntemi** (yeni / repete / revizyonlu
repete) ve **ücret** (ücretli / ücretsiz) ayrı alanlardır; muhasebe/fatura oluşturulmaz; numune ve deneme siparişleri de
normal üretim, stok ve izlenebilirlik kurallarından geçer. Repete yalnız yetkili müşterinin normal kalemlerinden yapılır
(yerine gönderim kalemi listelenmez). Yeni kalem kaynak kaleme bağlanır (`source_line`); miktar ve termin yeniden
girilir; teslimat bilgisi bugünkü müşteri kartından alınır ve doğrulatılır (sistemde fiyat alanı yoktur). Üretim,
rezervasyon, sevkiyat, onay ve düzeltme kayıtları kopyalanmaz; revizyon otomatik değişmez; kalem yine planlamanın teknik
onayından geçer. Pasif ürün tekrar edilemez; kaynak revizyon güncel değilse veya kaynak onaysızsa uyarı gösterilir.
Revizyonlu repetede satış yalnız izinli teknik alanları ve gerekçeyi kaydeder (karışım değiştirilemez); planlama yan
yana görür ve yeni revizyon taslağı (temel karışım korunur) veya **aynı müşteriye** yeni ürün kodu açar ya da reddeder;
karar verilmeden kalem teknik onayı verilemez. Önceki siparişler ve başlamış işler etkilenmez. Çok kalemli siparişte
hangi kalemlerin tekrarlanacağı tek tek seçilir; kaynak ilişkisi kalem düzeyindedir.

**Numune (Aşama 5).** Türler: müşteriden gelen referans, üretim kontrol, saklama/şahit, müşteriye gönderilen.
Fiziksel durum, iç kalite kararı ve müşteri geri bildirimi ayrı alanlardır. Stok: yalnız firma içi numunede paletin
**serbest** (rezervasyon dışı) kutularından tam kutu, bir kez (numune birimi kutu ve miktarı aynı olmalı) — tek palet
hareketi "Numune için ayırma"; ayrılan kutular sevk edilemez. Gönderim, geri dönüş/iade ve imha yalnız olay kaydıdır,
ikinci stok hareketi yaratmaz; müşteri referansı stok etkisizdir. Kalite kararı kapsadığı palet/bobinleri yalnız
belgeler; kalite durumlarını değiştirmez, blokaj kaldırmaz; numune kararı yetkisi blokaj kaldırma veya stok düzeltme
yetkisi değildir. Kriterler ve kabul aralıkları yetkili kişilerce tanımlanır (sabit fabrika standardı yoktur);
değişiklik yeni kriter kaydı açar, eski ölçüm ölçüm anındaki kriter kopyasıyla kalır. QR etiketi yalnız yetkili sayfayı
açar, stok/durum değiştirmez. Satış yalnız yetkili müşterisinin paylaşılabilir durumunu, paylaşılabilir notu ve müşteri
geri bildirimini görür (ölçüm, iç karar, iç not, konum, reçete yok).

**Kullanılabilirlik (Aşama 6).** Mevcut tasarım dili ve altyapı korundu. Alan etiketlerinde birim, altında kısa açıklama
(alan dolu iken de görünür). Hata mesajları alanın yanında, Türkçe (arayüz dili sabit Türkçe). Değişmiş formdan
ayrılırken tarayıcı uyarısı. Çift tıklama kilidi korunur ve tıklanan düğmenin değeri artık kaybolmaz (bulunan hata:
kilit adım düğmelerinin değerini düşürüyordu). Aynı sayfadaki her form kendi tek kullanımlık anahtarını kullanır
(bulunan hata: baskı görseli/değişiklik isteği sayfalarında ikinci işlem "zaten kaydedildi" sayılabilirdi). Etkili
işlemlerde "Emin misiniz?" yerine etki metni. Yetkisiz işlem ekranda sunulmaz ve sunucuda reddedilir.

## 4. Yetki matrisi (yeni ve ilgili işlemler)

T = tüm kayıtlar, M = erişim verilen müşteriler, A = kendisine atanmış işler, · = yok. Tam matris:
`docs/operasyon-genisletme.md` bölüm 1 (koddan yeniden üretildi).

| Yetki | Yönetici | İK | Satış | Planlama | Üretim yetkilisi | Ustabaşı | Usta / operatör | Kalite sorumlusu | Ambar yetkilisi | Depo personeli |
|---|---|---|---|---|---|---|---|---|---|---|
| `recipe.view` Reçete arşivi ve revizyonlarını görme | T | · | · | T | · | · | · | · | · | · |
| `recipe.change` Reçete revizyonu oluşturma | T | · | · | T | · | · | · | · | · | · |
| `artwork.view` Baskı görsellerini ve onay durumunu görme / indirme | T | · | M | T | T | · | · | · | · | · |
| `artwork.manage` Baskı görseli taslak sürümü yükleme / kullanımdan kaldırma | T | · | M | T | · | · | · | · | · | · |
| `artwork.approve` Baskı görseline iç teknik onay verme (üretime uygun) | T | · | · | T | · | · | · | · | · | · |
| `artwork.customer_approval` Baskı görseli için müşteri onayını kaydetme | T | · | M | T | · | · | · | · | · | · |
| `sample.view` Numuneleri görme (satış: yalnız paylaşılabilir durum ve geri bildirim) | T | · | M | T | T | A | · | T | T | T |
| `sample.manage` Numune kaydı, konum, gönderim, geri dönüş, imha, ek dosya | T | · | · | T | T | A | · | T | T | T |
| `sample.stock` Numune için paletten kutu ayırma (stok düşer) | T | · | · | · | · | · | · | · | T | T |
| `sample.decide` Numune ölçümü ve iç kalite kararı (blokaj kaldırma değildir) | T | · | · | · | T | · | · | T | · | · |
| `sample.feedback` Müşteri geri bildirimini kaydetme | T | · | M | T | · | · | · | T | · | · |
| `sample.criteria` Numune kontrol kriterlerini tanımlama | T | · | · | · | · | · | · | T | · | · |

Matbaa operatörü görsel dosyasını `artwork.view` olmadan, yalnız kendi açık iş emrindeki operasyonda aktif teknik
erişimi varken görür. Revizyonlu repete kararı `planning.approve` + `recipe.change` (yeni ürün kodu için ayrıca
`product.create`) ister.

## 5. Veri taşıma yaklaşımı

Migration'lar: `catalog.0009_teknik_ozellikler` (revizyona boş bırakılabilir teknik alanlar, bileşen uygulama notu,
renklendirici türü), `catalog.0010_recete_taslagi`, `catalog.0011_baski_gorseli`, `production.0009_baski_gorseli`
(iş emri → görsel sürümü), `orders.0008_teknik_ozellikler` (amaç/yöntem/ücret), `orders.0009_repete` (kaynak kalem,
değişiklik isteği), `warehouse.0004_numune` (hareket türü), `samples.0001_numune`, `accounts.0008_artwork_permissions`,
`accounts.0009_sample_permissions`.

- Eski revizyon, sipariş ve iş emirlerinde yeni alanlar boş = belirtilmemiş; hiçbir değer tahmin edilip yazılmadı.
- Yetkiler yalnız **değiştirilmemiş** şablon rollerine eklenir; özelleştirilmiş rollere dokunulmaz, işlem geçmişine
  "şablon farkı" notu yazılır (`template_sync`). Bu sırada bulunan hata düzeltildi: temiz kurulumda rolleri güncel
  şablonla oluşturan ilk migration nedeniyle eski senkron migration'ları sahte "şablon farkı" üretiyordu.
- Yükseltme denetimi: `mimcor_uat` kopyasında 29, `mimcor` kopyasında 38 migration hatasız; eski revizyon kartında
  "Körük / Baskı / Perfore: Belirtilmemiş", eski siparişte amaç/yöntem belirtilmemiş. UAT kopyasında depo ve üretim
  yetkilisi rolleri daha önce özelleştirildiği için (eksik `customer.document.view`) numune yetkileri otomatik
  eklenmedi ve şablon farkı olarak raporlandı — yönetici rol ekranından karar vermeli. Kaynak veritabanları değişmedi.
- Temiz kurulum + `seed_demo` iki kez: hatasız, tekrar çalıştırmada kayıt çoğalmaz; yetki denetiminde bulgu yok.

## 6. Test sonuçları ve ekran görüntüleri

Yerel, 28.10.2026, PostgreSQL 16 (seri çalıştırma):

| Kontrol | Sonuç |
|---|---|
| `python manage.py test` (tam takım) | 378/378 geçti (önceki tur 330; bu turda +48 test, eş zamanlılık testleri dahil) |
| Tarayıcı: `browser_checks`, `order_product_filter_checks`, `field_scenarios_checks`, yeni `teknik_numune_checks` (Chromium headless, 1440 px + 390 px emülasyon) | 8/8 geçti |
| `makemigrations --check --dry-run` | Değişiklik yok |
| Temiz kurulum + `seed_demo` ×2 | Hatasız; denetimde bulgu yok |
| Yükseltme: `mimcor_uat` kopyası (29 migration), `mimcor` kopyası (38 migration) | Hatasız; bölüm 5 |
| GitHub Actions | Son commit için iki iş (Django, Chromium) teslim notunda verilir |

Mevcut testler korundu; üç mevcut test yeni akışa uyarlandı (kontrol gevşetilmeden): sipariş girişi testleri tam
gönderimde amaç ve teslimat doğrulamasını da gönderir; ürün filtresi tarayıcı testi hatayı 2. adımda bekler ve "Geri"
ile seçimin korunduğunu doğrular, müşterinin bütün aktif ürünleriyle karşılaştırır (yeni demo ürünü); demo akışı 3
adımdan geçer.

Kabul senaryoları ve kanıtları:

| # | Senaryo | Test |
|---|---|---|
| 1 | Satış körüklü, baskılı ürün siparişi açar; planlama özellikleri ve onaylı görseli görür | `catalog/tests/test_spec.py::OrderWizardTests`, `ui_checks.teknik_numune_checks` (adım 1–2) |
| 2 | Eksik oranlı reçete taslak kaydolur, onaylanamaz; %100'de onaylanır; yetkisiz bileşimi göremez | `catalog/tests/test_recipe_draft.py`, tarayıcı adım 3 |
| 3 | Yeni baskı görseli eski iş emrinin talimatını değiştirmez | `catalog/tests/test_artwork.py::test_release_requires_valid_artwork_and_pins_version` |
| 4 | Repete eski stok/sevk/onay kayıtlarını kopyalamaz | `orders/tests/test_repeat.py::test_repeat_copies_nothing_operational` |
| 5 | Revizyonlu repete değişiklikleri planlamaya gösterir | `orders/tests/test_repeat.py` (yan yana, yeni revizyon, yeni ürün kodu, ret), tarayıcı adım 4 |
| 6 | Numune ayırma, gönderim, iade ve imhada stok iki kez etkilenmez | `samples/tests/test_samples.py::StockEffectTests` |
| 7 | Numune uygunluğu ilgisiz paleti serbest bırakmaz | `samples/tests/test_samples.py::DecisionScopeTests` |
| 8 | Başka müşterinin sipariş/numune/dosyasına URL ve POST ile erişilemez | `test_artwork.py::test_file_access_scoped`, `test_samples.py::test_other_customer_url_and_post_404`, `test_repeat.py::test_other_customer_hidden` |
| 9 | Çift gönderim ve eş zamanlı işlemler | Çift gönderim: sipariş, repete, görsel yükleme, stok ayırma; PostgreSQL eş zamanlı: reçete onayı, değişiklik kararı, numune stok ayırma (`TransactionTestCase`) |

Ekran görüntüleri (kurgusal veri, 1440 px ve 390 px emülasyon): `docs/screenshots/teknik-numune-repete/` —
`tnr-1-siparis-adim1`, `tnr-1-siparis-hata`, `tnr-1-siparis-adim3`, `tnr-2-planlama-onay`, `tnr-2-baski-gorselleri`,
`tnr-3-recete-eksik`, `tnr-3-recete-onaylandi`, `tnr-4-repete-kalemler`, `tnr-4-repete-kontrol`,
`tnr-4-planlama-yan-yana`, `tnr-5-numune-ayrinti`, `tnr-5-numune-etiket`, `tnr-5-numune-satis` (her biri `-1440` ve
`-390`).

## 7. Karar bekleyen terimler ve ölçüm yöntemleri

| Konu | Mevcut davranış (onaylanmış sayılmaz) |
|---|---|
| "Klişe kazan aralığı" teriminin karşılığı | Kesinleşmedi. Kazan çevresi, baskı tekrar boyu ve iki baskı arası boşluk ayrı mm alanları; hiçbiri bu terime eşitlenmedi, birbirinden türetilmez. İşletme terimi tanımlayınca etiket/eşleme yapılmalı. |
| Körüklü üründe en ölçümü | Kapalı en / açılmış toplam en açık seçim olarak kaydedilir; dönüşüm yok. İşletmenin standart ölçüm yöntemi belirlenmeli. |
| Baskı görselinde müşteri onayının üretim şartı olup olmadığı | Varsayılan: iç teknik + müşteri onayı gerekir (`PRINT_REQUIRE_CUSTOMER_APPROVAL=1`). |
| Geri dönen numunenin satılabilir stoğa alınması | Desteklenmiyor; geri dönüş yalnız olay kaydı. |
| Numune kabul aralıkları | Sistem standart uydurmaz; demo kriterinde aralık yok. Kalite yetkilisi tanımlamalı. |
| Tolerans alanları | Yalnız bilgi; ölçüm sonucuyla otomatik karşılaştırma yapılmaz. |
| Önceki turdan karar bekleyen dört konu (ambar talep hakkı, tedarikçi parti/irsaliye, belgesi beklenen mal kabulü, geciken devir) | Değiştirilmedi (`docs/kabul-denetimi.md` bölüm 6). |

## 8. Desteklenmeyen birim ve stok durumları

- Rezervasyon ve sevkiyat yine yalnız **kutu**; kg/rulo/adet arasında doğrulanmamış dönüşüm yapılmaz (sahte birim
  dönüşümü eklenmedi).
- Karışım yalnız kg birimli malzemelerle; litre/adet malzeme karışıma girmez.
- Numunede stoktan yalnız tam kutu düşer. Kutudan kısmi numune (birkaç adet/rulo, kg) ve bobinden kg numune
  kaydedilir ama palet/bobin miktarı değişmez — bu kasıtlı sınırdır; fiziksel sayım farkı varsa düzeltme talebiyle
  kapatılır.
- Ürün başına tek açık reçete taslağı. Yeni ürün kodu kararından sonra taslaktan vazgeçilirse kalem yeni kodda kalır;
  istek yeniden karar bekler.

## 9. Kısa kullanıcı kabul adımları (demo verisi, kurgusal)

1. **2001 Satış** → Siparişler → Yeni sipariş: Müşteri "Örnek Gıda", ürün ORN-KB-045 → İleri. Kartta "Yan körük",
   "Kapalı en", klişe KL-ORN-01, "Onaylı sürüm 1" görünür; karışım görünmez. Miktara 12,5 yazın → İleri: kutuda tam sayı
   uyarısı, yazdığınız korunur. 10 yazın, amaç Numune, ücret Ücretsiz → İleri → teslimatı işaretleyip kaydedin; kod ve
   sıradaki adım yazar. Siparişi onaylayın.
2. **3001 Planlama** → Planlama → kalemi açın: aynı özellikler; "Baskı görsellerini aç" → sürüm 1 "Geçerli".
3. **3001** → ORN-KB-045 reçetesi → Değiştir → Taslak aç → "+ Malzeme ekle"; ilk oranı 57 yapın: "Karışım toplamı %97…"
   → Onayla: reddedilir, taslak kalır. 60 yapıp onaylayın: yeni revizyon.
4. **2001** → aynı sipariş → "Tekrar sipariş (repete)" → kalem işaretli; miktar 5; "Teknik değişiklik iste" → işaret,
   gerekçe, en 420 → İleri → değişiklik tablosu → kaydedin. **3001** → değişiklik isteği: 400 mm / 420 mm yan yana;
   karar verin.
5. **5001 Depo** → Numuneler → Yeni: Saklama/şahit, Deneme Tekstil, DEN-PF-030, 2 kutu → kaydedin → "Stoktan ayır":
   PLT-2026-00001, 2 → palet 2 kutu azalır. Gönder → geri dönüş → imha: palet bir daha değişmez. QR etiketi açılır.
6. **4004 Kalite** → aynı numune: ölçüm (Kalınlık) ve "Uygun" kararı, kapsam PLT-2026-00001 → paletin kalite durumu
   değişmez. **2001** → numune: yalnız durum ve geri bildirim alanı; ölçüm/karar/konum görünmez.

Başarısızlıkta adım no, personel no, adres, kayıt kodu, saat, görülen mesaj ve ekran görüntüsünü kaydedin.
