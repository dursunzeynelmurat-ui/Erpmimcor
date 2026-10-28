from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.permissions import can, check, get_scoped_or_404, require_perm, scoped
from core.forms import ConfirmForm, ReasonForm
from core.services import history_for
from core.viewutils import run_form_action
from orders.models import OrderLine

from . import instructions, lifecycle, services
from catalog.models import MaterialLot

from .forms import (ClosureForm, CoilForm, CoilQualityForm, HandoverForm, MaterialMoveForm, PalletFromProductionForm,
                    ScheduleForm, StopForm, WorkOrderEditForm, WorkOrderForm)
from .models import Coil, ShiftHandover, StopReason, WorkInstruction, WorkOrder, WorkOrderOperation, WorkOrderStatus


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
                unit=d["unit"], planned_start=d["planned_start"], notes=d["notes"], foreman=d["foreman"]),
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
                target_quantity=d["target_quantity"], unit=d["unit"], planned_start=d["planned_start"], notes=d["notes"], foreman=d["foreman"]),
            success_message=lambda wo: f"Stok iş emri oluşturuldu: {wo.code}", url_for=_wo_url)
        if resp:
            return resp
    return render(request, "production/workorder_form.html", {"form": form, "line": None})


def workorder_list(request):
    check(request.user, "workorder.view")
    status = request.GET.get("durum", "aktif")
    qs = scoped(WorkOrder.objects.all(), request.user, "workorder.view").select_related("product__customer", "order_line__order").prefetch_related("operations__machine")
    if status != "arsiv":
        qs = qs.filter(archived_at__isnull=True)
    if status == "aktif":
        qs = qs.filter(status__in=[WorkOrderStatus.PLANNED, WorkOrderStatus.STARTED])
    elif status == "arsiv":
        qs = qs.filter(archived_at__isnull=False)
    elif status in WorkOrderStatus.values:
        qs = qs.filter(status=status)
    page = Paginator(qs.order_by("-created_at", "-pk"), 30).get_page(request.GET.get("page"))
    return render(request, "production/workorder_list.html", {"work_orders": page, "page_obj": page, "status": status, "statuses": WorkOrderStatus.choices})


def workorder_detail(request, pk):
    wo = get_scoped_or_404(WorkOrder.objects.select_related(
        "product__customer", "order_line__order", "order_line__approved_revision", "recipe_revision", "created_by",
        "started_by", "released_by", "assigned_foreman"),
        request.user, "workorder.view", pk=pk)
    user = request.user
    ops = list(wo.operations.select_related("machine", "started_by", "finished_by", "shift", "work_order")
               .prefetch_related("instructions", "stops"))
    op_rows = []
    for op in ops:
        ins = list(op.instructions.all())
        op_rows.append({
            "op": op,
            "tech": instructions.technical_info(op) if instructions.can_view_technical(user, op) else None,
            "can_hold": can(user, "production.hold", op), "can_schedule": can(user, "workorder.schedule", op),
            "can_print": can(user, "instruction.print", op),
            "current_instruction": next((i for i in ins if i.status == "gecerli"), None),
            "void_instructions": [i for i in ins if i.status != "gecerli"],
            "open_stop": next((s for s in op.stops.all() if s.ended_at is None), None),
        })
    conflicts = [c for c in (lifecycle.revision_conflict(wo), lifecycle.quantity_conflict(wo)) if c] \
        if wo.status in lifecycle.OPEN_STATUSES else []
    return render(request, "production/workorder_detail.html", {
        "wo": wo, "operations": ops, "op_rows": op_rows, "confirm_form": ConfirmForm(),
        "can_execute": can(user, "production.execute", wo),
        "can_release": can(user, "workorder.release", wo), "can_edit": can(user, "workorder.create", wo),
        "can_cancel": can(user, "workorder.cancel", wo),
        "can_hold_any": any(r["can_hold"] for r in op_rows),
        "moved": lifecycle.has_movements(wo), "conflicts": conflicts,
        "open_statuses": list(lifecycle.OPEN_STATUSES), "floor_statuses": list(instructions.OPEN_FOR_FLOOR),
        "final_statuses": [WorkOrderStatus.CLOSED, WorkOrderStatus.CANCELLED],
        "stop_reasons": StopReason.choices, "tech_labels": instructions.TECH_LABELS,
        "remaining": lifecycle.remaining_target(wo),
        # Üretim sahası (talimat yetkili) kullanıcıları ölçüleri yalnız aktif teknik erişimi varken görür
        "show_spec": (can(user, "recipe.view") or any(r["tech"] for r in op_rows)
                      or not can(user, "instruction.view")),
        "components": (wo.recipe_revision.components.select_related("material") if can(user, "recipe.view") else None),
        "handovers": _handovers(wo, user),
        "coils": wo.coils.select_related("operation", "machine").order_by("operation__seq", "created_at"),
        "pallets": wo.pallets.all(),
        "issued": services.issued_by_lot(wo),
        "lot_movements": wo.lot_movements.select_related("lot__material"),
        "closure": getattr(wo, "closure", None),
        "closure_state": services.closure_status(wo),
        "closure_labels": CLOSURE_LABELS,
        "can_review_closure": can(user, "correction.approve", wo) or can(user, "planning.approve"),
        "history": history_for(wo),
    })


