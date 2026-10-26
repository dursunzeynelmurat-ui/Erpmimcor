"""Depo iş kuralları. Palet kutu adedi yalnız buradaki hareket servisleriyle değişir.
Kaynak ve hedef palet hareketleri aynı transaction içinde yazılır: ya ikisi de olur ya hiçbiri."""
import uuid
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.permissions import check
from catalog.models import Unit
from core.exceptions import BusinessRuleError
from core.models import QualityStatus, ValueSource
from core.services import audit, next_code
from orders.models import LineStatus, OrderLine
from orders.services import set_line_status
from production.models import Coil, CoilLink, CoilStatus, WorkOrder, WorkOrderStatus
from production.services import final_operation

from .models import (LabelPrint, MovementKind, Pallet, PalletCoil, PalletMovement, PalletStatus, PalletWeighing,
                     Reservation, ReservationStatus, Shipment, ShipmentLine, ShipmentStatus, WeightStatus)


def _lock(*pallets):
    """Kilitlenme olmaması için paletleri her zaman pk sırasıyla kilitle."""
    ids = sorted({p.pk for p in pallets})
    locked = {p.pk: p for p in Pallet.objects.select_for_update().filter(pk__in=ids).order_by("pk")}
    return [locked[p.pk] for p in pallets]


def _reserved(pallet):
    agg = pallet.reservations.filter(status=ReservationStatus.ACTIVE).aggregate(q=Sum("quantity"), s=Sum("shipped_quantity"))
    return (agg["q"] or 0) - (agg["s"] or 0)


def _move(actor, pallet, kind, quantity, *, reason="", related=None, work_order=None, shipment_line=None, group=""):
    """Tek palet hareketi. pallet önceden kilitlenmiş olmalı. Negatif stok reddedilir."""
    new_count = pallet.box_count + quantity
    if new_count < 0:
        raise BusinessRuleError(f"{pallet.code} paletinde yeterli kutu yok (mevcut {pallet.box_count}, istenen {-quantity}).")
    old = pallet.box_count
    pallet.box_count = new_count
    changes = {"box_count": (old, new_count)}
    if pallet.weight_status == WeightStatus.CURRENT:
        # İçerik değişti: eski tartım geçmişte kalır, güncel ağırlık yeniden tartım bekler.
        pallet.weighings.filter(is_current=True).update(is_current=False)
        pallet.weight_status = WeightStatus.REWEIGH
        changes["weight_status"] = (WeightStatus.CURRENT, WeightStatus.REWEIGH)
    if new_count == 0 and pallet.status == PalletStatus.IN_STOCK:
        changes["status"] = (pallet.status, PalletStatus.EMPTY)
        pallet.status = PalletStatus.EMPTY
    pallet.save()
    mv = PalletMovement.objects.create(pallet=pallet, kind=kind, quantity=quantity, balance_after=new_count,
                                       related_pallet=related, work_order=work_order, shipment_line=shipment_line,
                                       group=group, reason=reason, user=actor)
    audit(actor, f"pallet.{kind}", pallet, changes, reason=reason)
    return mv


def _require_available(pallet, qty):
    available = pallet.box_count - _reserved(pallet)
    if qty > available:
        raise BusinessRuleError(f"{pallet.code}: serbest (ayrılmamış) kutu {available}; {qty} kutu işlenemez.",
                                field="quantity")


def _positive(qty):
    if qty is None or qty <= 0:
        raise BusinessRuleError("Kutu adedi sıfırdan büyük olmalı.", field="quantity")


def _require_box_unit(line, field="order_line"):
    """Bu sürümde fiziksel stok ve sevk kutu bazlıdır; başka birimdeki kalemle karşılaştırma/çevirme yapılmaz."""
    if line.unit != Unit.BOX:
        raise BusinessRuleError(
            f"{line.code}: bu sürümde stoktan ayırma ve sevkiyat yalnız kutu birimli kalemlerde yapılabilir "
            f"(kalem birimi: {line.get_unit_display()}).", field=field)


