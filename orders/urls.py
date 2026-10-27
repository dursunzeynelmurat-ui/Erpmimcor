from django.urls import path

from . import views

app_name = "orders"
urlpatterns = [
    path("siparisler/", views.order_list, name="order_list"),
    path("siparisler/yeni/", views.order_create, name="order_create"),
    path("siparisler/<int:pk>/", views.order_detail, name="order_detail"),
    path("siparisler/musteri/<int:pk>/urunler/", views.customer_products, name="customer_products"),
    path("ilerleme/", views.progress_list, name="progress_list"),
    path("planlama/", views.planning_queue, name="planning_queue"),
    path("planlama/kalem/<int:pk>/onay/", views.line_approve, name="line_approve"),
]
urlpatterns += [
    path("siparisler/<int:pk>/onayla/", views.order_approve, name="order_approve"),
    path("siparisler/<int:pk>/sil/", views.order_delete, name="order_delete"),
    path("siparisler/<int:pk>/kopyala/", views.order_copy, name="order_copy"),
    path("siparisler/<int:pk>/iptal/", views.order_cancel, name="order_cancel"),
    path("siparisler/<int:pk>/arsivle/", views.order_archive, name="order_archive"),
    path("siparisler/kalem/<int:pk>/degistir/", views.line_change, name="line_change"),
    path("siparisler/kalem/<int:pk>/iptal/", views.line_cancel, name="line_cancel"),
    path("siparisler/kalem/<int:pk>/istisna/<str:kind>/", views.line_exception, name="line_exception"),
    path("siparisler/esdegerlik/<int:pk>/geri-al/", views.compat_revoke, name="compat_revoke"),
]
