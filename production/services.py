from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.permissions import check
from catalog.models import LotMovementKind, MachineType, MaterialUnit, RecipeRevision
from catalog.services import move_lot
from core.models import QualityStatus, ValueSource
from core.exceptions import BusinessRuleError
from core.services import audit, next_code
from orders.models import LineStatus, OrderLine
from orders.services import set_line_status

from .models import (Coil, CoilLink, CoilStatus, MaterialDeviation, OperationStatus, RouteException, WorkOrder,
                     WorkOrderClosure, WorkOrderOperation, WorkOrderPurpose, WorkOrderStatus)


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
                      planned_start=None, notes="", foreman=None):
    """Siparişe bağlı (order_line) veya stok için (product + revision) iş emri açar.
    Reçete revizyonu iş emrine sabitlenir; sonradan açılan revizyonlar bu iş emrini etkilemez."""
    check(actor, "workorder.create")
    if target_quantity is None or target_quantity <= 0:
        raise BusinessRuleError("Hedef miktar sıfırdan büyük olmalı.", field="target_quantity")
    if order_line is not None:
        order_line = OrderLine.objects.select_for_update(of=("self",)).select_related("order", "product").get(pk=order_line.pk)
        if order_line.status not in (LineStatus.APPROVED, LineStatus.IN_PRODUCTION, LineStatus.PRODUCED_SHORT):
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
                                  planned_start=planned_start, notes=notes, created_by=actor,
                                  assigned_foreman=foreman, assigned_at=timezone.now() if foreman else None)
    for seq, machine in enumerate(machines, start=1):
        WorkOrderOperation.objects.create(work_order=wo, seq=seq, machine=machine, machine_type=machine.machine_type)
    audit(actor, "workorder.create", wo, {
        "purpose": (None, purpose), "order_line": (None, order_line.code if order_line else None),
        "product": (None, product.code), "recipe_revision": (None, str(revision)),
        "target_quantity": (None, target_quantity), "operations": (None, [m.code for m in machines]),
        "assigned_foreman": (None, foreman.personnel_no if foreman else None),
    })
    return wo


def _lock_wo(wo):
    return WorkOrder.objects.select_for_update(of=("self",)).select_related("order_line").get(pk=wo.pk)


@transaction.atomic
def start_work_order(actor, wo):
    check(actor, "production.execute", wo)
    wo = _lock_wo(wo)
    if wo.status == WorkOrderStatus.DRAFT:
        raise BusinessRuleError("İş emri taslak; planlama serbest bırakmadan üretim başlatılamaz.")
    if wo.status != WorkOrderStatus.PLANNED:
        raise BusinessRuleError(f"İş emri zaten '{wo.get_status_display()}' durumunda.")
    from .lifecycle import revision_conflict
    conflict = revision_conflict(wo)
    if conflict:
        raise BusinessRuleError(conflict)
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
    from .lifecycle import lock_machine_free
    op = WorkOrderOperation.objects.select_for_update().select_related("machine").get(pk=op.pk)
    if op.status != OperationStatus.WAITING:
        raise BusinessRuleError("Operasyon zaten başlatılmış.")
    lock_machine_free(op)
    op.status = OperationStatus.RUNNING
    op.started_at = timezone.now()
    op.started_by = actor
    op.save()
    audit(actor, "operation.start", op, {"status": (OperationStatus.WAITING, op.status), "machine": (None, op.machine.code)})
    return op


@transaction.atomic
def start_operation(actor, op):
    check(actor, "production.execute", op)
    wo = _lock_wo(op.work_order)
    if wo.status != WorkOrderStatus.STARTED:
        raise BusinessRuleError("Önce iş emri üretimi başlatılmalı.")
    prev = wo.operations.filter(seq__lt=op.seq).order_by("-seq").first()
    if prev and prev.status == OperationStatus.WAITING:
        raise BusinessRuleError(f"Önce {prev.seq}. operasyon başlatılmalı.")
    return _start_op(actor, op)


@transaction.atomic
def complete_operation(actor, op):
    check(actor, "production.execute", op)
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


# --- Malzeme çıkışı / iadesi --------------------------------------------------------

def _require_started(wo):
    if wo.status != WorkOrderStatus.STARTED:
        raise BusinessRuleError("Bu işlem yalnız üretimdeki iş emrinde yapılabilir.")


def recipe_material_ids(wo):
    return set(wo.recipe_revision.components.values_list("material_id", flat=True))


