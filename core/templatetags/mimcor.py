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
    "user.role_grant": "Rol atandı", "user.role_revoke": "Rol kaldırıldı", "user.deactivate": "Hesap kapatıldı",
    "role.create": "Rol oluşturuldu", "role.update": "Rol yetkileri değişti", "role.approve": "Rol taslağı onaylandı",
    "role.status": "Rol durumu değişti", "shift.create": "Vardiya oluşturuldu", "shift.update": "Vardiya güncellendi",
    "customer.create": "Müşteri açıldı", "customer.update": "Müşteri kartı güncellendi",
    "customer.activate": "Müşteri aktifleştirildi", "customer.deactivate": "Müşteri pasife alındı",
    "customer.access_grant": "Müşteri erişimi verildi", "customer.access_revoke": "Müşteri erişimi kaldırıldı",
    "customer.contact_add": "İlgili kişi eklendi", "customer.contact_update": "İlgili kişi güncellendi",
    "customer.address_add": "Adres eklendi", "customer.address_update": "Adres güncellendi",
    "customer.document_add": "Belge yüklendi", "customer.document_status": "Belge durumu değişti",
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
    "pallet.coil_hold": "Bağlı bobin blokeye alındı (sevk bekletiliyor)",
    "pallet.coil_release": "Bağlı bobin blokesi kaldırıldı",
    "reservation.create": "Rezervasyon", "reservation.cancel": "Rezervasyon iptali", "reservation.ship": "Rezervasyon sevki",
    "shipment.create": "Sevkiyat oluşturuldu", "shipment.line_add": "Sevkiyat satırı eklendi",
    "shipment.line_remove": "Sevkiyat satırı çıkarıldı", "shipment.confirm": "Sevk onaylandı (stoktan düşüldü)",
    # Aşama 2–4
    "workorder.release": "İş emri serbest bırakıldı", "workorder.withdraw": "İş emri geri çekildi",
    "workorder.update": "Taslak iş emri güncellendi", "workorder.cancel": "İş emri iptal edildi",
    "workorder.archive": "İş emri arşivlendi", "workorder.material_deviation": "Reçete dışı malzeme (sapma)",
    "operation.schedule": "Operasyon planlandı", "operation.stop": "Operasyon durduruldu",
    "operation.resume": "Operasyon devam etti", "operation.handover": "Vardiya devri",
    "operation.handover_ack": "Vardiya devri teslim alındı", "operation.route_exception": "Rota atlama izni",
    "instruction.print": "Makine talimatı basım talebi", "instruction.ack": "Talimat teslim kaydı",
    "order.approve": "Sipariş onaylandı", "order.status": "Sipariş durumu değişti", "order.copy": "Kopya sipariş",
    "order.delete_draft": "Taslak sipariş silindi", "order.line_change": "Kalem değiştirildi",
    "order.line_cancel": "Kalem iptal edildi", "order.archive": "Sipariş arşivlendi",
    "order.compatibility_exception": "Teknik eşdeğerlik onayı", "order.compatibility_revoke": "Eşdeğerlik onayı geri alındı", "order.over_delivery": "Fazla teslim izni",
    "order.return": "Müşteri iadesi", "order.replacement": "Yerine gönderim kalemi", "correction.request": "Düzeltme talebi",
    "correction.uygulandi": "Düzeltme onaylandı ve uygulandı", "correction.ret": "Düzeltme reddedildi",
    "correction.geri_cekildi": "Düzeltme talebi geri çekildi", "coil.correction": "Bobin ağırlığı düzeltildi",
    "pallet.duzeltme": "Onaylı üretim düzeltmesi", "pallet.sevk_geri_alma": "Sevk geri alındı",
    "pallet.iade_girisi": "Müşteri iadesi girişi", "pallet.location": "Depo yeri değişti",
    "pallet.label_result": "Etiket basım sonucu", "pallet.weigh_invalidated": "Tartım geçersiz (bobin düzeltmesi)",
    "workorder.closure_review": "Kapanış yeniden değerlendirildi", "product.stock_level": "Stok seviyesi tanımlandı",
    "reservation.reduce": "Rezervasyon azaltıldı", "reservation.unship": "Sevk geri alındı (rezervasyon)",
    "shipment.prepare": "Sevkiyat hazırlandı", "shipment.reopen": "Sevkiyat hazırlığa döndü",
    "shipment.scan": "Yükleme kontrolü okutması", "shipment.exit": "Araç çıkışı", "shipment.undo": "Sevk geri alındı",
    "shipment.cancel": "Sevkiyat iptal edildi", "shipment.return": "Müşteri iadesi alındı",
}


@register.filter
def action_label(code):
    return ACTION_LABELS.get(code, code)


