# Demo öncesi son işlevsel düzeltmeler — 30 Ekim 2026 (güncel)

Branch: `fix/mimcor-demo-son-duzeltmeler` (taban `c316506`; merge/PR/canlı yayın yok). Ayrıntı, her bulgu için önceki
davranış / düzeltme / regresyon testi / kalan sınır, doğrulama, demo senaryoları ve Astra UI devri:
**[docs/demo-son-duzeltmeler.md](docs/demo-son-duzeltmeler.md)**.

| # | Düzeltme |
|---|---|
| 1 | İç proses notu satış yanıtlarında hiç üretilmez (`recipe.view` sınırı) |
| 2 | Repete teknik onayında beklenen revizyon (kaynak anlık / karar sonucu); başka revizyon yalnız gerekçeyle |
| 3 | Firma stoğundan numune stok hareketi tamamlanmadan gönderilemez/imha edilemez; önceki çıkış bağlanabilir |
| 4 | Numune fiziksel kaynağın üretim revizyonuna bağlanır; çelişki ve belirsizlikte tahmin yok |
| 5 | Talimatta uygulama notu ve ürüne özel ambalaj notu (müşteri genel talimatından ayrı) |
| 6 | Numune işlem formu hatada girilen bilgiyi, alan hatasını ve açık bölümü korur |
| 7 | Normal yeni siparişte ve planlama bekleyen kalemde satışın teknik talebi; karar çıkana kadar onay/iş emri yok |

Doğrulama: 426 Django testi, 12 tarayıcı testi (1440/390 emülasyon), temiz kurulum ve iki yükseltme kopyası OK.
**Açık demo engeli:** erişilebilir sunum kurulumu yok (yalnız konteyner içi 127.0.0.1; tünel açılmadı). Gerçek
telefon, fiziksel yazıcı ve gerçek kullanıcı gözlemi yapılmadı.

---

# Teknik sipariş, reçete, baskı, numune ve repete — 28 Ekim 2026

Branch: `feature/mimcor-teknik-numune-repete` (taban `63a5105`; merge/PR/canlı yayın yok). Ayrıntı, iş kuralları, yetki
matrisi, veri taşıma, test sonuçları, ekran görüntüleri, karar bekleyenler ve kısa kabul adımları:
**[docs/teknik-numune-repete.md](docs/teknik-numune-repete.md)**.

| Aşama | Özet |
|---|---|
| 1 Teknik özellikler | Teknik revizyonda (tek kaynak) ölçüler/körük/en tanımı/toleranslar/görünüm/perfore/baskı alanları; boş = Belirtilmemiş, "Yok" ayrı; 3 adımlı sipariş girişi; Türkçe ondalık; amaç/yöntem/ücret ayrı |
| 2 Reçete | Satır eklenebilen taslak; %100 olmadan onay yok; parti kg hesabı ve yuvarlama farkı; renklendirici; yeni revizyon, eski işler değişmez |
| 3 Baskı | Görsel sürümleri, iç teknik ve müşteri onayı ayrı, baskı imzasına bağlı; iş emri serbest bırakılırken sürüme sabitlenir; güvenli indirme |
| 4 Repete | Kalem seçmeli tekrar sipariş; operasyon kaydı kopyalanmaz; revizyonlu repetede planlama yan yana karar verir |
| 5 Numune | Fiziksel durum / iç karar / müşteri geri bildirimi ayrı; tek stok hareketi; karar paletleri serbest bırakmaz; QR |
| 6 Kullanılabilirlik | Alan yanı Türkçe hatalar, açıklama + birim, ayrılma uyarısı; çift gönderim ve aynı sayfadaki form anahtarı hataları düzeltildi |

**Karar bekleyen:** "klişe kazan aralığı" teriminin karşılığı, körüklüde en ölçüm yöntemi, baskıda müşteri onayının
üretim şartı olup olmadığı (varsayılan: gerekir), geri dönen numunenin stoğa alınması (desteklenmiyor), numune kabul
aralıkları. Önceki turun dört karar konusu değiştirilmedi. **Gerçek kullanıcı gözlemi ve gerçek telefon testi
yapılmadı; testlerin geçmesi işletme onayı değildir.**

