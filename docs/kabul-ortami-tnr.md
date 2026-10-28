# Manuel kabul ortamı — sürüm `c316506` (teknik sipariş, reçete, baskı, repete, numune)

Bu belge yalnız kabul ortamını ve somut manuel senaryoyu anlatır. Uygulama kodu değişmedi: bu branch
(`kabul/mimcor-tnr-c316506`) `c316506` üzerine yalnız bu belgeyi ve `kabul/` altındaki hazırlık/doğrulama betiklerini
ekler. Merge, PR veya canlı yayın yok. Davranış ve kurallar: `docs/teknik-numune-repete.md`.

> **Erişim sınırı.** Ortam bulut oturum konteynerinde kuruldu; uygulama yalnız konteynerin içinden
> `http://127.0.0.1:8780` adresinde çalışıyor. Sizin tarayıcınız ve telefonunuz bu adrese **erişemez** (telefonda
> `127.0.0.1` telefonun kendisidir). Tünel, port yönlendirme veya internete açık yayın **yapılmadı**. Telefonla QR ve
> etiket yazdırma (T8) fabrika yerel ağında ayrı kurulum ister. Doğrulamalar konteyner içindeki Chromium ile yapıldı;
> **gerçek telefon testi ve gerçek kullanıcı gözlemi yapılmadı.** Otomatik kontrollerin geçmesi işletme onayı değildir.

## 1. Sürüm, çalışma dizini, başlatma

| | |
|---|---|
| Kabul commit'i | `c316506` (uzak `feature/mimcor-teknik-numune-repete` başı; daha yeni değişiklik yoktu) |
| Çalışma dizini | `/workspace/mimcor-kabul-c316506` (`git worktree add --detach … c316506`) |
| Ayarlar | Çalışma dizinindeki `.env` (git dışı, `chmod 600`): `POSTGRES_DB=mimcor_kabul_tnr_kopya`, `DJANGO_DEBUG=1` (HTTP'de giriş için; çerezler DEBUG=0'da yalnız HTTPS), `APP_BASE_URL=http://127.0.0.1:8780`, `MEDIA_ROOT=<çalışma dizini>/media` (baskı görselleri ve numune ekleri; MEDIA yayınlanmaz), `PRINT_REQUIRE_CUSTOMER_APPROVAL=1`, yeni `DJANGO_SECRET_KEY`, `KABUL_TEST_PASSWORD`. Demo parolası alınmadı. |

```
service postgresql start
cd /workspace/mimcor-kabul-c316506
python manage.py runserver 127.0.0.1:8780 --noreload
```

## 2. Kabul veritabanı

| | |
|---|---|
| Kaynak | `mimcor_uat` (değiştirilmedi) |
| Kopya | `mimcor_kabul_tnr_kopya`, `createdb -O mimcor -T mimcor_uat`, 2026-10-28T18:41:36Z (kaynakta açık bağlantı yokken) |
| Kopyada uygulanan migration'lar (29) | accounts 0005–0009 (0008 baskı görseli yetkileri, 0009 numune yetkileri), catalog 0006–0011 (0009 teknik özellikler, 0010 reçete taslağı, 0011 baskı görseli), warehouse 0002–0004 (0004 numune hareketi), production 0005–0009 (0009 iş emri görsel sürümü), corrections 0001–0002, orders 0003–0009 (0008 amaç/yöntem/ücret, 0009 repete), samples 0001 |
| Kontrol | Kopyada uygulanmamış migration 0. `mimcor_uat` hâlâ 29, `mimcor` 38 uygulanmamış; önceki kabul veritabanı `mimcor_kabul_kopya` değişmedi. |

## 3. Yetki denetimi ve kopyada yapılanlar

