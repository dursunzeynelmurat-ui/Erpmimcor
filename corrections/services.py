"""Düzeltme talebi ve onayı.

Kurallar:
- Talep açmak hiçbir kaydı veya stoğu değiştirmez; yalnız talep ve etkilenen kayıt listesi yazılır.
- Onayda hedef kilitlenir, güncel değer talepteki eski değerle yeniden karşılaştırılır; farklıysa talep
  "kayıt değişmiş" gerekçesiyle reddedilir (sessizce üzerine yazılmaz).
- Uygulama geçmişi koruyan hareketle yapılır: lot için ters/ek çıkış-iade hareketi, palet için düzeltme
  hareketi, tartım için yeni tartım kaydı, bobin için kalan miktarı tüketimin altına düşürmeyen güncelleme +
  işlem geçmişi. Orijinal hareket silinmez/değiştirilmez.
- Kendi talebini onaylama varsayılan kapalı (CORRECTION_SELF_APPROVAL).
"""
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.permissions import check
from catalog.models import LotMovement, LotMovementKind
from catalog.services import move_lot
from core.exceptions import BusinessRuleError
from core.services import audit, next_code, num
from production.models import Coil, CoilStatus, WorkOrderStatus
from warehouse.models import MovementKind, Pallet, PalletMovement, PalletWeighing, ReservationStatus

from .models import CorrectionKind, CorrectionRequest, CorrectionStatus

TARGETS = {
    CorrectionKind.COIL_WEIGHT: Coil,
    CorrectionKind.MATERIAL_ISSUE: LotMovement,
    CorrectionKind.MATERIAL_RETURN: LotMovement,
    CorrectionKind.PALLET_BOXES: PalletMovement,
    CorrectionKind.WEIGHING: PalletWeighing,
    CorrectionKind.COUNT: Pallet,
}
DISCRETE = (CorrectionKind.PALLET_BOXES, CorrectionKind.COUNT)


def _applied_delta(kind, target):
    rows = CorrectionRequest.objects.filter(kind=kind, target_model=target._meta.label_lower, target_id=target.pk,
                                            status=CorrectionStatus.APPLIED).values_list("old_value", "new_value")
    return sum((new - old for old, new in rows), Decimal("0"))


def current_value(kind, target):
    """Hedefin bugünkü (önceki onaylı düzeltmeler dahil) değeri."""
    if kind == CorrectionKind.COIL_WEIGHT:
        return target.weight_kg
    if kind == CorrectionKind.MATERIAL_ISSUE:
        return -target.quantity + _applied_delta(kind, target)
    if kind == CorrectionKind.MATERIAL_RETURN:
        return target.quantity + _applied_delta(kind, target)
    if kind == CorrectionKind.PALLET_BOXES:
        return Decimal(target.quantity) + _applied_delta(kind, target)
    if kind == CorrectionKind.WEIGHING:
        return target.gross_kg
    return Decimal(target.box_count)


def describe(kind, target):
    """(kayıt kodu, birim, müşteri, iş emri, palet)"""
    if kind == CorrectionKind.COIL_WEIGHT:
        return target.code, "kg", target.work_order.product.customer, target.work_order, None
    if kind in (CorrectionKind.MATERIAL_ISSUE, CorrectionKind.MATERIAL_RETURN):
        wo = target.work_order
        return (f"{target.lot.code} / {wo.code if wo else '—'} #{target.pk}", target.lot.material.unit,
                wo.product.customer if wo else None, wo, None)
    if kind == CorrectionKind.PALLET_BOXES:
        return (f"{target.pallet.code} üretim girişi #{target.pk}", "kutu", target.pallet.product.customer,
                target.work_order, target.pallet)
    if kind == CorrectionKind.WEIGHING:
        return (f"{target.pallet.code} tartım #{target.pk}", "kg", target.pallet.product.customer,
                target.pallet.work_order, target.pallet)
    return target.code, "kutu", target.product.customer, target.work_order, target


def affected_records(kind, target):
    out = []
    if kind == CorrectionKind.COIL_WEIGHT:
        link = getattr(target, "pallet_link", None) if hasattr(target, "pallet_link") else None
        if link is not None:
            out.append(f"Palet {link.pallet.code} (bobin kayıt ağırlığı; palet tartımı ayrıca değişmez)")
        for link in target.output_links.select_related("output"):
            out.append(f"Bu bobinden üretilen {link.output.code} ({num(link.consumed_kg)} kg tüketim)")
        wo = target.work_order
    elif kind in (CorrectionKind.MATERIAL_ISSUE, CorrectionKind.MATERIAL_RETURN):
        out.append(f"Lot {target.lot.code} bakiyesi")
        wo = target.work_order
    else:
        pallet = target if kind == CorrectionKind.COUNT else target.pallet
        for r in pallet.reservations.filter(status=ReservationStatus.ACTIVE).select_related("order_line__order"):
            out.append(f"Rezervasyon {r.order_line.code}: {r.open_quantity} kutu")
        wo = pallet.work_order
    if wo is not None:
        out.append(f"İş emri {wo.code} ({wo.get_status_display()})")
        if wo.status == WorkOrderStatus.CLOSED:
            out.append(f"{wo.code} kapanış dengesi kapanış anındaki değerlerle kayıtlı kalır; fark geçmişte görünür")
    return out


