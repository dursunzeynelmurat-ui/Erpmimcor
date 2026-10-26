from django.core.paginator import Paginator
import segno
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST

from accounts.permissions import can, check, get_scoped_or_404, require_perm, scoped
from core.forms import ConfirmForm, ReasonForm
from core.services import history_for
from core.viewutils import run_form_action
from orders.models import SalesOrder

from . import services
from .forms import (CountForm, DamageForm, PalletFilterForm, QualityForm, ReserveForm, ShipmentCreateForm,
                    ShipmentLineForm, TransferForm, WeighingForm)
from .models import Pallet, PalletStatus, Reservation, Shipment, ShipmentLine, ShipmentStatus


def _pallet_url(p):
    return reverse("warehouse:pallet_detail", args=[p.pk])


def pallet_list(request):
    check(request.user, "stock.view")
    form = PalletFilterForm(request.GET or None)
    qs = scoped(Pallet.objects.all(), request.user, "stock.view").select_related("product__customer", "work_order")
    if request.GET.get("musteri", "").isdigit():
        qs = qs.filter(product__customer_id=request.GET["musteri"])
    if form.is_valid():
        if form.cleaned_data["q"]:
            q = form.cleaned_data["q"].strip()
            qs = qs.filter(code__icontains=q) | qs.filter(product__code__icontains=q)
        if form.cleaned_data["status"]:
            qs = qs.filter(status=form.cleaned_data["status"])
        else:
            qs = qs.exclude(status=PalletStatus.EMPTY)
    else:
        qs = qs.exclude(status=PalletStatus.EMPTY)
    page = Paginator(qs.order_by("-created_at", "-pk"), 30).get_page(request.GET.get("page"))
    return render(request, "warehouse/pallet_list.html", {"pallets": page, "page_obj": page, "form": form})


def qr_open(request, token):
    """QR okutma yalnız kaydı açar; hiçbir stok hareketi oluşturmaz."""
    check(request.user, "stock.view")
    pallet = scoped(Pallet.objects.all(), request.user, "stock.view").filter(qr_token=token).first()
    if pallet is None:
        raise Http404("Palet bulunamadı.")
    return redirect(_pallet_url(pallet))


def qr_payload(request, pallet):
    """QR içeriği: yalnız paletin güncel kaydına giden tahmin edilemez bağlantı."""
    return request.build_absolute_uri(reverse("warehouse:qr_open", args=[pallet.qr_token]))


def pallet_detail(request, pk):
    pallet = get_scoped_or_404(Pallet.objects.select_related("product__customer", "work_order__order_line__order",
                                                             "created_by", "accepted_by"), request.user, "stock.view", pk=pk)
    u = request.user
    weighing = pallet.current_weighing
    weighings = pallet.weighings.select_related("user")
    ctx = {
        "pallet": pallet,
        "weighing_rows": services.weight_breakdown(weighing, pallet) if weighing else None,
        "coil_holds": services.coil_holds(pallet),
        "weighings": [(w, services.weight_breakdown(w)) for w in weighings],
        "movements": pallet.movements.select_related("user", "related_pallet", "shipment_line__shipment"),
        "reservations": pallet.reservations.select_related("order_line__order", "created_by"),
        "coils": pallet.pallet_coils.select_related("coil__machine"),
        "label_prints": pallet.label_prints.select_related("user"),
        "history": history_for(pallet),
        "quality_reason": history_for(pallet).filter(action="pallet.quality").values_list("reason", flat=True).first(),
    }
    if can(u, "warehouse.operate"):
        if pallet.status == PalletStatus.AWAITING_ACCEPTANCE:
            ctx["accept_form"] = WeighingForm(with_location=True)
        elif pallet.status == PalletStatus.IN_STOCK:
            ctx.update(weigh_form=WeighingForm(), transfer_form=TransferForm(), count_form=CountForm(),
                       damage_form=DamageForm())
    if can(u, "quality.block", pallet) and pallet.status != PalletStatus.EMPTY:
        form = QualityForm()
        if not can(u, "quality.decide", pallet):
            form.fields["quality_status"].choices = [c for c in form.fields["quality_status"].choices if c[0] == "blokeli"]
        ctx["quality_form"] = form
    if can(u, "reservation.manage") and pallet.status != PalletStatus.EMPTY:
        ctx["reserve_form"] = ReserveForm(pallet, user=u)
        ctx["cancel_form"] = ReasonForm()
    for key, value in ctx.items():
        if key.endswith("_form"):
            value.auto_id = key + "_%s"
    return render(request, "warehouse/pallet_detail.html", ctx)


