from decimal import Decimal

from django import forms

from core.forms import IdemForm, IdemModelForm

from .models import Customer, Machine, Material, MaterialLot, Product, RecipeRevision

DEC = {"widget": forms.NumberInput(attrs={"step": "any", "inputmode": "decimal"})}


class CustomerForm(IdemModelForm):
    class Meta:
        model = Customer
        fields = ["code", "name", "contact"]


class ProductForm(IdemModelForm):
    class Meta:
        model = Product
        fields = ["code", "customer", "name", "unit", "units_per_box", "notes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            # Ürün kodu ve müşterisi kalıcıdır.
            for name in ("code", "customer"):
                self.fields[name].disabled = True
        else:
            self.fields["customer"].queryset = Customer.objects.active()


class MachineForm(IdemModelForm):
    class Meta:
        model = Machine
        fields = ["code", "name", "machine_type"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["code"].disabled = True


class MaterialForm(IdemModelForm):
    class Meta:
        model = Material
        fields = ["code", "name", "kind", "unit"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["code"].disabled = True


class ActiveToggleForm(IdemForm):
    reason = forms.CharField(label="Gerekçe", widget=forms.Textarea(attrs={"rows": 2}))


class LotReceiveForm(IdemForm):
    material = forms.ModelChoiceField(label="Malzeme", queryset=Material.objects.active())
    quantity = forms.DecimalField(label="Miktar (kg)", max_digits=14, decimal_places=3, min_value=Decimal("0.001"), **DEC)
    supplier_lot = forms.CharField(label="Tedarikçi lot no", required=False)


class LotAdjustForm(IdemForm):
    counted = forms.DecimalField(label="Sayılan miktar (kg)", max_digits=14, decimal_places=3, min_value=Decimal("0"), **DEC)
    reason = forms.CharField(label="Gerekçe", widget=forms.Textarea(attrs={"rows": 2}))


class RevisionForm(IdemModelForm):
    COMPONENT_ROWS = 5

    class Meta:
        model = RecipeRevision
        fields = ["width_mm", "thickness_micron", "length_m", "color", "print_spec", "perforation_spec",
                  "process_notes", "change_reason"]

    def __init__(self, *args, initial_components=None, **kwargs):
        super().__init__(*args, **kwargs)
        materials = Material.objects.active()
        initial_components = initial_components or []
        for i in range(self.COMPONENT_ROWS):
            m, p = initial_components[i] if i < len(initial_components) else (None, None)
            self.fields[f"material_{i}"] = forms.ModelChoiceField(
                label=f"Bileşen {i + 1}", queryset=materials, required=False, initial=m)
            self.fields[f"pct_{i}"] = forms.DecimalField(
                label=f"Oran {i + 1} (%)", max_digits=6, decimal_places=3, required=False, initial=p,
                min_value=Decimal("0.001"), **DEC)

    def component_rows(self):
        return [(self[f"material_{i}"], self[f"pct_{i}"]) for i in range(self.COMPONENT_ROWS)]

    def spec_fields(self):
        return [self[n] for n in self.Meta.fields]

    def clean(self):
        data = super().clean()
        comps = []
        for i in range(self.COMPONENT_ROWS):
            m, p = data.get(f"material_{i}"), data.get(f"pct_{i}")
            if m and p:
                comps.append((m, p))
            elif m or p:
                self.add_error(f"pct_{i}", "Malzeme ve oran birlikte girilmelidir.")
        data["components"] = comps
        return data

    def spec(self):
        return {n: self.cleaned_data[n] for n in self.Meta.fields if n != "change_reason"}
