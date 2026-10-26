"""Yükseltme: ustabaşı kapsamı 'kendisine atanmış işler' olduğundan, açık iş emirleri tek aktif ustabaşı varsa
ona atanır (önceki sürümde ustabaşı tüm işleri görüyordu). Birden fazla ustabaşı varsa atama planlamaya bırakılır."""
from django.db import migrations
from django.utils import timezone


def forwards(apps, schema_editor):
    UserRole = apps.get_model("accounts", "UserRole")
    WorkOrder = apps.get_model("production", "WorkOrder")
    foremen = list(UserRole.objects.filter(role__code="ustabasi", revoked_at__isnull=True, user__is_active=True)
                   .values_list("user_id", flat=True).distinct())
    if len(foremen) == 1:
        WorkOrder.objects.filter(status__in=["planlandi", "basladi"], assigned_foreman__isnull=True).update(
            assigned_foreman_id=foremen[0], assigned_at=timezone.now())


class Migration(migrations.Migration):
    dependencies = [("production", "0003_foreman_and_operation_schedule"), ("accounts", "0004_remove_legacy_role")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
