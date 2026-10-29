import segno
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import can, check, get_scoped_or_404, require_perm, scoped
from catalog.models import Customer, Product
from core.exceptions import BusinessRuleError
from core.forms import ReasonForm
from core.services import history_for
from core.viewutils import run_form_action

from . import services
from .forms import (AllocateForm, AttachmentForm, CriterionForm, DecisionForm, FeedbackForm, LinkMovementForm,
                    MeasurementForm, MoveForm, ReturnForm, SampleCreateForm, SendForm, StockSourceForm)
from .models import PhysicalStatus, Sample, SampleAttachment, SampleCriterion, SampleKind


def _sample(request, pk):
    return get_scoped_or_404(Sample.objects.select_related("customer", "product", "revision", "work_order", "coil",
                                                           "pallet", "order_line__order", "stock_pallet"),
                             request.user, "sample.view", pk=pk)


def _url(sample):
    return reverse("samples:sample_detail", args=[sample.pk])


def sample_list(request):
    check(request.user, "sample.view")
    qs = scoped(Sample.objects.select_related("customer", "product"), request.user, "sample.view")
    kind, status = request.GET.get("tur", ""), request.GET.get("durum", "")
    if kind in SampleKind.values:
        qs = qs.filter(kind=kind)
    if status in PhysicalStatus.values:
        qs = qs.filter(physical_status=status)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(code__icontains=q) | qs.filter(product__code__icontains=q)
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "samples/sample_list.html", {
        "samples": page, "page_obj": page, "kinds": SampleKind.choices, "statuses": PhysicalStatus.choices,
        "f": {"tur": kind, "durum": status, "q": q}, "internal": services.internal_view(request.user),
        "can_create": can(request.user, "sample.manage")})


def _resolve(model, code, field):
    if not code:
        return None
    obj = model.objects.filter(code=code.strip()).first()
    if obj is None:
        raise BusinessRuleError(f"{code} bulunamadı.", field=field)
    return obj


def _resolve_line(code):
    from orders.models import OrderLine
    if not code:
        return None
    order_code, _, no = code.strip().rpartition("/")
    line = OrderLine.objects.filter(order__code=order_code, line_no=int(no)).first() if no.isdigit() else None
    if line is None:
        raise BusinessRuleError(f"{code} sipariş kalemi bulunamadı.", field="order_line_code")
    return line


@require_perm("sample.manage")
def sample_create(request):
    from production.models import Coil, WorkOrder
    from warehouse.models import Pallet
    form = SampleCreateForm(request.POST or None, user=request.user)
    if request.method == "POST":
        def create(d):
            return services.create_sample(
                request.user, kind=d["kind"], customer=d["customer"], product=d["product"],
                order_line=_resolve_line(d["order_line_code"]),
                work_order=_resolve(WorkOrder, d["work_order_code"], "work_order_code"),
                coil=_resolve(Coil, d["coil_code"], "coil_code"),
                pallet=_resolve(Pallet, d["pallet_code"], "pallet_code"), counterpart=d["counterpart"],
                occurred_at=d["occurred_at"], quantity=d["quantity"], unit=d["unit"], room=d["room"],
                cabinet=d["cabinet"], shelf=d["shelf"], box=d["box"], retention_until=d["retention_until"],
                retention_conditions=d["retention_conditions"], notes=d["notes"], shared_note=d["shared_note"],
                stock_source=d["stock_source"], revision=d["revision"], revision_uncertain=d["revision_uncertain"])
        resp = run_form_action(request, form, action="sample.create", fn=create,
                               success_message=lambda s: f"Numune kaydedildi: {s.code}. Sıradaki adım: QR etiketini "
                                                         "basın; firma stoğundan ayrılacaksa 'Stoktan ayır'.",
                               url_for=_url)
        if resp:
            return resp
    return render(request, "core/form_page.html", {"form": form, "title": "Yeni numune", "submit": "Numuneyi kaydet",
                                                   "back": reverse("samples:sample_list")})


@require_GET
@require_perm("sample.manage")
def customer_products(request, pk):
    customer = get_object_or_404(scoped(Customer.objects.active(), request.user, "sample.manage"), pk=pk)
    resp = JsonResponse({"products": [{"id": p.pk, "label": str(p)}
                                      for p in Product.objects.active().filter(customer=customer).order_by("code")]})
    resp["Cache-Control"] = "no-store"
    return resp


