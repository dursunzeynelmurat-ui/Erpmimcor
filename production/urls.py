from django.urls import path

from . import views

app_name = "production"
urlpatterns = [
    path("is-emirleri/", views.workorder_list, name="workorder_list"),
    path("is-emirleri/stok-icin/", views.workorder_create_stock, name="workorder_create_stock"),
    path("is-emirleri/kalem/<int:line_pk>/yeni/", views.workorder_create_for_line, name="workorder_create_for_line"),
    path("is-emirleri/<int:pk>/", views.workorder_detail, name="workorder_detail"),
    path("is-emirleri/<int:pk>/baslat/", views.workorder_start, name="workorder_start"),
    path("operasyon/<int:pk>/islem/<str:action>/", views.operation_action, name="operation_action"),
]
urlpatterns += [
    path("operasyon/<int:op_pk>/bobin/", views.coil_create, name="coil_create"),
    path("bobinler/<int:pk>/", views.coil_detail, name="coil_detail"),
    path("is-emirleri/<int:pk>/malzeme-cikis/", views.material_issue, name="material_issue"),
    path("is-emirleri/<int:pk>/malzeme-iade/", views.material_return, name="material_return"),
    path("is-emirleri/<int:pk>/palet/", views.pallet_from_production, name="pallet_from_production"),
    path("is-emirleri/<int:pk>/kapat/", views.workorder_close, name="workorder_close"),
]