---

# Kabul denetimi — 28 Ekim 2026

Branch: `fix/mimcor-kabul-denetimi` (kaynak `fix/mimcor-yetki-veri-tutarliligi` @ `ff01e32`; merge/PR/canlı yayın yok).
Yeni özellik yok. Ayrıntı, test kanıtı, gerçek ortamda yöneticinin elle yapacakları, karar bekleyenler ve 20–30
dakikalık manuel kabul senaryosu: **[docs/kabul-denetimi.md](docs/kabul-denetimi.md)**.

| # | Bulunan ve düzeltilen | Düzeltme |
|---|---|---|
| A | Araç çıkışı yapılmamış sevkiyata iade + sonra sevk geri alma → stok iki kez artıyordu | İade yalnız çıkış yapılmış sevkiyata; iadesi olan sevkiyat geri alınamaz |
| B | Yerine gönderim kalemi kalem değişikliğiyle iade hakkını aşabiliyordu | Değişiklik hak sınırında (iade kaydı kilitlenir) |
| C | Sipariş kopyası yerine gönderimi yeni satış yapıyordu | Kopyada yerine gönderim kalemleri atlanır |
| D, E | Devirden sonra eski operatör üretim kaydı formlarını (reçete bileşimini dolaylı gösteren lot listesi) ve iş emri başlığında ölçüleri görebiliyordu | Formlar GET'te de nesne kapsamlı yetki ister; ölçüler yalnız aktif teknik erişimle |
| F | Yerine gönderilen ürünün iadesi müşteride kalan neti düşürmüyordu | Net hesabı düzeltildi |

Tolerans hesabı doğru çıktı (taban net verilen malzeme; 3,3 kg = düzeltilmiş 110 kg × %3); sınır testleri eklendi.
Yeni salt okunur komut: `python manage.py yetki_denetimi` (kim neye hangi rol/müşteri erişimiyle erişiyor, iş kuralını
aşan durumlar). Migration yok. Orijinal demo/UAT veritabanları değiştirilmedi; yükseltme ve tarayıcıyla yönetici
kontrolü kopyalarda yapıldı. **Testlerin geçmesi işletme onayı değildir; gerçek telefon testi yapılmadı.**

---

# Kabul öncesi yetki ve veri tutarlılığı düzeltmeleri — 27 Ekim 2026 (güncel)

Branch: `fix/mimcor-yetki-veri-tutarliligi` (kaynak `feature/mimcor-operasyon-genisletme` @ `9902c1e`; merge/PR/canlı
yayın yok). Ayrıntı, önceki/yeni davranış tablosu, migration etkisi ve UAT adımları:
**[docs/operasyon-genisletme.md](docs/operasyon-genisletme.md) bölüm 5–8**.

| # | Konu | Özet |
|---|---|---|
| 1 | Stok düzeltmesi | Doğrudan lot/palet sayım yolları (gerçekten açıktı, testle kanıtlandı) kapatıldı; sayım farkı yalnız talep + başka yetkilinin onayı. Mal kabulünde tedarikçi parti zorunlu. |
| 2 | Müşteri erişimi | Kaynak (açan/yönetici/vekâlet/taşınan/belirsiz) + yönetici inceleme ekranı, önizlemeli toplu işlem. Hiçbir erişim silinmedi. |
| 3 | Karışım | Malzeme/oran farkı teknik eşdeğer sayılmaz; eşdeğerlik onayı kalem + kaynak + hedef revizyonla sınırlı. |
| 4 | Fazla teslim | İzin artır/azalt/kaldır; sınır ayrılmış+sevk edilenin altına inemez; eş zamanlılık testli. |
| 5 | Sevk yetkileri | `shipment.approve` / `shipment.undo` / `shipment.exit` ayrı; depo = hazırlama + QR. Özelleştirilmiş roller yeniden yazılmaz. |
| 6 | Kapalı iş emri | İlk kapanış ve düzeltmeler sonrası güncel denge ayrı; tolerans aşılırsa yeniden değerlendirme. |
| 7 | İade | Yerine gönderim iade kaydına bağlı kalem; ilk sevk / iade / yerine gönderim / net ayrı. |
| 8 | Vardiya | Operatör: atanmış makine **ve** sorumlu vardiya; devir teslim sorumluluğu taşır; saat kuralı yok. |
| 9 | CI | Django + ayrı Chromium tarayıcı işi; ekran görüntüleri ve hata trace'i artifact. |

