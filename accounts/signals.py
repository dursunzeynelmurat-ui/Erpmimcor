from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils import timezone

from .middleware import ABSOLUTE_KEY


@receiver(user_logged_in)
def mark_login_time(sender, request, user, **kwargs):
    """Mutlak oturum süresi her giriş yolunda (form, test, yönetim paneli) giriş anından sayılır."""
    if request is not None and hasattr(request, "session"):
        request.session[ABSOLUTE_KEY] = int(timezone.now().timestamp())
