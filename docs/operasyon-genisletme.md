# Operasyon genişletmesi — teknik teslim (feature/mimcor-operasyon-genisletme)

Kaynak: `design/mimcor-demo-2026-10-30` @ `5bc44c2`. Geliştirme branch'i: `feature/mimcor-operasyon-genisletme`.
Kaynak branch'e merge, force push veya canlı yayın yapılmadı. Aşama commit'leri:

| Commit | Aşama |
|---|---|
| `b39da11` | 1 — Yönetilebilir roller/kapsamlar, İK, satışçı–müşteri erişimi, genişletilmiş müşteri kartı, oturum süresi (bulgu 12) |
| `1d9be13` | 2 — İş emri ve sipariş yaşam döngüsü, ustabaşı planı, tek aktif iş (bulgu 7), duruş/vardiya devri, makine talimatı |
| `df631b8` | 3 — Düzeltme talebi/onayı, kalite blokaj kaldırma, teknik uygunluk ve miktar bulguları (1, 2, 3, 6, 8, 9, 11) |
| `e1183e2` | 4 — Sevkiyat aşamaları + QR yükleme kontrolü, geri alma, iade, etiket, stok seviyesi, iş listeleri |
| `9902c1e` | Kabul: saha senaryosu tarayıcı testleri, sunum etiketleri, belge |
| `fix/mimcor-yetki-veri-tutarliligi` | Kabul öncesi yetki ve veri tutarlılığı düzeltmeleri (bölüm 5; ayrı branch, kaynağa merge edilmedi) |

## 1. Yetki modeli

- **Eylem kataloğu** kodda (`accounts/catalog.py`): her eylemin türü ayrı — görme, oluşturma, değiştirme, onay, iptal,
  düzeltme talebi, düzeltme onayı, yazdırma. Roller veritabanında (`AccessRole`, `RolePermission`, `UserRole`); yönetici
  ekrandan rol oluşturur/değiştirir (Kullanıcılar → Roller).
- **Kapsamlar:** `T` tüm kayıtlar, `M` erişim verilen müşteriler, `A` kendisine atanmış işler (ustabaşı),
  `Mk` atandığı makineler, `V` operasyonun sorumlu vardiyası kendi vardiyası, `Mk+V` atandığı makine **ve** sorumlu
  vardiya (tek kapsam içinde ikisi birden; usta/operatör şablonu aktif teknik bilgi ve üretim kaydı için bunu kullanır).
- **Birleşim kuralı:** kullanıcının yetkileri aktif rollerinin birleşimidir; bir eylemin kapsamı o eylemi veren rollerin
  kapsamlarının birleşimidir (**VEYA**). `T` yalnız bir rol açıkça `T` veriyorsa geçerlidir. Bir kullanıcıya ikinci
  bir rol verilmesi erişimi genişletebilir (ör. `Mk+V` operatöre ayrıca `V` kapsamlı talimat görme verilirse aynı
  vardiyadaki başka makineler de açılır) — bu, rol atamasında yöneticinin kararıdır ve testle gösterilmiştir. Kapsam kuralı tanımsız modelde o
  kapsam erişim vermez (varsayılan kapalı). Kapsam dışı kayıt detayı **404** döner (varlığı sızdırılmaz).
- **Uygulama noktaları:** view (`require_perm`, `get_scoped_or_404`), servis (`check(actor, eylem, nesne)` her yazma
  servisinde), liste/arama/dashboard/endpoint (`scoped()`), çıktılar (talimat, etiket, belge indirme).
- **Oturum:** yetkiler her istekte veritabanından okunur → kaldırılan rol açık oturumda bir sonraki istekte geçerli.
  `is_superuser` uygulama yetkisi vermez; Django yönetim sitesi `system.admin` ister ve iş modellerinde salt okunur.
- **Son yönetici koruması:** `system.admin` taşıyan son aktif kullanıcının rolü kaldırılamaz, hesabı kapatılamaz, rolü
  pasife alınamaz veya yetkisi çıkarılamaz (advisory lock ile eş zamanlı değişikliklere karşı).
- **İK:** personel açar, ekip/vardiya atar, yönetici olmayan hesabı gerekçeyle kapatır, yalnız "İK atayabilir" rolleri
  atar, `system.admin` içeren rolü atayamaz, kendi rol/izinlerini değiştiremez; rol taslağı açabilir, etkinleşmesi
  yönetici onayı ister. Rol/hesap değişikliklerinin hepsi işlem geçmişinde.

### Başlangıç şablonları (veritabanına yazılır; yönetici değiştirebilir) — koddan üretildi

