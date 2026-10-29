"""Repete ve revizyonlu repete sipariş.

Repete: yetkili müşterinin eski kalemleri seçilir; yeni kalem kaynak kaleme bağlanır (`source_line`). Miktar ve teslim
tarihi yeniden girilir; teslimat bilgisi müşteri kartından alınır ve kullanıcıya doğrulatılır. Üretim, rezervasyon,
sevkiyat, onay ve düzeltme kayıtları kopyalanmaz; kalem planlamanın teknik onayından yine geçer; revizyon
otomatik değişmez. Yerine gönderim kalemi repete kaynağı olamaz (normal satışa dönüşmez).

Revizyonlu repete: satışçı değiştirmek istediği teknik değerleri ve gerekçeyi kaydeder (karışım hariç). Planlama
eski/yeni değerleri görür; yeni revizyon taslağı açar, yeni ürün kodu açar (aynı müşteri) veya reddeder.
Önceki siparişler ve başlamış işler etkilenmez.
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounts.permissions import can, check, scoped
from catalog import spec
from core.exceptions import BusinessRuleError
from core.services import audit

from .models import ChangeStatus, CreationMethod, LineChangeRequest, LineStatus, OPEN_CHANGE_STATES, OrderLine, \
    OrderStatus


def candidate_lines(user, customer):
    """Tekrar edilebilecek eski kalemler (yetkili müşteri, iptal/taslak/yerine gönderim hariç)."""
    qs = scoped(OrderLine.objects.all(), user, "order.view").filter(order__customer=customer)
    return (qs.exclude(status=LineStatus.CANCELLED).exclude(order__status=OrderStatus.DRAFT)
            .filter(replacement_for__isnull=True)
            .select_related("order", "product", "approved_revision").order_by("-order__order_date", "-pk"))


def source_warnings(line):
    out = []
    if not line.product.is_active:
        out.append("Ürün pasif; bu kalem tekrar edilemez.")
    current = line.product.current_revision
    if line.approved_revision is None:
        out.append("Kaynak kalem teknik onaysız kalmış; planlama revizyonu seçecek.")
    elif current and current.pk != line.approved_revision_id:
        out.append(f"Kaynak Rev.{line.approved_revision.rev_no} ile üretildi; ürünün güncel revizyonu Rev.{current.rev_no}. "
                   "Revizyon otomatik değiştirilmez; planlama karar verir.")
    return out


def base_values(line):
    rev = line.approved_revision or line.product.current_revision
    return {n: getattr(rev, n) for n in spec.FIELD_NAMES} if rev else {}


def diff_values(base, requested):
    """Yalnız gerçekten değişen ve izinli alanlar: {alan: (eski, yeni)}"""
    out = {}
    for name in spec.CHANGEABLE:
        if name not in requested:
            continue
        old, new = base.get(name), requested[name]
        norm = lambda v: None if v in (None, "") else (Decimal(str(v)).normalize() if isinstance(v, (Decimal, int, float)) else str(v).strip())  # noqa: E731
        if norm(old) != norm(new):
            out[name] = (old, new)
    return out


def _json(v):
    return None if v in (None, "") else (str(v) if not isinstance(v, (int, str)) else v)


@transaction.atomic
def create_repeat_order(actor, *, customer, items, order_date=None, due_date=None, customer_ref="", notes="",
                        purpose="", charge="", delivery_confirmed=False):
    """items: [{"source_line", "quantity", "due_date", "changes": {alan: yeni değer}, "reason"}]"""
    from . import services
    check(actor, "order.create")
    if not items:
        raise BusinessRuleError("Tekrar edilecek en az bir kalem seçin.")
    if not delivery_confirmed:
        raise BusinessRuleError("Teslimat adresi ve koşullarını kontrol edip işaretleyin.", field="delivery_confirmed")
    allowed = {ln.pk for ln in candidate_lines(actor, customer)}
    revised = False
    prepared = []
    for item in items:
        src = OrderLine.objects.select_related("order", "product", "approved_revision").get(pk=item["source_line"].pk)
        if src.pk not in allowed:
            raise BusinessRuleError("Seçilen kalem bu müşteride tekrar edilebilir kalemler arasında değil.")
        if not src.product.is_active:
            raise BusinessRuleError(f"{src.code}: ürün {src.product.code} pasif; tekrar edilemez.")
        changes = item.get("changes") or {}
        unknown = set(changes) - set(spec.CHANGEABLE)
        if unknown:
            raise BusinessRuleError("Bu alanlar repete isteğinde değiştirilemez: " + ", ".join(sorted(unknown)))
        changed = diff_values(base_values(src), changes)
        if changed and not (item.get("reason") or "").strip():
            raise BusinessRuleError(f"{src.code}: değişiklik gerekçesi zorunludur.", field="reason")
        revised = revised or bool(changed)
        prepared.append((src, item, changed))
    sources = {src.order_id for src, _, _ in prepared}
    order = services.create_order(
        actor, customer=customer, order_date=order_date or timezone.localdate(), due_date=due_date,
        customer_ref=customer_ref, notes=notes, purpose=purpose, charge=charge,
        creation_method=CreationMethod.REVISED_REPEAT if revised else CreationMethod.REPEAT,
        copied_from=prepared[0][0].order if len(sources) == 1 else None)
    for src, item, changed in prepared:
        line = services.add_line(actor, order, product=src.product, quantity=item["quantity"], unit=src.unit,
                                 due_date=item.get("due_date"), source_line=src)
        if changed:
            _create_request(actor, line, src.approved_revision or src.product.current_revision, changed,
                            item["reason"].strip())
    return order


def _create_request(actor, line, base, changed, reason):
    cr = LineChangeRequest.objects.create(
        line=line, base_revision=base, requested={k: _json(v) for k, (_o, v) in changed.items()}, reason=reason,
        requested_by=actor)
    audit(actor, "order.change_request", line, {k: (_json(o), _json(v)) for k, (o, v) in changed.items()},
          reason=reason, code=line.code)
    return cr


def requested_changes(line_or_product, changes):
    """Satışın girdiği değerlerden yalnız izinli ve mevcut revizyondan gerçekten farklı olanlar. Reçetesi olmayan
    üründe dolu her değer müşteri talebidir."""
    unknown = set(changes) - set(spec.CHANGEABLE)
    if unknown:
        raise BusinessRuleError("Bu alanlar teknik talepte değiştirilemez (karışım ve iç notlar planlamadadır): "
                                + ", ".join(sorted(unknown)))
    product = getattr(line_or_product, "product", line_or_product)
    rev = getattr(line_or_product, "approved_revision", None) or product.current_revision
    base = {n: getattr(rev, n) for n in spec.FIELD_NAMES} if rev else {}
    return rev, diff_values(base, changes)


@transaction.atomic
def request_line_change(actor, line, changes, reason):
    """Normal sipariş (taslak veya planlama bekleyen) kaleminde satışın teknik talebi. Ürün/reçete değişmez; istek
    planlamaya gider (yeni revizyon / yeni ürün kodu / ret). Karar verilene kadar teknik onay ve iş emri engellidir."""
    line = OrderLine.objects.select_for_update(of=("self",)).select_related("order", "product").get(pk=line.pk)
    check(actor, "order.create", line.order)
    if line.status != LineStatus.WAITING_PLANNING or line.order.status in (OrderStatus.CANCELLED, OrderStatus.DONE):
        raise BusinessRuleError("Teknik talep yalnız planlama onayı bekleyen (taslak veya onaylı siparişteki) kalemde "
                                "açılabilir. Onaylanmış veya üretimi başlamış kalemde planlamayla görüşün.")
    if line.work_orders.exists():
        raise BusinessRuleError("Bu kalemin iş emri var; teknik talep bu yolla açılamaz.")
    rev, changed = requested_changes(line, changes)
    if pending_request(line):
        raise BusinessRuleError("Bu kalem için açık bir teknik değişiklik isteği var; planlama kararını bekleyin.")
    reason = (reason or "").strip()
    if not changed:
        raise BusinessRuleError("İstenen değerler mevcut özelliklerle aynı; farklı bir değer girin.")
    if not reason:
        raise BusinessRuleError("Teknik talebin gerekçesini yazın (ör. müşteri 650 mm istedi).", field="change_reason")
    return _create_request(actor, line, rev, changed, reason)


def pending_request(line):
    return line.change_requests.filter(status__in=OPEN_CHANGE_STATES).first()


def comparison(cr):
    """Planlama için yan yana: [(etiket, birim, eski gösterim, yeni gösterim)]"""
    return change_rows(cr.base_revision, cr.requested)


def change_rows(base_revision, requested):
    """Mevcut revizyon (yoksa 'Belirtilmemiş') ile istenen değerler yan yana."""
    base = {n: getattr(base_revision, n) for n in spec.FIELD_NAMES} if base_revision else {}
    rows = []
    for name, new in requested.items():
        f = spec.BY_NAME[name]
        rows.append({"label": f.label, "old": spec.display(f, base.get(name)), "new": spec.display(f, new)})
    return rows


def revision_cards(revisions, expected):
    """Teknik onay ekranı: her seçilebilir revizyon için beklenen revizyona göre fark satırları (planlama içindir)."""
    def values(rev):
        return {n: getattr(rev, n) for n in spec.FIELD_NAMES}
    base = values(expected) if expected else None
    out = []
    for rev in revisions:
        rows = []
        if base is not None and rev.pk != expected.pk:
            cur = values(rev)
            for name in spec.FIELD_NAMES:
                f = spec.BY_NAME[name]
                old, new = spec.display(f, base[name]), spec.display(f, cur[name])
                if old != new:
                    rows.append({"label": f.label, "old": old, "new": new})
        out.append({"rev": rev, "diff": rows, "is_expected": bool(expected and rev.pk == expected.pk)})
    return out


def _decided_request(line):
    return (line.change_requests.exclude(status__in=OPEN_CHANGE_STATES)
            .select_related("result_revision", "base_revision").order_by("-decided_at", "-pk").first())


def expected_revision(line):
    """Teknik onayda beklenen revizyon: karara bağlanmış değişiklik isteğinin sonucu (yeni revizyon / yeni ürün kodu),
    reddedilmiş istekte temel revizyon, aksi hâlde repete anında sabitlenen kaynak revizyonu. Repete değilse None."""
    cr = _decided_request(line)
    if cr is not None:
        if cr.status in (ChangeStatus.NEW_REVISION, ChangeStatus.NEW_PRODUCT) and cr.result_revision_id:
            return cr.result_revision
        if cr.status == ChangeStatus.REJECTED and cr.base_revision_id:
            return cr.base_revision
    return line.expected_revision


def expected_basis(line):
    cr = _decided_request(line)
    if cr is not None and cr.status in (ChangeStatus.NEW_REVISION, ChangeStatus.NEW_PRODUCT) and cr.result_revision_id:
        return "satışın teknik değişiklik isteğinin sonucu"
    if cr is not None and cr.status == ChangeStatus.REJECTED and cr.base_revision_id:
        return "değişiklik isteği reddedildi; temel revizyon"
    return "repete kaynağının onaylı revizyonu"


def _lock(cr):
    cr = LineChangeRequest.objects.select_for_update(of=("self",)).select_related(
        "line__order__customer", "line__product", "base_revision").get(pk=cr.pk)
    if cr.status not in OPEN_CHANGE_STATES:
        raise BusinessRuleError("Bu değişiklik isteği karara bağlanmış.")
    return cr


def _spec_overrides(cr):
    from catalog.recipe_drafts import spec_from_json
    return {k: v for k, v in spec_from_json(cr.requested).items() if k in cr.requested}


@transaction.atomic
def start_new_revision(actor, cr):
    """Yeni revizyon taslağı: temel revizyonun karışımı aynen, istenen teknik değerler uygulanmış."""
    from catalog.models import DraftStatus, RecipeDraft
    from catalog.recipe_drafts import open_draft
    check(actor, "planning.approve")
    check(actor, "recipe.change")
    cr = _lock(cr)
    if cr.status == ChangeStatus.DRAFTING and cr.draft_id:
        return cr.draft
    product = cr.line.product
    if RecipeDraft.objects.filter(product=product, status=DraftStatus.OPEN).exists():
        raise BusinessRuleError(f"{product.code} için açık bir reçete taslağı var; önce onu tamamlayın veya vazgeçin.")
    draft = open_draft(actor, product, based_on=cr.base_revision, spec_overrides=_spec_overrides(cr),
                       reason=f"Revizyonlu repete {cr.line.code}: {cr.reason}")
    cr.draft, cr.status = draft, ChangeStatus.DRAFTING
    cr.save()
    audit(actor, "order.change_request_draft", cr.line, {"taslak": (None, draft.pk)}, code=cr.line.code)
    return draft


@transaction.atomic
def start_new_product(actor, cr, *, code, name):
    """Yeni ürün kodu: aynı müşteriye (müşteriye özel ürün kodu kuralı), temel revizyondan taslak; kalem yeni koda
    geçer (yalnız planlama bekleyen, iş emri olmayan kalemde)."""
    from catalog import services as cat
    from catalog.models import Product
    from catalog.recipe_drafts import open_draft
    check(actor, "planning.approve")
    check(actor, "product.create")
    cr = _lock(cr)
    line = OrderLine.objects.select_for_update(of=("self",)).select_related("order__customer", "product").get(
        pk=cr.line_id)
    if line.status != LineStatus.WAITING_PLANNING or line.work_orders.exists():
        raise BusinessRuleError("Yalnız planlama bekleyen ve iş emri olmayan kalem yeni ürün koduna geçirilebilir.")
    code = (code or "").strip()
    if not code:
        raise BusinessRuleError("Yeni ürün kodu zorunludur.", field="code")
    if Product.objects.filter(code=code).exists():
        raise BusinessRuleError("Bu ürün kodu zaten kullanılıyor.", field="code")
    old = line.product
    new = cat.create_definition(actor, Product, {
        "code": code, "customer": line.order.customer, "name": name or old.name, "unit": old.unit,
        "units_per_box": old.units_per_box, "package_type": old.package_type,
        "nominal_package_kg": old.nominal_package_kg, "notes": f"{old.code} ürününden revizyonlu repete ({line.code})"})
    draft = open_draft(actor, new, based_on=cr.base_revision, spec_overrides=_spec_overrides(cr),
                       reason=f"{old.code} → {new.code}: {cr.reason}")
    line.product = new
    line.save(update_fields=["product", "updated_at"])
    cr.draft, cr.new_product, cr.status = draft, new, ChangeStatus.DRAFTING
    cr.save()
    audit(actor, "order.line_product_change", line, {"product": (old.code, new.code)},
          reason=f"Revizyonlu repete kararı: yeni ürün kodu. {cr.reason}", code=line.code)
    return draft


@transaction.atomic
def reject(actor, cr, reason):
    check(actor, "planning.approve")
    if not reason:
        raise BusinessRuleError("Ret gerekçesi zorunludur.", field="reason")
    cr = _lock(cr)
    if cr.status == ChangeStatus.DRAFTING:
        raise BusinessRuleError("Bu istek için açılmış taslak var; önce taslaktan vazgeçin.")
    cr.status, cr.decided_by, cr.decided_at, cr.decision_note = ChangeStatus.REJECTED, actor, timezone.now(), reason
    cr.save()
    audit(actor, "order.change_request_reject", cr.line, {"istek": (cr.pk, None)}, reason=reason, code=cr.line.code)
    return cr


def on_draft_closed(draft, actor):
    """Reçete taslağı onaylanınca/vazgeçilince bağlı değişiklik isteğini sonuçlandırır."""
    from catalog.models import DraftStatus
    for cr in LineChangeRequest.objects.select_for_update().filter(draft=draft, status=ChangeStatus.DRAFTING):
        if draft.status == DraftStatus.APPROVED:
            cr.status = ChangeStatus.NEW_PRODUCT if cr.new_product_id else ChangeStatus.NEW_REVISION
            cr.result_revision = draft.result_revision
            cr.decided_by, cr.decided_at = actor, timezone.now()
            cr.decision_note = f"Rev.{draft.result_revision.rev_no} oluşturuldu; kalem bu revizyonla onaylanabilir."
        else:
            cr.status, cr.draft = ChangeStatus.PENDING, None
            cr.decision_note = "Taslaktan vazgeçildi; istek yeniden planlama kararı bekliyor."
        cr.save()


def can_decide(user):
    return can(user, "planning.approve")