# --- Bobin blokajının paletlere etkisi --------------------------------------------------

# Bu hareketlerde related_pallet, kutuların geldiği kaynak palettir.
_INFLOW_KINDS = (MovementKind.TRANSFER_IN, MovementKind.DAMAGE_IN)


def _expand(start_ids, step):
    seen, frontier = set(start_ids), set(start_ids)
    while frontier:
        frontier = set(step(frontier)) - seen
        seen |= frontier
    return seen


def _pallet_ancestors(pallet_ids):
    """Paletin kendisi ve aktarım/hasar ayırma ile kutu aldığı tüm kaynak paletler."""
    return _expand(pallet_ids, lambda ids: PalletMovement.objects.filter(
        pallet_id__in=ids, kind__in=_INFLOW_KINDS, related_pallet__isnull=False).values_list("related_pallet_id", flat=True))


def _pallet_descendants(pallet_ids):
    """Paletin kendisi ve aktarım/hasar ayırma ile kutu verdiği tüm paletler."""
    return _expand(pallet_ids, lambda ids: PalletMovement.objects.filter(
        related_pallet_id__in=ids, kind__in=_INFLOW_KINDS).values_list("pallet_id", flat=True))


def coil_holds(pallet):
    """Paletin içeriğiyle ilişkili blokeli bobin kodları.

    Kapsam: paletin ve aktarımla kutu aldığı paletlerin bobinleri ile bunların kaynak bobin zinciri.
    Aktarılan kutuların hangi bobinden geldiği modelde ayrılamadığı için ilişkili her palet ihtiyatlı olarak
    bekletilir. Palet kalite alanı değişmez; engel bobin blokajı kalkınca kendiliğinden kalkar.
    """
    pallets = _pallet_ancestors({pallet.pk})
    coils = set(PalletCoil.objects.filter(pallet_id__in=pallets).values_list("coil_id", flat=True))
    coils = _expand(coils, lambda ids: CoilLink.objects.filter(output_id__in=ids).values_list("source_id", flat=True))
    return list(Coil.objects.filter(pk__in=coils, quality_status=QualityStatus.BLOCKED)
                .order_by("code").values_list("code", flat=True))


def pallets_affected_by_coil(coil):
    """Bobinin (ve ondan üretilen bobinlerin) bağlı olduğu paletler ile bunlardan aktarım alan paletler."""
    coils = _expand({coil.pk}, lambda ids: CoilLink.objects.filter(source_id__in=ids).values_list("output_id", flat=True))
    direct = set(PalletCoil.objects.filter(coil_id__in=coils).values_list("pallet_id", flat=True))
    return _pallet_descendants(direct) if direct else set()


def _hold_message(pallet, codes):
    return (f"{pallet.code}: ilişkili bobin blokeli ({', '.join(codes)}); sevk edilemez. "
            "Bobin blokajı kaldırılana kadar palet bekletilir.")


def record_coil_hold_change(actor, coil, old_status, new_status, reason):
    """Bobin blokajı değişince etkilenen paletleri kilitler (bobin → palet sırası) ve geçmişe gerekçeyi yazar.
    Palet kalite durumu değiştirilmez; bağımsız palet blokeleri korunur."""
    pallets = _lock(*Pallet.objects.filter(pk__in=pallets_affected_by_coil(coil)).order_by("pk"))
    blocked = new_status == QualityStatus.BLOCKED
    for pallet in pallets:
        audit(actor, "pallet.coil_hold" if blocked else "pallet.coil_release", pallet,
              {"bobin": (old_status, f"{coil.code}: {new_status}")}, reason=reason)
    return pallets


# --- Üretimden palet ---------------------------------------------------------------