def _pallet_action(request, pk, *, perm, form_cls, action, fn, success, form_kwargs=None, needs_pallet=False):
    pallet = get_scoped_or_404(Pallet.objects.all(), request.user, "stock.view", pk=pk)
    check(request.user, perm, pallet)
    args = [pallet] if needs_pallet else []
    form = form_cls(*args, request.POST or None, **(form_kwargs or {}))
    if request.method == "POST":
        resp = run_form_action(request, form, action=action, fn=lambda d: fn(pallet, d), success_message=success,
                               url_for=lambda _: _pallet_url(pallet))
        if resp:
            return resp
    labels = {
        "pallet.accept": "Depo kabulü", "pallet.weigh": "Yeniden tartım",
        "pallet.transfer": "Başka palete aktarım", "pallet.count": "Sayım düzeltmesi",
        "pallet.damage": "Hasarlı ürün ayırma", "pallet.quality": "Kalite durumu",
        "pallet.reserve": "Siparişe ayırma",
    }
    return render(request, "core/form_page.html", {"form": form, "title": f"{pallet.code}: {labels[action]}", "submit": "Kaydet",
                                                   "review_title": f"{pallet.code} · {pallet.product.code}",
                                                   "back": _pallet_url(pallet)})


def pallet_accept(request, pk):
    return _pallet_action(request, pk, perm="warehouse.operate", form_cls=WeighingForm, action="pallet.accept",
                          form_kwargs={"with_location": True},
                          fn=lambda p, d: services.accept_pallet(request.user, p, d),
                          success="Depo kabulü ve tartım kaydedildi.")


def pallet_weigh(request, pk):
    return _pallet_action(request, pk, perm="warehouse.operate", form_cls=WeighingForm, action="pallet.weigh",
                          fn=lambda p, d: services.reweigh_pallet(request.user, p, d), success="Tartım kaydedildi.")


def pallet_transfer(request, pk):
    return _pallet_action(request, pk, perm="warehouse.operate", form_cls=TransferForm, action="pallet.transfer",
                          fn=lambda p, d: services.transfer_boxes(request.user, p, d["quantity"], d["reason"],
                                                                  target=d["target_code"]),
                          success="Aktarım kaydedildi (kaynak ve hedef birlikte güncellendi).")


def pallet_count(request, pk):
    return _pallet_action(request, pk, perm="warehouse.operate", form_cls=CountForm, action="pallet.count",
                          fn=lambda p, d: services.adjust_count(request.user, p, d["counted"], d["reason"]),
                          success="Sayım düzeltmesi kaydedildi.")


def pallet_damage(request, pk):
    return _pallet_action(request, pk, perm="warehouse.operate", form_cls=DamageForm, action="pallet.damage",
                          fn=lambda p, d: services.separate_damaged(request.user, p, d["quantity"], d["reason"]),
                          success="Hasarlı kutular blokeli palete ayrıldı.")


def pallet_quality(request, pk):
    return _pallet_action(request, pk, perm="quality.block", form_cls=QualityForm, action="pallet.quality",
                          fn=lambda p, d: services.set_pallet_quality(request.user, p, d["quality_status"], d["reason"]),
                          success="Kalite durumu güncellendi.")


def pallet_reserve(request, pk):
    return _pallet_action(request, pk, perm="reservation.manage", form_cls=ReserveForm, action="pallet.reserve",
                          needs_pallet=True, form_kwargs={"user": request.user},
                          fn=lambda p, d: services.reserve(request.user, p, d["order_line"], d["quantity"]),
                          success="Kutular siparişe ayrıldı (fiziksel stok değişmedi).")


@require_POST
@require_perm("reservation.manage")
def reservation_cancel(request, pk):
    res = get_scoped_or_404(Reservation.objects.all(), request.user, "reservation.manage", pk=pk)
    form = ReasonForm(request.POST)
    resp = run_form_action(request, form, action="reservation.cancel",
                           fn=lambda d: services.cancel_reservation(request.user, res, d["reason"]),
                           success_message="Rezervasyon iptal edildi.", url_for=lambda _: _pallet_url(res.pallet))
    if resp:
        return resp
    return render(request, "core/form_page.html", {
        "form": form, "title": f"{res.code}: rezervasyon iptali", "submit": "Rezervasyonu iptal et",
        "back": _pallet_url(res.pallet),
    })


