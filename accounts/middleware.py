import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.middleware import LoginRequiredMiddleware as DjangoLoginRequired
from django.shortcuts import redirect, resolve_url

from .permissions import end_request_cache, start_request_cache

LoginRequiredMiddleware = DjangoLoginRequired
ABSOLUTE_KEY = "_login_at"


class PermissionCacheMiddleware:
    """Yetkiler her istekte bir kez veritabanından okunur; istekler arasında önbellek yoktur."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start_request_cache()
        try:
            return self.get_response(request)
        finally:
            end_request_cache()


class SessionIdleTimeoutMiddleware:
    """Oturumu sunucu tarafında iki ayrı sınırla kapatır:
    - Hareketsizlik: son istekten bu yana SESSION_IDLE_MINUTES.
    - Mutlak süre: giriş anından itibaren SESSION_COOKIE_AGE (SESSION_MAX_HOURS); aktif kullanım uzatmaz.
    Giriş zamanı olmayan (yükseltme öncesi) oturumlar güvenli tarafta kalmak için kapatılır."""

    SESSION_KEY = "_last_activity"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            now = int(time.time())
            last = request.session.get(self.SESSION_KEY)
            login_at = request.session.get(ABSOLUTE_KEY)
            if login_at is None or now - login_at > settings.SESSION_COOKIE_AGE:
                return self._expire(request, "Oturum süreniz doldu. Lütfen tekrar giriş yapın.")
            if last is not None and now - last > settings.SESSION_IDLE_MINUTES * 60:
                return self._expire(request, "Oturumunuz uzun süre işlem yapılmadığı için kapatıldı. "
                                             "Lütfen tekrar giriş yapın.")
            request.session[self.SESSION_KEY] = now
        return self.get_response(request)

    def _expire(self, request, message):
        logout(request)
        messages.warning(request, message)
        return redirect(f"{resolve_url(settings.LOGIN_URL)}?next={request.path}")
