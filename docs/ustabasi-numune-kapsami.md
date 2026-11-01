# Ustabaşı numune kapsamı — 1 Kasım 2026

Dal: `fix/mimcor-ustabasi-numune-kapsami` (taban `design/mimcor-kullanim-kolayligi` @ `ab7819c`).
AGENTS.md yok. Merge, force push, PR ve canlı yayın yapılmadı.

## Bulgu

Ustabaşı (`sample.manage` = atanan iş emri) kendisine atanmış iş emri için servis üzerinden numune açabiliyordu.
Ama arayüzde müşteri listesi boştu; müşteriye göre ürün sorgusu ve palet/bobin kısayolu 404 dönüyordu.
Sebep: form ve sorgular `scoped(Customer, sample.manage)` kullanıyordu ve Customer modelinde atanan iş emri kapsamı
yok; servis ise iş emri atamasına bakıyordu. Aynı işlem için iki farklı kural vardı.

Önce hata başarısız testle gösterildi (commit `4f383bd`, `samples/tests/test_foreman_scope.py`):
kaynak sürümde 12 testin 10'u kırmızıydı.

## Düzeltme

Tek kural: `samples/access.py`. Bu kuralı yeni numune formu, `numuneler/musteri/<id>/urunler/` ürün sorgusu,
`?is_emri=` / `?bobin=` / `?palet=` kısayolları, iş emri/bobin/palet sayfalarındaki "numune başlat" bağlantıları ve
`services.create_sample` birlikte kullanır.

| `sample.manage` kapsamı | Müşteri/ürün seçeneği | Kayıt koşulu |
|---|---|---|
| all (depo, kalite, …) | Değişmedi: tüm aktif müşteri ve ürünler | Değişmedi |
| customer | Değişmedi: erişim verilmiş müşteriler | Değişmedi |
| assigned (ustabaşı) | Yalnız **şu anda kendisine atanmış** iş emirlerinin müşterisi ve ürünü | İş emri zorunlu ve atanmış olmalı. Ürün iş emrinin ürünü, bobin o iş emrinin, palet o iş emrinden, sipariş kalemi iş emrinin kalemi olmalı |

- **Otomatik doldurma:** İş emri kartında yeni "Bu iş emrinden numune başlat" bağlantısı var. İş emri, bobin veya
  paletten başlatılan formda müşteri, ürün, iş emri no, varsa sipariş kalemi ve bobin/palet kodu dolar.
  Tür, miktar ve stok kaynağı otomatik seçilmez. GET kayıt veya stok hareketi oluşturmaz.
- **Sunucu yeniden doğrular:** POST'ta seçenekler güncel yetki ve atamayla yeniden kurulur. Kodla girilen iş emri,
  bobin, palet ve sipariş kalemi yalnız kullanıcının numune kapsamında aranır. Kapsam dışı kayıt ile olmayan kayıt
  aynı mesajı alır ("bulunamadı veya bu numune için kullanılamaz"); kaydın varlığı açığa çıkmaz. Servis aynı kontrolü
  transaction içinde tekrar yapar: form atlanırsa da geçerlidir. Mevcut müşteri–ürün–revizyon–bobin–palet çelişki
  kontrolleri olduğu gibi kalır.
- **Devir / eski form:** İş emri başka ustabaşına devredildikten sonra eski sekmeden gönderilen form reddedilir.
- **Revizyon seçeneği:** Ustabaşı için yalnız iş emrinin revizyonu listelenir; etikette ölçü (mm/µ) gösterilmez.
- **Açıklama:** Formda ustabaşına "iş emri no zorunlu" notu var. Atanmış iş emri yoksa bu da yazılır.
  "Yeni numune" düğmesi gizlenmedi.

## Değişmeyenler

- `accounts/catalog.py` rol şablonları ve `accounts/scope.py` kapsam tabloları değişmedi; hiçbir rolün kapsamı
  büyümedi.
- Müşterinin numune formunda seçenek olarak görünmesi müşteri kartı, belge, sipariş veya ürün sayfalarına erişim
  vermez. Testte ustabaşına müşteri kartı 403/404 döner.
- Stok ayırma (`sample.stock`), kalite kararı (`sample.decide`) ve gönderim kuralları değişmedi. Ustabaşına stok
  ayırma ve karar hâlâ 403. Numune kaydı stok düşürmez.
- Makine, vardiya ve devir kuralları (`production`, `accounts.scope`) değişmedi. Reçete veya proses notu açılmadı.
- Depo/kalite akışında tek fark: palet/bobin kısayolu artık bilinen iş emri ve sipariş kalemi kodunu da doldurur.
  Hata mesajı "… bulunamadı veya bu numune için kullanılamaz" oldu.

## Testler

- `samples/tests/test_foreman_scope.py` (14 test):
  - kendi iş emri için form/servis kaydı ve stok hareketi olmaması;
  - üç kısayolun doldurduğu alanlar;
  - kapsam dışı seçenek, kısayol ve ürün sorgusunun 404 dönmesi;
  - 7 farklı değiştirilmiş POST (başka iş emri/müşteri/ürün, iş emri yok, başka iş emrinin paleti/bobini,
    başka siparişin kalemi);
  - olmayan ve kapsam dışı kodun aynı mesajı alması;
  - devirden sonra eski form;
  - müşteri kartına erişim olmaması;
  - stok ayırma ve kararın 403 kalması;
  - servisin formsuz çağrıda aynı kuralı uygulaması;
  - revizyon seçeneği;
  - depo akışının değişmemesi.
- `ui_checks/ustabasi_numune_checks.py`: 4001 ile 1440 px ve 390 px Chromium senaryosu. Kapsam:
  - liste → yeni numune seçenekleri;
  - iş emri kartından başlatma;
  - başka müşterinin iş emri koduyla değiştirilmiş gönderim (reddedilir, girilen bilgiler korunur);
  - başarılı kayıt ve stok hareketi olmaması;
  - palet kısayolu;
  - kapsam dışı URL'lerin 403/404 dönmesi.

  CI tarayıcı işine eklendi. Ekran görüntüleri: `screenshots/ustabasi-numune/`, yalnız kurgusal veri.

## Kalan sınırlamalar

- Transfer veya karışık paletler (paletin `work_order` alanı boş) ustabaşına numune kaynağı olarak açılmaz. Bu
  kasıtlı ve temkinli bir seçim; gerekiyorsa işletme kararı.
- Atama kontrolü kayıt anındaki transaction'da yapılır. Aynı milisaniyede yapılan devir ile kayıt arasında ayrı kilit
  yok.
- 390 px emülasyondur. Gerçek telefon ve gerçek ustabaşı ile kullanım testi yapılmadı. Testlerin geçmesi işletme
  onayı değildir.
