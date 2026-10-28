"""Reçete taslağı ve karışım hesabı.

Oranlar nihai karışımın yüzdesidir (toplam %100). "100 kg ana malzemeye ilave katkı" yöntemi kullanılmaz ve eski
revizyonlar yeniden yorumlanmaz (mevcut revizyonlar da nihai karışım yüzdesiyle kayıtlıdır).

Hesap hassasiyeti: oran %0,001; kg karşılıkları 0,001 kg'ye ROUND_HALF_UP yuvarlanır. Yuvarlanmış kg'lerin toplamı
parti büyüklüğünden farklıysa fark ayrıca gösterilir (gizlenmez, bir satıra eklenmez).
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from accounts.permissions import check
from core.exceptions import BusinessRuleError
from core.services import audit

from . import spec as specdef
from .models import DraftStatus, MaterialUnit, Product, RecipeDraft, RecipeDraftLine

KG_STEP = Decimal("0.001")
PCT_STEP = Decimal("0.001")
HUNDRED = Decimal("100")


def _to_json(values):
    out = {}
    for name in specdef.FIELD_NAMES:
        v = values.get(name)
        out[name] = None if v in (None, "") else (str(v) if not isinstance(v, (int, str)) else v)
    return out


def spec_from_json(data):
    out = {}
    for f in specdef.FIELDS:
        v = (data or {}).get(f.name)
        if v in (None, ""):
            out[f.name] = None if f.kind in ("decimal", "int") else ""
        elif f.kind == "decimal":
            out[f.name] = Decimal(str(v))
        elif f.kind == "int":
            out[f.name] = int(v)
        else:
            out[f.name] = str(v)
    return out


def batch_table(lines, batch_kg):
    """[(satır, kg)] + toplamlar. Eksik oranlı satırın kg'si hesaplanmaz."""
    batch_kg = Decimal(batch_kg)
    rows, total_pct, total_kg = [], Decimal("0"), Decimal("0")
    for ln in lines:
        pct = ln.percentage
        kg = None if pct is None else (batch_kg * pct / HUNDRED).quantize(KG_STEP, rounding=ROUND_HALF_UP)
        rows.append((ln, kg))
        total_pct += pct or 0
        total_kg += kg or 0
    rounding_diff = (total_kg - batch_kg) if total_pct == HUNDRED else None
    return {"rows": rows, "total_pct": total_pct, "total_kg": total_kg, "batch_kg": batch_kg,
            "rounding_diff": rounding_diff if rounding_diff else None, "missing_pct": HUNDRED - total_pct}


def total_message(total):
    from core.templatetags.mimcor import qty
    if total == HUNDRED:
        return "Karışım toplamı %100. Onaylanabilir."
    return f"Karışım toplamı %{qty(total)}. Onaylamak için %100 olmalı."


def _validate_lines(lines, final):
    seen = set()
    for i, (material, pct, _note) in enumerate(lines, start=1):
        if material.pk in seen:
            raise BusinessRuleError(f"{material.code} reçetede iki kez yer alamaz; satırları birleştirin.",
                                    field=f"material_{i - 1}")
        seen.add(material.pk)
        if not material.is_active:
            raise BusinessRuleError(f"{material.code} pasif; reçeteye eklenemez.", field=f"material_{i - 1}")
        if material.unit != MaterialUnit.KG:
            raise BusinessRuleError(f"{material.code} birimi {material.get_unit_display()}; karışım oranı yalnız kg "
                                    "birimli malzemelerle kurulur (birim dönüşümü yapılmaz).", field=f"material_{i - 1}")
        if pct is not None and (pct < 0 or pct > HUNDRED):
            raise BusinessRuleError(f"{material.code} oranı 0 ile 100 arasında olmalı.", field=f"pct_{i - 1}")
        if pct is not None and pct != pct.quantize(PCT_STEP):
            raise BusinessRuleError("Oran en fazla 3 ondalık basamak olabilir (ör. 2,125).", field=f"pct_{i - 1}")
        if final and (pct is None or pct == 0):
            raise BusinessRuleError(f"{material.code} için oran girilmedi; satırı silin veya oranı yazın.",
                                    field=f"pct_{i - 1}")


