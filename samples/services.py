"""Numune servisleri.

Stok kuralları:
- Müşteriden gelen referans numune stok hareketi yaratmaz (şirketin satılabilir stoğu değildir).
- İç stoktan numune yalnız paletten **tam kutu** olarak ve bir kez ayrılır (numune birimi kutu, miktarı ayrılan kutu
  adedine eşit olmalı); ayrılan kutular rezervasyon dışı serbest kutulardan düşer, sevk edilebilir miktarda kalmaz.
- Gönderim, geri dönüş/iade ve imha yalnız olay kaydıdır; ikinci stok hareketi oluşturmaz. Geri dönen numune
  satılabilir stoğa geri alınmaz (bu sürümde desteklenmez).
- Kutudan kısmi numune (ör. 3 rulo, 0,5 kg) stok düşmez: numune kaydı tutulur, palet/bobin miktarı değişmez.
Kalite kararı: yalnız numune kaydıdır; kapsadığı palet/bobinlerin kalite durumunu değiştirmez, blokaj kaldırmaz.
"""
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from accounts.catalog import Scope
from accounts.permissions import check, scopes_for
from accounts.scope import customer_in_scope
from core.exceptions import BusinessRuleError
from core.services import audit, next_code

from .models import (INTERNAL_KINDS, CriterionUnit, CustomerFeedback, EventKind, MeasurementResult, PhysicalStatus,
                     QualityDecision, Sample, SampleAttachment, SampleCriterion, SampleEvent, SampleKind,
                     SampleMeasurement, SampleUnit)


def _check_create_scope(actor, customer, work_order):
    scopes = scopes_for(actor, "sample.manage")
    if not scopes:
        raise PermissionDenied("Numune kaydetme yetkiniz yok.")
    if Scope.ALL in scopes:
        return
    if Scope.CUSTOMER in scopes and customer_in_scope(actor, "sample.manage", customer):
        return
    if Scope.ASSIGNED in scopes and work_order is not None and work_order.assigned_foreman_id == actor.pk:
        return
    raise PermissionDenied("Bu müşteri / iş emri için numune kaydetme yetkiniz yok.")


def _event(actor, sample, kind, note="", counterpart=""):
    SampleEvent.objects.create(sample=sample, kind=kind, note=note, counterpart=counterpart,
                               location=sample.location, user=actor)


def _locked(actor, sample, perm):
    sample = Sample.objects.select_for_update(of=("self",)).get(pk=sample.pk)
    check(actor, perm, sample)
    return sample


@transaction.atomic
def create_sample(actor, *, kind, customer, product=None, revision=None, order_line=None, work_order=None, coil=None,
                  pallet=None, counterpart="", occurred_at=None, quantity, unit, room="", cabinet="", shelf="", box="",
                  retention_until=None, retention_conditions="", notes="", shared_note=""):
    if kind not in SampleKind.values:
        raise BusinessRuleError("Numune türünü seçin.", field="kind")
    if unit not in SampleUnit.values:
        raise BusinessRuleError("Birimi listeden seçin.", field="unit")
    if quantity is None or quantity <= 0:
        raise BusinessRuleError("Miktar sıfırdan büyük olmalı.", field="quantity")
    _check_create_scope(actor, customer, work_order)
    if product and product.customer_id != customer.pk:
        raise BusinessRuleError("Ürün bu müşteriye ait değil.", field="product")
    if kind != SampleKind.CUSTOMER_REFERENCE and product is None:
        raise BusinessRuleError("Firma içi numunede ürün seçilmelidir.", field="product")
    if revision and (product is None or revision.recipe.product_id != product.pk):
        raise BusinessRuleError("Teknik revizyon seçilen ürüne ait değil.", field="revision")
    for obj, label, field in ((order_line, "sipariş kalemi", "order_line"), (work_order, "iş emri", "work_order"),
                              (coil, "bobin", "coil"), (pallet, "palet", "pallet")):
        if obj is None:
            continue
        obj_product = obj.product_id if hasattr(obj, "product_id") else obj.work_order.product_id
        if product is None or obj_product != product.pk:
            raise BusinessRuleError(f"Bağlanan {label} bu ürüne ait değil.", field=field)
    if coil and work_order and coil.work_order_id != work_order.pk:
        raise BusinessRuleError("Bobin bu iş emrine ait değil.", field="coil")
    if revision is None and work_order is not None:
        revision = work_order.recipe_revision
    if revision is None and order_line is not None:
        revision = order_line.approved_revision
    if revision is None and product is not None:
        revision = product.current_revision
    sample = Sample.objects.create(
        code=next_code("NUM"), kind=kind, customer=customer, product=product, revision=revision, order_line=order_line,
        work_order=work_order, coil=coil, pallet=pallet, counterpart=counterpart,
        occurred_at=occurred_at or timezone.now(), quantity=quantity, unit=unit, room=room, cabinet=cabinet, shelf=shelf,
        box=box, retention_until=retention_until, retention_conditions=retention_conditions, notes=notes,
        shared_note=shared_note, created_by=actor)
    _event(actor, sample, EventKind.CREATED, note=notes, counterpart=counterpart)
    audit(actor, "sample.create", sample, {"tur": (None, sample.get_kind_display()),
                                           "miktar": (None, f"{quantity} {unit}")}, code=sample.code)
    return sample


