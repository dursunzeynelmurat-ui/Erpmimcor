import uuid

from django import forms


class IdempotentFormMixin:
    """Her forma gizli, tek kullanımlık bir anahtar ekler (mükerrer gönderime karşı)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["idem_key"] = forms.CharField(widget=forms.HiddenInput, initial=uuid.uuid4().hex)

    @property
    def idem_key(self):
        return self.cleaned_data.get("idem_key") if hasattr(self, "cleaned_data") else None


class IdemForm(IdempotentFormMixin, forms.Form):
    pass


class IdemModelForm(IdempotentFormMixin, forms.ModelForm):
    pass


class ConfirmForm(IdemForm):
    """Yalnızca onay düğmesi olan eylemler için."""


class ReasonForm(IdemForm):
    reason = forms.CharField(label="Gerekçe", widget=forms.Textarea(attrs={"rows": 2}))


class TRDecimalInput(forms.TextInput):
    """Ondalık metin kutusu: değeri Türkçe virgülle gösterir, mobilde sayısal klavye açar."""

    def __init__(self, attrs=None):
        super().__init__({"inputmode": "decimal", "autocomplete": "off", **(attrs or {})})

    def format_value(self, value):
        from decimal import Decimal
        if isinstance(value, Decimal):
            text = format(value.normalize(), "f")
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            return text.replace(".", ",")
        return super().format_value(value)


def parse_tr_decimal(raw):
    """'12,5' ve '12.5' → '12.5'. Hem nokta hem virgül, birden çok ayırıcı veya binlik ayırıcı gibi duran nokta
    ('1.250') reddedilir; 12,5 asla 125 olmaz."""
    text = str(raw).strip().replace(" ", "").replace(" ", "")
    if not text:
        return text
    if "." in text and "," in text:
        raise forms.ValidationError("Ondalık için yalnız virgül kullanın (ör. 12,5); binlik ayırıcı yazmayın.",
                                    code="tr_decimal")
    sep = "," if "," in text else "." if "." in text else None
    if sep:
        if text.count(sep) > 1:
            raise forms.ValidationError("Sayıda yalnız bir ondalık virgülü olabilir (ör. 1250 veya 12,5).",
                                        code="tr_decimal")
        whole, frac = text.split(sep)
        if sep == "." and len(frac) == 3 and whole.lstrip("-").isdigit() and whole.lstrip("-") != "0":
            raise forms.ValidationError("Nokta binlik ayırıcı mı ondalık mı anlaşılmıyor. Ondalık için virgül "
                                        "kullanın (ör. 1,25) veya binlik ayırıcısız yazın (ör. 1250).",
                                        code="tr_decimal")
        text = text.replace(",", ".")
    return text


class TRDecimalField(forms.DecimalField):
    widget = TRDecimalInput

    def to_python(self, value):
        if isinstance(value, str):
            value = parse_tr_decimal(value)
        return super().to_python(value)

    def widget_attrs(self, widget):
        attrs = super().widget_attrs(widget)
        for key in ("step", "min", "max"):  # metin kutusunda geçersiz; sınırları sunucu doğrular
            attrs.pop(key, None)
        return attrs
