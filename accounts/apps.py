from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "accounts"
    verbose_name = "Kullanıcılar"

    def ready(self):
        from . import signals  # noqa: F401
