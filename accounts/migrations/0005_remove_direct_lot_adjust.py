"""Doğrudan lot sayım düzeltmesi (lot.adjust) kaldırıldı: sayım farkı artık yalnız düzeltme talebi + başka bir
yetkilinin onayıyla kaydedilir. Bu yetkiyi taşıyan rol satırları hiçbir şey vermediği için silinir; hangi rollerden
silindiği işlem geçmişine yazılır. Diğer rol yetkilerine dokunulmaz."""
from django.db import migrations


def remove_lot_adjust(apps, schema_editor):
    RolePermission = apps.get_model("accounts", "RolePermission")
    AuditLog = apps.get_model("core", "AuditLog")
    for rp in RolePermission.objects.filter(action="lot.adjust").select_related("role"):
        AuditLog.objects.create(user=None, action="role.update", object_type="accounts.accessrole",
                                object_id=str(rp.role_id), object_code=rp.role.code,
                                changes={"lot.adjust": [rp.scope, None]},
                                reason="Yükseltme: doğrudan lot sayım düzeltmesi kaldırıldı; düzeltme talebi akışı kullanılır")
        rp.delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_remove_legacy_role"), ("core", "0001_initial")]
    operations = [migrations.RunPython(remove_lot_adjust, migrations.RunPython.noop)]
