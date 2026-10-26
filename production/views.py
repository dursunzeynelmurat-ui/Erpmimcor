from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.permissions import can, check, require_perm
from core.forms import ConfirmForm
from core.services import history_for
from core.viewutils import run_form_action
from orders.models import OrderLine

from . import services
from .forms import WorkOrderForm
from .models import WorkOrder, WorkOrderOperation, WorkOrderStatus


def _wo_url(wo):
    return reverse("production:workorder_detail", args=[wo.pk])


@require_perm("workorder.create")
def workorder_create_for_line(request, line_pk):
    line = get_object_or_404(OrderLine.objects.select_related("order__customer", "product", "approved_revision"), pk=line_pk)
    form = WorkOrderForm(request.POST or None, initial={"target_quantity": line.quantity, "unit": line.unit})
    if request.method == "POST":
        resp = run_form_action(
            request, form, action="workorder.create",
            fn=lambda d: services.create_work_order(
                request.user, order_line=line, machines=d["machines"], target_quantity=d["target_quantity"],
                unit=d["unit"], planned_start=d["planned_start"], notes=d["notes"]),
            success_message=lambda wo: f"İş emri oluşturuldu: {wo.code}", url_for=_wo_url)
        if resp:
            return resp
    return render(request, "production/workorder_form.html", {"form": form, "line": line})


@require_perm("workorder.create")
def workorder_create_stock(request):
    form = WorkOrderForm(request.POST or None, stock=True)
    if request.method == "POST":
        resp = run_form_action(
            request, form, action="workorder.create",
            fn=lambda d: services.create_work_order(
                request.user, product=d["product"], revision=d["revision"], machines=d["machines"],
                target_quantity=d["target_quantity"], unit=d["unit"], planned_start=d["planned_start"], notes=d["notes"]),
            success_message=lambda wo: f"Stok iş emri oluşturuldu: {wo.code}", url_for=_wo_url)
        if resp:
            return resp
    return render(request, "production/workorder_form.html", {"form": form, "line": None})


def workorder_list(request):
    check(request.user, "view.production")
    status = request.GET.get("durum", "aktif")
    qs = WorkOrder.objects.select_related("product__customer", "order_line__order").prefetch_related("operations__machine")
    if status == "aktif":
        qs = qs.filter(status__in=[WorkOrderStatus.PLANNED, WorkOrderStatus.STARTED])
    elif status in WorkOrderStatus.values:
        qs = qs.filter(status=status)
    return render(request, "production/workorder_list.html", {"work_orders": qs[:200], "status": status,
                                                              "statuses": WorkOrderStatus.choices})


def workorder_detail(request, pk):
    check(request.user, "view.production")
    wo = get_object_or_404(WorkOrder.objects.select_related(
        "product__customer", "order_line__order", "recipe_revision", "created_by", "started_by"), pk=pk)
    ops = wo.operations.select_related("machine", "started_by", "finished_by")
    return render(request, "production/workorder_detail.html", {
        "wo": wo, "operations": ops, "confirm_form": ConfirmForm(),
        "can_execute": can(request.user, "production.execute"),
        "components": wo.recipe_revision.components.select_related("material"),
        "history": history_for(wo),
    })


@require_POST
@require_perm("production.execute")
def workorder_start(request, pk):
    wo = get_object_or_404(WorkOrder, pk=pk)
    resp = run_form_action(request, ConfirmForm(request.POST), action="workorder.start",
                           fn=lambda d: services.start_work_order(request.user, wo),
                           success_message="Üretim başlatıldı.", url_for=_wo_url)
    return resp or redirect(_wo_url(wo))


@require_POST
@require_perm("production.execute")
def operation_action(request, pk, action):
    op = get_object_or_404(WorkOrderOperation.objects.select_related("work_order"), pk=pk)
    fn = {"baslat": services.start_operation, "tamamla": services.complete_operation}.get(action)
    if fn is None:
        return redirect(_wo_url(op.work_order))
    resp = run_form_action(request, ConfirmForm(request.POST), action=f"operation.{action}",
                           fn=lambda d: fn(request.user, op),
                           success_message="Operasyon başlatıldı." if action == "baslat" else "Operasyon tamamlandı.",
                           url_for=lambda _: _wo_url(op.work_order))
    return resp or redirect(_wo_url(op.work_order))
