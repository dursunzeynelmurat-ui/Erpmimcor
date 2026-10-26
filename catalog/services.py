from decimal import Decimal

from django.db import transaction
from django.db.models import Max

from accounts.permissions import check
from accounts.scope import customer_in_scope
from core.exceptions import BusinessRuleError
from core.services import audit, diff, next_code

from .models import (Customer, LotMovement, LotMovementKind, Machine, Material, MaterialLot, Product, Recipe,
                     RecipeComponent, RecipeRevision)

# Model → (oluşturma yetkisi, değiştirme yetkisi)
DEFINITION_PERMS = {
    Customer: ("customer.create", "customer.change"),
    Product: ("product.create", "product.change"),
    Machine: ("machine.manage", "machine.manage"),
    Material: ("material.manage", "material.manage"),
}
# Oluşturulduktan sonra değişmeyen alanlar (ürün kodu tek müşteriye bağlıdır).
IMMUTABLE_FIELDS = {Product: {"code", "customer"}, Customer: {"code"}, Machine: {"code"}, Material: {"code"}}


@transaction.atomic
def create_definition(actor, model, data):
    if model is Customer:
        from .customers import create_customer
        return create_customer(actor, data, confirm_similar=True)
    check(actor, DEFINITION_PERMS[model][0])
    if model is Product:
        if not data["customer"].is_active:
            raise BusinessRuleError("Pasif müşteriye ürün tanımlanamaz.", field="customer")
        if not customer_in_scope(actor, "product.create", data["customer"]):
            raise BusinessRuleError("Bu müşteriye erişiminiz yok.", field="customer")
        if data.get("units_per_box") is not None and data["units_per_box"] <= 0:
            raise BusinessRuleError("Kutu içi adet sıfırdan büyük olmalı.", field="units_per_box")
    obj = model.objects.create(**data)
    audit(actor, f"{model._meta.model_name}.create", obj, {k: (None, v) for k, v in data.items()})
    return obj


@transaction.atomic
def update_definition(actor, obj, data, reason=""):
    model = type(obj)
    check(actor, DEFINITION_PERMS[model][1], obj)
    obj = model.objects.select_for_update().get(pk=obj.pk)
    blocked = IMMUTABLE_FIELDS.get(model, set()) & set(data)
    changes = diff(obj, data)
    if blocked & set(changes):
        raise BusinessRuleError(f"Şu alanlar sonradan değiştirilemez: {', '.join(sorted(blocked & set(changes)))}")
    if model is Product and data.get("units_per_box") is not None and data["units_per_box"] <= 0:
        raise BusinessRuleError("Kutu içi adet sıfırdan büyük olmalı.", field="units_per_box")
    for field, (_, new) in changes.items():
        setattr(obj, field, new)
    obj.save()
    if changes:
        audit(actor, f"{model._meta.model_name}.update", obj, changes, reason=reason)
    return obj


@transaction.atomic
def set_active(actor, obj, active, reason):
    """Tanımlar silinmez; pasife alınır veya yeniden aktifleştirilir."""
    model = type(obj)
    check(actor, DEFINITION_PERMS[model][1], obj)
    if not reason:
        raise BusinessRuleError("Gerekçe zorunludur.", field="reason")
    obj = model.objects.select_for_update().get(pk=obj.pk)
    if obj.is_active != active:
        old = obj.is_active
        obj.is_active = active
        obj.save(update_fields=["is_active"])
        audit(actor, f"{model._meta.model_name}.{'activate' if active else 'deactivate'}", obj,
              {"is_active": (old, active)}, reason=reason)
    return obj


# --- Reçete revizyonları -------------------------------------------------------------

