from decimal import Decimal

from django import forms

from core.forms import IdemForm, IdemModelForm

from .models import (Customer, CustomerAddress, CustomerContact, DocumentKind, DocumentVisibility, Machine, Material,
                     MaterialLot, Product, RecipeRevision)

DEC = {"widget": forms.NumberInput(attrs={"step": "any", "inputmode": "decimal"})}


class CustomerCreateForm(IdemModelForm):
    """Kısa açılış; diğer bölümler kartta sonradan tamamlanır."""

    confirm_similar = forms.BooleanField(label="Benzer ünvanlı kayıtların farklı firma olduğunu onaylıyorum",
                                         required=False)

    class Meta:
        model = Customer
        fields = ["code", "name", "short_name", "entity_type", "country", "tax_id_type", "tax_id", "tax_office"]


SECTION_FIELDS = {
    "firma": ("Firma", ["name", "short_name", "entity_type", "country", "language", "website", "notes"]),
    "kimlik": ("Kimlik", ["tax_id_type", "tax_id", "tax_office"]),
    "ticari": ("Ticari varsayılanlar", ["currency", "payment_method", "payment_term_days", "delivery_terms",
                                        "delivery_place", "po_required"]),
    "operasyon": ("Operasyon", ["receiving_hours", "appointment_required", "packing_instructions",
                                "labeling_instructions"]),
}


def customer_section_form(section):
    title, fields = SECTION_FIELDS[section]
    meta = type("Meta", (), {"model": Customer, "fields": fields})
    form = type(f"Customer{section.title()}Form", (IdemModelForm,), {"Meta": meta})
    form.section_title = title
    return form


class ContactForm(IdemModelForm):
    class Meta:
        model = CustomerContact
        fields = ["name", "department", "email", "phone", "is_primary", "is_active"]


class AddressForm(IdemModelForm):
    class Meta:
        model = CustomerAddress
        fields = ["purpose", "name", "country", "region", "city", "postal_code", "line", "default_billing",
                  "default_shipping", "is_active"]
        widgets = {"line": forms.Textarea(attrs={"rows": 2})}


class DocumentForm(IdemForm):
    kind = forms.ChoiceField(label="Belge türü", choices=DocumentKind.choices)
    title = forms.CharField(label="Başlık", max_length=120)
    version = forms.CharField(label="Sürüm", max_length=20, initial="1")
    valid_from = forms.DateField(label="Geçerlilik tarihi", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    visibility = forms.ChoiceField(label="Görünürlük", choices=DocumentVisibility.choices)
    file = forms.FileField(label="Dosya", help_text="PDF, PNG veya JPG; en fazla 10 MB.")


class AccessForm(IdemForm):
    user = forms.ModelChoiceField(label="Kullanıcı", queryset=None)
    valid_from = forms.DateField(label="Başlangıç", widget=forms.DateInput(attrs={"type": "date"}))
    valid_until = forms.DateField(label="Bitiş (boşsa kalıcı)", required=False,
                                  widget=forms.DateInput(attrs={"type": "date"}))
    reason = forms.CharField(label="Gerekçe", required=False)

    def __init__(self, *args, **kwargs):
        from django.utils import timezone

        from accounts.models import User
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = User.objects.filter(is_active=True)
        self.fields["valid_from"].initial = timezone.localdate


class ProductForm(IdemModelForm):
    class Meta:
        model = Product
        fields = ["code", "customer", "name", "unit", "package_type", "units_per_box", "nominal_package_kg", "notes"]

    def __init__(self, *args, user=None, **kwargs):
        self._user = user
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            # Ürün kodu ve müşterisi kalıcıdır.
            for name in ("code", "customer"):
                self.fields[name].disabled = True
        else:
            from accounts.permissions import scoped
            qs = Customer.objects.active()
            self.fields["customer"].queryset = scoped(qs, self._user, "product.create") if self._user else qs.none()


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
    supplier_lot = forms.CharField(label="Tedarikçi parti / irsaliye no", help_text="Zorunlu. Mal kabulü her zaman yeni lot açar; sayım farkı için kullanılmaz.")


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
