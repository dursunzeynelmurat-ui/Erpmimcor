from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def qty(value):
    """Ondalık miktarı gereksiz sıfırlar olmadan, Türkçe ondalık virgülüyle gösterir."""
    if value is None or value == "":
        return "—"
    if not isinstance(value, Decimal):
        try:
            value = Decimal(str(value))
        except Exception:
            return value
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


@register.filter
def get_item(mapping, key):
    return mapping.get(key) if mapping else None


@register.simple_tag
def idem_input():
    """Her düğme için ayrı, tek kullanımlık form anahtarı."""
    import uuid

    from django.utils.html import format_html

    return format_html('<input type="hidden" name="idem_key" value="{}">', uuid.uuid4().hex)
