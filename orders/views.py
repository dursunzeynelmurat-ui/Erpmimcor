from django.core.paginator import Paginator
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import can, check, get_scoped_or_404, require_perm, scoped
from catalog.models import Customer
from core.forms import ConfirmForm, ReasonForm
from core.services import history_for
from core.viewutils import run_form_action

from . import services
from .forms import (ApproveLineForm, CompatibilityForm, LineAddForm, LineChangeForm, OrderCreateForm, OverDeliveryForm,
                    products_for)
from .models import LineStatus, OrderLine, OrderStatus, SalesOrder
from .selectors import line_progress


def order_list(request):
    check(request.user, "order.view")
    orders = scoped(SalesOrder.objects.all(), request.user, "order.view").select_related("customer").prefetch_related("lines__product")
    if request.GET.get("musteri", "").isdigit():
        orders = orders.filter(customer_id=request.GET["musteri"])
    status = request.GET.get("durum", "")
    if status == "arsiv":
        orders = orders.filter(archived_at__isnull=False)
    else:
        orders = orders.filter(archived_at__isnull=True)
        if status in OrderStatus.values:
            orders = orders.filter(status=status)
    q = request.GET.get("q", "").strip()
    if q:
        orders = orders.filter(code__icontains=q) | orders.filter(customer__name__icontains=q)
    page = Paginator(orders.order_by("-created_at", "-pk"), 30).get_page(request.GET.get("page"))
    return render(request, "orders/order_list.html", {"orders": page, "page_obj": page, "q": q, "status": status,
                                                      "statuses": OrderStatus.choices})


@require_perm("order.create")
def order_create(request):
    form = OrderCreateForm(request.POST or None, user=request.user)
    if request.method == "POST":
        def create(d):
            return services.create_order(
                request.user, customer=d["customer"], order_date=d["order_date"], due_date=d["due_date"],
                customer_ref=d["customer_ref"], notes=d["notes"],
                lines=[{"product": d["product"], "quantity": d["quantity"], "unit": d["unit"]}])
        resp = run_form_action(request, form, action="order.create", fn=create,
                               success_message=lambda o: f"Sipariş oluşturuldu: {o.code} (taslak). Kalemleri kontrol edip onaylayın.",
                               url_for=lambda o: reverse("orders:order_detail", args=[o.pk]))
        if resp:
            return resp
    return render(request, "core/form_page.html", {"form": form, "title": "Yeni satış siparişi", "submit": "Siparişi kaydet",
                                                   "back": reverse("orders:order_list")})


@require_GET
@require_perm("order.create")
def customer_products(request, pk):
    """Sipariş formu için müşterinin aktif ürün kodları. Kayıtta sunucu ayrıca doğrular."""
    # Kapsam dışı (erişim verilmemiş/süresi dolmuş) müşteri 404 döner; ürün listesi sızmaz.
    customer = get_object_or_404(scoped(Customer.objects.active(), request.user, "order.create"), pk=pk)
    resp = JsonResponse({"products": [{"id": p.pk, "label": str(p)} for p in products_for(customer)]})
    resp["Cache-Control"] = "no-store"
    return resp


def order_detail(request, pk):
    order = get_scoped_or_404(SalesOrder.objects.select_related("customer", "created_by"), request.user, "order.view", pk=pk)
    form = LineAddForm(order, request.POST or None) if can(request.user, "order.create", order) else None
    if request.method == "POST":
        check(request.user, "order.create", order)
        resp = run_form_action(
            request, form, action="order.line_add",
            fn=lambda d: services.add_line(request.user, order, product=d["product"], quantity=d["quantity"],
                                           unit=d["unit"], due_date=d["due_date"]),
            success_message=lambda line: f"Kalem eklendi: {line.code}",
            url_for=lambda line: reverse("orders:order_detail", args=[order.pk]))
        if resp:
            return resp
    lines = order.lines.select_related("product", "approved_revision")
    from warehouse.selectors import order_warehouse_context
    return render(request, "orders/order_detail.html", {
        "order": order, "progress": [line_progress(line) for line in lines], "form": form,
        "history": history_for(order), **order_warehouse_context(order, lines, request.user),
        "commercial": can(request.user, "customer.commercial.view", order.customer),
        "can_approve": can(request.user, "order.approve", order), "can_change": can(request.user, "order.change", order),
        "can_cancel": can(request.user, "order.cancel", order), "can_create": can(request.user, "order.create", order),
    })


