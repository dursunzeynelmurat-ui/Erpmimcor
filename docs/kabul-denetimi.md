# Kabul denetimi — 28 Ekim 2026

Branch `fix/mimcor-kabul-denetimi` (kaynak `fix/mimcor-yetki-veri-tutarliligi` @ `ff01e32`). Yeni özellik eklenmedi;
mevcut sürümün işletme kurallarına uygunluğu doğrulandı, bulunan kabul engelleri düzeltildi. Merge, PR, force push
veya canlı yayın yapılmadı. Gerçek demo (`mimcor`) ve UAT (`mimcor_uat`) veritabanları değiştirilmedi; yükseltme ve
yönetici kontrolleri kopyalarda yapıldı. Hiçbir gerçek kullanıcının erişimi otomatik kaldırılmadı veya genişletilmedi.

> **Testlerin geçmesi işletme onayı değildir.** Aşağıdaki sonuçlar kodun yazılı kurallara göre davrandığını gösterir;
> kuralların doğruluğu (bölüm 6) işletmenin kararıdır. **Gerçek telefonla test yapılmadı**; tarayıcı testleri 1440 px
> ve 390 px Chromium emülasyonudur.

## 1. Doğrulanan sorunlar ve etkileri

Her sorun önce başarısız bir regresyon testiyle kanıtlandı, sonra düzeltildi (bölüm 2).

| # | Sorun | Etki |
|---|---|---|
| A | İade, araç çıkışı yapılmamış ("sevk onaylı") sevkiyata da alınabiliyordu; bu sevkiyat sonra geri alınabiliyordu. | Teslim edilmemiş kutular "iade" diye ayrı palete girer, sevk geri alınınca aynı kutular asıl palete de döner: stok iki kez artar. |
| B | Yerine gönderim kalemi, kalem değişikliği ekranından iade hakkının üzerine çıkarılabiliyordu (2 kutu iade → 3 kutu yerine gönderim). | Hak edilen miktar dolaylı aşılır; fazlası ücretsiz sevk olur. |
| C | "Siparişi kopyala" yerine gönderim kalemlerini normal satış kalemi olarak kopyalıyordu. | Yerine gönderim sessizce yeni satışa dönüşür (ör. 10 + 2 kutu yeni sipariş). |
| D | Vardiya devri teslim alındıktan sonra eski vardiya operatörü malzeme çıkışı / iadesi, bobin, palet ve kapanış sayfalarını adresle açabiliyordu (kayıt yapamıyordu; POST zaten reddediliyordu). | Malzeme çıkışı formunun lot listesi reçetede olmayan lotları "REÇETE DIŞI" diye işaretlediği için reçete bileşimi (oran değil) dolaylı görünür; kapanış sayfası denge özetini gösterir. |
| E | İş emri başlığı en/kalınlık bilgisini, aktif teknik erişimi kapanmış eski vardiya operatörüne de gösteriyordu. | Talimatta "teknik bilgi" sayılan ölçü, devirden sonra da açık kalıyordu (düşük etki). |
| F | Müşteride kalan net, yerine gönderilen ürün tekrar iade edilirse düşmüyordu (yerine gönderim iadesi sayılmıyordu). | Net teslim fazla görünür. |

Kanıt (düzeltme öncesi): `warehouse/tests/test_return_acceptance.py` 6 başarısız test (A: çıkış öncesi iade ve iade
varken geri alma; B; C; F); `production/tests/test_handover_secrecy.py` öncesi tarama — eski operatör için
`is_emri 200 ['600 mm', '30 µ']`, `malzeme_cikis 200`, `malzeme_iade 200`, `bobin 200`, `palet 200`, `kapanis 200`;
talimat/yazdır 404, plan 403, reçete 403 (bunlar zaten kapalıydı).

## 2. Düzeltmeler ve test kanıtı