FIELD_LABELS = {
    "revizyon": "Reçete revizyonu", "bileşenler": "Reçete bileşenleri (%)",
    "operations": "Operasyon makineleri", "good_kg": "Sağlam çıktı (kg)", "units_per_box": "Kutu içi adet",
    "box_count": "Kutu adedi", "quality_status": "Kalite durumu", "weight_status": "Tartım durumu",
    "remaining_kg": "Kalan ağırlık (kg)", "weight_kg": "Bobin ağırlığı (kg)", "weight_source": "Ağırlık kaynağı",
    "gross_kg": "Brüt ağırlık (kg)", "pallet_tare_kg": "Palet darası (kg)", "box_tare_kg": "Kutu darası (kg)",
    "core_tare_kg": "Masura darası (kg)", "issued_kg": "Verilen malzeme (kg)", "scrap_kg": "Fire (kg)",
    "difference_kg": "Denge farkı (kg)", "accounted_kg": "Hesaplanan toplam (kg)",
    "shipped_quantity": "Sevk edilen kutu", "approved_revision": "Onaylı reçete revizyonu",
    "recipe_revision": "Reçete revizyonu", "revision": "Revizyon", "rev_no": "Revizyon no",
    "product": "Ürün", "customer": "Müşteri", "order": "Sipariş", "order_line": "Sipariş kalemi",
    "order_date": "Sipariş tarihi", "work_order": "İş emri", "machine": "Makine", "pallet": "Palet",
    "quantity": "Miktar", "target_quantity": "Hedef miktar", "unit": "Birim", "status": "Durum",
    "role": "Rol", "full_name": "Ad soyad", "password": "Parola", "is_active": "Aktiflik",
    "code": "Kod", "name": "Ad", "location": "Depo yeri", "notes": "Notlar", "note": "Not",
    "kaynak": "Kaynak palet", "ayrilan": "Siparişe ayrılan", "satir": "Sevkiyat satırı",
    "bobin": "Bağlı bobin durumu", "coil": "Bobin", "coil_hold": "Bobin blokajı", "machine_type": "Makine türü", "kind": "Tür",
    "width_mm": "En (mm)", "thickness_micron": "Kalınlık (mikron)", "length_m": "Uzunluk (m)",
    "color": "Renk", "print_spec": "Baskı bilgisi", "perforation_spec": "Perfore bilgisi",
    "process_notes": "Üretim notları", "phone": "Telefon", "email": "E-posta", "address": "Adres",
    "contact": "İlgili kişi", "tax_no": "Vergi no", "purpose": "Üretim amacı",
    "assigned_foreman": "Sorumlu ustabaşı", "planned_start": "Planlanan başlangıç", "planned_date": "Planlanan tarih",
    "shift": "Vardiya", "queue_position": "Çalışma sırası", "operasyonlar": "Operasyonlar", "due_date": "Termin",
    "needs_review": "Kopya onayı bekliyor", "gecersiz_talimat": "Geçersiz işaretlenen talimat", "devir": "Vardiya devri",
    "teslim": "Teslim alan", "talimat": "Talimat", "deger": "Değer", "iade": "İade", "okutma": "Okutma",
    "etiket": "Etiket", "seviye": "Alt / hedef seviye", "ek_miktar": "Ek miktar", "kabul_edilen": "Kabul edilen revizyon",
    "rota_atlama": "Rota atlama", "malzeme": "Malzeme", "kalemler": "Kalemler", "archived": "Arşiv",
    "serbest_rezervasyon": "Bırakılan rezervasyonlar", "cancel_reason": "İptal gerekçesi",
    "sync_revision": "Revizyon eşitleme",
}

@register.filter
def field_label(key):
    return FIELD_LABELS.get(key, "Ek bilgi")

@register.simple_tag
def history_value(value, key, object_type=""):
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Evet" if value else "Hayır"
    if isinstance(value, dict):
        return "; ".join(f"{k}: {qty(v)}" for k, v in value.items())
    if isinstance(value, list):
        return " → ".join(str(v) for v in value)
    from django.apps import apps
    models = list(apps.get_models())
    if object_type:  # önce kaydın kendi modelinin seçenekleri (ör. iş emri 'taslak' ≠ rol 'taslak')
        own = [m for m in models if m._meta.label_lower == object_type]
        models = own + [m for m in models if m not in own]
    for model in models:
        for field in model._meta.fields:
            if field.name == key and field.choices:
                label = dict(field.flatchoices).get(value)
                if label:
                    return label
    if key.endswith("_kg") or key in ("quantity", "target_quantity", "width_mm", "thickness_micron", "length_m"):
        return qty(value)
    return value


@register.inclusion_tag("partials/spec_card.html", takes_context=True)
def spec_card(context, revision, product=None, title="Teknik özellikler", show_customer_notes=True):
    """Teknik özet kartı (karışım içermez; reçete bileşimi yalnız reçete ekranında, yetkiliye). Baskılı üründe
    görüntüleme yetkisi olana geçerli baskı görseli sürümü ve bağlantısı gösterilir."""
    from catalog import spec
    from accounts.permissions import can
    artwork = None
    request = context.get("request")
    if revision and product and revision.print_status == spec.YesNo.YES and request and \
            can(request.user, "artwork.view", product):
        from catalog import artwork as art
        artwork = {"valid": art.valid_artwork(product, revision), "product": product}
    return {"revision": revision, "sections": spec.card(revision) if revision else [], "product": product,
            "title": title, "customer": product.customer if (product and show_customer_notes) else None,
            "artwork": artwork}