def sample_detail(request, pk):
    sample = _sample(request, pk)
    user = request.user
    internal = services.internal_view(user, sample)
    ctx = {"s": sample, "internal": internal}
    if internal:
        ctx.update({
            "events": sample.events.select_related("user"), "measurements": sample.measurements.select_related("measured_by"),
            "attachments": sample.attachments.all(), "history": history_for(sample),
            "can_manage": can(user, "sample.manage", sample), "can_stock": can(user, "sample.stock"),
            "can_decide": can(user, "sample.decide"),
            # Aynı sayfadaki formlarda alan kimlikleri ayrılır (aynı ad, farklı id); her formun kendi işlem anahtarı var.
            "move_form": MoveForm(auto_id="id_move_%s", initial={"room": sample.room, "cabinet": sample.cabinet,
                                                                 "shelf": sample.shelf, "box": sample.box}),
            "allocate_form": AllocateForm(auto_id="id_stock_%s",
                                          initial={"boxes": int(sample.quantity) if sample.unit == "kutu" else None}),
            "send_form": SendForm(auto_id="id_send_%s"), "return_form": ReturnForm(auto_id="id_back_%s"),
            "decision_form": DecisionForm(auto_id="id_dec_%s"), "measure_form": MeasurementForm(auto_id="id_meas_%s"),
            "attachment_form": AttachmentForm(auto_id="id_att_%s"),
            "source_form": StockSourceForm(auto_id="id_src_%s", initial={"stock_source": sample.stock_source}),
            "link_form": LinkMovementForm(auto_id="id_link_%s", sample=sample),
            "stock_effect": services.stock_effect(sample),
            "stock_pending": services.stock_pending(sample),
        })
    ctx["can_feedback"] = can(user, "sample.feedback", sample)
    ctx["feedback_form"] = FeedbackForm(auto_id="id_fb_%s")
    return render(request, "samples/sample_detail.html", ctx)


ACTIONS = {
    "konum": (MoveForm, "sample.move", lambda u, s, d, f: services.move(u, s, room=d["room"], cabinet=d["cabinet"],
                                                                        shelf=d["shelf"], box=d["box"], note=d["note"]),
              "Konum güncellendi."),
    "stok": (AllocateForm, "sample.stock", None, "Numune için kutular stoktan ayrıldı (tek stok hareketi)."),
    "kaynak": (StockSourceForm, "sample.stock_source", lambda u, s, d, f: services.set_stock_source(
        u, s, d["stock_source"], d["reason"]), "Stok kaynağı kaydedildi."),
    "bagla": (LinkMovementForm, "sample.stock_link", None,
              "Önceki stok çıkışı numuneye bağlandı; yeni stok düşümü yapılmadı."),
    "gonder": (SendForm, "sample.send", lambda u, s, d, f: services.send(u, s, counterpart=d["counterpart"],
                                                                         note=d["note"]),
               "Gönderim kaydedildi. Stok hareketi oluşmadı."),
    "geri": (ReturnForm, "sample.return", lambda u, s, d, f: services.receive_back(u, s, counterpart=d["counterpart"],
                                                                                    note=d["note"]),
             "Geri dönüş kaydedildi. Numune satılabilir stoğa eklenmedi."),
    "imha": (ReasonForm, "sample.destroy", lambda u, s, d, f: services.destroy(u, s, d["reason"]),
             "İmha kaydedildi. Stok hareketi oluşmadı."),
    "karar": (DecisionForm, "sample.decide", None, "Kalite kararı kaydedildi. Palet/bobin kalite durumları değişmedi."),
    "geri_bildirim": (FeedbackForm, "sample.feedback", lambda u, s, d, f: services.record_feedback(
        u, s, d["feedback"], d["note"], d["reference"]), "Müşteri geri bildirimi kaydedildi."),
    "olcum": (MeasurementForm, "sample.measure", lambda u, s, d, f: services.add_measurement(
        u, s, d["criterion"], value=d["value"], text_value=d["text_value"], note=d["note"]), "Ölçüm kaydedildi."),
    "ek": (AttachmentForm, "sample.attachment", lambda u, s, d, f: services.add_attachment(u, s, d["file"], d["title"]),
           "Ek dosya yüklendi."),
}


