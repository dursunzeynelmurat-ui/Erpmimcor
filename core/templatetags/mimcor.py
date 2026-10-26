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


ACTION_LABELS = {
    "user.create": "Kullanıcı oluşturuldu", "user.update": "Kullanıcı güncellendi", "user.password_change": "Parola değiştirildi",
    "customer.create": "Müşteri oluşturuldu", "customer.update": "Müşteri güncellendi",
    "customer.deactivate": "Pasife alındı", "customer.activate": "Aktifleştirildi",
    "product.create": "Ürün oluşturuldu", "product.update": "Ürün güncellendi",
    "product.deactivate": "Pasife alındı", "product.activate": "Aktifleştirildi",
    "machine.create": "Makine oluşturuldu", "machine.update": "Makine güncellendi",
    "machine.deactivate": "Pasife alındı", "machine.activate": "Aktifleştirildi",
    "material.create": "Malzeme oluşturuldu", "material.update": "Malzeme güncellendi",
    "material.deactivate": "Pasife alındı", "material.activate": "Aktifleştirildi",
    "recipe.revision_create": "Reçete revizyonu oluşturuldu",
    "lot.giris": "Lot girişi", "lot.uretime_cikis": "Üretime çıkış", "lot.iade": "Üretimden iade",
    "lot.sayim_duzeltme": "Sayım düzeltmesi",
    "order.create": "Sipariş açıldı", "order.line_add": "Kalem eklendi", "order.line_approve": "Teknik onay verildi",
    "order.line_status": "Kalem durumu değişti",
    "workorder.create": "İş emri oluşturuldu", "workorder.start": "Üretim başlatıldı", "workorder.close": "İş emri kapatıldı",
    "operation.start": "Operasyon başladı", "operation.complete": "Operasyon tamamlandı",
    "coil.create": "Bobin kaydedildi", "coil.consume": "Bobinden tüketim", "coil.quality": "Bobin kalite durumu",
    "pallet.create": "Palet oluşturuldu", "pallet.uretim_girisi": "Üretimden giriş", "pallet.sevk": "Sevkiyat",
    "pallet.aktarim_cikis": "Aktarım (çıkış)", "pallet.aktarim_giris": "Aktarım (giriş)",
    "pallet.sayim_duzeltme": "Sayım düzeltmesi", "pallet.hasar_ayirma": "Hasarlı ürün ayırma",
    "pallet.hasar_girisi": "Hasarlı ürün paleti girişi", "pallet.weigh": "Tartım", "pallet.accept": "Depo kabulü",
    "pallet.quality": "Kalite durumu", "pallet.reserve": "Siparişe ayırma", "pallet.label_print": "Etiket basıldı",
    "pallet.label_reprint": "Etiket yeniden basıldı",
    "reservation.create": "Rezervasyon", "reservation.cancel": "Rezervasyon iptali", "reservation.ship": "Rezervasyon sevki",
    "shipment.create": "Sevkiyat oluşturuldu", "shipment.line_add": "Sevkiyat satırı eklendi",
    "shipment.line_remove": "Sevkiyat satırı çıkarıldı", "shipment.confirm": "Sevk edildi",
}


@register.filter
def action_label(code):
    return ACTION_LABELS.get(code, code)
