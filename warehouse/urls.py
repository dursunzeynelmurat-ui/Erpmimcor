from django.urls import path

from . import views

app_name = "warehouse"
urlpatterns = [
    path("q/<str:token>/", views.qr_open, name="qr_open"),
    path("paletler/", views.pallet_list, name="pallet_list"),
    path("stok-ozeti/", views.stock_summary, name="stock_summary"),
    path("stok-ihtiyac/", views.stock_needs, name="stock_needs"),
    path("paletler/<int:pk>/yer/", views.pallet_location, name="pallet_location"),
    path("paletler/<int:pk>/etiket/paket/", views.package_labels, name="package_labels"),
    path("etiket-basim/<int:pk>/<str:result>/", views.label_result, name="label_result"),
    path("rezervasyon/<int:pk>/azalt/", views.reservation_reduce, name="reservation_reduce"),
    path("sevkiyatlar/<int:pk>/hazirlandi/", views.shipment_prepare, name="shipment_prepare"),
    path("sevkiyatlar/<int:pk>/yeniden-ac/", views.shipment_reopen, name="shipment_reopen"),
    path("sevkiyatlar/<int:pk>/okut/", views.shipment_scan, name="shipment_scan"),
    path("sevkiyatlar/<int:pk>/cikis/", views.shipment_exit, name="shipment_exit"),
    path("sevkiyatlar/<int:pk>/geri-al/", views.shipment_undo, name="shipment_undo"),
    path("sevkiyat-satiri/<int:pk>/iade/", views.shipment_return, name="shipment_return"),
    path("iade/<int:pk>/yerine-gonderim/", views.replacement_create, name="replacement_create"),
    path("paletler/<int:pk>/", views.pallet_detail, name="pallet_detail"),
    path("paletler/<int:pk>/kabul/", views.pallet_accept, name="pallet_accept"),
    path("paletler/<int:pk>/tartim/", views.pallet_weigh, name="pallet_weigh"),
    path("paletler/<int:pk>/aktarim/", views.pallet_transfer, name="pallet_transfer"),
    path("paletler/<int:pk>/sayim/", views.pallet_count, name="pallet_count"),
    path("paletler/<int:pk>/hasar/", views.pallet_damage, name="pallet_damage"),
    path("paletler/<int:pk>/kalite/", views.pallet_quality, name="pallet_quality"),
    path("paletler/<int:pk>/ayir/", views.pallet_reserve, name="pallet_reserve"),
    path("paletler/<int:pk>/etiket/", views.pallet_label, name="pallet_label"),
    path("paletler/<int:pk>/etiket/bas/", views.pallet_label_print, name="pallet_label_print"),
    path("rezervasyon/<int:pk>/iptal/", views.reservation_cancel, name="reservation_cancel"),
    path("sevkiyatlar/", views.shipment_list, name="shipment_list"),
    path("sevkiyatlar/siparis/<int:order_pk>/yeni/", views.shipment_create, name="shipment_create"),
    path("sevkiyatlar/<int:pk>/", views.shipment_detail, name="shipment_detail"),
    path("sevkiyatlar/<int:pk>/onayla/", views.shipment_confirm, name="shipment_confirm"),
    path("sevkiyatlar/<int:pk>/iptal/", views.shipment_cancel, name="shipment_cancel"),
    path("sevkiyat-satiri/<int:pk>/cikar/", views.shipment_line_remove, name="shipment_line_remove"),
]
