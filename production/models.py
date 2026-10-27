from django.conf import settings
from django.db import models
from django.db.models import Q

from catalog.models import QTY, Machine, MachineType, Product, RecipeRevision, Unit
from core.models import QualityStatus, ValueSource
from orders.models import OrderLine


class WorkOrderPurpose(models.TextChoices):
    ORDER = "siparis", "Siparişe bağlı"
    STOCK = "stok", "Müşteri stoğu için"


class WorkOrderStatus(models.TextChoices):
    DRAFT = "taslak", "Taslak"
    PLANNED = "planlandi", "Serbest bırakıldı"
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
    status = models.CharField(max_length=20, choices=WorkOrderStatus.choices, default=WorkOrderStatus.DRAFT)
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    cancel_reason = models.TextField("İptal gerekçesi", blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    assigned_foreman = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                         related_name="assigned_work_orders", verbose_name="Sorumlu ustabaşı")
    assigned_at = models.DateTimeField(null=True, blank=True)
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
    STOPPED = "durduruldu", "Duruşta"
    DONE = "tamamlandi", "Tamamlandı"


class WorkOrderOperation(models.Model):
    work_order = models.ForeignKey(WorkOrder, on_delete=models.PROTECT, related_name="operations")
    seq = models.PositiveIntegerField("Sıra")
    machine_type = models.CharField("Makine türü", max_length=20, choices=MachineType.choices)
    machine = models.ForeignKey(Machine, on_delete=models.PROTECT, related_name="operations", verbose_name="Makine")
    planned_date = models.DateField("Planlanan tarih", null=True, blank=True)
    shift = models.ForeignKey("accounts.Shift", on_delete=models.PROTECT, null=True, blank=True, verbose_name="Vardiya")
    responsible_shift = models.ForeignKey("accounts.Shift", on_delete=models.PROTECT, null=True, blank=True,
                                          related_name="+", verbose_name="Sorumlu vardiya",
                                          help_text="Planlanan vardiya; vardiya devri teslim alınınca devralan vardiya.")
    queue_position = models.PositiveIntegerField("Çalışma sırası", null=True, blank=True)
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


class CoilStatus(models.TextChoices):
    AVAILABLE = "kullanilabilir", "Kullanılabilir"
    PARTIAL = "kismi_tuketildi", "Kısmi tüketildi"
    CONSUMED = "tuketildi", "Tüketildi"
    PALLETIZED = "palete_alindi", "Palete alındı"


class Coil(models.Model):
    """Bobin: extruder çıkışından itibaren izlenir. Dönüştürme/bölme CoilLink ile kaynağa bağlanır."""

    code = models.CharField("Bobin no", max_length=30, unique=True, editable=False)
    work_order = models.ForeignKey(WorkOrder, on_delete=models.PROTECT, related_name="coils")
    operation = models.ForeignKey(WorkOrderOperation, on_delete=models.PROTECT, related_name="coils")
    machine = models.ForeignKey(Machine, on_delete=models.PROTECT, related_name="coils")
    weight_kg = models.DecimalField("Ağırlık (kg)", max_digits=12, decimal_places=3)
    weight_source = models.CharField("Ağırlık kaynağı", max_length=12, choices=ValueSource.choices,
                                     default=ValueSource.MEASURED)
    length_m = models.DecimalField("Uzunluk (m)", max_digits=10, decimal_places=2, null=True, blank=True)
    remaining_kg = models.DecimalField("Kalan (kg)", max_digits=12, decimal_places=3)
    quality_status = models.CharField("Kalite", max_length=20, choices=QualityStatus.choices, default=QualityStatus.PENDING)
    status = models.CharField(max_length=20, choices=CoilStatus.choices, default=CoilStatus.AVAILABLE)
    notes = models.CharField("Not", max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Bobin"
        verbose_name_plural = "Bobinler"
        constraints = [
            models.CheckConstraint(condition=Q(weight_kg__gt=0), name="coil_weight_positive"),
            models.CheckConstraint(condition=Q(remaining_kg__gte=0) & Q(remaining_kg__lte=models.F("weight_kg")),
                                   name="coil_remaining_in_range"),
        ]

    def __str__(self):
        return self.code


class CoilLink(models.Model):
    """Kaynak bobinden çıktı bobine: kısmi tüketim miktarıyla birlikte."""

    source = models.ForeignKey(Coil, on_delete=models.PROTECT, related_name="output_links")
    output = models.ForeignKey(Coil, on_delete=models.PROTECT, related_name="source_links")
    consumed_kg = models.DecimalField("Tüketilen (kg)", max_digits=12, decimal_places=3)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(consumed_kg__gt=0), name="coil_link_positive"),
            models.UniqueConstraint(fields=["source", "output"], name="uniq_coil_link"),
        ]


