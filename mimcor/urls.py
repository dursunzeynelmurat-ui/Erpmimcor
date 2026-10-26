from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Mimcor Plastik – Yönetim"

urlpatterns = [
    path("yonetim/", admin.site.urls),
    path("", include("accounts.urls")),
    path("", include("catalog.urls")),
    path("", include("orders.urls")),
    path("", include("production.urls")),
    path("", include("core.urls")),
]
