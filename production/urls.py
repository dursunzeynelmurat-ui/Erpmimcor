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
urlpatterns += [
    path("is-emirleri/<int:pk>/serbest-birak/", views.workorder_release, name="workorder_release"),
    path("is-emirleri/<int:pk>/geri-cek/", views.workorder_withdraw, name="workorder_withdraw"),
    path("is-emirleri/<int:pk>/iptal/", views.workorder_cancel, name="workorder_cancel"),
    path("is-emirleri/<int:pk>/arsivle/", views.workorder_archive, name="workorder_archive"),
    path("is-emirleri/<int:pk>/duzenle/", views.workorder_edit, name="workorder_edit"),
    path("operasyon/<int:pk>/plan/", views.operation_schedule, name="operation_schedule"),
    path("operasyon/<int:pk>/durum/<str:action>/", views.operation_hold, name="operation_hold"),
    path("operasyon/<int:op_pk>/devir/", views.handover_create, name="handover_create"),
    path("devir/<int:pk>/teslim-al/", views.handover_ack, name="handover_ack"),
    path("operasyon/<int:op_pk>/talimat/bas/", views.instruction_print, name="instruction_print"),
    path("talimat/<int:pk>/", views.instruction_view, name="instruction_view"),
    path("talimat/<int:pk>/onay/<str:kind>/", views.instruction_ack, name="instruction_ack"),
    path("uretim-plani/", views.floor_plan, name="floor_plan"),
    path("is-emirleri/<int:pk>/kapanis-degerlendir/", views.closure_review, name="closure_review"),
]
