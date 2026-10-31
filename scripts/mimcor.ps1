<#
Mimcor yerel Docker ortamları (Windows / Docker Desktop). Mac/Linux eşdeğeri: scripts/mimcor.sh (aynı komutlar).

Çalıştırma (depo klasöründe, PowerShell):
  powershell -ExecutionPolicy Bypass -File .\scripts\mimcor.ps1 <komut> <ortam> [seçenek]

Ortam: demo | astra | claude  (her biri ayrı veritabanı, dosya, ağ ve port)
  init <ortam> [-Secure]     ilk kurulum: ayar dosyası (rastgele sırlar) + imaj + migrate + demo verisi + başlat
  up | down | status <ortam>  başlat / durdur (veri korunur) / durum
  build <ortam>               kod güncellemesinden sonra imajı yeniden derle (sonra: migrate, up)
  logs <ortam> [servis]       loglar (app | db | web); Ctrl+C ile çıkılır
  migrate <ortam>             migration'ları uygula (veri korunur)
  seed <ortam>                demo verisini yükle (var olan kayıt ve parolalara dokunmaz)
  backup <ortam>              veritabanı + yüklenen dosyalar -> backups\<ortam>-<zaman>\
  restore <ortam> <klasör>    yedekten geri yükle (yalnız bu ortamın verisi değişir)
  reset <ortam> [-Force]      YALNIZ bu ortamın verisini sil ve yeniden kur (demo için -Force gerekir)
  url <ortam>                 tarayıcı adresi
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("init", "up", "down", "status", "build", "logs", "migrate", "seed", "backup", "restore", "reset", "url")]
    [string]$Command,
    [Parameter(Mandatory = $true, Position = 1)]
    [ValidateSet("demo", "astra", "claude")]
    [string]$Env,
    [Parameter(Position = 2)]
    [string]$Arg = "",
    [switch]$Secure,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Ports = @{ demo = @(8080, 8443); astra = @(8081, 8444); claude = @(8082, 8445) }
$HttpPort = $Ports[$Env][0]
$HttpsPort = $Ports[$Env][1]
$EnvFile = "env/$Env.env"
$Project = "mimcor-$Env"

function Fail([string]$Message) { Write-Host "HATA: $Message" -ForegroundColor Red; exit 1 }

function Get-Profile {
    if (-not (Test-Path $EnvFile)) { Fail "$EnvFile yok. Önce: .\scripts\mimcor.ps1 init $Env" }
    $line = Get-Content $EnvFile | Where-Object { $_ -match '^MIMCOR_PROFILE=' } | Select-Object -First 1
    return ($line -replace '^MIMCOR_PROFILE=', '').Trim()
}

function Invoke-Compose {
    if (-not (Test-Path $EnvFile)) { Fail "$EnvFile yok. Önce: .\scripts\mimcor.ps1 init $Env" }
    $files = @("-f", "compose.yml")
    if ((Get-Profile) -eq "secure") { $files += @("-f", "compose.secure.yml") }
    & docker compose -p $Project --env-file $EnvFile @files @args
    if ($LASTEXITCODE -ne 0) { Fail "docker compose $($args -join ' ') başarısız (çıkış kodu $LASTEXITCODE)." }
}

function New-Secret([int]$Length) {
    $chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789".ToCharArray()
    $bytes = New-Object byte[] $Length
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}