def _lock_production_coils(wo, coils):
    """Üretimden paletleme için kaynak bobinleri doğrular ve pk sırasıyla kilitler.

    En az bir bobin zorunludur; yalnız bu iş emrinin son operasyonundan, kullanılabilir ve blokesiz bobinler
    kabul edilir. Kilit sırası bobin → palet (bobin blokajı servisiyle aynı), böylece kilitlenme döngüsü oluşmaz.
    """
    coils = [c for c in coils if c is not None]
    if not coils:
        raise BusinessRuleError("Üretimden palet için en az bir bobin/rulo seçilmelidir.", field="coils")
    ids = [c.pk for c in coils]
    if len(set(ids)) != len(ids):
        raise BusinessRuleError("Aynı bobin palete birden fazla kez eklenemez.", field="coils")
    last = final_operation(wo)
    locked = list(Coil.objects.select_for_update().filter(pk__in=ids).order_by("pk"))
    for c in locked:
        if c.work_order_id != wo.pk or c.operation_id != last.pk:
            raise BusinessRuleError(f"{c.code} bu iş emrinin son operasyon çıktısı değil.", field="coils")
        if PalletCoil.objects.filter(coil=c).exists():
            raise BusinessRuleError(f"{c.code} zaten bir palete bağlı.", field="coils")
        if c.status != CoilStatus.AVAILABLE:
            raise BusinessRuleError(f"{c.code} kullanılabilir durumda değil ({c.get_status_display()}).", field="coils")
        if c.quality_status == QualityStatus.BLOCKED:
            raise BusinessRuleError(f"{c.code} blokeli; palete alınamaz.", field="coils")
    return locked


def _lock_started_wo(wo, message):
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    if wo.status != WorkOrderStatus.STARTED:
        raise BusinessRuleError(message)
    return wo


@transaction.atomic
def create_pallet_from_production(actor, wo, box_count, coils):
    check(actor, "production.execute")
    wo = _lock_started_wo(wo, "Palet yalnız üretimdeki iş emrinden oluşturulabilir.")
    _positive(box_count)
    locked_coils = _lock_production_coils(wo, coils)
    pallet = Pallet.objects.create(code=next_code("PLT"), product=wo.product, work_order=wo, created_by=actor)
    audit(actor, "pallet.create", pallet, {"product": (None, wo.product.code), "work_order": (None, wo.code)})
    _attach(actor, pallet, wo, box_count, locked_coils)
    return pallet


@transaction.atomic
def add_production_to_pallet(actor, pallet, wo, box_count, coils):
    """Var olan palete aynı ürünün üretimini ekler. Farklı ürün kodu aynı palete konamaz."""
    check(actor, "production.execute")
    wo = _lock_started_wo(wo, "Yalnız üretimdeki iş emrinden ekleme yapılabilir.")
    _positive(box_count)
    locked_coils = _lock_production_coils(wo, coils)
    (pallet,) = _lock(pallet)
    if pallet.product_id != wo.product_id:
        raise BusinessRuleError(
            f"{pallet.code} paleti {pallet.product.code} ürününe ait; {wo.product.code} eklenemez. "
            "Her palet yalnız bir ürün kodu içerir.")
    if pallet.status == PalletStatus.EMPTY:
        raise BusinessRuleError("Boşalmış palete ekleme yapılamaz; yeni palet oluşturun.")
    _attach(actor, pallet, wo, box_count, locked_coils)
    return pallet


def _attach(actor, pallet, wo, box_count, locked_coils):
    for coil in locked_coils:
        coil.status = CoilStatus.PALLETIZED
        coil.save()
        PalletCoil.objects.create(pallet=pallet, coil=coil)
    _move(actor, pallet, MovementKind.PRODUCTION, box_count, work_order=wo)
    wo.save(update_fields=["updated_at"])


# --- Tartım ve kabul ---------------------------------------------------------------

