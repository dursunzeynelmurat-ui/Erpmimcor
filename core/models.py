from django.conf import settings
from django.db import models


class QualityStatus(models.TextChoices):
    PENDING = "kontrol_bekliyor", "Kontrol bekliyor"
    OK = "uygun", "Uygun"
    BLOCKED = "blokeli", "Blokeli"


class ValueSource(models.TextChoices):
    MEASURED = "olculen", "Ölçülen"
    CALCULATED = "hesaplanan", "Hesaplanan"
    ESTIMATED = "tahmini", "Tahmini"


class CodeSequence(models.Model):
    """Kod üretimi için yıl bazlı sayaç (SIP-2026-00001 gibi). Satır kilidiyle artırılır."""

    prefix = models.CharField(max_length=10)
    year = models.PositiveIntegerField()
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["prefix", "year"], name="uniq_code_sequence")]

    def __str__(self):
        return f"{self.prefix}-{self.year}: {self.last_value}"


class AuditLog(models.Model):
    """Kim, ne zaman, neyi, hangi değerden hangi değere, hangi gerekçeyle değiştirdi."""

    at = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    action = models.CharField(max_length=64)
    object_type = models.CharField(max_length=64, db_index=True)
    object_id = models.CharField(max_length=64, db_index=True)
    object_code = models.CharField(max_length=64, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-at", "-id"]
        verbose_name = "İşlem geçmişi kaydı"
        verbose_name_plural = "İşlem geçmişi"

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M} {self.action} {self.object_code}"


class IdempotencyKey(models.Model):
    """Bir formun tek kez işlenmesini garanti eder (çift tıklama / tekrar gönderim)."""

    key = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    action = models.CharField(max_length=64)
    result_url = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