**Doğrulama:** yerelde 301/301 Django testi ve 6/6 tarayıcı testi (1440 px + 390 px **emülasyon; gerçek telefon testi
yapılmadı**). GitHub Actions'ta iki iş de çalışır; sonuç teslim raporunda commit bazında verilmiştir. Yükseltme: orijinal
demo veritabanı ve UAT veritabanının **kopyalarında** migrate edildi; temiz kurulum + `seed_demo` ×2. Kullanıcı
veritabanları değiştirilmedi.

**Testlerin geçmesi işletme onayı değildir.** Kod şu varsayımlarla çalışır (bölüm 7): fazla teslim 0; karışım farkı her
zaman onay ister; çıkış kaydı ambar yetkilisi + yönetici; ustabaşı/ambar talep hakkı varsayılan kapalı; yükseltmedeki
geniş müşteri erişimleri inceleme bitene kadar aktif; mal kabulünde tedarikçi parti zorunlu.

**Yükseltmede yöneticinin yapması gerekenler:** (1) Müşteri erişimleri ekranında "inceleme bekliyor" kayıtları karara
bağlamak; (2) Roller ekranında "Şablondan farklı" uyarılarını incelemek (ör. UAT kopyasında depo rolü eski bir ara
şablondan kaldığı için otomatik güncellenmedi ve hâlâ son sevk onayına sahip); (3) operatörlere makine ve vardiya
atamak (vardiyası tanımsız operatör teknik bilgiyi göremez).

---

# Operasyon genişletmesi — 26 Ekim 2026 (güncel)

Branch: `feature/mimcor-operasyon-genisletme` (kaynak `design/mimcor-demo-2026-10-30` @ `5bc44c2`; merge/canlı yayın yok).
Dört aşama ayrı commit'lerde: yönetilebilir yetki/kapsam + İK + müşteri erişimi + müşteri kartı; iş emri/sipariş yaşam
döngüsü, ustabaşı planı, vardiya devri, makine talimatı; düzeltme talebi/onayı, kalite, 12 inceleme bulgusu;
sevkiyat aşamaları ve QR yükleme kontrolü, geri alma, iade, etiket, stok seviyesi, iş listeleri.

**Doğrulama:** 248/248 Django testi; 6/6 tarayıcı testi (Demo A/B, sipariş ürün filtresi, 4 saha senaryosu; 1440 px ve
390 px **emülasyon — fiziksel telefon testi yapılmadı**). **(26 Ekim itibarıyla) tarayıcı testleri CI'da çalışmıyordu** — 27 Ekim düzeltme branch'inde ayrı CI işi eklendi; o tarihte CI yalnız Django testleri,
`makemigrations --check` ve `seed_demo` çalıştırır. Yükseltme (orijinal demo DB kopyası, UAT DB kopyası) ve temiz kurulum
ayrı ayrı denendi.

Yetki matrisi, müşteri erişim kuralları, veri modeli/migration'lar, madde madde durum listesi (tamam/kısmi/yapılmadı),
bulgu kanıtları, UAT senaryoları, varsayımlar ve riskler: **[docs/operasyon-genisletme.md](docs/operasyon-genisletme.md)**.

Yeni ortam değişkenleri (`.env.example`): `APP_BASE_URL` (QR taban adresi), `CORRECTION_SELF_APPROVAL`, `MEDIA_ROOT`.
Demo kullanıcıları (`seed_demo`, kurgusal): 1001 yönetici, 2001/2002 satış, 3001 planlama, 4001 ustabaşı (V1),
4002 üretim yetkilisi, 4003 operatör (V1), 4004 kalite, 5001 depo, 5002 ambar yetkilisi, 6001 İK.