def _handovers(wo, user):
    rows = list(ShiftHandover.objects.filter(operation__work_order=wo).select_related(
        "operation", "from_shift", "to_shift", "created_by", "acknowledged_by"))
    for h in rows:
        h.can_ack = not h.acknowledged_at and h.created_by_id != user.pk and lifecycle.can_acknowledge(user, h)
    return rows


CLOSURE_LABELS = {"issued": "Net verilen malzeme (kg)", "good": "Sağlam çıktı (kg)", "blocked": "Blokeli (kg)",
                  "wip": "Ara stok bobin (kg)", "scrap": "Fire (kg, kapanışta girilen)", "boxes": "Palete alınan kutu",
                  "difference": "Denge farkı (kg)"}


@require_POST
def closure_review(request, pk):
    wo = _get_wo(request, pk)
    return _post_action(request, _wo_url(wo) + "#kapanis", ReasonForm(request.POST), "workorder.closure_review",
                        lambda d: services.review_closure(request.user, wo, d["reason"]),
                        "Güncel denge değerlendirildi; ilk kapanış kaydı değişmedi.")


@require_POST
@require_perm("production.execute")
def workorder_start(request, pk):
    wo = get_scoped_or_404(WorkOrder.objects.all(), request.user, "workorder.view", pk=pk)
    resp = run_form_action(request, ConfirmForm(request.POST), action="workorder.start",
                           fn=lambda d: services.start_work_order(request.user, wo),
                           success_message="Üretim başlatıldı.", url_for=_wo_url)
    return resp or redirect(_wo_url(wo))


@require_POST
@require_perm("production.execute")
def operation_action(request, pk, action):
    op = get_scoped_or_404(WorkOrderOperation.objects.select_related("work_order"), request.user, "workorder.view", pk=pk)
    fn = {"baslat": services.start_operation, "tamamla": services.complete_operation}.get(action)
    if fn is None:
        return redirect(_wo_url(op.work_order))
    resp = run_form_action(request, ConfirmForm(request.POST), action=f"operation.{action}",
                           fn=lambda d: fn(request.user, op),
                           success_message="Operasyon başlatıldı." if action == "baslat" else "Operasyon tamamlandı.",
                           url_for=lambda _: _wo_url(op.work_order))
    return resp or redirect(_wo_url(op.work_order))


# --- M2: bobin, malzeme, palet, kapanış --------------------------------------------

def _get_wo(request, pk):
    return get_scoped_or_404(WorkOrder.objects.select_related("product", "order_line"), request.user, "workorder.view", pk=pk)


def _get_wo_for_execute(request, pk):
    """Üretim kaydı formları (GET dahil) nesne kapsamlı production.execute ister: devri teslim edilmiş eski vardiya
    formu açamaz; form içeriği (lot listesi, kapanış özeti) reçete bileşimini dolaylı göstermesin."""
    wo = _get_wo(request, pk)
    check(request.user, "production.execute", wo)
    return wo


def _wo_page(request, wo, form, title, submit, template="core/form_page.html", extra=None):
    ctx = {"form": form, "title": f"{wo.code}: {title}", "submit": submit, "back": _wo_url(wo), "wo": wo}
    ctx.update(extra or {})
    return render(request, template, ctx)


@require_perm("production.execute")
def coil_create(request, op_pk):
    op = get_scoped_or_404(WorkOrderOperation.objects.select_related("work_order", "machine"), request.user,
                           "workorder.view", pk=op_pk)
    check(request.user, "production.execute", op)
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
    coil = get_scoped_or_404(Coil.objects.select_related("work_order__product", "operation", "machine", "created_by"),
                             request.user, "workorder.view", pk=pk)
    from core.models import QualityStatus
    from warehouse.services import quality_permission
    form = None
    allowed = set()
    if can(request.user, "quality.block", coil):
        allowed.add(QualityStatus.BLOCKED)
    if can(request.user, quality_permission(coil.quality_status, QualityStatus.OK), coil):
        allowed |= {QualityStatus.OK, QualityStatus.PENDING}
    allowed.discard(coil.quality_status)
    if allowed:
        form = CoilQualityForm(request.POST or None)
        form.fields["quality_status"].choices = [c for c in form.fields["quality_status"].choices if c[0] in allowed]
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
    wo = _get_wo_for_execute(request, pk)
    form = MaterialMoveForm(request.POST or None, deviation=can(request.user, "material.deviation", wo),
                            recipe_ids=services.recipe_material_ids(wo))
    if request.method == "POST":
        resp = run_form_action(request, form, action="material.issue",
                               fn=lambda d: services.issue_material(request.user, wo, d["lot"], d["quantity"],
                                                                    deviation_reason=d.get("deviation_reason", "")),
                               success_message="Malzeme çıkışı kaydedildi.", url_for=lambda _: _wo_url(wo))
        if resp:
            return resp
    return _wo_page(request, wo, form, "malzeme çıkışı", "Çıkışı kaydet")