**Öncesi** (`yetki_denetimi`): KURAL 1 (depo rolünde son sevk onayı), İNCELE 1 (2001'in toplu taşınan üç erişimi), BİLGİ 1.
Yükseltmede depo ve üretim yetkilisi rolleri daha önce özelleştirildiği için yeni numune/görsel yetkileri bu rollere
**otomatik eklenmedi** (tasarım gereği; şablon farkı olarak raporlandı).

Hazırlık betiği (`kabul/kabul_ortami_hazirla.py`; yalnız bu sürümün kabul kopyasında çalışır) kopyada, 8101 adına,
gerekçeli rol düzenlemesiyle:

| Rol | Değişiklik | Neden |
|---|---|---|
| depo | `shipment.approve` kaldırıldı; `sample.view`, `sample.manage`, `sample.stock` eklendi | Son sevk onayı ambar yetkilisinde; numune akışı için şablondaki yeni yetkiler |
| uretim_yetkilisi | `artwork.view`, `sample.view`, `sample.manage`, `sample.decide` eklendi | Şablondaki yeni yetkiler |

Şablon toplu uygulanmadı; iki rolde eksik olan `customer.document.view` eklenmedi (BİLGİ olarak kaldı). Rol ortak
olduğundan UAT kullanıcısı 5001 de etkilenir; parolası ve ataması değişmedi. **Sonrası:** KURAL 0, ENGEL 0, İNCELE 1
(2001 — işletme kararı, dokunulmadı), BİLGİ 3. Gerçek ortamda aynı rol kararlarını yönetici rol ekranından vermelidir.

**Test hesapları** (yalnız kopyada; parola: çalışma dizinindeki `.env` → `KABUL_TEST_PASSWORD`; belgeye/Git'e/loga
yazılmadı; kendi değerinizi yazıp `python <repo>/kabul/kabul_ortami_hazirla.py --parola-yenile` çalıştırabilirsiniz):

| Personel no | Rol | Vardiya / makine / müşteri |
|---|---|---|
| 8101 | Yönetici | — |
| 8201 | Satış A | KBL-YETKILI (kalıcı), KBL-SURELI (süreli, bitiş 04.11.2026) |
| 8202 | Satış B | KBL-YETKISIZ |
| 8301 | Planlama | — |
| 8401 | Ustabaşı | V1; kabul iş emirlerinin sorumlusu |
| 8402 | Üretim yetkilisi | V1 |
| 8403 | Operatör (extruder) | V1, KBL-EXT1 |
| 8404 | Operatör (extruder) | V2, KBL-EXT1 |
| 8405 | Matbaa operatörü | V1, KBL-MAT1 |
| 8501 | Depo | — |
| 8502 | Ambar yetkilisi | — |
| 8601 | Kalite | — |

**Kurgusal kayıtlar:**

| Kayıt | Kod / adres | Durum |
|---|---|---|
| Müşteriler | KBL-YETKILI (`/musteriler/4/`), KBL-SURELI (5), KBL-YETKISIZ (6) | Kart bilgileri kurgusal |
| Körüklü baskılı ürün | KBL-KB-045 (`/urun/4/recete/`, `/urun/4/baski-gorselleri/`) | Rev.1: 400 mm kapalı en, yan körük 60 mm, 45 µm, 550 mm, mavi RAL 5015, perfore yok, baskı var (klişe yeni KL-KBL-01, kazan 600, tekrar 550, boşluk 50 mm, 2 renk, tek yüz); karışım KBL-LDPE %60, KBL-LLDPE %36, KBL-MAVI (renklendirici) %3, KBL-SLIP %1; görsel sürüm 1 iç teknik + müşteri onaylı |
| Eski tip ürünler | KBL-FILM-A (5), KBL-FILM-S (6), KBL-FILM-B (7) | Yalnız temel ölçüler; yeni alanlar "Belirtilmemiş" |
| Siparişler | SIP-2026-00002 (`/siparisler/2/`, KB-045, 20 kutu), -00003 (FILM-A, 10), -00004 (FILM-S, 5), -00005 (FILM-B, 8202) | Onaylı; kalemler teknik onaylı |
| Baskılı iş emri | IE-2026-00002 (`/is-emirleri/2/`), OP1 KBL-EXT1 (id 3), OP2 KBL-MAT1 (id 4), bugün V1 | Başladı; **görsel s.1'e sabit**; talimatlar `/talimat/1/` (extruder), `/talimat/2/` (matbaa) |
| Stok paleti | PLT-2026-00002 (`/paletler/2/`), KBL-FILM-A, 40 kutu, KBL-A-01 | Kabul + kalite uygun (IE-2026-00003 kapalı) |
| Numune kriterleri | Kalınlık (µm, aralıksız), Görünüm (metin) | Kurgusal; aralığı kalite tanımlar |
| Referans numune | NUM-2026-00001 (`/numuneler/1/`), KBL-YETKILI, 5 adet | Kalite lab / D1 / R2 / K1 |

## 4. Uygulama ve QR doğrulaması (konteyner içinden)

`kabul/ortam_dogrula.py` (yalnız GET) **37/37** geçti: 12 hesabın girişi; 8201 sipariş listesinde 00002 ve 00004 var,
00005 yok, `/siparisler/5/` 404; 8202 için `/siparisler/2/`, görsel listesi ve dosyası 404; sipariş kaleminde "Yan
körük, Kapalı en, KL-KBL-01, Onaylı sürüm 1" görünür, karışım görünmez; eski tip üründe "Belirtilmemiş"; satışa reçete
403; planlamada karışım ve "Renklendirici"; matbaa operatörü 8405 talimatta klişe ve görsel sürüm 1'i görür, karışımı
görmez, görsel önizlemesi açılır, görsel listesi 404; 8403 extruder talimatında karışımı görür, 8404 (V2) 404; kalite
numune listesi ve kriterleri; depo yeni numune formu; satış numunede yalnız paylaşılabilir notu görür (konum ve
geçmiş yok), 8202 404; numune QR içeriği `http://127.0.0.1:8780/numune/q/<anahtar>/` (SVG birebir) ve oturum açıkken
numune sayfasına gider. Bu adres telefon için geçerli değildir (T8).

## 5. Somut manuel senaryo

Sıra önemlidir. Adres öneki `http://127.0.0.1:8780`. Beklenen sağlanmazsa **şunları kaydedin:** senaryo/adım no
(ör. T3.4), personel no, tam adres, kayıt kodu, saat, görülen mesaj veya HTTP durumu (403/404/500), ekran görüntüsü,
beklenmeyen değişiklik (miktar, durum, sürüm). Hatadan sonra ortamı düzeltmeye çalışmayın.

### T1 — Teknik özellikli sipariş (ilk test, ~5 dk)

| # | Hesap | Sayfa / kayıt | İşlem | Beklenen |
|---|---|---|---|---|
| 1 | 8201 | `/siparisler/yeni/` | Müşteri "Kabul Kurgu Yetkili", ürün KBL-KB-045 → "İleri: özellikler ve miktar". | Ürün listesinde yalnız KBL-YETKILI ürünleri. 2. adımda teknik kart: Yan körük, Kapalı en, 60 mm, KL-KBL-01, "Onaylı sürüm 1"; karışım yok. |
| 2 | 8201 | 2. adım | Miktar `12,5`, amaç Numune, ücret Ücretsiz → İleri. | Miktar alanının yanında "Kutu biriminde miktar tam sayı olmalı (12,5 girildi)"; yazılanlar korunur. |
| 3 | 8201 | 2. adım | Miktar `10` → İleri. | 3. adım: özet, "Düzenle" düğmeleri, teslimat adresi. |
| 4 | 8201 | 3. adım | İşaretlemeden "Siparişi kaydet". | Teslimat doğrulama uyarısı; kayıt yok. İşaretleyip kaydedin → "Sipariş oluşturuldu: SIP-…" ve sıradaki adım. |
| 5 | 8201 | Yeni sipariş | "Siparişi onayla"; kalem kartında "Teknik özellikleri göster". | Planlama onayı beklediği yazar; özellikler ürünün güncel revizyonundan. |
| 6 | 8201 | `/siparisler/3/` | Kalemin teknik özelliklerini açın. | Körük/baskı/perfore "Belirtilmemiş" (eski tip kayıt). |

### T2 — Reçete taslağı (~5 dk)

| # | Hesap | Sayfa | İşlem | Beklenen |
|---|---|---|---|---|
| 1 | 8201 | `/urun/4/recete/` | Açın. | 403 — satış reçeteyi göremez. |
| 2 | 8301 | `/urun/4/recete/` → "Değiştir (yeni revizyon taslağı)" → "Taslak aç" | "+ Malzeme ekle"; ilk satır (KBL-LDPE) oranını `57` yapın. | Yeni satır açılır; toplam "Karışım toplamı %97. Onaylamak için %100 olmalı." |
| 3 | 8301 | Taslak | Gerekçe yazıp "Onayla ve revizyon oluştur". | "Onaylanmadı (taslak kaydedildi)"; taslak ve satırlar kalır. |
| 4 | 8301 | Taslak | Aynı malzemeyi iki satırda seçip kaydedin. | Malzemenin yanında "iki kez yer alamaz". Düzeltin. |
| 5 | 8301 | Taslak | Parti `250` yazıp kaydedin; tabloya bakın. | 250 kg ve 100 kg karşılıkları; varsa yuvarlama farkı ayrı. |
| 6 | 8301 | Taslak | Oranı `60` yapıp onaylayın. | "Reçete onaylandı: KBL-KB-045 Rev.2". IE-2026-00002 Rev.1'de kalır (`/is-emirleri/2/`). |

### T3 — Baskı görseli ve iş emri (~5 dk)

| # | Hesap | Sayfa | İşlem | Beklenen |
|---|---|---|---|---|
| 1 | 8405 | `/talimat/2/` | Açın. | Klişe KL-KBL-01, kazan 600, tekrar 550, boşluk 50 mm, görsel "Sürüm 1" küçük önizleme; karışım yok. |
| 2 | 8201 | `/urun/4/baski-gorselleri/` | Herhangi bir PNG/JPG (kurgusal) yükleyin. | Sürüm 2 "Taslak · onay yok". |
| 3 | 8201 | Sürüm 2 | "Müşteri onayını kaydet" (kişi + kanıt). | "İç teknik onaylı" değil → geçerli olmaz; satış iç teknik onay düğmesini görmez. |
| 4 | 8301 | Sürüm 2 | "İç teknik onay ver". | Sürüm 2 "Geçerli". |
| 5 | 8405 | `/talimat/2/` | Yenileyin. | Hâlâ **Sürüm 1** (eski iş emri değişmez). |
| 6 | 8202 | `/urun/4/baski-gorselleri/`, `/baski-gorseli/1/dosya/` | Açın. | 404. |

### T4 — Repete (~4 dk)

| # | Hesap | Sayfa | İşlem | Beklenen |
|---|---|---|---|---|
| 1 | 8201 | `/siparisler/2/` → "Tekrar sipariş (repete)" | SIP-2026-00002/01 işaretli gelir; yeni miktar 15 → İleri. | Kaynak sipariş, ürün ve Rev.1 görünür; Rev.2 varsa "güncel revizyon Rev.2; otomatik değiştirilmez" uyarısı. |
| 2 | 8201 | 3. adım | Amaç Ticari, teslimatı işaretleyip kaydedin. | Yeni taslak sipariş "Repete"; kalemde rezervasyon/iş emri/sevk yok; kalem planlama onayı bekler. |
| 3 | 8201 | Aynı sayfa | "Tekrar siparişi kaydet"e iki kez hızlıca basın (yeni başlangıçla). | Tek sipariş oluşur. |

### T5 — Revizyonlu repete (~5 dk)

| # | Hesap | Sayfa | İşlem | Beklenen |
|---|---|---|---|---|
| 1 | 8201 | `/siparisler/tekrar/?musteri=4&kaynak=2` | Kalemi işaretleyin, miktar 5; "Teknik değişiklik iste" → işaret; en `420`; gerekçesiz İleri. | Gerekçe uyarısı; girilenler korunur. |
| 2 | 8201 | Aynı | Gerekçe "Müşteri 420 mm istedi (kurgusal)" → İleri → kaydedin. | Özette En 400 mm → 420 mm; sipariş "Revizyonlu repete". |
| 3 | 8301 | Planlama listesi → "Değişiklik isteği" | Açın; kalemi teknik onaylamayı deneyin. | Eski/yeni yan yana; karışım satır yok; onay "karara bağlanmadan verilemez". |
| 4 | 8301 | Değişiklik isteği | "Aynı ürün koduna yeni teknik revizyon" → taslakta en 420 ve karışım temel revizyondan → onaylayın. | İstek "Yeni teknik revizyon oluşturuldu"; kalem yeni revizyonla onaylanabilir; SIP-2026-00002 ve IE-2026-00002 değişmez. |

### T6 — Numune ve stok (~6 dk)

| # | Hesap | Sayfa | İşlem | Beklenen |
|---|---|---|---|---|
| 1 | 8501 | `/numuneler/yeni/` | Şahit numunesi, KBL-YETKILI, KBL-FILM-A, 2 kutu, oda "Numune odası" → kaydedin. | Kod NUM-2026-00002; sıradaki adım: QR ve stoktan ayır. |
| 2 | 8501 | Numune → "Stoktan ayır" | Palet PLT-2026-00002, 2 kutu. | "tek stok hareketi"; `/paletler/2/` 38 kutu. Tekrar denemede "ikinci kez düşülmez". |
| 3 | 8501 | Numune | "Müşteriye gönder" (kime) → "Geri döndü" → "İmha edildi" (gerekçe). | Her adım geçmişte; palet hâlâ 38 kutu. |
| 4 | 8501 | `/numuneler/1/` (referans) | "Stoktan ayır" arayın. | Yok — müşteri referansı stok etkilemez. |
| 5 | 8601 | `/numuneler/kriterler/` | Kalınlık için alt 42, üst 48 ile "Değiştir". | Eski kriter "Değiştirildi", yenisi kullanımda. |
| 6 | 8601 | NUM-2026-00002 | Ölçüm: Kalınlık 49 → "Aralık dışında"; karar "Uygun değil", kapsam PLT-2026-00002. | Paletin kalite durumu **değişmez** (`/paletler/2/` uygun kalır); karar yalnız numunede. |

### T7 — Satış görünümü ve izolasyon (~3 dk)

| # | Hesap | Sayfa | İşlem | Beklenen |
|---|---|---|---|---|
| 1 | 8201 | NUM-2026-00002 | Açın; müşteri geri bildirimi "Müşteri onayladı" kaydedin. | Yalnız durum, paylaşılabilir not, geri bildirim; ölçüm/iç karar/konum/geçmiş yok. İç karar değişmez. |
| 2 | 8202 | `/numuneler/1/`, `/numuneler/2/`, `/siparisler/2/` | Açın. | Hepsi 404. |

### T8 — Telefonla QR açma ve etiket yazdırma (ayrı manuel adım; bu ortamda yapılamaz)

Fabrika yerel ağındaki bir bilgisayarda `c316506` ayrı dizine alınır; `mimcor_uat`'tan yeni kabul kopyası açılıp
migrate edilir ve hazırlık betiği çalıştırılır. `.env`: `APP_BASE_URL=http://<yerel IP>:8000`,
`DJANGO_ALLOWED_HOSTS=<IP>,127.0.0.1`, `DJANGO_CSRF_TRUSTED_ORIGINS=http://<IP>:8000`, `DJANGO_DEBUG=1`,
`MEDIA_ROOT=<git dışı klasör>`; `python manage.py runserver 0.0.0.0:8000 --noreload` — yalnız yerel ağ, internete açık
değil.

| # | Hesap / cihaz | İşlem | Beklenen | Kaydedilecek |
|---|---|---|---|---|
| 1 | 8501, bilgisayar | `/paletler/2/etiket/` ve `/numuneler/<id>/etiket/` → Yazdır, etiket yazıcısında basın. | Kod, ürün, QR okunaklı; palet basımı kayıtlı. | Yazıcı modeli, etiket boyutu, fotoğraf. |
| 2 | Telefon (oturum kapalı) | Numune QR'ını okutun. | `http://<IP>:8000/numune/q/…/` → giriş → numune sayfası; durum/stok değişmez. | Telefon/OS/tarayıcı, mesafe, açılan adres. |
| 3 | Telefon, 8202 | Aynı QR. | 404 (yetkisiz müşteri). | Görülen mesaj. |
| 4 | Telefon, 8405 | Matbaa talimatı `/talimat/2/` | Görsel önizleme telefonda okunur. | Ekran görüntüsü. |

## 6. Kalan sınırlar

- Uygulama yalnız konteyner içinden erişilebilir; kullanıcı tarayıcısı ve telefon erişimi yok (T8 ayrı kurulum ister).
- Konteyner yeniden başlarsa PostgreSQL ve sunucu yeniden başlatılmalı; oturum kapanırsa ortam kaybolur
  (`pg_dump mimcor_kabul_tnr_kopya` ve `media/` klasörünün yedeği alınmalı).
- İşletme kararı bekleyen konular (klişe kazan aralığı terimi, körüklüde en ölçümü, baskıda müşteri onayı şartı,
  geri dönen numunenin stoğa alınması, numune kabul aralıkları ve önceki turun dört konusu) değiştirilmedi.