Aşağıdaki "Bilinen eksikler" listesindeki şu maddeler bu çalışmayla **giderildi**: sipariş/kalem/iş emri iptali ve kalem
miktar düzeltmesi; rezervasyonun kısmi azaltılması; işlem geçmişinde teknik alan adları (yeni alanlar Türkçe etiketli);
adet/rulo üretim özetinin kutudan türetilmesi. Birim sınırı (rezervasyon/sevk yalnız kutu) **sürüyor**.

---

# UI/UX teslim güncellemesi — 26 Ekim 2026

Tasarım branch’i: `design/mimcor-demo-2026-10-30`. Kaynak `ee86192` korunarak devam edildi.

Mevcut Django şablonları yenilendi: masaüstü yan menü, mobil gezinme, role göre gerçek iş listeleri,
ayrı üretim/rezervasyon/sevk miktarları, görünür tartım/blokaj uyarıları, stok işlemi kontrol penceresi,
form hataları/etiketleri, Türkçe işlem geçmişi ve ana operasyon listelerinde sayfalama.
Servisler, yetki matrisi ve veritabanı şeması değişmedi.

**Doğrulama:** başlangıç 114/114; son durumda 123/123 Django testi ve 2/2 tarayıcı testi.
Tarayıcı testlerinin her biri Demo A + B’yi 1440 px veya 390 px genişlikte çalıştırır.
Kısmi sevkiyat, aynı POST tekrarı, kayıt sonrası bağlantı kaybı, QR ve yetkisiz erişim de kontrol edilir.
Bu bir **mobil ekran emülasyonudur; fiziksel telefon testi yapılmadı**.

[Ekran görüntüleri, komutlar, test kapsamı ve kalan sınırlamalar](docs/ui-ux-dogrulama.md).

Aşağıdaki önceki devir notu tarihsel backend referansıdır. Güncel UI açısından iki madde artık çözülmüştür:
- İşlem geçmişinin teknik alan/durum etiketleri sunum katmanında Türkçedir.
- Sipariş/iş emri/palet/sevkiyat listelerinde 30 kayıtlık sayfalama vardır.

Ek bulgular: adet/rulo üretim özeti alttaki selector’da kutu hareketlerine dayanabildiği için arayüzde gösterilmez;
genel birim hesabı ayrı backend işi olarak raporlandı. Rezervasyon iptali hata görünümü artık gerekçeyi ve
işlem anahtarını korur; mevcut servis kuralları değiştirilmedi. Özel iş emri/bobin/reçete şablonlarının gizli işlem
anahtarı render’ı `hidden_fields` ile güvence altına alındı.

---

# Codex devir notu

Bu belge, UI/UX ve görsel tasarım aşamasını devralacak geliştirici (Codex) içindir.
Durum tarihi: 26 Ekim 2026. Hedef: 30 Ekim 2026 demo.

## Çalışan özellikler (test edildi)

- **Giriş / yetki**: personel no + kişisel parola, 5 başarısız denemede 15 dk kilit, 30 dk hareketsizlikte sunucu
  tarafında oturum kapanışı, 12 saat mutlak oturum süresi. Roller ve eylem yetkileri `accounts/permissions.py` (`MATRIX`).
  Yetki hem view'da (`require_perm` / `check`) hem servis fonksiyonunda kontrol edilir.
- **Temel tanımlar**: müşteri, ürün kodu (tek müşteriye bağlı, müşteri sonradan değiştirilemez), makine (4 tür, kayıt
  olarak eklenir), hammadde/katkı tanımı, fiziksel malzeme lotu ve lot hareketleri. Silme yok; gerekçeli pasife alma.
- **Reçete**: değişmez revizyonlar (Rev.1, Rev.2 …); değişiklik = yeni revizyon + gerekçe.
- **Sipariş → planlama → iş emri**: sipariş kodu ilk kayıtta; planlama teknik onayı revizyonu kaleme sabitler; iş emri
  operasyon sırası + makine ataması (ilk operasyon extruder); stok için iş emri siparişsiz.
- **Ustabaşı**: üretimi başlat, operasyon başlat/tamamla, malzeme çıkışı/iadesi, bobin kaydı (extruder kaynaksız; sonraki
  operasyonlar kaynak bobinden kısmi tüketimle), bobin soy ağacı, palet oluştur/palete ekle, iş emri kapanışı (malzeme dengesi).