@transaction.atomic
def create_revision(actor, product, spec, components, reason=""):
    """Reçeteyi değiştirmenin tek yolu: yeni revizyon. Önceki revizyonlar aynen korunur.

    components: [(Material, Decimal yüzde), ...] toplamı 100 olmalı.
    """
    check(actor, "recipe.change")
    product = Product.objects.select_for_update().get(pk=product.pk)
    if not product.is_active:
        raise BusinessRuleError("Pasif ürüne reçete revizyonu açılamaz.")
    if not components:
        raise BusinessRuleError("En az bir reçete bileşeni girilmelidir.")
    materials = [m for m, _ in components]
    if len(set(m.pk for m in materials)) != len(materials):
        raise BusinessRuleError("Aynı malzeme reçetede iki kez yer alamaz.")
    if any(not m.is_active for m in materials):
        raise BusinessRuleError("Pasif malzeme reçeteye eklenemez.")
    total = sum((Decimal(p) for _, p in components), Decimal("0"))
    if total != Decimal("100"):
        raise BusinessRuleError(f"Bileşen oranlarının toplamı %100 olmalı (şu an %{total}).")
    recipe, _ = Recipe.objects.get_or_create(product=product)
    last = recipe.revisions.aggregate(m=Max("rev_no"))["m"] or 0
    if last and not reason:
        raise BusinessRuleError("Yeni revizyon için değişiklik gerekçesi zorunludur.", field="change_reason")
    revision = RecipeRevision.objects.create(recipe=recipe, rev_no=last + 1, created_by=actor,
                                             change_reason=reason, **spec)
    for material, pct in components:
        RecipeComponent.objects.create(revision=revision, material=material, percentage=pct)
    audit(actor, "recipe.revision_create", product,
          {"revizyon": (last or None, revision.rev_no),
           "bileşenler": (None, {m.code: str(p) for m, p in components}),
           **{k: (None, v) for k, v in spec.items()}},
          reason=reason, code=product.code)
    return revision


# --- Malzeme lotları ---------------------------------------------------------------

@transaction.atomic
def receive_lot(actor, material, quantity, supplier_lot=""):
    check(actor, "lot.receive")
    if not material.is_active:
        raise BusinessRuleError("Pasif malzeme için lot girişi yapılamaz.")
    if quantity <= 0:
        raise BusinessRuleError("Giriş miktarı sıfırdan büyük olmalı.", field="quantity")
    lot = MaterialLot.objects.create(code=next_code("LOT"), material=material, supplier_lot=supplier_lot)
    move_lot(actor, lot, LotMovementKind.RECEIPT, quantity, _skip_perm=True)
    return lot


def move_lot(actor, lot, kind, quantity, work_order=None, reason="", _skip_perm=False):
    """Lot miktarını değiştirmenin tek yolu. quantity işaretli (+ giriş, - çıkış).
    Bakiye negatife düşerse işlem tümüyle reddedilir."""
    if not _skip_perm:
        perm = {LotMovementKind.ADJUST: "lot.adjust"}.get(kind, "production.execute")
        check(actor, perm)
    if kind == LotMovementKind.ADJUST and not reason:
        raise BusinessRuleError("Sayım düzeltmesi için gerekçe zorunludur.", field="reason")
    if quantity == 0:
        raise BusinessRuleError("Miktar sıfır olamaz.", field="quantity")
    with transaction.atomic():
        lot = MaterialLot.objects.select_for_update().get(pk=lot.pk)
        new_balance = lot.quantity + quantity
        if new_balance < 0:
            raise BusinessRuleError(
                f"{lot.code} lotunda yeterli miktar yok (mevcut {lot.quantity}, istenen {-quantity}).", field="quantity")
        old = lot.quantity
        lot.quantity = new_balance
        lot.save(update_fields=["quantity"])
        mv = LotMovement.objects.create(lot=lot, kind=kind, quantity=quantity, balance_after=new_balance,
                                        work_order=work_order, reason=reason, user=actor)
        audit(actor, f"lot.{kind}", lot, {"quantity": (old, new_balance)}, reason=reason)
    return mv


@transaction.atomic
def adjust_lot_to(actor, lot, counted, reason):
    """Sayım sonucu: farkı kilit altında hesaplayıp düzeltme hareketi yazar."""
    check(actor, "lot.adjust")
    lot = MaterialLot.objects.select_for_update().get(pk=lot.pk)
    delta = counted - lot.quantity
    if delta == 0:
        raise BusinessRuleError("Sayılan miktar mevcut miktarla aynı; düzeltme gerekmiyor.", field="counted")
    return move_lot(actor, lot, LotMovementKind.ADJUST, delta, reason=reason)
