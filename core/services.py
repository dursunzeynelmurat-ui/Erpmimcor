"""Çapraz servisler: kod üretimi, işlem geçmişi, tekrar-gönderim koruması."""
from dataclasses import dataclass
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from .exceptions import BusinessRuleError
from .models import AuditLog, CodeSequence, IdempotencyKey


def next_code(prefix, width=5):
    """Yıl bazlı artan, benzersiz kod üretir. Çağıran transaction içinde kilitlenir."""
    year = timezone.localdate().year
    with transaction.atomic():
        CodeSequence.objects.bulk_create(
            [CodeSequence(prefix=prefix, year=year, last_value=0)], ignore_conflicts=True
        )
        seq = CodeSequence.objects.select_for_update().get(prefix=prefix, year=year)
        seq.last_value += 1
        seq.save(update_fields=["last_value"])
    return f"{prefix}-{year}-{seq.last_value:0{width}d}"


def _jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "pk"):
        return str(value)
    return value


def audit(user, action, obj, changes=None, reason="", code=None):
    """İşlem geçmişine kayıt ekler. changes: {alan: (eski, yeni)}"""
    clean = {k: [_jsonable(v[0]), _jsonable(v[1])] for k, v in (changes or {}).items()}
    return AuditLog.objects.create(
        user=user if (user and user.is_authenticated) else None,
        action=action,
        object_type=obj._meta.label_lower,
        object_id=str(obj.pk),
        object_code=code or getattr(obj, "code", "") or str(obj)[:64],
        changes=clean,
        reason=reason or "",
    )


def history_for(obj):
    return AuditLog.objects.filter(object_type=obj._meta.label_lower, object_id=str(obj.pk)).select_related("user")


def diff(instance, data):
    """instance alanlarını data ile karşılaştırır, değişenleri {alan: (eski, yeni)} döndürür."""
    changes = {}
    for field, new in data.items():
        old = getattr(instance, field)
        if old != new:
            changes[field] = (old, new)
    return changes


@dataclass
class OnceResult:
    value: object
    duplicate: bool
    url: str = ""


def perform_once(*, user, key, action, fn, url_for=None):
    """fn() tek transaction içinde ve aynı form anahtarı için yalnızca bir kez çalışır.

    Aynı anahtarla gelen ikinci istek ilk işlem bitene kadar bekler (benzersiz indeks
    kilidi), sonra yeni hareket üretmeden ilk sonucun adresini döndürür. fn hata
    verirse anahtar da geri alınır, kullanıcı formu düzeltip yeniden gönderebilir.
    """
    if not key:
        raise BusinessRuleError("Form anahtarı eksik. Sayfayı yenileyip tekrar deneyin.")
    with transaction.atomic():
        try:
            with transaction.atomic():
                record = IdempotencyKey.objects.create(key=key, user=user, action=action)
        except IntegrityError:
            existing = IdempotencyKey.objects.filter(key=key).first()
            if existing is None or existing.user_id != user.pk:
                raise BusinessRuleError("Geçersiz form anahtarı. Sayfayı yenileyip tekrar deneyin.")
            return OnceResult(value=None, duplicate=True, url=existing.result_url)
        value = fn()
        url = url_for(value) if url_for else ""
        if url:
            record.result_url = url
            record.save(update_fields=["result_url"])
    return OnceResult(value=value, duplicate=False, url=url)


def num(value):
    """Mesajlar için ondalık gösterimi: 40.000 → '40', 12.500 → '12.5' (bilimsel gösterim yok)."""
    if value is None:
        return ""
    return format(value.normalize(), "f") if hasattr(value, "normalize") else str(value)
