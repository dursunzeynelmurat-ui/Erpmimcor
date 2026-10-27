"""Müşteri kartı ekranları. Tüm sorgular kullanıcının müşteri kapsamından geçer (kapsam dışı → 404)."""
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.permissions import can, check, get_scoped_or_404, require_perm, scoped
from core.forms import ReasonForm
from core.services import history_for
from core.viewutils import run_form_action

from . import customers as svc
from .forms import (AccessForm, AddressForm, ContactForm, CustomerCreateForm, DocumentForm, SECTION_FIELDS,
                    customer_section_form)
from .models import Customer, CustomerAccess, CustomerAddress, CustomerContact, CustomerDocument


def _customer(request, pk, action="customer.view"):
    return get_scoped_or_404(Customer.objects.all(), request.user, action, pk=pk)


def _card_url(customer, anchor=""):
    return reverse("catalog:customer_detail", args=[customer.pk]) + (f"#{anchor}" if anchor else "")


@require_perm("customer.view")
def customer_list(request):
    qs = scoped(Customer.objects.all(), request.user, "customer.view")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q) | Q(short_name__icontains=q) | Q(tax_id__icontains=q))
    show = request.GET.get("durum", "aktif")
    if show == "aktif":
        qs = qs.filter(is_active=True)
    elif show == "pasif":
        qs = qs.filter(is_active=False)
    return render(request, "catalog/customer_list.html", {"customers": qs.order_by("code")[:300], "q": q, "show": show})


@require_perm("customer.create")
def customer_create(request):
    form = CustomerCreateForm(request.POST or None)
    similar = []
    if request.method == "POST":
        if form.is_valid():
            similar = svc.similar_customers(form.cleaned_data["name"])
        fields = CustomerCreateForm.Meta.fields
        resp = run_form_action(
            request, form, action="customer.create",
            fn=lambda d: svc.create_customer(request.user, {f: d[f] for f in fields},
                                             confirm_similar=d["confirm_similar"]),
            success_message=lambda c: f"Müşteri açıldı: {c.code}. Kartın diğer bölümlerini sonradan tamamlayabilirsiniz.",
            url_for=_card_url)
        if resp:
            return resp
    return render(request, "catalog/customer_create.html", {"form": form, "similar": similar})


@require_perm("customer.view")
def customer_detail(request, pk):
    customer = _customer(request, pk)
    u = request.user
    commercial = can(u, "customer.commercial.view", customer)
    docs = [d for d in customer.documents.select_related("uploaded_by") if svc.can_view_document(u, d)] \
        if can(u, "customer.document.view", customer) else None
    return render(request, "catalog/customer_detail.html", {
        "customer": customer, "commercial": commercial, "can_change": can(u, "customer.change", customer),
        "can_docs": can(u, "customer.document.manage", customer), "can_access": can(u, "customer.access.manage"),
        "documents": docs,
        "contacts": customer.contacts.all(), "addresses": customer.addresses.all(),
        "accesses": customer.accesses.select_related("user", "granted_by", "revoked_by") if can(u, "customer.access.manage") else None,
        "readiness": {key: (label, svc.readiness(customer, key)) for key, label in svc.STAGES.items()},
        "links": {
            "products": scoped(customer.products.all(), u, "product.view").count() if can(u, "product.view") else None,
            "orders": customer.orders.exclude(status__in=["tamamlandi", "iptal"]).count() if can(u, "order.view", customer) else None,
        },
        "toggle_form": ReasonForm(), "history": history_for(customer),
    })


@require_perm("customer.change")
def customer_section_edit(request, pk, section):
    if section not in SECTION_FIELDS:
        raise Http404
    customer = _customer(request, pk, "customer.change")
    if section == "ticari":
        check(request.user, "customer.commercial.view", customer)
    form_class = customer_section_form(section)
    form = form_class(request.POST or None, instance=customer)
    if request.method == "POST":
        resp = run_form_action(
            request, form, action=f"customer.section.{section}",
            fn=lambda d: svc.update_customer(request.user, customer, {f: d[f] for f in form_class.Meta.fields}),
            success_message=f"{form_class.section_title} bölümü kaydedildi.", url_for=lambda c: _card_url(c, section))
        if resp:
            return resp
        customer.refresh_from_db()
    return render(request, "core/form_page.html", {"form": form, "title": f"{customer.code}: {form_class.section_title}",
                                                   "submit": "Kaydet", "back": _card_url(customer, section)})


