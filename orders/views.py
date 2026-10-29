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
from .forms import (ApproveLineForm, CompatibilityForm, LineAddForm, LineChangeForm, LineSpecRequestForm,
                    OrderCreateForm, OverDeliveryForm, products_for)
from .models import CreationMethod, LineStatus, OrderLine, OrderStatus, SalesOrder
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
    """Kısa adımlı sipariş girişi. 'git': ileri / geri / 1-2 (Düzenle) / kaydet. Adım bilgisi olmadan gelen tam
    gönderim (eski form) kaydet olarak işlenir; her durumda aynı sunucu doğrulaması uygulanır."""
    from . import repeat
    form = OrderCreateForm(request.POST or None, user=request.user)
    step, stuck = 1, False
    if request.method == "POST":
        current = int(request.POST.get("adim", "3")) if request.POST.get("adim", "3").isdigit() else 1
        current = min(max(current, 1), 3)
        action = request.POST.get("git", "kaydet")
        if action == "ileri":
            stuck = bool(form.step_errors(current))
            step = current if stuck else min(current + 1, 3)
        elif action == "geri":
            step = max(current - 1, 1)
        elif action in ("1", "2"):
            step = int(action)
        else:
            from django.db import transaction

            @transaction.atomic
            def create(d):
                order = services.create_order(
                    request.user, customer=d["customer"], order_date=d["order_date"], due_date=d["due_date"],
                    customer_ref=d["customer_ref"], notes=d["notes"], purpose=d["purpose"], charge=d["charge"],
                    creation_method=CreationMethod.NEW,
                    lines=[{"product": d["product"], "quantity": d["quantity"], "unit": d["unit"]}])
                if d.get("want_change") and form.spec_changes:
                    repeat.request_line_change(request.user, order.lines.get(), form.spec_changes, d["change_reason"])
                return order

            def created(o):
                tail = (" Teknik talebiniz planlamaya iletildi; planlama kararından önce kalem teknik onay almaz."
                        if form.spec_changes else "")
                return (f"Sipariş oluşturuldu: {o.code} (taslak).{tail} Sıradaki adım: kalemleri kontrol edin, "
                        "gerekiyorsa kalem ekleyin ve 'Siparişi onayla' düğmesine basın.")
            resp = run_form_action(
                request, form, action="order.create", fn=create, success_message=created,
                url_for=lambda o: reverse("orders:order_detail", args=[o.pk]))
            if resp:
                return resp
            step = form.first_error_step()
    # Hata yalnız kullanıcı o adımı göndermiş ve geçememişse gösterilir (yeni açılan adımda boş alan uyarısı yok).
    show_errors = request.method == "POST" and (request.POST.get("git", "kaydet") == "kaydet"
                                                or (request.POST.get("git") == "ileri" and stuck))
    if form.is_bound and not show_errors:
        form.errors.clear()  # temizlenmiş değerler korunur; yeni açılan adımda hata/aria-invalid gösterilmez
    product = form.selected_product() if form.is_bound else None
    customer = form._selected_customer() if form.is_bound else None
    revision = product.current_revision if product else None
    visible = form.STEPS[step][1]
    return render(request, "orders/order_create.html", {
        "form": form, "step": step, "steps": [(k, t) for k, (t, _) in form.STEPS.items()],
        "visible": visible, "visible_plain": [n for n in visible if n not in form.spec_names],
        "spec_request": [form[n] for n in form.spec_names if n != "want_change"],
        "spec_diff": repeat.change_rows(revision, form.spec_changes) if form.spec_changes else [],
        "show_errors": show_errors, "product": product, "customer": customer,
        "snapshot": services.order_snapshot(customer) if customer else None,
        "revision": revision,
    })


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
            fn=lambda d: services.approve_line(request.user, line, d["revision"], d["note"],
                                               deviation_reason=d["deviation_reason"]),
            success_message=lambda ln: f"{ln.code} teknik olarak onaylandı (Rev.{ln.approved_revision.rev_no}).",
            url_for=lambda ln: reverse("orders:planning_queue"))
        if resp:
            return resp
    from .repeat import expected_basis, pending_request, revision_cards, source_warnings
    revisions = list(form.fields["revision"].queryset)
    by_pk = {r.pk: r for r in revisions}
    chosen = form["revision"].value() if form.is_bound else request.GET.get("rev") or form.fields["revision"].initial
    chosen = getattr(chosen, "pk", chosen)
    revision = by_pk.get(int(chosen)) if str(chosen or "").isdigit() and int(chosen) in by_pk else \
        form.fields["revision"].initial
    return render(request, "orders/line_approve.html", {
        "line": line, "form": form, "revision": revision, "history": history_for(line),
        "cards": revision_cards(revisions, form.expected), "expected": form.expected,
        "expected_basis": expected_basis(line) if form.expected else "",
        "source_warnings": source_warnings(line.source_line) if line.source_line_id else [],
        "pending": pending_request(line)})


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