@transaction.atomic
def issue_material(actor, wo, lot, quantity, deviation_reason=""):
    """Üretime malzeme çıkışı. Reçete dışı malzeme yalnız material.deviation yetkisi ve gerekçeyle (bulgu 9)."""
    check(actor, "production.execute", wo)
    wo = _lock_wo(wo)
    _require_started(wo)
    if quantity <= 0:
        raise BusinessRuleError("Miktar sıfırdan büyük olmalı.", field="quantity")
    off_recipe = lot.material_id not in recipe_material_ids(wo)
    if off_recipe:
        if not deviation_reason:
            raise BusinessRuleError(f"{lot.material.code} bu iş emrinin reçetesinde yok (Rev.{wo.recipe_revision.rev_no}). "
                                    "Reçete dışı çıkış yalnız yetkili sapma kaydı ve gerekçeyle yapılır.",
                                    field="deviation_reason")
        check(actor, "material.deviation", wo)
    mv = move_lot(actor, lot, LotMovementKind.ISSUE, -quantity, work_order=wo,
                  reason=f"Reçete dışı: {deviation_reason}" if off_recipe else "")
    if off_recipe:
        MaterialDeviation.objects.create(work_order=wo, lot_movement=mv, material=lot.material,
                                         reason=deviation_reason, approved_by=actor)
        audit(actor, "workorder.material_deviation", wo,
              {"malzeme": (None, f"{lot.material.code} {quantity} {lot.material.unit}")}, reason=deviation_reason)
    return mv


def issued_by_lot(wo):
    """{lot: net verilen miktar} (çıkış - iade)."""
    result = {}
    for mv in wo.lot_movements.select_related("lot__material"):
        result[mv.lot] = result.get(mv.lot, Decimal("0")) - mv.quantity
    return result


@transaction.atomic
def return_material(actor, wo, lot, quantity):
    """Makine başında kalan malzemenin lota iadesi; bu iş emrine verilen miktarı aşamaz."""
    check(actor, "production.execute", wo)
    wo = _lock_wo(wo)
    _require_started(wo)
    if quantity <= 0:
        raise BusinessRuleError("Miktar sıfırdan büyük olmalı.", field="quantity")
    net = issued_by_lot(wo).get(lot, Decimal("0"))
    if quantity > net:
        raise BusinessRuleError(f"Bu iş emrine {lot.code} lotundan net {net} kg verilmiş; daha fazlası iade edilemez.",
                                field="quantity")
    return move_lot(actor, lot, LotMovementKind.RETURN, quantity, work_order=wo)


# --- Bobinler -------------------------------------------------------------------------

@transaction.atomic
def record_coil(actor, op, *, weight_kg, length_m=None, weight_source=ValueSource.MEASURED, sources=(), notes=""):
    """Operasyon çıktısı bobin kaydı.

    Extruder bobini kaynaksızdır (malzeme iş emrine verilir). Sonraki operasyonlarda en az bir kaynak bobin
    gerekir: sources=[(Coil, tüketilen_kg)]. Kaynak kısmen tüketilebilir; kalan miktar negatife düşemez.
    """
    check(actor, "production.execute", op)
    wo = _lock_wo(op.work_order)
    _require_started(wo)
    op = WorkOrderOperation.objects.select_for_update().select_related("machine").get(pk=op.pk)
    if op.status != OperationStatus.RUNNING:
        raise BusinessRuleError("Bobin yalnız devam eden operasyona kaydedilebilir.")
    if weight_kg is None or weight_kg <= 0:
        raise BusinessRuleError("Bobin ağırlığı sıfırdan büyük olmalı.", field="weight_kg")
    sources = [(c, q) for c, q in sources if c is not None]
    if op.machine_type == MachineType.EXTRUDER:
        if sources:
            raise BusinessRuleError("Extruder bobini kaynak bobinden üretilmez.")
    elif not sources:
        raise BusinessRuleError("Dönüştürme/bölme operasyonunda kaynak bobin seçilmelidir.", field="source_1")
    seen = set()
    for coil, _ in sources:
        if coil.pk in seen:
            raise BusinessRuleError(f"Aynı kaynak bobin ({coil.code}) birden fazla satırda seçilemez; "
                                    "tüketimi tek satırda toplam olarak girin.", field="source_1")
        seen.add(coil.pk)
    # Kaynaklar tek sorguda, pk sırasıyla kilitlenir; kontroller kilitli (güncel) kalan miktar üzerinden yapılır.
    current = {c.pk: c for c in Coil.objects.select_for_update().filter(pk__in=seen).order_by("pk")}
    locked = []
    for coil, qty in sources:
        coil = current[coil.pk]
        if coil.work_order_id != wo.pk:
            raise BusinessRuleError(f"{coil.code} bu iş emrine ait değil.")
        if coil.operation.seq >= op.seq:
            raise BusinessRuleError(f"{coil.code} önceki bir operasyonun çıktısı olmalı.")
        if coil.operation.seq != op.seq - 1 and not op.route_exceptions.exists():
            raise BusinessRuleError(f"{coil.code} {coil.operation.seq}. operasyonun çıktısı; bu operasyonun kaynağı bir "
                                    f"önceki ({op.seq - 1}.) operasyonun bobini olmalı. Rota atlama yalnız yetkili "
                                    "gerekçeli izinle yapılır.")
        if coil.quality_status == QualityStatus.BLOCKED:
            raise BusinessRuleError(f"{coil.code} blokeli; kullanılamaz.")
        if qty is None or qty <= 0:
            raise BusinessRuleError("Tüketilen miktar sıfırdan büyük olmalı.")
        if qty > coil.remaining_kg:
            raise BusinessRuleError(f"{coil.code} kalan miktarı {coil.remaining_kg} kg; {qty} kg tüketilemez.")
        locked.append((coil, qty))
    consumed_total = sum((q for _, q in locked), Decimal("0"))
    if locked and weight_kg > consumed_total:
        raise BusinessRuleError(f"Çıktı ağırlığı ({weight_kg} kg) tüketilen kaynak toplamını ({consumed_total} kg) aşamaz.",
                                field="weight_kg")
    coil = Coil.objects.create(code=next_code("BOB"), work_order=wo, operation=op, machine=op.machine,
                               weight_kg=weight_kg, remaining_kg=weight_kg, length_m=length_m,
                               weight_source=weight_source, notes=notes, created_by=actor)
    for src, qty in locked:
        old = src.remaining_kg
        src.remaining_kg -= qty
        src.status = CoilStatus.CONSUMED if src.remaining_kg == 0 else CoilStatus.PARTIAL
        src.save()
        CoilLink.objects.create(source=src, output=coil, consumed_kg=qty)
        audit(actor, "coil.consume", src, {"remaining_kg": (old, src.remaining_kg)}, reason=f"Çıktı: {coil.code}")
    audit(actor, "coil.create", coil, {"weight_kg": (None, weight_kg), "machine": (None, op.machine.code),
                                       "sources": (None, [f"{c.code}:{q}" for c, q in locked])})
    wo.save(update_fields=["updated_at"])
    return coil


