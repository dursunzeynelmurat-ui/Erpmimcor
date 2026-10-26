from django import forms
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache

from core.forms import IdemForm, IdemModelForm

from .catalog import Kind, Scope, grouped
from .models import AccessRole, Shift, User


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


class UserCreateForm(IdemForm):
    personnel_no = forms.CharField(label="Personel no", max_length=20)
    full_name = forms.CharField(label="Ad soyad", max_length=120)
    team = forms.CharField(label="Ekip", max_length=60, required=False)
    shift = forms.ModelChoiceField(label="Vardiya", queryset=Shift.objects.filter(is_active=True), required=False)
    roles = forms.ModelMultipleChoiceField(label="Roller", queryset=AccessRole.objects.none(), required=False,
                                           widget=forms.CheckboxSelectMultiple)
    password = forms.CharField(label="İlk parola", widget=forms.PasswordInput, help_text="En az 8 karakter.")
    reason = forms.CharField(label="Not / gerekçe", required=False)

    def __init__(self, *args, assignable=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["roles"].queryset = assignable if assignable is not None else AccessRole.objects.none()

    def clean_personnel_no(self):
        no = self.cleaned_data["personnel_no"].strip()
        if User.objects.filter(personnel_no=no).exists():
            raise forms.ValidationError("Bu personel numarası kullanılıyor.")
        return no

    def clean_password(self):
        pw = self.cleaned_data["password"]
        validate_password(pw)
        return pw


class UserUpdateForm(IdemModelForm):
    new_password = forms.CharField(label="Yeni parola (değiştirmek için)", required=False, widget=forms.PasswordInput)
    machines = forms.ModelMultipleChoiceField(label="Çalıştığı makineler", queryset=None, required=False,
                                              widget=forms.CheckboxSelectMultiple,
                                              help_text="Makine kapsamlı yetkiler (operatör) bu listeye göre uygulanır.")
    reason = forms.CharField(label="Gerekçe (hesap kapatmada zorunlu)", required=False)

    class Meta:
        model = User
        fields = ["full_name", "team", "shift", "is_active"]

    def __init__(self, *args, **kwargs):
        from catalog.models import Machine
        super().__init__(*args, **kwargs)
        self.fields["shift"].queryset = Shift.objects.filter(is_active=True)
        self.fields["machines"].queryset = Machine.objects.active()
        if self.instance.pk:
            self.fields["machines"].initial = [m.machine_id for m in self.instance.machine_links.all()]

    def clean_new_password(self):
        pw = self.cleaned_data.get("new_password")
        if pw:
            validate_password(pw)
        return pw


class RoleGrantForm(IdemForm):
    role = forms.ModelChoiceField(label="Atanacak rol", queryset=AccessRole.objects.none())
    reason = forms.CharField(label="Gerekçe", required=False)

    def __init__(self, *args, assignable=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].queryset = assignable if assignable is not None else AccessRole.objects.none()


class RoleForm(IdemForm):
    """Rol adı + desteklenen her işlem için kapsam seçimi ("" = yetki yok)."""

    code = forms.SlugField(label="Rol kodu", max_length=40, help_text="Küçük harf, rakam, tire/alt çizgi.")
    name = forms.CharField(label="Rol adı", max_length=80)
    description = forms.CharField(label="Açıklama", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    hr_assignable = forms.BooleanField(label="İK atayabilir", required=False)
    reason = forms.CharField(label="Değişiklik gerekçesi", required=False)

    def __init__(self, *args, role=None, admin=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.groups = []
        current = dict(role.permissions.values_list("action", "scope")) if role else {}
        if role:
            self.fields["code"].disabled = True
            self.fields["code"].initial = role.code
            self.fields["name"].initial = role.name
            self.fields["description"].initial = role.description
            self.fields["hr_assignable"].initial = role.hr_assignable
        if not admin:
            del self.fields["hr_assignable"]
        for group, actions in grouped().items():
            names = []
            for a in actions:
                if a.admin_only and not admin:
                    continue
                name = "perm__" + a.code.replace(".", "__")
                self.fields[name] = forms.ChoiceField(
                    label=a.label, required=False, initial=current.get(a.code, ""),
                    choices=[("", "Yok")] + [(s, Scope.LABELS[s]) for s in a.scopes],
                    help_text=f"{Kind.LABELS[a.kind]} · {a.code}")
                names.append(name)
            if names:
                self.groups.append((group, names))

    def group_fields(self):
        return [(g, [self[n] for n in names]) for g, names in self.groups]

    def perms(self):
        return {n[len("perm__"):].replace("__", "."): v for n, v in self.cleaned_data.items()
                if n.startswith("perm__") and v}


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


class ShiftForm(IdemModelForm):
    class Meta:
        model = Shift
        fields = ["code", "name", "start_time", "end_time", "is_active"]
        widgets = {"start_time": forms.TimeInput(attrs={"type": "time"}),
                   "end_time": forms.TimeInput(attrs={"type": "time"})}
