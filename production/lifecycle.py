"""İş emri yaşam döngüsü: taslak → serbest bırakma → (geri çekme → düzenleme → yeniden serbest bırakma) →
başlatma → operasyon duruş/devam ve vardiya devri → kapanış / iptal → arşiv.

Hareket oluşmuş iş emri silinmez; başlamış işte ürün ve kullanılmış reçete revizyonu değiştirilemez.
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounts.permissions import check
from catalog.models import Machine
from core.exceptions import BusinessRuleError
from core.models import QualityStatus
from core.services import audit, diff, num

from .models import (CoilStatus, InstructionStatus, OperationStatus, OperationStop, ShiftHandover, WorkInstruction,
                     WorkOrder, WorkOrderOperation, WorkOrderStatus)

OPEN_STATUSES = (WorkOrderStatus.DRAFT, WorkOrderStatus.PLANNED, WorkOrderStatus.STARTED)


def _lock(wo):
    return WorkOrder.objects.select_for_update(of=("self",)).select_related("order_line").get(pk=wo.pk)


def has_movements(wo):
    """Fiziksel iz bırakmış mı? (operasyon başlangıcı, bobin, malzeme, palet hareketi)"""
    return (wo.operations.exclude(status=OperationStatus.WAITING).exists() or wo.coils.exists()
            or wo.lot_movements.exists() or wo.pallet_movements.exists())


def invalidate_instructions(wo, status, reason):
    """İş emri değişince/iptalde basılmış talimatlar sistemde geçersiz işaretlenir (kâğıt geri alınamaz)."""
    return WorkInstruction.objects.filter(operation__work_order=wo, status=InstructionStatus.CURRENT).update(
        status=status, status_reason=reason, status_changed_at=timezone.now())


def revision_conflict(wo):
    """Siparişe bağlı işte kalemin onaylı revizyonu iş emrinkinden farklıysa açıklama döner (sessiz bırakılmaz)."""
    line = wo.order_line
    if line and line.approved_revision_id and line.approved_revision_id != wo.recipe_revision_id:
        return (f"Sipariş kalemi Rev.{line.approved_revision.rev_no} ile onaylı; iş emri Rev.{wo.recipe_revision.rev_no} "
                "ile açılmış. İş emri geri çekilip güncellenmeli veya kalem onayı gözden geçirilmeli.")
    return None


def quantity_conflict(wo):
    line = wo.order_line
    if line and wo.status in OPEN_STATUSES and wo.target_quantity != line.quantity:
        return f"Kalem miktarı {num(line.quantity)} {line.get_unit_display()}, iş emri hedefi {num(wo.target_quantity)}."
    return None


@transaction.atomic
def release(actor, wo):
    check(actor, "workorder.release")
    wo = _lock(wo)
    if wo.status != WorkOrderStatus.DRAFT:
        raise BusinessRuleError("Yalnız taslak iş emri serbest bırakılabilir.")
    if not wo.assigned_foreman_id:
        raise BusinessRuleError("Serbest bırakmadan önce sorumlu ustabaşı atanmalı.", field="foreman")
    conflict = revision_conflict(wo)
    if conflict:
        raise BusinessRuleError(conflict)
    wo.status, wo.released_at, wo.released_by = WorkOrderStatus.PLANNED, timezone.now(), actor
    wo.save()
    audit(actor, "workorder.release", wo, {"status": (WorkOrderStatus.DRAFT, wo.status)})
    return wo


@transaction.atomic
def withdraw(actor, wo, reason):
    """Serbest bırakılmış ama başlamamış işi düzenleme için taslağa geri çeker."""
    check(actor, "workorder.release")
    if not reason:
        raise BusinessRuleError("Geri çekme gerekçesi zorunludur.", field="reason")
    wo = _lock(wo)
    if wo.status != WorkOrderStatus.PLANNED or has_movements(wo):
        raise BusinessRuleError("Yalnız başlamamış ve hareketi olmayan serbest iş emri geri çekilebilir.")
    wo.status = WorkOrderStatus.DRAFT
    wo.save()
    n = invalidate_instructions(wo, InstructionStatus.CANCELLED, f"İş emri geri çekildi: {reason}")
    audit(actor, "workorder.withdraw", wo, {"status": (WorkOrderStatus.PLANNED, WorkOrderStatus.DRAFT),
                                            "gecersiz_talimat": (None, n)}, reason=reason)
    return wo


@transaction.atomic
def update_draft(actor, wo, *, target_quantity, planned_start, notes, foreman, machines, sync_revision=False, reason=""):
    """Taslak iş emrini düzenler. Operasyonlar yeniden kurulur; revizyon yalnız açıkça istenirse kalemin onaylı
    revizyonuna eşitlenir (sessiz değişiklik yok)."""
    from .services import _validate_machines

    check(actor, "workorder.create")
    wo = _lock(wo)
    if wo.status != WorkOrderStatus.DRAFT:
        raise BusinessRuleError("Yalnız taslak iş emri düzenlenebilir; serbest iş emrini önce geri çekin.")
    if target_quantity is None or target_quantity <= 0:
        raise BusinessRuleError("Hedef miktar sıfırdan büyük olmalı.", field="target_quantity")
    _validate_machines(machines)
    data = {"target_quantity": target_quantity, "planned_start": planned_start, "notes": notes,
            "assigned_foreman": foreman}
    if sync_revision and wo.order_line and wo.order_line.approved_revision_id:
        data["recipe_revision"] = wo.order_line.approved_revision
    changes = diff(wo, data)
    for f, (_, new) in changes.items():
        setattr(wo, f, new)
    if "assigned_foreman" in changes:
        wo.assigned_at = timezone.now()
    wo.save()
    old_ops = [op.machine.code for op in wo.operations.order_by("seq")]
    new_ops = [m.code for m in machines]
    if old_ops != new_ops:
        if WorkInstruction.objects.filter(operation__work_order=wo).exists():
            raise BusinessRuleError("Bu iş emri için talimat basılmış; rota (operasyon listesi) değiştirilemez. Makine "
                                    "değişikliğini operasyon planından yapın veya iş emrini iptal edip yenisini açın.")
        wo.operations.all().delete()  # taslak ve hareketsiz: operasyon satırları güvenle yeniden kurulur
        for seq, machine in enumerate(machines, start=1):
            WorkOrderOperation.objects.create(work_order=wo, seq=seq, machine=machine, machine_type=machine.machine_type)
        changes["operasyonlar"] = (old_ops, new_ops)
    if changes:
        audit(actor, "workorder.update", wo, changes, reason=reason)
    return wo


@transaction.atomic
def cancel(actor, wo, reason):
    """İptal. Başlamamış iş doğrudan; başlamış işte açık malzeme ve sahipsiz bobin bırakılmaz."""
    from .services import issued_by_lot

    check(actor, "workorder.cancel")
    if not reason:
        raise BusinessRuleError("İptal gerekçesi zorunludur.", field="reason")
    wo = _lock(wo)
    if wo.status not in OPEN_STATUSES:
        raise BusinessRuleError("Bu iş emri iptal edilemez.")
    problems = []
    if wo.operations.filter(status=OperationStatus.RUNNING).exists():
        problems.append("devam eden operasyon var (önce durdurun veya tamamlayın)")
    open_material = {lot.code: qty for lot, qty in issued_by_lot(wo).items() if qty > 0}
    if open_material:
        problems.append("makineye verilen malzeme iade edilmedi: " +
                        ", ".join(f"{c} {num(q)}" for c, q in open_material.items()))
    loose = wo.coils.filter(status__in=[CoilStatus.AVAILABLE, CoilStatus.PARTIAL]).exclude(
        quality_status=QualityStatus.BLOCKED)
    if loose.exists():
        problems.append("palete alınmamış/blokelenmemiş bobin var: " + ", ".join(loose.values_list("code", flat=True)))
    if problems:
        raise BusinessRuleError("İptal edilemez: " + "; ".join(problems) + ".")
    old = wo.status
    wo.status, wo.cancel_reason = WorkOrderStatus.CANCELLED, reason
    wo.save()
    invalidate_instructions(wo, InstructionStatus.CANCELLED, f"İş emri iptal: {reason}")
    audit(actor, "workorder.cancel", wo, {"status": (old, wo.status)}, reason=reason)
    return wo


@transaction.atomic
def archive(actor, wo):
    check(actor, "workorder.create")
    wo = _lock(wo)
    if wo.status not in (WorkOrderStatus.CLOSED, WorkOrderStatus.CANCELLED):
        raise BusinessRuleError("Yalnız kapanmış veya iptal edilmiş iş emri arşivlenir.")
    wo.archived_at = timezone.now()
    wo.save()
    audit(actor, "workorder.archive", wo, {"archived": (None, True)})
    return wo


# --- Ustabaşı planlaması -------------------------------------------------------------

def machine_queue(machine, planned_date=None, shift=None, exclude=None):
    qs = WorkOrderOperation.objects.filter(machine=machine, work_order__status__in=OPEN_STATUSES).exclude(
        status=OperationStatus.DONE).select_related("work_order", "shift")
    if planned_date:
        qs = qs.filter(planned_date=planned_date)
    if shift:
        qs = qs.filter(shift=shift)
    if exclude:
        qs = qs.exclude(pk=exclude.pk)
    return qs.order_by("planned_date", "shift__start_time", "queue_position", "pk")


@transaction.atomic
def schedule_operation(actor, op, *, planned_date, shift, machine, queue_position, reason=""):
    """Ustabaşı operasyonu tarih, vardiya, makine ve çalışma sırasına dağıtır (planlama kuyruğu; fiili çalışma
    durumu ayrı tutulur). Aynı makine/tarih/vardiyadaki diğer işler etkilenen kayıt olarak döner."""
    op = WorkOrderOperation.objects.select_for_update(of=("self",)).select_related("work_order").get(pk=op.pk)
    check(actor, "workorder.schedule", op)
    if op.status != OperationStatus.WAITING:
        raise BusinessRuleError("Başlamış operasyonun makinesi/planı değiştirilemez.")
    if op.work_order.status not in OPEN_STATUSES:
        raise BusinessRuleError("Kapanmış/iptal iş emrinde planlama yapılamaz.")
    if machine.machine_type != op.machine_type or not machine.is_active:
        raise BusinessRuleError(f"Bu operasyon için aktif bir {op.get_machine_type_display()} makinesi seçilmeli.",
                                field="machine")
    data = {"planned_date": planned_date, "shift": shift, "machine": machine, "queue_position": queue_position}
    changes = diff(op, data)
    for f, (_, new) in changes.items():
        setattr(op, f, new)
    op.save()
    affected = list(machine_queue(machine, planned_date, shift, exclude=op))
    if changes:
        op.instructions.filter(status=InstructionStatus.CURRENT).update(
            status=InstructionStatus.SUPERSEDED, status_reason="Makine/plan değişti", status_changed_at=timezone.now())
        audit(actor, "operation.schedule", op, {k: (str(v[0]) if v[0] else None, str(v[1]) if v[1] else None)
                                                for k, v in changes.items()},
              reason=reason or (f"Aynı makine/vardiyada {len(affected)} başka iş" if affected else ""))
    return op, affected


def machine_busy_with(machine, exclude_op=None):
    """Makinede fiilen çalışan (devam eden) başka operasyon."""
    qs = WorkOrderOperation.objects.filter(machine=machine, status=OperationStatus.RUNNING).select_related("work_order")
    if exclude_op is not None:
        qs = qs.exclude(pk=exclude_op.pk)
    return qs.first()


def lock_machine_free(op):
    """Tek aktif iş kuralı: makine satırı kilitlenir; başka devam eden iş varsa ret (bulgu 7)."""
    Machine.objects.select_for_update().get(pk=op.machine_id)
    other = machine_busy_with(op.machine, exclude_op=op)
    if other:
        raise BusinessRuleError(f"{op.machine.code} makinesinde {other.work_order.code} ({other.seq}. operasyon) "
                                "devam ediyor. Önce onu durdurun veya tamamlayın.")


# --- Duruş / devam / vardiya devri ---------------------------------------------------

@transaction.atomic
def stop_operation(actor, op, reason, note=""):
    op = WorkOrderOperation.objects.select_for_update(of=("self",)).select_related("work_order").get(pk=op.pk)
    check(actor, "production.hold", op)
    if op.status != OperationStatus.RUNNING:
        raise BusinessRuleError("Yalnız devam eden operasyon durdurulabilir.")
    if not reason:
        raise BusinessRuleError("Duruş nedeni seçilmeli.", field="reason")
    op.status = OperationStatus.STOPPED
    op.save()
    stop = OperationStop.objects.create(operation=op, reason=reason, note=note, started_by=actor)
    audit(actor, "operation.stop", op, {"status": (OperationStatus.RUNNING, op.status)},
          reason=f"{stop.get_reason_display()}: {note}".strip(": "))
    op.work_order.save(update_fields=["updated_at"])
    return stop


@transaction.atomic
def resume_operation(actor, op):
    op = WorkOrderOperation.objects.select_for_update(of=("self",)).select_related("work_order", "machine").get(pk=op.pk)
    check(actor, "production.hold", op)
    if op.status != OperationStatus.STOPPED:
        raise BusinessRuleError("Yalnız duruştaki operasyon devam ettirilebilir.")
    lock_machine_free(op)
    op.status = OperationStatus.RUNNING
    op.save()
    stop = op.stops.filter(ended_at__isnull=True).first()
    if stop:
        stop.ended_at, stop.ended_by = timezone.now(), actor
        stop.save()
    audit(actor, "operation.resume", op, {"status": (OperationStatus.STOPPED, op.status)})
    return op


def handover_snapshot(op):
    wo = op.work_order
    coils = []
    for c in wo.coils.filter(remaining_kg__gt=0, status__in=[CoilStatus.AVAILABLE, CoilStatus.PARTIAL]).order_by("code"):
        coils.append({"code": c.code, "op": c.operation.seq, "remaining_kg": str(c.remaining_kg),
                      "quality": c.quality_status})
    return coils


@transaction.atomic
def create_handover(actor, op, *, to_shift, remaining_note, machine_side_material="", notes=""):
    op = WorkOrderOperation.objects.select_for_update(of=("self",)).select_related("work_order", "shift").get(pk=op.pk)
    check(actor, "production.hold", op)
    if op.status not in (OperationStatus.RUNNING, OperationStatus.STOPPED):
        raise BusinessRuleError("Devir yalnız devam eden veya duruştaki operasyonda yapılır.")
    if not remaining_note:
        raise BusinessRuleError("Kalan işi yazın (ör. '2 bobin daha, ~140 kg').", field="remaining_note")
    h = ShiftHandover.objects.create(operation=op, from_shift=actor.shift or op.shift, to_shift=to_shift,
                                     remaining_note=remaining_note, open_coils=handover_snapshot(op),
                                     machine_side_material=machine_side_material, notes=notes, created_by=actor)
    audit(actor, "operation.handover", op, {"devir": (str(h.from_shift or "—"), str(to_shift))}, reason=remaining_note)
    return h


@transaction.atomic
def acknowledge_handover(actor, handover):
    h = ShiftHandover.objects.select_for_update(of=("self",)).select_related("operation").get(pk=handover.pk)
    check(actor, "production.hold", h.operation)
    if h.acknowledged_at:
        raise BusinessRuleError("Devir zaten teslim alınmış.")
    if h.created_by_id == actor.pk:
        raise BusinessRuleError("Devri yapan kişi kendi devrini teslim alamaz.")
    h.acknowledged_by, h.acknowledged_at = actor, timezone.now()
    h.save()
    audit(actor, "operation.handover_ack", h.operation, {"teslim": (None, actor.personnel_no)})
    return h


def remaining_target(wo):
    """Kalan hedef (kutu birimli işte palete alınan kutuya göre; diğer birimlerde hesaplanmaz)."""
    from catalog.models import Unit
    if wo.unit != Unit.BOX:
        return None
    done = sum(m.quantity for m in wo.pallet_movements.filter(kind="uretim_girisi"))
    return max(wo.target_quantity - Decimal(done), Decimal("0"))
