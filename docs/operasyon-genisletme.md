# Operasyon genişletmesi — teknik teslim (feature/mimcor-operasyon-genisletme)

Kaynak: `design/mimcor-demo-2026-10-30` @ `5bc44c2`. Geliştirme branch'i: `feature/mimcor-operasyon-genisletme`.
Kaynak branch'e merge, force push veya canlı yayın yapılmadı. Aşama commit'leri:

| Commit | Aşama |
|---|---|
| `b39da11` | 1 — Yönetilebilir roller/kapsamlar, İK, satışçı–müşteri erişimi, genişletilmiş müşteri kartı, oturum süresi (bulgu 12) |
| `1d9be13` | 2 — İş emri ve sipariş yaşam döngüsü, ustabaşı planı, tek aktif iş (bulgu 7), duruş/vardiya devri, makine talimatı |
| `df631b8` | 3 — Düzeltme talebi/onayı, kalite blokaj kaldırma, teknik uygunluk ve miktar bulguları (1, 2, 3, 6, 8, 9, 11) |
| `e1183e2` | 4 — Sevkiyat aşamaları + QR yükleme kontrolü, geri alma, iade, etiket, stok seviyesi, iş listeleri |
| (son commit) | Kabul: saha senaryosu tarayıcı testleri, sunum etiketleri, belge |

## 1. Yetki modeli

- **Eylem kataloğu** kodda (`accounts/catalog.py`): her eylemin türü ayrı — görme, oluşturma, değiştirme, onay, iptal,
  düzeltme talebi, düzeltme onayı, yazdırma. Roller veritabanında (`AccessRole`, `RolePermission`, `UserRole`); yönetici
  ekrandan rol oluşturur/değiştirir (Kullanıcılar → Roller).
- **Kapsamlar:** `T` tüm kayıtlar, `M` erişim verilen müşteriler, `A` kendisine atanmış işler (ustabaşı),
  `Mk` atandığı makineler, `V` vardiyası (tanımlı; varsayılan şablonlarda kullanılmıyor, rol düzenlemede seçilebilir).
- **Birleşim kuralı:** kullanıcının yetkileri aktif rollerinin birleşimidir; bir eylemin kapsamı o eylemi veren rollerin
  kapsamlarının birleşimidir. `T` yalnız bir rol açıkça `T` veriyorsa geçerlidir. Kapsam kuralı tanımsız modelde o
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

### Başlangıç şablonları (veritabanına yazılır; yönetici değiştirebilir)

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
| `machine.view` Makineleri görme | T | · | T | T | T | T | T | T | T | T |
| `machine.manage` Makine ekleme/değiştirme | T | · | · | T | · | · | · | · | · | · |
| `material.view` Hammadde/katkı ve lotları görme | T | · | · | T | T | T | · | · | T | T |
| `material.manage` Hammadde/katkı tanımlama | T | · | · | T | · | · | · | · | · | · |
| `lot.receive` Malzeme lot girişi | T | · | · | · | · | · | · | · | T | T |
| `lot.adjust` Lot sayım düzeltmesi (doğrudan) | T | · | · | · | · | · | · | · | T | T |
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
| `production.execute` Üretim kaydı (başlat, bobin, malzeme, palet, kapanış) | T | · | · | · | T | A | Mk | · | · | · |
| `production.hold` Duruş/devam ve vardiya devri | T | · | · | · | T | A | Mk | · | · | · |
| `instruction.view` Operasyon teknik talimatını görme ve teslim alma | T | · | · | T | T | A | Mk | · | · | · |
| `instruction.print` Makine iş talimatı yazdırma | T | · | · | T | T | A | · | · | · | · |
| `material.deviation` Reçete dışı malzeme kullanımına gerekçeli izin | T | · | · | T | T | · | · | · | · | · |
| `quality.decide` Kalite: uygun/kontrol bekliyor kararı | T | · | · | · | T | A | · | T | · | · |
| `quality.block` Kalite blokajı koyma | T | · | · | · | T | A | Mk | T | T | T |
| `quality.release` Kalite blokajını gerekçeyle kaldırma | T | · | · | T | · | A | · | T | · | · |
| `stock.view` Palet/stok görme | T | · | M | T | T | T | · | T | T | T |
| `warehouse.operate` Depo kabul, tartım, aktarım, sayım, hasar, konum | T | · | · | · | · | · | · | · | T | T |
| `reservation.manage` Siparişe ayırma, azaltma, iptal | T | · | M | · | · | · | · | · | T | T |
| `shipment.view` Sevkiyatları görme | T | · | M | T | T | · | · | · | T | T |
| `shipment.manage` Sevkiyat hazırlama, yükleme kontrolü, iade kabulü | T | · | · | · | · | · | · | · | T | T |
| `shipment.approve` Sevk onayı ve sevk geri alma | T | · | · | · | · | · | · | · | T | T |
| `label.print` Palet/kutu etiketi yazdırma | T | · | · | · | · | · | · | · | T | T |
| `stocklevel.manage` Hedef/alt stok seviyesi tanımlama | T | · | · | T | · | · | · | · | · | · |
| `correction.request` Düzeltme talebi açma | T | · | M | T | T | · | · | · | · | · |
| `correction.approve` Düzeltme talebini onaylama/reddetme | T | · | · | · | T | · | · | · | T | · |

