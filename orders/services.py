from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from accounts.permissions import check
from accounts.scope import customer_in_scope
from catalog.customers import order_snapshot
from catalog.models import Customer, RecipeRevision
from core.exceptions import BusinessRuleError
from core.services import audit, next_code, num

from catalog.models import Unit

from .models import LineStatus, OrderLine, OrderStatus, SalesOrder

DISCRETE_UNITS = (Unit.BOX, Unit.PIECE, Unit.ROLL)


def _validate_product(order, product):
    if product.customer_id != order.customer_id:
        raise BusinessRuleError(
            f"{product.code} ürün kodu {order.customer.name} müşterisine ait değil. "
            "Her ürün kodu tek müşteriye bağlıdır.", field="product")
    if not product.is_active:
        raise BusinessRuleError("Pasif ürün siparişe eklenemez.", field="product")


@transaction.atomic
def create_order(actor, *, customer, order_date, due_date=None, customer_ref="", notes="", lines=()):
    """Sipariş kodu ilk kayıtta üretilir. lines: [{product, quantity, unit, due_date}]"""
    check(actor, "order.create")
    customer = Customer.objects.get(pk=customer.pk)  # anlık kopya güncel karttan alınır
    if not customer.is_active:
        raise BusinessRuleError("Pasif müşteriye sipariş açılamaz.", field="customer")
    if not customer_in_scope(actor, "order.create", customer):
        raise BusinessRuleError("Bu müşteriye erişiminiz yok.", field="customer")
    if customer.po_required and not customer_ref:
        raise BusinessRuleError("Bu müşteri için müşteri PO/referans numarası zorunludur.", field="customer_ref")
    order = SalesOrder.objects.create(code=next_code("SIP"), customer=customer, order_date=order_date,
                                      due_date=due_date, customer_ref=customer_ref, notes=notes, created_by=actor,
                                      **order_snapshot(customer))
    audit(actor, "order.create", order, {"customer": (None, customer), "order_date": (None, order_date)})
    for line in lines:
        add_line(actor, order, **line)
    return order


@transaction.atomic
def add_line(actor, order, *, product, quantity, unit, due_date=None):
    check(actor, "order.create", order)
    order = SalesOrder.objects.select_for_update(of=("self",)).select_related("customer").get(pk=order.pk)
    if order.status not in (OrderStatus.DRAFT, OrderStatus.OPEN, OrderStatus.PARTIAL):
        raise BusinessRuleError("Kapalı/iptal siparişe kalem eklenemez.")
    _validate_product(order, product)
    validate_quantity(quantity, unit)
    line_no = (order.lines.aggregate(m=Max("line_no"))["m"] or 0) + 1
    line = OrderLine.objects.create(order=order, line_no=line_no, product=product, quantity=quantity, unit=unit,
                                    due_date=due_date or order.due_date)
    audit(actor, "order.line_add", line, {"product": (None, product.code), "quantity": (None, quantity),
                                          "unit": (None, unit)}, code=line.code)
    return line


@transaction.atomic
def approve_line(actor, line, revision, note=""):
    """Planlama teknik bilgileri/reçeteyi onaylar; onaylanan revizyon kaleme sabitlenir."""
    check(actor, "planning.approve")
    line = OrderLine.objects.select_for_update(of=("self",)).select_related("order", "product").get(pk=line.pk)
    if line.order.status in (OrderStatus.DRAFT, OrderStatus.CANCELLED):
        raise BusinessRuleError("Taslak veya iptal edilmiş siparişin kalemi teknik onaya alınamaz.")
    if line.status not in (LineStatus.WAITING_PLANNING, LineStatus.APPROVED):
        raise BusinessRuleError("Bu kalem için üretim başlamış; onay değiştirilemez.")
    from production.models import WorkOrderStatus
    active = line.work_orders.filter(status__in=[WorkOrderStatus.PLANNED, WorkOrderStatus.STARTED])
    if active.exists() and line.approved_revision_id != revision.pk:
        raise BusinessRuleError(
            "Serbest bırakılmış iş emri var: " + ", ".join(active.values_list("code", flat=True)) +
            ". Farklı revizyonla yeniden onaydan önce planlama bu iş emirlerini geri çekmeli; geri çekilen taslak iş "
            "emirleri 'revizyonu kaleme eşitle' ile güncellenir (bulgu 5).")
    revision = RecipeRevision.objects.select_related("recipe").get(pk=revision.pk)
    if revision.recipe.product_id != line.product_id:
        raise BusinessRuleError("Seçilen reçete revizyonu bu ürüne ait değil.", field="revision")
    old = (line.approved_revision, line.status)
    line.approved_revision = revision
    line.approved_by = actor
    line.approved_at = timezone.now()
    line.planning_note = note
    line.status = LineStatus.APPROVED
    line.save()
    audit(actor, "order.line_approve", line, {"approved_revision": (old[0], revision), "status": (old[1], line.status)},
          reason=note, code=line.code)
    return line


