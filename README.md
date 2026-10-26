# Mimcor Plastik – Üretim ve Depo Demo Uygulaması

Tarayıcıdan çalışan üretim ve depo uygulamasının **demo** sürümü. Gerçek işletmeye geçiş için değildir.
Bu sürümde muhasebe, Mikro entegrasyonu, e-belge, terazi/sensör bağlantısı, otomatik çizelgeleme ve
maliyet muhasebesi **yoktur**.

Teknoloji: Python 3.12+ / Django 5.2 LTS, PostgreSQL 14+, Django şablonları ve az miktarda JavaScript.

## Kurulum

```bash
# 1) Python bağımlılıkları (sanal ortam önerilir)
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# 2) PostgreSQL veritabanı ve kullanıcısı (örnek; parolayı kendiniz belirleyin)
sudo -u postgres psql -c "CREATE USER mimcor WITH PASSWORD 'yerel-parolaniz' CREATEDB;"
sudo -u postgres createdb -O mimcor mimcor

# 3) Ortam değişkenleri
cp .env.example .env
#   .env içinde DJANGO_SECRET_KEY, POSTGRES_PASSWORD ve DEMO_USER_PASSWORD değerlerini doldurun.
#   Rastgele anahtar üretmek için:
python -c "import secrets; print(secrets.token_urlsafe(50))"

# 4) Veritabanı geçişleri ve kurgusal örnek veri
python manage.py migrate
python manage.py seed_demo          # tekrar çalıştırılabilir; var olan kayıtları atlar

# 5) Çalıştırma
python manage.py runserver 0.0.0.0:8000
```

Telefondan aynı ağ üzerinden denemek için `.env` içindeki `DJANGO_ALLOWED_HOSTS` değerine sunucu
IP adresini ekleyin (ör. `localhost,127.0.0.1,192.168.1.20`) ve `http://192.168.1.20:8000` adresini açın.
`DJANGO_DEBUG=0` iken oturum çerezleri yalnız HTTPS üzerinden gönderilir.

## Testler

```bash
python manage.py test
```
Testler PostgreSQL üzerinde çalışır (Django geçici bir `test_mimcor` veritabanı oluşturur; kullanıcının
`CREATEDB` yetkisi olmalıdır).

## Demo kullanıcıları (`seed_demo`)

Parola tüm kullanıcılar için `.env` içindeki `DEMO_USER_PASSWORD` değeridir.

| Personel no | Ad | Rol |
|---|---|---|
| 1001 | Ayşe Yönetici | Yönetici (ayrıca `/yonetim/` paneli) |
| 2001 | Selin Satış | Satış |
| 3001 | Murat Planlama | Planlama |
| 4001 | Hasan Ustabaşı | Üretim / Ustabaşı |
| 5001 | Deniz Depo | Depo |

Tüm müşteri, ürün ve üretim verileri kurgusaldır.

## Proje yapısı

| Modül | İçerik |
|---|---|
| `core` | Kod üretimi (SIP-2026-00001…), işlem geçmişi (AuditLog), mükerrer istek koruması, ortak şablonlar |
| `accounts` | Personel no + parola ile giriş, roller, yetki matrisi (`accounts/permissions.py`), oturum zaman aşımı |
| `catalog` | Müşteri, ürün kodu, makine, hammadde/katkı, malzeme lotu ve lot hareketleri, reçete revizyonları |
| `orders` | Satış siparişi, sipariş kalemi, planlama teknik onayı, satış ilerleme görünümü |
| `production` | İş emri, operasyon sırası ve makine ataması, ustabaşı başlat/tamamla |
| `production` (devam) | Bobin kaydı ve kaynak–çıktı ilişkisi, malzeme çıkışı/iadesi, iş emri kapanışı |
| `warehouse` | Palet, palet hareketleri, tartım/ağırlık hesabı, QR ve etiket, depo kabulü, aktarım/sayım/hasar, rezervasyon, sevkiyat |

İş kuralları her modülün `services.py` dosyasındadır; view'lar yalnız formu doğrulayıp servisi çağırır,
şablonlarda iş mantığı yoktur.

## Demo senaryoları ve devir

- Adım adım demo akışları (A: sipariş üretimi, B: stoktan kısmi sevkiyat): [`docs/demo-senaryolari.md`](docs/demo-senaryolari.md)
- Çalışan özellikler, test sonuçları, bilinen eksikler ve varsayımlar: [`HANDOFF.md`](HANDOFF.md)

`seed_demo` ayrıca Demo B için müşteri stoğu amaçlı üretilmiş, kabul edilmiş ve kalitesi uygun 40 kutulu bir palet oluşturur.
Demoyu sıfırdan başlatmak için veritabanını silip yeniden oluşturun:

```bash
sudo -u postgres dropdb mimcor && sudo -u postgres createdb -O mimcor mimcor
python manage.py migrate && python manage.py seed_demo
```

## UI/UX demo doğrulaması

Tasarım çalışması mevcut Django uygulaması üzerindedir. Ekranlar, kapsam ve sonuçlar:
[UI/UX doğrulama notu](docs/ui-ux-dogrulama.md).

```bash
pip install -r requirements-dev.txt
# Chromium sistemde değilse Playwright Chromium'u kurun:
python -m playwright install chromium
# Test dosyası varsayılan olarak /usr/bin/chromium kullanır.
# Başka kurulumda CHROMIUM_PATH değerini tarayıcı yürütülebilir dosyasına ayarlayın.
python manage.py test --noinput
python manage.py test ui_checks.browser_checks --noinput
```

Tarayıcı testleri ayrı PostgreSQL test veritabanını oluşturur ve siler; yerel demo verilerini değiştirmez.
İki test Demo A/B'yi 1440 ve 390 px ekranlarda çalıştırır, `docs/screenshots/` görüntülerini yeniler.
Telefon görünümü emülasyondur; sunum öncesi gerçek cihaz ve tarayıcıyla ayrıca deneyin.
