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

`python manage.py test` → **84 test, hepsi başarılı** (PostgreSQL 16, ~11 sn).
Kapsanan başlıklar: yetkisiz işlem (view + servis), personel no tek başına giriş, kilit ve oturum zaman aşımı;
farklı ürünü aynı palete koyma (ekleme ve aktarım); negatif stok (palet, lot, bobin) ve atomik geri alma; mükerrer
sevkiyat (ikinci onay, aynı form anahtarı, rezervasyonun iki sevkiyata bölünmesi); kısmi sevkiyat 40→10→30 aynı palet;
rezervasyonun fiziksel stoğu düşürmemesi; uygun olmayan ürün sevki; reçete revizyonu ve iş emrinin revizyonu koruması;
eksik dara (sıfır sayılmama, “Palet hariç”, net yalnız tüm daralar bilinince); içerik değişince yeniden tartım; etiket
yeniden basımı; QR’ın hareket oluşturmaması; iş emri kapanış dengesi; tüm rollerde sayfaların açılması.

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
- Rezervasyon ve sevkiyat yalnız **kutu** biriminde. kg/adet/rulo birimli kalemlerde gerçekleşen miktar gösterilir
  (kg: son operasyon bobin ağırlıkları) ama stoktan ayırma/sevk kutu üzerinden yapılır.
- Rezervasyon kısmi iptal edilemez (tamamı iptal; kısmen sevk edilmişse kalan serbest bırakılır).
- Palet içinde parti/lot karışımı izlenmez; palet kaynağı tek iş emri alanıyla tutulur, aktarımla gelenler hareketlerden izlenir.
- Bobin kalite durumu ile palet kalite durumu ayrı yönetilir; birbirine otomatik yansımaz.
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
   `WORK_ORDER_BALANCE_TOLERANCE_PCT` (vars. %3); aşılırsa gerekçe zorunlu.
8. Sipariş kalemine iş emri açılabilmesi için planlama teknik onayı gerekir; stoktan karşılanan kalem için gerekmez.
9. Hareketsizlik zaman aşımı 30 dk, mutlak oturum 12 saat (`.env` ile değişir).
