# Önce / sonra — kullanım kolaylığı

Kurgusal `seed_demo` kayıtları; Chromium 1440×900 masaüstü ve 390×844 telefon emülasyonu.
`once-*`: değişiklik öncesi kaynak `8e689b6`, Docker uygulaması.
`sonra-*`: bu tasarım dalı; uygulama incelemesi ve otomatik tarayıcı testleri.

Karşılaştırma için:

| Ekran | Önce | Sonra |
|---|---|---|
| Sipariş miktar/teslim | `once-siparis-adim2-{genişlik}.png` | `sonra-siparis-adim2-{genişlik}.png` |
| Teknik istek | `once-teknik-talep-{genişlik}.png` | `sonra-teknik-talep-{genişlik}.png` |
| Reçete düzenleme | `once-recete-{genişlik}.png` | `sonra-recete-{genişlik}.png` |
| Yeni numune | `once-kalite-numuneler-yeni-{genişlik}.png` | `sonra-kalite-numuneler-yeni-{genişlik}.png` |
| İş emirleri | `once-ustabasi-is-emirleri-{genişlik}.png` | `sonra-ustabasi-is-emirleri-{genişlik}.png` |
| Palet | `once-depo-paletler-1-{genişlik}.png` | `sonra-depo-paletler-1-{genişlik}.png` |

Otomatik test kanıtları ayrıca `sonra-numune-kaynak`, `sonra-numune-durum`, `sonra-numune-bos`,
`sonra-siparis-teknik-hata` öneklerini kullanır. Ekran yüksekliğinden uzun formlar tam sayfa kaydedilmiştir;
masaüstü sabit işlem alanı görüntü alınan anda ekranın altındaki konumdadır.

Bunlar gerçek telefon/kullanıcı testi veya fabrika verisi değildir. Tarih girdisinin görünümü işletim sistemi
ve tarayıcı diline bağlıdır; Django'ya ISO tarih gider, uygulama tarih gösterimleri Türkçedir.
