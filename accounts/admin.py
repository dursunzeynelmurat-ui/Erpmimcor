from django.contrib import admin

from core.admin import ReadOnlyAdmin

from .models import AccessRole, User, UserRole


@admin.register(User)
class UserAdmin(ReadOnlyAdmin):
    """Salt okunur: kullanıcı ve rol değişiklikleri uygulama ekranından, kayıt altına alınarak yapılır."""

    list_display = ["personnel_no", "full_name", "team", "shift", "is_active"]
    list_filter = ["is_active", "shift"]
    search_fields = ["personnel_no", "full_name"]
    exclude = ["password", "user_permissions", "groups"]


@admin.register(AccessRole)
class AccessRoleAdmin(ReadOnlyAdmin):
    list_display = ["code", "name", "status", "hr_assignable", "is_template"]


@admin.register(UserRole)
class UserRoleAdmin(ReadOnlyAdmin):
    list_display = ["user", "role", "granted_at", "revoked_at"]
