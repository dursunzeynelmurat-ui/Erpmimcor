# UI/UX teslim ve doğrulama — 26 Ekim 2026

Hedef demo: **30 Ekim 2026 Cuma**. Kaynak `claude/affectionate-gates-18657o`, başlangıç ve inceleme commit’i `ee861923fd0ad59f7887cc9b57e65f2e2bbb1ce0`. Başlangıçta kaynak branch daha ileri değildi. Tasarım branch’i `design/mimcor-demo-2026-10-30`. Depoda AGENTS.md bulunmadı.

## Değişiklikler

- Mevcut Django uygulamasında açık zemin, ölçülü petrol yeşili vurgu, belirgin yazı hiyerarşisi; masaüstünde sabit yan menü, telefonda açılır menü. Logo yerine sade Mimcor yazısı.
- Satış, planlama, ustabaşı ve depo için gerçek sorgulara dayanan ana ekran iş listeleri; yöneticide birleşik görünüm. Kişiye iş ataması bulunmadığından “atanmış işlerim” iddiası yok.
- Siparişlerde gerçekleşen üretim, açık rezervasyon ve sevk ayrı gösterilir. Teslim tarihi yalnız kayıtlı tarihtir. Teknik onay bekleme nedeni ve planlama notu görünür. Desteklenmeyen adet/rulo üretim özeti ve yanıltıcı yüzdeler gösterilmez.
- Siparişler, iş emirleri, paletler ve sevkiyatlar 30 kayıtlık sayfalara ayrılır; mevcut filtre ve arama parametreleri korunur. Telefonda sipariş tablosu etiketli satırlara dönüşür; geniş işlem tabloları kendi alanında kayar.
- Palet stok özeti, kalite gerekçesi, bobin blokajı ve tartım kaynağı belirgin. Stok değişikliği formlarında miktar/hedef/gerekçe kaydetmeden önce kontrol penceresinde görünür. Sevkiyat onayında palet → kalem ve miktar ayrıca özetlenir.
- Ustabaşı ekranında üretimi başlat ve işlem bölümlerine erişim üstte; bobin kaydında ek kaynak alanları gerektiğinde açılır. Denge toleransı açıkça ayarlanabilir demo varsayılanıdır.
- Görünür klavye odağı, içeriğe geç bağlantısı, form hata özeti, alanla ilişkili hata/yardım metinleri ve ayrı form alan kimlikleri. Yetki hatası Türkçedir.
- İşlem geçmişi alan/durum etiketleri ve ondalıkları sunumda Türkçeleştirilir; ham kayıtlar değiştirilmez.
- Özel iş emri/bobin/reçete şablonlarında `form.idem_key` özelliğiyle gizli alanın çakışması giderildi; gizli alanlar `hidden_fields` üzerinden render edilir. Anahtar üretimi ve sunucudaki `perform_once` değişmedi.
- Rezervasyon iptali hata görünümü yönlendirme yerine bağlı formu gösterir; gerekçe ve işlem anahtarı korunur.
- İş kuralları, servisler, yetki matrisi ve veritabanı şeması değiştirilmedi.

## Doğrulama

Ortam: Python 3.12, Django 5.2.18, PostgreSQL 17.11, Chromium; yalnız kurgusal `seed_demo` verisi. Testler ayrı `test_mimcor` veritabanında çalışır. PostgreSQL eş zamanlılık testleri SQLite ile değiştirilmedi.

| Kontrol | Sonuç |
|---|---|
| Başlangıç `python manage.py test` | 114/114 başarılı |
| Değişiklik sonrası `python manage.py test --noinput` | 123/123 başarılı (114 mevcut + 9 sunum testi) |
| `python manage.py test ui_checks.browser_checks --noinput` | 2/2 başarılı; her test Demo A ve B’yi baştan sona çalıştırır |
| Masaüstü | 1440 × 900 Chromium |
| Telefon görünümü | 390 × 844 Chromium ekran emülasyonu |
| Beş rol | Giriş, ortak yetkili listeler; yönetici kullanıcı listesi, satış siparişi, planlama onayı, ustabaşı üretimi, depo kabul/sevkiyat |
| Yetkisiz işlem | Satış/planlama/depo üretim başlatma POST’u; satış planlama ekranı, ustabaşı kullanıcı listesi: 403 |
| Demo A | Sipariş → teknik onay → iki operasyon → 70 kg malzeme → 68/67,5 kg bobin → 20 kutu palet → kapanış → kabul → kalite → ayırma → sevk |
| Demo B | Aynı palet: fiziksel 40, ayrılan 10, serbest 30 → sevk 10 → kalan 30; yeniden tartım uyarısı |
| Tekrar gönderim | Aynı sevkiyat POST’u aynı anahtarla tekrar: tek hareket |
| Bağlantı kaybı | Aktarım sunucuda kaydedildikten sonra yanıt kesilir; aynı POST tekrar gönderilir: tek stok düşümü |
| Hata durumları | Geçersiz sipariş miktarı, kaynak toplamını aşan bobin, stok üstü aktarım; geçerli girdiler ve işlem anahtarı korunur |
| Boş durum | Sonuçsuz sipariş araması ve boş iş listeleri |
| QR | Anonim kullanıcı girişe gider; yetkili kullanıcı kayıt açar; hareket sayısı değişmez |
| Görsel/HTML | Kontrol edilen ekranlarda sayfa genişliği taşmaz; form kimlikleri ve işlem anahtarları benzersiz |
| Sistem | `manage.py check`, `git diff --check` |