def _target(kind, target_id, lock=False):
    model = TARGETS[kind]
    qs = model.objects.select_for_update(of=("self",)) if lock else model.objects
    obj = qs.get(pk=target_id)
    if kind == CorrectionKind.MATERIAL_ISSUE and obj.kind != LotMovementKind.ISSUE:
        raise BusinessRuleError("Seçilen hareket bir üretime çıkış hareketi değil.")
    if kind == CorrectionKind.MATERIAL_RETURN and obj.kind != LotMovementKind.RETURN:
        raise BusinessRuleError("Seçilen hareket bir iade hareketi değil.")
    if kind == CorrectionKind.PALLET_BOXES and obj.kind != MovementKind.PRODUCTION:
        raise BusinessRuleError("Seçilen hareket bir üretimden giriş hareketi değil.")
    if kind == CorrectionKind.WEIGHING and not obj.is_current:
        raise BusinessRuleError("Yalnız paletin güncel tartımı düzeltilebilir.")
    return obj


def _validate_value(kind, value):
    if value is None or value < 0:
        raise BusinessRuleError("Yeni değer negatif olamaz.", field="new_value")
    if kind in DISCRETE and value != value.to_integral_value():
        raise BusinessRuleError("Kutu adedi tam sayı olmalı.", field="new_value")
    if kind in (CorrectionKind.COIL_WEIGHT, CorrectionKind.WEIGHING) and value <= 0:
        raise BusinessRuleError("Ağırlık sıfırdan büyük olmalı.", field="new_value")


@transaction.atomic
def request_correction(actor, kind, target, new_value, reason):
    if kind not in TARGETS:
        raise BusinessRuleError("Geçersiz düzeltme türü.")
    target = _target(kind, target.pk)
    check(actor, "correction.request", target)
    if not reason:
        raise BusinessRuleError("Düzeltme gerekçesi zorunludur.", field="reason")
    new_value = Decimal(new_value)
    _validate_value(kind, new_value)
    old = current_value(kind, target)
    if new_value == old:
        raise BusinessRuleError("Yeni değer mevcut değerle aynı.", field="new_value")
    label = target._meta.label_lower
    if CorrectionRequest.objects.filter(target_model=label, target_id=target.pk, status=CorrectionStatus.REQUESTED).exists():
        raise BusinessRuleError("Bu kayıt için onay bekleyen bir düzeltme talebi zaten var.")
    code, unit, customer, wo, pallet = describe(kind, target)
    cr = CorrectionRequest.objects.create(
        code=next_code("DZT"), kind=kind, target_model=label, target_id=target.pk, target_code=code, customer=customer,
        work_order=wo, pallet=pallet, old_value=old, new_value=new_value, unit=unit, reason=reason,
        affected=affected_records(kind, target), requested_by=actor)
    audit(actor, "correction.request", cr, {"deger": (num(old), num(new_value))}, reason=reason)
    return cr


def _lock_request(cr):
    cr = CorrectionRequest.objects.select_for_update().get(pk=cr.pk)
    if cr.status != CorrectionStatus.REQUESTED:
        raise BusinessRuleError(f"{cr.code} artık onay beklemiyor ({cr.get_status_display()}).")
    return cr


@transaction.atomic
def reject(actor, cr, reason):
    cr = _lock_request(cr)
    check(actor, "correction.approve", cr)
    if not reason:
        raise BusinessRuleError("Ret gerekçesi zorunludur.", field="reason")
    _decide(actor, cr, CorrectionStatus.REJECTED, reason)
    return cr


@transaction.atomic
def withdraw(actor, cr, reason=""):
    cr = _lock_request(cr)
    if cr.requested_by_id != actor.pk:
        raise BusinessRuleError("Talebi yalnız açan kişi geri çekebilir.")
    _decide(actor, cr, CorrectionStatus.WITHDRAWN, reason or "Talep eden geri çekti")
    return cr


def _decide(actor, cr, status, reason):
    old = cr.status
    cr.status, cr.decided_by, cr.decided_at, cr.decision_reason = status, actor, timezone.now(), reason
    cr.save()
    audit(actor, f"correction.{status}", cr, {"status": (old, status)}, reason=reason)