| Commit | Düzeltme | Testler |
|---|---|---|
| `c647ba0` | İade yalnız "çıkış yapıldı" sevkiyata; iade kaydı olan sevkiyat geri alınamaz (eski sürümde alınmış iadeler için de); yerine gönderim kalemi `change_line` ile hak sınırını aşamaz (iade kaydı kilitlenir); `copy_order` yerine gönderim kalemlerini atlar; net, yerine gönderim iadesini düşer. Sevkiyat ekranında "İade al" yalnız çıkış yapılmış satırlarda. | `warehouse/tests/test_return_acceptance.py` (11): 10 sevk → 2 iade → 2 yerine gönderim → ilk satış 10, net 10; ikinci yerine gönderim reddi; ayrılmış / hazırlanmış / sevk onaylı / çıkışlı / sevki geri alınmış yerine gönderimlerin hakkı tüketmesi; iptalin hakkı bir kez serbest bırakması; blokeli iade paletinin açılmaması; ürün/müşterinin değişmemesi; PostgreSQL üzerinde eş zamanlı iki yerine gönderim (biri reddedilir). |
| `00a3f5f` | Üretim kaydı sayfaları GET'te de nesne kapsamlı `production.execute` ister (yoksa 403). İş emri başlığında ölçüler talimat yetkili saha kullanıcısına yalnız aktif teknik erişim varken; satış/depo/planlama görünümü değişmedi. | `production/tests/test_handover_secrecy.py` (5): devirden önce erişim; devirden sonra eski operatör için 12 adreste (iş emri, malzeme çıkış/iade, bobin, palet, kapanış, devir, plan, talimat, talimat yazdır, reçete, bobin detayı) karışım malzeme kodu, ölçü, "En (mm)", "Kalınlık" hiçbir yanıtta yok; talimat bas / teslim / bobin / malzeme POST'ları 403/404 ve kayıt oluşmaz; işlem geçmişi ve kendi bobin kaydı görünür; yeni vardiya yalnız atandığı makinenin talimatını görür; planlama ve yönetici erişimi sürer. |
| `809efeb` | Kod değişikliği yok (tolerans hesabı doğru çıktı). | `production/tests/test_balance_tolerance.py` (8): bölüm 4. |
| `d57980e` | `python manage.py yetki_denetimi` (salt okunur). | `accounts/tests/test_yetki_denetimi.py` (5): bölüm 3. |
| `fbf02ea` | `ui_checks/kopya_yonetici_kontrol.py` (yalnız "kopya" adlı veritabanında çalışır). | Bölüm 3.3. |

Tam takım ve tarayıcı sonuçları bölüm 8'de.

## 3. Yükseltilmiş kullanıcıların gerçek etkin yetkileri

### 3.1 Denetim komutu

```
python manage.py yetki_denetimi                       # aktif kullanıcılar, yalnız bulgusu olanlar
python manage.py yetki_denetimi --kullanici 2001      # etkin yetkiler: işlem: kapsam ← rol
python manage.py yetki_denetimi --kullanici 2001 --musteri M-ORNEK   # bu müşteriyi neden görüyor?
python manage.py yetki_denetimi --json
```

Rapor hiçbir kaydı değiştirmez (testte denetim kaydı, rol yetkisi, müşteri erişimi ve rol ataması sayıları öncesi ve
sonrası aynı). Bulgu düzeyleri:

- **KURAL** — iş kuralını aşıyor. Hassas işlemler (onay türleri, düzeltme onayı, yönetim, `recipe.view/change`,
  `shipment.undo`, `material.deviation`, `customer.access.manage`) yalnız başlangıç şablonunda onları içeren
  rollerden gelmelidir; örn. `shipment.approve/undo/exit` → yönetici ve ambar yetkilisi. Şablonda daha dar olan bir
  işlemin rolde "tüm kayıtlar" kapsamına genişletilmesi de KURAL'dır.
- **İNCELE** — yönetici kararı: inceleme bekleyen / taşınan / kaynağı belirsiz müşteri erişimi; aynı işlemin birden
  fazla rolden farklı kapsamla gelip VEYA ile genişlemesi (ör. operatör `instruction.view` makine+vardiya ← operator,
  vardiya ← ek rol).