def weight_breakdown(weighing, pallet=None):
    """Tartımdan gösterilecek değerler: [(etiket, kg, kaynak)]. Eksik dara sıfır sayılmaz;
    yalnız tüm daralar biliniyorsa 'Net ürün ağırlığı' denir."""
    rows = [("Brüt ağırlık", weighing.gross_kg, ValueSource.MEASURED)]
    tares = [("palet", weighing.pallet_tare_kg),
             ("kutu", None if weighing.box_tare_kg is None else weighing.box_tare_kg * weighing.box_count_at_weighing),
             ("masura", weighing.core_tare_kg)]
    known = [(n, v) for n, v in tares if v is not None]
    missing = [n for n, v in tares if v is None]
    if known:
        value = weighing.gross_kg - sum((v for _, v in known), Decimal("0"))
        names = " ve ".join(n.capitalize() if i == 0 else n for i, (n, _) in enumerate(known))
        if not missing:
            label = "Net ürün ağırlığı"
        elif [n for n, _ in known] == ["palet"]:
            label = "Palet hariç ağırlık"
        else:
            label = f"{names} hariç ağırlık"
        if missing:
            label += f" ({', '.join(missing)} darası bilinmiyor)"
        rows.append((label, value, ValueSource.CALCULATED))
    if weighing.roll_weights:
        rolls = [Decimal(str(w)) for w in weighing.roll_weights]
        rows.append((f"Tekil rulo ağırlıkları toplamı ({len(rolls)} rulo)", sum(rolls, Decimal("0")), ValueSource.MEASURED))
    if pallet is not None:
        coil_sum = sum((pc.coil.weight_kg for pc in pallet.pallet_coils.select_related("coil")), Decimal("0"))
        only_production = not pallet.movements.exclude(kind=MovementKind.PRODUCTION).exists()
        if coil_sum and only_production:
            rows.append(("Bobin kayıtlarından ürün ağırlığı", coil_sum, ValueSource.ESTIMATED))
    return rows


def _record_weighing(actor, pallet, data):
    gross = data.get("gross_kg")
    if gross is None or gross <= 0:
        raise BusinessRuleError("Palet brüt ağırlığı zorunludur.", field="gross_kg")
    for f in ("pallet_tare_kg", "box_tare_kg", "core_tare_kg"):
        if data.get(f) is not None and data[f] < 0:
            raise BusinessRuleError("Dara negatif olamaz.", field=f)
    rolls = [Decimal(str(w)) for w in (data.get("roll_weights") or [])]
    if any(w <= 0 for w in rolls):
        raise BusinessRuleError("Rulo ağırlıkları sıfırdan büyük olmalı.", field="roll_weights")
    pallet.weighings.filter(is_current=True).update(is_current=False)
    w = PalletWeighing(pallet=pallet, gross_kg=gross, pallet_tare_kg=data.get("pallet_tare_kg"),
                       box_tare_kg=data.get("box_tare_kg"), core_tare_kg=data.get("core_tare_kg"),
                       roll_weights=[str(r) for r in rolls], box_count_at_weighing=pallet.box_count,
                       note=data.get("note", ""), user=actor)
    rows = weight_breakdown(w)
    if len(rows) > 1 and rows[1][2] == ValueSource.CALCULATED and rows[1][1] <= 0:
        raise BusinessRuleError("Daralar brüt ağırlıktan büyük veya eşit olamaz.", field="gross_kg")
    w.save()
    old = pallet.weight_status
    pallet.weight_status = WeightStatus.CURRENT
    pallet.save()
    audit(actor, "pallet.weigh", pallet, {"gross_kg": (None, gross), "pallet_tare_kg": (None, data.get("pallet_tare_kg")),
                                          "box_tare_kg": (None, data.get("box_tare_kg")),
                                          "core_tare_kg": (None, data.get("core_tare_kg")),
                                          "weight_status": (old, WeightStatus.CURRENT)})
    return w


