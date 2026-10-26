from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.permissions import can, check, require_perm
from core.services import history_for
from core.viewutils import run_form_action

from . import services
from .forms import (ActiveToggleForm, CustomerForm, LotAdjustForm, LotReceiveForm, MachineForm, MaterialForm,
                    ProductForm, RevisionForm)
from .models import Customer, Machine, Material, MaterialLot, Product, Recipe

# Basit tanımlar için ortak liste / ekle / düzenle / pasife al ekranları.
DEFINITIONS = {
    "musteriler": {"model": Customer, "form": CustomerForm, "title": "Müşteriler", "singular": "Müşteri",
                   "columns": [("code", "Kod"), ("name", "Ünvan"), ("contact", "İletişim")]},
    "urunler": {"model": Product, "form": ProductForm, "title": "Ürünler", "singular": "Ürün",
                "columns": [("code", "Ürün kodu"), ("customer", "Müşteri"), ("name", "Ad"), ("get_unit_display", "Birim")]},
    "makineler": {"model": Machine, "form": MachineForm, "title": "Makineler", "singular": "Makine",
                  "columns": [("code", "Kod"), ("name", "Ad"), ("get_machine_type_display", "Tür")]},
    "malzemeler": {"model": Material, "form": MaterialForm, "title": "Hammadde ve katkılar", "singular": "Malzeme",
                   "columns": [("code", "Kod"), ("name", "Ad"), ("get_kind_display", "Tür"), ("unit", "Birim")]},
}


def _cfg(kind):
    cfg = DEFINITIONS.get(kind)
    if not cfg:
        from django.http import Http404
        raise Http404
    return cfg


def definition_list(request, kind):
    cfg = _cfg(kind)
    check(request.user, "view.catalog")
    qs = cfg["model"].objects.all()
    if kind == "urunler":
        qs = qs.select_related("customer")
    show = request.GET.get("durum", "aktif")
    if show == "aktif":
        qs = qs.filter(is_active=True)
    elif show == "pasif":
        qs = qs.filter(is_active=False)
    rows = [{"obj": o, "cells": [_cell(o, f) for f, _ in cfg["columns"]]} for o in qs]
    return render(request, "catalog/definition_list.html", {
        "cfg": cfg, "kind": kind, "rows": rows, "show": show,
        "can_manage": can(request.user, services.DEFINITION_PERMS[cfg["model"]]),
    })


def _cell(obj, field):
    v = getattr(obj, field)
    return v() if callable(v) else v


def definition_create(request, kind):
    cfg = _cfg(kind)
    check(request.user, services.DEFINITION_PERMS[cfg["model"]])
    form = cfg["form"](request.POST or None)
    if request.method == "POST":
        fields = cfg["form"].Meta.fields
        resp = run_form_action(
            request, form, action=f"{kind}.create",
            fn=lambda d: services.create_definition(request.user, cfg["model"], {f: d[f] for f in fields}),
            success_message=lambda o: f"{cfg['singular']} kaydedildi: {o}",
            url_for=lambda o: reverse("catalog:definition_edit", args=[kind, o.pk]),
        )
        if resp:
            return resp
    return render(request, "core/form_page.html", {"form": form, "title": f"Yeni {cfg['singular'].lower()}",
                                                   "submit": "Kaydet", "back": reverse("catalog:definition_list", args=[kind])})


def definition_edit(request, kind, pk):
    cfg = _cfg(kind)
    obj = get_object_or_404(cfg["model"], pk=pk)
    manage = can(request.user, services.DEFINITION_PERMS[cfg["model"]])
    form = cfg["form"](request.POST if (request.method == "POST" and manage) else None, instance=obj)
    if not manage:
        for f in form.fields.values():
            f.disabled = True
    if request.method == "POST":
        check(request.user, services.DEFINITION_PERMS[cfg["model"]])
        editable = [f for f in cfg["form"].Meta.fields if not form.fields[f].disabled]
        resp = run_form_action(
            request, form, action=f"{kind}.update",
            fn=lambda d: services.update_definition(request.user, obj, {f: d[f] for f in editable}),
            success_message="Değişiklikler kaydedildi.",
            url_for=lambda o: reverse("catalog:definition_edit", args=[kind, o.pk]),
        )
        if resp:
            return resp
    obj.refresh_from_db()
    ctx = {"form": form, "title": f"{cfg['singular']}: {obj}", "submit": "Kaydet" if manage else None,
           "obj": obj, "kind": kind, "history": history_for(obj), "toggle_form": ActiveToggleForm(auto_id="toggle_%s") if manage else None,
           "back": reverse("catalog:definition_list", args=[kind])}
    if kind == "urunler":
        ctx["recipe"] = Recipe.objects.filter(product=obj).first()
    return render(request, "catalog/definition_edit.html", ctx)