@require_perm("production.execute")
def material_return(request, pk):
    wo = _get_wo_for_execute(request, pk)
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

    wo = _get_wo_for_execute(request, pk)
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
    wo = _get_wo_for_execute(request, pk)
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


# --- Aşama 2: yaşam döngüsü, planlama, duruş, devir, talimat ------------------------------

def _post_action(request, obj_url, form, action, fn, message):
    resp = run_form_action(request, form, action=action, fn=fn, success_message=message, url_for=lambda _: obj_url)
    return resp or redirect(obj_url)


@require_POST
@require_perm("workorder.release")
def workorder_release(request, pk):
    wo = _get_wo(request, pk)
    return _post_action(request, _wo_url(wo), ConfirmForm(request.POST), "workorder.release",
                        lambda d: lifecycle.release(request.user, wo), "İş emri serbest bırakıldı.")


@require_POST
@require_perm("workorder.release")
def workorder_withdraw(request, pk):
    wo = _get_wo(request, pk)
    return _post_action(request, _wo_url(wo), ReasonForm(request.POST), "workorder.withdraw",
                        lambda d: lifecycle.withdraw(request.user, wo, d["reason"]),
                        "İş emri taslağa geri çekildi; basılı talimatlar sistemde geçersiz işaretlendi.")


@require_POST
@require_perm("workorder.cancel")
def workorder_cancel(request, pk):
    wo = _get_wo(request, pk)
    return _post_action(request, _wo_url(wo), ReasonForm(request.POST), "workorder.cancel",
                        lambda d: lifecycle.cancel(request.user, wo, d["reason"]), "İş emri iptal edildi.")


@require_POST
@require_perm("workorder.create")
def workorder_archive(request, pk):
    wo = _get_wo(request, pk)
    return _post_action(request, _wo_url(wo), ConfirmForm(request.POST), "workorder.archive",
                        lambda d: lifecycle.archive(request.user, wo), "İş emri arşivlendi.")


@require_perm("workorder.create")
def workorder_edit(request, pk):
    wo = _get_wo(request, pk)
    form = WorkOrderEditForm(wo, request.POST or None)
    if request.method == "POST":
        resp = run_form_action(
            request, form, action="workorder.update",
            fn=lambda d: lifecycle.update_draft(request.user, wo, target_quantity=d["target_quantity"],
                                                planned_start=d["planned_start"], notes=d["notes"],
                                                foreman=d["foreman"], machines=d["machines"],
                                                sync_revision=d.get("sync_revision", False), reason=d["reason"]),
            success_message="Taslak iş emri güncellendi.", url_for=_wo_url)
        if resp:
            return resp
    return render(request, "production/workorder_form.html", {"form": form, "line": wo.order_line, "edit_wo": wo})


def operation_schedule(request, pk):
    op = get_scoped_or_404(WorkOrderOperation.objects.select_related("work_order", "machine"), request.user,
                           "workorder.schedule", pk=pk)
    form = ScheduleForm(op, request.POST or None)
    affected = None
    if request.method == "POST":
        if form.is_valid():
            affected = list(lifecycle.machine_queue(form.cleaned_data["machine"], form.cleaned_data["planned_date"],
                                                    form.cleaned_data["shift"], exclude=op))
        resp = run_form_action(
            request, form, action="operation.schedule",
            fn=lambda d: lifecycle.schedule_operation(request.user, op, planned_date=d["planned_date"], shift=d["shift"],
                                                      machine=d["machine"], queue_position=d["queue_position"],
                                                      reason=d["reason"])[0],
            success_message=lambda o: "Operasyon planı kaydedildi." + (
                f" Aynı makine/vardiyada {len(affected)} başka iş var; sıraları kontrol edin." if affected else ""),
            url_for=lambda o: _wo_url(o.work_order))
        if resp:
            return resp
    queue = lifecycle.machine_queue(op.machine, exclude=op)[:20]
    return render(request, "production/operation_schedule.html", {"op": op, "form": form, "queue": queue})