@transaction.atomic
def accept_pallet(actor, pallet, data):
    """Depo kabulü: brüt tartım zorunlu."""
    check(actor, "warehouse.operate")
    (pallet,) = _lock(pallet)
    if pallet.status != PalletStatus.AWAITING_ACCEPTANCE:
        raise BusinessRuleError("Palet depo kabulü beklemiyor.")
    if pallet.box_count <= 0:
        raise BusinessRuleError("Boş palet kabul edilemez.")
    _record_weighing(actor, pallet, data)
    pallet.status = PalletStatus.IN_STOCK
    pallet.accepted_by = actor
    pallet.accepted_at = timezone.now()
    pallet.location = data.get("location", "") or pallet.location
    pallet.save()
    audit(actor, "pallet.accept", pallet, {"status": (PalletStatus.AWAITING_ACCEPTANCE, PalletStatus.IN_STOCK),
                                           "location": (None, pallet.location)})
    return pallet


@transaction.atomic
def reweigh_pallet(actor, pallet, data):
    check(actor, "warehouse.operate")
    (pallet,) = _lock(pallet)
    if pallet.status != PalletStatus.IN_STOCK:
        raise BusinessRuleError("Yalnız depodaki palet yeniden tartılabilir.")
    return _record_weighing(actor, pallet, data)


@transaction.atomic
def record_label_print(actor, pallet):
    """Etiket (yeniden) basımı yalnız kayıt oluşturur; yeni palet oluşmaz."""
    check(actor, "view.warehouse")
    reprint = pallet.label_prints.exists()
    lp = LabelPrint.objects.create(pallet=pallet, is_reprint=reprint, box_count_at_print=pallet.box_count, user=actor)
    audit(actor, "pallet.label_reprint" if reprint else "pallet.label_print", pallet, {})
    return lp


# --- Kalite --------------------------------------------------------------------------

@transaction.atomic
def set_pallet_quality(actor, pallet, status, reason):
    check(actor, "quality.block" if status == QualityStatus.BLOCKED else "quality.decide")
    if not reason:
        raise BusinessRuleError("Kalite değişikliği için gerekçe zorunludur.", field="reason")
    (pallet,) = _lock(pallet)
    if pallet.quality_status == status:
        raise BusinessRuleError("Palet zaten bu kalite durumunda.")
    old = pallet.quality_status
    pallet.quality_status = status
    pallet.save()
    audit(actor, "pallet.quality", pallet, {"quality_status": (old, status)}, reason=reason)
    return pallet


# --- Palet işlemleri (atomik, kaynak + hedef) ---------------------------------------

def _require_in_stock(pallet):
    if pallet.status != PalletStatus.IN_STOCK:
        raise BusinessRuleError(f"{pallet.code} depoda değil ({pallet.get_status_display()}).")


@transaction.atomic
def transfer_boxes(actor, source, quantity, reason, target=None):
    """Başka palete aktarım. Hedef verilmezse aynı ürün için yeni palet açılır."""
    check(actor, "warehouse.operate")
    _positive(quantity)
    if not reason:
        raise BusinessRuleError("Aktarım için gerekçe zorunludur.", field="reason")
    if target is not None and target.pk == source.pk:
        raise BusinessRuleError("Kaynak ve hedef palet aynı olamaz.", field="target_code")
    if target is not None:
        source, target = _lock(source, target)
    else:
        (source,) = _lock(source)
    _require_in_stock(source)
    _require_available(source, quantity)
    if target is None:
        target = Pallet.objects.create(code=next_code("PLT"), product=source.product, work_order=source.work_order,
                                       status=PalletStatus.IN_STOCK, quality_status=source.quality_status,
                                       location=source.location, created_by=actor, accepted_by=actor,
                                       accepted_at=timezone.now())
        audit(actor, "pallet.create", target, {"product": (None, source.product.code), "kaynak": (None, source.code)},
              reason=reason)
    else:
        if target.product_id != source.product_id:
            raise BusinessRuleError(
                f"Farklı ürün kodları aynı palete konamaz ({source.product.code} → {target.product.code}).",
                field="target_code")
        _require_in_stock(target)
        if target.quality_status != source.quality_status:
            raise BusinessRuleError("Kalite durumu farklı paletler arasında aktarım yapılamaz.", field="target_code")
    group = uuid.uuid4().hex
    _move(actor, source, MovementKind.TRANSFER_OUT, -quantity, reason=reason, related=target, group=group)
    _move(actor, target, MovementKind.TRANSFER_IN, quantity, reason=reason, related=source, group=group)
    return target