| Yetki | Yönetici | İK | Satış | Planlama | Üretim yetkilisi | Ustabaşı | Usta / operatör | Kalite sorumlusu | Ambar yetkilisi | Depo personeli |
|---|---|---|---|---|---|---|---|---|---|---|
| `system.admin` Sistem yönetimi (son yönetici koruması bu yetkiye bakar) | T | · | · | · | · | · | · | · | · | · |
| `user.manage` Tüm kullanıcı ve rol atamalarını yönetme | T | · | · | · | · | · | · | · | · | · |
| `role.manage` Rol oluşturma, yetki seçme, etkinleştirme | T | · | · | · | · | · | · | · | · | · |
| `role.approve` İK rol taslağını onaylama | T | · | · | · | · | · | · | · | · | · |
| `hr.user.manage` Personel oluşturma, ekip/vardiya, yönetici dışı hesap kapatma | T | T | · | · | · | · | · | · | · | · |
| `hr.role.assign` İK atayabilir rolleri atama | T | T | · | · | · | · | · | · | · | · |
| `role.draft` Rol taslağı hazırlama | T | T | · | · | · | · | · | · | · | · |
| `shift.manage` Vardiya tanımlarını yönetme | T | T | · | T | · | · | · | · | · | · |
| `customer.view` Müşteri kartını görme | T | · | M | T | T | · | · | · | T | T |
| `customer.create` Müşteri açma | T | · | T | · | · | · | · | · | · | · |
| `customer.change` Müşteri kartını değiştirme / pasife alma | T | · | M | · | · | · | · | · | · | · |
| `customer.commercial.view` Ticari bilgileri (vade, ödeme, para birimi) görme | T | · | M | · | · | · | · | · | · | · |
| `customer.document.view` Müşteri belgelerini görme/indirme | T | · | M | T | T | · | · | · | T | T |
| `customer.document.manage` Müşteri belgesi yükleme/pasife alma | T | · | M | · | · | · | · | · | · | · |
| `customer.access.manage` Satışçılara müşteri erişimi verme/kaldırma | T | · | · | · | · | · | · | · | · | · |
| `product.view` Ürün kodlarını görme | T | · | M | T | T | · | · | T | T | T |
| `product.create` Ürün kodu açma | T | · | M | T | · | · | · | · | · | · |
| `product.change` Ürün kodunu değiştirme / pasife alma | T | · | M | T | · | · | · | · | · | · |
| `recipe.view` Reçete arşivi ve revizyonlarını görme | T | · | · | T | · | · | · | · | · | · |
| `recipe.change` Reçete revizyonu oluşturma | T | · | · | T | · | · | · | · | · | · |
| `artwork.view` Baskı görsellerini ve onay durumunu görme / indirme | T | · | M | T | T | · | · | · | · | · |
| `artwork.manage` Baskı görseli taslak sürümü yükleme / kullanımdan kaldırma | T | · | M | T | · | · | · | · | · | · |
| `artwork.approve` Baskı görseline iç teknik onay verme (üretime uygun) | T | · | · | T | · | · | · | · | · | · |
| `artwork.customer_approval` Baskı görseli için müşteri onayını kaydetme | T | · | M | T | · | · | · | · | · | · |
| `machine.view` Makineleri görme | T | · | T | T | T | T | T | T | T | T |
| `machine.manage` Makine ekleme/değiştirme | T | · | · | T | · | · | · | · | · | · |
| `material.view` Hammadde/katkı ve lotları görme | T | · | · | T | T | T | · | · | T | T |
| `material.manage` Hammadde/katkı tanımlama | T | · | · | T | · | · | · | · | · | · |
| `lot.receive` Malzeme lot girişi | T | · | · | · | · | · | · | · | T | T |
| `order.view` Siparişleri ve üretim ilerlemesini görme | T | · | M | T | T | · | · | · | T | T |
| `order.create` Sipariş ve kalem oluşturma | T | · | M | · | · | · | · | · | · | · |
| `order.change` Sipariş/kalem değiştirme, kopyalama, taslak silme | T | · | M | · | · | · | · | · | · | · |
| `order.approve` Siparişi onaylama (taslaktan çıkarma) | T | · | M | · | · | · | · | · | · | · |
| `order.cancel` Sipariş/kalem iptali | T | · | M | · | · | · | · | · | · | · |
| `planning.approve` Teknik/reçete onayı | T | · | · | T | · | · | · | · | · | · |
| `exception.approve` Uyumluluk, fazla teslim ve rota istisnalarını onaylama | T | · | · | T | · | · | · | · | · | · |
| `workorder.view` İş emirlerini görme | T | · | M | T | T | A | Mk | T | T | T |
| `workorder.create` İş emri oluşturma/düzenleme, ustabaşı atama | T | · | · | T | · | · | · | · | · | · |
| `workorder.release` İş emrini serbest bırakma/geri çekme | T | · | · | T | · | · | · | · | · | · |
| `workorder.cancel` İş emri iptali | T | · | · | T | · | · | · | · | · | · |
| `workorder.schedule` Operasyonları tarih/makine/vardiya/sıraya dağıtma | T | · | · | T | T | A | · | · | · | · |
| `production.execute` Üretim kaydı (başlat, bobin, malzeme, palet, kapanış) | T | · | · | · | T | A | Mk+V | · | · | · |
| `production.hold` Duruş/devam ve vardiya devri | T | · | · | · | T | A | Mk+V | · | · | · |
| `instruction.view` Operasyon teknik talimatını görme ve teslim alma | T | · | · | T | T | A | Mk+V | · | · | · |
| `instruction.print` Makine iş talimatı yazdırma | T | · | · | T | T | A | · | · | · | · |
| `material.deviation` Reçete dışı malzeme kullanımına gerekçeli izin | T | · | · | T | T | · | · | · | · | · |
| `quality.decide` Kalite: uygun/kontrol bekliyor kararı | T | · | · | · | T | A | · | T | · | · |
| `quality.block` Kalite blokajı koyma | T | · | · | · | T | A | Mk+V | T | T | T |
| `quality.release` Kalite blokajını gerekçeyle kaldırma | T | · | · | T | · | A | · | T | · | · |
| `stock.view` Palet/stok görme | T | · | M | T | T | T | · | T | T | T |
| `warehouse.operate` Depo kabul, tartım, aktarım, sayım, hasar, konum | T | · | · | · | · | · | · | · | T | T |
| `reservation.manage` Siparişe ayırma, azaltma, iptal | T | · | M | · | · | · | · | · | T | T |
| `shipment.view` Sevkiyatları görme | T | · | M | T | T | · | · | · | T | T |
| `shipment.manage` Sevkiyat hazırlama, QR yükleme kontrolü, iade kabulü | T | · | · | · | · | · | · | · | T | T |
| `shipment.approve` Son sevk onayı (stok düşer) | T | · | · | · | · | · | · | · | T | · |
| `shipment.undo` Çıkış öncesi sevk geri alma | T | · | · | · | · | · | · | · | T | · |
| `shipment.exit` Fiziksel araç çıkışını kaydetme | T | · | · | · | · | · | · | · | T | · |
| `label.print` Palet/kutu etiketi yazdırma | T | · | · | · | · | · | · | · | T | T |
| `stocklevel.manage` Hedef/alt stok seviyesi tanımlama | T | · | · | T | · | · | · | · | · | · |
| `correction.request` Düzeltme talebi açma | T | · | M | T | T | · | · | · | · | · |
| `correction.approve` Düzeltme talebini onaylama/reddetme | T | · | · | · | T | · | · | · | T | · |
| `sample.view` Numuneleri görme (satış: yalnız paylaşılabilir durum ve geri bildirim) | T | · | M | T | T | A | · | T | T | T |
| `sample.manage` Numune kaydı, konum, gönderim, geri dönüş, imha, ek dosya | T | · | · | T | T | A | · | T | T | T |
| `sample.stock` Numune için paletten kutu ayırma (stok düşer) | T | · | · | · | · | · | · | · | T | T |
| `sample.decide` Numune ölçümü ve iç kalite kararı (blokaj kaldırma değildir) | T | · | · | · | T | · | · | T | · | · |
| `sample.feedback` Müşteri geri bildirimini kaydetme | T | · | M | T | · | · | · | T | · | · |
| `sample.criteria` Numune kontrol kriterlerini tanımlama | T | · | · | · | · | · | · | T | · | · |