@require_perm("order.create")
def line_change_request(request, pk):
    """Satış: taslak / planlama bekleyen kalem için farklı teknik özellik talebi (karışım yok; karar planlamada)."""
    from . import repeat
    line = get_object_or_404(OrderLine.objects.select_related("order__customer", "product", "approved_revision"),
                             pk=pk)
    order = _get_order(request, line.order_id)
    form = LineSpecRequestForm(line, request.POST or None)
    if request.method == "POST":
        resp = run_form_action(
            request, form, action="order.change_request",
            fn=lambda d: repeat.request_line_change(request.user, line, form.spec_changes, d["change_reason"]),
            success_message="Teknik talep planlamaya iletildi. Planlama kararından önce kalem teknik onay almaz ve "
                            "üretime çıkmaz; ürün ve reçete değişmedi.",
            url_for=lambda _: _order_url(order))
        if resp:
            return resp
    return render(request, "orders/line_change_request.html", {
        "line": line, "form": form, "revision": line.approved_revision or line.product.current_revision,
        "fields": [form[n] for n in form.spec_names if n != "change_reason"], "pending": repeat.pending_request(line),
        "diff": repeat.change_rows(line.approved_revision or line.product.current_revision, form.spec_changes)
        if form.spec_changes else []})


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
        form = OverDeliveryForm(request.POST or None, initial={"extra": services.current_over_delivery(line)})
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


# --- Repete / revizyonlu repete -------------------------------------------------------------

@require_perm("order.create")
def repeat_order(request):
    """Tekrar sipariş sihirbazı: 1) müşteri, 2) kalemler (miktar/termin, isteğe bağlı değişiklik), 3) teslimat ve kontrol."""
    from catalog.customers import order_snapshot
    from .forms import RepeatOrderForm
    from . import repeat
    customers = scoped(Customer.objects.active(), request.user, "order.create").order_by("name")
    cid = request.POST.get("musteri") or request.GET.get("musteri")
    customer = customers.filter(pk=cid).first() if cid and str(cid).isdigit() else None
    if customer is None:
        return render(request, "orders/repeat_order.html", {"step": 1, "customers": customers})
    candidates = list(repeat.candidate_lines(request.user, customer)[:30])
    initial = {}
    src = request.GET.get("kaynak")
    if src and src.isdigit():
        initial = {f"sel_{ln.pk}": True for ln in candidates if ln.order_id == int(src) and ln.product.is_active}
    form = RepeatOrderForm(request.POST or None, candidates=candidates, initial=initial)
    step, show_errors = 2, False
    if request.method == "POST":
        action = request.POST.get("git", "kaydet")
        line_names = form.line_field_names()
        if action == "ileri":
            line_errors = {k: v for k, v in form.errors.items() if k in line_names or k == "__all__"}
            step, show_errors = (2, True) if line_errors else (3, False)
        elif action == "geri":
            step = 2
        else:
            def create(d):
                return repeat.create_repeat_order(
                    request.user, customer=customer, due_date=d["due_date"], customer_ref=d["customer_ref"],
                    notes=d["notes"], purpose=d["purpose"], charge=d["charge"],
                    delivery_confirmed=d["delivery_confirmed"],
                    items=[{k: v for k, v in c.items() if k != "item"} for c in d["chosen"]])
            resp = run_form_action(
                request, form, action="order.repeat", fn=create,
                success_message=lambda o: f"Tekrar sipariş oluşturuldu: {o.code} ({o.get_creation_method_display()}, "
                                          "taslak). Sıradaki adım: siparişi onaylayın; planlama teknik onayı verecek.",
                url_for=lambda o: reverse("orders:order_detail", args=[o.pk]))
            if resp:
                return resp
            show_errors = True
            step = 2 if any(k in form.errors for k in line_names) or "__all__" in form.errors else 3
    if form.is_bound and not show_errors:
        form.errors.clear()
    chosen = form.cleaned_data.get("chosen", []) if form.is_bound and hasattr(form, "cleaned_data") else []
    for c in chosen:
        from .repeat import base_values, diff_values
        c["diff"] = [(spec_label(k), spec_show(k, o), spec_show(k, n))
                     for k, (o, n) in diff_values(base_values(c["source_line"]), c["changes"]).items()]
    return render(request, "orders/repeat_order.html", {
        "step": step, "customer": customer, "form": form, "show_errors": show_errors, "chosen": chosen,
        "snapshot": order_snapshot(customer), "visible_order": form.STEP_ORDER_FIELDS,
    })


