# Mimcor — kendi bilgisayarınızda Docker ile kurulum

Bu belge uygulamayı **kendi bilgisayarınızda** Docker Desktop ile çalıştırmayı anlatır. Branch:
`chore/mimcor-local-docker` (kod tabanı `fix/mimcor-demo-son-duzeltmeler`, `0abbc87`).

> **Önemli:** Bu yapılandırma bir bulut oturum konteynerinde (Linux) denendi. Oradaki `localhost` o konteynerin
> kendisidir; **sizin bilgisayarınızın `localhost`'u değildir**. Kurulum sizin bilgisayarınızda yapılmadı ve Windows /
> Docker Desktop üzerinde denenmedi. Aşağıdaki komutları siz çalıştırdığınızda ortam sizin bilgisayarınızda oluşur.

## 1. Ne kurulur

| Servis | Görev | Dışarı açık mı |
|---|---|---|
| `db` | PostgreSQL 16.15 (veritabanı) | Hayır — port yayınlanmaz |
| `app` | Django uygulaması, gunicorn (geliştirme sunucusu değil), Python 3.12.16 | Hayır |
| `web` | Caddy 2.11.7: statik dosyalar + uygulamaya yönlendirme | Yalnız `127.0.0.1` (bu bilgisayar) |

Üç ayrı ortam aynı yapılandırmadan çalışır; her birinin veritabanı, yüklenen dosyaları, ağı ve portu ayrıdır:

| Ortam | Amaç | Yerel HTTP adresi | Güvenlik profili (HTTPS) adresi |
|---|---|---|---|
| `demo` | Sunum | http://localhost:8080 | https://localhost:8443 |
| `astra` | Astra güvenlik testi | http://localhost:8081 | https://localhost:8444 |
| `claude` | Claude güvenlik testi | http://localhost:8082 | https://localhost:8445 |

Bir ortam ya yerel HTTP ya da güvenlik (HTTPS) profiliyle kurulur (aynı anda ikisi değil). Ortam adı Docker'daki proje
adıdır (`mimcor-demo` vb.): aynı bilgisayarda depoyu **tek klasörde** tutun; iki ayrı klondan aynı ortamı kurmak
aynı veritabanını kullanır.

Veri: veritabanı ve yüklenen dosyalar Docker volume'larında tutulur; durdurma/yeniden başlatma veriyi silmez.
Demo verisi tamamen kurgusaldır (`seed_demo`); gerçek veritabanı yedeği veya müşteri verisi kullanılmaz.

## 2. Ön gereksinimler (Windows)

- Windows 10/11 64 bit, sanallaştırma açık (BIOS/UEFI).
- **Docker Desktop** (WSL 2 altyapısıyla). Kurulumdan sonra Docker Desktop açık olmalı.
  Kontrol: `docker compose version` → **2.24 veya üstü** olmalı (`compose.secure.yml` `!override` kullanır).
- **Git for Windows**.
- Boş bellek: ortam başına yaklaşık 1,5 GB (sınırlar: uygulama 768 MB, veritabanı 512 MB, web 128 MB); disk ~3 GB.
- İnternet: ilk kurulumda imajlar ve Python paketleri indirilir. Uygulama çalışırken internete açılmaz.

PowerShell betikleri için her komutun başında `powershell -ExecutionPolicy Bypass -File` kullanılır; sistem
ayarınızı değiştirmeniz gerekmez.

## 3. İlk kurulum (PowerShell)

```powershell
cd C:\
git clone https://github.com/dursunzeynelmurat-ui/Erpmimcor.git mimcor
cd C:\mimcor
git checkout chore/mimcor-local-docker
powershell -ExecutionPolicy Bypass -File .\scripts\mimcor.ps1 init demo
```