def _child_page(request, pk, model, form_class, child_pk, save_fn, title, anchor):
    customer = _customer(request, pk, "customer.change")
    obj = None
    if child_pk:
        obj = model.objects.filter(customer=customer, pk=child_pk).first()
        if obj is None:
            raise Http404
    form = form_class(request.POST or None, instance=obj)
    if request.method == "POST":
        resp = run_form_action(
            request, form, action=f"customer.{anchor}",
            fn=lambda d: save_fn(request.user, customer, {f: d[f] for f in form_class.Meta.fields}, obj),
            success_message="Kaydedildi.", url_for=lambda _: _card_url(customer, anchor))
        if resp:
            return resp
    return render(request, "core/form_page.html", {"form": form, "title": f"{customer.code}: {title}",
                                                   "submit": "Kaydet", "back": _card_url(customer, anchor)})


@require_perm("customer.change")
def contact_edit(request, pk, contact_pk=None):
    return _child_page(request, pk, CustomerContact, ContactForm, contact_pk, svc.save_contact, "İlgili kişi", "kisiler")


@require_perm("customer.change")
def address_edit(request, pk, address_pk=None):
    return _child_page(request, pk, CustomerAddress, AddressForm, address_pk, svc.save_address, "Adres", "adresler")


@require_POST
@require_perm("customer.change")
def customer_toggle(request, pk):
    customer = _customer(request, pk, "customer.change")
    resp = run_form_action(request, ReasonForm(request.POST), action="customer.toggle",
                           fn=lambda d: svc.set_customer_active(request.user, customer, not customer.is_active, d["reason"]),
                           success_message="Müşteri durumu güncellendi.", url_for=_card_url)
    return resp or redirect(_card_url(customer))


# --- Belgeler ---------------------------------------------------------------------------

@require_perm("customer.document.manage")
def document_upload(request, pk):
    customer = _customer(request, pk, "customer.document.manage")
    form = DocumentForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        resp = run_form_action(
            request, form, action="customer.document",
            fn=lambda d: svc.upload_document(request.user, customer, kind=d["kind"], title=d["title"],
                                             version=d["version"], valid_from=d["valid_from"],
                                             visibility=d["visibility"], upload=d["file"]),
            success_message="Belge yüklendi.", url_for=lambda _: _card_url(customer, "belgeler"))
        if resp:
            return resp
    return render(request, "core/form_page.html", {"form": form, "title": f"{customer.code}: belge yükle",
                                                   "submit": "Yükle", "back": _card_url(customer, "belgeler"),
                                                   "multipart": True})


@require_perm("customer.document.view")
def document_download(request, pk, doc_pk):
    customer = _customer(request, pk, "customer.document.view")
    doc = CustomerDocument.objects.filter(customer=customer, pk=doc_pk).first()
    if doc is None or not svc.can_view_document(request.user, doc):
        raise Http404
    # Her zaman ek olarak indirilir; tarayıcıda çalıştırılabilir içerik olarak açılmaz.
    resp = FileResponse(doc.file.open("rb"), as_attachment=True, filename=doc.original_name,
                        content_type=doc.content_type)
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Content-Security-Policy"] = "default-src 'none'; sandbox"
    resp["Cache-Control"] = "private, no-store"
    return resp


@require_POST
@require_perm("customer.document.manage")
def document_toggle(request, pk, doc_pk):
    customer = _customer(request, pk, "customer.document.manage")
    doc = CustomerDocument.objects.filter(customer=customer, pk=doc_pk).first()
    if doc is None:
        raise Http404
    resp = run_form_action(request, ReasonForm(request.POST), action="customer.document_toggle",
                           fn=lambda d: svc.set_document_active(request.user, doc, not doc.is_active, d["reason"]),
                           success_message="Belge durumu güncellendi.", url_for=lambda _: _card_url(customer, "belgeler"))
    return resp or redirect(_card_url(customer, "belgeler"))


