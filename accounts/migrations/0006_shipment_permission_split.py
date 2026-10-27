"""Sevk onayı, çıkış öncesi geri alma ve fiziksel çıkış kaydı ayrı yetkiler oldu.

Şablonlar: depo personeli = hazırlama + QR yükleme kontrolü (son onay yok); ambar yetkilisi ve yönetici = onay,
geri alma, çıkış. Yalnız değiştirilmemiş şablon rolleri güncellenir (accounts/template_sync.py); özelleştirilmiş
roller ve şablon dışı roller yeniden yazılmaz. shipment.approve taşıyan özel roller artık geri alma ve çıkışı
kapsamaz; bu roller rol listesinde uyarıyla gösterilir ve yönetici gerekirse ayrıca atar."""
from django.db import migrations

A = "all"
OPS_VIEW = {"customer.view": A, "product.view": A, "order.view": A, "workorder.view": A, "stock.view": A,
            "shipment.view": A, "machine.view": A, "material.view": A}
DEPO_OLD = {**OPS_VIEW, "warehouse.operate": A, "reservation.manage": A, "shipment.manage": A, "shipment.approve": A,
            "label.print": A, "lot.receive": A, "quality.block": A, "customer.document.view": A}
DEPO_NEW = {k: v for k, v in DEPO_OLD.items() if k != "shipment.approve"}
AMBAR_OLD = {**OPS_VIEW, "warehouse.operate": A, "reservation.manage": A, "shipment.manage": A, "shipment.approve": A,
             "label.print": A, "lot.receive": A, "quality.block": A, "correction.approve": A,
             "customer.document.view": A}
AMBAR_NEW = {**AMBAR_OLD, "shipment.undo": A, "shipment.exit": A}
ADMIN_OLD_CODES = [
    "correction.approve", "correction.request", "customer.access.manage", "customer.change", "customer.commercial.view",
    "customer.create", "customer.document.manage", "customer.document.view", "customer.view", "exception.approve",
    "hr.role.assign", "hr.user.manage", "instruction.print", "instruction.view", "label.print", "lot.receive",
    "machine.manage", "machine.view", "material.deviation", "material.manage", "material.view", "order.approve",
    "order.cancel", "order.change", "order.create", "order.view", "planning.approve", "product.change",
    "product.create", "product.view", "production.execute", "production.hold", "quality.block", "quality.decide",
    "quality.release", "recipe.change", "recipe.view", "reservation.manage", "role.approve", "role.draft",
    "role.manage", "shift.manage", "shipment.approve", "shipment.manage", "shipment.view", "stock.view",
    "stocklevel.manage", "system.admin", "user.manage", "warehouse.operate", "workorder.cancel", "workorder.create",
    "workorder.release", "workorder.schedule", "workorder.view"]
ADMIN_OLD = {c: A for c in ADMIN_OLD_CODES}
ADMIN_NEW = {**ADMIN_OLD, "shipment.undo": A, "shipment.exit": A}
REASON = "Sevk onayı / geri alma / çıkış ayrıldı."


def forwards(apps, schema_editor):
    from accounts.template_sync import sync_template_role
    for code, old, new in (("depo", DEPO_OLD, DEPO_NEW), ("ambar_yetkilisi", AMBAR_OLD, AMBAR_NEW),
                           ("yonetici", ADMIN_OLD, ADMIN_NEW)):
        sync_template_role(apps, code, old, new, REASON)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0005_remove_direct_lot_adjust"), ("core", "0001_initial")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
