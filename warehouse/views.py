from datetime import timedelta

from django import forms
from django.core.paginator import Paginator
import segno
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST

from accounts.permissions import can, check, get_scoped_or_404, require_perm, scoped
from core.forms import ConfirmForm, ReasonForm
from core.models import QualityStatus
from core.services import history_for
from core.viewutils import run_form_action
from orders.models import SalesOrder

from . import services
from .forms import (CountForm, DamageForm, LocationForm, PackageLabelForm, PalletFilterForm, QualityForm,
                    ReduceReservationForm, ReserveForm, ReturnForm, ScanForm, ShipmentCreateForm, ShipmentLineForm,
                    StockLevelForm, TransferForm, WeighingForm)
from .models import (SHIPPED_STATES, LabelKind, LabelPrint, Pallet, PalletStatus, Reservation, Shipment, ShipmentLine,
                     ShipmentStatus, StockLevel)


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
        if form.cleaned_data.get("quality"):
            qs = qs.filter(quality_status=form.cleaned_data["quality"])
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
    """QR içeriği: yalnız paletin güncel kaydına giden tahmin edilemez bağlantı. Taban adres APP_BASE_URL
    ayarından alınır (etiket hangi bilgisayardan basılırsa basılsın aynı adres); ayar yoksa isteğin adresi."""
    from django.conf import settings
    path = reverse("warehouse:qr_open", args=[pallet.qr_token])
    base = getattr(settings, "APP_BASE_URL", "")
    return base.rstrip("/") + path if base else request.build_absolute_uri(path)


