from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.permissions import can, require_any, scoped
from core.forms import ConfirmForm, ReasonForm
from core.services import history_for, num
from core.viewutils import run_form_action

from . import services
from .forms import CorrectionForm
from .models import CorrectionKind, CorrectionRequest, CorrectionStatus


def _visible(user):
    """Onaylayabildikleri + kendi açtıkları."""
    approvable = scoped(CorrectionRequest.objects.all(), user, "correction.approve").values("pk")
    return CorrectionRequest.objects.filter(Q(pk__in=approvable) | Q(requested_by=user))


@require_any("correction.request", "correction.approve")
def correction_list(request):
    status = request.GET.get("durum", CorrectionStatus.REQUESTED)
    qs = _visible(request.user).select_related("requested_by", "decided_by")
    if status in CorrectionStatus.values:
        qs = qs.filter(status=status)
    page = Paginator(qs.order_by("-requested_at"), 30).get_page(request.GET.get("page"))
    return render(request, "corrections/correction_list.html", {"rows": page, "page_obj": page, "status": status,
                                                                "statuses": CorrectionStatus.choices})


@require_any("correction.request")
def correction_create(request, kind, target_id):
    if kind not in CorrectionKind.values:
        raise Http404
    target = get_object_or_404(services.TARGETS[kind], pk=target_id)
    if not can(request.user, "correction.request", target):
        raise Http404
    current = services.current_value(kind, target)
    code, unit, *_ = services.describe(kind, target)
    form = CorrectionForm(request.POST or None, initial={"new_value": current})
    if request.method == "POST":
        resp = run_form_action(
            request, form, action="correction.request",
            fn=lambda d: services.request_correction(request.user, kind, target, d["new_value"], d["reason"]),
            success_message=lambda cr: f"{cr.code} düzeltme talebi açıldı. Onaylanana kadar kayıt ve stok değişmez.",
            url_for=lambda cr: reverse("corrections:correction_detail", args=[cr.pk]))
        if resp:
            return resp
    return render(request, "corrections/correction_form.html", {
        "form": form, "kind_label": CorrectionKind(kind).label, "target_code": code, "unit": unit,
        "current": num(current), "affected": services.affected_records(kind, target),
        "back": request.GET.get("geri") or reverse("core:home")})


def correction_detail(request, pk):
    cr = get_object_or_404(_visible(request.user).select_related("requested_by", "decided_by"), pk=pk)
    can_decide = (cr.status == CorrectionStatus.REQUESTED and can(request.user, "correction.approve", cr))
    return render(request, "corrections/correction_detail.html", {
        "cr": cr, "can_decide": can_decide, "own": cr.requested_by_id == request.user.pk,
        "history": history_for(cr)})


def _decide(request, pk, fn, form_cls, message):
    cr = get_object_or_404(_visible(request.user), pk=pk)
    url = reverse("corrections:correction_detail", args=[pk])
    resp = run_form_action(request, form_cls(request.POST), action="correction.decide", fn=lambda d: fn(cr, d),
                           success_message=message, url_for=lambda _: url)
    return resp or redirect(url)


@require_POST
def correction_approve(request, pk):
    return _decide(request, pk, lambda cr, d: services.approve(request.user, cr), ConfirmForm,
                   lambda cr: "Düzeltme uygulandı." if cr.status == CorrectionStatus.APPLIED else
                   f"Uygulanmadı: {cr.decision_reason}")


@require_POST
def correction_reject(request, pk):
    return _decide(request, pk, lambda cr, d: services.reject(request.user, cr, d["reason"]), ReasonForm,
                   "Talep reddedildi; kayıt değişmedi.")


@require_POST
def correction_withdraw(request, pk):
    return _decide(request, pk, lambda cr, d: services.withdraw(request.user, cr), ConfirmForm, "Talep geri çekildi.")