@transaction.atomic
def adjust_count(actor, pallet, counted, reason):
    check(actor, "warehouse.operate")
    if not reason:
        raise BusinessRuleError("Sayım düzeltmesi için gerekçe zorunludur.", field="reason")
    if counted is None or counted < 0:
        raise BusinessRuleError("Sayılan adet negatif olamaz.", field="counted")
    (pallet,) = _lock(pallet)
    _require_in_stock(pallet)
    delta = counted - pallet.box_count
    if delta == 0:
        raise BusinessRuleError("Sayılan adet mevcut adetle aynı.", field="counted")
    if counted < _reserved(pallet):
        raise BusinessRuleError(f"Sayılan adet ({counted}) siparişlere ayrılan miktarın ({_reserved(pallet)}) altında. "
                                "Önce rezervasyonu düzeltin.", field="counted")
    return _move(actor, pallet, MovementKind.COUNT_ADJUST, delta, reason=reason)


@transaction.atomic
def separate_damaged(actor, pallet, quantity, reason):
    """Hasarlı kutuları blokeli yeni palete ayırır (fiziksel stokta kalır ama sevk edilemez)."""
    check(actor, "warehouse.operate")
    _positive(quantity)
    if not reason:
        raise BusinessRuleError("Hasar ayırma için gerekçe zorunludur.", field="reason")
    (pallet,) = _lock(pallet)
    _require_in_stock(pallet)
    _require_available(pallet, quantity)
    damaged = Pallet.objects.create(code=next_code("PLT"), product=pallet.product, work_order=pallet.work_order,
                                    status=PalletStatus.IN_STOCK, quality_status=QualityStatus.BLOCKED,
                                    location="Hasarlı ürün alanı", created_by=actor, accepted_by=actor,
                                    accepted_at=timezone.now())
    audit(actor, "pallet.create", damaged, {"kaynak": (None, pallet.code), "quality_status": (None, QualityStatus.BLOCKED)},
          reason=reason)
    group = uuid.uuid4().hex
    _move(actor, pallet, MovementKind.DAMAGE_OUT, -quantity, reason=reason, related=damaged, group=group)
    _move(actor, damaged, MovementKind.DAMAGE_IN, quantity, reason=reason, related=pallet, group=group)
    return damaged


# --- Rezervasyon -----------------------------------------------------------------------

@transaction.atomic
def reserve(actor, pallet, order_line, quantity):
    """Paletten sipariş kalemine kutu ayırır. Fiziksel stok (box_count) değişmez."""
    check(actor, "reservation.manage")
    _positive(quantity)
    (pallet,) = _lock(pallet)
    line = OrderLine.objects.select_for_update(of=("self",)).select_related("order", "product").get(pk=order_line.pk)
    _require_box_unit(line)
    if pallet.product_id != line.product_id:
        raise BusinessRuleError(f"Palet ürünü ({pallet.product.code}) kalem ürünüyle ({line.product.code}) aynı değil.",
                                field="order_line")
    if line.status in (LineStatus.CANCELLED, LineStatus.SHIPPED):
        raise BusinessRuleError("Kapanmış kaleme ayırma yapılamaz.", field="order_line")
    if pallet.status == PalletStatus.EMPTY:
        raise BusinessRuleError("Boş paletten ayırma yapılamaz.")
    if pallet.quality_status == QualityStatus.BLOCKED:
        raise BusinessRuleError("Blokeli paletten ayırma yapılamaz.")
    _require_available(pallet, quantity)
    res = Reservation.objects.create(pallet=pallet, order_line=line, quantity=quantity, created_by=actor)
    audit(actor, "reservation.create", res, {"pallet": (None, pallet.code), "order_line": (None, line.code),
                                             "quantity": (None, quantity)})
    audit(actor, "pallet.reserve", pallet, {"ayrilan": (None, f"{quantity} kutu → {line.code}")})
    return res