İK atayabilir: Satış, Planlama, Üretim yetkilisi, Ustabaşı, Usta / operatör, Kalite sorumlusu, Ambar yetkilisi, Depo personeli

Şablon değişiklikleri yükseltmede yalnız **değiştirilmemiş** şablon rollerine uygulanır; özelleştirilmiş roller yeniden
yazılmaz, rol listesinde "Şablondan farklı" uyarısı, önerilen fark ve etkilenen kullanıcı sayısıyla gösterilir.

## 2. Satışçı–müşteri erişimi

- Müşteriyi açan satışçıya otomatik **kalıcı** erişim; yönetici (`customer.access.manage`) kalıcı ya da
  **başlangıç–bitiş tarihli** (vekâlet) erişim verir/kaldırır. Süre dolunca erişim sorguda tarihle düşer; liste, detay,
  belge indirme, ürün endpoint'i, sipariş, palet/etiket, sevkiyat ve stok özetinde kapanır (testli).
- Müşteri kapsamı tüm yüzeylerde aynı fonksiyondan (`accounts/scope.py`) geçer; kapsam dışı detay 404.
- Operasyon rolleri müşteri **operasyon** bilgisini `T` kapsamla görür; **ticari** bilgi ayrı `customer.commercial.view`.
- **Erişim kaynağı** her kayıtta: müşteriyi açan kullanıcı / yönetici ataması / süreli (vekâlet) / eski sistemden
  taşınan (toplu) / kaynağı belirsiz. Yükseltmedeki toplu erişimler kesin imzayla (veren boş + 0004 migration'ının tam
  gerekçe metni) ayrılır; imzası tutmayan sistem kayıtları "belirsiz" olur. **Hiçbiri silinmez veya kapatılmaz**;
  toplu taşınan ve belirsiz kayıtlar aktif kalır ve "inceleme bekliyor" işaretlenir.
- **Yönetici inceleme ekranı** (menü: Müşteri erişimleri): kaynak/inceleme/satışçı/müşteri süzgeci; onayla, süreliye
  çevir, kaldır; toplu işlemde etkilenecek kullanıcı × müşteri listesi önce önizlenir. Kaldırma yalnız erişimi kapatır;
  müşteri, sipariş ve işlem geçmişi silinmez. Ana sayfada "İncelenmemiş müşteri erişimleri" iş listesi.
- **Durum:** inceleme aracı hazır; mevcut geniş erişimlerin **kimde kalacağı işletme kararıdır ve henüz verilmedi**.
  Yükseltilmiş kurulumda yönetici bu listeyi bitirene kadar satışçılar eski geniş erişimi korur.
- Kurgusal demo: 2001 → M-ORNEK, M-DENEME; 2002 → M-KURGU, M-NORD + M-ORNEK'e 7 günlük vekâlet. 2002 M-DENEME'yi
  listede, URL'de ve ürün endpoint'inde göremez (tarayıcı senaryosu 0).

## 3. Veri modeli ve migration'lar

