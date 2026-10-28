from django.urls import path

from . import views

app_name = "samples"
urlpatterns = [
    path("numuneler/", views.sample_list, name="sample_list"),
    path("numuneler/yeni/", views.sample_create, name="sample_create"),
    path("numuneler/musteri/<int:pk>/urunler/", views.customer_products, name="customer_products"),
    path("numuneler/<int:pk>/", views.sample_detail, name="sample_detail"),
    path("numuneler/<int:pk>/islem/<str:action>/", views.sample_action, name="sample_action"),
    path("numuneler/<int:pk>/etiket/", views.sample_label, name="sample_label"),
    path("numuneler/ek/<int:pk>/", views.attachment_file, name="attachment_file"),
    path("numuneler/kriterler/", views.criteria, name="criteria"),
    path("numune/q/<str:token>/", views.qr_open, name="qr_open"),
]