- **ENGEL** — kişi işini yapamaz: vardiyası tanımsız veya makinesi atanmamış saha kullanıcısı; müşteri kapsamlı
  yetkisi olup geçerli erişimi olmayan satışçı.
- **BİLGİ** — şablondan farklı (özelleştirilmiş) rol veya özel rol. **Tek başına hata sayılmaz.**

Birleşik etkin yetki her kullanıcı için "işlem → kapsam ← sağlayan rol" olarak yazılır; müşteri erişimi kaynağı
(açan, yönetici, vekâlet, taşınan, belirsiz), inceleme durumu ve bitiş tarihiyle listelenir. Liste, detay, arama,
çıktı, doğrudan adres ve POST aynı `can/scoped/get_scoped_or_404` kontrolünden geçer; kapsam dışı kayıt 404 döner.

### 3.2 Kopyalardaki sonuç (yükseltme sonrası, değişiklik öncesi)

| Kopya | Bulgu |
|---|---|
| `mimcor_uat` → `kabul_mimcor_uat_kopya` (19 migration uygulandı) | **KURAL** 5001 Deniz Depo: `shipment.approve` depo rolünden geliyor (eski ara şablondan kalmış). **BİLGİ** depo rolü şablondan farklı (eksik `customer.document.view`, fazla `shipment.approve`). **İNCELE** 2001 Selin Satış: M-DENEME, M-KURGU, M-ORNEK erişimleri toplu taşınan, inceleme bekliyor. |
| `mimcor` → `kabul_mimcor_kopya` (28 migration uygulandı) | **İNCELE** 2001: aynı üç taşınan erişim. Depo rolü güncel şablonda (KURAL yok). |
| Temiz kurulum `kabul_temiz_kopya` (migrate + `seed_demo` ×2) | Bulgu yok (12 kullanıcı). |

Orijinal veritabanları denetimden sonra da aynı migration durumunda (UAT 19, demo 28 uygulanmamış migration).

### 3.3 Açık oturumda erişim daraltma (kopyada, gerçek tarayıcı)

`ui_checks/kopya_yonetici_kontrol.py`, kopyaya bağlı yerel sunucuda: hazırlık olarak planlama kalemi onaylar, depo
personeli 5 kutuluk sevkiyatı hazırlar ve okutur (servislerle). Sonra satışçı (2001) sipariş detayını ve kalem
değişiklik formunu, depo personeli (5001) sevkiyat sayfasını açık bırakır; yönetici (1001) tarayıcıdan müşteri erişim
incelemesinde 2001'in M-DENEME erişimini kaldırır ve Roller ekranında depo rolüne "Önerilen şablonu uygula" der.

| Kontrol | UAT kopyası | Demo kopyası |
|---|---|---|
| Satışçının açık formu gönderilince | 404, miktar değişmedi | 404, miktar değişmedi |
| Aynı oturumda sipariş detayı | 404 | 404 |
| Aynı oturumda sipariş araması | sonuç yok | sonuç yok |
| Depo personelinin açık "Sevk et" formu | 403, sevkiyat "hazırlandı"da kaldı | uygulanmaz (depo rolünde sevk onayı zaten yoktu; düğme görünmüyor) |
| Sonuç | 9/9 | 8/8 |

Sonraki denetim: UAT kopyasında KURAL kalktı, yalnız 2001'in kalan iki taşınan erişimi İNCELE. Demo kopyasında
oturum açabilmek için yalnız kopyadaki dört kullanıcıya rastgele geçici parola verildi (ekrana/dosyaya yazılmadı);
UAT kopyasında `.env` demo parolası kullanıldı. Aynı davranış testte de var
(`OpenSessionRevocationTests`): yetki her istekte veritabanından okunduğu için önbellek veya oturum yeniden açma
gerekmez.

### 3.4 Gerçek ortamda yöneticinin elle yapacakları (uygulanmadı)