def spec_label(name):
    from catalog import spec
    f = spec.BY_NAME[name]
    return f"{f.label} ({f.unit})" if f.unit else f.label


def spec_show(name, value):
    from catalog import spec
    return spec.display(spec.BY_NAME[name], value)


def change_request(request, pk):
    """Planlama: revizyonlu repete isteğini eski/yeni yan yana görür ve karar verir."""
    from .models import LineChangeRequest
    from . import repeat
    cr = get_object_or_404(LineChangeRequest.objects.select_related("line__order__customer", "line__product",
                                                                    "base_revision", "requested_by", "draft"), pk=pk)
    get_scoped_or_404(OrderLine.objects.all(), request.user, "order.view", pk=cr.line_id)
    return render(request, "orders/change_request.html", {
        "cr": cr, "rows": repeat.comparison(cr), "can_decide": repeat.can_decide(request.user),
        "confirm_form": ConfirmForm(), "reason_form": ReasonForm(),
    })


@require_POST
@require_perm("planning.approve")
def change_request_decide(request, pk, action):
    from .models import LineChangeRequest
    from . import repeat
    cr = get_object_or_404(LineChangeRequest, pk=pk)
    url = reverse("orders:change_request", args=[pk])
    if action == "revizyon":
        return _post_action_redirect(request, ConfirmForm(request.POST), "order.change_request_draft",
                                     lambda d: repeat.start_new_revision(request.user, cr),
                                     "Yeni revizyon taslağı açıldı; istenen değerler uygulandı, karışım değişmedi. "
                                     "Kontrol edip onaylayın.",
                                     lambda dr: reverse("catalog:recipe_draft", args=[dr.pk]), url)
    if action == "urun":
        from django import forms as djforms

        class NewProductForm(ConfirmForm):
            code = djforms.CharField(label="Yeni ürün kodu", max_length=40)
            name = djforms.CharField(label="Ürün adı", max_length=200, required=False)
        return _post_action_redirect(request, NewProductForm(request.POST), "order.change_request_product",
                                     lambda d: repeat.start_new_product(request.user, cr, code=d["code"], name=d["name"]),
                                     "Yeni ürün kodu açıldı ve kalem bu koda geçti; reçete taslağını kontrol edip onaylayın.",
                                     lambda dr: reverse("catalog:recipe_draft", args=[dr.pk]), url)
    if action == "reddet":
        return _post_action_redirect(request, ReasonForm(request.POST), "order.change_request_reject",
                                     lambda d: repeat.reject(request.user, cr, d["reason"]),
                                     "İstek reddedildi; kalem eski teknik özelliklerle onaylanabilir.", lambda _: url, url)
    raise Http404


def _post_action_redirect(request, form, action, fn, message, url_for, fallback):
    resp = run_form_action(request, form, action=action, fn=fn, success_message=message, url_for=url_for)
    return resp or redirect(fallback)