- **Depo**: palet kabulü (brüt zorunlu, daralar opsiyonel), yeniden tartım, ölçülen/hesaplanan/tahmini ayrımı,
  başka palete aktarım, gerekçeli sayım, hasarlı ürün ayırma, kalite durumu, rezervasyon, sevkiyat.
- **QR / etiket**: QR yalnız `/q/<tahmin edilemez anahtar>/` bağlantısı; okutma girişe yönlendirir ve kaydı açar.
  Etiket yeniden basımı yalnız basım kaydı oluşturur.
- **Satış ilerleme**: gerçekleşen miktar, sevk edilen, aşama, son güncelleme (salt okunur).
- **İşlem geçmişi**: her kayıt sayfasının altında kullanıcı, zaman, eski → yeni değer ve gerekçe.
- **Mükerrer istek koruması**: her form/düğmede tek kullanımlık `idem_key`; aynı anahtarla gelen ikinci istek bekler ve
  hareket oluşturmadan ilk sonuca yönlenir. Ek olarak JS düğmeyi kilitler.
- **Demo A ve B** uçtan uca tarayıcıda (Playwright, Pixel 5 ekranı) çalıştırıldı; adımlar `docs/demo-senaryolari.md`.

## Test sonuçları

`python manage.py test` → **114 test, hepsi başarılı** (PostgreSQL 16, ~16 sn). Bunların 4'ü gerçek eş zamanlılık
testidir (`warehouse/tests/test_concurrency.py`, aşağıda).
Kapsanan başlıklar: yetkisiz işlem (view + servis), personel no tek başına giriş, kilit ve oturum zaman aşımı;
farklı ürünü aynı palete koyma (ekleme ve aktarım); negatif stok (palet, lot, bobin) ve atomik geri alma; mükerrer
sevkiyat (ikinci onay, aynı form anahtarı, rezervasyonun iki sevkiyata bölünmesi); kısmi sevkiyat 40→10→30 aynı palet;
rezervasyonun fiziksel stoğu düşürmemesi; uygun olmayan ürün sevki; reçete revizyonu ve iş emrinin revizyonu koruması;
eksik dara (sıfır sayılmama, “Palet hariç”, net yalnız tüm daralar bilinince); içerik değişince yeniden tartım; etiket
yeniden basımı; QR’ın hareket oluşturmaması; iş emri kapanış dengesi; tüm rollerde sayfaların açılması.

### Kod incelemesi düzeltmeleri (d40f3b4 sonrası)

| Açık | Düzeltme | Testler |
|---|---|---|
| Aynı kaynak bobin birden fazla satırda seçilebiliyordu (100 kg’dan 60+60 tüketim geçiyordu) | Form ve `record_coil` mükerrer kaynağı reddeder; kaynaklar tek sorguda pk sırasıyla kilitlenip güncel kalan miktarla doğrulanır | `DuplicateSourceCoilTests` |
| Üretimden bobinsiz palet açılabiliyordu | `create_pallet_from_production` / `add_production_to_pallet` en az bir bobin ister; yalnız iş emrinin son operasyonundan, kullanılabilir, blokesiz, palete bağlanmamış bobin; tekrar yok. Depo aktarım/bölme işlemleri etkilenmez | `ProductionPalletRequiresCoilTests` |
| kg/adet/rulo kaleme kutu sevk edilip kalem “sevk edildi” olabiliyordu | `reserve`, `add_shipment_line`, `confirm_shipment` kutu dışı kalemi reddeder (önceden oluşmuş uyumsuz kayıtlar dahil); kalem durumu yalnız kutu kalemlerinde güncellenir | `UnitLimitTests` |
| Palete alınmış bobin blokelense de palet “uygun” olduğu için sevk ediliyordu | Sevk anında `coil_holds()` palet ve kaynak soyundaki blokeli bobinleri arar; varsa sevk reddedilir. Bobin blokajı/kaldırılması etkilenen paletleri kilitleyip geçmişe gerekçeyle yazar; palet kalite alanına dokunmaz | `CoilBlockShipmentTests` |

