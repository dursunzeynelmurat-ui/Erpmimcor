from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max

from accounts.permissions import check
from accounts.scope import customer_in_scope
from core.exceptions import BusinessRuleError
from core.services import audit, diff, next_code

from .models import (Customer, LotMovement, LotMovementKind, Machine, Material, MaterialLot, MaterialUnit, Product,
                     Recipe, RecipeComponent, RecipeRevision)

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
            raise BusinessRuleError("Paket içi adet sıfırdan büyük olmalı.", field="units_per_box")
        if data.get("nominal_package_kg") is not None and data["nominal_package_kg"] <= 0:
            raise BusinessRuleError("Tahmini paket ağırlığı sıfırdan büyük olmalı (bilinmiyorsa boş bırakın).",
                                    field="nominal_package_kg")
    if model is Material:
        _validate_material_unit(data.get("unit", MaterialUnit.KG))
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
        raise BusinessRuleError("Paket içi adet sıfırdan büyük olmalı.", field="units_per_box")
    if model is Product and data.get("nominal_package_kg") is not None and data["nominal_package_kg"] <= 0:
        raise BusinessRuleError("Tahmini paket ağırlığı sıfırdan büyük olmalı.", field="nominal_package_kg")
    if model is Material and "unit" in changes:
        _validate_material_unit(data["unit"])
        if LotMovement.objects.filter(lot__material=obj).exists():
            raise BusinessRuleError("Hareket görmüş malzemenin birimi değiştirilemez (miktarlar eski birimle kayıtlı). "
                                    "Yeni birim için yeni malzeme kodu açın.", field="unit")
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


def _validate_material_unit(unit):
    if unit not in MaterialUnit.values:
        raise BusinessRuleError(f"Malzeme birimi şunlardan biri olmalı: {', '.join(MaterialUnit.values)}.", field="unit")


SPEC_POSITIVE = (("width_mm", "En"), ("thickness_micron", "Kalınlık"), ("length_m", "Rulo boyu"))


def validate_spec(spec, components):
    """Reçete/teknik bilgi doğrulaması: zorunlu ölçüler, pozitif ölçüler, körüklüde en tanımı, oranlar 0–100."""
    from . import spec as specdef
    for field, label in SPEC_POSITIVE:
        value = spec.get(field)
        if value is None and field != "length_m":
            raise BusinessRuleError(f"{label} zorunludur.", field=field)
        if value is not None and value <= 0:
            raise BusinessRuleError(f"{label} sıfırdan büyük olmalı.", field=field)
    for f in specdef.FIELDS:
        value = spec.get(f.name)
        if f.kind in ("decimal", "int") and value is not None and value <= 0:
            raise BusinessRuleError(f"{f.label} sıfırdan büyük olmalı.", field=f.name)
        if f.kind == "choice" and value and value not in f.choices.values:
            raise BusinessRuleError(f"{f.label} için geçersiz seçim.", field=f.name)
    for name, message in specdef.approval_problems(spec):
        raise BusinessRuleError(message, field=name)
    for material, pct in components:
        if pct is None or Decimal(pct) <= 0 or Decimal(pct) > 100:
            raise BusinessRuleError(f"{material.code} oranı 0 ile 100 arasında olmalı.")


# --- Reçete revizyonları -------------------------------------------------------------

@transaction.atomic
def create_revision(actor, product, spec, components, reason="", notes=None):
    """Reçeteyi değiştirmenin tek yolu: yeni revizyon. Önceki revizyonlar aynen korunur.

    components: [(Material, Decimal yüzde), ...] toplamı 100 olmalı.
    """
    check(actor, "recipe.change")
    product = Product.objects.select_for_update().get(pk=product.pk)
    if not product.is_active:
        raise BusinessRuleError("Pasif ürüne reçete revizyonu açılamaz.")
    if not components:
        raise BusinessRuleError("En az bir reçete bileşeni girilmelidir.")
    from . import spec as specdef
    unknown = set(spec) - set(specdef.FIELD_NAMES)
    if unknown:
        raise BusinessRuleError("Tanımsız teknik alan: " + ", ".join(sorted(unknown)))
    spec = {n: spec.get(n) for n in specdef.FIELD_NAMES}
    for f in specdef.FIELDS:  # servis doğrudan çağrıldığında metin sayılar da güvenle çevrilir
        v = spec[f.name]
        if isinstance(v, str) and f.kind in ("decimal", "int"):
            try:
                spec[f.name] = (Decimal(v.replace(",", ".")) if f.kind == "decimal" else int(v)) if v.strip() else None
            except Exception:
                raise BusinessRuleError(f"{f.label} sayı olmalı.", field=f.name)
    spec = specdef.clean_hidden(spec)
    spec = {n: ("" if v is None and specdef.BY_NAME[n].kind not in ("decimal", "int") else v) for n, v in spec.items()}
    validate_spec(spec, components)
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
        RecipeComponent.objects.create(revision=revision, material=material, percentage=pct,
                                       application_note=(notes or {}).get(material.pk, ""))
    audit(actor, "recipe.revision_create", product,
          {"revizyon": (last or None, revision.rev_no),
           "bileşenler": (None, {m.code: str(p) for m, p in components}),
           **{k: (None, v) for k, v in spec.items() if v not in (None, "")}},
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
    if not (supplier_lot or "").strip():
        # Mal kabulü her zaman yeni bir lot açar ve tedarikçi belgesine bağlanır; mevcut lottaki sayım farkını
        # kapatmak için kullanılamaz (o iş düzeltme talebi ile yapılır).
        raise BusinessRuleError("Mal kabulünde tedarikçi parti / irsaliye no zorunludur.", field="supplier_lot")
    lot = MaterialLot.objects.create(code=next_code("LOT"), material=material, supplier_lot=supplier_lot)
    move_lot(actor, lot, LotMovementKind.RECEIPT, quantity, _skip_perm=True)
    return lot


def move_lot(actor, lot, kind, quantity, work_order=None, reason="", _skip_perm=False):
    """Lot miktarını değiştirmenin tek yolu. quantity işaretli (+ giriş, - çıkış).
    Bakiye negatife düşerse işlem tümüyle reddedilir.
    Giriş yalnız receive_lot, sayım düzeltmesi yalnız onaylı düzeltme talebi (corrections) üzerinden yazılır;
    bu iki tür doğrudan çağrıda reddedilir (_skip_perm yalnız bu iki iç servis içindir)."""
    if not _skip_perm:
        if kind in (LotMovementKind.ADJUST, LotMovementKind.RECEIPT):
            raise PermissionDenied("Lot girişi mal kabulüyle, sayım farkı onaylı düzeltme talebiyle kaydedilir.")
        check(actor, "production.execute")
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