def pallet_label(request, pk):
    """Yazdırılabilir etiket. Basım kaydı POST ile tutulur; yeni palet oluşmaz."""
    pallet = get_scoped_or_404(Pallet.objects.select_related("product__customer", "work_order"), request.user,
                               "stock.view", pk=pk)
    payload = qr_payload(request, pallet)
    svg = segno.make(payload, error="m").svg_inline(scale=4, dark="#000")
    return render(request, "warehouse/pallet_label.html", {
        "pallet": pallet, "qr_svg": mark_safe(svg), "payload": payload, "do_print": request.GET.get("yazdir") == "1",
        "prints": pallet.label_prints.count(),
    })


@require_POST
def pallet_label_print(request, pk):
    pallet = get_scoped_or_404(Pallet.objects.all(), request.user, "stock.view", pk=pk)
    url = reverse("warehouse:pallet_label", args=[pk]) + "?yazdir=1"
    resp = run_form_action(request, ConfirmForm(request.POST), action="pallet.label_print",
                           fn=lambda d: services.record_label_print(request.user, pallet),
                           success_message=lambda lp: "Etiket yeniden basımı kaydedildi." if lp.is_reprint else "Etiket basımı kaydedildi.",
                           url_for=lambda _: url)
    return resp or redirect(url)


# --- Sevkiyat --------------------------------------------------------------------------

def shipment_list(request):
    check(request.user, "shipment.view")
    shipments = scoped(Shipment.objects.all(), request.user, "shipment.view").select_related("order__customer").prefetch_related("lines")
    if request.GET.get("musteri", "").isdigit():
        shipments = shipments.filter(order__customer_id=request.GET["musteri"])
    page = Paginator(shipments.order_by("-created_at", "-pk"), 30).get_page(request.GET.get("page"))
    return render(request, "warehouse/shipment_list.html", {"shipments": page, "page_obj": page})


@require_perm("shipment.manage")
def shipment_create(request, order_pk):
    order = get_scoped_or_404(SalesOrder.objects.all(), request.user, "order.view", pk=order_pk)
    form = ShipmentCreateForm(request.POST or None)
    if request.method == "POST":
        resp = run_form_action(request, form, action="shipment.create",
                               fn=lambda d: services.create_shipment(request.user, order, d["vehicle"], d["notes"]),
                               success_message=lambda s: f"Sevkiyat oluşturuldu: {s.code}",
                               url_for=lambda s: reverse("warehouse:shipment_detail", args=[s.pk]))
        if resp:
            return resp
    return render(request, "core/form_page.html", {"form": form, "title": f"{order.code} için sevkiyat", "submit": "Oluştur",
                                                   "back": reverse("orders:order_detail", args=[order.pk])})


def shipment_detail(request, pk):
    shipment = get_scoped_or_404(Shipment.objects.select_related("order__customer", "created_by", "shipped_by"),
                                 request.user, "shipment.view", pk=pk)
    form = None
    if can(request.user, "shipment.manage") and shipment.status == ShipmentStatus.DRAFT:
        form = ShipmentLineForm(shipment, request.POST or None)
        if request.method == "POST":
            resp = run_form_action(request, form, action="shipment.line_add",
                                   fn=lambda d: services.add_shipment_line(request.user, shipment, d["reservation"], d["quantity"]),
                                   success_message="Sevkiyata satır eklendi.",
                                   url_for=lambda _: reverse("warehouse:shipment_detail", args=[shipment.pk]))
            if resp:
                return resp
    return render(request, "warehouse/shipment_detail.html", {
        "shipment": shipment, "lines": shipment.lines.select_related("pallet", "order_line__product", "reservation"),
        "form": form, "history": history_for(shipment),
    })


@require_POST
@require_perm("shipment.manage")
def shipment_line_remove(request, pk):
    line = get_scoped_or_404(ShipmentLine.objects.all(), request.user, "shipment.view", pk=pk)
    url = reverse("warehouse:shipment_detail", args=[line.shipment_id])
    resp = run_form_action(request, ConfirmForm(request.POST), action="shipment.line_remove",
                           fn=lambda d: services.remove_shipment_line(request.user, line),
                           success_message="Satır çıkarıldı.", url_for=lambda _: url)
    return resp or redirect(url)


@require_POST
@require_perm("shipment.manage")
def shipment_confirm(request, pk):
    shipment = get_scoped_or_404(Shipment.objects.all(), request.user, "shipment.view", pk=pk)
    url = reverse("warehouse:shipment_detail", args=[pk])
    resp = run_form_action(request, ConfirmForm(request.POST), action="shipment.confirm",
                           fn=lambda d: services.confirm_shipment(request.user, shipment),
                           success_message=lambda s: f"{s.code} sevk edildi; stoktan düşüldü.", url_for=lambda _: url)
    return resp or redirect(url)