@transaction.atomic
def set_line_status(actor, line, status, reason=""):
    line = OrderLine.objects.select_for_update().get(pk=line.pk)
    if line.status != status:
        old = line.status
        line.status = status
        line.save(update_fields=["status", "updated_at"])
        audit(actor, "order.line_status", line, {"status": (old, status)}, reason=reason, code=line.code)
    return line


# --- Yaşam döngüsü --------------------------------------------------------------------------

def validate_quantity(quantity, unit, field="quantity"):
    """Bölünmez birimlerde (kutu/adet/rulo) tam sayı (bulgu 10)."""
    if quantity is None or quantity <= 0:
        raise BusinessRuleError("Miktar sıfırdan büyük olmalı.", field=field)
    if unit in DISCRETE_UNITS and quantity != quantity.to_integral_value():
        raise BusinessRuleError(f"{Unit(unit).label} birimi bölünemez; tam sayı girin.", field=field)


def _lock_order(order):
    return SalesOrder.objects.select_for_update(of=("self",)).select_related("customer").get(pk=order.pk)


def shipped_quantity(line):
    from django.db.models import Sum
    from warehouse.models import ShipmentLine, ShipmentStatus
    return ShipmentLine.objects.filter(order_line=line, shipment__status=ShipmentStatus.SHIPPED).aggregate(
        s=Sum("quantity"))["s"] or 0


def line_impacts(line):
    """Kalem değişikliğinin etkilediği kayıtlar (kullanıcıya gösterilir, sessiz değişiklik yok)."""
    from production.models import WorkOrderStatus
    from warehouse.models import ReservationStatus, ShipmentStatus
    impacts = []
    for wo in line.work_orders.exclude(status__in=[WorkOrderStatus.CLOSED, WorkOrderStatus.CANCELLED]):
        impacts.append(f"İş emri {wo.code} ({wo.get_status_display()}, hedef {num(wo.target_quantity)}) — hedefi "
                       "kendiliğinden değişmez; planlama gözden geçirmeli")
    for r in line.reservations.filter(status=ReservationStatus.ACTIVE).select_related("pallet"):
        impacts.append(f"Rezervasyon {r.pallet.code}: {r.open_quantity} kutu")
    for sl in line.shipment_lines.filter(shipment__status=ShipmentStatus.DRAFT).select_related("shipment"):
        impacts.append(f"Hazırlanan sevkiyat {sl.shipment.code}: {sl.quantity} kutu")
    return impacts


@transaction.atomic
def approve_order(actor, order):
    from catalog.customers import require_ready
    order = _lock_order(order)
    check(actor, "order.approve", order)
    if order.status != OrderStatus.DRAFT:
        raise BusinessRuleError("Yalnız taslak sipariş onaylanır.")
    lines = list(order.lines.exclude(status=LineStatus.CANCELLED))
    if not lines:
        raise BusinessRuleError("Kalemi olmayan sipariş onaylanamaz.")
    pending = [ln.code for ln in lines if ln.needs_review]
    if pending:
        raise BusinessRuleError("Kopyadan gelen kalemlerin miktar/termini onaylanmalı: " + ", ".join(pending))
    if order.customer.po_required and not order.customer_ref:
        raise BusinessRuleError("Bu müşteri için müşteri PO/referans numarası zorunludur.")
    require_ready(order.customer, "siparis")
    order.status, order.approved_by, order.approved_at = OrderStatus.OPEN, actor, timezone.now()
    order.save()
    audit(actor, "order.approve", order, {"status": (OrderStatus.DRAFT, OrderStatus.OPEN)})
    return order