# --- Erişimler -------------------------------------------------------------------------------

@require_perm("customer.access.manage")
def access_grant(request, pk):
    customer = get_scoped_or_404(Customer.objects.all(), request.user, "customer.view", pk=pk)
    form = AccessForm(request.POST or None)
    if request.method == "POST":
        resp = run_form_action(
            request, form, action="customer.access_grant",
            fn=lambda d: svc.grant_access(request.user, customer, d["user"], d["valid_from"], d["valid_until"],
                                          d["reason"]),
            success_message="Erişim verildi.", url_for=lambda _: _card_url(customer, "erisim"))
        if resp:
            return resp
    return render(request, "core/form_page.html", {"form": form, "title": f"{customer.code}: müşteri erişimi ver",
                                                   "submit": "Erişim ver", "back": _card_url(customer, "erisim")})


@require_POST
@require_perm("customer.access.manage")
def access_revoke(request, pk, access_pk):
    access = CustomerAccess.objects.filter(customer_id=pk, pk=access_pk).first()
    if access is None:
        raise Http404
    url = _card_url(access.customer, "erisim")
    resp = run_form_action(request, ReasonForm(request.POST), action="customer.access_revoke",
                           fn=lambda d: svc.revoke_access(request.user, access, d["reason"]),
                           success_message="Erişim kaldırıldı.", url_for=lambda _: url)
    return resp or redirect(url)


# --- Müşteri–satışçı erişim incelemesi ----------------------------------------------------

def _parse_date(value):
    import datetime
    try:
        return datetime.date.fromisoformat(value) if value else None
    except ValueError:
        return None


@require_perm("customer.access.manage")
def access_review(request):
    """Yönetici: aktif erişimleri kaynağıyla listeler; seçilenleri önce önizler, sonra onaylar/süreliye çevirir/
    kaldırır. Varsayılan görünüm yalnız inceleme bekleyenler (toplu taşınan ve kaynağı belirsiz)."""
    from core.forms import ConfirmForm
    from .models import AccessSource
    qs = svc.access_review_queryset()
    f = {k: request.GET.get(k, "") for k in ("durum", "kaynak", "satisci", "musteri")}
    if f["durum"] != "tumu":
        qs = qs.filter(needs_review=True)
    if f["kaynak"] in AccessSource.values:
        qs = qs.filter(source=f["kaynak"])
    if f["satisci"]:
        qs = qs.filter(user__personnel_no=f["satisci"])
    if f["musteri"]:
        qs = qs.filter(customer__code__iexact=f["musteri"])
    ctx = {"rows": qs[:500], "total": qs.count(), "f": f, "sources": AccessSource.choices,
           "actions": svc.REVIEW_ACTIONS, "pending": svc.access_review_queryset().filter(needs_review=True).count()}
    if request.method == "POST":
        ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
        action, reason = request.POST.get("islem", ""), request.POST.get("reason", "").strip()
        valid_until = _parse_date(request.POST.get("valid_until", ""))
        if request.POST.get("adim") == "uygula":
            resp = run_form_action(
                request, ConfirmForm(request.POST), action="customer.access_review",
                fn=lambda d: svc.review_accesses(request.user, ids, action, reason, valid_until),
                success_message=lambda done: f"{len(done)} erişim işlendi.",
                url_for=lambda _: reverse("catalog:access_review"))
            if resp:
                return resp
        try:
            ctx["preview"] = svc.preview_review(ids, action)
        except Exception as exc:  # noqa: BLE001 - kullanıcıya iş kuralı mesajı
            from django.contrib import messages
            messages.error(request, getattr(exc, "message", str(exc)))
            return render(request, "catalog/access_review.html", ctx)
        ctx.update(ids=ids, islem=action, islem_label=svc.REVIEW_ACTIONS[action], reason=reason,
                   valid_until=valid_until, users=sorted({r.user.full_name for r in ctx["preview"]}),
                   customers=sorted({r.customer.code for r in ctx["preview"]}))
    return render(request, "catalog/access_review.html", ctx)
