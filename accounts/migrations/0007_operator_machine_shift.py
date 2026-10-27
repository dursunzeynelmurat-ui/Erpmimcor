"""Usta/operatör şablonu: aktif teknik bilgi, üretim kaydı, duruş/devir ve blokaj 'atandığı makine VE sorumlu
vardiya' (machine_shift) kapsamına alındı; geçmiş iş emri görüntüleme makine kapsamında kaldı. Yalnız değiştirilmemiş
şablon rolü güncellenir; özelleştirilmiş rol yeniden yazılmaz (accounts/template_sync.py)."""
from django.db import migrations

OLD = {"workorder.view": "machine", "production.execute": "machine", "production.hold": "machine",
       "instruction.view": "machine", "quality.block": "machine", "machine.view": "all"}
NEW = {"workorder.view": "machine", "production.execute": "machine_shift", "production.hold": "machine_shift",
       "instruction.view": "machine_shift", "quality.block": "machine_shift", "machine.view": "all"}


def forwards(apps, schema_editor):
    from accounts.template_sync import sync_template_role
    sync_template_role(apps, "operator", OLD, NEW, "Operatör erişimi makine + vardiya (ikisi birden) kapsamına alındı.")


class Migration(migrations.Migration):
    dependencies = [("accounts", "0006_shipment_permission_split"), ("core", "0001_initial")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
