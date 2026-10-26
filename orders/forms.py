from decimal import Decimal

from django import forms
from django.utils import timezone

from catalog.models import Customer, Product, RecipeRevision, Unit
from core.forms import IdemForm

DEC = forms.NumberInput(attrs={"step": "any", "inputmode": "decimal"})
DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class OrderCreateForm(IdemForm):
    customer = forms.ModelChoiceField(label="Müşteri", queryset=Customer.objects.active())
    customer_ref = forms.CharField(label="Müşteri sipariş ref.", required=False)
    order_date = forms.DateField(label="Sipariş tarihi", widget=DATE, initial=timezone.localdate)
    due_date = forms.DateField(label="Termin", widget=DATE, required=False)
    notes = forms.CharField(label="Not", widget=forms.Textarea(attrs={"rows": 2}), required=False)
    product = forms.ModelChoiceField(label="Ürün (ilk kalem)", queryset=Product.objects.active().select_related("customer"),
                                     help_text="Yalnızca seçilen müşterinin ürün kodları kabul edilir.")
    quantity = forms.DecimalField(label="Miktar", max_digits=14, decimal_places=3, min_value=Decimal("0.001"), widget=DEC)
    unit = forms.ChoiceField(label="Birim", choices=Unit.choices, initial=Unit.BOX)


class LineAddForm(IdemForm):
    product = forms.ModelChoiceField(label="Ürün", queryset=Product.objects.none())
    quantity = forms.DecimalField(label="Miktar", max_digits=14, decimal_places=3, min_value=Decimal("0.001"), widget=DEC)
    unit = forms.ChoiceField(label="Birim", choices=Unit.choices, initial=Unit.BOX)
    due_date = forms.DateField(label="Kalem termini", widget=DATE, required=False)

    def __init__(self, order, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.active().filter(customer=order.customer)


class ApproveLineForm(IdemForm):
    revision = forms.ModelChoiceField(label="Onaylanan reçete revizyonu", queryset=RecipeRevision.objects.none())
    note = forms.CharField(label="Planlama notu", widget=forms.Textarea(attrs={"rows": 2}), required=False)

    def __init__(self, line, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = RecipeRevision.objects.filter(recipe__product=line.product).order_by("-rev_no")
        self.fields["revision"].queryset = qs
        self.fields["revision"].initial = qs.first()
        self.fields["revision"].label_from_instance = lambda r: (
            f"Rev.{r.rev_no} – {r.width_mm} mm / {r.thickness_micron} µ ({r.created_at:%d.%m.%Y})")