@transaction.atomic
def move(actor, sample, *, room, cabinet, shelf, box, note=""):
    sample = _locked(actor, sample, "sample.manage")
    if sample.physical_status == PhysicalStatus.DESTROYED:
        raise BusinessRuleError("İmha edilmiş numunenin konumu değiştirilemez.")
    old = sample.location
    sample.room, sample.cabinet, sample.shelf, sample.box = room, cabinet, shelf, box
    sample.save()
    _event(actor, sample, EventKind.MOVED, note=note or f"{old} → {sample.location}")
    audit(actor, "sample.move", sample, {"konum": (old, sample.location)}, reason=note, code=sample.code)
    return sample


@transaction.atomic
def allocate_from_pallet(actor, sample, pallet, boxes, reason=""):
    """Tek stok hareketi: paletten serbest tam kutu düşer. Numune birimi kutu ve miktarı aynı olmalı."""
    from warehouse import services as wh
    from warehouse.models import MovementKind
    check(actor, "sample.stock")
    sample = _locked(actor, sample, "sample.manage")
    if sample.kind not in INTERNAL_KINDS:
        raise BusinessRuleError("Müşteriden gelen referans numune firma stoğundan düşülmez.")
    if sample.stock_movement_id:
        raise BusinessRuleError(f"Bu numune için stok zaten ayrıldı ({sample.stock_pallet.code}, "
                                f"{sample.stock_boxes} kutu); ikinci kez düşülmez.")
    if sample.physical_status != PhysicalStatus.STORED:
        raise BusinessRuleError("Stok ayırma yalnız firmada saklanan numune için yapılır.")
    if boxes is None or int(boxes) != boxes or boxes <= 0:
        raise BusinessRuleError("Kutu adedi pozitif tam sayı olmalı.", field="boxes")
    if sample.unit != SampleUnit.BOX or sample.quantity != Decimal(boxes):
        raise BusinessRuleError(f"Stoktan yalnız tam kutu ayrılır: numune {sample.quantity} {sample.unit}; ayrılan "
                                f"kutu sayısıyla aynı olmalı (birim dönüşümü yapılmaz).", field="boxes")
    (pallet,) = wh._lock(pallet)
    if pallet.product_id != sample.product_id:
        raise BusinessRuleError("Palet bu numunenin ürününe ait değil.", field="pallet")
    wh._require_in_stock(pallet)
    wh._require_available(pallet, boxes)
    mv = wh._move(actor, pallet, MovementKind.SAMPLE_OUT, -int(boxes), reason=f"{sample.code}: {reason}".strip(": "),
                  work_order=sample.work_order)
    sample.stock_pallet, sample.stock_boxes, sample.stock_movement = pallet, int(boxes), mv
    if sample.pallet_id is None:
        sample.pallet = pallet
    sample.save()
    _event(actor, sample, EventKind.STOCK, note=f"{pallet.code} paletinden {boxes} kutu ayrıldı. {reason}".strip())
    audit(actor, "sample.stock", sample, {"palet": (None, pallet.code), "kutu": (None, boxes)}, reason=reason,
          code=sample.code)
    return sample


