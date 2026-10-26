"""Eski serbest metin `contact` alanını ana ilgili kişi kaydına taşır ve yükseltme öncesi tüm müşterileri gören
satış kullanıcılarına mevcut müşteriler için kalıcı erişim kaydı yazar (yönetici sonradan kaldırabilir)."""
from django.db import migrations
from django.utils import timezone


def forwards(apps, schema_editor):
    Customer = apps.get_model("catalog", "Customer")
    CustomerContact = apps.get_model("catalog", "CustomerContact")
    CustomerAccess = apps.get_model("catalog", "CustomerAccess")
    UserRole = apps.get_model("accounts", "UserRole")
    for c in Customer.objects.exclude(contact=""):
        if not CustomerContact.objects.filter(customer=c).exists():
            CustomerContact.objects.create(customer=c, name=c.contact[:120], is_primary=True,
                                           department="(eski kayıttan taşındı)")
    sales = UserRole.objects.filter(role__code="satis", revoked_at__isnull=True).values_list("user_id", flat=True)
    today = timezone.localdate()
    for c in Customer.objects.all():
        for uid in sales:
            CustomerAccess.objects.get_or_create(customer=c, user_id=uid, revoked_at=None, defaults={
                "valid_from": today, "reason": "Yükseltme: önceki sürümde satış tüm müşterileri görüyordu"})


class Migration(migrations.Migration):
    dependencies = [("catalog", "0003_customer_card"), ("accounts", "0004_remove_legacy_role")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
