"""Başlangıç rol şablonları, iki vardiya ve eski User.role değerlerinin rol atamasına taşınması."""
import datetime

from django.db import migrations

OLD_ROLE_MAP = {"yonetici": "yonetici", "satis": "satis", "planlama": "planlama", "uretim": "ustabasi", "depo": "depo"}


def forwards(apps, schema_editor):
    from accounts.catalog import TEMPLATES

    Shift = apps.get_model("accounts", "Shift")
    AccessRole = apps.get_model("accounts", "AccessRole")
    RolePermission = apps.get_model("accounts", "RolePermission")
    UserRole = apps.get_model("accounts", "UserRole")
    User = apps.get_model("accounts", "User")
    for code, name, start, end in (("V1", "1. vardiya", datetime.time(7), datetime.time(19)),
                                   ("V2", "2. vardiya", datetime.time(19), datetime.time(7))):
        Shift.objects.get_or_create(code=code, defaults={"name": name, "start_time": start, "end_time": end})
    for code, (name, hr_assignable, perms) in TEMPLATES.items():
        role, _ = AccessRole.objects.get_or_create(code=code, defaults={
            "name": name, "status": "aktif", "is_template": True, "hr_assignable": hr_assignable})
        for action, scope in perms.items():
            RolePermission.objects.get_or_create(role=role, action=action, defaults={"scope": scope})
    for user in User.objects.exclude(role=""):
        code = OLD_ROLE_MAP.get(user.role)
        if code:
            UserRole.objects.get_or_create(user=user, role=AccessRole.objects.get(code=code), revoked_at=None,
                                           defaults={"reason": f"Eski sabit rolden taşındı ({user.role})"})


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_roles_shifts")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