@transaction.atomic
def send(actor, sample, *, counterpart, note=""):
    sample = _locked(actor, sample, "sample.manage")
    if sample.physical_status != PhysicalStatus.STORED:
        raise BusinessRuleError("Yalnız firmada saklanan numune gönderilebilir.")
    if not counterpart:
        raise BusinessRuleError("Kime / hangi kargoyla gönderildiğini yazın.", field="counterpart")
    sample.physical_status = PhysicalStatus.SENT
    sample.save()
    _event(actor, sample, EventKind.SENT, note=note, counterpart=counterpart)
    audit(actor, "sample.send", sample, {"fiziksel": (PhysicalStatus.STORED, PhysicalStatus.SENT)}, reason=note,
          code=sample.code)
    return sample


@transaction.atomic
def receive_back(actor, sample, *, counterpart="", note=""):
    sample = _locked(actor, sample, "sample.manage")
    if sample.physical_status != PhysicalStatus.SENT:
        raise BusinessRuleError("Yalnız gönderilmiş numune geri alınabilir.")
    sample.physical_status = PhysicalStatus.RETURNED
    sample.save()
    _event(actor, sample, EventKind.RETURNED, note=note, counterpart=counterpart)
    audit(actor, "sample.return", sample, {"fiziksel": (PhysicalStatus.SENT, PhysicalStatus.RETURNED)}, reason=note,
          code=sample.code)
    return sample


@transaction.atomic
def destroy(actor, sample, reason):
    sample = _locked(actor, sample, "sample.manage")
    if not reason:
        raise BusinessRuleError("İmha gerekçesi zorunludur.", field="reason")
    if sample.physical_status in (PhysicalStatus.DESTROYED, PhysicalStatus.SENT):
        raise BusinessRuleError("İmha edilmiş veya müşteride olan numune imha edilemez.")
    old = sample.physical_status
    sample.physical_status = PhysicalStatus.DESTROYED
    sample.save()
    _event(actor, sample, EventKind.DESTROYED, note=reason)
    audit(actor, "sample.destroy", sample, {"fiziksel": (old, PhysicalStatus.DESTROYED)}, reason=reason,
          code=sample.code)
    return sample


@transaction.atomic
def decide(actor, sample, decision, reason, *, pallets=(), coils=()):
    """İç kalite kararı. Kapsadığı palet/bobinler yalnız belgelenir; kalite durumları değişmez (blokaj kaldırılmaz)."""
    sample = _locked(actor, sample, "sample.view")
    check(actor, "sample.decide")
    if decision not in (QualityDecision.OK, QualityDecision.NOT_OK):
        raise BusinessRuleError("Kararı seçin (uygun / uygun değil).", field="decision")
    if not reason:
        raise BusinessRuleError("Karar gerekçesi zorunludur.", field="reason")
    for p in pallets:
        if p.product_id != sample.product_id:
            raise BusinessRuleError(f"{p.code} bu numunenin ürününe ait değil.", field="pallets")
    for c in coils:
        if c.work_order.product_id != sample.product_id:
            raise BusinessRuleError(f"{c.code} bu numunenin ürününe ait değil.", field="coils")
    old = sample.quality_decision
    sample.quality_decision, sample.decision_reason = decision, reason
    sample.decided_by, sample.decided_at = actor, timezone.now()
    sample.save()
    sample.decision_pallets.set(pallets)
    sample.decision_coils.set(coils)
    scope = ", ".join([p.code for p in pallets] + [c.code for c in coils]) or "yalnız bu numune"
    _event(actor, sample, EventKind.DECISION, note=f"{sample.get_quality_decision_display()} · kapsam: {scope}. {reason}")
    audit(actor, "sample.decide", sample, {"karar": (old, decision), "kapsam": (None, scope)}, reason=reason,
          code=sample.code)
    return sample


@transaction.atomic
def record_feedback(actor, sample, feedback, note="", reference=""):
    sample = _locked(actor, sample, "sample.feedback")
    if feedback not in [v for v in CustomerFeedback.values if v]:
        raise BusinessRuleError("Geri bildirimi seçin.", field="feedback")
    old = sample.customer_feedback
    sample.customer_feedback, sample.feedback_note, sample.feedback_reference = feedback, note, reference
    sample.feedback_by, sample.feedback_at = actor, timezone.now()
    sample.save()
    _event(actor, sample, EventKind.FEEDBACK, note=f"{sample.get_customer_feedback_display()}. {note}".strip())
    audit(actor, "sample.feedback", sample, {"geri_bildirim": (old or None, feedback)}, reason=note, code=sample.code)
    return sample


