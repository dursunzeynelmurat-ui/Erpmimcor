# Mimcor ERP kullanım kolaylığı — 1 Kasım 2026

## Kaynak ve ortam

`chore/mimcor-local-docker` uzak dalı başlangıçta kontrol edildi: `8e689b64122fe05cd0c66782c9e525466b5adfae`.
Çalışma ayrı `design/mimcor-kullanim-kolayligi` dalındadır. Eski tasarım dalından devam edilmedi; kaynak dal değiştirilmedi.
AGENTS.md bulunmadı. HANDOFF, demo-son-duzeltmeler ve yerel-kurulum belgeleri okundu.

Mevcut uygulama önce Docker ile çalıştırıldı: PostgreSQL 16, Gunicorn, Caddy, DEBUG=0, yalnız `mimcor-astra`
projesi ve `seed_demo` kurgusal kayıtları. Ön inceleme altı rol ile Chromium'da 1440×900 ve 390×844 yapıldı.
Bu çalışma bulut çalışma alanında yapıldı; kullanıcının bilgisayarı veya fiziksel telefon kullanılmadı.
Bulut vekili için yalnız /tmp altında Caddy NO_PROXY=app,db ek ayarı gerekti; depodaki Docker ayrımı değiştirilmedi.
Testler ayrı PostgreSQL konteynerinde çalıştırıldı; uygulama veritabanına test yazılmadı.

## Değişen kullanım

- Sipariş: müşteri → müşteriye göre ürün → miktar/teslim → amaç → teknik talep → kontrol sırası.
  Onaylı teknik kart açılır referans bölümünde; miktar uzun teknik metnin altında kalmaz.
  Normal/numune/deneme amacı ile eski siparişten repete başlatma farkı açıklanır.
  Teknik istek ölçüler, görünüm, perfore, baskı ve paketleme bölümlerine ayrıldı.
- Talepte körük/perfore/baskı koşulları mevcut merkezi alan tanımından gelir. Gizlenen girdiler tarayıcıda
  devre dışıdır; form adaptörü gönderilmiş kontrol alanına bağlı ilgisiz değerleri sunucuda temizler.
  İstek ürünün mevcut reçetesini değiştirmez. Karışım ve iç proses notları satış HTML/JSON'una eklenmedi.
- Reçete: bölüm bağlantıları, iki kolonlu masaüstü / tek kolonlu telefon alanları, açılır kaydedilmiş parti
  hesabı, belirgin kayıt/onay bölümü. %100 toplam mesajı tek başına onaylanabilirlik iddia etmez.
  Malzeme ekleme 30 satırlık mevcut sunucu sınırıyla uyumludur.
- Numune: dört anlamlı form bölümü. Yetkili palet/bobin kaydından müşteri, ürün ve kaynak kodu doldurulur.
  Tür/miktar/stok kaynağı otomatik seçilmez. GET stok hareketi veya numune oluşturmaz.
  Mevcut bağlı palet stoktan ayırma formuna önerilir; miktar ve palet kaydetmeden önce hâlâ doğrulanır.
  Fiziksel yer, kalite, müşteri geri bildirimi ve stok etkisi ayrı gösterilir.
- İş listesi: bekleyen iş satırından kayda doğrudan erişim; iş emri kartında operasyon, makine ve vardiya;
  numune takibine ana ekrandan erişim; boş aramada filtreyi temizleme bağlantısı.
- Erişilebilirlik: Django'nun alan hata kimlikleri ve aria-describedby bağlantıları, mevcut klavye
  odağı/etiket/44 px kontroller korunur. Uzun metinler sarılır. Masaüstünde form işlem alanı görünür kalır;
  telefonda ekranı/klavyeyi örtmemesi için normal akıştadır.

## Korunan sınırlar ve güvenlik incelemesi

`services.py`, stok hesapları, yetki kataloğu, revizyon/onay kuralları, veritabanı şeması ve Docker yapılandırması
bu değişiklikte değiştirilmedi. Sunucu CSRF/idempotency ve işlem anahtarlarının form bazında ayrılığı korunur.
Mevcut rezervasyon, yükleme, sevkiyat ve numune stok çıkışı yalnız desteklenen kutu akışlarında kalır.

Güvenlik incelemesinde özellikle:

1. `sample_create?palet=… / ?bobin=…`: kaynak görüntüleme yetkisi **ve** kaynak/müşteri için sample.manage
   denetimi; kapsam dışı veya bozuk kimliğe 404. POST kendi gönderilmiş verisini doğrular; GET önerisi POST'u ezmez.
2. Satışın reçete sayfasına doğrudan isteği 403; talep alanları yalnız `spec.CHANGEABLE` üzerinden oluşturulur.
   Eklenen form bölümleri yetkisiz iç not veya karışım verisi taşımamalıdır.
3. Koşullu alan adaptörü yalnız açıkça gönderilmiş kontrol alanının bağımlı değerlerini temizlemelidir;
   teknik talep eski reçeteyi değiştirmemeli; adımlar arasında seçimler ve işlem anahtarı korunmalıdır.
4. Yeni iş satırı bağlantıları yalnız zaten kapsamdan geçmiş nesneleri kullanır; hedef view yetkileri kalır.
5. Hatalı POST tekrarında palet/miktar/konum korunmalı; aynı kaydın tekrar gönderimi ikinci stok çıkışı üretmemelidir.

## Ayrı mevcut bulgu: ustabaşı numune formu