@transaction.atomic
def allow_route_skip(actor, op, reason):
    """Operasyonun bir önceki operasyonu atlayarak daha önceki bobini kaynak almasına izin (bulgu 6)."""
    check(actor, "exception.approve")
    if not reason:
        raise BusinessRuleError("Rota atlama için gerekçe zorunludur.", field="reason")
    op = WorkOrderOperation.objects.select_for_update(of=("self",)).select_related("work_order").get(pk=op.pk)
    exc = RouteException.objects.create(operation=op, reason=reason, approved_by=actor)
    audit(actor, "operation.route_exception", op, {"rota_atlama": (None, f"{op.seq}. operasyon")}, reason=reason)
    return exc


@transaction.atomic
def set_coil_quality(actor, coil, status, reason):
    from warehouse.services import quality_permission
    if not reason:
        raise BusinessRuleError("Kalite değişikliği için gerekçe zorunludur.", field="reason")
    coil = Coil.objects.select_for_update().get(pk=coil.pk)
    check(actor, quality_permission(coil.quality_status, status), coil)
    if coil.quality_status != status:
        old = coil.quality_status
        coil.quality_status = status
        coil.save()
        audit(actor, "coil.quality", coil, {"quality_status": (old, status)}, reason=reason)
        if QualityStatus.BLOCKED in (old, status):
            # Bağlı paletlerin sevki sevk anında bobin durumundan engellenir; burada paletler kilitlenip
            # gerekçe geçmişe yazılır. Palet kalite alanına dokunulmaz.
            from warehouse.services import record_coil_hold_change
            record_coil_hold_change(actor, coil, old, status, reason)
    return coil


def final_operation(wo):
    return wo.operations.order_by("-seq").first()


# --- Kapanış ------------------------------------------------------------------------