@transaction.atomic
def approve(actor, cr, note=""):
    cr = _lock_request(cr)
    check(actor, "correction.approve", cr)
    if cr.requested_by_id == actor.pk and not settings.CORRECTION_SELF_APPROVAL:
        raise BusinessRuleError("Kendi düzeltme talebinizi onaylayamazsınız; başka bir yetkili onaylamalı.")
    kind = cr.kind
    target = _target(kind, cr.target_id, lock=True)
    now = current_value(kind, target)
    if now != cr.old_value:
        _decide(actor, cr, CorrectionStatus.REJECTED,
                f"Kayıt talepten sonra değişmiş (talepte {num(cr.old_value)}, şimdi {num(now)}); yeni talep açın.")
        return cr
    _apply(actor, cr, target)
    cr.affected = affected_records(kind, target) if kind != CorrectionKind.COUNT else cr.affected
    _decide(actor, cr, CorrectionStatus.APPLIED, note or "Onaylandı")
    return cr


def _apply(actor, cr, target):
    delta = cr.new_value - cr.old_value
    why = f"{cr.code}: {cr.reason}"
    kind = cr.kind
    if kind == CorrectionKind.COIL_WEIGHT:
        consumed = target.weight_kg - target.remaining_kg
        if cr.new_value < consumed:
            raise BusinessRuleError(f"{target.code} bobininden {num(consumed)} kg tüketilmiş; ağırlık bunun altına "
                                    "düşürülemez.")
        old_w, old_r = target.weight_kg, target.remaining_kg
        target.weight_kg = cr.new_value
        target.remaining_kg = old_r + delta
        if target.status in (CoilStatus.AVAILABLE, CoilStatus.PARTIAL, CoilStatus.CONSUMED):
            target.status = (CoilStatus.CONSUMED if target.remaining_kg == 0 else
                             CoilStatus.PARTIAL if target.remaining_kg < target.weight_kg else CoilStatus.AVAILABLE)
        target.save()
        audit(actor, "coil.correction", target, {"weight_kg": (old_w, target.weight_kg),
                                                 "remaining_kg": (old_r, target.remaining_kg)}, reason=why)
    elif kind in (CorrectionKind.MATERIAL_ISSUE, CorrectionKind.MATERIAL_RETURN):
        wo = target.work_order
        more_issue = delta > 0 if kind == CorrectionKind.MATERIAL_ISSUE else delta < 0
        if not more_issue and wo is not None:
            from production.services import issued_by_lot
            net = issued_by_lot(wo).get(target.lot, Decimal("0"))
            if abs(delta) > net:
                raise BusinessRuleError(f"{wo.code} iş emrinde {target.lot.code} lotundan net {num(net)} verilmiş; "
                                        "düzeltme bunu negatife düşüremez.")
        move_lot(actor, target.lot, LotMovementKind.ISSUE if more_issue else LotMovementKind.RETURN,
                 -abs(delta) if more_issue else abs(delta), work_order=wo, reason=f"Düzeltme {why}", _skip_perm=True)
    elif kind == CorrectionKind.PALLET_BOXES:
        from warehouse.services import _lock, _move, _reserved
        (pallet,) = _lock(target.pallet)
        if pallet.box_count + delta < _reserved(pallet):
            raise BusinessRuleError(f"{pallet.code} paletinde ayrılmış kutu var; düzeltme ayrılan miktarın altına inemez.")
        _move(actor, pallet, MovementKind.CORRECTION, int(delta), work_order=target.work_order, reason=why)
    elif kind == CorrectionKind.WEIGHING:
        from warehouse.services import _lock, _record_weighing
        (pallet,) = _lock(target.pallet)
        _record_weighing(actor, pallet, {"gross_kg": cr.new_value, "pallet_tare_kg": target.pallet_tare_kg,
                                         "box_tare_kg": target.box_tare_kg, "core_tare_kg": target.core_tare_kg,
                                         "roll_weights": target.roll_weights, "note": f"Düzeltme {cr.code}"})
    else:
        from warehouse.services import _lock, _move, _reserved
        (pallet,) = _lock(target)
        if cr.new_value < _reserved(pallet):
            raise BusinessRuleError(f"Sayılan adet ayrılan miktarın ({_reserved(pallet)}) altında; önce rezervasyonu "
                                    "azaltın.")
        _move(actor, pallet, MovementKind.COUNT_ADJUST, int(delta), reason=why)


def open_requests_for(user):
    from accounts.permissions import scoped
    return scoped(CorrectionRequest.objects.filter(status=CorrectionStatus.REQUESTED), user, "correction.approve")

