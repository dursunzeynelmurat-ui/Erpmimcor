from django.contrib import admin

from .models import AuditLog


def _admin_site_permission(request):
    """Yönetim paneli is_superuser/is_staff ile değil, uygulamadaki system.admin yetkisiyle açılır."""
    from accounts.permissions import can
    return can(request.user, "system.admin")


admin.site.has_permission = _admin_site_permission


class ReadOnlyAdmin(admin.ModelAdmin):
    """Panelde iş kayıtları yalnız görüntülenir; değişiklikler kayıt altına alan servislerle yapılır."""

    def has_view_permission(self, request, obj=None):
        return _admin_site_permission(request)

    def has_module_permission(self, request):
        return _admin_site_permission(request)

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdmin):
    list_display = ["at", "user", "action", "object_code", "reason"]
    list_filter = ["action", "object_type"]
    search_fields = ["object_code", "reason"]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False
