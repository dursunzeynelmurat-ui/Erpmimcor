# Demo senaryoları

Ön koşul: `python manage.py migrate && python manage.py seed_demo` çalıştırılmış ve sunucu açık
(`python manage.py runserver 0.0.0.0:8000`). Parola: `.env` içindeki `DEMO_USER_PASSWORD`.
Her rol için ayrı tarayıcı / gizli pencere kullanmak en pratik yoldur. Tüm veriler kurgusaldır.

| Personel no | Rol |
|---|---|
| 2001 | Satış |
| 3001 | Planlama |
| 4001 | Üretim / Ustabaşı |
| 5001 | Depo |
| 1001 | Yönetici (her şeyi yapabilir) |

## A — Sipariş üretimi

1. **Satış (2001)** → Ana sayfa → *Yeni sipariş*. Müşteri `M-ORNEK`, ürün `ORN-PF-030`, miktar `20`, birim Kutu → *Siparişi kaydet*.
   Sipariş kodu (`SIP-2026-…`) kayıt anında oluşur. Başka müşterinin ürün kodunu seçerseniz kayıt reddedilir.
2. **Planlama (3001)** → *Planlama* → bekleyen kalemi açın → güncel reçete revizyonunu görün → *Teknik bilgileri ve reçeteyi onayla*.
3. Aynı ekranda kalemin yanında *İş emri oluştur*: 1. operasyon `EXT-01` (extruder zorunlu), 2. operasyon `PER-01` → *İş emrini oluştur*.
   Onaylanan reçete revizyonu iş emrine sabitlenir.
4. **Ustabaşı (4001)** → *İş emirleri* → iş emri → *Üretimi başlat* (extruder operasyonu başlar).
5. *Malzeme çıkışı*: lot `HM-LDPE…`, 70 kg.
6. *Extruder: bobin kaydet*: 68 kg (ağırlık kaynağı “Ölçülen”).
7. 2. operasyonda *Operasyonu başlat*, 1. operasyonda *Operasyonu tamamla*.
8. *Perfore: bobin kaydet*: kaynak bobin = extruder bobini, tüketilen 68 kg, çıktı 67,5 kg. (Kaynak kısmen de tüketilebilir.)
9. *Palet oluştur / ekle*: 20 kutu, perfore bobinini işaretleyin → palet açılır (`PLT-…`), kalite “Kontrol bekliyor”.
   *Etiket / QR* ile etiketi basın (yeniden basım yeni palet açmaz).
10. Perfore operasyonunu tamamlayın → *İş emrini kapat*: fire 2,5 kg, “kalan malzeme iade edildi veya kalmadı” işaretli.
    Denge toleransı (±%3) aşılırsa gerekçe istenir.
11. **Depo (5001)** → *Depo kabulü* → palet → *Depo kabulü ve tartım*: brüt 89,4 kg, palet darası 21 kg → *Kabul et*.
    Ekranda “Palet hariç ağırlık (kutu, masura darası bilinmiyor)” görünür; kesin net ağırlık denmez.
12. **Ustabaşı (4001)** → paletin *Kalite durumu* → “Uygun” + gerekçe. (Depo yalnız blokeye alabilir.)
13. **Satış (2001)** veya **Depo** → palet → *Siparişe ayır*: kalem seçin, 20 kutu. Fiziksel stok değişmez.
14. **Depo (5001)** → sipariş sayfası → *Sevkiyat oluştur* → rezervasyonu satır olarak ekleyin → *Sevk et*.
15. **Satış (2001)** → sipariş veya *Üretim ilerlemesi*: gerçekleşen 20 kutu, sevk edilen 20 kutu, aşama ve son güncelleme zamanı.

## B — Stoktan kısmi sevkiyat

`seed_demo`, `DEN-PF-030` ürünü için **müşteri stoğu amaçlı** üretilmiş, kabul edilmiş, kalitesi uygun,
40 kutulu bir palet (`PLT-2026-00001`) oluşturur (iş emri siparişsiz, yapay sipariş yok).

1. **Satış (2001)** → *Yeni sipariş*: müşteri `M-DENEME`, ürün `DEN-PF-030`, 10 kutu.
2. Sipariş sayfasında *Stoktan karşılanabilir paletler* bölümünde 40 kutulu palet görünür → palete gidin.
3. *Siparişe ayır*: yeni siparişin kalemi, 10 kutu. Palette “Fiziksel stok 40, Siparişe ayrılan 10, Serbest 30”.
4. **Depo (5001)** → sipariş sayfası → *Sevkiyat oluştur* → rezervasyonu ekleyin (10 kutu) → *Sevk et*.
5. Palet sayfası: aynı palet numarası, **30 kutu**; hareketlerde “Sevkiyat −10 → 30” ve sevkiyat bağlantısı (`SVK-…`);
   işlem geçmişinde kim/ne zaman; ağırlık durumu “Yeniden tartım bekliyor” (önceki tartım geçmişte duruyor).

## Gösterilebilecek kontroller

- *Sevk et* düğmesine çift tıklama veya aynı formu yeniden gönderme: tek sevkiyat hareketi oluşur, “daha önce kaydedilmişti” mesajı görünür.
- Kalitesi “Kontrol bekliyor” veya “Blokeli” palet sevk edilemez.
- Palet → *Başka palete aktar*: farklı ürün kodlu hedef palet numarası girilirse reddedilir; aynı ürün için yeni/var olan palete aktarım kaynak ve hedefte birlikte görünür.
- *Sayım düzeltmesi* ve *Hasarlı ürün ayır* gerekçe ister; hasarlı kutular blokeli yeni palete alınır.
- Ürün → *Reçete* → *Değiştir (yeni revizyon)*: Rev.3 oluşur; mevcut iş emirleri eski revizyonu gösterir.
- Satış kullanıcısı iş emri ekranında “Üretimi başlat”/bobin düğmelerini görmez; adres çubuğundan doğrudan istekte 403 alır.
- QR bağlantısı (`/q/<anahtar>/`) giriş yapmamış kullanıcıyı giriş sayfasına yönlendirir; okutmak hareket oluşturmaz.
