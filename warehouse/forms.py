from decimal import Decimal, InvalidOperation

from django import forms

from catalog.models import Unit
from core.forms import IdemForm
from core.templatetags.mimcor import qty
from core.models import QualityStatus
from orders.models import LineStatus, OrderLine

from .models import Pallet, PalletStatus, Reservation, ReservationStatus

DEC = forms.NumberInput(attrs={"step": "any", "inputmode": "decimal"})
INT = forms.NumberInput(attrs={"inputmode": "numeric", "min": "1"})


def kg_field(label, required=False, help_text=""):
    return forms.DecimalField(label=label, max_digits=12, decimal_places=3, required=required, min_value=Decimal("0"),
                              widget=DEC, help_text=help_text)


class WeighingForm(IdemForm):
    gross_kg = kg_field("Brüt ağırlık (kg)", required=True, help_text="Terazide okunan palet brüt ağırlığı. Zorunlu.")
    pallet_tare_kg = kg_field("Palet darası (kg)", help_text="Bilinmiyorsa boş bırakın (boş ≠ 0).")
    box_tare_kg = kg_field("Kutu darası (kg / kutu)", help_text="Bilinmiyorsa boş bırakın.")
    core_tare_kg = kg_field("Masura darası (kg, toplam)", help_text="Masura yoksa 0 yazın; bilinmiyorsa boş bırakın.")
    roll_weights = forms.CharField(label="Tekil rulo ağırlıkları (kg)", required=False,
                                   help_text="İsteğe bağlı. Boşluk veya noktalı virgülle ayırın, ör. 2,45; 2,50; 2,48")
    note = forms.CharField(label="Not", required=False)

    def __init__(self, *args, with_location=False, **kwargs):
        super().__init__(*args, **kwargs)
        if with_location:
            self.fields["location"] = forms.CharField(label="Depo yeri", required=False)

    def clean_roll_weights(self):
        raw = self.cleaned_data.get("roll_weights", "").replace(";", " ").split()
        try:
            return [Decimal(v.replace(",", ".")) for v in raw]
        except InvalidOperation:
            raise forms.ValidationError("Rulo ağırlıkları sayı olmalı.")


class QualityForm(IdemForm):
    quality_status = forms.ChoiceField(label="Kalite durumu", choices=QualityStatus.choices)
    reason = forms.CharField(label="Gerekçe", widget=forms.Textarea(attrs={"rows": 2}))


class TransferForm(IdemForm):
    quantity = forms.IntegerField(label="Aktarılacak kutu", min_value=1, widget=INT)
    target_code = forms.CharField(label="Hedef palet no", required=False,
                                  help_text="Boş bırakılırsa aynı ürün için yeni palet açılır.")
    reason = forms.CharField(label="Gerekçe", widget=forms.Textarea(attrs={"rows": 2}))

    def clean_target_code(self):
        code = self.cleaned_data.get("target_code", "").strip().upper()
        if not code:
            return None
        try:
            return Pallet.objects.get(code=code)
        except Pallet.DoesNotExist:
            raise forms.ValidationError("Bu numarada palet yok.")


class CountForm(IdemForm):
    counted = forms.IntegerField(label="Sayılan kutu adedi", min_value=0, widget=INT)
    reason = forms.CharField(label="Gerekçe", widget=forms.Textarea(attrs={"rows": 2}))


class DamageForm(IdemForm):
    quantity = forms.IntegerField(label="Hasarlı kutu", min_value=1, widget=INT)
    reason = forms.CharField(label="Gerekçe", widget=forms.Textarea(attrs={"rows": 2}))


class ReserveForm(IdemForm):
    order_line = forms.ModelChoiceField(label="Sipariş kalemi", queryset=OrderLine.objects.none(),
                                        help_text="Bu sürümde yalnız kutu birimli kalemler listelenir; "
                                                  "kg/adet/rulo kalemleri stoktan ayrılamaz.")
    quantity = forms.IntegerField(label="Ayrılacak kutu", min_value=1, widget=INT)

    def __init__(self, pallet, *args, user=None, **kwargs):
        from accounts.permissions import scoped
        super().__init__(*args, **kwargs)
        lines = scoped(OrderLine.objects.all(), user, "reservation.manage") if user else OrderLine.objects.none()
        self.fields["order_line"].queryset = (lines.filter(product=pallet.product, unit=Unit.BOX)
                                              .exclude(status__in=[LineStatus.CANCELLED, LineStatus.SHIPPED])
                                              .select_related("order"))
        self.fields["order_line"].label_from_instance = lambda ln: f"{ln.code} – {qty(ln.quantity)} {ln.get_unit_display()}"


class ShipmentCreateForm(IdemForm):
    vehicle = forms.CharField(label="Araç / plaka", required=False)
    notes = forms.CharField(label="Not", required=False, widget=forms.Textarea(attrs={"rows": 2}))


class ShipmentLineForm(IdemForm):
    reservation = forms.ModelChoiceField(label="Rezervasyon (palet → kalem)", queryset=Reservation.objects.none())
    quantity = forms.IntegerField(label="Sevk edilecek kutu", min_value=1, widget=INT)

    def __init__(self, shipment, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reservation"].queryset = (Reservation.objects.filter(order_line__order=shipment.order,
                                                                          status=ReservationStatus.ACTIVE)
                                               .select_related("pallet", "order_line__order"))
        self.fields["reservation"].label_from_instance = (
            lambda r: f"{r.pallet.code} → {r.order_line.code} ({r.open_quantity} kutu açık, kalite: "
                      f"{r.pallet.get_quality_status_display()})")


class PalletFilterForm(forms.Form):
    q = forms.CharField(label="Palet / ürün", required=False)
    status = forms.ChoiceField(label="Durum", required=False, choices=[("", "Tümü (boş hariç)")] + PalletStatus.choices)