@transaction.atomic
def open_draft(actor, product, based_on=None, spec_overrides=None, reason=""):
    """Ürünün açık taslağını döndürür; yoksa güncel (veya verilen) revizyondan yeni taslak açar."""
    check(actor, "recipe.change")
    product = Product.objects.select_for_update().get(pk=product.pk)
    if not product.is_active:
        raise BusinessRuleError("Pasif ürüne reçete taslağı açılamaz.")
    existing = RecipeDraft.objects.filter(product=product, status=DraftStatus.OPEN).first()
    if existing:
        return existing
    base = based_on or product.current_revision
    values = {n: getattr(base, n) for n in specdef.FIELD_NAMES} if base else {}
    values.update(spec_overrides or {})
    draft = RecipeDraft.objects.create(product=product, based_on=base, spec_values=_to_json(values),
                                       change_reason=reason, created_by=actor, updated_by=actor)
    if base:
        for i, c in enumerate(base.components.select_related("material").order_by("-percentage", "material__code")):
            RecipeDraftLine.objects.create(draft=draft, position=i, material=c.material, percentage=c.percentage,
                                           note=c.application_note)
    audit(actor, "recipe.draft_open", product, {"taslak": (None, draft.pk),
                                                "temel": (None, f"Rev.{base.rev_no}" if base else None)},
          reason=reason, code=product.code)
    return draft


def _lock_open(draft):
    draft = RecipeDraft.objects.select_for_update().select_related("product").get(pk=draft.pk)
    if draft.status != DraftStatus.OPEN:
        raise BusinessRuleError("Bu taslak artık açık değil (onaylanmış veya vazgeçilmiş).")
    return draft


@transaction.atomic
def save_draft(actor, draft, *, spec_values, lines, change_reason="", batch_kg=None):
    """Eksik tarif kaydedilebilir. lines: [(Material, Decimal|None, not)]"""
    check(actor, "recipe.change")
    draft = _lock_open(draft)
    _validate_lines(lines, final=False)
    if batch_kg is not None and batch_kg <= 0:
        raise BusinessRuleError("Parti büyüklüğü sıfırdan büyük olmalı.", field="batch_kg")
    draft.spec_values = _to_json(specdef.clean_hidden({n: spec_values.get(n) for n in specdef.FIELD_NAMES}))
    draft.change_reason = change_reason
    if batch_kg is not None:
        draft.batch_kg = batch_kg
    draft.updated_by = actor
    draft.save()
    draft.lines.all().delete()
    for i, (material, pct, note) in enumerate(lines):
        RecipeDraftLine.objects.create(draft=draft, position=i, material=material, percentage=pct, note=note or "")
    return draft


@transaction.atomic
def approve_draft(actor, draft):
    """Toplam tam %100 ve teknik zorunlular tamamsa değişmez revizyon oluşturur. Kullanılmış revizyonlar ve onları
    kullanan sipariş kalemleri / iş emirleri değişmez (yeni revizyon numarası)."""
    from .services import create_revision
    check(actor, "recipe.change")
    draft = _lock_open(draft)
    lines = list(draft.lines.select_related("material"))
    if not lines:
        raise BusinessRuleError("En az bir malzeme satırı ekleyin.")
    _validate_lines([(ln.material, ln.percentage, ln.note) for ln in lines], final=True)
    total = sum((ln.percentage for ln in lines), Decimal("0"))
    if total != HUNDRED:
        raise BusinessRuleError(total_message(total))
    values = spec_from_json(draft.spec_values)
    revision = create_revision(actor, draft.product, values, [(ln.material, ln.percentage) for ln in lines],
                               draft.change_reason, notes={ln.material_id: ln.note for ln in lines})
    draft.status, draft.result_revision, draft.updated_by = DraftStatus.APPROVED, revision, actor
    draft.save()
    return revision


@transaction.atomic
def discard_draft(actor, draft, reason):
    check(actor, "recipe.change")
    if not reason:
        raise BusinessRuleError("Gerekçe zorunludur.", field="reason")
    draft = _lock_open(draft)
    draft.status, draft.updated_by = DraftStatus.DISCARDED, actor
    draft.save()
    audit(actor, "recipe.draft_discard", draft.product, {"taslak": (draft.pk, None)}, reason=reason,
          code=draft.product.code)
    return draft