def pallet_detail(request, pk):
    pallet = get_scoped_or_404(Pallet.objects.select_related("product__customer", "work_order__order_line__order",
                                                             "created_by", "accepted_by"), request.user, "stock.view", pk=pk)
    u = request.user
    weighing = pallet.current_weighing
    weighings = pallet.weighings.select_related("user")
    ctx = {
        "pallet": pallet, "current_weighing": weighing,
        "weighing_rows": services.weight_breakdown(weighing, pallet) if weighing else None,
        "coil_holds": services.coil_holds(pallet),
        "weighings": [(w, services.weight_breakdown(w)) for w in weighings],
        "movements": pallet.movements.select_related("user", "related_pallet", "shipment_line__shipment"),
        "reservations": pallet.reservations.select_related("order_line__order", "created_by"),
        "coils": pallet.pallet_coils.select_related("coil__machine"),
        "label_prints": pallet.label_prints.select_related("user"),
        "location_changes": pallet.location_changes.select_related("user"),
        "return_receipts": pallet.return_receipts.select_related("shipment_line__shipment"),
        "can_relocate": can(u, "warehouse.operate") and pallet.status == PalletStatus.IN_STOCK,
        "history": history_for(pallet),
        "quality_reason": history_for(pallet).filter(action="pallet.quality").values_list("reason", flat=True).first(),
    }
    if can(u, "warehouse.operate"):
        if pallet.status == PalletStatus.AWAITING_ACCEPTANCE:
            ctx["accept_form"] = WeighingForm(with_location=True)
        elif pallet.status == PalletStatus.IN_STOCK:
            ctx.update(weigh_form=WeighingForm(), transfer_form=TransferForm(), count_form=CountForm(),
                       damage_form=DamageForm())
    if pallet.status != PalletStatus.EMPTY:
        # Blokelemek, karar vermek ve blokajı kaldırmak ayrı yetkiler (quality.block / decide / release).
        allowed = set()
        if can(u, "quality.block", pallet):
            allowed.add(QualityStatus.BLOCKED)
        if can(u, services.quality_permission(pallet.quality_status, QualityStatus.OK), pallet):
            allowed |= {QualityStatus.OK, QualityStatus.PENDING}
        allowed.discard(pallet.quality_status)
        if allowed:
            form = QualityForm()
            form.fields["quality_status"].choices = [c for c in form.fields["quality_status"].choices if c[0] in allowed]
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
    if isinstance(perm, (tuple, list)):
        if not any(can(request.user, p, pallet) for p in perm):
            check(request.user, perm[0], pallet)
    else:
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
    return _pallet_action(request, pk, perm=("quality.block", "quality.decide", "quality.release"), form_cls=QualityForm, action="pallet.quality",
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
        "prints": pallet.label_prints.count(), "base_url_missing": not _base_url_set(),
        "last_print": pallet.label_prints.filter(kind=LabelKind.PALLET).first(),
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
    show_all = request.GET.get("durum") == "tumu"
    if not show_all:  # iptal edilen ve boş bırakılmış taslaklar aktif listeyi kalabalıklaştırmaz
        shipments = shipments.exclude(status=ShipmentStatus.CANCELLED).exclude(
            status=ShipmentStatus.DRAFT, lines__isnull=True, created_at__lt=timezone.now() - timedelta(days=1))
    page = Paginator(shipments.distinct().order_by("-created_at", "-pk"), 30).get_page(request.GET.get("page"))
    return render(request, "warehouse/shipment_list.html", {"shipments": page, "page_obj": page, "show_all": show_all})


@require_POST
@require_perm("shipment.manage")
def shipment_cancel(request, pk):
    shipment = get_scoped_or_404(Shipment.objects.all(), request.user, "shipment.view", pk=pk)
    url = reverse("warehouse:shipment_detail", args=[pk])
    resp = run_form_action(request, ReasonForm(request.POST), action="shipment.cancel",
                           fn=lambda d: services.cancel_shipment(request.user, shipment, d["reason"]),
                           success_message="Sevkiyat iptal edildi; stok değişmedi.", url_for=lambda _: url)
    return resp or redirect(url)


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
    lines = list(shipment.lines.select_related("pallet", "order_line__product", "reservation")
                 .prefetch_related("returns__pallet"))
    for ln in lines:
        ln.returnable = services.returnable_quantity(ln) if shipment.status in SHIPPED_STATES else 0
    return render(request, "warehouse/shipment_detail.html", {
        "shipment": shipment, "lines": lines, "form": form, "history": history_for(shipment),
        "check": services.load_check(shipment) if shipment.status == ShipmentStatus.PREPARED else None,
        "scan_form": ScanForm(auto_id="scan_%s") if shipment.status == ShipmentStatus.PREPARED else None,
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
@require_perm("shipment.approve")
def shipment_confirm(request, pk):
    shipment = get_scoped_or_404(Shipment.objects.all(), request.user, "shipment.view", pk=pk)
    url = reverse("warehouse:shipment_detail", args=[pk])
    resp = run_form_action(request, ConfirmForm(request.POST), action="shipment.confirm",
                           fn=lambda d: services.confirm_shipment(request.user, shipment),
                           success_message=lambda s: f"{s.code} sevk edildi; stoktan düşüldü.", url_for=lambda _: url)
    return resp or redirect(url)


def stock_summary(request):
    from .selectors import stock_breakdown
    check(request.user, "stock.view")
    return render(request, "warehouse/stock_summary.html", {"rows": stock_breakdown(request.user)})


# --- Aşama 4 ------------------------------------------------------------------------------

def _shipment_post(request, pk, form, action, fn, message):
    shipment = get_scoped_or_404(Shipment.objects.all(), request.user, "shipment.view", pk=pk)
    url = reverse("warehouse:shipment_detail", args=[pk])
    resp = run_form_action(request, form, action=action, fn=lambda d: fn(shipment, d), success_message=message,
                           url_for=lambda _: url)
    return resp or redirect(url)


@require_POST
@require_perm("shipment.manage")
def shipment_prepare(request, pk):
    return _shipment_post(request, pk, ConfirmForm(request.POST), "shipment.prepare",
                          lambda s, d: services.prepare_shipment(request.user, s),
                          "Sevkiyat hazırlandı; yükleme kontrolü için paletleri okutun.")


@require_POST
@require_perm("shipment.manage")
def shipment_reopen(request, pk):
    return _shipment_post(request, pk, ReasonForm(request.POST), "shipment.reopen",
                          lambda s, d: services.reopen_shipment(request.user, s, d["reason"]),
                          "Sevkiyat hazırlığa döndü; yükleme kontrolü yeniden yapılacak.")


@require_POST
@require_perm("shipment.manage")
def shipment_scan(request, pk):
    return _shipment_post(request, pk, ScanForm(request.POST), "shipment.scan",
                          lambda s, d: services.scan_pallet(request.user, s, d["raw"]),
                          lambda scan: f"Okutma: {scan.pallet.code if scan.pallet else scan.raw[:30]} — "
                                       f"{scan.get_result_display()}")


@require_POST
@require_perm("shipment.approve")
def shipment_exit(request, pk):
    return _shipment_post(request, pk, ConfirmForm(request.POST), "shipment.exit",
                          lambda s, d: services.mark_exited(request.user, s), "Araç çıkışı kaydedildi.")


@require_POST
@require_perm("shipment.approve")
def shipment_undo(request, pk):
    return _shipment_post(request, pk, ReasonForm(request.POST), "shipment.undo",
                          lambda s, d: services.undo_shipment(request.user, s, d["reason"]),
                          "Sevk geri alındı; kutular stoğa ve rezervasyona döndü.")


@require_perm("shipment.manage")
def shipment_return(request, pk):
    line = get_scoped_or_404(ShipmentLine.objects.select_related("shipment", "pallet", "order_line"), request.user,
                             "shipment.view", pk=pk)
    form = ReturnForm(request.POST or None, initial={"quantity": services.returnable_quantity(line)})
    if request.method == "POST":
        resp = run_form_action(
            request, form, action="shipment.return",
            fn=lambda d: services.receive_return(request.user, line, d["quantity"], d["reason"], d["location"]),
            success_message=lambda r: f"{r.code} iadesi {r.pallet.code} paletine alındı (kontrol bekliyor).",
            url_for=lambda r: _pallet_url(r.pallet))
        if resp:
            return resp
    return render(request, "core/form_page.html", {
        "form": form, "title": f"İade: {line.shipment.code} / {line.pallet.code}", "submit": "İadeyi kaydet",
        "back": reverse("warehouse:shipment_detail", args=[line.shipment_id]),
        "intro": f"{line.order_line.code} · sevk edilen {line.quantity} kutu, iade alınabilir "
                 f"{services.returnable_quantity(line)} kutu. İade ayrı bir palete girer ve kalite kararı bekler; "
                 "eski sevkiyat ve sipariş geçmişi değişmez."})


def pallet_location(request, pk):
    return _pallet_action(request, pk, perm="warehouse.operate", form_cls=LocationForm, action="pallet.location",
                          fn=lambda p, d: services.change_location(request.user, p, d["location"], d["reason"]),
                          success="Depo yeri değiştirildi; eski yer geçmişte duruyor.")


def reservation_reduce(request, pk):
    res = get_scoped_or_404(Reservation.objects.select_related("pallet", "order_line"), request.user,
                            "reservation.manage", pk=pk)
    form = ReduceReservationForm(request.POST or None, initial={"quantity": res.quantity})
    if request.method == "POST":
        resp = run_form_action(request, form, action="reservation.reduce",
                               fn=lambda d: services.reduce_reservation(request.user, res, d["quantity"], d["reason"]),
                               success_message="Rezervasyon azaltıldı.", url_for=lambda _: _pallet_url(res.pallet))
        if resp:
            return resp
    return render(request, "core/form_page.html", {
        "form": form, "title": f"{res.code}: rezervasyonu azalt", "submit": "Kaydet", "back": _pallet_url(res.pallet),
        "intro": f"{res.order_line.code} için {res.quantity} kutu ayrılmış, {res.shipped_quantity} kutu sevk edilmiş. "
                 "Tamamen bırakmak için 'rezervasyonu iptal et' kullanın."})


def package_labels(request, pk):
    """Kutu/rulo etiketleri: n/N numaralı toplu basım. QR palete gider; kutu başına ayrı stok kimliği oluşmaz."""
    pallet = get_scoped_or_404(Pallet.objects.select_related("product__customer", "work_order"), request.user,
                               "stock.view", pk=pk)
    lp = None
    if request.GET.get("basim", "").isdigit():
        lp = get_object_or_404(LabelPrint, pk=request.GET["basim"], pallet=pallet)
    form = PackageLabelForm(request.POST or None, initial={"copies": pallet.box_count or 1})
    if request.method == "POST":
        check(request.user, "label.print")
        resp = run_form_action(request, form, action="pallet.package_label",
                               fn=lambda d: services.request_label_print(request.user, pallet, LabelKind.PACKAGE,
                                                                         d["copies"]),
                               success_message="Basım talebi kaydedildi. Yazıcı sonucunu aşağıdan bildirin.",
                               url_for=lambda lp: reverse("warehouse:package_labels", args=[pk]) + f"?basim={lp.pk}")
        if resp:
            return resp
    payload = qr_payload(request, pallet)
    svg = mark_safe(segno.make(payload, error="m").svg_inline(scale=2, dark="#000"))
    copies = lp.copies if lp else 0
    return render(request, "warehouse/package_labels.html", {
        "pallet": pallet, "form": form, "lp": lp, "qr_svg": svg, "labels": range(1, copies + 1), "total": copies,
        "base_url_missing": not _base_url_set()})


def _base_url_set():
    from django.conf import settings
    return bool(getattr(settings, "APP_BASE_URL", ""))


@require_POST
def label_result(request, pk, result):
    lp = get_object_or_404(LabelPrint.objects.select_related("pallet"), pk=pk)
    get_scoped_or_404(Pallet.objects.all(), request.user, "stock.view", pk=lp.pallet_id)
    url = request.POST.get("next") or _pallet_url(lp.pallet)
    if not url.startswith("/"):
        url = _pallet_url(lp.pallet)
    resp = run_form_action(request, ConfirmForm(request.POST), action="pallet.label_result",
                           fn=lambda d: services.confirm_label_print(request.user, lp, result == "basildi"),
                           success_message="Basım sonucu kaydedildi.", url_for=lambda _: url)
    return resp or redirect(url)


def stock_needs(request):
    """Planlama ihtiyaç listesi ve ürün stok seviyeleri."""
    if not (can(request.user, "stocklevel.manage") or can(request.user, "planning.approve")):
        check(request.user, "stocklevel.manage")
    from catalog.models import Product
    rows = services.planning_needs(request.user)
    levels = StockLevel.objects.select_related("product__customer").order_by("product__code")
    form = None
    if can(request.user, "stocklevel.manage"):
        form = StockLevelForm(request.POST or None)
        form.fields["product"] = forms.ModelChoiceField(
            label="Ürün", queryset=Product.objects.active().select_related("customer"))
        form.order_fields(["product", "min_boxes", "target_boxes"])
        if request.method == "POST":
            resp = run_form_action(request, form, action="product.stock_level",
                                   fn=lambda d: services.set_stock_level(request.user, d["product"], d["min_boxes"],
                                                                         d["target_boxes"]),
                                   success_message="Stok seviyesi kaydedildi.",
                                   url_for=lambda _: reverse("warehouse:stock_needs"))
            if resp:
                return resp
    return render(request, "warehouse/stock_needs.html", {"rows": rows, "levels": levels, "form": form})
