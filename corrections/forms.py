from decimal import Decimal

from django import forms

from core.forms import IdemForm


class CorrectionForm(IdemForm):
    new_value = forms.DecimalField(label="Doğru değer", max_digits=14, decimal_places=3, min_value=Decimal("0"),
                                   widget=forms.NumberInput(attrs={"step": "any", "inputmode": "decimal"}))
    reason = forms.CharField(label="Gerekçe", widget=forms.Textarea(attrs={"rows": 2}),
                             help_text="Ör. 'Terazi yanlış okundu, yeniden tartıldı: 64,5 kg'.")