@transaction.atomic
def delete_draft(actor, order, reason):
    """Hareketsiz taslak sipariş silinebilir; silen kişi ve içerik işlem geçmişinde kalır."""
    order = _lock_order(order)
    check(actor, "order.change", order)
    if order.status != OrderStatus.DRAFT:
        raise BusinessRuleError("Yalnız taslak sipariş silinebilir; onaylı sipariş iptal edilir.")
    if not reason:
        raise BusinessRuleError("Silme gerekçesi zorunludur.", field="reason")
    lines = list(order.lines.all())
    if any(ln.work_orders.exists() or ln.reservations.exists() or ln.shipment_lines.exists() for ln in lines):
        raise BusinessRuleError("Bağlı iş emri/rezervasyon/sevkiyat olan sipariş silinemez; iptal edin.")
    audit(actor, "order.delete_draft", order, {"kalemler": ([f"{ln.code} {ln.product.code} {num(ln.quantity)} "
                                                             f"{ln.unit}" for ln in lines], None)}, reason=reason)
    for ln in lines:
        ln.delete()
    order.delete()


@transaction.atomic
def change_line(actor, line, *, quantity, due_date, reason, confirm_impacts=False):
    line = OrderLine.objects.select_for_update(of=("self",)).select_related("order__customer").get(pk=line.pk)
    check(actor, "order.change", line.order)
    if line.order.status in (OrderStatus.CANCELLED, OrderStatus.DONE) or line.status in (LineStatus.CANCELLED,
                                                                                          LineStatus.SHIPPED):
        raise BusinessRuleError("Kapanmış/iptal kalem değiştirilemez.")
    validate_quantity(quantity, line.unit)
    if line.unit == Unit.BOX:
        from warehouse.models import ReservationStatus
        committed = shipped_quantity(line) + sum(r.open_quantity for r in line.reservations.filter(
            status=ReservationStatus.ACTIVE))
        if quantity < committed:
            raise BusinessRuleError(f"Kalemde sevk edilmiş + ayrılmış {committed} kutu var; miktar bunun altına "
                                    "inemez. Önce rezervasyonu azaltın.", field="quantity")
    impacts = line_impacts(line) if quantity != line.quantity or due_date != line.due_date else []
    if impacts and not confirm_impacts:
        raise BusinessRuleError("Bu değişiklik şu kayıtları etkiler: " + "; ".join(impacts) +
                                ". Gördüğünüzü onaylayıp tekrar kaydedin.", field="confirm_impacts")
    if line.order.status != OrderStatus.DRAFT and not reason:
        raise BusinessRuleError("Onaylı siparişte değişiklik gerekçesi zorunludur.", field="reason")
    changes = {}
    for f, new in (("quantity", quantity), ("due_date", due_date)):
        if getattr(line, f) != new:
            changes[f] = (getattr(line, f), new)
            setattr(line, f, new)
    if line.needs_review:
        changes["needs_review"] = (True, False)
        line.needs_review = False
    line.save()
    if changes:
        audit(actor, "order.line_change", line, changes, reason=reason + (" | Etkilenen: " + "; ".join(impacts)
                                                                          if impacts else ""), code=line.code)
    return line


@transaction.atomic
def copy_order(actor, order):
    """Kontrollü tekrar sipariş: müşteri, ürün ve birim taşınır; tarih bugün, termin boş, miktar onay bekler,
    teknik onay yeniden gerekir. Eski revizyon körlemesine taşınmaz."""
    check(actor, "order.create", order)
    src = SalesOrder.objects.select_related("customer").get(pk=order.pk)
    lines = [ln for ln in src.lines.select_related("product") if ln.product.is_active and ln.status != LineStatus.CANCELLED]
    if not lines:
        raise BusinessRuleError("Kopyalanacak aktif ürünlü kalem yok.")
    new = create_order(actor, customer=src.customer, order_date=timezone.localdate(), customer_ref="",
                       notes=f"{src.code} siparişinden kopyalandı.")
    new.copied_from = src
    new.save(update_fields=["copied_from"])
    for ln in lines:
        copy = add_line(actor, new, product=ln.product, quantity=ln.quantity, unit=ln.unit, due_date=None)
        copy.needs_review = True
        copy.save(update_fields=["needs_review"])
    audit(actor, "order.copy", new, {"kaynak": (None, src.code)})
    return new


