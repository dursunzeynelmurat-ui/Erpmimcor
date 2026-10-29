from decimal import Decimal

from django import forms
from django.urls import reverse
from django.utils import timezone

from catalog.models import Customer, Product
from core.forms import IdemForm, TRDecimalField

from .models import (INTERNAL_KINDS, CriterionUnit, CustomerFeedback, QualityDecision, SampleCriterion, SampleKind,
                     SampleUnit, StockSource)

STOCK_SOURCE_CHOICES = [("", "Seçin")] + [(v, label) for v, label in StockSource.choices if v]

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
DATETIME = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class SampleCreateForm(IdemForm):
    kind = forms.ChoiceField(label="Numune türü", choices=[("", "Seçin")] + list(SampleKind.choices),
                             help_text="Müşteriden gelen referans firma stoğunu etkilemez. Firma içi numune için "
                                       "stoktan kutu ayırma ayrıca yapılır.")
    customer = forms.ModelChoiceField(label="Müşteri", queryset=Customer.objects.none())
    product = forms.ModelChoiceField(label="Ürün", queryset=Product.objects.none(), required=False,
                                     help_text="Firma içi numunede zorunlu. Yalnız seçilen müşterinin ürünleri.")
    order_line_code = forms.CharField(label="Sipariş kalemi kodu", required=False, max_length=40,
                                      help_text="Varsa. Örnek: SIP-2026-00012/01")
    work_order_code = forms.CharField(label="İş emri no", required=False, max_length=30)
    coil_code = forms.CharField(label="Bobin no", required=False, max_length=30)
    pallet_code = forms.CharField(label="Palet no", required=False, max_length=30,
                                  help_text="Bağlantı içindir; stok düşmez. Stoktan ayırma kayıttan sonra yapılır.")
    stock_source = forms.ChoiceField(
        label="Stok kaynağı (firma içi numune)", choices=STOCK_SOURCE_CHOICES, required=False,
        help_text="Firma stoğundan tam kutu: kayıttan sonra 'Stoktan ayır' yapılmadan gönderilemez/imha edilemez. "
                  "Adet/rulo/kg numunede stok düşümü desteklenmez; 'Stok dışı' seçin. Müşteri referansında boş bırakın.")
    counterpart = forms.CharField(label="Teslim eden / teslim alan kişi", required=False, max_length=120)
    occurred_at = forms.DateTimeField(label="Alınma / teslim tarihi ve saati", widget=DATETIME,
                                      initial=lambda: timezone.localtime().strftime("%Y-%m-%dT%H:%M"))
    quantity = TRDecimalField(label="Miktar", max_digits=12, decimal_places=3, min_value=Decimal("0.001"),
                              help_text="Ondalık için virgül. Birimler arasında dönüşüm yapılmaz.")
    unit = forms.ChoiceField(label="Birim", choices=SampleUnit.choices)
    room = forms.CharField(label="Oda", required=False, max_length=40)
    cabinet = forms.CharField(label="Dolap", required=False, max_length=40)
    shelf = forms.CharField(label="Raf", required=False, max_length=40)
    box = forms.CharField(label="Kutu", required=False, max_length=40)
    retention_until = forms.DateField(label="Saklama son tarihi", widget=DATE, required=False)
    retention_conditions = forms.CharField(label="Saklama koşulu", required=False, max_length=200,
                                           help_text="Varsa. Örnek: güneş görmeyen, kuru ortam.")
    notes = forms.CharField(label="İç açıklama", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    shared_note = forms.CharField(label="Müşteriyle paylaşılabilir not", required=False,
                                  widget=forms.Textarea(attrs={"rows": 2}),
                                  help_text="Satış ekibi görür. İç ölçüm ve reçete bilgisi yazmayın.")

    def __init__(self, *args, user=None, **kwargs):
        from accounts.permissions import scoped
        super().__init__(*args, **kwargs)
        customers = scoped(Customer.objects.active(), user, "sample.manage") if user else Customer.objects.none()
        self.fields["customer"].queryset = customers
        cid = self.data.get("customer") if self.is_bound else None
        customer = customers.filter(pk=cid).first() if cid and str(cid).isdigit() else None
        field = self.fields["product"]
        field.queryset = Product.objects.active().filter(customer=customer) if customer else Product.objects.none()
        field.widget.attrs.update({
            "data-customer-field": "customer",
            "data-products-url": reverse("samples:customer_products", args=[0]).replace("/0/", "/__ID__/"),
            "data-msg-no-customer": "Önce müşteri seçin.", "data-msg-empty": "Bu müşteriye ait aktif ürün yok.",
            "data-msg-ready": "Yalnız seçilen müşterinin ürünleri listelenir.", "data-msg-loading": "Ürünler yükleniyor…",
            "data-msg-error": "Ürün listesi yüklenemedi. Tekrar deneyin.",
            "data-loaded-customer": str(customer.pk) if customer else "",
        })
        if customer is None:
            field.widget.attrs["disabled"] = "disabled"

    def clean(self):
        data = super().clean()
        if data.get("kind") in INTERNAL_KINDS and not data.get("stock_source"):
            self.add_error("stock_source", "Firma içi numunede stok kaynağını seçin (firma stoğundan / stok dışı / "
                                           "daha önce stoktan çıkarıldı).")
        return data


class StockSourceForm(IdemForm):
    stock_source = forms.ChoiceField(label="Stok kaynağı", choices=STOCK_SOURCE_CHOICES)
    reason = forms.CharField(label="Gerekçe", max_length=200, help_text="Örnek: numune hattan kesildi, stoktan değil.")


class LinkMovementForm(IdemForm):
    movement = forms.ChoiceField(label="Önceki stok çıkışı", choices=[],
                                 help_text="Aynı ürünün, numuneyle aynı kutu sayısındaki çıkış hareketleri listelenir. "
                                           "Bağlama yeni stok düşümü yapmaz.")

    def __init__(self, *args, sample=None, **kwargs):
        from .services import prior_movement_candidates
        super().__init__(*args, **kwargs)
        moves = prior_movement_candidates(sample) if sample is not None else []
        self.fields["movement"].choices = [("", "Seçin")] + [
            (m.pk, f"{m.pallet.code} · {m.get_kind_display()} · {-m.quantity} kutu · {m.at:%d.%m.%Y}")
            for m in moves]


class MoveForm(IdemForm):
    room = forms.CharField(label="Oda", required=False, max_length=40)
    cabinet = forms.CharField(label="Dolap", required=False, max_length=40)
    shelf = forms.CharField(label="Raf", required=False, max_length=40)
    box = forms.CharField(label="Kutu", required=False, max_length=40)
    note = forms.CharField(label="Not", required=False, max_length=200)


class AllocateForm(IdemForm):
    pallet_code = forms.CharField(label="Palet no", max_length=30)
    boxes = forms.IntegerField(label="Ayrılacak kutu", min_value=1,
                               help_text="Numunenin kutu adediyle aynı olmalı. Bu kutular stoktan düşer ve sevk "
                                         "edilemez; gönderim ve iade tekrar stok hareketi yaratmaz.")
    reason = forms.CharField(label="Not", required=False, max_length=200)


class SendForm(IdemForm):
    counterpart = forms.CharField(label="Kime / nasıl gönderildi", max_length=120,
                                  help_text="Örnek: Ayşe Kurgu, kargo takip 123")
    note = forms.CharField(label="Not", required=False, max_length=200)


class ReturnForm(IdemForm):
    counterpart = forms.CharField(label="Getiren", required=False, max_length=120)
    note = forms.CharField(label="Not", required=False, max_length=200)


class DecisionForm(IdemForm):
    decision = forms.ChoiceField(label="Kalite kararı", choices=[(v, l) for v, l in QualityDecision.choices
                                                                 if v != QualityDecision.PENDING])
    reason = forms.CharField(label="Gerekçe", widget=forms.Textarea(attrs={"rows": 2}))
    pallet_codes = forms.CharField(label="Kararın kapsadığı paletler", required=False, max_length=300,
                                   help_text="Virgülle ayırın. Boşsa karar yalnız bu numune içindir. Palet kalite "
                                             "durumu değişmez; blokaj kaldırma ayrı yetkili işlemdir.")
    coil_codes = forms.CharField(label="Kararın kapsadığı bobinler", required=False, max_length=300)


class FeedbackForm(IdemForm):
    feedback = forms.ChoiceField(label="Müşteri geri bildirimi", choices=[(v, l) for v, l in CustomerFeedback.choices if v])
    note = forms.CharField(label="Not", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    reference = forms.CharField(label="Referans", required=False, max_length=200, help_text="Örnek: e-posta tarihi.")


class MeasurementForm(IdemForm):
    criterion = forms.ModelChoiceField(label="Kriter", queryset=SampleCriterion.objects.filter(is_active=True))
    value = TRDecimalField(label="Ölçülen değer", max_digits=12, decimal_places=3, required=False,
                           help_text="Kriterin biriminde. Metin kriterinde boş bırakıp gözlemi yazın.")
    text_value = forms.CharField(label="Gözlem", required=False, max_length=200)
    note = forms.CharField(label="Not", required=False, max_length=200)


class AttachmentForm(IdemForm):
    title = forms.CharField(label="Başlık", max_length=120)
    file = forms.FileField(label="Dosya", help_text="PDF, PNG veya JPG; en fazla 10 MB.")


class CriterionForm(IdemForm):
    name = forms.CharField(label="Kriter adı", max_length=120, help_text="Örnek: Kalınlık, Kopma dayanımı.")
    unit = forms.ChoiceField(label="Birim", choices=CriterionUnit.choices)
    min_value = TRDecimalField(label="Alt sınır", max_digits=12, decimal_places=3, required=False,
                               help_text="Yalnız yetkili kişinin tanımladığı kabul aralığı. Bilinmiyorsa boş.")
    max_value = TRDecimalField(label="Üst sınır", max_digits=12, decimal_places=3, required=False)
    description = forms.CharField(label="Açıklama / ölçüm yöntemi", required=False, max_length=200)
