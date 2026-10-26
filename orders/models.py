from django.conf import settings
from django.db import models
from django.db.models import Q

from catalog.models import QTY, Customer, Product, RecipeRevision, Unit


class OrderStatus(models.TextChoices):
    OPEN = "acik", "Açık"
    DONE = "tamamlandi", "Tamamlandı"
    CANCELLED = "iptal", "İptal"


class SalesOrder(models.Model):
    code = models.CharField("Sipariş no", max_length=30, unique=True, editable=False)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="orders", verbose_name="Müşteri")
    customer_ref = models.CharField("Müşteri sipariş ref.", max_length=60, blank=True)
    order_date = models.DateField("Sipariş tarihi")
    due_date = models.DateField("Termin", null=True, blank=True)
    notes = models.TextField("Not", blank=True)
    # Müşteri kartından oluşturma anında kopyalanır; kart sonradan değişse de bu sipariş değişmez.
    billing_address = models.TextField("Fatura adresi", blank=True)
    shipping_address = models.TextField("Teslimat adresi", blank=True)
    currency = models.CharField("Para birimi", max_length=3, blank=True)
    payment_method = models.CharField("Ödeme şekli", max_length=20, blank=True)
    payment_term_days = models.PositiveIntegerField("Vade (gün)", null=True, blank=True)
    delivery_terms = models.CharField("Teslim şekli", max_length=20, blank=True)
    delivery_place = models.CharField("Teslim yeri", max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=OrderStatus.choices, default=OrderStatus.OPEN)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Satış siparişi"
        verbose_name_plural = "Satış siparişleri"

    def __str__(self):
        return self.code


class LineStatus(models.TextChoices):
    WAITING_PLANNING = "planlama_bekliyor", "Planlama onayı bekliyor"
    APPROVED = "onaylandi", "Teknik onaylı"
    IN_PRODUCTION = "uretimde", "Üretimde"
    PRODUCED = "uretim_tamamlandi", "Üretim tamamlandı"
    SHIPPED = "sevk_edildi", "Sevk edildi"
    CANCELLED = "iptal", "İptal"


class OrderLine(models.Model):
    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name="lines")
    line_no = models.PositiveIntegerField("Kalem no")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="order_lines", verbose_name="Ürün")
    quantity = models.DecimalField("Miktar", **QTY)
    unit = models.CharField("Birim", max_length=10, choices=Unit.choices, default=Unit.BOX)
    due_date = models.DateField("Kalem termini", null=True, blank=True)
    status = models.CharField(max_length=30, choices=LineStatus.choices, default=LineStatus.WAITING_PLANNING)
    approved_revision = models.ForeignKey(RecipeRevision, on_delete=models.PROTECT, null=True, blank=True,
                                          related_name="approved_lines", verbose_name="Onaylı reçete revizyonu")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    planning_note = models.TextField("Planlama notu", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "line_no"]
        constraints = [
            models.UniqueConstraint(fields=["order", "line_no"], name="uniq_order_line_no"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="order_line_qty_positive"),
        ]

    def __str__(self):
        return self.code

    @property
    def code(self):
        return f"{self.order.code}/{self.line_no:02d}"
