from decimal import Decimal

from django import forms

from catalog.models import Machine, MaterialLot, Product, RecipeRevision, Unit
from core.forms import IdemForm
from core.templatetags.mimcor import qty
from core.models import QualityStatus, ValueSource

from .models import Coil, CoilStatus

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


class CoilForm(IdemForm):
    """Bobin kaydı. Dönüştürme/bölme operasyonlarında en az bir kaynak bobin ve tüketilen miktar girilir."""

    SOURCE_ROWS = 3
    weight_kg = forms.DecimalField(label="Bobin ağırlığı (kg)", max_digits=12, decimal_places=3,
                                   min_value=Decimal("0.001"), widget=DEC)
    weight_source = forms.ChoiceField(label="Ağırlık kaynağı", choices=ValueSource.choices[:1] + ValueSource.choices[2:],
                                      initial=ValueSource.MEASURED)
    length_m = forms.DecimalField(label="Uzunluk (m)", max_digits=10, decimal_places=2, required=False, widget=DEC)
    notes = forms.CharField(label="Not", required=False)

    def __init__(self, op, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.needs_source = op.seq > 1
        if self.needs_source:
            sources = Coil.objects.filter(work_order=op.work_order, operation__seq__lt=op.seq, remaining_kg__gt=0) \
                .exclude(quality_status=QualityStatus.BLOCKED)
            for i in range(1, self.SOURCE_ROWS + 1):
                self.fields[f"source_{i}"] = forms.ModelChoiceField(label=f"Kaynak bobin {i}", queryset=sources,
                                                                    required=(i == 1))
                self.fields[f"source_{i}"].label_from_instance = lambda c: f"{c.code} (kalan {qty(c.remaining_kg)} kg)"
                self.fields[f"consumed_{i}"] = forms.DecimalField(label=f"Tüketilen {i} (kg)", max_digits=12,
                                                                  decimal_places=3, required=(i == 1),
                                                                  min_value=Decimal("0.001"), widget=DEC)

    def source_rows(self):
        if not self.needs_source:
            return []
        return [(self[f"source_{i}"], self[f"consumed_{i}"]) for i in range(1, self.SOURCE_ROWS + 1)]

    def clean(self):
        data = super().clean()
        sources = []
        if self.needs_source:
            for i in range(1, self.SOURCE_ROWS + 1):
                c, q = data.get(f"source_{i}"), data.get(f"consumed_{i}")
                if c and any(c.pk == s.pk for s, _ in sources):
                    self.add_error(f"source_{i}", "Aynı kaynak bobin birden fazla satırda seçilemez.")
                elif c and q:
                    sources.append((c, q))
                elif c or q:
                    self.add_error(f"consumed_{i}", "Kaynak bobin ve tüketilen miktar birlikte girilmeli.")
        data["sources"] = sources
        return data


class MaterialMoveForm(IdemForm):
    lot = forms.ModelChoiceField(label="Malzeme lotu", queryset=MaterialLot.objects.none())
    quantity = forms.DecimalField(label="Miktar (kg)", max_digits=14, decimal_places=3, min_value=Decimal("0.001"), widget=DEC)

    def __init__(self, *args, lots=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lot"].queryset = lots if lots is not None else MaterialLot.objects.filter(quantity__gt=0).select_related("material")
        self.fields["lot"].label_from_instance = lambda lot: f"{lot.code} – {lot.material.code} (mevcut {qty(lot.quantity)} kg)"


class PalletFromProductionForm(IdemForm):
    box_count = forms.IntegerField(label="Kutu adedi", min_value=1, widget=forms.NumberInput(attrs={"inputmode": "numeric"}))
    coils = forms.ModelMultipleChoiceField(label="Palete alınan bobinler/rulolar", queryset=Coil.objects.none(),
                                           required=True, widget=forms.CheckboxSelectMultiple,
                                           error_messages={"required": "En az bir bobin/rulo seçilmelidir."})
    target_code = forms.CharField(label="Var olan palete ekle (palet no)", required=False,
                                  help_text="Boş bırakılırsa yeni palet açılır. Palet aynı ürün koduna ait olmalı.")

    def __init__(self, wo, *args, **kwargs):
        super().__init__(*args, **kwargs)
        last = wo.operations.order_by("-seq").first()
        self.fields["coils"].queryset = Coil.objects.filter(work_order=wo, operation=last, status=CoilStatus.AVAILABLE) \
            .exclude(quality_status=QualityStatus.BLOCKED)
        self.fields["coils"].label_from_instance = lambda c: f"{c.code} – {qty(c.weight_kg)} kg"

    def clean_target_code(self):
        from warehouse.models import Pallet
        code = self.cleaned_data.get("target_code", "").strip().upper()
        if not code:
            return None
        pallet = Pallet.objects.filter(code=code).first()
        if pallet is None:
            raise forms.ValidationError("Bu numarada palet yok.")
        return pallet


class ClosureForm(IdemForm):
    scrap_kg = forms.DecimalField(label="Fire (kg)", max_digits=12, decimal_places=3, min_value=Decimal("0"), widget=DEC,
                                  help_text="Fire yoksa 0 yazın.")
    remaining_confirmed = forms.BooleanField(label="Makine başında kalan malzeme lota iade edildi veya kalmadı", required=False)
    reason = forms.CharField(label="Gerekçe (denge farkı varsa zorunlu)", required=False,
                             widget=forms.Textarea(attrs={"rows": 2}))


class CoilQualityForm(IdemForm):
    quality_status = forms.ChoiceField(label="Kalite durumu", choices=QualityStatus.choices)
    reason = forms.CharField(label="Gerekçe", widget=forms.Textarea(attrs={"rows": 2}))
