from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.permissions import can, check, require_perm
from core.forms import ConfirmForm
from core.services import history_for
from core.viewutils import run_form_action
from orders.models import OrderLine

from . import services
from catalog.models import MaterialLot

from .forms import ClosureForm, CoilForm, CoilQualityForm, MaterialMoveForm, PalletFromProductionForm, WorkOrderForm
from .models import Coil, WorkOrder, WorkOrderOperation, WorkOrderStatus


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
        "coils": wo.coils.select_related("operation", "machine").order_by("operation__seq", "created_at"),
        "pallets": wo.pallets.all(),
        "issued": services.issued_by_lot(wo),
        "closure": getattr(wo, "closure", None),
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


# --- M2: bobin, malzeme, palet, kapanış --------------------------------------------

def _get_wo(pk):
    return get_object_or_404(WorkOrder.objects.select_related("product", "order_line"), pk=pk)


def _wo_page(request, wo, form, title, submit, template="core/form_page.html", extra=None):
    ctx = {"form": form, "title": f"{wo.code}: {title}", "submit": submit, "back": _wo_url(wo), "wo": wo}
    ctx.update(extra or {})
    return render(request, template, ctx)


@require_perm("production.execute")
def coil_create(request, op_pk):
    op = get_object_or_404(WorkOrderOperation.objects.select_related("work_order", "machine"), pk=op_pk)
    form = CoilForm(op, request.POST or None)
    if request.method == "POST":
        resp = run_form_action(
            request, form, action="coil.create",
            fn=lambda d: services.record_coil(request.user, op, weight_kg=d["weight_kg"], length_m=d["length_m"],
                                              weight_source=d["weight_source"], sources=d["sources"], notes=d["notes"]),
            success_message=lambda c: f"Bobin kaydedildi: {c.code}", url_for=lambda c: _wo_url(op.work_order))
        if resp:
            return resp
    return _wo_page(request, op.work_order, form, f"{op.seq}. {op.get_machine_type_display()} ({op.machine.code}) bobin kaydı",
                    "Bobini kaydet", template="production/coil_form.html", extra={"op": op})


def coil_detail(request, pk):
    check(request.user, "view.production")
    coil = get_object_or_404(Coil.objects.select_related("work_order__product", "operation", "machine", "created_by"), pk=pk)
    form = None
    if can(request.user, "quality.block"):
        form = CoilQualityForm(request.POST or None)
        if not can(request.user, "quality.decide"):
            form.fields["quality_status"].choices = [c for c in form.fields["quality_status"].choices if c[0] == "blokeli"]
        if request.method == "POST":
            resp = run_form_action(request, form, action="coil.quality",
                                   fn=lambda d: services.set_coil_quality(request.user, coil, d["quality_status"], d["reason"]),
                                   success_message="Kalite durumu güncellendi.",
                                   url_for=lambda c: reverse("production:coil_detail", args=[c.pk]))
            if resp:
                return resp
    return render(request, "production/coil_detail.html", {
        "coil": coil, "sources": coil.source_links.select_related("source__machine"),
        "outputs": coil.output_links.select_related("output__machine"),
        "pallet_link": getattr(coil, "pallet_link", None), "form": form, "history": history_for(coil),
    })


@require_perm("production.execute")
def material_issue(request, pk):
    wo = _get_wo(pk)
    form = MaterialMoveForm(request.POST or None)
    if request.method == "POST":
        resp = run_form_action(request, form, action="material.issue",
                               fn=lambda d: services.issue_material(request.user, wo, d["lot"], d["quantity"]),
                               success_message="Malzeme çıkışı kaydedildi.", url_for=lambda _: _wo_url(wo))
        if resp:
            return resp
    return _wo_page(request, wo, form, "malzeme çıkışı", "Çıkışı kaydet")


@require_perm("production.execute")
def material_return(request, pk):
    wo = _get_wo(pk)
    lots = MaterialLot.objects.filter(pk__in=[lot.pk for lot in services.issued_by_lot(wo)]).select_related("material")
    form = MaterialMoveForm(request.POST or None, lots=lots)
    if request.method == "POST":
        resp = run_form_action(request, form, action="material.return",
                               fn=lambda d: services.return_material(request.user, wo, d["lot"], d["quantity"]),
                               success_message="Kalan malzeme lota iade edildi.", url_for=lambda _: _wo_url(wo))
        if resp:
            return resp
    return _wo_page(request, wo, form, "kalan malzeme iadesi", "İadeyi kaydet")


@require_perm("production.execute")
def pallet_from_production(request, pk):
    from warehouse import services as wh

    wo = _get_wo(pk)
    form = PalletFromProductionForm(wo, request.POST or None)
    if request.method == "POST":
        def run(d):
            if d["target_code"]:
                return wh.add_production_to_pallet(request.user, d["target_code"], wo, d["box_count"], d["coils"])
            return wh.create_pallet_from_production(request.user, wo, d["box_count"], d["coils"])
        resp = run_form_action(request, form, action="pallet.from_production", fn=run,
                               success_message=lambda p: f"Palet kaydedildi: {p.code} ({p.box_count} kutu). Etiketi basın.",
                               url_for=lambda p: reverse("warehouse:pallet_detail", args=[p.pk]))
        if resp:
            return resp
    return _wo_page(request, wo, form, "palet oluştur / palete ekle", "Kaydet")


@require_perm("production.execute")
def workorder_close(request, pk):
    wo = _get_wo(pk)
    form = ClosureForm(request.POST or None)
    if request.method == "POST":
        resp = run_form_action(
            request, form, action="workorder.close",
            fn=lambda d: services.close_work_order(request.user, wo, scrap_kg=d["scrap_kg"],
                                                   remaining_confirmed=d["remaining_confirmed"], reason=d["reason"]),
            success_message="İş emri kapatıldı.", url_for=_wo_url)
        if resp:
            return resp
    return _wo_page(request, wo, form, "kapanış", "İş emrini kapat", template="production/closure_form.html",
                    extra={"summary": services.closure_summary(wo),
                           "issued": services.issued_by_lot(wo)})