def progress_list(request):
    """Satış ekranı: açık kalemlerin gerçekleşen miktarı, aşaması, son güncellemesi (salt okunur)."""
    check(request.user, "order.view")
    lines = (scoped(OrderLine.objects.all(), request.user, "order.view").select_related("order__customer", "product")
             .exclude(status__in=[LineStatus.CANCELLED, LineStatus.SHIPPED]).order_by("order__due_date", "order__code"))
    return render(request, "orders/progress_list.html", {"rows": [line_progress(line) for line in lines]})


@require_perm("planning.approve")
def planning_queue(request):
    waiting = OrderLine.objects.select_related("order__customer", "product").filter(
        status=LineStatus.WAITING_PLANNING, order__status__in=[OrderStatus.OPEN, OrderStatus.PARTIAL])
    approved = OrderLine.objects.select_related("order__customer", "product", "approved_revision").filter(
        status=LineStatus.APPROVED)
    return render(request, "orders/planning_queue.html", {"waiting": waiting, "approved": approved})


@require_perm("planning.approve")
def line_approve(request, pk):
    line = get_object_or_404(OrderLine.objects.select_related("order__customer", "product"), pk=pk)
    form = ApproveLineForm(line, request.POST or None)
    if request.method == "POST":
        resp = run_form_action(
            request, form, action="order.line_approve",
            fn=lambda d: services.approve_line(request.user, line, d["revision"], d["note"]),
            success_message=lambda ln: f"{ln.code} teknik olarak onaylandı (Rev.{ln.approved_revision.rev_no}).",
            url_for=lambda ln: reverse("orders:planning_queue"))
        if resp:
            return resp
    revision = line.product.current_revision
    return render(request, "orders/line_approve.html", {"line": line, "form": form, "revision": revision,
                                                        "history": history_for(line)})


# --- Aşama 2: sipariş yaşam döngüsü ---------------------------------------------------

def _order_url(order):
    return reverse("orders:order_detail", args=[order.pk])


def _get_order(request, pk, action="order.view"):
    return get_scoped_or_404(SalesOrder.objects.select_related("customer"), request.user, action, pk=pk)


def _post(request, order, form, action, fn, message, url_for=None):
    resp = run_form_action(request, form, action=action, fn=fn, success_message=message,
                           url_for=url_for or (lambda _: _order_url(order)))
    return resp or redirect(_order_url(order))


@require_POST
@require_perm("order.approve")
def order_approve(request, pk):
    order = _get_order(request, pk)
    return _post(request, order, ConfirmForm(request.POST), "order.approve",
                 lambda d: services.approve_order(request.user, order), "Sipariş onaylandı; kalemler planlamaya düştü.")


@require_POST
@require_perm("order.change")
def order_delete(request, pk):
    order = _get_order(request, pk)
    return _post(request, order, ReasonForm(request.POST), "order.delete_draft",
                 lambda d: services.delete_draft(request.user, order, d["reason"]),
                 f"Taslak sipariş {order.code} silindi (işlem geçmişinde kayıtlı).",
                 url_for=lambda _: reverse("orders:order_list"))


@require_POST
@require_perm("order.create")
def order_copy(request, pk):
    order = _get_order(request, pk)
    return _post(request, order, ConfirmForm(request.POST), "order.copy",
                 lambda d: services.copy_order(request.user, order),
                 lambda o: f"Kopya taslak sipariş {o.code} açıldı. Miktar ve terminleri onaylayın; teknik onay yeniden "
                           "yapılacak.", url_for=_order_url)