def definition_toggle(request, kind, pk):
    cfg = _cfg(kind)
    obj = get_object_or_404(cfg["model"], pk=pk)
    check(request.user, services.DEFINITION_PERMS[cfg["model"]])
    form = ActiveToggleForm(request.POST or None)
    url = reverse("catalog:definition_edit", args=[kind, pk])
    if request.method == "POST":
        resp = run_form_action(
            request, form, action=f"{kind}.toggle",
            fn=lambda d: services.set_active(request.user, obj, not obj.is_active, d["reason"]),
            success_message=lambda o: "Kayıt aktifleştirildi." if o.is_active else "Kayıt pasife alındı.",
            url_for=lambda o: url,
        )
        if resp:
            return resp
    return redirect(url)


# --- Reçete -------------------------------------------------------------------------

def recipe_detail(request, product_pk):
    check(request.user, "view.catalog")
    product = get_object_or_404(Product.objects.select_related("customer"), pk=product_pk)
    recipe = Recipe.objects.filter(product=product).first()
    revisions = recipe.revisions.prefetch_related("components__material").select_related("created_by") if recipe else []
    return render(request, "catalog/recipe_detail.html", {
        "product": product, "recipe": recipe, "revisions": revisions,
    })


@require_perm("recipe.manage")
def revision_create(request, product_pk):
    product = get_object_or_404(Product, pk=product_pk)
    current = product.current_revision
    initial, comps = {}, []
    if current:
        initial = {f: getattr(current, f) for f in RevisionForm.Meta.fields if f != "change_reason"}
        comps = [(c.material, c.percentage) for c in current.components.all()]
    form = RevisionForm(request.POST or None, initial=initial, initial_components=comps)
    if request.method == "POST":
        resp = run_form_action(
            request, form, action="recipe.revision_create",
            fn=lambda d: services.create_revision(request.user, product, form.spec(), d["components"],
                                                  d.get("change_reason", "")),
            success_message=lambda r: f"Reçete revizyonu oluşturuldu: Rev.{r.rev_no}",
            url_for=lambda r: reverse("catalog:recipe_detail", args=[product.pk]),
        )
        if resp:
            return resp
    return render(request, "catalog/revision_form.html", {"form": form, "product": product, "current": current})


# --- Lotlar -------------------------------------------------------------------------

def lot_list(request):
    check(request.user, "view.catalog")
    lots = MaterialLot.objects.select_related("material")
    if request.GET.get("tumu") != "1":
        lots = lots.filter(quantity__gt=0)
    return render(request, "catalog/lot_list.html", {"lots": lots})


@require_perm("lot.receive")
def lot_receive(request):
    form = LotReceiveForm(request.POST or None)
    if request.method == "POST":
        resp = run_form_action(
            request, form, action="lot.receive",
            fn=lambda d: services.receive_lot(request.user, d["material"], d["quantity"], d["supplier_lot"]),
            success_message=lambda lot: f"Lot girişi yapıldı: {lot.code}",
            url_for=lambda lot: reverse("catalog:lot_detail", args=[lot.pk]),
        )
        if resp:
            return resp
    return render(request, "core/form_page.html", {"form": form, "title": "Malzeme lot girişi", "submit": "Kaydet",
                                                   "back": reverse("catalog:lot_list")})


def lot_detail(request, pk):
    check(request.user, "view.catalog")
    lot = get_object_or_404(MaterialLot.objects.select_related("material"), pk=pk)
    form = LotAdjustForm(request.POST or None) if can(request.user, "lot.adjust") else None
    if request.method == "POST" and form is not None:
        resp = run_form_action(
            request, form, action="lot.adjust",
            fn=lambda d: services.adjust_lot_to(request.user, lot, d["counted"], d["reason"]),
            success_message="Sayım düzeltmesi kaydedildi.",
            url_for=lambda mv: reverse("catalog:lot_detail", args=[lot.pk]),
        )
        if resp:
            return resp
    return render(request, "catalog/lot_detail.html", {
        "lot": lot, "movements": lot.movements.select_related("user", "work_order"), "form": form,
    })
