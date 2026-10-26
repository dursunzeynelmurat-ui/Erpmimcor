from django.conf import settings
from django.db import models


class CorrectionKind(models.TextChoices):
    COIL_WEIGHT = "bobin_agirlik", "Bobin ağırlığı"
    MATERIAL_ISSUE = "malzeme_cikis", "Malzeme çıkış miktarı"
    MATERIAL_RETURN = "malzeme_iade", "Malzeme iade miktarı"
    PALLET_BOXES = "palet_kutu", "Paletteki üretim kutu adedi"
    WEIGHING = "tartim", "Palet brüt tartımı"
    COUNT = "sayim", "Palet sayım adedi"


class CorrectionStatus(models.TextChoices):
    REQUESTED = "talep", "Onay bekliyor"
    APPLIED = "uygulandi", "Onaylandı ve uygulandı"
    REJECTED = "ret", "Reddedildi"
    WITHDRAWN = "geri_cekildi", "Talep geri çekildi"


class CorrectionRequest(models.Model):
    """Kayıt düzeltme talebi. Onaya kadar hiçbir kayıt veya stok değişmez; onayda hedefin güncel değeri talepteki
    eski değerle yeniden karşılaştırılır ve düzeltme geçmişi koruyan bir hareketle uygulanır."""

    code = models.CharField("Talep no", max_length=30, unique=True, editable=False)
    kind = models.CharField("Tür", max_length=20, choices=CorrectionKind.choices)
    target_model = models.CharField(max_length=60)
    target_id = models.PositiveBigIntegerField()
    target_code = models.CharField("Kayıt", max_length=60)
    customer = models.ForeignKey("catalog.Customer", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    work_order = models.ForeignKey("production.WorkOrder", on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="corrections")
    pallet = models.ForeignKey("warehouse.Pallet", on_delete=models.PROTECT, null=True, blank=True,
                               related_name="corrections")
    old_value = models.DecimalField("Eski değer", max_digits=14, decimal_places=3)
    new_value = models.DecimalField("Önerilen değer", max_digits=14, decimal_places=3)
    unit = models.CharField(max_length=10)
    reason = models.TextField("Gerekçe")
    affected = models.JSONField("Etkilenen kayıtlar", default=list)
    status = models.CharField(max_length=15, choices=CorrectionStatus.choices, default=CorrectionStatus.REQUESTED)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField("Karar gerekçesi", blank=True)

    class Meta:
        ordering = ["-requested_at"]
        constraints = [models.UniqueConstraint(fields=["target_model", "target_id"], condition=models.Q(status="talep"),
                                               name="uniq_open_correction_per_target")]

    def __str__(self):
        return self.code
