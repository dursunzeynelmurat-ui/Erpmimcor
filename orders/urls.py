from django.urls import path

from . import views

app_name = "orders"
urlpatterns = [
    path("siparisler/", views.order_list, name="order_list"),
    path("siparisler/yeni/", views.order_create, name="order_create"),
    path("siparisler/<int:pk>/", views.order_detail, name="order_detail"),
    path("ilerleme/", views.progress_list, name="progress_list"),
    path("planlama/", views.planning_queue, name="planning_queue"),
    path("planlama/kalem/<int:pk>/onay/", views.line_approve, name="line_approve"),
]
