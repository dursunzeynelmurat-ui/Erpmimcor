from django.contrib import admin

from .models import Pallet, Shipment


@admin.register(Pallet)
class PalletAdmin(admin.ModelAdmin):
    """Salt okunur görünüm: palet miktarları yalnız uygulama içi hareketlerle değişir."""

    list_display = ["code", "product", "box_count", "status", "quality_status", "weight_status"]
    list_filter = ["status", "quality_status"]
    search_fields = ["code", "product__code"]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False


@admin.register(Shipment)
class ShipmentAdmin(PalletAdmin):
    list_display = ["code", "order", "status", "shipped_at"]
    list_filter = ["status"]
    search_fields = ["code", "order__code"]
