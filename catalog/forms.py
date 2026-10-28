import re
from decimal import Decimal

from django import forms
from django.db import models

from core.forms import IdemForm, IdemModelForm, TRDecimalField

from . import spec

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


HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def spec_form_field(f, required=False):
    """catalog/spec.py tanımından form alanı. Etiket birimi içerir; açıklama alan dolduğunda da görünür."""
    model_field = RecipeRevision._meta.get_field(f.name)
    label = f"{f.label} ({f.unit})" if f.unit else f.label
    if f.kind == "decimal":
        return TRDecimalField(label=label, max_digits=model_field.max_digits, decimal_places=model_field.decimal_places,
                              required=required, help_text=f.help)
    if f.kind == "int":
        return forms.IntegerField(label=label, min_value=1, max_value=32, required=required, help_text=f.help)
    if f.kind == "choice":
        return forms.ChoiceField(label=label, choices=[("", spec.UNSPECIFIED)] + list(f.choices.choices),
                                 required=required, help_text=f.help)
    if f.kind == "color":
        return forms.CharField(label=label, required=False, max_length=7, help_text=f.help + " Biçim: #RRGGBB.",
                               widget=forms.TextInput(attrs={"data-color-preview": "1"}))
    widget = forms.Textarea(attrs={"rows": 2}) if isinstance(model_field, models.TextField) else forms.TextInput()
    return forms.CharField(label=label, required=False, max_length=getattr(model_field, "max_length", None),
                           widget=widget, help_text=f.help)


class SpecFieldsMixin:
    """Teknik özellik alanlarını forma ekler; koşullu alanlar (baskı/körük/perfore) için data-show-if verir.
    Gizli alanların eski değerleri sunucuda spec.clean_hidden ile temizlenir."""

    spec_required = ("width_mm", "thickness_micron")

    def add_spec_fields(self, initial=None, required_now=False, names=None):
        for f in spec.FIELDS:
            if names is not None and f.name not in names:
                continue
            field = spec_form_field(f, required=required_now and f.name in self.spec_required)
            if f.show_if:
                field.widget.attrs["data-show-if"] = f"{f.show_if[0]}:{','.join(f.show_if[1])}"
            if initial and f.name in initial:
                field.initial = initial[f.name]
            self.fields[f.name] = field

    def spec_sections(self):
        return [(title, [self[f.name] for f in spec.FIELDS if f.section == key and f.name in self.fields])
                for key, title in spec.SECTIONS]

    def clean_spec_values(self, names=None):
        names = names or spec.FIELD_NAMES
        values = {n: self.cleaned_data.get(n) for n in names}
        if values.get("color_preview") and not HEX_COLOR.match(values["color_preview"]):
            self.add_error("color_preview", "Renk kodunu #RRGGBB biçiminde yazın (ör. #1E5AA8) veya boş bırakın.")
        for n in names:
            f = spec.BY_NAME[n]
            if f.kind == "decimal" and values.get(n) is not None and values[n] <= 0:
                self.add_error(n, f"{f.label} sıfırdan büyük olmalı; bilinmiyorsa boş bırakın.")
        values = spec.clean_hidden(values) if names == spec.FIELD_NAMES else values
        for n in names:
            if values.get(n) is None and spec.BY_NAME[n].kind not in ("decimal", "int"):
                values[n] = ""
        return values


class RevisionForm(SpecFieldsMixin, IdemForm):
    COMPONENT_ROWS = 5

    def __init__(self, *args, initial_components=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_spec_fields(required_now=True)
        self.fields["change_reason"] = forms.CharField(label="Değişiklik gerekçesi", required=False,
                                                       widget=forms.Textarea(attrs={"rows": 2}))
        materials = Material.objects.active()
        initial_components = initial_components or []
        for i in range(self.COMPONENT_ROWS):
            m, p = initial_components[i] if i < len(initial_components) else (None, None)
            self.fields[f"material_{i}"] = forms.ModelChoiceField(
                label=f"Bileşen {i + 1}", queryset=materials, required=False, initial=m)
            self.fields[f"pct_{i}"] = TRDecimalField(
                label=f"Oran {i + 1} (%)", max_digits=6, decimal_places=3, required=False, initial=p,
                min_value=Decimal("0.001"))

    def component_rows(self):
        return [(self[f"material_{i}"], self[f"pct_{i}"]) for i in range(self.COMPONENT_ROWS)]

    def spec_fields(self):
        return [self[n] for n in spec.FIELD_NAMES] + [self["change_reason"]]

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
        data["spec"] = self.clean_spec_values()
        for name, message in spec.approval_problems(data["spec"]):
            self.add_error(name, message)
        return data

    def spec(self):
        return self.cleaned_data["spec"]


class RecipeDraftForm(SpecFieldsMixin, IdemForm):
    """Satır eklenebilen reçete düzenleyici. Boş satırlar yok sayılır; malzemesiz oran kabul edilmez."""

    def __init__(self, *args, rows=1, initial_lines=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.add_spec_fields()
        self.fields["batch_kg"] = TRDecimalField(
            label="Hesap için karışım miktarı (kg)", max_digits=10, decimal_places=3, required=False,
            help_text="Oranlar bu miktara göre kg'ye çevrilir. Örnek: 100 veya 250. Reçeteyi değiştirmez.")
        self.fields["change_reason"] = forms.CharField(
            label="Değişiklik gerekçesi", required=False, widget=forms.Textarea(attrs={"rows": 2}),
            help_text="İlk revizyonda boş bırakılabilir; sonraki revizyonlarda onay için zorunludur.")
        if self.is_bound:
            raw = self.data.get("satir_sayisi", "")
            rows = int(raw) if raw.isdigit() else rows
        self.rows = max(1, min(rows, 30))
        materials = Material.objects.active().filter(unit="kg").order_by("code")
        initial_lines = list(initial_lines)
        for i in range(self.rows):
            m, p, n = initial_lines[i] if i < len(initial_lines) else (None, None, "")
            self.fields[f"material_{i}"] = forms.ModelChoiceField(
                label="Malzeme", queryset=materials, required=False, initial=m,
                help_text="Yalnız kg birimli aktif malzeme kartları listelenir.")
            self.fields[f"material_{i}"].label_from_instance = lambda x: f"{x.code} – {x.name} ({x.get_kind_display()})"
            self.fields[f"pct_{i}"] = TRDecimalField(label="Oran (%)", max_digits=6, decimal_places=3, required=False,
                                                     initial=p, help_text="Nihai karışımın yüzdesi.")
            self.fields[f"note_{i}"] = forms.CharField(label="Uygulama notu", required=False, max_length=200, initial=n)

    def line_rows(self):
        return [(self[f"material_{i}"], self[f"pct_{i}"], self[f"note_{i}"]) for i in range(self.rows)]

    def clean(self):
        data = super().clean()
        lines = []
        for i in range(self.rows):
            m, p, n = data.get(f"material_{i}"), data.get(f"pct_{i}"), (data.get(f"note_{i}") or "").strip()
            if not m and p is None and not n:
                continue
            if not m:
                self.add_error(f"material_{i}", "Malzeme seçin veya satırı boşaltın.")
                continue
            if p is not None and p < 0:
                self.add_error(f"pct_{i}", "Oran negatif olamaz.")
            lines.append((m, p, n))
        data["lines"] = lines
        data["spec"] = self.clean_spec_values()
        return data