İK atayabilir: Satış, Planlama, Üretim yetkilisi, Ustabaşı, Usta / operatör, Kalite sorumlusu, Ambar yetkilisi, Depo personeli

## 2. Satışçı–müşteri erişimi

- Müşteriyi açan satışçıya otomatik **kalıcı** erişim; yönetici (veya `customer.access.manage`) kalıcı ya da
  **başlangıç–bitiş tarihli geçici** erişim verir/kaldırır. Süre dolunca erişim sorguda tarihle düşer (zamanlanmış iş
  gerekmez). Verilen/kaldırılan erişim müşteri kartı geçmişinde.
- Müşteri kapsamı arama, URL, ürün listesi endpoint'i (`/siparisler/musteri/<id>/urunler/`), dashboard, raporlar,
  stok özeti, sevkiyat, belge indirme ve düzeltme taleplerinde aynı fonksiyondan (`accounts/scope.py`) geçer.
- Operasyon rolleri (planlama, üretim, depo) müşteri **operasyon** bilgisini `T` kapsamla görür; **ticari** bilgi
  (vade, ödeme, para birimi, fatura adresi) ayrı `customer.commercial.view` yetkisidir.
- Upgrade: mevcut satış kullanıcılarına mevcut müşterilere kalıcı erişim verildi (davranış değişmesin diye);
  yönetici kısıtlamalıdır (bkz. işletme soruları).

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
| 1 | Vardiya kapsamı | Kısmi | Kapsam tanımlı ve sorguya bağlı; hiçbir varsayılan şablon kullanmıyor |
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
| — | Tarayıcı testlerinin CI'da çalışması | **Yapılmadı** | CI yalnız Django testlerini çalıştırır |

### Bulgular (1–12)

| # | Düzeltme | Regresyon testi | Düzeltme öncesi başarısızlık kanıtı |
|---|---|---|---|
| 1 | Teknik imza (en, kalınlık, boy, renk, baskı, perfore) uyuşmazsa ayırma/sevk/aktarım reddi; yetkili gerekçeli istisna | `core/tests/test_findings.py::Finding1…` | Var (FAIL) |
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

## 5. Test sonuçları (26.10.2026, PostgreSQL 16, Chromium 1243 headless)

- Django: **248 test, tamamı başarılı** (`python3 manage.py test accounts catalog orders production warehouse core corrections`).
  Not: `--parallel` ile çalıştırmada bir hata olduğunda Django çalıştırıcısı "cannot pickle traceback" ile çöküyor;
  hata ayıklarken seri çalıştırın.