| Uygulama | Migration | İçerik |
|---|---|---|
| accounts | 0002–0004 | `Shift` (V1 07–19, V2 19–07), `AccessRole`, `RolePermission`, `UserRole`, `UserMachine`, `User.team/shift`; eski `User.role` → rol ataması (uretim → ustabaşı), alan kaldırıldı |
| catalog | 0003–0005 | Müşteri kartı alanları, `CustomerContact`, `CustomerAddress`, `CustomerDocument`, `CustomerAccess`; eski serbest metin `contact` → ana ilgili kişi (test edildi) |
| catalog | 0006 | `Material.unit` seçenekli (kg/lt/adet); eski metin birimleri normalize edilir, tanınmayan değer korunur |
| catalog | 0007 | `Product.package_type`, `nominal_package_kg` (yalnız bilgi; kg dönüşümü yok) |
| orders | 0002–0003 | Sipariş anlık kopyası (adres/ticari), taslak/onay/iptal/arşiv, kopya kaynağı, kalem iptal/kopya onayı |
| orders | 0004 | `LineStatus.PRODUCED_SHORT`, `CompatibilityException`, `OverDeliveryAllowance` |
| production | 0003–0005 | `assigned_foreman`, operasyon tarih/vardiya/sıra, taslak/serbest/iptal/arşiv, `OperationStop`, `ShiftHandover`, `WorkInstruction`(+basım, teslim kaydı); açık iş emirleri tek ustabaşı varsa ona atanır |
| production | 0006 | `RouteException`, `MaterialDeviation` |
| warehouse | 0002 | Sevkiyat iptal durumu |
| warehouse | 0003 | Sevkiyat aşamaları (hazırlandı/çıkış), `ShipmentScan`, `ReturnReceipt`, `PalletLocationChange`, `StockLevel`, etiket basım durumu; eski "sevk edildi" kayıtlar "çıkış yapıldı" sayılır (test edildi) |
| corrections | 0001 | `CorrectionRequest` (yeni uygulama, aynı proje/şablon yapısı) |
| accounts | 0005 | `lot.adjust` rol satırları silinir (işlem geçmişine yazılır) |
| accounts | 0006–0007 | Sevk onayı/geri alma/çıkış ayrımı; operatör makine+vardiya. Yalnız değiştirilmemiş şablon rolleri güncellenir (`accounts/template_sync.py`) |
| catalog | 0008 | Müşteri erişimi kaynağı + inceleme; kesin imzalı sınıflandırma, silme yok |
| corrections | 0002 | `lot_sayim` düzeltme türü |
| orders | 0005–0007 | Eşdeğerlik onayı hedef revizyon + geri alma (eski onaylar o anki hedefe bağlanır); fazla teslim izin geçmişi; yerine gönderim bağı |
| production | 0007–0008 | `ClosureReview`; `responsible_shift` (son teslim alınmış devirden doldurulur) |

Hareketli kayıtlar silinmez; düzeltme ters/ek hareketle yapılır (`PalletMovement` türleri: `duzeltme`,
`sevk_geri_alma`, `iade_girisi`). Taslak sipariş silme içeriği işlem geçmişine yazılır.

**Doğrulama:** (a) Yükseltme — orijinal demo veritabanının kopyası (`createdb -T mimcor mimcor_upgrade_a2`) her aşamada
ileri taşındı; roller, iş emirleri, siparişler, malzeme birimleri kontrol edildi. UAT veritabanının kopyası
(`mimcor_uat_kopya_a4`) de son migration'a taşındı. Orijinal `mimcor` ve `mimcor_uat` veritabanlarına **son
aşamalarda dokunulmadı**; ancak `mimcor_uat`, Aşama 1 geliştirmesi sırasında ileri taşınmıştı (veri korunarak).
(b) Temiz kurulum — boş `mimcor_clean_a2`: migrate + `seed_demo` iki kez (tekrar çalıştırılabilir).

## 4. Kapsam kontrol listesi

Durum: **Tamam** = uygulandı ve testle doğrulandı · **Kısmi** = sınırı yazılı · **Yapılmadı/Engelli**.