Gerçek ortamda önce `python manage.py yetki_denetimi` çalıştırılmalı; aşağıdaki liste kopyalardan çıkan durumdur.

| Kişi / rol | Durum | Önerilen işlem (yönetici ekranı) |
|---|---|---|
| Depo rolü (UAT'de 5001 Deniz Depo ve bu rolü taşıyan herkes) | Son sevk onayı (`shipment.approve`) depo personelinde | Roller → Depo personeli → "Önerilen şablonu uygula" **veya** yalnız `shipment.approve` kaldırılır. Şablon uygulamak `customer.document.view` de ekler; bu istenmiyorsa elle düzenleyin. Etki: bu kişiler sevk onayı veremez, ambar yetkilisi verir. |
| 2001 Selin Satış | M-DENEME, M-KURGU, M-ORNEK toplu taşınan, inceleme bekliyor | Müşteri erişimleri → her biri için "Onayla" / "Süreli erişime çevir" / "Kaldır". Karar satış müdürünün. |
| Operatörler (gerçek ortamda varsa) | Vardiya veya makine tanımsızsa teknik bilgi ve üretim kaydı açılmaz (ENGEL) | İK ekranından vardiya, kullanıcı ekranından makine ataması. |
| Ek rol verilmiş kullanıcılar | VEYA ile genişleyen kapsam (İNCELE) | Genişleme bilinçli değilse ek rolü kaldırın. |

## 4. Malzeme dengesi ve tolerans

Denklem (kg; `production/services.closure_summary`, `close_work_order`):

```
fark = net verilen − (sağlam + blokeli + ara stok + fire)
sınır = net verilen × tolerans % / 100        (tolerans: WORK_ORDER_BALANCE_TOLERANCE_PCT, demo değeri 3)
```

| Kalem | Anlamı | Kaynağı | Çift sayım kontrolü |
|---|---|---|---|
| Giriş (mal kabulü) | Tedarikçiden lota giriş | `receive_lot` | İş emri dengesine girmez; yalnız lot bakiyesini artırır. |
| Çıkış = **net verilen** | Lottan bu iş emrine verilen malzeme (lotun gözünden çıkış, iş emrinin gözünden **girdi**) | `issue_material` | Yalnız kg birimli lotlar; litre/adet ayrı listelenir, kg'ye çevrilmez. |
| İade (malzeme) | Makine başında kalanın lota dönüşü | `return_material` | Net verilenden düşülür; verileni aşamaz. |
| Sağlam | Son operasyonun blokesiz bobin ağırlığı | bobin kayıtları | Ara bobinler sağlama eklenmez. |
| Blokeli | Son operasyonun blokeli bobinleri + ara operasyonların blokeli **kalan** kg'si | bobin kalite durumu | Her bobin bir kez, ya sağlam ya blokeli. |
| Ara stok | Ara operasyonların blokesiz **kalan** kg'si | `remaining_kg` | Sonraki operasyona tüketilen kısım kalan kg'den düşer; son bobine geçen ağırlık iki kez sayılmaz. |
| Fire | Kapanışta girilen fire kg | kapanış formu | Bir kez girilir; kapanış kaydında sabittir. |
| Yeniden işleme | Kodda ayrı bir kalem **yok**. Kırma/geri kazanılmış malzeme lota mal kabulüyle girip başka iş emrine verilirse o iş emrinin girdisi olur; üreten iş emrinde fire olarak kalır. | — | Aynı iş emri içinde çift sayım yok; geri kazanım için ayrı kural işletme kararıdır. |
| Müşteri iadesi (ürün) | Sevk sonrası iade paleti | `receive_return` | İş emri dengesini değiştirmez. |

**3,3 kg nereden geldi?** Önceki örnekte (100 kg verildi, 95 sağlam, 5 fire, fark 0) lottan iş emrine verilen miktar
düzeltme talebiyle 110 kg'a çekildi. Tolerans tabanı **düzeltilmiş net verilen** malzemedir: 110 × %3 = 3,3 kg; güncel
fark 110 − 100 = 10 kg > 3,3 kg. Önceki belgede "çıkış" lot çıkışını (iş emri girdisi) kastediyordu; çıktı değildir.
Taban çıktı veya fire büyüdükçe büyümez (110 kg çıktı / 100 kg girdi → sınır 3 kg, fark −10 kg).

Sınır testleri (`production/tests/test_balance_tolerance.py`, tolerans %3): 100 kg → sınır 3 kg (fark 3,000 gerekçesiz
kapanır, 3,001 gerekçe ister); çıktı 103 kg → −3 kg sınırda, 103,001 gerekçe; kayıtlı fire 7 kg → fark 3 (6,9 kg → 3,1
gerekçe); kısmi iade (100 − 20 = 80 kg → sınır 2,4 kg; 2,5 kg gerekçe); sıfır giriş → gerekçe zorunlu, kapanış kaydı 0
kg; kapanıştan sonra onaylı düzeltme 100 → 102 kg: güncel fark 2 kg ≤ 3,06 kg, değerlendirme gerekmez; 100 → 110 kg:
değerlendirme bekler. İlk kapanış kaydı her durumda değişmez; güncel denge ayrı gösterilir. Sınır dışı fark otomatik
kabul edilmez: kapanışta gerekçe, kapanıştan sonra yetkili değerlendirmesi (üretim yetkilisi/planlama) gerekir.

**%3 bir demo kuralıdır, fabrika standardı değildir**; gerçek değer ürün/işlem türüne göre işletmece belirlenmelidir.

## 5. Sorun çıkmayan kontroller

- Reçete/karışım sayfası yalnız `recipe.view` (planlama, yönetici); operatöre 403.
- Talimat görüntüleme, yazdırma sayfası ve teslim POST'u kapsam (makine **ve** sorumlu vardiya) + açık iş emri
  kontrolünden geçer; eski vardiyaya 404.
- İşlem geçmişinde karışım oranı yazılmıyor (iş emri geçmişi revizyon numarası gösterir).
- Operatörde `customer.document.view` ve `product.view` yok; müşteri belgesi indirme ve ürün sayfaları kapalı.
  Uygulamada reçete içeren başka bir dosya indirme veya JSON uç noktası yok (tek JSON uç noktası satışçının
  müşteri-ürün listesi).
- Sipariş listesi arama sonucu kapsamlı (arama kutusu yalnız yazılan metni geri gösterir).
- Paralel iade ve paralel düzeltme onayı (önceki sürümden) PostgreSQL'de tek hareket üretir.
- Tolerans tabanı (bölüm 4): kod doğru; değişiklik yapılmadı.
- `makemigrations --check`: değişiklik yok; bu turda migration oluşturulmadı.

## 6. Korunan davranışlar ve karar bekleyenler

Korunan (değiştirilmedi): karışım/malzeme farkı her zaman eşdeğerlik onayı ister; fazla teslim izni varsayılan 0;
fiziksel araç çıkışı yalnız yönetici ve ambar yetkilisi; vardiyası tanımsız operatör teknik bilgi görmez; kişi kendi
düzeltme talebini onaylayamaz (`CORRECTION_SELF_APPROVAL=0`).

Karar verilmiş gibi değiştirilmeyenler — mevcut davranış ve günlük engel örneği:

| Konu | Mevcut davranış | Günlük engel örneği |
|---|---|---|
| Ambar/depo varsayılan düzeltme talep hakkı | Depo personeli, ambar yetkilisi ve ustabaşı şablonlarında `correction.request` yok. | Depo sayımında paletin 40 değil 38 kutu olduğunu gören personel talep açamaz; üretim yetkilisi veya planlamaya söyler, onlar açar. |
| Tedarikçi parti / irsaliye no | Mal kabulünde zorunlu. | Malzeme irsaliyesiz veya parti numarası okunmayan çuvalla gelirse lot girilemez; üretim bekler ya da ambar geçici bir referans yazar (izlenebilirlik zayıflar). |
| Belgesi beklenen mal kabulü | "Belge bekliyor" durumu yok; kabul edilen lot hemen kullanılabilir, kabul edilmeyen hiç girilemez. | Analiz sertifikası sonradan gelecek bir partiyi "girildi ama kullanılamaz" diye tutmanın yolu yok. |
| Geciken devir / yeniden atama | Devir teslim alınmadıkça sorumluluk eski vardiyada kalır; süre sınırı yok. Teslimi operasyonda devir yetkisi olan (ustabaşı, üretim yetkilisi) veya devralan vardiyada o makineye atanmış kişi alır; devreden kendi devrini alamaz. Devir teslim alınmışsa planlama ekranındaki vardiya değişikliği sorumluluğu değiştirmez. | Gece operatörü geldiğinde gündüz devri yazmadan çıktıysa gece operatörü talimatı göremez ve kayıt yapamaz; ustabaşı devri kaydedip teslim almalıdır. |

## 7. Kalan sınırlar

- Basılmış talimat, indirilen dosya veya ekran görüntüsü geri alınamaz; sistem yalnız sonraki erişimi kapatır.
  Basımlar talimat geçmişinde kişi/saat ile kayıtlıdır.
- Gerçek telefonla test yapılmadı (yalnız 390 px emülasyon).
- Kopya kontrolü iki küçük veritabanında (5 kullanıcı) yapıldı; gerçek ortamda kullanıcı sayısı ve özel roller
  farklı olabilir — önce denetim komutu çalıştırılmalı.
- Yeniden işleme (kırma/geri kazanım) için ayrı bir dengeleme kuralı yok (bölüm 4).
- İade kuralı artık araç çıkışını şart koşar: sevk onaylı ama çıkışı kaydedilmemiş sevkiyatta yanlışlık varsa
  sevk geri alınır (ambar yetkilisi), iade alınmaz.

## 8. Test sonuçları

Yerel, 28.10.2026, PostgreSQL 16, Python 3.13 (CI 3.12):

| Kontrol | Sonuç |
|---|---|
| `python manage.py test` (seri, tam takım) | 330/330 geçti (önceki tur 301; bu turda +29 test) |
| Tarayıcı: `ui_checks.browser_checks`, `order_product_filter_checks`, `field_scenarios_checks` (Chromium headless, 1440 px + 390 px emülasyon) | 6/6 geçti (saha senaryosu 4 artık "çıkışı yapılmış sevkiyattan iade" akışını kullanıyor; değişiklik gerekmedi) |
| `makemigrations --check --dry-run` | Değişiklik yok |
| Yükseltme: UAT kopyası (19 migration) ve demo kopyası (28 migration) | Hatasız; denetim ve tarayıcı kontrolü bölüm 3 |
| Temiz kurulum: migrate + `seed_demo` ×2 | Hatasız; ikinci çalıştırma tekrar kayıt üretmedi; denetimde bulgu yok |
| GitHub Actions | Son commit için iki iş (Django, Chromium) teslim raporunda commit bazında verilir |

Düzeltme öncesi başarısız test çıktıları ve kopya denetim raporları oturum çalışma alanında saklandı; depoya
eklenmedi (yerel yol ve veritabanı adları içerir).

## 9. Manuel kabul senaryosu (20–30 dk)

Ortam: kopya veya UAT; başlangıç verisi `seed_demo` (kurgusal). Parolalar ortam yöneticisinden alınır, bu belgede yok.
Her adımda "beklenen" sağlanmazsa adım numarasıyla not alın.

| # | Rol (personel no) | Başlangıç verisi | İşlem | Beklenen |
|---|---|---|---|---|
| 1 | Satış 2001 | 2001'in erişimi M-ORNEK, M-DENEME | Siparişler listesini açın; arama kutusuna M-KURGU siparişinin numarasını yazın. | Yalnız M-ORNEK/M-DENEME siparişleri; aramada sonuç yok. |
| 2 | Satış 2001 | M-KURGU sipariş detayının adresi (2002'den alın) | Adresi doğrudan açın. | "Bulunamadı" (404). |
| 3 | Satış 2001 + Yönetici 1001 | 2001'de M-DENEME sipariş kalemi değişiklik formu açık | Yönetici: Müşteri erişimleri → 2001 / M-DENEME → "Erişimi kaldır" → önizle → uygula. Satışçı açık formu kaydeder. | Satışçıya 404; kalem miktarı değişmez; sipariş listesinden kaybolur. Sonra yönetici erişimi yeniden verir. |
| 4 | Depo 5001 | Hazırlanmış, okutulmuş sevkiyat | Sevkiyat sayfasını açın. | "Sevk et" düğmesi yok; "Son sevk onayını ambar yetkilisi verir" yazar. |
| 5 | Ambar 5002 | Aynı sevkiyat | "Sevk et", sonra "Araç çıkışı". Depo 5001 ile araç çıkışını deneyin. | Ambar onaylar; depo çıkış kaydedemez. |
| 6 | Ustabaşı 4001 | EXT-01'de V1 vardiyasına planlı, başlamış iş | Operasyonda talimatı basın; 4003 (V1) talimatı görür. | 4003 karışımı ve ölçüleri görür. |
| 7 | Ustabaşı 4001 → Operatör 4005 (V2) | Adım 6 | Ustabaşı V2'ye devir yazar; 4005 devri teslim alır. | 4005 talimatı görür, bobin kaydedebilir. |
| 8 | Operatör 4003 (V1) | Adım 7 sonrası; adım 6'daki talimat ve malzeme çıkışı adresleri | Talimat adresini, `?yazdir=1` ile yazdırma adresini, malzeme çıkışı ve bobin sayfasını açın. | Talimat/yazdır 404; malzeme çıkışı/bobin 403; iş emri sayfası açılır ama ölçü ve karışım yok; işlem geçmişi görünür. |
| 9 | Planlama 3001 | Adım 7 sonrası | Aynı talimatı ve reçete sayfasını açın. | Görür. |
| 10 | Üretim yetkilisi 4002 | Kapalı iş emri, malzeme çıkışı 100 kg | Düzeltme talepleri → çıkışı 110 kg'a düzeltme talebi. Aynı kişi onaylamayı dener. | Kendi talebini onaylayamaz. |
| 11 | Ambar 5002 | Adım 10'daki talep | Onaylayın. İş emri sayfasını açın. | İlk kapanış 100 kg / fark 0 değişmez; güncel 110 kg / fark 10 kg; sınır 3,3 kg aşıldığı için "yeniden değerlendirme bekliyor". 4002 gerekçeyle değerlendirir. |
| 12 | Depo 5001 | 10 kutuluk sevkiyat, **araç çıkışı yapılmamış** | "İade al" düğmesini arayın. | Görünmez; çıkış öncesi yanlışlık için ambar sevki geri alır. |
| 13 | Ambar 5002 → Depo 5001 | Adım 12'de çıkış kaydedilir | Depo 2 kutu iade alır. | Ayrı iade paleti, "kontrol bekliyor"; asıl sevk geçmişi değişmez. |
| 14 | Satış 2001 | Adım 13'teki iade | "Yerine gönderim" 2 kutu; sonra 3 kutu daha deneyin; kalem değişikliğiyle 3'e çıkarmayı deneyin; siparişi kopyalayın. | İlki kabul; 3 ve değişiklik reddedilir; kopyada yalnız asıl 10 kutuluk kalem. |
| 15 | Depo + Ambar | Adım 14'teki yerine gönderim | Ayırın, sevk edin, çıkış yapın. Sipariş detayını açın. | İlk sevk 10, iade 2, yerine gönderim 2, müşteride net 10; iade paleti kalite kararı verilmeden sevk edilemez. |
