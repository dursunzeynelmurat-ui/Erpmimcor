"""Müşteri kartı iş kuralları: kısa açılış, aşamaya göre zorunluluk, mükerrer kontrolü, kişi/adres/belge ve
satışçı erişimleri. Finans hareketi olmadığı için bakiye hesaplanmaz/gösterilmez."""
import difflib
import re
import unicodedata

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.permissions import can, check
from core.exceptions import BusinessRuleError
from core.services import audit, diff

from .models import (Customer, CustomerAccess, CustomerAddress, CustomerContact, CustomerDocument, TaxIdType)

STAGES = {"taslak": "Taslak", "siparis": "Sipariş onayı", "sevkiyat": "Sevkiyat"}
FIRM_SUFFIXES = r"\b(a\.?s\.?|ltd\.?|sti\.?|san\.?|tic\.?|ve|limited|sirketi|anonim|gmbh|bv|llc|inc)\b"


# --- Aşama zorunlulukları -------------------------------------------------------------

def tax_id_problem(country, entity_type, id_type, tax_id, tax_office):
    """Kimlik bilgisindeki sorun (yoksa None). Türkiye formatı yalnız TR müşterilere uygulanır."""
    if not id_type or not tax_id:
        return "Vergi/kayıt kimlik türü ve numarası girilmeli."
    if country == "TR":
        if id_type == TaxIdType.VKN:
            if not re.fullmatch(r"\d{10}", tax_id):
                return "VKN 10 haneli olmalı."
            if not tax_office:
                return "VKN için vergi dairesi girilmeli."
        elif id_type == TaxIdType.TCKN:
            if not re.fullmatch(r"[1-9]\d{10}", tax_id):
                return "TC kimlik no 11 haneli olmalı ve 0 ile başlamamalı."
        else:
            return "Türkiye müşterisi için VKN veya TC kimlik no kullanılmalı."
    elif id_type in (TaxIdType.VKN, TaxIdType.TCKN):
        return "VKN/TCKN yalnız Türkiye müşterileri için kullanılır."
    return None


def readiness(customer, stage):
    """Aşama için eksik bilgilerin listesi (boş liste = hazır)."""
    customer = Customer.objects.get(pk=customer.pk)
    missing = []
    if not customer.name or not customer.country:
        missing.append("Resmî ünvan ve ülke")
    if stage in ("siparis", "sevkiyat"):
        problem = tax_id_problem(customer.country, customer.entity_type, customer.tax_id_type, customer.tax_id,
                                 customer.tax_office)
        if problem:
            missing.append(problem)
        if not customer.addresses.filter(is_active=True, default_billing=True).exists():
            missing.append("Varsayılan fatura adresi")
        if not (customer.currency and customer.payment_method and customer.payment_term_days is not None):
            missing.append("Ticari varsayılanlar (para birimi, ödeme şekli, vade)")
        if not customer.contacts.filter(is_active=True).exists():
            missing.append("En az bir ilgili kişi")
    if stage == "sevkiyat":
        if not customer.addresses.filter(is_active=True, default_shipping=True).exists():
            missing.append("Varsayılan teslimat adresi")
        if not customer.delivery_terms:
            missing.append("Teslim şekli")
    return missing


def require_ready(customer, stage):
    missing = readiness(customer, stage)
    if missing:
        raise BusinessRuleError(f"{customer.code}: {STAGES[stage]} için müşteri kartı eksik: " + "; ".join(missing))


# --- Mükerrer kontrolü ---------------------------------------------------------------------

