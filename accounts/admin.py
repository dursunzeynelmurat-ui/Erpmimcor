from django.contrib import admin

from .models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ["personnel_no", "full_name", "role", "is_active"]
    list_filter = ["role", "is_active"]
    search_fields = ["personnel_no", "full_name"]
    exclude = ["password", "user_permissions", "groups"]
    readonly_fields = ["last_login", "date_joined"]
