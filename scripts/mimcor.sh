#!/usr/bin/env bash
# Mimcor yerel Docker ortamları (Mac/Linux). Windows için: scripts/mimcor.ps1 (aynı komutlar).
# Kullanım: scripts/mimcor.sh <komut> <ortam> [seçenek]
#   ortam: demo | astra | claude  (her biri ayrı veritabanı, dosya, ağ ve port)
#   init <ortam> [--secure]   ilk kurulum: ayar dosyası (rastgele sırlar) + imaj + migrate + demo verisi + başlat
#   up | down | status <ortam> başlat / durdur (veri korunur) / durum
#   build <ortam>              kod güncellemesinden sonra imajı yeniden derle (sonra: migrate, up)
#   logs <ortam> [servis]      loglar (app | db | web)
#   migrate <ortam>            migration'ları uygula (veri korunur)
#   seed <ortam>               demo verisini yükle (var olan kayıt ve parolalara dokunmaz)
#   backup <ortam>             veritabanı + yüklenen dosyalar → backups/<ortam>-<zaman>/
#   restore <ortam> <klasör>   yedekten geri yükle (bu ortamın verisini değiştirir)
#   reset <ortam> [--force]    YALNIZ bu ortamın verisini sil ve yeniden kur (demo için --force gerekir)
#   url <ortam>                tarayıcı adresi
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

die() { echo "HATA: $*" >&2; exit 1; }

cmd="${1:-}"; envname="${2:-}"
[ -n "$cmd" ] || die "komut gerekli (init, up, down, status, build, logs, migrate, seed, backup, restore, reset, url)"
case "$envname" in
  demo) http=8080; https=8443 ;;
  astra) http=8081; https=8444 ;;
  claude) http=8082; https=8445 ;;
  *) die "ortam adı demo, astra veya claude olmalı" ;;
esac
envfile="env/$envname.env"
project="mimcor-$envname"

profile() { grep -E '^MIMCOR_PROFILE=' "$envfile" | cut -d= -f2-; }

dc() {
  [ -f "$envfile" ] || die "$envfile yok. Önce: scripts/mimcor.sh init $envname"
  local files=(-f compose.yml)
  [ "$(profile)" = "secure" ] && files+=(-f compose.secure.yml)
  docker compose -p "$project" --env-file "$envfile" "${files[@]}" "$@"
}

secret() { LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c "${1:-48}"; }

write_env() {
  local prof="$1" scheme port
  if [ "$prof" = "secure" ]; then
    scheme=https; port=$https; local_http=0; origins="https://localhost:$https"   # HTTPS yalnız localhost adıyla
  else
    scheme=http; port=$http; local_http=1; origins="http://localhost:$http,http://127.0.0.1:$http"
  fi
  umask 077
  mkdir -p env
  cat > "$envfile" <<EOF
# $envname ortamı — scripts/mimcor.sh init ile üretildi. SIR İÇERİR; paylaşmayın, Git'e eklemeyin.
COMPOSE_PROJECT_NAME=$project
MIMCOR_PROFILE=$prof
HTTP_PORT=$http
HTTPS_PORT=$https
DJANGO_SECRET_KEY=$(secret 64)
POSTGRES_PASSWORD=$(secret 32)
DEMO_USER_PASSWORD=$(secret 16)
DJANGO_LOCAL_HTTP=$local_http
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=$origins
APP_BASE_URL=$scheme://localhost:$port
APP_IMAGE_TAG=$envname
EOF
}

url() { if [ "$(profile)" = "secure" ]; then echo "https://localhost:$https/"; else echo "http://localhost:$http/"; fi; }

setup_data() {
  dc up -d --wait db
  dc run --rm --no-deps app python manage.py migrate --noinput
  dc run --rm --no-deps app python manage.py createcachetable
  dc run --rm --no-deps app python manage.py seed_demo
}

case "$cmd" in
  init)
    [ -f "$envfile" ] && die "$envfile zaten var: bu ortam kurulmuş. Başlatmak için 'up'; sıfırlamak için 'reset'."
    prof=local-http; [ "${3:-}" = "--secure" ] && prof=secure
    write_env "$prof"
    dc build app
    setup_data
    dc up -d --wait
    echo "Hazır: $(url)  (kullanıcı parolası: $envfile → DEMO_USER_PASSWORD)"
    ;;
  up) dc up -d --wait; echo "Adres: $(url)" ;;
  down) dc down ;;
  status) dc ps ;;
  build) dc build app ;;
  logs) dc logs --tail 200 -f ${3:-} ;;
  migrate)
    dc run --rm --no-deps app python manage.py migrate --noinput
    dc run --rm --no-deps app python manage.py createcachetable ;;
  seed) dc run --rm --no-deps app python manage.py seed_demo ;;
  url) url ;;
  backup)
    dest="backups/$envname-$(date +%Y%m%d-%H%M%S)"
    (umask 077; mkdir -p "$dest")
    dc exec -T db sh -c 'pg_dump -U mimcor -d mimcor -Fc -f /tmp/mimcor.dump'
    dc cp db:/tmp/mimcor.dump "$dest/mimcor.dump"
    dc exec -T db rm -f /tmp/mimcor.dump
    dc exec -T app sh -c 'tar -C /app/media -cf /tmp/media.tar .'
    dc cp app:/tmp/media.tar "$dest/media.tar"
    dc exec -T -u root app rm -f /tmp/media.tar
    chmod 600 "$dest"/*   # yedek kişisel veri ve parola özetleri içerir
    echo "Yedek: $dest (mimcor.dump, media.tar)" ;;
  restore)
    src="${3:-}"; [ -f "$src/mimcor.dump" ] && [ -f "$src/media.tar" ] || die "yedek klasörü: <klasör>/mimcor.dump ve media.tar"
    dc stop web app
    dc cp "$src/mimcor.dump" db:/tmp/restore.dump
    dc exec -T db sh -c 'pg_restore -U mimcor -d mimcor --clean --if-exists --no-owner /tmp/restore.dump; rm -f /tmp/restore.dump'
    dc up -d --wait app
    dc cp "$src/media.tar" app:/tmp/restore-media.tar
    dc exec -T -u root app sh -c 'find /app/media -mindepth 1 -delete && tar -C /app/media -xf /tmp/restore-media.tar \
      && chown -R mimcor:mimcor /app/media && rm -f /tmp/restore-media.tar'
    dc up -d --wait
    echo "Geri yüklendi: $src → $envname" ;;
  reset)
    [ "$envname" = "demo" ] && [ "${3:-}" != "--force" ] && die "demo ortamını sıfırlamak için --force ekleyin (demo verisi silinir)."
    dc down -v --remove-orphans   # yalnız mimcor-$envname projesinin container, ağ ve volume'ları
    setup_data
    dc up -d --wait
    echo "Sıfırlandı ve yeniden kuruldu: $envname → $(url)" ;;
  *) die "bilinmeyen komut: $cmd" ;;
esac
