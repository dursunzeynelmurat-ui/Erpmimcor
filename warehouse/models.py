import secrets

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from catalog.models import Product
from core.models import QualityStatus
from orders.models import OrderLine, SalesOrder
from production.models import Coil, WorkOrder

KG = {"max_digits": 12, "decimal_places": 3}


def new_qr_token():
    return secrets.token_urlsafe(24)


class PalletStatus(models.TextChoices):
    AWAITING_ACCEPTANCE = "depo_kabul_bekliyor", "Depo kabulü bekliyor"
    IN_STOCK = "depoda", "Depoda"
    EMPTY = "bos", "Boşaldı"


class WeightStatus(models.TextChoices):
    NOT_WEIGHED = "tartilmadi", "Tartılmadı"
    CURRENT = "guncel", "Güncel"
    REWEIGH = "yeniden_tartim_bekliyor", "Yeniden tartım bekliyor"


class Pallet(models.Model):
    """Her palet tek bir ürün koduna aittir (yapısal olarak tek product alanı).
    Kutu adedi yalnız PalletMovement yazan servislerle değişir."""

    code = models.CharField("Palet no", max_length=30, unique=True, editable=False)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="pallets", verbose_name="Ürün")
    work_order = models.ForeignKey(WorkOrder, on_delete=models.PROTECT, null=True, blank=True, related_name="pallets")
    box_count = models.PositiveIntegerField("Kutu adedi", default=0, editable=False)
    status = models.CharField(max_length=30, choices=PalletStatus.choices, default=PalletStatus.AWAITING_ACCEPTANCE)
    quality_status = models.CharField("Kalite", max_length=20, choices=QualityStatus.choices,
                                      default=QualityStatus.PENDING)
    weight_status = models.CharField("Ağırlık durumu", max_length=30, choices=WeightStatus.choices,
                                     default=WeightStatus.NOT_WEIGHED)
    qr_token = models.CharField(max_length=64, unique=True, default=new_qr_token, editable=False)
    location = models.CharField("Depo yeri", max_length=60, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    accepted_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Palet"
        verbose_name_plural = "Paletler"
        constraints = [models.CheckConstraint(condition=Q(box_count__gte=0), name="pallet_box_count_non_negative")]

    def __str__(self):
        return self.code

    @property
    def reserved_boxes(self):
        return sum(r.open_quantity for r in self.reservations.filter(status=ReservationStatus.ACTIVE))

    @property
    def available_boxes(self):
        return self.box_count - self.reserved_boxes

    @property
    def current_weighing(self):
        return self.weighings.filter(is_current=True).first()


class PalletCoil(models.Model):
    pallet = models.ForeignKey(Pallet, on_delete=models.PROTECT, related_name="pallet_coils")
    coil = models.OneToOneField(Coil, on_delete=models.PROTECT, related_name="pallet_link")
    created_at = models.DateTimeField(auto_now_add=True)


class MovementKind(models.TextChoices):
    PRODUCTION = "uretim_girisi", "Üretimden giriş"
    SHIPMENT = "sevk", "Sevkiyat"
    TRANSFER_OUT = "aktarim_cikis", "Başka palete aktarım (çıkış)"
    TRANSFER_IN = "aktarim_giris", "Paletten aktarım (giriş)"
    COUNT_ADJUST = "sayim_duzeltme", "Sayım düzeltmesi"
    DAMAGE_OUT = "hasar_ayirma", "Hasarlı ürün ayırma (çıkış)"
    DAMAGE_IN = "hasar_girisi", "Hasarlı ürün paleti (giriş)"


class PalletMovement(models.Model):
    pallet = models.ForeignKey(Pallet, on_delete=models.PROTECT, related_name="movements")
    kind = models.CharField(max_length=20, choices=MovementKind.choices)
    quantity = models.IntegerField("Kutu (+/-)")
    balance_after = models.PositiveIntegerField()
    related_pallet = models.ForeignKey(Pallet, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    work_order = models.ForeignKey(WorkOrder, on_delete=models.PROTECT, null=True, blank=True, related_name="pallet_movements")
    shipment_line = models.ForeignKey("ShipmentLine", on_delete=models.PROTECT, null=True, blank=True, related_name="movements")
    group = models.CharField("İşlem grubu", max_length=32, blank=True,
                             help_text="Kaynak ve hedef hareketlerini birbirine bağlar.")
    reason = models.TextField(blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at", "-id"]
        constraints = [models.CheckConstraint(condition=~Q(quantity=0), name="pallet_movement_non_zero")]


class PalletWeighing(models.Model):
    """Tartım kaydı. Brüt zorunlu; daralar boş bırakılabilir (boş ≠ 0). Eski tartımlar silinmez."""

    pallet = models.ForeignKey(Pallet, on_delete=models.PROTECT, related_name="weighings")
    gross_kg = models.DecimalField("Brüt ağırlık (kg)", **KG)
    pallet_tare_kg = models.DecimalField("Palet darası (kg)", null=True, blank=True, **KG)
    box_tare_kg = models.DecimalField("Kutu darası (kg / kutu)", null=True, blank=True, **KG)
    core_tare_kg = models.DecimalField("Masura darası (kg, toplam)", null=True, blank=True, **KG)
    roll_weights = models.JSONField("Tekil rulo ağırlıkları (kg)", default=list, blank=True)
    box_count_at_weighing = models.PositiveIntegerField()
    is_current = models.BooleanField(default=True)
    note = models.CharField("Not", max_length=200, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(gross_kg__gt=0), name="weighing_gross_positive"),
            models.UniqueConstraint(fields=["pallet"], condition=Q(is_current=True), name="one_current_weighing"),
        ]


class LabelPrint(models.Model):
    pallet = models.ForeignKey(Pallet, on_delete=models.PROTECT, related_name="label_prints")
    is_reprint = models.BooleanField(default=False)
    box_count_at_print = models.PositiveIntegerField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]


class ReservationStatus(models.TextChoices):
    ACTIVE = "aktif", "Aktif"
    SHIPPED = "sevk_edildi", "Sevk edildi"
    CANCELLED = "iptal", "İptal"


class Reservation(models.Model):
    """Siparişe ayırma: fiziksel stoğu azaltmaz; yalnız sevk edilebilir/ayrılabilir miktarı sınırlar."""

    pallet = models.ForeignKey(Pallet, on_delete=models.PROTECT, related_name="reservations")
    order_line = models.ForeignKey(OrderLine, on_delete=models.PROTECT, related_name="reservations")
    quantity = models.PositiveIntegerField("Ayrılan kutu")
    shipped_quantity = models.PositiveIntegerField("Sevk edilen kutu", default=0)
    status = models.CharField(max_length=20, choices=ReservationStatus.choices, default=ReservationStatus.ACTIVE)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    cancel_reason = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="reservation_qty_positive"),
            models.CheckConstraint(condition=Q(shipped_quantity__lte=F("quantity")), name="reservation_not_overshipped"),
        ]

    def __str__(self):
        return f"{self.pallet.code} → {self.order_line.code} ({self.quantity})"

    @property
    def code(self):
        return f"REZ-{self.pk}"

    @property
    def open_quantity(self):
        return self.quantity - self.shipped_quantity


class ShipmentStatus(models.TextChoices):
    DRAFT = "taslak", "Hazırlanıyor"
    SHIPPED = "sevk_edildi", "Sevk edildi"


class Shipment(models.Model):
    code = models.CharField("Sevkiyat no", max_length=30, unique=True, editable=False)
    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name="shipments")
    status = models.CharField(max_length=20, choices=ShipmentStatus.choices, default=ShipmentStatus.DRAFT)
    vehicle = models.CharField("Araç / plaka", max_length=60, blank=True)
    notes = models.TextField("Not", blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    shipped_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    shipped_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.code


class ShipmentLine(models.Model):
    shipment = models.ForeignKey(Shipment, on_delete=models.PROTECT, related_name="lines")
    reservation = models.ForeignKey(Reservation, on_delete=models.PROTECT, related_name="shipment_lines")
    pallet = models.ForeignKey(Pallet, on_delete=models.PROTECT, related_name="shipment_lines")
    order_line = models.ForeignKey(OrderLine, on_delete=models.PROTECT, related_name="shipment_lines")
    quantity = models.PositiveIntegerField("Kutu")

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="shipment_line_qty_positive"),
            models.UniqueConstraint(fields=["shipment", "reservation"], name="uniq_shipment_reservation"),
        ]