@require_POST
def operation_hold(request, pk, action):
    op = get_scoped_or_404(WorkOrderOperation.objects.select_related("work_order"), request.user, "workorder.view", pk=pk)
    url = _wo_url(op.work_order)
    if action == "durdur":
        return _post_action(request, url, StopForm(request.POST), "operation.stop",
                            lambda d: lifecycle.stop_operation(request.user, op, d["reason"], d["note"]),
                            "Operasyon durduruldu; makine boşa çıktı.")
    if action == "devam":
        return _post_action(request, url, ConfirmForm(request.POST), "operation.resume",
                            lambda d: lifecycle.resume_operation(request.user, op), "Operasyon devam ediyor.")
    raise Http404


def handover_create(request, op_pk):
    op = get_scoped_or_404(WorkOrderOperation.objects.select_related("work_order", "machine"), request.user,
                           "production.hold", pk=op_pk)
    form = HandoverForm(request.POST or None)
    if request.method == "POST":
        resp = run_form_action(
            request, form, action="operation.handover",
            fn=lambda d: lifecycle.create_handover(request.user, op, to_shift=d["to_shift"],
                                                   remaining_note=d["remaining_note"],
                                                   machine_side_material=d["machine_side_material"], notes=d["notes"]),
            success_message="Vardiya devri kaydedildi.", url_for=lambda h: _wo_url(op.work_order) + "#devirler")
        if resp:
            return resp
    return render(request, "production/handover_form.html", {"form": form, "op": op,
                                                             "coils": lifecycle.handover_snapshot(op)})


@require_POST
def handover_ack(request, pk):
    h = get_object_or_404(ShiftHandover.objects.select_related("operation__work_order"), pk=pk)
    get_scoped_or_404(WorkOrderOperation.objects.all(), request.user, "workorder.view", pk=h.operation_id)
    if not lifecycle.can_acknowledge(request.user, h):
        raise PermissionDenied("Bu devri teslim alma yetkiniz yok.")
    url = _wo_url(h.operation.work_order) + "#devirler"
    return _post_action(request, url, ConfirmForm(request.POST), "operation.handover_ack",
                        lambda d: lifecycle.acknowledge_handover(request.user, h), "Devir teslim alındı.")


@require_POST
def instruction_print(request, op_pk):
    op = get_scoped_or_404(WorkOrderOperation.objects.select_related("work_order"), request.user, "instruction.print",
                           pk=op_pk)
    resp = run_form_action(request, ConfirmForm(request.POST), action="instruction.print",
                           fn=lambda d: instructions.issue_for_print(request.user, op),
                           success_message=lambda i: f"Talimat sürüm {i.version} basım için hazır.",
                           url_for=lambda i: reverse("production:instruction_view", args=[i.pk]) + "?yazdir=1")
    return resp or redirect(_wo_url(op.work_order))


def instruction_view(request, pk):
    ins = get_object_or_404(WorkInstruction.objects.select_related("operation__work_order", "created_by"), pk=pk)
    op = get_scoped_or_404(WorkOrderOperation.objects.select_related("work_order"), request.user, "instruction.view",
                           pk=ins.operation_id)
    if not instructions.can_view_technical(request.user, op):
        raise PermissionDenied("İş emri açık değil; talimat içeriğine erişim kapandı.")
    last_print = ins.prints.select_related("user").order_by("-at").first()
    return render(request, "production/instruction_print.html", {
        "ins": ins, "s": ins.snapshot, "tech_labels": instructions.TECH_LABELS, "last_print": last_print,
        "current": op.instructions.filter(status="gecerli").first(), "acks": ins.acks.select_related("user"),
        "do_print": request.GET.get("yazdir") == "1",
    })


@require_POST
def instruction_ack(request, pk, kind):
    ins = get_object_or_404(WorkInstruction, pk=pk)
    get_scoped_or_404(WorkOrderOperation.objects.all(), request.user, "instruction.view", pk=ins.operation_id)
    url = reverse("production:instruction_view", args=[pk])
    return _post_action(request, url, ConfirmForm(request.POST), "instruction.ack",
                        lambda d: instructions.acknowledge(request.user, ins, kind), "Kaydınız alındı.")


def floor_plan(request):
    """Ustabaşı planı: planlama kuyruğu (tarih/vardiya/sıra) ile fiili çalışma durumu ayrı gösterilir."""
    check(request.user, "workorder.view")
    ops = (scoped(WorkOrderOperation.objects.filter(work_order__status__in=lifecycle.OPEN_STATUSES)
                  .exclude(status="tamamlandi"), request.user, "workorder.view")
           .select_related("work_order__product", "machine", "shift")
           .order_by("machine__code", "planned_date", "shift__start_time", "queue_position", "pk"))
    machines = {}
    for op in ops:
        machines.setdefault(op.machine, []).append(op)
    running = {m: next((o for o in lst if o.status == "devam_ediyor"), None) for m, lst in machines.items()}
    return render(request, "production/floor_plan.html", {"machines": machines, "running": running})