@require_POST
@require_perm("order.cancel")
def order_cancel(request, pk):
    order = _get_order(request, pk)
    return _post(request, order, ReasonForm(request.POST), "order.cancel",
                 lambda d: services.cancel_order(request.user, order, d["reason"]),
                 "Sipariş iptal işlendi; rezervasyonlar bırakıldı.")


@require_POST
@require_perm("order.change")
def order_archive(request, pk):
    order = _get_order(request, pk)
    return _post(request, order, ConfirmForm(request.POST), "order.archive",
                 lambda d: services.archive_order(request.user, order), "Sipariş arşivlendi.")


@require_perm("order.change")
def line_change(request, pk):
    line = get_object_or_404(OrderLine.objects.select_related("order__customer", "product"), pk=pk)
    order = _get_order(request, line.order_id)
    form = LineChangeForm(line, request.POST or None)
    if request.method == "POST":
        resp = run_form_action(
            request, form, action="order.line_change",
            fn=lambda d: services.change_line(request.user, line, quantity=d["quantity"], due_date=d["due_date"],
                                              reason=d["reason"], confirm_impacts=d["confirm_impacts"]),
            success_message=lambda ln: f"{ln.code} güncellendi.", url_for=lambda _: _order_url(order))
        if resp:
            return resp
    return render(request, "orders/line_change.html", {"line": line, "form": form,
                                                       "impacts": services.line_impacts(line),
                                                       "shipped": services.shipped_quantity(line)})


@require_POST
@require_perm("order.cancel")
def line_cancel(request, pk):
    line = get_object_or_404(OrderLine.objects.select_related("order"), pk=pk)
    order = _get_order(request, line.order_id)
    return _post(request, order, ReasonForm(request.POST), "order.line_cancel",
                 lambda d: services.cancel_line(request.user, line, d["reason"]), f"{line.code} iptal edildi.")


@require_perm("exception.approve")
def line_exception(request, pk, kind):
    line = get_object_or_404(OrderLine.objects.select_related("order__customer", "product", "approved_revision"), pk=pk)
    order = _get_order(request, line.order_id)
    if kind == "uyumluluk":
        form = CompatibilityForm(line, request.POST or None)
        fn = lambda d: services.approve_compatibility(request.user, line, d["revision"], d["reason"])  # noqa: E731
        title, msg = "Teknik eşdeğerlik onayı", "Teknik eşdeğerlik onayı kaydedildi (bu kalem ve hedef revizyon için)."
    elif kind == "fazla-teslim":
        form = OverDeliveryForm(request.POST or None)
        fn = lambda d: services.allow_over_delivery(request.user, line, d["extra"], d["reason"])  # noqa: E731
        title, msg = "Fazla teslim izni", "Fazla teslim izni kaydedildi."
    else:
        raise Http404
    if request.method == "POST":
        resp = run_form_action(request, form, action=f"order.{kind}", fn=fn, success_message=msg,
                               url_for=lambda _: _order_url(order))
        if resp:
            return resp
    rev = line.approved_revision
    return render(request, "core/form_page.html", {
        "form": form, "title": f"{line.code}: {title}", "submit": "Kaydet", "back": _order_url(order),
        "intro": (f"Kalem Rev.{rev.rev_no} ile onaylı." if rev else "") + " Kayıt gerekçesiyle işlem geçmişine yazılır."})


@require_POST
@require_perm("exception.approve")
def compat_revoke(request, pk):
    from .models import CompatibilityException
    exc = get_object_or_404(CompatibilityException.objects.select_related("order_line"), pk=pk)
    order = _get_order(request, exc.order_line.order_id)
    return _post(request, order, ReasonForm(request.POST), "order.compatibility_revoke",
                 lambda d: services.revoke_compatibility(request.user, exc, d["reason"]),
                 "Eşdeğerlik onayı geri alındı; ayrılmış/hazırlanan stok sevkte yeniden kontrol edilir.")
