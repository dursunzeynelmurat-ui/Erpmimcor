from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from accounts.permissions import check
from accounts.scope import customer_in_scope
from catalog.customers import order_snapshot
from catalog.models import Customer, RecipeRevision
from core.exceptions import BusinessRuleError
from core.services import audit, next_code

from .models import LineStatus, OrderLine, OrderStatus, SalesOrder


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
    if order.status != OrderStatus.OPEN:
        raise BusinessRuleError("Kapalı/iptal siparişe kalem eklenemez.")
    _validate_product(order, product)
    if quantity <= 0:
        raise BusinessRuleError("Miktar sıfırdan büyük olmalı.", field="quantity")
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
    if line.status not in (LineStatus.WAITING_PLANNING, LineStatus.APPROVED):
        raise BusinessRuleError("Bu kalem için iş emri açılmış; onay değiştirilemez.")
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
