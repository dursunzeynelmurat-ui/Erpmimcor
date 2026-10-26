from django import forms
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache

from core.forms import IdemForm, IdemModelForm

from .models import User


class LoginForm(forms.Form):
    personnel_no = forms.CharField(label="Personel no", max_length=20,
                                   widget=forms.TextInput(attrs={"autocomplete": "username", "inputmode": "numeric", "autofocus": True}))
    password = forms.CharField(label="Parola", widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

    def __init__(self, request, *args, **kwargs):
        self.request = request
        self.user = None
        super().__init__(*args, **kwargs)

    def _lock_key(self, personnel_no):
        return f"login-fail:{personnel_no}"

    def clean(self):
        data = super().clean()
        no, pw = data.get("personnel_no"), data.get("password")
        if not no or not pw:
            raise forms.ValidationError("Personel no ve parola birlikte zorunludur.")
        key = self._lock_key(no.strip())
        failures = cache.get(key, 0)
        if failures >= settings.LOGIN_MAX_FAILURES:
            raise forms.ValidationError(
                f"Çok sayıda hatalı deneme. {settings.LOGIN_LOCK_MINUTES} dakika sonra tekrar deneyin."
            )
        user = authenticate(self.request, personnel_no=no.strip(), password=pw)
        if user is None:
            cache.set(key, failures + 1, settings.LOGIN_LOCK_MINUTES * 60)
            raise forms.ValidationError("Personel no veya parola hatalı.")
        cache.delete(key)
        self.user = user
        return data


class UserCreateForm(IdemModelForm):
    password = forms.CharField(label="İlk parola", widget=forms.PasswordInput, help_text="En az 8 karakter.")

    class Meta:
        model = User
        fields = ["personnel_no", "full_name", "role"]

    def clean_password(self):
        pw = self.cleaned_data["password"]
        validate_password(pw)
        return pw


class UserUpdateForm(IdemModelForm):
    new_password = forms.CharField(label="Yeni parola (değiştirmek için)", required=False, widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ["full_name", "role", "is_active"]

    def clean_new_password(self):
        pw = self.cleaned_data.get("new_password")
        if pw:
            validate_password(pw)
        return pw


class PasswordChangeForm(IdemForm):
    old_password = forms.CharField(label="Mevcut parola", widget=forms.PasswordInput)
    new_password = forms.CharField(label="Yeni parola", widget=forms.PasswordInput)

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_old_password(self):
        pw = self.cleaned_data["old_password"]
        if not self.user.check_password(pw):
            raise forms.ValidationError("Mevcut parola hatalı.")
        return pw

    def clean_new_password(self):
        pw = self.cleaned_data["new_password"]
        validate_password(pw, self.user)
        return pw
