from django.contrib import admin

from core.admin import ReadOnlyAdmin

from .models import Pallet, Shipment


@admin.register(Pallet)
class PalletAdmin(ReadOnlyAdmin):
    """Salt okunur görünüm: palet miktarları yalnız uygulama içi hareketlerle değişir."""

    list_display = ["code", "product", "box_count", "status", "quality_status", "weight_status"]
    list_filter = ["status", "quality_status"]
    search_fields = ["code", "product__code"]


@admin.register(Shipment)
class ShipmentAdmin(ReadOnlyAdmin):
    list_display = ["code", "order", "status", "shipped_at"]
    list_filter = ["status"]
    search_fields = ["code", "order__code"]
