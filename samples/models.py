"""Fiziksel numune yönetimi.

Fiziksel durum, iç kalite kararı ve müşteri geri bildirimi ayrı alanlardır (ör. "müşteride" olan numunenin iç kalite
kararı ayrıca korunur). Stok etkisi: yalnız iç stoktan ayrılan numune bir kez palet hareketi (numune ayırma) yaratır;
gönderim, geri dönüş ve imha yalnız olay kaydıdır, ikinci stok hareketi oluşturmaz. Müşteriden gelen referans numune
şirket stoğunu artırmaz. Birimler arasında (kutu/rulo/kg/m) dönüşüm yapılmaz.
"""
import secrets

from django.conf import settings
from django.db import models
from django.db.models import Q


def new_token():
    return secrets.token_urlsafe(16)


class SampleKind(models.TextChoices):
    CUSTOMER_REFERENCE = "musteri_referans", "Müşteriden gelen referans"
    PRODUCTION_CONTROL = "uretim_kontrol", "Üretim kontrol numunesi"
    RETAINED = "sahit", "Saklama / şahit numunesi"
    TO_CUSTOMER = "musteriye_giden", "Müşteriye gönderilen numune"


INTERNAL_KINDS = (SampleKind.PRODUCTION_CONTROL, SampleKind.RETAINED, SampleKind.TO_CUSTOMER)


class StockSource(models.TextChoices):
    """Firma içi numunenin stokla ilişkisi. Boş: müşteri referansı veya eski kayıt (belirtilmemiş)."""
    UNKNOWN = "", "Belirtilmemiş"
    FROM_STOCK = "stoktan", "Firma stoğundan tam kutu (stoktan ayrılacak)"
    NOT_FROM_STOCK = "stok_disi", "Stok dışı (hattan/kesimden alındı; stok etkisi kaydedilmez)"
    ALREADY_OUT = "onceden_cikti", "Daha önce başka işlemle stoktan çıkarıldı (o hareket bağlanır)"


class SampleUnit(models.TextChoices):
    PIECE = "adet", "adet"
    BOX = "kutu", "kutu"
    ROLL = "rulo", "rulo"
    KG = "kg", "kg"
    METRE = "m", "metre"


class PhysicalStatus(models.TextChoices):
    STORED = "saklamada", "Firmada saklanıyor"
    SENT = "gonderildi", "Müşteriye gönderildi"
    RETURNED = "geri_dondu", "Geri döndü / iade alındı"
    DESTROYED = "imha", "İmha edildi"


class QualityDecision(models.TextChoices):
    PENDING = "bekliyor", "Karar bekliyor"
    OK = "uygun", "Uygun"
    NOT_OK = "uygun_degil", "Uygun değil"


class CustomerFeedback(models.TextChoices):
    NONE = "", "Henüz geri bildirim yok"
    APPROVED = "onayladi", "Müşteri onayladı"
    REJECTED = "reddetti", "Müşteri reddetti"
    COMMENT = "gorus", "Müşteri görüş bildirdi"


class Sample(models.Model):
    code = models.CharField("Numune kodu", max_length=30, unique=True, editable=False)
    kind = models.CharField("Numune türü", max_length=20, choices=SampleKind.choices)
    customer = models.ForeignKey("catalog.Customer", on_delete=models.PROTECT, related_name="samples")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, null=True, blank=True,
                                related_name="samples")
    revision = models.ForeignKey("catalog.RecipeRevision", on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="+")
    order_line = models.ForeignKey("orders.OrderLine", on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="samples")
    work_order = models.ForeignKey("production.WorkOrder", on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="samples")
    coil = models.ForeignKey("production.Coil", on_delete=models.PROTECT, null=True, blank=True, related_name="samples")
    pallet = models.ForeignKey("warehouse.Pallet", on_delete=models.PROTECT, null=True, blank=True,
                               related_name="samples")
    counterpart = models.CharField("Teslim eden / teslim alan", max_length=120, blank=True,
                                   help_text="Numuneyi getiren veya alan kişi (müşteri temsilcisi, kurye vb.).")
    occurred_at = models.DateTimeField("Alınma / teslim tarihi ve saati")
    quantity = models.DecimalField("Miktar", max_digits=12, decimal_places=3)
    unit = models.CharField("Birim", max_length=5, choices=SampleUnit.choices)
    room = models.CharField("Oda", max_length=40, blank=True)
    cabinet = models.CharField("Dolap", max_length=40, blank=True)
    shelf = models.CharField("Raf", max_length=40, blank=True)
    box = models.CharField("Kutu", max_length=40, blank=True)
    retention_until = models.DateField("Saklama son tarihi", null=True, blank=True)
    retention_conditions = models.CharField("Saklama koşulu", max_length=200, blank=True)
    notes = models.TextField("Açıklama", blank=True)
    shared_note = models.TextField("Müşteriyle paylaşılabilir not", blank=True,
                                   help_text="Satış ekibinin göreceği not. İç ölçüm ve reçete bilgisi yazmayın.")
    physical_status = models.CharField("Fiziksel durum", max_length=12, choices=PhysicalStatus.choices,
                                       default=PhysicalStatus.STORED)
    quality_decision = models.CharField("İç kalite kararı", max_length=12, choices=QualityDecision.choices,
                                        default=QualityDecision.PENDING)
    decision_reason = models.TextField("Karar gerekçesi", blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_pallets = models.ManyToManyField("warehouse.Pallet", blank=True, related_name="sample_decisions",
                                              verbose_name="Kararın kapsadığı paletler")
    decision_coils = models.ManyToManyField("production.Coil", blank=True, related_name="sample_decisions",
                                            verbose_name="Kararın kapsadığı bobinler")
    customer_feedback = models.CharField("Müşteri geri bildirimi", max_length=10, choices=CustomerFeedback.choices,
                                         blank=True)
    feedback_note = models.TextField("Geri bildirim notu", blank=True)
    feedback_reference = models.CharField("Geri bildirim referansı", max_length=200, blank=True)
    feedback_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+")
    feedback_at = models.DateTimeField(null=True, blank=True)
    stock_pallet = models.ForeignKey("warehouse.Pallet", on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="sample_allocations", verbose_name="Stoktan ayrıldığı palet")
    stock_boxes = models.PositiveIntegerField("Stoktan ayrılan kutu", null=True, blank=True)
    stock_movement = models.OneToOneField("warehouse.PalletMovement", on_delete=models.PROTECT, null=True, blank=True,
                                          related_name="sample")
    stock_source = models.CharField("Stok kaynağı", max_length=15, choices=StockSource.choices, blank=True,
                                    help_text="Firma içi numune firma stoğundan mı geliyor? Stoktan gelende gönderim/"
                                              "imha öncesi stok hareketi tamamlanmalıdır.")
    qr_token = models.CharField(max_length=40, unique=True, default=new_token, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name="sample_quantity_positive")]

    def __str__(self):
        return self.code

    @property
    def location(self):
        parts = [p for p in (self.room, self.cabinet, self.shelf, self.box) if p]
        return " / ".join(parts) or "—"