| # | Madde | Durum | Not |
|---|---|---|---|
| 1 | Yönetilebilir rol/yetki/kapsam, birleşim, varsayılan kapalı | Tamam | `accounts/tests/test_roles_scopes.py` |
| 1 | Son yönetici, rol/hesap audit, açık oturumda geri alma, superuser atlatmaz | Tamam | aynı |
| 1 | İK sınırları ve rol taslağı onayı | Tamam | aynı |
| 1 | Satışçı–müşteri kalıcı/geçici erişim, tüm yüzeylerde izolasyon | Tamam | sipariş/ürün endpoint/belge/stok testleri |
| 1 | Vardiya kapsamı | Tamam | Operatör şablonu makine + sorumlu vardiya (bölüm 5, madde 8) |
| 2 | Müşteri kartı (kimlik, kişiler, adresler, ticari, operasyon, belgeler, erişim, durum/geçmiş) | Tamam | `catalog/tests/test_customer_card.py` |
| 2 | Aşamalı zorunluluk (taslak/sipariş onayı/sevkiyat), yabancıda TR formatı yok, mükerrer + benzer ünvan | Tamam | |
| 2 | Belge sürüm/tarih/görünürlük, indirme yetkisi, boyut/tür/imza kontrolü | Tamam | Virüs taraması yok (kapsam dışı) |
| 2 | Sipariş anlık kopyası, bakiye gösterilmez, eski iletişim verisi taşıma | Tamam | |
| 3 | Reçete yalnız yönetici/planlama; ürün geçmişinde reçete kayıtları gizli | Tamam | |
| 3 | İş emri önce ustabaşına; ustabaşı tarih/makine/vardiya/sıra dağıtır; iki vardiya DB'de | Tamam | |
| 3 | Üretim yalnız açık iş emrinde kendi operasyonunun teknik alt kümesini görür | Tamam | operatör testi |
| 3 | Makine iş talimatı (kodlar, sipariş kalemi, ürün, makine, vardiya, hedef, revizyon, ilgili talimatlar, belge ref., sürüm, basan, zaman), geçersiz damgası, teslim kaydı | Tamam | Kiosk yok (kapsam dışı) |
| 4 | Sipariş/iş emri yaşam döngüsü (taslak, onay/serbest, geri çekme, durdur/devam, kapat, iptal, arşiv) | Tamam | |
| 4 | Başlamış iş emrinde müşteri/ürün/revizyon değişmez; kalem değişikliği iş emri revizyonunu sessizce değiştirmez | Tamam | çelişki rozeti |
| 4 | Makine/tarih/vardiya değişikliğinde etkilenen kayıtlar | Tamam | aynı makine/vardiya kuyruğu gösterilir |
| 4 | Hedef/termin değişikliğinde etkilenen kayıtlar | Kısmi | Kalem değişikliğinde liste + onay var; iş emri hedefi yalnız taslakta düzenlenir ve ayrı etki listesi göstermez |
| 4 | Kontrollü kopya, duruş nedenleri, vardiya devri, iptal edilen işte malzeme/bobin sahipsiz kalmaz, iptal/boş taslak sevkiyat listeden düşer | Tamam | |
| 5 | Düzeltme talebi/onayı (bobin, malzeme çıkış/iade, palet kutu, tartım, sayım), onayda yeniden kontrol, ret, kendi onayı kapalı | Tamam | `corrections/tests` |
| 6 | Kalite blokaj kaldırma yetkisi + gerekçe, bağımsız blokajlar, yeni içerikte yeniden değerlendirme, satış stok dökümü | Tamam | |
| 7 | Bulgu 1–12 | Tamam | Ayrıntı aşağıda |
| 7 | Reçete/ürün doğrulamaları | Tamam | |
| 8 | Depo yeri geçmişi, rezervasyon azaltma | Tamam | |
| 8 | Sevkiyat hazırlık / QR yükleme kontrolü (yanlış/mükerrer/eksik/fazla) / onay / çıkış; çıkış öncesi geri alma | Tamam | |
| 8 | İade (eski sevkiyata bağlı, kontrol bekliyor) | Tamam | |
| 8 | Kutu/rulo etiketi toplu basım, yeniden basım kimlik/stok üretmez, basım talebi ≠ başarı, QR APP_BASE_URL | Tamam | Yazıcı sürücüsü yok (kapsam dışı) — "basıldı" kullanıcı bildirimidir |
| 8 | Alt/hedef stok + ihtiyaç listesi, uygulama içi iş listeleri, paket tipi, kg dönüşümü yok | Tamam | |
| — | Fiziksel telefon testi | **Yapılmadı** | Yalnız 390 px Chromium emülasyonu |
| — | Tarayıcı testlerinin CI'da çalışması | Tamam | Ayrı "Tarayıcı testleri (Chromium)" işi (bölüm 5, madde 9) |

### Bulgular (1–12)

