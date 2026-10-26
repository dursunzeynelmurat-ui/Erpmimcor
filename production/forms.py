from decimal import Decimal

from django import forms

from catalog.models import Machine, Product, RecipeRevision, Unit
from core.forms import IdemForm

DEC = forms.NumberInput(attrs={"step": "any", "inputmode": "decimal"})
DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
MAX_OPERATIONS = 4


class WorkOrderForm(IdemForm):
    """Operasyon sırası: 1. satır extruder, sonraki satırlar sırayla matbaa/perfore/dilimleme vb."""

    target_quantity = forms.DecimalField(label="Hedef miktar", max_digits=14, decimal_places=3,
                                         min_value=Decimal("0.001"), widget=DEC)
    unit = forms.ChoiceField(label="Birim", choices=Unit.choices, initial=Unit.BOX)
    planned_start = forms.DateField(label="Planlanan başlangıç", widget=DATE, required=False)
    notes = forms.CharField(label="Not", widget=forms.Textarea(attrs={"rows": 2}), required=False)

    def __init__(self, *args, stock=False, **kwargs):
        super().__init__(*args, **kwargs)
        if stock:
            self.fields["product"] = forms.ModelChoiceField(
                label="Ürün (müşteri stoğu)", queryset=Product.objects.active().select_related("customer"))
            self.fields["revision"] = forms.ModelChoiceField(
                label="Reçete revizyonu", queryset=RecipeRevision.objects.select_related("recipe__product"),
                help_text="Seçilen ürüne ait revizyon olmalı.")
            self.fields["revision"].label_from_instance = lambda r: f"{r.recipe.product.code} Rev.{r.rev_no}"
            self.order_fields(["product", "revision"])
        machines = Machine.objects.active()
        for i in range(1, MAX_OPERATIONS + 1):
            self.fields[f"machine_{i}"] = forms.ModelChoiceField(
                label=f"{i}. operasyon makinesi", queryset=machines, required=(i == 1),
                help_text="İlk operasyon extruder olmalı." if i == 1 else "")

    def machine_fields(self):
        return [self[f"machine_{i}"] for i in range(1, MAX_OPERATIONS + 1)]

    def clean(self):
        data = super().clean()
        machines, gap = [], False
        for i in range(1, MAX_OPERATIONS + 1):
            m = data.get(f"machine_{i}")
            if m and gap:
                self.add_error(f"machine_{i}", "Operasyon sırasında boşluk bırakılamaz.")
            if m:
                machines.append(m)
            else:
                gap = True
        data["machines"] = machines
        return data