- Tarayıcı (yalnız yerelde, **CI'da yok**): `ui_checks.browser_checks` (Demo A + B, 1440 ve 390 px),
  `ui_checks.order_product_filter_checks` (2), `ui_checks.field_scenarios_checks` (4 saha senaryosu, 1440 ve 390 px)
  — **6/6 başarılı**. 390 px bir **ekran emülasyonudur; gerçek telefonda deneme yapılmadı**.
- Eş zamanlılık: aynı sevkiyatın iki kez onayı, aynı form anahtarı, bobin blokajı–sevk yarışı, paralel ayırma (bulgu 2).

Çalıştırma: `CHROMIUM_PATH=<chromium> python3 manage.py test ui_checks.browser_checks ui_checks.order_product_filter_checks ui_checks.field_scenarios_checks`
(`browser_checks` `docs/screenshots` altına yazar; istemeden değişiklik commit'lenmesin).

### UAT senaryoları (kullanıcı kabulü için)

1. **Müşteri değiştirdi:** Satış (2001) onaylı kalemde miktarı 20 → 15 yapar; ekran etkilenen iş emrini listeler ve onay
   ister. İş emri hedefi 20 kalır, iş emri sayfasında çelişki görünür. Planlama (3001) iş emrini gerekçeyle geri çeker
   (basılı talimatlar geçersiz damgalanır), hedefi 15 yapar, yeniden serbest bırakır.
2. **Usta yanlış girdi:** Üretim yetkilisi (4002) bobin sayfasından 97 → 79 kg düzeltme talebi açar; kayıt değişmez.
   Ambar yetkilisi (5002) "Düzeltme talepleri"nden onaylar; bobin 79 kg olur, geçmişte eski değer kalır. 4002 kendi
   talebini onaylayamaz.
3. **Vardiya bitti:** Ustabaşı (4001, V1) çalışan operasyonda "Vardiya devri" ile kalan işi ve makine başı malzemeyi yazar
   (açık bobinler otomatik eklenir). Başka bir yetkili (4002) devri teslim alır; iş emri ve hedef sürer.
4. **Ürün iade geldi:** Depo (5001) çıkışı yapılmış sevkiyattan "İade al" ile 3 kutu alır; ayrı iade paleti "kontrol
   bekliyor" açılır, eski sevkiyat ve sipariş değişmez. Kalite (4004) kararı verir (ör. blokeli).
5. **Yükleme kontrolü:** Depo sevkiyatı "Hazırlandı" yapar, paletleri okutur; yanlış palet ve mükerrer okutma
   uyarılır, eksik palet varken "Sevk et" çıkmaz. Sevk sonrası araç çıkmadan "Sevki geri al" stoğu geri koyar.

## 6. Varsayımlar ve işletme kararı gereken sorular

1. **Fazla teslim toleransı 0.** Örnek: 10 kutuluk kaleme 12 kutu göndermek için planlama/yönetici "Fazla teslim izni
   +2" verir. Müşteri bazında sabit tolerans (ör. %5) istenirse ayrı karar gerekir.
2. **Teknik uygunluk imzası:** en, kalınlık, rulo boyu, renk, baskı, perfore. Karışım oranı farkı tek başına
   uyumsuzluk sayılmıyor (aynı ölçü/renk, farklı katkı → uyumlu). Örnek: Rev.1 ve Rev.2 yalnız katkı oranında farklıysa
   stok paleti kaleme ayrılabilir. Karışım farkı da engellemeli mi?
3. **Kendi düzeltmesini onaylama kapalı** (`CORRECTION_SELF_APPROVAL=0`).
4. **Talep/onay şablonları:** talep — üretim yetkilisi, planlama, satış (müşteri kapsamlı); onay — üretim yetkilisi,
   ambar yetkilisi; operatör ve kalite talep açamaz; ustabaşı ve ambar yetkilisinin talep hakkı varsayılan kapalı,
   rol düzenlemeyle verilebilir.
5. **Blokaj kaldırma:** yönetici, planlama, ustabaşı (kendi işleri), kalite. Depo yalnız blokeler.
6. **Sevk onayı ve geri alma** (`shipment.approve`) şablonda hem ambar yetkilisi hem depo personelinde. Yalnız ambar
   yetkilisi mi onaylamalı?
7. **Vardiya saatleri** V1 07–19, V2 19–07 örnektir (ekrandan değiştirilebilir).
8. **İade varsayılanı** "kontrol bekliyor"; iade sipariş durumunu değiştirmez (yeniden sevk gerekiyorsa yeni sipariş/kalem).
9. Upgrade'de mevcut satışçılara mevcut müşterilere kalıcı erişim verildi; işletme isterse daraltılmalı.

## 7. Riskler ve sınırlar

- **Fiziksel cihaz testi yok:** el okuyucu (klavye emülasyonu) ile QR okutma ve mobil kullanım gerçek cihazda denenmedi.
- **Yazıcı:** tarayıcı yazdırma; "basıldı" kullanıcının beyanıdır, cihazdan doğrulanmaz.
- **Bobin bekletmesi ihtiyatlı:** aktarımla ilişkili paletler de bekletilir (sağlam kutular bekleyebilir).
- **Malzeme düzeltmesi tamamlanmış/kapalı iş emrinde:** kapanış dengesi kapanış anındaki değerlerle kalır; fark
  geçmişte görünür, kapanış yeniden hesaplanmaz.
- **Çok süreçli kurulum:** hatalı giriş kilidi süreç belleğinde; paylaşılan cache gerekir. Canlı kurulum yapılandırması
  (HTTPS, gunicorn, statik dosya sunumu, `MEDIA_ROOT` yedeği) yapılmadı.
- **Rol değişikliği kapsamı:** yönetici bir şablonu genişletirse tüm atanmış kullanıcılar etkilenir; değişiklik
  geçmişte, ancak onay akışı yalnız taslak roller için var.
