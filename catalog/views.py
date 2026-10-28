from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from django.http import Http404
from django.views.decorators.http import require_POST

from accounts.permissions import can, check, get_scoped_or_404, require_perm, scoped
from core.forms import ConfirmForm, ReasonForm
from core.services import history_for
from core.viewutils import run_form_action

from . import recipe_drafts, services
from .forms import (ActiveToggleForm, LotReceiveForm, MachineForm, MaterialForm,
                    ProductForm, RecipeDraftForm)
from .models import Customer, DraftStatus, Machine, Material, MaterialLot, Product, Recipe, RecipeDraft

# Basit tanımlar için ortak liste / ekle / düzenle / pasife al ekranları.
DEFINITIONS = {
    "urunler": {"model": Product, "form": ProductForm, "title": "Ürünler", "singular": "Ürün", "view": "product.view",
                "columns": [("code", "Ürün kodu"), ("customer", "Müşteri"), ("name", "Ad"), ("get_unit_display", "Birim")]},
    "makineler": {"model": Machine, "form": MachineForm, "title": "Makineler", "singular": "Makine", "view": "machine.view",
                  "columns": [("code", "Kod"), ("name", "Ad"), ("get_machine_type_display", "Tür")]},
    "malzemeler": {"model": Material, "form": MaterialForm, "title": "Hammadde ve katkılar", "singular": "Malzeme",
                   "view": "material.view",
                   "columns": [("code", "Kod"), ("name", "Ad"), ("get_kind_display", "Tür"), ("unit", "Birim")]},
}


def _cfg(kind):
    cfg = DEFINITIONS.get(kind)
    if not cfg:
        raise Http404
    return cfg


def _perms(cfg):
    return services.DEFINITION_PERMS[cfg["model"]]


def definition_list(request, kind):
    if kind == "musteriler":
        return redirect("catalog:customer_list")
    cfg = _cfg(kind)
    check(request.user, cfg["view"])
    qs = scoped(cfg["model"].objects.all(), request.user, cfg["view"])
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
        "can_manage": can(request.user, _perms(cfg)[0]),
    })


def _cell(obj, field):
    v = getattr(obj, field)
    return v() if callable(v) else v


def definition_create(request, kind):
    if kind == "musteriler":
        return redirect("catalog:customer_create")
    cfg = _cfg(kind)
    check(request.user, _perms(cfg)[0])
    form = cfg["form"](request.POST or None, user=request.user) if kind == "urunler" else cfg["form"](request.POST or None)
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
    if kind == "musteriler":
        return redirect("catalog:customer_detail", pk=pk)
    cfg = _cfg(kind)
    obj = get_scoped_or_404(cfg["model"].objects.all(), request.user, cfg["view"], pk=pk)
    manage = can(request.user, _perms(cfg)[1], obj)
    form = cfg["form"](request.POST if (request.method == "POST" and manage) else None, instance=obj)
    if not manage:
        for f in form.fields.values():
            f.disabled = True
    if request.method == "POST":
        check(request.user, _perms(cfg)[1], obj)
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
    if kind == "urunler" and can(request.user, "recipe.view"):
        ctx["recipe"] = Recipe.objects.filter(product=obj).first()
    elif kind == "urunler":  # reçete revizyon kayıtları (karışım oranları) yetkisiz kullanıcıya gösterilmez; silinmez
        ctx["history"] = ctx["history"].exclude(action__startswith="recipe.")
    return render(request, "catalog/definition_edit.html", ctx)


def definition_toggle(request, kind, pk):
    cfg = _cfg(kind)
    obj = get_scoped_or_404(cfg["model"].objects.all(), request.user, cfg["view"], pk=pk)
    check(request.user, _perms(cfg)[1], obj)
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
    check(request.user, "recipe.view")
    product = get_object_or_404(Product.objects.select_related("customer"), pk=product_pk)
    recipe = Recipe.objects.filter(product=product).first()
    revisions = recipe.revisions.prefetch_related("components__material").select_related("created_by") if recipe else []
    return render(request, "catalog/recipe_detail.html", {
        "product": product, "recipe": recipe, "revisions": revisions,
        "open_draft": RecipeDraft.objects.filter(product=product, status=DraftStatus.OPEN).first(),
    })


