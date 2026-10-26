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
