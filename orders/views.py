from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from accounts.permissions import can, check, require_perm
from core.services import history_for
from core.viewutils import run_form_action

from . import services
from .forms import ApproveLineForm, LineAddForm, OrderCreateForm
from .models import LineStatus, OrderLine, SalesOrder
from .selectors import line_progress


def order_list(request):
    check(request.user, "view.orders")
    orders = SalesOrder.objects.select_related("customer").prefetch_related("lines__product")
    q = request.GET.get("q", "").strip()
    if q:
        orders = orders.filter(code__icontains=q) | orders.filter(customer__name__icontains=q)
    return render(request, "orders/order_list.html", {"orders": orders[:200], "q": q})


@require_perm("order.create")
def order_create(request):
    form = OrderCreateForm(request.POST or None)
    if request.method == "POST":
        def create(d):
            return services.create_order(
                request.user, customer=d["customer"], order_date=d["order_date"], due_date=d["due_date"],
                customer_ref=d["customer_ref"], notes=d["notes"],
                lines=[{"product": d["product"], "quantity": d["quantity"], "unit": d["unit"]}])
        resp = run_form_action(request, form, action="order.create", fn=create,
                               success_message=lambda o: f"Sipariş oluşturuldu: {o.code}",
                               url_for=lambda o: reverse("orders:order_detail", args=[o.pk]))
        if resp:
            return resp
    return render(request, "core/form_page.html", {"form": form, "title": "Yeni satış siparişi", "submit": "Siparişi kaydet",
                                                   "back": reverse("orders:order_list")})


def order_detail(request, pk):
    check(request.user, "view.orders")
    order = get_object_or_404(SalesOrder.objects.select_related("customer", "created_by"), pk=pk)
    form = LineAddForm(order, request.POST or None) if can(request.user, "order.create") else None
    if request.method == "POST":
        check(request.user, "order.create")
        resp = run_form_action(
            request, form, action="order.line_add",
            fn=lambda d: services.add_line(request.user, order, product=d["product"], quantity=d["quantity"],
                                           unit=d["unit"], due_date=d["due_date"]),
            success_message=lambda line: f"Kalem eklendi: {line.code}",
            url_for=lambda line: reverse("orders:order_detail", args=[order.pk]))
        if resp:
            return resp
    lines = order.lines.select_related("product", "approved_revision")
    return render(request, "orders/order_detail.html", {
        "order": order, "progress": [line_progress(line) for line in lines], "form": form,
        "history": history_for(order),
    })


def progress_list(request):
    """Satış ekranı: açık kalemlerin gerçekleşen miktarı, aşaması, son güncellemesi (salt okunur)."""
    check(request.user, "view.orders")
    lines = (OrderLine.objects.select_related("order__customer", "product")
             .exclude(status__in=[LineStatus.CANCELLED, LineStatus.SHIPPED]).order_by("order__due_date", "order__code"))
    return render(request, "orders/progress_list.html", {"rows": [line_progress(line) for line in lines]})


@require_perm("planning.approve")
def planning_queue(request):
    waiting = OrderLine.objects.select_related("order__customer", "product").filter(status=LineStatus.WAITING_PLANNING)
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