@require_perm("recipe.change")
def revision_create(request, product_pk):
    """Yeni revizyon her zaman taslak üzerinden: açık taslak varsa ona gider, yoksa güncel revizyondan açar."""
    from django.contrib import messages as msgs
    product = get_object_or_404(Product, pk=product_pk)
    draft = RecipeDraft.objects.filter(product=product, status=DraftStatus.OPEN).first()
    if draft:
        return redirect("catalog:recipe_draft", pk=draft.pk)
    if request.method == "POST":
        resp = run_form_action(request, ConfirmForm(request.POST), action="recipe.draft_open",
                               fn=lambda d: recipe_drafts.open_draft(request.user, product),
                               success_message="Reçete taslağı açıldı. Değişiklikleri yapıp kaydedin.",
                               url_for=lambda dr: reverse("catalog:recipe_draft", args=[dr.pk]))
        if resp:
            return resp
        msgs.error(request, "Taslak açılamadı.")
    return render(request, "core/form_page.html", {
        "form": ConfirmForm(), "title": f"{product.code}: reçete taslağı aç", "submit": "Taslak aç",
        "intro": "Değişiklikler önce taslağa kaydedilir; onaylanınca yeni revizyon oluşur. Kullanılmış revizyonlar ve "
                 "başlamış iş emirleri değişmez.", "back": reverse("catalog:recipe_detail", args=[product.pk])})


@require_perm("recipe.change")
def recipe_draft(request, pk):
    from django.contrib import messages as msgs
    from core.exceptions import BusinessRuleError
    draft = get_object_or_404(RecipeDraft.objects.select_related("product__customer", "based_on"), pk=pk)
    if draft.status != DraftStatus.OPEN:
        msgs.info(request, "Bu taslak kapanmış.")
        return redirect("catalog:recipe_detail", product_pk=draft.product_id)
    lines = list(draft.lines.select_related("material"))
    extra = 1 if request.GET.get("ek") or not lines else 0
    initial = {**recipe_drafts.spec_from_json(draft.spec_values), "batch_kg": draft.batch_kg,
               "change_reason": draft.change_reason}
    form = RecipeDraftForm(request.POST or None, initial=initial, rows=len(lines) + extra,
                           initial_lines=[(ln.material, ln.percentage, ln.note) for ln in lines])
    if request.method == "POST":
        action = request.POST.get("git", "kaydet")
        resp = run_form_action(
            request, form, action="recipe.draft_save",
            fn=lambda d: recipe_drafts.save_draft(request.user, draft, spec_values=d["spec"], lines=d["lines"],
                                                  change_reason=d["change_reason"], batch_kg=d["batch_kg"]),
            success_message="Taslak kaydedildi." if action != "onayla" else "Taslak kaydedildi; onay kontrol ediliyor.",
            url_for=lambda dr: reverse("catalog:recipe_draft", args=[dr.pk]) + ("?ek=1" if action == "satir" else ""))
        if resp and action == "onayla":
            try:
                rev = recipe_drafts.approve_draft(request.user, draft)
            except BusinessRuleError as exc:
                msgs.error(request, f"Onaylanmadı (taslak kaydedildi): {exc.message}")
                return resp
            msgs.success(request, f"Reçete onaylandı: {draft.product.code} Rev.{rev.rev_no}. Sıradaki adım: planlama "
                                  "bekleyen sipariş kalemlerini bu revizyonla onaylayabilir.")
            return redirect("catalog:recipe_detail", product_pk=draft.product_id)
        if resp:
            return resp
    table = recipe_drafts.batch_table(lines, draft.batch_kg)
    return render(request, "catalog/recipe_draft.html", {
        "draft": draft, "form": form, "table": table, "total_message": recipe_drafts.total_message(table["total_pct"]),
        "discard_form": ReasonForm(), "can_manage_material": can(request.user, "material.manage"),
    })


@require_POST
@require_perm("recipe.change")
def recipe_draft_discard(request, pk):
    draft = get_object_or_404(RecipeDraft, pk=pk)
    url = reverse("catalog:recipe_detail", args=[draft.product_id])
    resp = run_form_action(request, ReasonForm(request.POST), action="recipe.draft_discard",
                           fn=lambda d: recipe_drafts.discard_draft(request.user, draft, d["reason"]),
                           success_message="Taslaktan vazgeçildi; mevcut revizyonlar değişmedi.", url_for=lambda _: url)
    return resp or redirect("catalog:recipe_draft", pk=pk)


# --- Lotlar -------------------------------------------------------------------------

def lot_list(request):
    check(request.user, "material.view")
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
    check(request.user, "material.view")
    lot = get_object_or_404(MaterialLot.objects.select_related("material"), pk=pk)
    if request.method == "POST":
        # Eski doğrudan sayım formu kaldırıldı: miktar yalnız onaylı düzeltme talebiyle değişir.
        from django.contrib import messages
        messages.warning(request, "Lot miktarı bu ekrandan değiştirilemez. Sayım farkı için düzeltme talebi açın "
                                  "veya talep yetkiniz yoksa üretim/ambar yetkilisine bildirin.")
        return redirect("catalog:lot_detail", pk=lot.pk)
    return render(request, "catalog/lot_detail.html", {
        "lot": lot, "movements": lot.movements.select_related("user", "work_order"),
        "can_request": can(request.user, "correction.request", lot),
    })
