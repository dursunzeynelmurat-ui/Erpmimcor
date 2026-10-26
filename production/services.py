from django.db import transaction
from django.utils import timezone

from accounts.permissions import check
from catalog.models import MachineType, RecipeRevision
from core.exceptions import BusinessRuleError
from core.services import audit, next_code
from orders.models import LineStatus, OrderLine
from orders.services import set_line_status

from .models import OperationStatus, WorkOrder, WorkOrderOperation, WorkOrderPurpose, WorkOrderStatus


def _validate_machines(machines):
    if not machines:
        raise BusinessRuleError("En az bir operasyon (makine) seçilmelidir.", field="machine_1")
    if machines[0].machine_type != MachineType.EXTRUDER:
        raise BusinessRuleError("İlk operasyon extruder olmalıdır; bobin takibi extruder çıkışından başlar.",
                                field="machine_1")
    for m in machines:
        if not m.is_active:
            raise BusinessRuleError(f"{m.code} makinesi pasif.", field="machine_1")


@transaction.atomic
def create_work_order(actor, *, machines, target_quantity, unit, order_line=None, product=None, revision=None,
                      planned_start=None, notes=""):
    """Siparişe bağlı (order_line) veya stok için (product + revision) iş emri açar.
    Reçete revizyonu iş emrine sabitlenir; sonradan açılan revizyonlar bu iş emrini etkilemez."""
    check(actor, "workorder.create")
    if target_quantity is None or target_quantity <= 0:
        raise BusinessRuleError("Hedef miktar sıfırdan büyük olmalı.", field="target_quantity")
    if order_line is not None:
        order_line = OrderLine.objects.select_for_update(of=("self",)).select_related("order", "product").get(pk=order_line.pk)
        if order_line.status not in (LineStatus.APPROVED, LineStatus.IN_PRODUCTION):
            raise BusinessRuleError("Sipariş kalemi planlama tarafından teknik olarak onaylanmamış.")
        purpose, product, revision = WorkOrderPurpose.ORDER, order_line.product, order_line.approved_revision
    else:
        if product is None or revision is None:
            raise BusinessRuleError("Stok üretimi için ürün ve reçete revizyonu seçilmelidir.", field="product")
        revision = RecipeRevision.objects.select_related("recipe").get(pk=revision.pk)
        if revision.recipe.product_id != product.pk:
            raise BusinessRuleError("Reçete revizyonu seçilen ürüne ait değil.", field="revision")
        purpose = WorkOrderPurpose.STOCK
    if not product.is_active:
        raise BusinessRuleError("Pasif ürün için iş emri açılamaz.", field="product")
    _validate_machines(machines)
    wo = WorkOrder.objects.create(code=next_code("IE"), purpose=purpose, order_line=order_line, product=product,
                                  recipe_revision=revision, target_quantity=target_quantity, unit=unit,
                                  planned_start=planned_start, notes=notes, created_by=actor)
    for seq, machine in enumerate(machines, start=1):
        WorkOrderOperation.objects.create(work_order=wo, seq=seq, machine=machine, machine_type=machine.machine_type)
    audit(actor, "workorder.create", wo, {
        "purpose": (None, purpose), "order_line": (None, order_line.code if order_line else None),
        "product": (None, product.code), "recipe_revision": (None, str(revision)),
        "target_quantity": (None, target_quantity), "operations": (None, [m.code for m in machines]),
    })
    return wo


def _lock_wo(wo):
    return WorkOrder.objects.select_for_update(of=("self",)).select_related("order_line").get(pk=wo.pk)


@transaction.atomic
def start_work_order(actor, wo):
    check(actor, "production.execute")
    wo = _lock_wo(wo)
    if wo.status != WorkOrderStatus.PLANNED:
        raise BusinessRuleError(f"İş emri zaten '{wo.get_status_display()}' durumunda.")
    wo.status = WorkOrderStatus.STARTED
    wo.started_at = timezone.now()
    wo.started_by = actor
    wo.save()
    audit(actor, "workorder.start", wo, {"status": (WorkOrderStatus.PLANNED, wo.status)})
    first = wo.operations.order_by("seq").first()
    _start_op(actor, first)
    if wo.order_line_id:
        set_line_status(actor, wo.order_line, LineStatus.IN_PRODUCTION)
    return wo


def _start_op(actor, op):
    op = WorkOrderOperation.objects.select_for_update().get(pk=op.pk)
    if op.status != OperationStatus.WAITING:
        raise BusinessRuleError("Operasyon zaten başlatılmış.")
    op.status = OperationStatus.RUNNING
    op.started_at = timezone.now()
    op.started_by = actor
    op.save()
    audit(actor, "operation.start", op, {"status": (OperationStatus.WAITING, op.status), "machine": (None, op.machine.code)})
    return op


@transaction.atomic
def start_operation(actor, op):
    check(actor, "production.execute")
    wo = _lock_wo(op.work_order)
    if wo.status != WorkOrderStatus.STARTED:
        raise BusinessRuleError("Önce iş emri üretimi başlatılmalı.")
    prev = wo.operations.filter(seq__lt=op.seq).order_by("-seq").first()
    if prev and prev.status == OperationStatus.WAITING:
        raise BusinessRuleError(f"Önce {prev.seq}. operasyon başlatılmalı.")
    return _start_op(actor, op)


@transaction.atomic
def complete_operation(actor, op):
    check(actor, "production.execute")
    wo = _lock_wo(op.work_order)
    op = WorkOrderOperation.objects.select_for_update().get(pk=op.pk)
    if op.status != OperationStatus.RUNNING:
        raise BusinessRuleError("Yalnızca devam eden operasyon tamamlanabilir.")
    if wo.operations.filter(seq__lt=op.seq).exclude(status=OperationStatus.DONE).exists():
        raise BusinessRuleError("Önceki operasyonlar tamamlanmadan bu operasyon tamamlanamaz.")
    op.status = OperationStatus.DONE
    op.finished_at = timezone.now()
    op.finished_by = actor
    op.save()
    wo.save(update_fields=["updated_at"])
    audit(actor, "operation.complete", op, {"status": (OperationStatus.RUNNING, op.status)})
    return op
