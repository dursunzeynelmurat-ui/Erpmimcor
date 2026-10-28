"""Baskı görseli sürümleri ve onayları.

Geçerlilik: bir görsel sürümü, bir revizyon için ancak (1) iç teknik onayı ve (2) gerekiyorsa müşteri onayı
o revizyonun **güncel baskı imzasıyla** kaydedilmişse geçerlidir. Kritik baskı özelliği (klişe, kazan çevresi, tekrar
boyu, boşluk, renkler, yüz, yön, en, ürün boyu) değişen yeni revizyonda eski onaylar geçersizdir.
İş emri serbest bırakılırken o anki geçerli sürüme sabitlenir; sonraki yükleme/onay eski iş emrini değiştirmez.
"""
from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from accounts.permissions import can, check
from core.exceptions import BusinessRuleError
from core.services import audit, num

from . import spec
from .customers import validate_upload
from .models import ArtworkApproval, ArtworkApprovalKind, PrintArtwork, Product

SIGNATURE_EXTRA = ("width_mm", "product_length_mm")


def print_signature(revision):
    out = []
    for name in list(SIGNATURE_EXTRA) + spec.PRINT_CRITICAL:
        v = getattr(revision, name)
        out.append(None if v in (None, "") else (num(v) if hasattr(v, "normalize") else " ".join(str(v).split())))
    return out


def requires_customer_approval():
    return getattr(settings, "PRINT_REQUIRE_CUSTOMER_APPROVAL", True)


def approval_state(artwork, revision):
    """{'technical': onay|None, 'customer': onay|None, 'valid': bool, 'stale': [geçersizleşmiş onaylar]}"""
    sig = print_signature(revision) if revision else None
    active = list(artwork.approvals.filter(revoked_at__isnull=True).select_related("approved_by", "revision"))
    current = {k: next((a for a in active if a.kind == k and a.print_signature == sig), None)
               for k in ArtworkApprovalKind.values}
    stale = [a for a in active if a.print_signature != sig]
    valid = bool(artwork.is_active and current["ic_teknik"] and (current["musteri"] or not requires_customer_approval()))
    return {"technical": current["ic_teknik"], "customer": current["musteri"], "valid": valid, "stale": stale}


def valid_artwork(product, revision):
    """Revizyon için geçerli en yeni sürüm (yoksa None)."""
    for art in product.artworks.filter(is_active=True).order_by("-version_no"):
        if approval_state(art, revision)["valid"]:
            return art
    return None


@transaction.atomic
def upload_artwork(actor, product, upload, note=""):
    check(actor, "artwork.manage", product)
    product = Product.objects.select_for_update().get(pk=product.pk)
    content_type = validate_upload(upload)
    no = (product.artworks.aggregate(m=Max("version_no"))["m"] or 0) + 1
    art = PrintArtwork(product=product, version_no=no, original_name=upload.name[:200], content_type=content_type,
                       size=upload.size, note=note, uploaded_by=actor)
    art.file.save(upload.name, upload, save=False)
    art.save()
    audit(actor, "artwork.upload", product, {"gorsel": (None, f"s.{no} {art.original_name}")}, reason=note,
          code=product.code)
    return art


@transaction.atomic
def approve(actor, artwork, kind, *, evidence="", customer_contact=""):
    artwork = PrintArtwork.objects.select_for_update().select_related("product").get(pk=artwork.pk)
    if kind == ArtworkApprovalKind.TECHNICAL:
        check(actor, "artwork.approve", artwork)
    elif kind == ArtworkApprovalKind.CUSTOMER:
        check(actor, "artwork.customer_approval", artwork)
        if not evidence:
            raise BusinessRuleError("Müşteri onayının kanıtını veya referansını yazın (ör. e-posta tarihi).",
                                    field="evidence")
    else:
        raise BusinessRuleError("Geçersiz onay türü.")
    if not artwork.is_active:
        raise BusinessRuleError("Kullanımdan kaldırılmış görsel onaylanamaz.")
    revision = artwork.product.current_revision
    if revision is None or revision.print_status != spec.YesNo.YES:
        raise BusinessRuleError("Ürünün güncel revizyonunda 'Baskı: Var' seçili değil; önce teknik özellikleri "
                                "tamamlayın.")
    sig = print_signature(revision)
    if artwork.approvals.filter(kind=kind, revoked_at__isnull=True, print_signature=sig).exists():
        raise BusinessRuleError("Bu görsel bu baskı özellikleriyle zaten onaylı.")
    a = ArtworkApproval.objects.create(artwork=artwork, kind=kind, revision=revision, print_signature=sig,
                                       evidence=evidence, customer_contact=customer_contact, approved_by=actor)
    audit(actor, f"artwork.approve_{kind}", artwork.product,
          {"gorsel": (None, f"s.{artwork.version_no} Rev.{revision.rev_no}"), "kanit": (None, evidence or None)},
          code=artwork.product.code)
    return a


@transaction.atomic
def revoke(actor, approval, reason):
    approval = ArtworkApproval.objects.select_for_update().select_related("artwork__product").get(pk=approval.pk)
    perm = "artwork.approve" if approval.kind == ArtworkApprovalKind.TECHNICAL else "artwork.customer_approval"
    check(actor, perm, approval.artwork)
    if not reason:
        raise BusinessRuleError("Gerekçe zorunludur.", field="reason")
    if approval.revoked_at:
        raise BusinessRuleError("Onay zaten geri alınmış.")
    approval.revoked_at, approval.revoked_by, approval.revoke_reason = timezone.now(), actor, reason
    approval.save()
    audit(actor, "artwork.revoke", approval.artwork.product,
          {"onay": (approval.get_kind_display(), None)}, reason=reason, code=approval.artwork.product.code)
    return approval


def can_view_file(user, artwork):
    """Yetkili görüntüleme: artwork.view kapsamı veya bu görselin sabitlendiği açık iş emrinde aktif teknik erişim."""
    if can(user, "artwork.view", artwork):
        return True
    from production import instructions
    for wo in artwork.work_orders.all():
        if any(instructions.can_view_technical(user, op) for op in wo.operations.all()):
            return True
    return False