Bu 26 yeni testin 17'si d40f3b4 kodunda çalıştırıldığında başarısız oluyor (düzeltmeyi gerçekten yakalıyorlar).

### Eş zamanlılık testleri

`TransactionTestCase`; her thread ayrı PostgreSQL bağlantısı. Sıra zamanlamaya değil koşula dayanır: A hedef satırı
kilitleyip bekler, ana thread B'nin gerçekten kilit beklediğini `pg_stat_activity` üzerinden görür, sonra A devam eder.
Her bekleme süre sınırlı (20 sn), thread hataları ana teste aktarılır, `lock_timeout` 10 sn; iş kuralı hatası
(`BusinessRuleError`) ile teknik hata (kilitlenme, IntegrityError vb.) ayrı doğrulanır.

- Aynı sevkiyatın eş zamanlı iki onayı → tek stok düşümü, ikinci istek “zaten sevk edildi”.
- Aynı form anahtarıyla eş zamanlı iki gönderim → biri işlenir, diğeri “daha önce kaydedildi”; tek hareket.
- Aynı 100 kg kaynaktan eş zamanlı iki 60 kg tüketim → biri başarılı, diğeri iş kuralı hatası; kalan 40 kg, tek bağlantı.
- Bobin blokajı ile sevk onayı yarışı → blokaj önce kilitlerse sevk bekler ve blokajı görerek reddedilir; stok değişmez.

### Kilit sırası (değiştirirken korunmalı)

- Paletleme: iş emri → bobin(ler) → palet. Bobin blokajı: bobin → etkilenen paletler (pk sırası).
- Sevk onayı: sevkiyat → paletler (pk sırası) → rezervasyonlar; bobinler kilitlenmez, okunur.
- Bobin kaydı: iş emri → operasyon → kaynak bobinler (pk sırası).
Bu sıra döngü oluşturmaz; blokaj ve sevk birbirini bekleyerek serileşir.

## UI/UX için mimari notlar

- İş mantığı yalnız `*/services.py` içinde. View'lar ince; şablonlarda hesap yok. Şablonları özgürce yeniden düzenleyebilirsiniz.
- Ortak parçalar: `templates/base.html`, `templates/partials/` (`form_fields.html`, `field.html`, `history.html`,
  `confirm_button.html`), stiller `core/static/css/app.css` (renkler `:root` değişkenlerinde), JS `core/static/js/app.js`.
- **Korunması gerekenler**:
  - Her POST formunda `idem_key` gizli alanı olmalı. ModelForm/Form'lar bunu `IdemForm` üzerinden otomatik ekler;
    tek düğmelik formlarda `{% idem_input %}` etiketi (bkz. `partials/confirm_button.html`) kullanılır.
    Aynı sayfadaki iki form aynı anahtarı paylaşmamalı.
  - Yetkiye bağlı düğmeler `{% if can.<eylem_adi> %}` ile gösterilir (nokta yerine alt çizgi, ör. `can.shipment_manage`).
    Gizlemek yalnız kolaylıktır; asıl kontrol sunucuda.
  - Başarı mesajı yalnız kayıt tamamlandıktan sonra gelir; hata durumunda “Kaydedilmedi: …” gösterilir. Bu davranışı
    JS ile “iyimser” başarı göstererek bozmayın.
  - Ağırlık satırlarındaki etiketler (“Palet hariç ağırlık …”, “Net ürün ağırlığı”) ve kaynak (Ölçülen/Hesaplanan/
    Tahmini) servis tarafından üretilir; tasarımda bu ayrımı görünür tutun.
- Durum rozetleri `status status-<değer>` sınıfını kullanır; değerler model `TextChoices` değerleridir.
- Menü şu an `<details>` ile çalışır (JS gerektirmez).

## Bilinen eksikler (çalışıyormuş gibi sunulmamalı)

- Sipariş/kalem iptali, kalem miktarı düzeltme ve iş emri iptali için ekran ve servis yok (durum değerleri tanımlı).
- **Geçici sınır – birim:** rezervasyon ve sevkiyat yalnız **kutu** birimli kalemlerde yapılabilir. kg/adet/rulo
  kalemleri sunucuda reddedilir (sipariş sayfasında uyarı görünür); otomatik birim çevirme yoktur ve bu kalemler
  “sevk edildi” durumuna geçmez. kg kalemlerde gerçekleşen üretim (son operasyon bobin ağırlıkları) gösterilir.
  Genel birim desteği ayrı bir iş olarak ele alınmalı.