@transaction.atomic
def cancel_reservation(actor, reservation, reason):
    check(actor, "reservation.manage")
    if not reason:
        raise BusinessRuleError("İptal gerekçesi zorunludur.", field="reason")
    res = Reservation.objects.select_for_update().get(pk=reservation.pk)
    if res.status != ReservationStatus.ACTIVE:
        raise BusinessRuleError("Yalnız aktif rezervasyon iptal edilebilir.")
    if res.shipment_lines.filter(shipment__status=ShipmentStatus.DRAFT).exists():
        raise BusinessRuleError("Rezervasyon hazırlanan bir sevkiyatta; önce sevkiyattan çıkarın.")
    if res.shipped_quantity:
        # Kısmen sevk edilmiş: kalan kısım serbest bırakılır, kayıt sevk edilen miktarla kapanır.
        old = res.quantity
        res.quantity = res.shipped_quantity
        res.status = ReservationStatus.SHIPPED
        changes = {"quantity": (old, res.quantity), "status": (ReservationStatus.ACTIVE, res.status)}
    else:
        res.status = ReservationStatus.CANCELLED
        changes = {"status": (ReservationStatus.ACTIVE, res.status)}
    res.cancel_reason = reason
    res.save()
    audit(actor, "reservation.cancel", res, changes, reason=reason)
    return res


# --- Sevkiyat --------------------------------------------------------------------------

@transaction.atomic
def create_shipment(actor, order, vehicle="", notes=""):
    check(actor, "shipment.manage")
    shipment = Shipment.objects.create(code=next_code("SVK"), order=order, vehicle=vehicle, notes=notes, created_by=actor)
    audit(actor, "shipment.create", shipment, {"order": (None, order.code)})
    return shipment


@transaction.atomic
def add_shipment_line(actor, shipment, reservation, quantity):
    check(actor, "shipment.manage")
    _positive(quantity)
    shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)
    if shipment.status != ShipmentStatus.DRAFT:
        raise BusinessRuleError("Sevk edilmiş sevkiyata satır eklenemez.")
    res = Reservation.objects.select_for_update().select_related("order_line", "pallet").get(pk=reservation.pk)
    if res.order_line.order_id != shipment.order_id:
        raise BusinessRuleError("Rezervasyon bu siparişe ait değil.", field="reservation")
    _require_box_unit(res.order_line, field="reservation")
    if res.status != ReservationStatus.ACTIVE:
        raise BusinessRuleError("Rezervasyon aktif değil.", field="reservation")
    held = coil_holds(res.pallet)
    if held:
        raise BusinessRuleError(_hold_message(res.pallet, held), field="reservation")
    if shipment.lines.filter(reservation=res).exists():
        raise BusinessRuleError("Bu rezervasyon zaten sevkiyatta.", field="reservation")
    in_drafts = (ShipmentLine.objects.filter(reservation=res, shipment__status=ShipmentStatus.DRAFT)
                 .aggregate(s=Sum("quantity"))["s"] or 0)
    if quantity > res.open_quantity - in_drafts:
        raise BusinessRuleError(f"Rezervasyonda sevk edilebilecek {res.open_quantity - in_drafts} kutu var.",
                                field="quantity")
    line = ShipmentLine.objects.create(shipment=shipment, reservation=res, pallet=res.pallet,
                                       order_line=res.order_line, quantity=quantity)
    audit(actor, "shipment.line_add", shipment, {"satir": (None, f"{res.pallet.code} {quantity} kutu → {res.order_line.code}")})
    return line


