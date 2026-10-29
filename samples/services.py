"""Numune servisleri.

Stok kuralları:
- Müşteriden gelen referans numune stok hareketi yaratmaz (şirketin satılabilir stoğu değildir).
- İç stoktan numune yalnız paletten **tam kutu** olarak ve bir kez ayrılır (numune birimi kutu, miktarı ayrılan kutu
  adedine eşit olmalı); ayrılan kutular rezervasyon dışı serbest kutulardan düşer, sevk edilebilir miktarda kalmaz.
- Gönderim, geri dönüş/iade ve imha yalnız olay kaydıdır; ikinci stok hareketi oluşturmaz. Geri dönen numune
  satılabilir stoğa geri alınmaz (bu sürümde desteklenmez).
- Kutudan kısmi numune (ör. 3 rulo, 0,5 kg) stok düşmez: numune kaydı tutulur, palet/bobin miktarı değişmez.
- Firma içi numunenin stok kaynağı açıktır (StockSource): "stoktan" ise gönderim ve imhadan önce stoktan ayırma
  yapılmış olmalı; "önceden çıktı" ise o çıkış hareketi numuneye bağlanmalı (bir hareket tek numuneye); "stok dışı"
  stok etkisi kaydedilmeyen numunedir (adet/rulo/kg için tek seçenek). Belirtilmemiş eski kayıtta gönderim/imha
  öncesi kaynak belirtilir. Kalite blokeli palet/bobinden gelen numune müşteriye gönderilmez.
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
from core.models import QualityStatus
from core.services import audit, next_code

from .models import (INTERNAL_KINDS, CriterionUnit, CustomerFeedback, EventKind, MeasurementResult, PhysicalStatus,
                     QualityDecision, Sample, SampleAttachment, SampleCriterion, SampleEvent, SampleKind,
                     SampleMeasurement, SampleUnit, RevisionBasis, StockSource)

# Numuneye bağlanabilecek önceki çıkış hareketleri (kutular paletten fiziksel olarak ayrılmış)
PRIOR_OUT_KINDS = ("hasar_ayirma", "sayim_duzeltme", "duzeltme", "numune_ayirma")


def _resolve_stock_source(kind, unit, stock_source):
    if kind not in INTERNAL_KINDS:
        return StockSource.UNKNOWN  # müşteri referansı: firma stoğu değildir
    if stock_source in (None, ""):
        # Güvenli varsayılan: tam kutu iç numune stoktan gelir (ayırma yapılmadan gönderilemez); kısmi birimde stok
        # işlemi desteklenmez ve açıkça "stok dışı" olarak kaydedilir.
        return StockSource.FROM_STOCK if unit == SampleUnit.BOX else StockSource.NOT_FROM_STOCK
    if stock_source not in StockSource.values:
        raise BusinessRuleError("Stok kaynağını listeden seçin.", field="stock_source")
    if stock_source in (StockSource.FROM_STOCK, StockSource.ALREADY_OUT) and unit != SampleUnit.BOX:
        raise BusinessRuleError("Stoktan yalnız tam kutu numune düşülür; adet/rulo/kg/metre numunede birim dönüşümü "
                                "yapılmaz. 'Stok dışı' seçin.", field="stock_source")
    return stock_source


def stock_effect(sample):
    """Kullanıcıya gösterilecek stok etkisi metni (desteklenmeyen durum tamamlanmış gibi gösterilmez)."""
    if sample.kind not in INTERNAL_KINDS:
        return "Yok — müşterinin numunesi firma stoğu değildir."
    if sample.stock_movement_id:
        mv = sample.stock_movement
        if sample.stock_source == StockSource.ALREADY_OUT:
            return (f"{mv.pallet.code} paletindeki önceki çıkış hareketine bağlı ({mv.get_kind_display()}, "
                    f"{-mv.quantity} kutu); ikinci kez düşülmedi.")
        return f"{sample.stock_pallet.code} paletinden {sample.stock_boxes} kutu ayrıldı (tek hareket)."
    if sample.stock_source == StockSource.FROM_STOCK:
        return "Stoktan ayrılacak — henüz ayrılmadı. Gönderim ve imhadan önce 'Stoktan ayır' yapılmalı."
    if sample.stock_source == StockSource.ALREADY_OUT:
        return "Önceki stok çıkışı henüz bağlanmadı. Gönderim ve imhadan önce çıkış hareketini bağlayın."
    if sample.stock_source == StockSource.NOT_FROM_STOCK:
        if sample.unit != SampleUnit.BOX:
            return (f"Stok etkisi kaydedilmedi: {sample.get_unit_display()} biriminde stok düşümü desteklenmiyor "
                    "(birim dönüşümü yapılmaz).")
        return "Stok dışı numune; stok etkisi kaydedilmez."
    return "Stok kaynağı belirtilmemiş (eski kayıt). Gönderim ve imhadan önce stok kaynağını belirtin."


def stock_pending(sample):
    """Gönderim/imha öncesi tamamlanması gereken stok işlemi var mı (ekranda uyarı için)."""
    if sample.kind not in INTERNAL_KINDS or sample.physical_status != PhysicalStatus.STORED:
        return False
    return sample.stock_source == StockSource.UNKNOWN or (
        sample.stock_source in (StockSource.FROM_STOCK, StockSource.ALREADY_OUT) and not sample.stock_movement_id)


def _require_stock_complete(sample, action):
    if sample.kind not in INTERNAL_KINDS:
        return
    if sample.stock_source == StockSource.UNKNOWN:
        raise BusinessRuleError(f"Stok kaynağını belirtmeden numune {action}: firma stoğundan mı geldiğini seçin "
                                "('Stok kaynağını belirt').")
    if sample.stock_source in (StockSource.FROM_STOCK, StockSource.ALREADY_OUT) and not sample.stock_movement_id:
        step = "'Stoktan ayır'" if sample.stock_source == StockSource.FROM_STOCK else "'Önceki stok çıkışını bağla'"
        raise BusinessRuleError(f"Numune firma stoğundan geliyor ama stok hareketi tamamlanmadı; önce {step} "
                                f"(Stoktan ayır bölümü) yapılmalı. Numune {action}.")


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


def pallet_sources(pallet):
    """Paletin (aktarım/hasar ayırmayla kutu aldığı kaynak paletler dahil) fiziksel kökeni:
    (olası üretim revizyonları, iş emirleri, bobinler ve kaynak bobin zinciri)."""
    from production.models import Coil, CoilLink, WorkOrder
    from warehouse import services as wh
    from warehouse.models import MovementKind, Pallet, PalletCoil, PalletMovement
    pallets = wh._pallet_ancestors({pallet.pk})
    coils = set(PalletCoil.objects.filter(pallet_id__in=pallets).values_list("coil_id", flat=True))
    wos = set(Pallet.objects.filter(pk__in=pallets, work_order__isnull=False).values_list("work_order_id", flat=True))
    wos |= set(Coil.objects.filter(pk__in=coils).values_list("work_order_id", flat=True))
    wos |= set(PalletMovement.objects.filter(pallet_id__in=pallets, kind=MovementKind.PRODUCTION,
                                             work_order__isnull=False).values_list("work_order_id", flat=True))
    chain = wh._expand(coils, lambda ids: CoilLink.objects.filter(output_id__in=ids).values_list("source_id", flat=True))
    revisions = set(WorkOrder.objects.filter(pk__in=wos).values_list("recipe_revision_id", flat=True))
    return revisions, wos, chain


def _rev_names(ids):
    from catalog.models import RecipeRevision
    return ", ".join(f"Rev.{n}" for n in RecipeRevision.objects.filter(pk__in=ids).order_by("rev_no")
                     .values_list("rev_no", flat=True)) or "bilinmiyor"


def _resolve_revision(product, revision, uncertain, *, order_line, work_order, coil, pallet):
    """Numunenin teknik revizyonu ve dayanağı. Öncelik: fiziksel kaynak (bobin/palet) → iş emri → sipariş kalemi →
    kullanıcı seçimi; ürünün güncel revizyonu yalnız hiçbir fiziksel/üretim bağlantısı yoksa ve dayanağı yazılarak.
    Çelişen bağlantı ve belirsiz paletteki tahmin reddedilir."""
    from catalog.models import RecipeRevision
    if work_order is not None and order_line is not None and work_order.order_line_id and \
            work_order.order_line_id != order_line.pk:
        raise BusinessRuleError(f"{work_order.code} iş emri bu sipariş kalemine ait değil; bağlantılar çelişiyor.",
                                field="order_line")
    candidates = None
    if coil is not None:
        candidates = {coil.work_order.recipe_revision_id}
    if pallet is not None:
        revs, wos, coils = pallet_sources(pallet)
        if coil is not None and coil.pk not in coils:
            raise BusinessRuleError(f"{coil.code} bobini bu palette değil ({pallet.code}); bağlantılar çelişiyor.",
                                    field="coil")
        if work_order is not None and work_order.pk not in wos:
            raise BusinessRuleError(f"{pallet.code} paleti {work_order.code} iş emrinden gelmiyor; bağlantılar "
                                    "çelişiyor.", field="work_order")
        candidates = revs if candidates is None else candidates
    if candidates is not None:
        if len(candidates) == 1:
            known = RecipeRevision.objects.get(pk=next(iter(candidates)))
            if revision is not None and revision.pk != known.pk:
                raise BusinessRuleError(f"Fiziksel kaynak Rev.{known.rev_no} ile üretildi; seçilen Rev.{revision.rev_no} "
                                        "ile çelişiyor.", field="revision")
            return known, RevisionBasis.PHYSICAL
        names = _rev_names(candidates)
        if revision is not None:
            if candidates and revision.pk not in candidates:
                raise BusinessRuleError(f"Seçilen Rev.{revision.rev_no} bu paletin olası revizyonları arasında değil "
                                        f"({names}).", field="revision")
            return revision, RevisionBasis.USER
        if uncertain:
            return None, RevisionBasis.UNCERTAIN
        what = (f"{pallet.code} paleti birden fazla revizyondan oluşuyor ({names})" if len(candidates) > 1
                else f"{pallet.code} paletinin üretim revizyonu kayıtlı değil")
        raise BusinessRuleError(f"{what}; tahmin yapılmaz. Numunenin revizyonunu seçin veya 'Revizyon kesin "
                                "bilinmiyor' işaretleyin.", field="revision")
    if work_order is not None:
        if revision is not None and revision.pk != work_order.recipe_revision_id:
            raise BusinessRuleError(f"{work_order.code} Rev.{work_order.recipe_revision.rev_no} ile üretiliyor; seçilen "
                                    f"Rev.{revision.rev_no} ile çelişiyor.", field="revision")
        return work_order.recipe_revision, RevisionBasis.WORK_ORDER
    if order_line is not None and order_line.approved_revision_id:
        if revision is not None and revision.pk != order_line.approved_revision_id:
            raise BusinessRuleError(f"{order_line.code} Rev.{order_line.approved_revision.rev_no} ile onaylı; seçilen "
                                    f"Rev.{revision.rev_no} ile çelişiyor.", field="revision")
        return order_line.approved_revision, RevisionBasis.ORDER_LINE
    if revision is not None:
        return revision, RevisionBasis.USER
    if uncertain:
        return None, RevisionBasis.UNCERTAIN
    if product is not None and product.current_revision is not None:
        return product.current_revision, RevisionBasis.PRODUCT_CURRENT
    return None, RevisionBasis.UNKNOWN


@transaction.atomic
def create_sample(actor, *, kind, customer, product=None, revision=None, order_line=None, work_order=None, coil=None,
                  pallet=None, counterpart="", occurred_at=None, quantity, unit, room="", cabinet="", shelf="", box="",
                  retention_until=None, retention_conditions="", notes="", shared_note="", stock_source=None,
                  revision_uncertain=False):
    if kind not in SampleKind.values:
        raise BusinessRuleError("Numune türünü seçin.", field="kind")
    if unit not in SampleUnit.values:
        raise BusinessRuleError("Birimi listeden seçin.", field="unit")
    if quantity is None or quantity <= 0:
        raise BusinessRuleError("Miktar sıfırdan büyük olmalı.", field="quantity")
    stock_source = _resolve_stock_source(kind, unit, stock_source)
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
    revision, basis = _resolve_revision(product, revision, revision_uncertain, order_line=order_line,
                                        work_order=work_order, coil=coil, pallet=pallet)
    sample = Sample.objects.create(
        code=next_code("NUM"), kind=kind, customer=customer, product=product, revision=revision, revision_basis=basis,
        order_line=order_line,
        work_order=work_order, coil=coil, pallet=pallet, counterpart=counterpart, stock_source=stock_source,
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
    if sample.stock_source == StockSource.NOT_FROM_STOCK:
        raise BusinessRuleError("Bu numune stok dışı olarak kaydedildi; stoktan ayrılamaz. Firma stoğundan geldiyse "
                                "önce stok kaynağını 'Firma stoğundan' olarak düzeltin.")
    if sample.stock_source == StockSource.ALREADY_OUT:
        raise BusinessRuleError("Bu numunenin kutuları daha önce başka işlemle stoktan çıktı; tekrar düşülmez. "
                                "Önceki çıkış hareketini bağlayın.")
    (pallet,) = wh._lock(pallet)
    if pallet.product_id != sample.product_id:
        raise BusinessRuleError("Palet bu numunenin ürününe ait değil.", field="pallet")
    if sample.pallet_id != pallet.pk:
        # Numune kaydındaki bağlantılardan farklı bir paletten ayırma: fiziksel köken tekrar doğrulanır.
        revs, wos, coils = pallet_sources(pallet)
        if sample.coil_id and sample.coil_id not in coils:
            raise BusinessRuleError(f"Numunenin bobini {sample.coil.code} bu palette değil; bağlantılar çelişiyor.",
                                    field="pallet_code")
        if sample.work_order_id and sample.work_order_id not in wos:
            raise BusinessRuleError(f"{pallet.code} paleti numunenin iş emrinden ({sample.work_order.code}) gelmiyor.",
                                    field="pallet_code")
        if sample.revision_id and revs != {sample.revision_id}:
            raise BusinessRuleError(f"{pallet.code} paletinin üretim revizyonu ({_rev_names(revs)}) numunenin "
                                    f"Rev.{sample.revision.rev_no} revizyonuyla uyuşmuyor; bu paletten ayrılamaz.",
                                    field="pallet_code")
    wh._require_in_stock(pallet)
    wh._require_available(pallet, boxes)
    mv = wh._move(actor, pallet, MovementKind.SAMPLE_OUT, -int(boxes), reason=f"{sample.code}: {reason}".strip(": "),
                  work_order=sample.work_order)
    sample.stock_pallet, sample.stock_boxes, sample.stock_movement = pallet, int(boxes), mv
    sample.stock_source = StockSource.FROM_STOCK
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
    _require_stock_complete(sample, "gönderilemez")
    _require_not_blocked(sample)
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
    _require_stock_complete(sample, "imha edilemez")
    old = sample.physical_status
    sample.physical_status = PhysicalStatus.DESTROYED
    sample.save()
    _event(actor, sample, EventKind.DESTROYED, note=reason)
    audit(actor, "sample.destroy", sample, {"fiziksel": (old, PhysicalStatus.DESTROYED)}, reason=reason,
          code=sample.code)
    return sample


def _require_not_blocked(sample):
    """Müşteriye gönderim, sevk kuralıyla aynı: kalite blokeli palet veya ilişkili blokeli bobin varsa gönderilmez."""
    from warehouse import services as wh
    for pallet in {p for p in (sample.stock_pallet, sample.pallet) if p is not None}:
        if pallet.quality_status == QualityStatus.BLOCKED:
            raise BusinessRuleError(f"{pallet.code} kalite blokeli; bu paletten gelen numune müşteriye gönderilemez.")
        codes = wh.coil_holds(pallet)
        if codes:
            raise BusinessRuleError(f"{pallet.code}: ilişkili bobin blokeli ({', '.join(codes)}); numune müşteriye "
                                    "gönderilemez.")
    if sample.coil is not None and sample.coil.quality_status == QualityStatus.BLOCKED:
        raise BusinessRuleError(f"{sample.coil.code} blokeli; numune müşteriye gönderilemez.")


@transaction.atomic
def set_stock_source(actor, sample, stock_source, reason):
    """Stok kaynağını belirtir/düzeltir: yalnız stok hareketi bağlanmamış, firmada saklanan iç numunede."""
    sample = _locked(actor, sample, "sample.manage")
    if sample.kind not in INTERNAL_KINDS:
        raise BusinessRuleError("Müşteriden gelen referans numunenin stok kaynağı olmaz.")
    if sample.stock_movement_id:
        raise BusinessRuleError("Stok hareketi bağlı numunenin stok kaynağı değiştirilemez.")
    if sample.physical_status != PhysicalStatus.STORED:
        raise BusinessRuleError("Stok kaynağı yalnız firmada saklanan numunede belirtilir.")
    if not (reason or "").strip():
        raise BusinessRuleError("Gerekçe zorunludur.", field="reason")
    if stock_source in (None, ""):
        raise BusinessRuleError("Stok kaynağını seçin.", field="stock_source")
    new = _resolve_stock_source(sample.kind, sample.unit, stock_source)
    old = sample.stock_source
    sample.stock_source = new
    sample.save()
    audit(actor, "sample.stock_source", sample, {"stok_kaynagi": (old, new)}, reason=reason, code=sample.code)
    return sample


def prior_movement_candidates(sample):
    """Numuneye bağlanabilecek önceki çıkış hareketleri: aynı ürün, çıkış yönlü, kutu sayısı numuneyle aynı, başka
    numuneye bağlı değil. Bağlı palet varsa yalnız o palet."""
    from warehouse.models import PalletMovement
    if sample.unit != SampleUnit.BOX or sample.quantity != sample.quantity.to_integral_value():
        return PalletMovement.objects.none()
    qs = PalletMovement.objects.filter(pallet__product_id=sample.product_id, kind__in=PRIOR_OUT_KINDS,
                                       quantity=-int(sample.quantity), sample__isnull=True)
    if sample.pallet_id:
        qs = qs.filter(pallet_id=sample.pallet_id)
    return qs.select_related("pallet").order_by("-pk")


@transaction.atomic
def link_prior_movement(actor, sample, movement):
    """Önceden stoktan çıkmış kutuları numuneye bağlar; yeni stok hareketi oluşmaz (ikinci düşüm yok)."""
    from warehouse.models import PalletMovement
    check(actor, "sample.stock")
    sample = _locked(actor, sample, "sample.manage")
    if sample.kind not in INTERNAL_KINDS:
        raise BusinessRuleError("Müşteriden gelen referans numune firma stoğuna bağlanmaz.")
    if sample.stock_source != StockSource.ALREADY_OUT:
        raise BusinessRuleError("Önceki çıkış yalnız stok kaynağı 'Daha önce stoktan çıkarıldı' olan numuneye bağlanır.")
    if sample.stock_movement_id:
        raise BusinessRuleError("Bu numuneye stok hareketi zaten bağlı.")
    movement = PalletMovement.objects.select_for_update(of=("self",)).select_related("pallet").get(pk=movement.pk)
    if Sample.objects.filter(stock_movement=movement).exists():
        raise BusinessRuleError("Bu stok hareketi başka numuneye bağlı; bir çıkış tek numuneye bağlanır.")
    if movement.kind not in PRIOR_OUT_KINDS or movement.quantity >= 0:
        raise BusinessRuleError("Yalnız paletten çıkış yönlü hareket (hasar ayırma, sayım/düzeltme çıkışı) bağlanır; "
                                f"seçilen: {movement.get_kind_display()} ({movement.quantity:+d} kutu).", field="movement")
    if movement.pallet.product_id != sample.product_id:
        raise BusinessRuleError("Hareketin paleti bu numunenin ürününe ait değil.", field="movement")
    if sample.pallet_id and movement.pallet_id != sample.pallet_id:
        raise BusinessRuleError(f"Numune {sample.pallet.code} paletine bağlı; hareket {movement.pallet.code} paletinin.",
                                field="movement")
    if sample.unit != SampleUnit.BOX or Decimal(-movement.quantity) != sample.quantity:
        raise BusinessRuleError(f"Hareket {-movement.quantity} kutu; numune {sample.quantity} {sample.unit}. Kutu sayısı "
                                "aynı olmalı (birim dönüşümü yapılmaz).", field="movement")
    sample.stock_movement, sample.stock_pallet, sample.stock_boxes = movement, movement.pallet, -movement.quantity
    if sample.pallet_id is None:
        sample.pallet = movement.pallet
    sample.save()
    _event(actor, sample, EventKind.STOCK, note=f"{movement.pallet.code}: önceki çıkış hareketi bağlandı "
                                                f"({movement.get_kind_display()}, {-movement.quantity} kutu); "
                                                "yeni stok düşümü yapılmadı.")
    audit(actor, "sample.stock_link", sample, {"hareket": (None, movement.pk), "palet": (None, movement.pallet.code)},
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