def closure_summary(wo):
    """Kapanış ekranı ve doğrulama için hesaplanan değerler (kg)."""
    last = final_operation(wo)
    zero = Decimal("0")
    by_lot = issued_by_lot(wo)
    # Denge yalnız kg birimli malzemeyle kurulur; litre/adet ayrı listelenir, kg'ye çevrilmez (bulgu 8).
    issued = sum((q for lot, q in by_lot.items() if lot.material.unit == MaterialUnit.KG), zero)
    other_units = {}
    for lot, q in by_lot.items():
        if lot.material.unit != MaterialUnit.KG and q:
            other_units[lot.material.unit] = other_units.get(lot.material.unit, zero) + q
    final_coils = list(wo.coils.filter(operation=last)) if last else []
    good = sum((c.weight_kg for c in final_coils if c.quality_status != QualityStatus.BLOCKED), zero)
    blocked_intermediate = sum((c.remaining_kg for c in wo.coils.exclude(operation=last)
                                if c.quality_status == QualityStatus.BLOCKED), zero)
    blocked = sum((c.weight_kg for c in final_coils if c.quality_status == QualityStatus.BLOCKED), zero) + blocked_intermediate
    wip = sum((c.remaining_kg for c in wo.coils.exclude(operation=last)
               if c.quality_status != QualityStatus.BLOCKED), zero)
    boxes = sum(m.quantity for m in wo.pallet_movements.filter(kind__in=["uretim_girisi", "duzeltme"]))
    return {"issued": issued, "good": good, "blocked": blocked, "wip": wip, "boxes": boxes,
            "other_units": other_units,
            "deviations": list(wo.material_deviations.select_related("material", "approved_by", "lot_movement__lot")),
            "tolerance_pct": Decimal(str(settings.WORK_ORDER_BALANCE_TOLERANCE_PCT))}


@transaction.atomic
def close_work_order(actor, wo, *, scrap_kg, remaining_confirmed, reason=""):
    """Kapanış: sağlam çıktı, fire ve kalan malzeme doğrulanır.

    Denge: net verilen malzeme ≈ sağlam + blokeli + ara stok + fire. Fark toleransı aşarsa gerekçe zorunlu.
    """
    check(actor, "production.execute", wo)
    wo = _lock_wo(wo)
    _require_started(wo)
    if wo.operations.exclude(status=OperationStatus.DONE).exists():
        raise BusinessRuleError("Tüm operasyonlar tamamlanmadan iş emri kapatılamaz.")
    if not remaining_confirmed:
        raise BusinessRuleError("Kalan malzemenin iade edildiği / kalmadığı onaylanmalıdır.", field="remaining_confirmed")
    if scrap_kg is None or scrap_kg < 0:
        raise BusinessRuleError("Fire miktarı girilmelidir (fire yoksa 0).", field="scrap_kg")
    s = closure_summary(wo)
    if s["good"] <= 0:
        raise BusinessRuleError("Sağlam çıktı yok; son operasyonda bobin kaydı bulunmuyor.")
    accounted = s["good"] + s["blocked"] + s["wip"] + scrap_kg
    difference = s["issued"] - accounted
    limit = s["issued"] * s["tolerance_pct"] / 100
    if s["issued"] <= 0 and not reason:
        raise BusinessRuleError("Bu iş emrine malzeme çıkışı kaydedilmemiş; kapanış için gerekçe zorunludur.", field="reason")
    if abs(difference) > limit and not reason:
        raise BusinessRuleError(
            f"Malzeme dengesi tutmuyor: verilen {s['issued']} kg, hesaplanan {accounted} kg (fark {difference} kg, "
            f"tolerans ±%{s['tolerance_pct']}). Gerekçe yazın veya değerleri düzeltin.", field="reason")
    WorkOrderClosure.objects.create(work_order=wo, issued_kg=s["issued"], good_kg=s["good"], blocked_kg=s["blocked"],
                                    wip_kg=s["wip"], scrap_kg=scrap_kg, difference_kg=difference, good_boxes=s["boxes"],
                                    reason=reason, closed_by=actor)
    wo.status = WorkOrderStatus.CLOSED
    wo.closed_at = timezone.now()
    wo.save()
    audit(actor, "workorder.close", wo, {"status": (WorkOrderStatus.STARTED, wo.status), "issued_kg": (None, s["issued"]),
                                         "good_kg": (None, s["good"]), "scrap_kg": (None, scrap_kg),
                                         "difference_kg": (None, difference)}, reason=reason)
    if wo.order_line_id and not wo.order_line.work_orders.exclude(
            status__in=[WorkOrderStatus.CLOSED, WorkOrderStatus.CANCELLED]).exists():
        if wo.order_line.status == LineStatus.IN_PRODUCTION:
            # Kapanış karşılama demek değildir (bulgu 3): üretilen miktar kalem miktarına ulaşmadıysa "eksik".
            from .selectors import produced_quantity_for_line
            line = wo.order_line
            produced = produced_quantity_for_line(line)["quantity"]
            if produced is not None and produced >= line.quantity:
                set_line_status(actor, line, LineStatus.PRODUCED)
            else:
                shown = "ölçülemiyor" if produced is None else f"{produced} {line.get_unit_display()}"
                set_line_status(actor, line, LineStatus.PRODUCED_SHORT,
                                reason=f"İş emirleri kapandı; üretilen {shown}, sipariş {line.quantity}")
    return wo