@transaction.atomic
def cancel_line(actor, line, reason):
    from production.models import WorkOrderStatus
    from warehouse.models import ReservationStatus, ShipmentStatus
    line = OrderLine.objects.select_for_update(of=("self",)).select_related("order").get(pk=line.pk)
    check(actor, "order.cancel", line.order)
    if not reason:
        raise BusinessRuleError("İptal gerekçesi zorunludur.", field="reason")
    if line.status == LineStatus.CANCELLED:
        raise BusinessRuleError("Kalem zaten iptal.")
    if shipped_quantity(line):
        raise BusinessRuleError("Kısmen sevk edilmiş kalem iptal edilemez; kalan miktarı azaltın.")
    open_wos = line.work_orders.exclude(status__in=[WorkOrderStatus.CLOSED, WorkOrderStatus.CANCELLED])
    if open_wos.exists():
        raise BusinessRuleError("Açık iş emri var: " + ", ".join(open_wos.values_list("code", flat=True)) +
                                ". Planlama iş emrini iptal etmeden kalem iptal edilemez.")
    if line.shipment_lines.filter(shipment__status=ShipmentStatus.DRAFT).exists():
        raise BusinessRuleError("Kalem hazırlanan bir sevkiyatta; önce sevkiyattan çıkarın.")
    released = []
    for r in line.reservations.select_for_update().filter(status=ReservationStatus.ACTIVE):
        r.status, r.cancel_reason = ReservationStatus.CANCELLED, f"Kalem iptal: {reason}"
        r.save()
        released.append(f"{r.pallet.code}:{r.quantity}")
        audit(actor, "reservation.cancel", r, {"status": (ReservationStatus.ACTIVE, r.status)}, reason=r.cancel_reason)
    old = line.status
    line.status, line.cancel_reason = LineStatus.CANCELLED, reason
    line.save()
    audit(actor, "order.line_cancel", line, {"status": (old, line.status), "serbest_rezervasyon": (None, released)},
          reason=reason, code=line.code)
    refresh_order_status(actor, line.order)
    return line


@transaction.atomic
def cancel_order(actor, order, reason):
    order = _lock_order(order)
    check(actor, "order.cancel", order)
    if not reason:
        raise BusinessRuleError("İptal gerekçesi zorunludur.", field="reason")
    if order.status in (OrderStatus.CANCELLED, OrderStatus.DONE):
        raise BusinessRuleError("Sipariş zaten kapanmış.")
    for line in order.lines.exclude(status__in=[LineStatus.CANCELLED, LineStatus.SHIPPED]):
        if not shipped_quantity(line):
            cancel_line(actor, line, reason)
    order.refresh_from_db()
    order.cancel_reason = reason
    order.save(update_fields=["cancel_reason"])
    refresh_order_status(actor, order)
    return order


def refresh_order_status(actor, order):
    """Üst durum kalemlerden türetilir (bulgu 4): tümü iptal → iptal; iptal dışı tümü sevk → tamamlandı;
    sevk varsa → kısmi sevk; taslak/onaylı korunur."""
    order = _lock_order(order)
    lines = list(order.lines.all())
    live = [ln for ln in lines if ln.status != LineStatus.CANCELLED]
    if not live:
        new = OrderStatus.CANCELLED
    elif all(ln.status == LineStatus.SHIPPED for ln in live):
        new = OrderStatus.DONE
    elif any(shipped_quantity(ln) for ln in live):
        new = OrderStatus.PARTIAL
    else:
        new = order.status if order.status in (OrderStatus.DRAFT, OrderStatus.OPEN) else OrderStatus.OPEN
    if new != order.status:
        old = order.status
        order.status = new
        order.save(update_fields=["status", "updated_at"])
        audit(actor, "order.status", order, {"status": (old, new)})
    return order


@transaction.atomic
def archive_order(actor, order):
    order = _lock_order(order)
    check(actor, "order.change", order)
    if order.status not in (OrderStatus.DONE, OrderStatus.CANCELLED):
        raise BusinessRuleError("Yalnız tamamlanmış veya iptal sipariş arşivlenir.")
    order.archived_at = timezone.now()
    order.save(update_fields=["archived_at"])
    audit(actor, "order.archive", order, {"archived": (None, True)})
    return order