function Write-EnvFile([string]$Profile) {
    if ($Profile -eq "secure") {
        $scheme = "https"; $port = $HttpsPort; $localHttp = "0"; $origins = "https://localhost:$HttpsPort"
    } else {
        $scheme = "http"; $port = $HttpPort; $localHttp = "1"; $origins = "http://localhost:$HttpPort,http://127.0.0.1:$HttpPort"
    }
    $lines = @(
        "# $Env ortamı — scripts\mimcor.ps1 init ile üretildi. SIR İÇERİR; paylaşmayın, Git'e eklemeyin.",
        "COMPOSE_PROJECT_NAME=$Project",
        "MIMCOR_PROFILE=$Profile",
        "HTTP_PORT=$HttpPort",
        "HTTPS_PORT=$HttpsPort",
        "DJANGO_SECRET_KEY=$(New-Secret 64)",
        "POSTGRES_PASSWORD=$(New-Secret 32)",
        "DEMO_USER_PASSWORD=$(New-Secret 16)",
        "DJANGO_LOCAL_HTTP=$localHttp",
        "DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1",
        "DJANGO_CSRF_TRUSTED_ORIGINS=$origins",
        "APP_BASE_URL=${scheme}://localhost:$port",
        "APP_IMAGE_TAG=$Env"
    )
    New-Item -ItemType Directory -Force -Path "env" | Out-Null
    # UTF-8 (BOM'suz) ve LF satır sonu: docker compose ayar dosyasını değiştirmeden okur.
    [System.IO.File]::WriteAllText((Join-Path $Root $EnvFile), (($lines -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding $false))
    Protect-Path (Join-Path $Root $EnvFile)
}

function Protect-Path([string]$Path) {
    # Sır/kişisel veri içeren dosya ve klasörleri yalnız mevcut kullanıcıya açar.
    if ($IsLinux -or $IsMacOS) { & chmod -R go-rwx $Path; return }
    & icacls $Path /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" /grant:r "SYSTEM:(OI)(CI)F" *> $null
    if ($LASTEXITCODE -ne 0) { Write-Host "Uyarı: $Path izinleri kısıtlanamadı; klasörü yalnız siz kullanın." -ForegroundColor Yellow }
}

function Get-Url {
    if ((Get-Profile) -eq "secure") { return "https://localhost:$HttpsPort/" }
    return "http://localhost:$HttpPort/"
}

function Initialize-Data {
    Invoke-Compose up -d --wait db
    Invoke-Compose run --rm --no-deps app python manage.py migrate --noinput
    Invoke-Compose run --rm --no-deps app python manage.py createcachetable
    Invoke-Compose run --rm --no-deps app python manage.py seed_demo
}

& docker version --format "{{.Server.Version}}" *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker çalışmıyor. Docker Desktop'ı başlatın ve tekrar deneyin." }

switch ($Command) {
    "init" {
        if (Test-Path $EnvFile) { Fail "$EnvFile zaten var: bu ortam kurulmuş. Başlatmak için 'up'; sıfırlamak için 'reset'." }
        $prof = "local-http"
        if ($Secure) { $prof = "secure" }
        Write-EnvFile $prof
        Invoke-Compose build app
        Initialize-Data
        Invoke-Compose up -d --wait
        Write-Host "Hazır: $(Get-Url)  (kullanıcı parolası: $EnvFile -> DEMO_USER_PASSWORD)" -ForegroundColor Green
    }
    "up" { Invoke-Compose up -d --wait; Write-Host "Adres: $(Get-Url)" -ForegroundColor Green }
    "down" { Invoke-Compose down }
    "status" { Invoke-Compose ps }
    "build" { Invoke-Compose build app }
    "logs" { if ($Arg) { Invoke-Compose logs --tail 200 -f $Arg } else { Invoke-Compose logs --tail 200 -f } }
    "migrate" {
        Invoke-Compose run --rm --no-deps app python manage.py migrate --noinput
        Invoke-Compose run --rm --no-deps app python manage.py createcachetable
    }
    "seed" { Invoke-Compose run --rm --no-deps app python manage.py seed_demo }
    "url" { Get-Url }
    "backup" {
        $dest = "backups/$Env-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        New-Item -ItemType Directory -Force -Path $dest | Out-Null
        # İkili dosyalar PowerShell yönlendirmesiyle (>) bozulur; docker compose cp kullanılır. Yollar '/' ile: Windows'ta da geçerli.
        Invoke-Compose exec -T db sh -c "pg_dump -U mimcor -d mimcor -Fc -f /tmp/mimcor.dump"
        Invoke-Compose cp db:/tmp/mimcor.dump "$dest/mimcor.dump"
        Invoke-Compose exec -T db rm -f /tmp/mimcor.dump
        Invoke-Compose exec -T app sh -c "tar -C /app/media -cf /tmp/media.tar ."
        Invoke-Compose cp app:/tmp/media.tar "$dest/media.tar"
        Invoke-Compose exec -T -u root app rm -f /tmp/media.tar
        Protect-Path $dest
        Write-Host "Yedek: $dest (mimcor.dump, media.tar). Kişisel veri ve parola özetleri içerir; güvenli saklayın." -ForegroundColor Green
    }
    "restore" {
        if (-not $Arg -or -not (Test-Path "$Arg/mimcor.dump") -or -not (Test-Path "$Arg/media.tar")) {
            Fail "Yedek klasörü verin: .\scripts\mimcor.ps1 restore $Env backups\<klasör> (içinde mimcor.dump ve media.tar)"
        }
        Invoke-Compose stop web app
        Invoke-Compose cp "$Arg/mimcor.dump" db:/tmp/restore.dump
        Invoke-Compose exec -T db sh -c "pg_restore -U mimcor -d mimcor --clean --if-exists --no-owner /tmp/restore.dump; rm -f /tmp/restore.dump"
        Invoke-Compose up -d --wait app
        Invoke-Compose cp "$Arg/media.tar" app:/tmp/restore-media.tar
        Invoke-Compose exec -T -u root app sh -c "find /app/media -mindepth 1 -delete && tar -C /app/media -xf /tmp/restore-media.tar && chown -R mimcor:mimcor /app/media && rm -f /tmp/restore-media.tar"
        Invoke-Compose up -d --wait
        Write-Host "Geri yüklendi: $Arg -> $Env" -ForegroundColor Green
    }
    "reset" {
        if ($Env -eq "demo" -and -not $Force) { Fail "demo ortamını sıfırlamak için -Force ekleyin (demo verisi silinir)." }
        # Yalnız mimcor-<ortam> projesinin container, ağ ve volume'ları silinir; diğer ortamlar ve projeler etkilenmez.
        Invoke-Compose down -v --remove-orphans
        Initialize-Data
        Invoke-Compose up -d --wait
        Write-Host "Sıfırlandı ve yeniden kuruldu: $Env -> $(Get-Url)" -ForegroundColor Green
    }
}