# --- Kriter ve ölçüm ---------------------------------------------------------------------

def _validate_criterion(name, unit, min_value, max_value):
    if not name:
        raise BusinessRuleError("Kriter adı zorunludur.", field="name")
    if unit not in CriterionUnit.values:
        raise BusinessRuleError("Birimi listeden seçin.", field="unit")
    if unit == CriterionUnit.TEXT and (min_value is not None or max_value is not None):
        raise BusinessRuleError("Metin kriterinde sayısal sınır olmaz.", field="min_value")
    if min_value is not None and max_value is not None and min_value > max_value:
        raise BusinessRuleError("Alt sınır üst sınırdan büyük olamaz.", field="max_value")


@transaction.atomic
def create_criterion(actor, *, name, unit, min_value=None, max_value=None, description="", replaces=None):
    check(actor, "sample.criteria")
    _validate_criterion(name, unit, min_value, max_value)
    if replaces is not None:
        replaces = SampleCriterion.objects.select_for_update().get(pk=replaces.pk)
        if not replaces.is_active:
            raise BusinessRuleError("Bu kriter zaten değiştirilmiş.")
        replaces.is_active = False
        replaces.save(update_fields=["is_active"])
    c = SampleCriterion.objects.create(name=name, unit=unit, min_value=min_value, max_value=max_value,
                                       description=description, replaces=replaces, created_by=actor)
    audit(actor, "sample.criterion", c, {"kriter": (replaces and str(replaces), str(c))})
    return c


def evaluate(criterion, value):
    if criterion.unit == CriterionUnit.TEXT or (criterion.min_value is None and criterion.max_value is None):
        return MeasurementResult.NO_RANGE
    if (criterion.min_value is not None and value < criterion.min_value) or \
            (criterion.max_value is not None and value > criterion.max_value):
        return MeasurementResult.OUT
    return MeasurementResult.IN


@transaction.atomic
def add_measurement(actor, sample, criterion, *, value=None, text_value="", note=""):
    sample = _locked(actor, sample, "sample.view")
    check(actor, "sample.decide")
    criterion = SampleCriterion.objects.get(pk=criterion.pk)  # ekran açıldıktan sonra değişmiş olabilir
    if not criterion.is_active:
        raise BusinessRuleError("Pasif kriterle yeni ölçüm girilemez; güncel kriteri seçin.", field="criterion")
    if criterion.unit == CriterionUnit.TEXT:
        if not text_value:
            raise BusinessRuleError("Gözlemi yazın.", field="text_value")
        value = None
    elif value is None:
        raise BusinessRuleError(f"Ölçülen değeri {criterion.get_unit_display()} olarak yazın.", field="value")
    m = SampleMeasurement.objects.create(
        sample=sample, criterion=criterion, criterion_name=criterion.name, unit=criterion.unit,
        min_value=criterion.min_value, max_value=criterion.max_value, value=value, text_value=text_value,
        result=MeasurementResult.NO_RANGE if value is None else evaluate(criterion, value), note=note,
        measured_by=actor)
    audit(actor, "sample.measure", sample, {criterion.name: (None, str(value) if value is not None else text_value)},
          code=sample.code)
    return m


@transaction.atomic
def add_attachment(actor, sample, upload, title):
    from catalog.customers import validate_upload
    sample = _locked(actor, sample, "sample.manage")
    if not title:
        raise BusinessRuleError("Başlık zorunludur.", field="title")
    content_type = validate_upload(upload)
    att = SampleAttachment(sample=sample, title=title, original_name=upload.name[:200], content_type=content_type,
                           size=upload.size, uploaded_by=actor)
    att.file.save(upload.name, upload, save=False)
    att.save()
    audit(actor, "sample.attachment", sample, {"ek": (None, title)}, code=sample.code)
    return att


def internal_view(user, sample=None):
    """Satış dışı (iç) görünüm: ölçümler, iç karar, konum, ekler. Satış yalnız paylaşılabilir bilgiyi görür."""
    from accounts.permissions import can
    return can(user, "sample.manage", sample) or can(user, "sample.decide")
