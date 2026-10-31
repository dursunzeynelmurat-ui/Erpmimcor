# Mimcor uygulama imajı: Python 3.12 (CI ile aynı) + gunicorn. Sır içermez; ayarlar çalışma anında ortamdan gelir.
FROM python:3.12.16-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt requirements-docker.txt ./
# extra_ca: yalnız kurumsal/TLS denetimli ağlarda gerekiyorsa verilen isteğe bağlı CA sertifikası (BuildKit secret;
# imaja yazılmaz). Normal kurulumda kullanılmaz.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install -r requirements-docker.txt

RUN groupadd --system --gid 10001 mimcor \
    && useradd --system --uid 10001 --gid mimcor --home-dir /app --shell /usr/sbin/nologin mimcor

COPY --chown=mimcor:mimcor . .
RUN mkdir -p /app/staticfiles /app/media && chown mimcor:mimcor /app/staticfiles /app/media \
    && chmod 0755 docker/entrypoint.sh

USER mimcor
EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn"]
