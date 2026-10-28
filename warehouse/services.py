"""Depo iş kuralları. Palet kutu adedi yalnız buradaki hareket servisleriyle değişir.
Kaynak ve hedef palet hareketleri aynı transaction içinde yazılır: ya ikisi de olur ya hiçbiri."""
import uuid
from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from accounts.permissions import check
from catalog.models import Unit
from core.exceptions import BusinessRuleError
from core.models import QualityStatus, ValueSource
from core.services import audit, next_code, num
from orders.models import LineStatus, OrderLine, OrderStatus
from orders.services import (committed_quantity, compatibility_problem, line_limit, refresh_order_status,
                             set_line_status, tech_signature)
from production.models import Coil, CoilLink, CoilStatus, WorkOrder, WorkOrderStatus
from production.services import final_operation

from .models import (LabelKind, LabelPrint, MovementKind, Pallet, PalletCoil, PalletLocationChange, PalletMovement,
                     PalletStatus, PalletWeighing, PrintStatus, ReturnReceipt, ScanResult, ShipmentScan, StockLevel,
                     OPEN_SHIPMENT_STATES, Reservation, ReservationStatus, SHIPPED_STATES, Shipment, ShipmentLine, ShipmentStatus, WeightStatus)


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
    check(actor, "production.execute", wo)
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
    check(actor, "production.execute", wo)
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
    if pallet.quality_status == QualityStatus.OK and pallet.box_count > 0:
        # Uygun palete kontrol edilmemiş yeni içerik: palet yeniden kontrol bekler.
        pallet.quality_status = QualityStatus.PENDING
        pallet.save()
        audit(actor, "pallet.quality", pallet, {"quality_status": (QualityStatus.OK, QualityStatus.PENDING)},
              reason=f"{wo.code} üretiminden {box_count} kutu eklendi; yeni içerik kontrol edilmeli")
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
    new_location = data.get("location", "") or pallet.location
    if new_location and new_location != pallet.location:
        PalletLocationChange.objects.create(pallet=pallet, old_location=pallet.location, new_location=new_location,
                                            reason="Depo kabulü", user=actor)
    pallet.location = new_location
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
    return request_label_print(actor, pallet)


# --- Kalite --------------------------------------------------------------------------

def quality_permission(old, new):
    """Blokelemek quality.block, blokajı kaldırmak quality.release, diğer kararlar quality.decide ister."""
    if new == QualityStatus.BLOCKED:
        return "quality.block"
    if old == QualityStatus.BLOCKED:
        return "quality.release"
    return "quality.decide"


@transaction.atomic
def set_pallet_quality(actor, pallet, status, reason):
    if not reason:
        raise BusinessRuleError("Kalite değişikliği için gerekçe zorunludur.", field="reason")
    (pallet,) = _lock(pallet)
    check(actor, quality_permission(pallet.quality_status, status), pallet)
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
        a, b = pallet_revision(source), pallet_revision(target)
        if a and b and a.pk != b.pk and tech_signature(a) != tech_signature(b):
            raise BusinessRuleError(f"{source.code} (Rev.{a.rev_no}) ve {target.code} (Rev.{b.rev_no}) teknik olarak "
                                    "farklı; aynı palette karıştırılamaz.", field="target_code")
    group = uuid.uuid4().hex
    _move(actor, source, MovementKind.TRANSFER_OUT, -quantity, reason=reason, related=target, group=group)
    _move(actor, target, MovementKind.TRANSFER_IN, quantity, reason=reason, related=source, group=group)
    return target


# Palet sayım düzeltmesi doğrudan servis olarak yoktur: yalnız onaylı düzeltme talebi (corrections) uygular.
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

def pallet_revision(pallet):
    return pallet.work_order.recipe_revision if pallet.work_order_id else None


def _require_compatible(line, pallet, field=None):
    problem = compatibility_problem(line, pallet_revision(pallet))
    if problem:
        raise BusinessRuleError(f"{pallet.code}: {problem}", field=field)