`init` sırasıyla şunları yapar (yalnız ilk kurulumda; `env\demo.env` varsa çalışmaz):
1. `env\demo.env` dosyasını **rastgele** `DJANGO_SECRET_KEY`, veritabanı parolası ve demo kullanıcı parolasıyla üretir
   (Git'e girmez; yalnız sizin kullanıcınız okuyabilir).
2. Uygulama imajını derler.
3. Veritabanını başlatır ve **hazır olmasını bekler** (`pg_isready`).
4. `migrate`, `createcachetable`, `seed_demo` (kurgusal demo verisi).
5. Servisleri başlatır.

**Beklenen sonuç:** son satır
`Hazır: http://localhost:8080/  (kullanıcı parolası: env/demo.env -> DEMO_USER_PASSWORD)`.
İlk kurulum internet hızına göre birkaç dakika sürer.

## 4. Giriş

Tarayıcıda **http://localhost:8080** adresini açın.

Demo kullanıcılarının hepsinin parolası aynıdır ve `env\demo.env` dosyasındadır:

```powershell
Select-String -Path env\demo.env -Pattern '^DEMO_USER_PASSWORD='
```

| Personel no | Kişi (kurgusal) | Rol |
|---|---|---|
| 1001 | Ayşe Yönetici | Yönetici |
| 2001 | Selin Satış | Satış (M-ORNEK, M-DENEME) |
| 2002 | Kerem Satış | Satış (M-KURGU; M-ORNEK'e 7 günlük süreli erişim) |
| 3001 | Murat Planlama | Planlama |
| 4001 | Hasan Ustabaşı | Ustabaşı |
| 4002 | Elif Üretim Yetkilisi | Üretim yetkilisi |
| 4003 / 4005 | Can / Ece | Operatör (V1 / V2) |
| 4004 | Zeynep Kalite | Kalite |
| 5001 | Deniz Depo | Depo |
| 5002 | Burak Ambar | Ambar yetkilisi |
| 6001 | Gül İK | İK |

Normal başlatma (`up`) parolaları ve veriyi değiştirmez. `seed` yalnız eksik demo kayıtlarını ekler; var olan
kullanıcının parolasına dokunmaz.

## 5. Günlük kullanım

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\mimcor.ps1 up demo       # başlat
powershell -ExecutionPolicy Bypass -File .\scripts\mimcor.ps1 down demo     # durdur (veri korunur)
powershell -ExecutionPolicy Bypass -File .\scripts\mimcor.ps1 status demo   # durum
powershell -ExecutionPolicy Bypass -File .\scripts\mimcor.ps1 logs demo     # tüm loglar (Ctrl+C ile çık)
powershell -ExecutionPolicy Bypass -File .\scripts\mimcor.ps1 logs demo app # yalnız uygulama
powershell -ExecutionPolicy Bypass -File .\scripts\mimcor.ps1 url demo      # adres
```

Kod güncellemesinden sonra (`git pull`):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\mimcor.ps1 build demo
powershell -ExecutionPolicy Bypass -File .\scripts\mimcor.ps1 migrate demo
powershell -ExecutionPolicy Bypass -File .\scripts\mimcor.ps1 up demo
```

Mac/Linux'ta aynı komutlar: `./scripts/mimcor.sh <komut> <ortam>` (`-Secure` yerine `--secure`, `-Force` yerine
`--force`).

## 6. Yedekleme ve geri yükleme

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\mimcor.ps1 backup demo
# → backups\demo-YYYYMMDD-HHMMSS\mimcor.dump (veritabanı) ve media.tar (yüklenen dosyalar)

powershell -ExecutionPolicy Bypass -File .\scripts\mimcor.ps1 restore demo backups\demo-YYYYMMDD-HHMMSS
```

- Yedek kişisel veri ve parola özetleri içerir; klasör yalnız sizin kullanıcınıza açılır. Paylaşmayın, Git'e eklemeyin
  (`backups\` Git dışıdır).
- Geri yükleme hedef ortamın veritabanını ve dosyalarını **yedektekiyle değiştirir**. Kullanıcı parolaları da
  yedekten gelir (başka ortamın yedeği yüklenirse o ortamın demo parolası geçerli olur).
- Başka ortamın yedeği de yüklenebilir (ör. demo yedeğini astra'ya).

## 7. Yalnız bir test ortamını sıfırlama

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\mimcor.ps1 reset astra
```

Yalnız `mimcor-astra` projesinin container, ağ ve volume'larını siler, sonra temiz kurulum (migrate + demo verisi)
yapar. `demo` ve `claude` etkilenmez; genel temizlik komutu (`docker system prune`, `docker volume prune`)
kullanılmaz ve **kullanmayın**. `demo` ortamını sıfırlamak için bilerek `-Force` eklemek gerekir:
`... reset demo -Force`. Ayar dosyası (`env\astra.env`) ve sırları korunur.

## 8. Güvenlik denetimi profili (HTTPS, DEBUG=0, çoklu worker)

Astra / Claude güvenlik testleri için:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\mimcor.ps1 init claude -Secure
# Adres: https://localhost:8445/
```

Bu profilde: `DEBUG=0`, yalnız HTTPS (HTTP portu yayınlanmaz), oturum ve CSRF çerezleri `Secure`, uygulama
`X-Forwarded-Proto`'yu yalnız Caddy'den alır, 4 gunicorn worker'ı, giriş deneme kilidi tüm worker'larca paylaşılan
veritabanı önbelleğinde. Sertifika Caddy'nin **yerel** sertifika otoritesinden üretilir; tarayıcı bu otoriteye
güvenmediği için ilk açılışta uyarı gösterir. Uyarısız kullanmak için kök sertifikayı alıp Windows'ta
"Güvenilen Kök Sertifika Yetkilileri"ne ekleyebilirsiniz (yalnız bu bilgisayar için):

```powershell
docker compose -p mimcor-claude --env-file env/claude.env -f compose.yml -f compose.secure.yml cp web:/data/caddy/pki/authorities/local/root.crt .\env\claude-yerel-ca.crt
```

HTTPS yalnız `https://localhost:<port>` adıyla çalışır (`127.0.0.1` ile sertifika doğrulanmaz).

**Durum:** Bu profil bulut konteynerinde gerçekten çalıştırıldı (kök sertifikayla `curl` doğrulaması, Chromium ile
giriş, Secure çerez, dosya erişimi, sipariş akışı, 6. hatalı denemede kilit). Windows tarayıcısında ve sertifikanın
Windows'a eklenmesiyle **denenmedi**. Bu profil yerel güvenlik testi içindir; HSTS, yedek/anahtar yönetimi, dış
erişim gibi üretim gereksinimlerini karşılamaz ve **üretim güvenliği doğrulanmış sayılmaz**.

## 9. Yerel HTTP kipi hakkında

`demo` varsayılan olarak yerel HTTP ile çalışır: `DEBUG=0` (hata ayrıntısı gösterilmez), ama çerezler `Secure` değildir
(`DJANGO_LOCAL_HTTP=1`). Bu yalnız **bu bilgisayarın localhost'unda** demo içindir; ağdaki başka cihazlara açılmaz
(port `127.0.0.1`'e bağlıdır). Tünel, internete açık yayın veya port yönlendirme kurulmaz; kurmayın. Telefonla erişim
ayrıca ele alınacaktır.

## 10. Yaygın sorunlar

| Belirti | Neden / çözüm |
|---|---|
| `HATA: Docker çalışmıyor` | Docker Desktop'ı başlatın, simge "running" olunca tekrar deneyin. |
| `env\demo.env zaten var` | Ortam kurulmuş; `up` kullanın. Baştan kurmak için `reset demo -Force`. |
| `port is already allocated` / `bind: ... 8080` | Port başka program tarafından kullanılıyor. Programı kapatın ya da `env\demo.env` içinde `HTTP_PORT`, `DJANGO_CSRF_TRUSTED_ORIGINS` ve `APP_BASE_URL` değerlerini aynı yeni portla değiştirip `up` çalıştırın. |
| `additional property ... !override` / `!override` hatası | Docker Compose eski; Docker Desktop'ı güncelleyin (`docker compose version` ≥ 2.24). |
| Derlemede `CERTIFICATE_VERIFY_FAILED` | Kurumsal ağ TLS denetimi yapıyor. Kurumun CA sertifikasını proje içinde `env\kurum-ca.pem` olarak kaydedin, `$env:MIMCOR_EXTRA_CA = ".\env\kurum-ca.pem"` verip `init`/`build` komutunu tekrar çalıştırın (sertifika imaja yazılmaz). |
| `exec /app/docker/entrypoint.sh: no such file or directory` | Satır sonu CRLF'e çevrilmiş. Depoda `.gitattributes` bunu önler; depoyu bu branch'ten yeniden klonlayın, sonra `build`. |
| Girişte "CSRF doğrulaması başarısız" (403) | Adresi `env` dosyasındaki adresle açın (`http://localhost:8080`; HTTPS profilinde yalnız `https://localhost:<port>`). |
| `betik çalıştırma devre dışı` | Komutu `powershell -ExecutionPolicy Bypass -File ...` biçiminde çalıştırın. |
| WSL 2 / sanallaştırma hatası | Docker Desktop'ın önerdiği WSL 2 güncellemesini kurun, BIOS'ta sanallaştırmayı açın. |
| Uygulama "unhealthy" kalıyor | `logs demo app` ile hatayı görün; çoğunlukla veritabanı parolası değişmiş `env` dosyasıdır (env dosyasını elle değiştirmeyin). |
| 6 hatalı girişten sonra giriş yapılamıyor | Güvenlik kilidi: 15 dakika bekleyin. |

## 11. Bulut konteynerinde yapılan doğrulama (sizin bilgisayarınızda değil)

- `demo` (yerel HTTP) temiz kurulum: migrate + `seed_demo`; yalnız `127.0.0.1:8080` yayınlandı, PostgreSQL ve uygulama
  portu yayınlanmadı; CPU/bellek sınırları uygulandı.
- DEBUG=0'da statik dosyalar (200), Chromium ile giriş, yetkili dosya indirme (yetkili 200, kapsam dışı 404, `/media/`
  404), sipariş oluştur → onayla → planlamada görünür.
- `down` / `up` sonrası sipariş, yüklenen dosya ve parola özeti aynı.
- `astra` ayrı kuruldu: her ortamın veritabanında yalnız kendi kaydı; `reset astra` demo volume'larını değiştirmedi;
  `reset demo` `--force` olmadan reddedildi.
- Yedekleme (izin 600/700) ve başka ortama geri yükleme (veritabanı + dosyalar, doğru sahiplik).
- `claude` güvenlik profili (HTTPS) yukarıdaki gibi.
- PowerShell betiği Linux'taki PowerShell 7 ile çalıştırıldı (init -Secure, backup, reset, restore, down/up). Windows
  PowerShell 5.1 ve Docker Desktop üzerinde **denenmedi**.