class EventKind(models.TextChoices):
    CREATED = "kayit", "Kayıt"
    MOVED = "konum", "Konum değişikliği"
    STOCK = "stok_ayirma", "Stoktan ayırma"
    SENT = "gonderim", "Müşteriye gönderim"
    RETURNED = "geri_donus", "Geri dönüş / iade alma"
    DESTROYED = "imha", "İmha"
    DECISION = "karar", "Kalite kararı"
    FEEDBACK = "geri_bildirim", "Müşteri geri bildirimi"


class SampleEvent(models.Model):
    sample = models.ForeignKey(Sample, on_delete=models.PROTECT, related_name="events")
    kind = models.CharField(max_length=15, choices=EventKind.choices)
    note = models.TextField(blank=True)
    counterpart = models.CharField(max_length=120, blank=True)
    location = models.CharField(max_length=200, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at", "-pk"]


class CriterionUnit(models.TextChoices):
    MM = "mm", "mm"
    MICRON = "mikron", "µm"
    PERCENT = "yuzde", "%"
    NEWTON = "N", "N"
    GRAM = "g", "g"
    PIECE = "adet", "adet"
    TEXT = "metin", "Metin (sayısal değil)"


class SampleCriterion(models.Model):
    """Yetkili kişilerce tanımlanır; sabit fabrika standardı değildir. Değiştirmek yeni kayıt açar (eski pasif);
    eski ölçümler kendi anlık kriter kopyasıyla kalır, yeniden yorumlanmaz."""

    name = models.CharField("Kriter adı", max_length=120)
    unit = models.CharField("Birim", max_length=8, choices=CriterionUnit.choices)
    min_value = models.DecimalField("Alt sınır", max_digits=12, decimal_places=3, null=True, blank=True)
    max_value = models.DecimalField("Üst sınır", max_digits=12, decimal_places=3, null=True, blank=True)
    description = models.CharField("Açıklama / ölçüm yöntemi", max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    replaces = models.OneToOneField("self", on_delete=models.PROTECT, null=True, blank=True, related_name="replaced_by")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "-created_at"]

    def __str__(self):
        return f"{self.name} ({self.get_unit_display()})"


class MeasurementResult(models.TextChoices):
    IN = "icinde", "Aralık içinde"
    OUT = "disinda", "Aralık dışında"
    NO_RANGE = "aralik_yok", "Kabul aralığı tanımlı değil"


class SampleMeasurement(models.Model):
    sample = models.ForeignKey(Sample, on_delete=models.PROTECT, related_name="measurements")
    criterion = models.ForeignKey(SampleCriterion, on_delete=models.PROTECT, related_name="measurements")
    criterion_name = models.CharField(max_length=120)
    unit = models.CharField(max_length=8)
    min_value = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    max_value = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    value = models.DecimalField("Ölçülen değer", max_digits=12, decimal_places=3, null=True, blank=True)
    text_value = models.CharField("Gözlem", max_length=200, blank=True)
    result = models.CharField(max_length=12, choices=MeasurementResult.choices)
    note = models.CharField("Not", max_length=200, blank=True)
    measured_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    measured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["measured_at", "pk"]


def sample_attachment_path(instance, filename):
    import uuid
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"numune-ekleri/{instance.sample_id}/{uuid.uuid4().hex}.{ext}"


class SampleAttachment(models.Model):
    sample = models.ForeignKey(Sample, on_delete=models.PROTECT, related_name="attachments")
    title = models.CharField("Başlık", max_length=120)
    file = models.FileField(upload_to=sample_attachment_path)
    original_name = models.CharField(max_length=200)
    content_type = models.CharField(max_length=60)
    size = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)