@transaction.atomic
def reserve(actor, pallet, order_line, quantity):
    """Paletten sipariş kalemine kutu ayırır. Fiziksel stok (box_count) değişmez."""
    check(actor, "reservation.manage", order_line)
    _positive(quantity)
    (pallet,) = _lock(pallet)
    line = OrderLine.objects.select_for_update(of=("self",)).select_related("order", "product").get(pk=order_line.pk)
    _require_box_unit(line)
    if pallet.product_id != line.product_id:
        raise BusinessRuleError(f"Palet ürünü ({pallet.product.code}) kalem ürünüyle ({line.product.code}) aynı değil.",
                                field="order_line")
    if line.status in (LineStatus.CANCELLED, LineStatus.SHIPPED):
        raise BusinessRuleError("Kapanmış kaleme ayırma yapılamaz.", field="order_line")
    if line.order.status not in (OrderStatus.OPEN, OrderStatus.PARTIAL):
        raise BusinessRuleError("Sipariş onaylanmadan stok ayrılamaz.", field="order_line")
    if pallet.status == PalletStatus.EMPTY:
        raise BusinessRuleError("Boş paletten ayırma yapılamaz.")
    if pallet.quality_status == QualityStatus.BLOCKED:
        raise BusinessRuleError("Blokeli paletten ayırma yapılamaz.")
    _require_compatible(line, pallet, field="order_line")
    _require_available(pallet, quantity)
    # Kalem satırı kilitli: eş zamanlı ayırmalar sırayla kontrol edilir (bulgu 2).
    committed, limit = committed_quantity(line), line_limit(line)
    if committed + quantity > limit:
        raise BusinessRuleError(f"{line.code}: kalem miktarı {num(limit)} kutu; sevk edilmiş + ayrılmış {committed} kutu. "
                                f"En fazla {max(limit - committed, 0)} kutu daha ayrılabilir. Fazla teslim yalnız yetkili "
                                "onayla yapılır.", field="quantity")
    res = Reservation.objects.create(pallet=pallet, order_line=line, quantity=quantity, created_by=actor)
    audit(actor, "reservation.create", res, {"pallet": (None, pallet.code), "order_line": (None, line.code),
                                             "quantity": (None, quantity)})
    audit(actor, "pallet.reserve", pallet, {"ayrilan": (None, f"{quantity} kutu → {line.code}")})
    return res