Tarayıcı testleri gerçek HTTP istekleri, Django şablonları ve PostgreSQL kayıtlarıyla çalışır; iş akışındaki yazmalar UI formlarından yapılır. ORM yalnız kurgusal başlangıç verisini kurmak ve sonucu doğrulamak için kullanılır. Playwright’ın senkron API’si için async koruma istisnası yalnız test sürecinde ve seri doğrulamalarda etkinleştirilir; uygulama ayarlarına eklenmez.

## Ekran görüntüleri

| Ekran | Masaüstü | Telefon emülasyonu |
|---|---|---|
| Ana ekran | [1440 px](screenshots/ana-ekran-1440.png) | [390 px](screenshots/ana-ekran-390.png) |
| Sipariş | [1440 px](screenshots/siparis-1440.png) | [390 px](screenshots/siparis-390.png) |
| Planlama | [1440 px](screenshots/planlama-1440.png) | [390 px](screenshots/planlama-390.png) |
| İş emri | [1440 px](screenshots/is-emri-1440.png) | [390 px](screenshots/is-emri-390.png) |
| Bobin formu | [1440 px](screenshots/bobin-formu-1440.png) | [390 px](screenshots/bobin-formu-390.png) |
| Palet / kısmi sevk | [1440 px](screenshots/palet-kismi-sevk-1440.png) | [390 px](screenshots/palet-kismi-sevk-390.png) |
| Sevkiyat onayı | [1440 px](screenshots/sevkiyat-onay-1440.png) | [390 px](screenshots/sevkiyat-onay-390.png) |
| Form hatası | [1440 px](screenshots/form-hatasi-1440.png) | [390 px](screenshots/form-hatasi-390.png) |

Başlangıç görünümü: [masaüstü](screenshots/before-desktop.png), [telefon](screenshots/before-mobile.png). Diğer görseller `screenshots/` altındadır. Son görüntüler test veritabanındaki kurgusal kayıtlardan alınmıştır; kodlar yerel demo veritabanındaki kayıtlarla aynı olmak zorunda değildir.

## Sınırlar ve ayrı backend bulguları

- **Fiziksel telefon testi yapılmadı.** iOS Safari, Android Chrome cihaz testi, gerçek QR kamera/yazıcı, gerçek ağ koşulları ve fiziksel terazi doğrulanmadı. Chromium emülasyonu bunların yerine geçmez.
- Demo yerel ortamda doğrulandı; canlı yayın ve kaynak branch’e merge yapılmadı. Gerçek müşteri verisi kullanılmadı.
- Rezervasyon/sevkiyat yalnız kutu; muhasebe, Mikro, sensör/terazi, tam maliyet, otomatik çizelgeleme kapsam dışında. İnternet kesintisinde çevrimdışı çalışma kuyruğu yok.
- **Ayrı iş mantığı bulgusu:** `production/selectors.py:produced_quantity_for_line` kg dışındaki tüm birimler için kutu hareketlerini topluyor; adet/ruloda bu miktar kendi birimiymiş gibi yorumlanmamalı. Bu teslimde hesap değiştirilmedi; desteklenmeyen özet arayüzde kaldırıldı. Genel birim desteği ayrı backend çalışması gerektirir.
- Bobin blokajı aktarılmış içeriğin kesin kaynağı ayrılamadığından ilişkili paletleri ihtiyatlı bekletebilir; ekranda açıklanır.
- Kişiye atama modeli yok. Planlama ve ustabaşı gerçek aktif iş emirlerini görür.
- Ana ekran özet listeleri 6 (aktif iş emirleri 10) kayıtla sınırlıdır. Tanım/lot listeleri ve üretim ilerlemesi için genel sayfalama bu teslimde eklenmedi.
- Görsel erişilebilirlik kontrolleri tam bir WCAG sertifikasyonu veya ekran okuyucu denetimi değildir.