@require_POST
def sample_action(request, pk, action):
    from production.models import Coil
    from warehouse.models import Pallet
    sample = _sample(request, pk)
    if action not in ACTIONS:
        raise Http404
    form_cls, audit_action, fn, message = ACTIONS[action]
    if action == "ek":
        form = form_cls(request.POST, request.FILES)
    elif action == "bagla":
        form = form_cls(request.POST, sample=sample)
    else:
        form = form_cls(request.POST)
    def allocate(u, s, d, f):
        return services.allocate_from_pallet(u, s, _resolve(Pallet, d["pallet_code"], "pallet_code"), d["boxes"],
                                             d["reason"])

    def decide(u, s, d, f):
        def many(model, text, field):
            return [_resolve(model, c, field) for c in (text or "").replace(";", ",").split(",") if c.strip()]
        return services.decide(u, s, d["decision"], d["reason"], pallets=many(Pallet, d["pallet_codes"], "pallet_codes"),
                               coils=many(Coil, d["coil_codes"], "coil_codes"))
    def link(u, s, d, f):
        from warehouse.models import PalletMovement
        mv = PalletMovement.objects.filter(pk=d["movement"]).first() if str(d["movement"]).isdigit() else None
        if mv is None:
            raise BusinessRuleError("Stok hareketini listeden seçin.", field="movement")
        return services.link_prior_movement(u, s, mv)

    fn = {"stok": allocate, "karar": decide, "bagla": link}.get(action, fn)
    resp = run_form_action(request, form, action=audit_action, fn=lambda d: fn(request.user, sample, d, form),
                           success_message=message, url_for=lambda _: _url(sample))
    return resp or redirect(_url(sample))


def sample_label(request, pk):
    from django.conf import settings
    sample = _sample(request, pk)
    if not services.internal_view(request.user, sample):
        raise PermissionDenied
    path = reverse("samples:qr_open", args=[sample.qr_token])
    base = getattr(settings, "APP_BASE_URL", "")
    payload = base.rstrip("/") + path if base else request.build_absolute_uri(path)
    svg = mark_safe(segno.make(payload, error="m").svg_inline(scale=4, dark="#000"))
    return render(request, "samples/sample_label.html", {"s": sample, "qr_svg": svg, "base_url_missing": not base})


def qr_open(request, token):
    """QR yalnız kaydı açar; stok veya durum değişikliği oluşturmaz."""
    check(request.user, "sample.view")
    sample = scoped(Sample.objects.all(), request.user, "sample.view").filter(qr_token=token).first()
    if sample is None:
        raise Http404("Numune bulunamadı.")
    return redirect(_url(sample))


def attachment_file(request, pk):
    att = get_object_or_404(SampleAttachment.objects.select_related("sample"), pk=pk)
    sample = _sample(request, att.sample_id)
    if not services.internal_view(request.user, sample):
        raise Http404
    resp = FileResponse(att.file.open("rb"), as_attachment=True, filename=att.original_name,
                        content_type=att.content_type)
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Content-Security-Policy"] = "default-src 'none'; sandbox"
    resp["Cache-Control"] = "private, no-store"
    return resp


@require_perm("sample.criteria")
def criteria(request):
    form = CriterionForm(request.POST or None)
    replace = request.GET.get("degistir") or request.POST.get("degistir")
    old = SampleCriterion.objects.filter(pk=replace, is_active=True).first() if replace and replace.isdigit() else None
    if old and request.method == "GET":
        form = CriterionForm(initial={"name": old.name, "unit": old.unit, "min_value": old.min_value,
                                      "max_value": old.max_value, "description": old.description})
    if request.method == "POST":
        resp = run_form_action(request, form, action="sample.criterion",
                               fn=lambda d: services.create_criterion(request.user, replaces=old, **{
                                   k: d[k] for k in ("name", "unit", "min_value", "max_value", "description")}),
                               success_message="Kriter kaydedildi. Eski ölçümler kendi kriter kopyasıyla kalır.",
                               url_for=lambda _: reverse("samples:criteria"))
        if resp:
            return resp
    return render(request, "samples/criteria.html", {"form": form, "old": old,
                                                     "criteria": SampleCriterion.objects.select_related("created_by")})