| # | Düzeltme | Regresyon testi | Düzeltme öncesi başarısızlık kanıtı |
|---|---|---|---|
| 1 | Teknik imza (en, kalınlık, boy, renk, baskı, perfore; kabul öncesi düzeltmeyle + karışım) uyuşmazsa ayırma/sevk/aktarım reddi; yetkili gerekçeli istisna | `core/tests/test_findings.py::Finding1…` | Var (FAIL) |
| 2 | Ayırma + sevk ≤ kalem miktarı + yetkili fazla teslim; kalem satır kilidi | `Finding2…`, eş zamanlı `Finding2ConcurrencyTests` | Var (FAIL, eş zamanlı test dahil) |
| 3 | Kapanışta üretilen < sipariş → "Üretim kapandı – eksik" | `Finding3…`, `test_coils` güncellendi | Var (ERROR: durum yoktu) |
| 4 | Sipariş üst durumu kalemlerden türetilir | `test_order_lifecycle`, `test_phase4` | Kaydedilmedi (Aşama 2'de düzeltildi) |
| 5 | Serbest/başlamış iş emri varken farklı revizyonla yeniden onay reddi | `Finding5ReapprovalTests` | Kaydedilmedi (Aşama 1'de düzeltildi) |
| 6 | Kaynak bobin bir önceki operasyondan; rota atlama yetkili izinle | `Finding6…` | İlk koşu test kurgusu hatasıyla düştü; temiz yeniden üretim kaydı **yok** |
| 7 | Makine başına tek aktif iş (makine satırı kilidi) | `test_lifecycle::MachineAndScheduleTests` | Kaydedilmedi (Aşama 2) |
| 8 | Malzeme birimi seçenekli/dondurulmuş; denge yalnız kg | `Finding8…` | Var |
| 9 | Reçete dışı malzeme yalnız `material.deviation` + gerekçe; kapanışta listelenir | `Finding9…` | Var |
| 10 | Kutu/rulo/adet tam sayı (form + servis) | `test_order_lifecycle::test_discrete_units…` | Kaydedilmedi (Aşama 1) |
| 11 | Adet/rulo kalemde kutudan üretim türetilmez | `Finding11…` | Var |
| 12 | Mutlak oturum süresi giriş anından; hareketsizlik ayrı | `test_roles_scopes` (oturum testleri) | Kaydedilmedi (Aşama 1) |

## 5. Kabul öncesi yetki ve veri tutarlılığı düzeltmeleri (`fix/mimcor-yetki-veri-tutarliligi`)

Kaynak: `feature/mimcor-operasyon-genisletme` @ `9902c1e`. Her başlık ayrı commit. "Önce" sütunundaki davranışlar,
düzeltmeden önce yazılan testlerin mevcut kodda başarısız olmasıyla doğrulandı (1 ve 3 için kayıtlı; diğerleri kod
okuması + yeni test).

| # | Önceki davranış | Yapılan değişiklik | Doğrulama | Veri taşıma / sınır |
|---|---|---|---|---|
| 1 Stok düzeltmesi | Depo personeli `lot.adjust` ile lot sayfasından ve `warehouse.operate` ile palet "Sayım düzeltmesi" formundan miktarı doğrudan değiştirebiliyordu (testte 40→30 kutu, 1000→900 kg). | `lot.adjust`, `adjust_lot_to`, `adjust_count` kaldırıldı; `move_lot` doğrudan çağrıda giriş/sayım türlerini reddeder. Sayım farkı yalnız düzeltme talebi (palet `sayim`, lot `lot_sayim`) + başka yetkilinin onayı, onayda yeniden kontrol. Eski adresler stok değiştirmez; talep ekranına ya da "yetkilisine bildirin" uyarısına yönlendirir. Mal kabulünde tedarikçi parti/irsaliye zorunlu (her zaman yeni lot). | `corrections/tests/test_no_bypass.py` (doğrudan POST, servis, kendi onayı, değişmiş kayıt, şablonlar); tarayıcı senaryosu 2. | `lot.adjust` rol satırları silinir, işlem geçmişine yazılır. Gözlem: üretimden palete giriş kutu adedi serbest girilir (bobin + başlamış iş emri zorunlu); hasar ayırma/aktarım toplam stoğu değiştirmez — düzeltme yerine kullanılamaz. |
| 2 Müşteri erişimi | Yükseltmede tüm satışçılara tüm müşteriler kalıcı açıldı ve ayırt edilemiyordu. | Kaynak + inceleme alanları, kesin imzalı sınıflandırma, yönetici inceleme ekranı, önizlemeli toplu işlem, iş listesi. | `catalog/tests/test_access_review.py` (migration sınıflandırması, önizleme/uygulama, yetki, süresi dolan vekâlet her yüzeyde, iki satışçı izolasyonu); tarayıcı senaryosu 0. | Erişim silinmez; karar yöneticide. Orijinal demo kopyasında 3 erişim "taşınan / inceleme bekliyor" çıktı. |
| 3 Karışım | Teknik imza karışımı içermiyordu; istisna hedef revizyona bağlı değildi. | İmza = ölçü, renk, baskı, perfore + (malzeme kimliği, oran) çiftleri; rev no karşılaştırılmaz. Mevcut istisna modeli genişletildi: kaynak revizyon + kalem + **onay anındaki hedef revizyon**, geri alınabilir. Kontrol ayırma, sevk satırı, sevk onayı (eski rezervasyonlar dahil) ve aktarımda. | `orders/tests/test_mix_compatibility.py` (farklı katkı oranı/malzeme ret, onayla izin, başka kalem ret, yeniden onaylanan kalemde düşer, aynı içerik farklı rev no izin, 98 = 98.000, geri alınan onayda sevk ret). | Eski onaylar yükseltme anındaki kalem hedefine bağlanır. Varsayım: karışım farkında tolerans yok. |
| 4 Fazla teslim | İzin yalnız eklenebiliyordu (birikimli). | `set_over_delivery`: artır/azalt/0=kaldır, gerekçe, kaleme özel; yeni sınır sevk + ayrılmışın altına inemez (etkilenen rezervasyon/sevkiyat listelenir); eski izinler "yerine geçildi". | `orders/tests/test_over_delivery.py` (eş zamanlı izin azaltma + ayırma, paralel iki ayırma). | Varsayılan 0; müşteri yüzdesi yok. |
| 5 Sevk yetkileri | `shipment.approve` = onay + geri alma + çıkış; depo şablonunda da vardı. | `shipment.approve` / `shipment.undo` / `shipment.exit` ayrı. Depo = hazırlama + QR; ambar yetkilisi + yönetici = üçü. Rol listesinde şablon farkı ve "önerilen şablonu uygula". | `warehouse/tests/test_shipment_permissions.py` (doğrudan POST 403, bağımsız yetkiler, çıkış → iade, migration'ın özelleştirilmiş rolü yeniden yazmaması). | Yalnız değiştirilmemiş şablonlar güncellenir. UAT kopyasında depo rolü ara bir şablon sürümünden kaldığı için **dokunulmadı** ve rol listesinde gösterilir. Fiziksel çıkışı **ambar yetkilisi ve yönetici** kaydeder (varsayım). |
| 6 Kapalı iş emri | Kapanıştan sonraki düzeltmeler ekranda görünmüyordu; paletli bobin düzeltmesi tartımı etkilemiyordu. | İlk kapanış (değişmez) ve güncel denge yan yana; kapanıştan sonra uygulanan düzeltmeler; tolerans aşılırsa "yeniden değerlendirme bekliyor" + gerekçeli `ClosureReview`. Paletli bobin: stoktaki palet yeniden tartım bekler, sevk edilmiş palet → ret. | `production/tests/test_closure_corrections.py`: verilen 100 kg, sağlam 95, fire 5 → fark 0; lottan iş emrine verilen malzeme (lot çıkışı = iş emri girdisi) 110 kg'a düzeltilince ilk kapanış 100/0 kalır, güncel 110/10 kg (tolerans tabanı net verilen 110 kg × %3 = 3,3 kg; çıktı değil) — ayrıntı `docs/kabul-denetimi.md` → değerlendirme; seri ve eş zamanlı çift onay tek hareket. | Kapanış yeniden hesaplanıp üzerine yazılmaz. |
| 7 İade / yerine gönderim | Yerine gönderimin iade ile bağı yoktu. | `OrderLine.replacement_for`: asıl siparişe iade kaydına bağlı kalem (yeni satış sayılmaz), miktar ≤ iade − önceki yerine gönderimler; kartta ilk sevk / iade / yerine gönderim / müşteride kalan net. | `warehouse/tests/test_replacement.py` (zincir ve net, sınırlar, yetki, eş zamanlı iade). | Muhasebe/fatura yok (kapsam dışı). |
| 8 Vardiya + makine | Vardiya kapsamı kullanılmıyordu; operatör makinesindeki tüm vardiyaların teknik bilgisini görüyordu; devralan operatör devri teslim alamıyordu. | `Mk+V` kapsamı; operasyonun `responsible_shift` alanı (planlanan vardiya, devir teslim alınınca devralan vardiya). Eski vardiyanın aktif teknik erişimi devirle kapanır; geçmiş (makine kapsamlı iş emri görünümü) açık. Devralan vardiyadaki, o makineye atanmış kişi devri teslim alabilir. Saat kuralı yok. | `production/tests/test_shift_scope.py` (VE kuralı, gece vardiyası, geçmiş/aktif, devir, vardiyasız operatör, çoklu rol genişlemesi, şablon migration'ı). Seed: 4003 (V1) ve 4005 (V2) EXT-01/PER-01. | Sorumlu vardiya mevcut kayıtlarda son teslim alınmış devirden doldurulur. Planlanmamış (vardiyasız) operasyonun teknik bilgisi operatöre açılmaz; ustabaşı planlamalıdır. |
| 9 CI | Tarayıcı testleri yalnız yerelde. | Ayrı "Tarayıcı testleri (Chromium)" işi; ekran görüntüleri ve başarısızlıkta trace "tarayici-ciktilari" artifact'ı. | Başarısızlık artifact'ı geçici başarısız testle doğrulandı. | Emülasyon; gerçek telefon değil. |

## 6. Test sonuçları (27.10.2026, PostgreSQL 16, Chromium headless)

- Django: **301 test, tamamı başarılı** (`python3 manage.py test accounts catalog orders production warehouse core corrections`;
  seri çalıştırıldı — `--parallel` bir hata olduğunda "cannot pickle traceback" ile çöküyor).
- Tarayıcı: `ui_checks.browser_checks` (Demo A + B), `ui_checks.order_product_filter_checks`,
  `ui_checks.field_scenarios_checks` (satış izolasyonu + 4 saha senaryosu), her biri 1440 ve 390 px — yerelde **6/6**.
  **CI'da ayrı iş olarak çalışır** (sonuç HANDOFF.md'de). 390 px bir ekran emülasyonudur; **gerçek telefonda deneme yapılmadı**.
- Eş zamanlılık: aynı sevkiyatın iki kez onayı, aynı form anahtarı, bobin blokajı–sevk yarışı, paralel ayırma,
  izin azaltma–ayırma yarışı, eş zamanlı çift düzeltme onayı, eş zamanlı iade.
- Ekran görüntüleri: `docs/screenshots/saha-senaryolari/` (güncel), `docs/screenshots/saha-senaryolari/9902c1e/` (önceki tur).

Yerel çalıştırma: `CHROMIUM_PATH=<chromium> DEMO_SCREENSHOT_DIR=/tmp/demo python3 manage.py test ui_checks.browser_checks
ui_checks.order_product_filter_checks ui_checks.field_scenarios_checks` (`DEMO_SCREENSHOT_DIR` verilmezse `docs/screenshots`
üzerine yazar). CI'da `pip install -r requirements-browser.txt && python -m playwright install --with-deps chromium`.

### UAT senaryoları (kullanıcı kabulü için; testlerin geçmesi işletme onayı değildir)

0. **Satış izolasyonu:** 2002 ile girin; Müşteriler listesinde M-DENEME yok, M-ORNEK (vekâlet bitiş tarihiyle) var.
   Yönetici (1001) "Müşteri erişimleri" ekranında kaynakları görür; yükseltilmiş kurulumda "inceleme bekliyor"
   kayıtları önizleyip onaylar / süreliye çevirir / kaldırır.

1. **Müşteri değiştirdi:** Satış (2001) onaylı kalemde miktarı 20 → 15 yapar; ekran etkilenen iş emrini listeler ve onay
   ister. İş emri hedefi 20 kalır, iş emri sayfasında çelişki görünür. Planlama (3001) iş emrini gerekçeyle geri çeker
   (basılı talimatlar geçersiz damgalanır), hedefi 15 yapar, yeniden serbest bırakır.
2. **Usta yanlış girdi / sayım farkı:** Depo (5001) palet sayım adresine gider → miktar değişmez, "yetkilisine
   bildirin" uyarısı. Üretim yetkilisi (4002) bobin 97 → 79 kg (veya lot/palet sayım) düzeltme talebi açar; kayıt
   değişmez. Ambar yetkilisi (5002) onaylar; geçmişte eski değer kalır. 4002 kendi talebini onaylayamaz.
3. **Vardiya bitti:** Ustabaşı (4001, V1) çalışan operasyonu V1'e planlamışsa operatör 4003 (V1, EXT-01) teknik bilgiyi
   görür, 4005 (V2) görmez. 4001 "Vardiya devri" ile kalan işi yazar; 4005 veya üretim yetkilisi teslim alır. Bundan
   sonra 4005 teknik bilgiyi görür, 4003 görmez (iş emri geçmişini görmeye devam eder).
4. **Ürün iade geldi:** Depo (5001) çıkışı yapılmış sevkiyattan "İade al" ile 3 kutu alır; ayrı iade paleti "kontrol
   bekliyor" açılır, eski sevkiyat ve sipariş değişmez. Kalite (4004) kararı verir. Yerine gönderim gerekiyorsa satış
   kalem kartındaki iade satırından "Yerine gönderim oluştur" der; kartta ilk sevk / iade / yerine gönderim / net görünür.
5. **Yükleme kontrolü:** Depo sevkiyatı "Hazırlandı" yapar, paletleri okutur; yanlış palet ve mükerrer okutma
   uyarılır. Depo son onayı veremez ("Son sevk onayını ambar yetkilisi verir"). Ambar yetkilisi (5002) "Sevk et",
   gerekirse araç çıkmadan "Sevki geri al", sonra "Araç çıktı".
6. **Karışım farkı:** Planlama aynı ölçüde katkı oranı farklı bir revizyonla stok üretirse bu stok onaylı kaleme
   ayrılamaz; kalem kartından gerekçeli "Teknik eşdeğerlik onayı" verilince ayrılabilir; kalem başka revizyona yeniden
   onaylanırsa onay düşer.
7. **Kapalı iş emri düzeltmesi:** Kapalı iş emrinde malzeme çıkışı düzeltmesi onaylanınca iş emri sayfası ilk kapanış ve
   güncel değeri yan yana gösterir; tolerans aşıldıysa üretim yetkilisi/planlama "Güncel dengeyi değerlendir" der.

## 7. Varsayımlar ve işletme kararı gereken sorular (kod bunlara göre çalışır; işletme onayı henüz yok)

1. **Fazla teslim toleransı 0**; fazla teslim yalnız kaleme gerekçeli, miktarı belirli izinle.
2. **Karışım farkı her zaman eşdeğerlik onayı ister** (oran toleransı yok). Örnek: LDPE %98 / katkı %2 ile onaylı kaleme
   LDPE %97 / katkı %3 stoğu ancak planlama onayıyla gider. Belirli bir oran farkının (ör. ±0,5 puan) serbest olması
   istenirse ayrı karar gerekir.
3. **Kendi düzeltmesini onaylama kapalı** (`CORRECTION_SELF_APPROVAL=0`).
4. **Talep/onay şablonları:** talep — üretim yetkilisi, planlama, satış (müşteri kapsamlı); onay — üretim yetkilisi,
   ambar yetkilisi. Operatör, kalite, ustabaşı, ambar yetkilisi ve depo personeli talep açamaz (ustabaşı/ambar için
   ayrıca atanabilir).
5. **Fiziksel çıkış kaydı** ambar yetkilisi ve yönetici; depo personeli yalnız hazırlama ve QR kontrolü. Kapıda depo
   personelinin çıkış kaydetmesi istenirse `shipment.exit` ayrıca verilir.
6. **Mal kabulünde tedarikçi parti/irsaliye no zorunlu.**
7. **Yükseltilmiş kurulumda geniş müşteri erişimleri** inceleme sonuçlanana kadar aktif kalır.
8. **Vardiya:** saat kuralı yok; devir gecikirse eski vardiya teslim alınana kadar sorumlu; vardiyası tanımsız operatöre
   teknik erişim yok. Vardiya saatleri V1 07–19, V2 19–07 örnektir.
9. **İade varsayılanı** "kontrol bekliyor"; yerine gönderim asıl siparişe bağlı kalem olarak açılır.
10. **Blokaj kaldırma:** yönetici, planlama, ustabaşı (kendi işleri), kalite. Depo yalnız blokeler.

## 8. Riskler ve sınırlar

- **Fiziksel cihaz testi yok:** el okuyucu (klavye emülasyonu) ile QR okutma ve mobil kullanım gerçek cihazda denenmedi.
- **Yazıcı:** tarayıcı yazdırma; "basıldı" kullanıcının beyanıdır, cihazdan doğrulanmaz.
- **Bobin bekletmesi ihtiyatlı:** aktarımla ilişkili paletler de bekletilir (sağlam kutular bekleyebilir).
- **Kapalı iş emrinde düzeltme:** ilk kapanış değişmez; güncel denge ve yeniden değerlendirme ayrı gösterilir. Fire,
  kapanışta girilen değerdir (sonradan düzeltilemez; gerekirse değerlendirme gerekçesinde açıklanır).
- **Şablon farkı:** özelleştirilmiş roller yükseltmede otomatik güncellenmez; yönetici rol listesindeki uyarıları
  inceleyip karar vermelidir.
- **Üretimden giriş kutu adedi** kullanıcı beyanıdır (bobin ve başlamış iş emri şartı var); sonradan düzeltme talep–onayla.
- **Çok süreçli kurulum:** hatalı giriş kilidi süreç belleğinde; paylaşılan cache gerekir. Canlı kurulum yapılandırması
  (HTTPS, gunicorn, statik dosya sunumu, `MEDIA_ROOT` yedeği) yapılmadı.
- **Rol değişikliği kapsamı:** yönetici bir şablonu genişletirse tüm atanmış kullanıcılar etkilenir; değişiklik
  geçmişte, ancak onay akışı yalnız taslak roller için var.