Kaynak sürümde ustabaşının `sample.manage=assigned` yetkisi var; `SampleCreateForm` müşteri listesini
`scoped(Customer, sample.manage)` ile kuruyor. Customer modelinde assigned kapsamı tanımlı olmadığından
liste boş. `seed_demo` kullanıcısı 4001 için doğrulama: `can(sample.manage)=True`, müşteri queryset sayısı=0.
Servis `_check_create_scope` atanmış iş emriyle kayıt kabul edebiliyor, fakat genel form bu akışı sunamıyor.
Bu, tasarım kapsamında yetki genişletilerek düzeltilmedi. Yeni kaynak kısayolları mevcut formun desteklediği
müşteri kapsamı olmayan kullanıcıya gösterilmez. Atanmış iş emrinden güvenli numune formu ayrı backend işidir.

## Doğrulama

Başlangıç: **430 Django testi geçti**. Değişikliklerden sonra **439 Django testi geçti**
(233,876 saniye). `manage.py check`, `makemigrations --check --dry-run` ve `git diff --check` temiz.
Son Docker build ve app/web/db sağlık kontrolleri geçti; 1440/390 px sipariş formunda klavye odağı
3 px görünür çizgi ve incelenen kontroller en az 44 px olarak tarayıcıdan doğrulandı.
Yeni regresyonlar: kaynak ön doldurma (GET yazmaz, kapsam dışı kaynak sızmaz, hatalı POST korunur),
koşullu teknik alanlar ve satışta iç alanların bulunmaması; 1440/390 px Chromium kullanım akışı.
Mevcut Demo A/B, teknik talep→reçete→üretim→depo→sevkiyat, repete, numune stok akışları da çalıştırılır.

Ekran görüntüleri: [kullanim-kolayligi](screenshots/kullanim-kolayligi/), `once-` ve `sonra-` önekleri.
Tümü kurgusal veridir. 390 px görüntüler emülasyondur; gerçek telefon, fiziksel QR tarayıcı, yazıcı/terazi
ve fabrika çalışanıyla kullanılabilirlik oturumu yapılmadı. Desteklenmeyen entegrasyon/otomatik çizelgeleme eklenmedi.

### Son tarayıcı sonucu

- Mevcut **12 Chromium senaryosu geçti**: Demo A/B, beş rol ve yetkisiz istekler, müşteri/ürün filtresi,
  saha akışları, teknik talep/reçete/baskı/üretim/sevkiyat zinciri, repete ve numune stok işlemleri.
- Yeni **2 kullanım senaryosu geçti** (1440 ve 390 px, 27,552 saniye): hatalı miktar korunması,
  koşullu alanlar, kaynak paletten numune, hatalı kayıt sonrası konumun korunması, uzun açıklama,
  boş arama, reçete satırı ekleme, benzersiz alan/idem anahtarları, taşma ve reçete erişim reddi.
- Kayıt açıklığı: 14 senaryoluk toplu koşunun mevcut 12 testi başarılıydı; yeni iki test, test verisinde
  olmayan ORN paletini aradığı için hata verdi. Yeni test doğru kurgusal DEN paletine yöneltildi ve iki
  genişlikte yeniden çalıştırılıp geçti. Son sonuç **12 mevcut + 2 yeni ayrı son koşu**dur; sonradan ikinci
  bir toplu 14 koşusu yapılmadı. Uygulama hatası gibi gösterilip backend değiştirilmedi.
- Hata gösterimindeki yinelenen HTML kimliği tarayıcı denetiminde yakalandı; Django'nun kendi hata
  kimliği korundu. Son testler yinelenen kimlik/işlem anahtarı ve yatay taşma denetimlerini geçer.

Tekrar çalıştırma (kurulum/env hazırken):

```sh
python manage.py test --noinput
python manage.py test --noinput ui_checks.browser_checks ui_checks.order_product_filter_checks ui_checks.field_scenarios_checks ui_checks.teknik_numune_checks ui_checks.demo_son_checks ui_checks.usability_checks
```

Ekran çıktıları için CI'da `USABILITY_SCREENSHOT_DIR` eklendi; aynı test modülü mevcut browser job'una dahil.

## Commit / aktarım / CI

Uygulama değişiklikleri ayrı tasarım dalında commit'lendi. GitHub bağlayıcısının branch oluşturması
`403 Resource not accessible by integration`, ortamın Git kimliğiyle normal push da `403 Permission denied`
döndürdü. Kullanıcı hesabının depo izinleri push olarak görünse de bu oturumun kimliği yazmayı gerçekleştiremiyor.
Kaynak uzak dal son kontrolde yine `8e689b6`; yeni uzak tasarım dalı oluşmadı. **CI tetiklenemedi/doğrulanamadı**.
Merge, force push, PR açma ve canlı yayın yapılmadı.

Yerel teslim dizini: `/workspace/Erpmimcor-kullanim`.
Git bundle: `/workspace/Erpmimcor-kullanim.bundle` (yalnız kaynak `8e689b6` üzerindeki yeni commit'ler;
ortam parolaları, veritabanı, .env, sanal ortam ve tarayıcı oturumları dahil değil).
Bundle'ı erişimi olan bilgisayara aldıktan sonra, kaynak commit'i içeren depo içinde:

```sh
git fetch /dosya/konumu/Erpmimcor-kullanim.bundle design/mimcor-kullanim-kolayligi:design/mimcor-kullanim-kolayligi
git switch design/mimcor-kullanim-kolayligi
git push -u origin design/mimcor-kullanim-kolayligi
```

Hedef dal zaten varsa üzerine zorla yazmayın; fetch'in normal korumasını kaldırmayın. Push sonrası GitHub Actions
`tests` iş akışının Django, Docker ve Chromium işlerini kontrol edin. Yerel başarı uzak CI sonucu değildir.
