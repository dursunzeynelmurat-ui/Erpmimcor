from django.urls import path

from . import views

app_name = "catalog"
urlpatterns = [
    path("tanim/<str:kind>/", views.definition_list, name="definition_list"),
    path("tanim/<str:kind>/yeni/", views.definition_create, name="definition_create"),
    path("tanim/<str:kind>/<int:pk>/", views.definition_edit, name="definition_edit"),
    path("tanim/<str:kind>/<int:pk>/durum/", views.definition_toggle, name="definition_toggle"),
    path("urun/<int:product_pk>/recete/", views.recipe_detail, name="recipe_detail"),
    path("urun/<int:product_pk>/recete/yeni-revizyon/", views.revision_create, name="revision_create"),
    path("lotlar/", views.lot_list, name="lot_list"),
    path("lotlar/giris/", views.lot_receive, name="lot_receive"),
    path("lotlar/<int:pk>/", views.lot_detail, name="lot_detail"),
]
