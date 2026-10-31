#!/bin/sh
# Uygulama konteyneri girişi. Normal başlatma veriye dokunmaz: migration ve demo verisi ayrı komutlardır
# (scripts/mimcor.ps1 / mimcor.sh: init, migrate, seed). Burada yalnız statik dosyalar paylaşılan volume'a kopyalanır.
set -eu

if [ "${1:-}" = "gunicorn" ]; then
    python manage.py collectstatic --noinput --verbosity 0
    exec gunicorn mimcor.wsgi:application \
        --bind 0.0.0.0:8000 \
        --workers "${GUNICORN_WORKERS:-2}" \
        --timeout "${GUNICORN_TIMEOUT:-60}" \
        --access-logfile - \
        --error-logfile -
fi

exec "$@"
