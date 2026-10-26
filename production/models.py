from django.conf import settings
from django.db import models
from django.db.models import Q

from catalog.models import QTY, Machine, MachineType, Product, RecipeRevision, Unit
from orders.models import OrderLine


class WorkOrderPurpose(models.TextChoices):
    ORDER = "siparis", "Siparişe bağlı"
    STOCK = "stok", "Müşteri stoğu için"


class WorkOrderStatus(models.TextChoices):
    PLANNED = "planlandi", "Planlandı"
    STARTED = "basladi", "Üretimde"
    CLOSED = "kapatildi", "Kapatıldı"
    CANCELLED = "iptal", "İptal"


class WorkOrder(models.Model):
    code = models.CharField("İş emri no", max_length=30, unique=True, editable=False)
    purpose = models.CharField("Üretim amacı", max_length=10, choices=WorkOrderPurpose.choices)
    order_line = models.ForeignKey(OrderLine, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="work_orders", verbose_name="Sipariş kalemi")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="work_orders", verbose_name="Ürün")
    recipe_revision = models.ForeignKey(RecipeRevision, on_delete=models.PROTECT, related_name="work_orders",
                                        verbose_name="Reçete revizyonu")
    target_quantity = models.DecimalField("Hedef miktar", **QTY)
    unit = models.CharField("Birim", max_length=10, choices=Unit.choices, default=Unit.BOX)
    planned_start = models.DateField("Planlanan başlangıç", null=True, blank=True)
    notes = models.TextField("Not", blank=True)
    status = models.CharField(max_length=20, choices=WorkOrderStatus.choices, default=WorkOrderStatus.PLANNED)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    closed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "İş emri"
        verbose_name_plural = "İş emirleri"
        constraints = [
            # Stok üretimi sipariş kalemsiz; siparişe bağlı üretim kalemli olmalı (yapay sipariş yok).
            models.CheckConstraint(
                condition=(Q(purpose="siparis", order_line__isnull=False) | Q(purpose="stok", order_line__isnull=True)),
                name="work_order_purpose_matches_line"),
            models.CheckConstraint(condition=Q(target_quantity__gt=0), name="work_order_target_positive"),
        ]

    def __str__(self):
        return self.code

    @property
    def current_operation(self):
        ops = list(self.operations.all())
        running = [o for o in ops if o.status == OperationStatus.RUNNING]
        if running:
            return running[-1]
        waiting = [o for o in ops if o.status == OperationStatus.WAITING]
        return waiting[0] if waiting else (ops[-1] if ops else None)

    @property
    def stage_display(self):
        if self.status == WorkOrderStatus.PLANNED:
            return "Planlandı – üretim başlamadı"
        if self.status in (WorkOrderStatus.CLOSED, WorkOrderStatus.CANCELLED):
            return self.get_status_display()
        op = self.current_operation
        if op is None:
            return self.get_status_display()
        return f"{op.seq}. {op.get_machine_type_display()} ({op.machine.code}) – {op.get_status_display()}"


class OperationStatus(models.TextChoices):
    WAITING = "bekliyor", "Bekliyor"
    RUNNING = "devam_ediyor", "Devam ediyor"
    DONE = "tamamlandi", "Tamamlandı"


class WorkOrderOperation(models.Model):
    work_order = models.ForeignKey(WorkOrder, on_delete=models.PROTECT, related_name="operations")
    seq = models.PositiveIntegerField("Sıra")
    machine_type = models.CharField("Makine türü", max_length=20, choices=MachineType.choices)
    machine = models.ForeignKey(Machine, on_delete=models.PROTECT, related_name="operations", verbose_name="Makine")
    status = models.CharField(max_length=20, choices=OperationStatus.choices, default=OperationStatus.WAITING)
    started_at = models.DateTimeField(null=True, blank=True)
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    finished_at = models.DateTimeField(null=True, blank=True)
    finished_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["work_order", "seq"]
        constraints = [models.UniqueConstraint(fields=["work_order", "seq"], name="uniq_operation_seq")]

    def __str__(self):
        return f"{self.work_order.code} op.{self.seq} {self.get_machine_type_display()}"

    @property
    def code(self):
        return f"{self.work_order.code}/OP{self.seq}"