@transaction.atomic
def remove_shipment_line(actor, line):
    check(actor, "shipment.manage")
    shipment = Shipment.objects.select_for_update().get(pk=line.shipment_id)
    if shipment.status != ShipmentStatus.DRAFT:
        raise BusinessRuleError("Sevk edilmiş sevkiyat değiştirilemez.")
    desc = f"{line.pallet.code} {line.quantity} kutu"
    line.delete()  # Henüz stok hareketi oluşmamış taslak satır.
    audit(actor, "shipment.line_remove", shipment, {"satir": (desc, None)})
    return shipment


@transaction.atomic
def confirm_shipment(actor, shipment):
    """Sevkiyatı onaylar: her satır için fiziksel stoktan düşer, rezervasyonu kapatır.
    Uygun olmayan ürün sevk edilemez; ikinci onay reddedilir."""
    check(actor, "shipment.manage")
    shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)
    if shipment.status != ShipmentStatus.DRAFT:
        raise BusinessRuleError(f"{shipment.code} zaten sevk edildi.")
    lines = list(shipment.lines.select_related("reservation", "order_line").order_by("pallet_id"))
    if not lines:
        raise BusinessRuleError("Sevkiyatta satır yok.")
    # Kilit sırası: sevkiyat → paletler (pk sırası) → rezervasyonlar. Bobin satırları kilitlenmez, okunur:
    # bobin blokajı servisi bobin → palet sırasıyla kilitlediği için sevkle birlikte serileşir.
    pallets = {p.pk: p for p in _lock(*[ln.pallet for ln in lines])}
    reservations = {r.pk: r for r in Reservation.objects.select_for_update().select_related("order_line")
                    .filter(pk__in=[ln.reservation_id for ln in lines]).order_by("pk")}
    # Önce tüm satırlar doğrulanır; tek bir satır bile uygun değilse hiçbir stok hareketi yazılmaz.
    for ln in lines:
        pallet, res = pallets[ln.pallet_id], reservations[ln.reservation_id]
        _require_box_unit(ln.order_line)
        _require_box_unit(res.order_line)
        if pallet.quality_status != QualityStatus.OK:
            raise BusinessRuleError(f"{pallet.code} kalite durumu '{pallet.get_quality_status_display()}'; "
                                    "yalnız uygun ürün sevk edilebilir.")
        held = coil_holds(pallet)
        if held:
            raise BusinessRuleError(_hold_message(pallet, held))
        _require_in_stock(pallet)
        if res.status != ReservationStatus.ACTIVE or ln.quantity > res.open_quantity:
            raise BusinessRuleError(f"{pallet.code} rezervasyonu sevk için yeterli değil.")
    for ln in lines:
        pallet, res = pallets[ln.pallet_id], reservations[ln.reservation_id]
        _move(actor, pallet, MovementKind.SHIPMENT, -ln.quantity, shipment_line=ln,
              reason=f"{shipment.code} / {ln.order_line.code}")
        old = res.shipped_quantity
        res.shipped_quantity += ln.quantity
        if res.shipped_quantity == res.quantity:
            res.status = ReservationStatus.SHIPPED
        res.save()
        audit(actor, "reservation.ship", res, {"shipped_quantity": (old, res.shipped_quantity)}, reason=shipment.code)
    shipment.status = ShipmentStatus.SHIPPED
    shipment.shipped_by = actor
    shipment.shipped_at = timezone.now()
    shipment.save()
    audit(actor, "shipment.confirm", shipment, {"status": (ShipmentStatus.DRAFT, ShipmentStatus.SHIPPED)})
    for line in {ln.order_line_id: ln.order_line for ln in lines}.values():
        if line.unit != Unit.BOX:  # Kutu sayısı başka birimdeki sipariş miktarıyla karşılaştırılmaz.
            continue
        shipped = ShipmentLine.objects.filter(order_line=line, shipment__status=ShipmentStatus.SHIPPED).aggregate(
            s=Sum("quantity"))["s"] or 0
        if shipped >= line.quantity:
            set_line_status(actor, line, LineStatus.SHIPPED, reason=shipment.code)
    return shipment