class WorkOrderClosure(models.Model):
    work_order = models.OneToOneField(WorkOrder, on_delete=models.PROTECT, related_name="closure")
    issued_kg = models.DecimalField("Net verilen malzeme (kg)", max_digits=12, decimal_places=3)
    good_kg = models.DecimalField("Sağlam çıktı (kg)", max_digits=12, decimal_places=3)
    blocked_kg = models.DecimalField("Blokeli çıktı (kg)", max_digits=12, decimal_places=3)
    wip_kg = models.DecimalField("Ara stokta kalan bobin (kg)", max_digits=12, decimal_places=3)
    scrap_kg = models.DecimalField("Fire (kg)", max_digits=12, decimal_places=3)
    difference_kg = models.DecimalField("Denge farkı (kg)", max_digits=12, decimal_places=3)
    good_boxes = models.PositiveIntegerField("Palete alınan kutu")
    reason = models.TextField("Gerekçe", blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    closed_at = models.DateTimeField(auto_now_add=True)


class StopReason(models.TextChoices):
    BREAKDOWN = "ariza", "Arıza"
    MATERIAL = "malzeme", "Malzeme bekleme"
    MAINTENANCE = "bakim", "Planlı bakım"
    QUALITY = "kalite", "Kalite sorunu"
    SETUP = "ayar", "Ayar / kalıp değişimi"
    SHIFT = "vardiya", "Vardiya sonu"
    OTHER = "diger", "Diğer"


class OperationStop(models.Model):
    operation = models.ForeignKey(WorkOrderOperation, on_delete=models.PROTECT, related_name="stops")
    reason = models.CharField("Duruş nedeni", max_length=10, choices=StopReason.choices)
    note = models.TextField("Açıklama", blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-started_at"]


class ShiftHandover(models.Model):
    """Vardiya devri: kalan iş, kaynak bobinler ve makine başı malzeme kayıt anındaki hâliyle saklanır."""

    operation = models.ForeignKey(WorkOrderOperation, on_delete=models.PROTECT, related_name="handovers")
    from_shift = models.ForeignKey("accounts.Shift", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    to_shift = models.ForeignKey("accounts.Shift", on_delete=models.PROTECT, related_name="+", verbose_name="Devralan vardiya")
    remaining_note = models.CharField("Kalan iş", max_length=200)
    open_coils = models.JSONField(default=list)
    machine_side_material = models.TextField("Makine başındaki malzeme", blank=True)
    notes = models.TextField("Devir notu", blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    acknowledged_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class InstructionStatus(models.TextChoices):
    CURRENT = "gecerli", "Geçerli"
    SUPERSEDED = "guncellendi", "Güncellendi – geçersiz"
    CANCELLED = "iptal", "İptal – geçersiz"


class WorkInstruction(models.Model):
    """Makineye özel iş talimatı sürümü. İçerik değişince yeni sürüm açılır, eskisi geçersiz işaretlenir.
    Basılmış kâğıt sistemden geri alınamaz; yalnız sistemde geçersiz olduğu görünür."""

    operation = models.ForeignKey(WorkOrderOperation, on_delete=models.PROTECT, related_name="instructions")
    version = models.PositiveIntegerField()
    content_hash = models.CharField(max_length=64)
    snapshot = models.JSONField()
    status = models.CharField(max_length=12, choices=InstructionStatus.choices, default=InstructionStatus.CURRENT)
    status_reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    status_changed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["operation", "-version"]
        constraints = [models.UniqueConstraint(fields=["operation", "version"], name="uniq_instruction_version")]

    @property
    def code(self):
        return f"{self.operation.code}/T{self.version}"


class InstructionPrint(models.Model):
    instruction = models.ForeignKey(WorkInstruction, on_delete=models.PROTECT, related_name="prints")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    at = models.DateTimeField(auto_now_add=True)


class AckKind(models.TextChoices):
    SEEN = "gordum", "Gördüm"
    RECEIVED = "teslim_aldim", "İşi/talimatı teslim aldım"


class InstructionAck(models.Model):
    instruction = models.ForeignKey(WorkInstruction, on_delete=models.PROTECT, related_name="acks")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=15, choices=AckKind.choices)
    at = models.DateTimeField(auto_now_add=True)


class RouteException(models.Model):
    """Operasyonun bir önceki operasyon yerine daha önceki bir operasyonun bobinini kaynak almasına yetkili izin."""

    operation = models.ForeignKey(WorkOrderOperation, on_delete=models.PROTECT, related_name="route_exceptions")
    reason = models.TextField("Gerekçe")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)


class MaterialDeviation(models.Model):
    """Reçete dışı malzeme çıkışı; gerekçe ve yetkili kişi kapanışta listelenir."""

    work_order = models.ForeignKey(WorkOrder, on_delete=models.PROTECT, related_name="material_deviations")
    lot_movement = models.OneToOneField("catalog.LotMovement", on_delete=models.PROTECT, related_name="deviation")
    material = models.ForeignKey("catalog.Material", on_delete=models.PROTECT, related_name="+")
    reason = models.TextField("Gerekçe")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)


class ClosureReview(models.Model):
    """Kapanıştan sonra onaylı düzeltmelerle değişen dengenin yetkili tarafından değerlendirilmesi. İlk kapanış kaydı
    (WorkOrderClosure) değişmez; değerlendirme anındaki güncel değerler ayrı saklanır."""

    closure = models.ForeignKey(WorkOrderClosure, on_delete=models.PROTECT, related_name="reviews")
    snapshot = models.JSONField()
    reason = models.TextField("Değerlendirme gerekçesi")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    reviewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-reviewed_at", "-id"]
