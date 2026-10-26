"""Başlangıç rol şablonları ve vardiyalar (idempotent). Migration aynı içeriği tarihsel modellerle yazar; bu
fonksiyon test ve kurulum komutları içindir. Var olan roller/yetkiler değiştirilmez."""
import datetime

from .catalog import TEMPLATES


def install_defaults():
    from .models import AccessRole, RolePermission, RoleStatus, Shift

    for code, name, start, end in (("V1", "1. vardiya", datetime.time(7), datetime.time(19)),
                                   ("V2", "2. vardiya", datetime.time(19), datetime.time(7))):
        Shift.objects.get_or_create(code=code, defaults={"name": name, "start_time": start, "end_time": end})
    for code, (name, hr_assignable, perms) in TEMPLATES.items():
        role, created = AccessRole.objects.get_or_create(code=code, defaults={
            "name": name, "status": RoleStatus.ACTIVE, "is_template": True, "hr_assignable": hr_assignable})
        if created:
            RolePermission.objects.bulk_create([RolePermission(role=role, action=a, scope=s) for a, s in perms.items()])