- Rezervasyon kısmi iptal edilemez (tamamı iptal; kısmen sevk edilmişse kalan serbest bırakılır).
- Palet içinde parti/lot karışımı izlenmez; palet kaynağı tek iş emri alanıyla tutulur, aktarımla gelenler hareketlerden izlenir.
- **Bobin blokajı ihtiyatlıdır:** palete alınmış bir bobin (veya onun kaynak zincirindeki ara bobin) blokeliyse,
  o palet ve aktarım/hasar ayırma ile ondan kutu almış tüm paletler sevkte bekletilir. Model, aktarılan kutuların
  hangi bobinden geldiğini ayırmadığı için sağlam kutular da beklemeye girebilir; bu bilinçli olarak güvenli taraftır.
  Engel palet kalite alanına yazılmaz, sevk anında hesaplanır: bobin blokajı kalkınca kendiliğinden kalkar, paletin
  bağımsız blokesi ise korunur. Rezervasyon (fiziksel stok değiştirmediği için) bu durumda engellenmez; sevk satırı
  ekleme ve sevk onayı engellenir.
- Üretimden palet oluşturmak/palete eklemek için en az bir bobin seçimi zorunludur.
- Hatalı giriş kilidi süreç içi bellekte (locmem cache) tutulur; birden çok süreçli kurulumda paylaşılan cache gerekir.
- Canlı kurulum yapılandırması yok: `DJANGO_DEBUG=0` için `collectstatic` + statik dosya sunucusu (ör. whitenoise/nginx),
  HTTPS ve uygulama sunucusu (gunicorn) eklenmeli.
- Parola sıfırlama akışı yok; yönetici kullanıcı ekranından yeni parola belirler.
- İşlem geçmişinde değişen alan adları teknik anahtar olarak görünür (ör. `box_count`); eylem adları Türkçeleştirildi.
- Listelerde sayfalama yok (ilk 200–300 kayıt).
- Etiket tarayıcı yazdırma diyaloğuyla basılır; etiket yazıcısı entegrasyonu yok.
- Kapsam dışı (bilinçli): muhasebe, Mikro, e-belge, terazi/sensör, otomatik çizelgeleme, maliyet, çevrimdışı kuyruk.

## Varsayımlar

1. Palet içerik birimi kutudur; rezervasyon ve sevk kutu adediyle yapılır.
2. Kalite kararını (Uygun / Kontrol bekliyor) üretim-ustabaşı ve yönetici verir; depo yalnız **blokeye** alabilir.
3. “Net ürün ağırlığı” yalnız palet, kutu ve masura daralarının tamamı girildiğinde (açıkça 0 dahil) gösterilir;
   kutu darası kutu başınadır ve tartım anındaki kutu adediyle çarpılır.
4. Stok paleti siparişe ayrılırken palet ürün kodu kalem ürün koduyla aynı olmalıdır (müşteri ayrımı korunur).
5. İlk operasyon extruder olmalıdır; operasyon n, operasyon n−1 başlamadan başlayamaz ve ondan önce tamamlanamaz.
6. Dönüştürmede çıktı bobini, tüketilen kaynak toplamından ağır olamaz; aradaki fark kapanışta fire olarak girilir.
7. İş emri kapanış dengesi: net verilen malzeme ≈ sağlam + blokeli + ara stok bobin + fire, tolerans
   `WORK_ORDER_BALANCE_TOLERANCE_PCT` (vars. %3); aşılırsa gerekçe zorunlu. %3 yalnız demo için seçilmiş,
   `.env` ile ayarlanabilir bir varsayılandır; onaylanmış bir fabrika standardı değildir.
8. Sipariş kalemine iş emri açılabilmesi için planlama teknik onayı gerekir; stoktan karşılanan kalem için gerekmez.
9. Hareketsizlik zaman aşımı 30 dk, mutlak oturum 12 saat (`.env` ile değişir).
