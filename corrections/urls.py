from django.urls import path

from . import views

app_name = "corrections"
urlpatterns = [
    path("duzeltmeler/", views.correction_list, name="correction_list"),
    path("duzeltmeler/yeni/<str:kind>/<int:target_id>/", views.correction_create, name="correction_create"),
    path("duzeltmeler/<int:pk>/", views.correction_detail, name="correction_detail"),
    path("duzeltmeler/<int:pk>/onayla/", views.correction_approve, name="correction_approve"),
    path("duzeltmeler/<int:pk>/reddet/", views.correction_reject, name="correction_reject"),
    path("duzeltmeler/<int:pk>/geri-cek/", views.correction_withdraw, name="correction_withdraw"),
]
