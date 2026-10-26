import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.middleware import LoginRequiredMiddleware as DjangoLoginRequired
from django.shortcuts import redirect, resolve_url

LoginRequiredMiddleware = DjangoLoginRequired


class SessionIdleTimeoutMiddleware:
    """Belirli süre işlem yapılmayan oturumu sunucu tarafında sonlandırır."""

    SESSION_KEY = "_last_activity"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            now = int(time.time())
            last = request.session.get(self.SESSION_KEY)
            limit = settings.SESSION_IDLE_MINUTES * 60
            if last is not None and now - last > limit:
                logout(request)
                messages.warning(request, "Oturumunuz uzun süre işlem yapılmadığı için kapatıldı. Lütfen tekrar giriş yapın.")
                return redirect(f"{resolve_url(settings.LOGIN_URL)}?next={request.path}")
            request.session[self.SESSION_KEY] = now
        return self.get_response(request)