@transaction.atomic
def cancel_reservation(actor, reservation, reason):
    check(actor, "reservation.manage", reservation)
    if not reason:
        raise BusinessRuleError("İptal gerekçesi zorunludur.", field="reason")
    res = Reservation.objects.select_for_update().get(pk=reservation.pk)
    if res.status != ReservationStatus.ACTIVE:
        raise BusinessRuleError("Yalnız aktif rezervasyon iptal edilebilir.")
    if res.shipment_lines.filter(shipment__status__in=OPEN_SHIPMENT_STATES).exists():
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
    from orders.models import SalesOrder
    order = SalesOrder.objects.select_for_update(of=("self",)).get(pk=order.pk)  # güncel durum (eski nesneye güvenme)
    if order.status not in (OrderStatus.OPEN, OrderStatus.PARTIAL):
        raise BusinessRuleError("Yalnız onaylı ve kapanmamış sipariş için sevkiyat hazırlanır.")
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
    res = Reservation.objects.select_for_update(of=("self",)).select_related("order_line", "pallet").get(pk=reservation.pk)
    if res.order_line.order_id != shipment.order_id:
        raise BusinessRuleError("Rezervasyon bu siparişe ait değil.", field="reservation")
    _require_box_unit(res.order_line, field="reservation")
    if res.status != ReservationStatus.ACTIVE:
        raise BusinessRuleError("Rezervasyon aktif değil.", field="reservation")
    held = coil_holds(res.pallet)
    if held:
        raise BusinessRuleError(_hold_message(res.pallet, held), field="reservation")
    _require_compatible(res.order_line, res.pallet, field="reservation")
    if shipment.lines.filter(reservation=res).exists():
        raise BusinessRuleError("Bu rezervasyon zaten sevkiyatta.", field="reservation")
    in_drafts = (ShipmentLine.objects.filter(reservation=res, shipment__status__in=OPEN_SHIPMENT_STATES)
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
    check(actor, "shipment.approve")
    shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)
    if shipment.status in SHIPPED_STATES:
        raise BusinessRuleError(f"{shipment.code} zaten sevk edildi.")
    if shipment.status != ShipmentStatus.PREPARED:
        raise BusinessRuleError("Sevk onayından önce sevkiyat 'hazırlandı' yapılmalı ve yükleme kontrolü tamamlanmalı.")
    lines = list(shipment.lines.select_related("reservation", "order_line").order_by("pallet_id"))
    if not lines:
        raise BusinessRuleError("Sevkiyatta satır yok.")
    check_result = load_check(shipment)
    if check_result["missing"]:
        raise BusinessRuleError("Yükleme kontrolü tamamlanmadı; okutulmayan palet: " +
                                ", ".join(p.code for p in check_result["missing"]))
    # Kilit sırası: sevkiyat → paletler (pk sırası) → rezervasyonlar. Bobin satırları kilitlenmez, okunur:
    # bobin blokajı servisi bobin → palet sırasıyla kilitlediği için sevkle birlikte serileşir.
    pallets = {p.pk: p for p in _lock(*[ln.pallet for ln in lines])}
    reservations = {r.pk: r for r in Reservation.objects.select_for_update(of=("self",)).select_related("order_line")
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
        _require_compatible(ln.order_line, pallet)
    # Kalem satırları en son kilitlenir (sevkiyat → paletler → rezervasyonlar → kalemler); sevk toplamı kalem
    # sınırını (miktar + yetkili fazla teslim) aşamaz.
    order_lines = {ol.pk: ol for ol in OrderLine.objects.select_for_update(of=("self",))
                   .filter(pk__in={ln.order_line_id for ln in lines}).order_by("pk")}
    for ol in order_lines.values():
        shipped = ShipmentLine.objects.filter(order_line=ol, shipment__status__in=SHIPPED_STATES).aggregate(
            s=Sum("quantity"))["s"] or 0
        now = sum(ln.quantity for ln in lines if ln.order_line_id == ol.pk)
        if shipped + now > line_limit(ol):
            raise BusinessRuleError(f"{ol.code}: sevk toplamı ({shipped + now}) kalem miktarını ({num(line_limit(ol))}) "
                                    "aşıyor.")
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
    audit(actor, "shipment.confirm", shipment, {"status": (ShipmentStatus.PREPARED, ShipmentStatus.SHIPPED)})
    for line in {ln.order_line_id: ln.order_line for ln in lines}.values():
        if line.unit != Unit.BOX:  # Kutu sayısı başka birimdeki sipariş miktarıyla karşılaştırılmaz.
            continue
        shipped = ShipmentLine.objects.filter(order_line=line, shipment__status__in=SHIPPED_STATES).aggregate(
            s=Sum("quantity"))["s"] or 0
        if shipped >= line.quantity:
            set_line_status(actor, line, LineStatus.SHIPPED, reason=shipment.code)
    refresh_order_status(actor, shipment.order)
    return shipment


@transaction.atomic
def cancel_shipment(actor, shipment, reason):
    """Hazırlanan (stok düşmemiş) sevkiyat iptal edilir; aktif listelerde atıl kalmaz, geçmişi korunur."""
    if not reason:
        raise BusinessRuleError("İptal gerekçesi zorunludur.", field="reason")
    shipment = Shipment.objects.select_for_update(of=("self",)).select_related("order").get(pk=shipment.pk)
    check(actor, "shipment.manage", shipment)
    if shipment.status not in OPEN_SHIPMENT_STATES:
        raise BusinessRuleError("Yalnız stok düşmemiş (hazırlanan/yükleme kontrolündeki) sevkiyat iptal edilebilir.")
    old_status = shipment.status
    shipment.status = ShipmentStatus.CANCELLED
    shipment.notes = (shipment.notes + f"\nİptal: {reason}").strip()
    shipment.save()
    audit(actor, "shipment.cancel", shipment, {"status": (old_status, ShipmentStatus.CANCELLED)}, reason=reason)
    return shipment



# --- Aşama 4: sevkiyat hazırlığı, yükleme kontrolü, çıkış, geri alma, iade --------------------

def _lock_shipment(shipment):
    return Shipment.objects.select_for_update(of=("self",)).select_related("order").get(pk=shipment.pk)


@transaction.atomic
def prepare_shipment(actor, shipment):
    """Hazırlık tamam: satır eklenmez/çıkarılmaz; yükleme kontrolü (QR okutma) başlar. Stok değişmez."""
    shipment = _lock_shipment(shipment)
    check(actor, "shipment.manage", shipment)
    if shipment.status != ShipmentStatus.DRAFT:
        raise BusinessRuleError("Yalnız hazırlanan (taslak) sevkiyat 'hazırlandı' yapılabilir.")
    if not shipment.lines.exists():
        raise BusinessRuleError("Sevkiyatta satır yok.")
    shipment.status, shipment.prepared_by, shipment.prepared_at = ShipmentStatus.PREPARED, actor, timezone.now()
    shipment.save()
    audit(actor, "shipment.prepare", shipment, {"status": (ShipmentStatus.DRAFT, ShipmentStatus.PREPARED)})
    return shipment


@transaction.atomic
def reopen_shipment(actor, shipment, reason):
    """Yükleme kontrolünden hazırlığa dönüş (satır değişikliği için); önceki okutmalar geçersiz sayılır."""
    shipment = _lock_shipment(shipment)
    check(actor, "shipment.manage", shipment)
    if shipment.status != ShipmentStatus.PREPARED:
        raise BusinessRuleError("Yalnız 'hazırlandı' durumundaki sevkiyat yeniden açılabilir.")
    if not reason:
        raise BusinessRuleError("Gerekçe zorunludur.", field="reason")
    shipment.status, shipment.prepared_at = ShipmentStatus.DRAFT, None
    shipment.save()
    audit(actor, "shipment.reopen", shipment, {"status": (ShipmentStatus.PREPARED, ShipmentStatus.DRAFT)}, reason=reason)
    return shipment


def _token_from(raw):
    """Okutulan değer QR bağlantısı (…/q/<token>/) veya doğrudan belirteç/palet no olabilir."""
    raw = (raw or "").strip()
    if "/q/" in raw:
        return raw.rstrip("/").rsplit("/q/", 1)[1].split("/")[0]
    return raw


@transaction.atomic
def scan_pallet(actor, shipment, raw):
    """Yükleme kontrolü okutması. Stok değişmez. Doğru/yanlış/mükerrer/bilinmeyen sonucu kaydedilir."""
    from accounts.permissions import scoped
    shipment = _lock_shipment(shipment)
    check(actor, "shipment.manage", shipment)
    if shipment.status != ShipmentStatus.PREPARED:
        raise BusinessRuleError("Okutma yalnız 'hazırlandı' (yükleme kontrolü) durumunda yapılır.")
    token = _token_from(raw)
    if not token:
        raise BusinessRuleError("Okutulan değer boş.", field="raw")
    visible = scoped(Pallet.objects.all(), actor, "stock.view")
    pallet = visible.filter(qr_token=token).first() or visible.filter(code=token.upper()).first()
    expected = set(shipment.lines.values_list("pallet_id", flat=True))
    if pallet is None:
        result = ScanResult.UNKNOWN
    elif pallet.pk not in expected:
        result = ScanResult.WRONG
    elif _round_scans(shipment).filter(pallet=pallet, result=ScanResult.OK).exists():
        result = ScanResult.DUPLICATE
    else:
        result = ScanResult.OK
    scan = ShipmentScan.objects.create(shipment=shipment, pallet=pallet, raw=raw[:300], result=result, user=actor)
    audit(actor, "shipment.scan", shipment, {"okutma": (None, f"{pallet.code if pallet else raw[:40]}: {result}")})
    return scan


def _round_scans(shipment):
    """Son 'hazırlandı' anından sonraki okutmalar (yeniden açılan sevkiyatta eski okutmalar sayılmaz)."""
    qs = shipment.scans.all()
    return qs.filter(at__gte=shipment.prepared_at) if shipment.prepared_at else qs.none()


def load_check(shipment):
    shipment = Shipment.objects.get(pk=shipment.pk)  # güncel hazırlık zamanı (yeniden açma/geri alma sonrası)
    expected = {ln.pallet_id: ln.pallet for ln in shipment.lines.select_related("pallet")}
    scans = list(_round_scans(shipment).select_related("pallet"))
    ok = {s.pallet_id for s in scans if s.result == ScanResult.OK}
    return {"expected": list(expected.values()), "scanned": [p for pk, p in expected.items() if pk in ok],
            "missing": [p for pk, p in expected.items() if pk not in ok],
            "wrong": [s for s in scans if s.result in (ScanResult.WRONG, ScanResult.UNKNOWN)],
            "duplicates": [s for s in scans if s.result == ScanResult.DUPLICATE], "scans": scans}


@transaction.atomic
def mark_exited(actor, shipment):
    """Araç çıkışı: bundan sonra sevk geri alınamaz; değişiklik ancak iade ile yapılır."""
    shipment = _lock_shipment(shipment)
    check(actor, "shipment.exit", shipment)
    if shipment.status != ShipmentStatus.SHIPPED:
        raise BusinessRuleError("Yalnız sevk onaylı sevkiyat için çıkış kaydedilir.")
    shipment.status, shipment.exited_by, shipment.exited_at = ShipmentStatus.EXITED, actor, timezone.now()
    shipment.save()
    audit(actor, "shipment.exit", shipment, {"status": (ShipmentStatus.SHIPPED, ShipmentStatus.EXITED)})
    return shipment


def _previous_line_status(line):
    from core.models import AuditLog
    for log in AuditLog.objects.filter(action="order.line_status", object_type="orders.orderline",
                                       object_id=str(line.pk)).order_by("-at", "-pk"):
        old, new = (log.changes or {}).get("status", (None, None))
        if new == LineStatus.SHIPPED and old:
            return old
    return LineStatus.APPROVED


@transaction.atomic
def undo_shipment(actor, shipment, reason):
    """Çıkış öncesi kontrollü geri alma: ters stok hareketi, rezervasyon geri, kalem/sipariş durumu yeniden hesap.
    Sevkiyat 'hazırlandı'ya döner (yeniden yükleme kontrolü gerekir). Hareketler silinmez."""
    if not reason:
        raise BusinessRuleError("Geri alma gerekçesi zorunludur.", field="reason")
    shipment = _lock_shipment(shipment)
    check(actor, "shipment.undo", shipment)
    if shipment.status == ShipmentStatus.EXITED:
        raise BusinessRuleError("Araç çıkışı yapılmış sevkiyat geri alınamaz; müşteri iadesi olarak kaydedin.")
    if shipment.status != ShipmentStatus.SHIPPED:
        raise BusinessRuleError("Yalnız sevk onaylı sevkiyat geri alınabilir.")
    if ReturnReceipt.objects.filter(shipment_line__shipment=shipment).exists():
        raise BusinessRuleError("Bu sevkiyata bağlı iade kaydı var; geri alma aynı kutuları ikinci kez stoğa sokar. "
                                "İade kaydını yönetici ile inceleyin.")
    lines = list(shipment.lines.select_related("order_line").order_by("pallet_id"))
    pallets = {p.pk: p for p in _lock(*[ln.pallet for ln in lines])}
    reservations = {r.pk: r for r in Reservation.objects.select_for_update(of=("self",))
                    .filter(pk__in=[ln.reservation_id for ln in lines]).order_by("pk")}
    for ln in lines:
        pallet, res = pallets[ln.pallet_id], reservations[ln.reservation_id]
        if pallet.status == PalletStatus.EMPTY:
            pallet.status = PalletStatus.IN_STOCK
        _move(actor, pallet, MovementKind.SHIPMENT_UNDO, ln.quantity, shipment_line=ln, reason=f"{shipment.code}: {reason}")
        old = res.shipped_quantity
        res.shipped_quantity -= ln.quantity
        res.status = ReservationStatus.ACTIVE
        res.save()
        audit(actor, "reservation.unship", res, {"shipped_quantity": (old, res.shipped_quantity)}, reason=reason)
    shipment.status, shipment.shipped_at, shipment.shipped_by = ShipmentStatus.PREPARED, None, None
    shipment.prepared_at = timezone.now()  # yeniden yükleme kontrolü
    shipment.save()
    audit(actor, "shipment.undo", shipment, {"status": (ShipmentStatus.SHIPPED, ShipmentStatus.PREPARED)}, reason=reason)
    for line in {ln.order_line_id: ln.order_line for ln in lines}.values():
        if line.status == LineStatus.SHIPPED:
            set_line_status(actor, line, _previous_line_status(line), reason=f"{shipment.code} geri alındı")
    order = shipment.order
    if order.status == OrderStatus.DONE:  # türetilmiş durum: yeniden hesap için açığa çek
        order.status = OrderStatus.PARTIAL
        order.save(update_fields=["status"])
    refresh_order_status(actor, order)
    return shipment


def returnable_quantity(shipment_line):
    returned = shipment_line.returns.aggregate(s=Sum("quantity"))["s"] or 0
    return shipment_line.quantity - returned


@transaction.atomic
def receive_return(actor, shipment_line, quantity, reason, location=""):
    """Müşteri iadesi: eski sevkiyat satırına bağlı ayrı bir iade paleti açılır, varsayılan 'kontrol bekliyor'.
    Sipariş/sevk geçmişi değişmez; iade kalite kararı verilmeden sevk edilemez."""
    check(actor, "shipment.manage", shipment_line.shipment)
    _positive(quantity)
    if not reason:
        raise BusinessRuleError("İade nedeni zorunludur.", field="reason")
    line = ShipmentLine.objects.select_for_update(of=("self",)).select_related("shipment", "pallet", "order_line").get(
        pk=shipment_line.pk)
    if line.shipment.status != ShipmentStatus.EXITED:
        raise BusinessRuleError("İade yalnız araç çıkışı yapılmış (müşteriye teslim edilmiş) sevkiyat için alınır; "
                                "çıkış öncesi düzeltme için sevki geri alın.")
    if quantity > returnable_quantity(line):
        raise BusinessRuleError(f"Bu satırdan en fazla {returnable_quantity(line)} kutu iade alınabilir.",
                                field="quantity")
    src = line.pallet
    pallet = Pallet.objects.create(code=next_code("PLT"), product=src.product, work_order=src.work_order,
                                   status=PalletStatus.IN_STOCK, quality_status=QualityStatus.PENDING,
                                   location=location or "İade alanı", created_by=actor, accepted_by=actor,
                                   accepted_at=timezone.now())
    receipt = ReturnReceipt.objects.create(code=next_code("IAD"), shipment_line=line, quantity=quantity, reason=reason,
                                           pallet=pallet, created_by=actor)
    audit(actor, "pallet.create", pallet, {"iade": (None, f"{receipt.code} ← {line.shipment.code}/{src.code}")},
          reason=reason)
    _move(actor, pallet, MovementKind.RETURN_IN, quantity, related=src, reason=f"{receipt.code}: {reason}")
    audit(actor, "shipment.return", line.shipment, {"iade": (None, f"{receipt.code} {quantity} kutu → {pallet.code}")},
          reason=reason)
    audit(actor, "order.return", line.order_line.order, {"iade": (None, f"{receipt.code} {line.order_line.code} "
                                                                       f"{quantity} kutu")}, reason=reason)
    return receipt


# --- Konum, rezervasyon azaltma ---------------------------------------------------------

@transaction.atomic
def change_location(actor, pallet, location, reason=""):
    check(actor, "warehouse.operate")
    location = (location or "").strip()
    if not location:
        raise BusinessRuleError("Yeni depo yeri yazılmalı.", field="location")
    (pallet,) = _lock(pallet)
    if pallet.status == PalletStatus.EMPTY:
        raise BusinessRuleError("Boş paletin yeri değiştirilemez.")
    if pallet.location == location:
        raise BusinessRuleError("Palet zaten bu yerde.", field="location")
    change = PalletLocationChange.objects.create(pallet=pallet, old_location=pallet.location, new_location=location,
                                                 reason=reason, user=actor)
    old = pallet.location
    pallet.location = location
    pallet.save(update_fields=["location", "updated_at"])
    audit(actor, "pallet.location", pallet, {"location": (old, location)}, reason=reason)
    return change


@transaction.atomic
def reduce_reservation(actor, reservation, new_quantity, reason):
    """Rezervasyonu kısmen azaltır; sevk edilen + hazırlanan sevkiyatlardaki miktarın altına inemez.
    Sevk edilenle eşitlenirse rezervasyon kapanır."""
    check(actor, "reservation.manage", reservation)
    if not reason:
        raise BusinessRuleError("Gerekçe zorunludur.", field="reason")
    res = Reservation.objects.select_for_update(of=("self",)).select_related("pallet", "order_line").get(pk=reservation.pk)
    if res.status != ReservationStatus.ACTIVE:
        raise BusinessRuleError("Yalnız aktif rezervasyon azaltılabilir.")
    in_drafts = (ShipmentLine.objects.filter(reservation=res, shipment__status__in=[ShipmentStatus.DRAFT,
                                                                                   ShipmentStatus.PREPARED])
                 .aggregate(s=Sum("quantity"))["s"] or 0)
    floor = res.shipped_quantity + in_drafts
    if new_quantity is None or new_quantity < floor or new_quantity >= res.quantity:
        raise BusinessRuleError(f"Yeni miktar {floor} ile {res.quantity - 1} arasında olmalı (sevk edilen {res.shipped_quantity}, "
                                f"hazırlanan sevkiyatta {in_drafts}).", field="quantity")
    old = res.quantity
    res.quantity = new_quantity
    changes = {"quantity": (old, new_quantity)}
    if new_quantity == 0:
        res.status = ReservationStatus.CANCELLED
        changes["status"] = (ReservationStatus.ACTIVE, res.status)
    elif new_quantity == res.shipped_quantity:
        res.status = ReservationStatus.SHIPPED
        changes["status"] = (ReservationStatus.ACTIVE, res.status)
    res.save()
    audit(actor, "reservation.reduce", res, changes, reason=reason)
    return res


# --- Etiket -------------------------------------------------------------------------------

@transaction.atomic
def request_label_print(actor, pallet, kind=LabelKind.PALLET, copies=1):
    """Basım talebi kaydı. Yeni palet, kimlik veya stok hareketi oluşmaz; yazıcı sonucu ayrıca onaylanır."""
    check(actor, "label.print")
    if kind not in LabelKind.values:
        raise BusinessRuleError("Geçersiz etiket türü.")
    if copies is None or copies < 1 or copies > 500:
        raise BusinessRuleError("Kopya sayısı 1–500 arası olmalı.", field="copies")
    reprint = pallet.label_prints.filter(kind=kind).exists()
    lp = LabelPrint.objects.create(pallet=pallet, kind=kind, is_reprint=reprint, box_count_at_print=pallet.box_count,
                                   copies=copies, user=actor)
    audit(actor, "pallet.label_reprint" if reprint else "pallet.label_print", pallet,
          {"etiket": (None, f"{lp.get_kind_display()} × {copies}")})
    return lp


@transaction.atomic
def confirm_label_print(actor, label_print, printed):
    check(actor, "label.print")
    lp = LabelPrint.objects.select_for_update().get(pk=label_print.pk)
    if lp.status != PrintStatus.REQUESTED:
        raise BusinessRuleError("Bu basım için sonuç zaten bildirildi.")
    lp.status = PrintStatus.PRINTED if printed else PrintStatus.FAILED
    lp.confirmed_by, lp.confirmed_at = actor, timezone.now()
    lp.save()
    audit(actor, "pallet.label_result", lp.pallet, {"etiket": (PrintStatus.REQUESTED, lp.status)})
    return lp


# --- Stok seviyeleri ve ihtiyaç listesi ------------------------------------------------------

@transaction.atomic
def set_stock_level(actor, product, min_boxes, target_boxes):
    check(actor, "stocklevel.manage")
    if min_boxes is None or target_boxes is None or min_boxes < 0 or target_boxes < min_boxes:
        raise BusinessRuleError("Hedef seviye alt seviyeden küçük olamaz; değerler negatif olamaz.", field="target_boxes")
    level, created = StockLevel.objects.select_for_update().get_or_create(
        product=product, defaults={"min_boxes": min_boxes, "target_boxes": target_boxes, "updated_by": actor})
    old = None if created else (level.min_boxes, level.target_boxes)
    level.min_boxes, level.target_boxes, level.updated_by = min_boxes, target_boxes, actor
    level.save()
    audit(actor, "product.stock_level", product, {"seviye": (old, (min_boxes, target_boxes))})
    return level


def planning_needs(user):
    """Sevke uygun serbest stok alt seviyenin altındaki ürünler; açık stok iş emirlerinin kalan hedefi düşülür."""
    from production.lifecycle import OPEN_STATUSES
    from production.models import WorkOrder, WorkOrderPurpose

    from .selectors import stock_breakdown
    free = {r["product"].pk: r["shippable"] for r in stock_breakdown(user)}
    rows = []
    for level in StockLevel.objects.select_related("product__customer").order_by("product__code"):
        available = free.get(level.product_id, 0)
        if available >= level.min_boxes:
            continue
        open_wos = WorkOrder.objects.filter(product=level.product, purpose=WorkOrderPurpose.STOCK,
                                            status__in=OPEN_STATUSES, unit="kutu")
        planned = sum(int(wo.target_quantity) for wo in open_wos)
        rows.append({"level": level, "available": available, "planned": planned,
                     "need": max(level.target_boxes - available - planned, 0), "work_orders": list(open_wos)})
    return rows


# --- İade → yerine gönderim ------------------------------------------------------------------

def replaced_quantity(receipt):
    from orders.models import LineStatus
    return receipt.replacement_lines.exclude(status=LineStatus.CANCELLED).aggregate(s=Sum("quantity"))["s"] or 0


@transaction.atomic
def create_replacement_line(actor, receipt, quantity, reason):
    """İade edilen ürünün yerine gönderilecek kalemi asıl siparişe ekler. Kalem iade kaydına bağlıdır (yeni satış
    sayılmaz); miktarı iade edilenden önceki yerine gönderimler düşülerek fazla olamaz. Normal teknik uygunluk,
    ayırma ve sevk sınırları bu kalemde de geçerlidir."""
    from orders.models import LineStatus, OrderLine, OrderStatus, SalesOrder
    receipt = ReturnReceipt.objects.select_for_update(of=("self",)).select_related(
        "shipment_line__order_line__order", "shipment_line__order_line__approved_revision").get(pk=receipt.pk)
    src = receipt.shipment_line.order_line
    order = SalesOrder.objects.select_for_update(of=("self",)).get(pk=src.order_id)
    check(actor, "order.change", order)
    if not reason:
        raise BusinessRuleError("Gerekçe zorunludur.", field="reason")
    if quantity is None or int(quantity) != quantity or quantity <= 0:
        raise BusinessRuleError("Miktar pozitif tam sayı olmalı.", field="quantity")
    left = receipt.quantity - replaced_quantity(receipt)
    if quantity > left:
        raise BusinessRuleError(f"{receipt.code} için en fazla {left} kutu yerine gönderilebilir "
                                f"(iade {receipt.quantity}, önceki yerine gönderim {receipt.quantity - left}).",
                                field="quantity")
    if order.status == OrderStatus.CANCELLED:
        raise BusinessRuleError("İptal edilmiş siparişe yerine gönderim eklenemez.")
    line_no = (order.lines.aggregate(m=Max("line_no"))["m"] or 0) + 1
    line = OrderLine.objects.create(
        order=order, line_no=line_no, product=src.product, quantity=quantity, unit=src.unit, due_date=src.due_date,
        replacement_for=receipt, approved_revision=src.approved_revision, approved_by=src.approved_by,
        approved_at=src.approved_at,
        status=LineStatus.APPROVED if src.approved_revision_id else LineStatus.WAITING_PLANNING,
        planning_note=f"Yerine gönderim: {receipt.code} ({src.code})")
    audit(actor, "order.replacement", line, {"iade": (None, receipt.code), "kaynak_kalem": (None, src.code),
                                             "quantity": (None, quantity)}, reason=reason, code=line.code)
    refresh_order_status(actor, order)
    return line


def _net_delivered(line):
    """Kalemin müşteride kalan neti: sevk − bu kalemin sevkinden iade + yerine gönderim kalemlerinin neti (iade
    edilen yerine gönderim de düşülür)."""
    from orders.services import shipped_quantity
    receipts = ReturnReceipt.objects.filter(shipment_line__order_line=line).prefetch_related("replacement_lines")
    returned = sum(r.quantity for r in receipts)
    return shipped_quantity(line) - returned + sum(_net_delivered(ln) for r in receipts for ln in r.replacement_lines.all())


def line_fulfilment(line):
    """Asıl kalem için ilk sevk, iade, yerine gönderim (sevk edilen) ve müşteride kalan net (kutu)."""
    from orders.services import shipped_quantity
    shipped = shipped_quantity(line)
    receipts = ReturnReceipt.objects.filter(shipment_line__order_line=line)
    returned = receipts.aggregate(s=Sum("quantity"))["s"] or 0
    repl_lines = [ln for r in receipts.prefetch_related("replacement_lines") for ln in r.replacement_lines.all()]
    replaced_planned = sum(ln.quantity for ln in repl_lines if ln.status != "iptal")
    replaced_shipped = sum(shipped_quantity(ln) for ln in repl_lines)
    return {"shipped": shipped, "returned": returned, "replacement_planned": replaced_planned,
            "replacement_shipped": replaced_shipped, "net": _net_delivered(line),
            "receipts": list(receipts.select_related("shipment_line__shipment", "pallet"))}