def normalize_name(name):
    text = unicodedata.normalize("NFKD", name.lower().replace("ı", "i"))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(FIRM_SUFFIXES, " ", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def similar_customers(name, exclude_pk=None, threshold=0.82):
    """İsim benzerliği yalnız uyarıdır; farklı firma kaydını engellemez."""
    target = normalize_name(name)
    if not target:
        return []
    result = []
    for c in Customer.objects.exclude(pk=exclude_pk).only("pk", "code", "name", "short_name"):
        other = normalize_name(c.name)
        ratio = difflib.SequenceMatcher(None, target, other).ratio()
        if ratio >= threshold or (len(target) > 4 and (target in other or other in target)):
            result.append(c)
    return result


def _check_unique_ids(code, country, id_type, tax_id, exclude_pk=None):
    qs = Customer.objects.exclude(pk=exclude_pk)
    if qs.filter(code=code).exists():
        raise BusinessRuleError("Bu müşteri kodu kullanılıyor.", field="code")
    if tax_id and qs.filter(country=country, tax_id_type=id_type, tax_id=tax_id).exists():
        other = qs.get(country=country, tax_id_type=id_type, tax_id=tax_id)
        raise BusinessRuleError(f"Bu vergi/kayıt numarası {other.code} müşterisinde kayıtlı; aynı müşteri tekrar "
                                "açılamaz.", field="tax_id")


# --- Müşteri -----------------------------------------------------------------------------

@transaction.atomic
def create_customer(actor, data, confirm_similar=False):
    """Kısa açılış: kod, ünvan, tür, ülke (+ isteğe bağlı kimlik). Açan kullanıcıya kalıcı erişim yazılır."""
    check(actor, "customer.create")
    data = dict(data)
    data.setdefault("country", "TR")
    _check_unique_ids(data["code"], data["country"], data.get("tax_id_type", ""), data.get("tax_id", ""))
    if data.get("tax_id"):
        problem = tax_id_problem(data["country"], data.get("entity_type", "firma"), data.get("tax_id_type", ""),
                                 data["tax_id"], data.get("tax_office", "x"))
        if problem and "vergi dairesi" not in problem:
            raise BusinessRuleError(problem, field="tax_id")
    similar = similar_customers(data["name"])
    if similar and not confirm_similar:
        raise BusinessRuleError("Benzer ünvanlı müşteri var: " + ", ".join(str(c) for c in similar)
                                + ". Farklı firma ise onay kutusunu işaretleyip tekrar kaydedin.", field="confirm_similar")
    customer = Customer.objects.create(created_by=actor, **data)
    audit(actor, "customer.create", customer, {k: (None, v) for k, v in data.items()},
          reason="Benzer kayıt uyarısı onaylandı: " + ", ".join(c.code for c in similar) if similar else "")
    CustomerAccess.objects.create(customer=customer, user=actor, valid_from=timezone.localdate(), granted_by=actor,
                                  reason="Müşteriyi açan kullanıcı")
    return customer


@transaction.atomic
def update_customer(actor, customer, data, reason=""):
    """Kart bölümü güncellemesi. Kod değişmez. Kimlik değişikliği mükerrer kontrolünden geçer."""
    check(actor, "customer.change", customer)
    commercial = {"currency", "payment_method", "payment_term_days", "delivery_terms", "delivery_place", "po_required"}
    if commercial & set(data) and not can(actor, "customer.commercial.view", customer):
        raise BusinessRuleError("Ticari bilgileri değiştirme yetkiniz yok.")
    customer = Customer.objects.select_for_update().get(pk=customer.pk)
    if "code" in data and data["code"] != customer.code:
        raise BusinessRuleError("Müşteri kodu sonradan değiştirilemez.", field="code")
    merged = {f: data.get(f, getattr(customer, f)) for f in ("country", "tax_id_type", "tax_id", "entity_type",
                                                             "tax_office")}
    if {"country", "tax_id_type", "tax_id"} & set(data):
        _check_unique_ids(customer.code, merged["country"], merged["tax_id_type"], merged["tax_id"],
                          exclude_pk=customer.pk)
        if merged["tax_id"]:
            problem = tax_id_problem(merged["country"], merged["entity_type"], merged["tax_id_type"],
                                     merged["tax_id"], merged["tax_office"] or "x")
            if problem:
                raise BusinessRuleError(problem, field="tax_id")
    changes = diff(customer, data)
    for f, (_, new) in changes.items():
        setattr(customer, f, new)
    customer.save()
    if changes:
        audit(actor, "customer.update", customer, changes, reason=reason)
    return customer


@transaction.atomic
def set_customer_active(actor, customer, active, reason):
    check(actor, "customer.change", customer)
    if not reason:
        raise BusinessRuleError("Gerekçe zorunludur.", field="reason")
    customer = Customer.objects.select_for_update().get(pk=customer.pk)
    if customer.is_active != active:
        customer.is_active = active
        customer.save(update_fields=["is_active", "updated_at"])
        audit(actor, "customer.activate" if active else "customer.deactivate", customer,
              {"is_active": (not active, active)}, reason=reason)
    return customer


# --- Kişiler ve adresler --------------------------------------------------------------------

def _save_child(actor, customer, model, obj, data, action, flag_fields=()):
    check(actor, "customer.change", customer)
    Customer.objects.select_for_update().get(pk=customer.pk)
    for flag in flag_fields:  # tek varsayılan / tek ana kişi
        if data.get(flag) and data.get("is_active", True):
            model.objects.filter(customer=customer, **{flag: True}).exclude(pk=getattr(obj, "pk", None)).update(**{flag: False})
    if obj is None:
        obj = model.objects.create(customer=customer, **data)
        audit(actor, f"customer.{action}_add", customer, {action: (None, str(obj))})
        return obj
    obj = model.objects.select_for_update().get(pk=obj.pk, customer=customer)
    changes = diff(obj, data)
    for f, (_, new) in changes.items():
        setattr(obj, f, new)
    obj.save()
    if changes:
        audit(actor, f"customer.{action}_update", customer, {f"{action}.{k}": v for k, v in changes.items()})
    return obj


@transaction.atomic
def save_contact(actor, customer, data, contact=None):
    return _save_child(actor, customer, CustomerContact, contact, data, "contact", ("is_primary",))


@transaction.atomic
def save_address(actor, customer, data, address=None):
    if data.get("default_billing") and data.get("purpose") == "teslimat":
        raise BusinessRuleError("Yalnız teslimat amaçlı adres varsayılan fatura adresi olamaz.", field="default_billing")
    if data.get("default_shipping") and data.get("purpose") == "fatura":
        raise BusinessRuleError("Yalnız fatura amaçlı adres varsayılan teslimat adresi olamaz.", field="default_shipping")
    return _save_child(actor, customer, CustomerAddress, address, data, "address",
                       ("default_billing", "default_shipping"))


# --- Belgeler -----------------------------------------------------------------------------

ALLOWED_DOCS = {  # uzantı → (içerik türü, dosya imzası)
    "pdf": ("application/pdf", (b"%PDF-",)),
    "png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    "jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    "jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
}


def validate_upload(upload):
    ext = upload.name.rsplit(".", 1)[-1].lower() if "." in upload.name else ""
    if ext not in ALLOWED_DOCS:
        raise BusinessRuleError("Yalnız PDF, PNG ve JPG dosyaları yüklenebilir.", field="file")
    if upload.size > settings.CUSTOMER_DOCUMENT_MAX_BYTES:
        raise BusinessRuleError(f"Dosya en fazla {settings.CUSTOMER_DOCUMENT_MAX_BYTES // (1024 * 1024)} MB olabilir.",
                                field="file")
    head = upload.read(16)
    upload.seek(0)
    content_type, signatures = ALLOWED_DOCS[ext]
    if not any(head.startswith(sig) for sig in signatures):
        raise BusinessRuleError("Dosya içeriği uzantısıyla uyuşmuyor.", field="file")
    return content_type


def can_view_document(user, document):
    if not can(user, "customer.document.view", document.customer):
        return False
    if document.visibility == "ticari":
        return can(user, "customer.commercial.view", document.customer)
    return True


@transaction.atomic
def upload_document(actor, customer, *, kind, title, version, valid_from, visibility, upload):
    check(actor, "customer.document.manage", customer)
    content_type = validate_upload(upload)
    doc = CustomerDocument(customer=customer, kind=kind, title=title, version=version, valid_from=valid_from,
                           visibility=visibility, original_name=upload.name[:200], content_type=content_type,
                           size=upload.size, uploaded_by=actor)
    doc.file.save(upload.name, upload, save=False)
    doc.save()
    audit(actor, "customer.document_add", customer, {"belge": (None, f"{title} s.{version}")})
    return doc


@transaction.atomic
def set_document_active(actor, document, active, reason):
    check(actor, "customer.document.manage", document.customer)
    if not reason:
        raise BusinessRuleError("Gerekçe zorunludur.", field="reason")
    doc = CustomerDocument.objects.select_for_update().get(pk=document.pk)
    doc.is_active = active
    doc.save(update_fields=["is_active"])
    audit(actor, "customer.document_status", doc.customer, {str(doc): (not active, active)}, reason=reason)
    return doc


# --- Erişimler -----------------------------------------------------------------------------

@transaction.atomic
def grant_access(actor, customer, user, valid_from, valid_until=None, reason=""):
    check(actor, "customer.access.manage")
    if valid_until and valid_until < valid_from:
        raise BusinessRuleError("Bitiş tarihi başlangıçtan önce olamaz.", field="valid_until")
    if not user.is_active:
        raise BusinessRuleError("Pasif kullanıcıya erişim verilemez.", field="user")
    access = CustomerAccess.objects.create(customer=customer, user=user, valid_from=valid_from,
                                           valid_until=valid_until, reason=reason, granted_by=actor)
    audit(actor, "customer.access_grant", customer,
          {"erisim": (None, f"{user.personnel_no} {valid_from}–{valid_until or 'kalıcı'}")}, reason=reason)
    return access


@transaction.atomic
def revoke_access(actor, access, reason):
    check(actor, "customer.access.manage")
    if not reason:
        raise BusinessRuleError("Gerekçe zorunludur.", field="reason")
    access = CustomerAccess.objects.select_for_update().get(pk=access.pk)
    if access.revoked_at:
        raise BusinessRuleError("Erişim zaten kaldırılmış.")
    access.revoked_at, access.revoked_by = timezone.now(), actor
    access.save()
    audit(actor, "customer.access_revoke", access.customer, {"erisim": (access.user.personnel_no, None)}, reason=reason)
    return access


def order_snapshot(customer):
    """Siparişe kopyalanan müşteri varsayılanları (sonradan kart değişikliğinden etkilenmez)."""
    billing = customer.addresses.filter(is_active=True, default_billing=True).first()
    shipping = customer.addresses.filter(is_active=True, default_shipping=True).first()
    return {"billing_address": billing.as_text() if billing else "", "shipping_address": shipping.as_text() if shipping else "",
            "currency": customer.currency, "payment_method": customer.payment_method,
            "payment_term_days": customer.payment_term_days, "delivery_terms": customer.delivery_terms,
            "delivery_place": customer.delivery_place}
