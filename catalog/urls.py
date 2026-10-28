from django.urls import path

from . import customer_views as cv
from . import views

app_name = "catalog"
urlpatterns = [
    path("musteriler/", cv.customer_list, name="customer_list"),
    path("musteriler/yeni/", cv.customer_create, name="customer_create"),
    path("musteriler/<int:pk>/", cv.customer_detail, name="customer_detail"),
    path("musteriler/<int:pk>/bolum/<str:section>/", cv.customer_section_edit, name="customer_section_edit"),
    path("musteriler/<int:pk>/durum/", cv.customer_toggle, name="customer_toggle"),
    path("musteriler/<int:pk>/kisi/yeni/", cv.contact_edit, name="contact_create"),
    path("musteriler/<int:pk>/kisi/<int:contact_pk>/", cv.contact_edit, name="contact_edit"),
    path("musteriler/<int:pk>/adres/yeni/", cv.address_edit, name="address_create"),
    path("musteriler/<int:pk>/adres/<int:address_pk>/", cv.address_edit, name="address_edit"),
    path("musteriler/<int:pk>/belge/yeni/", cv.document_upload, name="document_upload"),
    path("musteriler/<int:pk>/belge/<int:doc_pk>/indir/", cv.document_download, name="document_download"),
    path("musteriler/<int:pk>/belge/<int:doc_pk>/durum/", cv.document_toggle, name="document_toggle"),
    path("musteriler/<int:pk>/erisim/yeni/", cv.access_grant, name="access_grant"),
    path("musteriler/<int:pk>/erisim/<int:access_pk>/kaldir/", cv.access_revoke, name="access_revoke"),
    path("musteri-erisimleri/", cv.access_review, name="access_review"),
    path("tanim/<str:kind>/", views.definition_list, name="definition_list"),
    path("tanim/<str:kind>/yeni/", views.definition_create, name="definition_create"),
    path("tanim/<str:kind>/<int:pk>/", views.definition_edit, name="definition_edit"),
    path("tanim/<str:kind>/<int:pk>/durum/", views.definition_toggle, name="definition_toggle"),
    path("urun/<int:product_pk>/recete/", views.recipe_detail, name="recipe_detail"),
    path("urun/<int:product_pk>/recete/yeni-revizyon/", views.revision_create, name="revision_create"),
    path("recete-taslagi/<int:pk>/", views.recipe_draft, name="recipe_draft"),
    path("recete-taslagi/<int:pk>/vazgec/", views.recipe_draft_discard, name="recipe_draft_discard"),
    path("lotlar/", views.lot_list, name="lot_list"),
    path("lotlar/giris/", views.lot_receive, name="lot_receive"),
    path("lotlar/<int:pk>/", views.lot_detail, name="lot_detail"),
]
