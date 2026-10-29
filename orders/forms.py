from decimal import Decimal

from django import forms
from django.urls import reverse
from django.utils import timezone

from catalog.models import Customer, Product, RecipeRevision, Unit

from .models import Charge, OrderPurpose
from core.forms import IdemForm, TRDecimalField
from core.templatetags.mimcor import qty

DEC = forms.NumberInput(attrs={"step": "any", "inputmode": "decimal"})
DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
NO_CUSTOMER = "Önce müşteri seçin."
NO_PRODUCTS = "Bu müşteriye ait aktif ürün bulunmuyor."
HAS_PRODUCTS = "Yalnız seçilen müşteriye ait aktif ürün kodları listelenir."


SPEC_PREFIX = "s_"


def _as_input(value):
    """Mevcut değeri form girdisi biçimine çevirir (ondalık: gereksiz sıfırsız, virgüllü)."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f").replace(".", ",")
    return str(value)


def add_spec_request_fields(form, revision, with_toggle=True):
    """Satışın teknik talep alanları: izinli teknik alanlar (karışım ve iç notlar yok), mevcut revizyonun
    değerleriyle. Döner: forma eklenen alan adları."""
    from catalog import spec
    from catalog.forms import spec_form_field
    names = []
    if with_toggle:
        form.fields["want_change"] = forms.BooleanField(
            label="Farklı teknik özellik istiyorum", required=False,
            help_text="Ürün ve reçete değişmez; istek planlamaya gider. Planlama yeni revizyon veya yeni ürün kodu "
                      "kararı verene kadar kalem teknik onay alamaz ve üretime çıkmaz.")
        names.append("want_change")
    form.fields["change_reason"] = forms.CharField(
        label="Talebin gerekçesi", required=False, widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Örnek: müşteri 650 mm en ve mavi renk istedi (e-posta 30.10.2026).")
    names.append("change_reason")
    for f in spec.FIELDS:
        if f.name not in spec.CHANGEABLE:
            continue
        field = spec_form_field(f)
        field.initial = getattr(revision, f.name) if revision else None
        form.fields[SPEC_PREFIX + f.name] = field
        names.append(SPEC_PREFIX + f.name)
    return names


def clean_spec_request(form, data, product_or_line):
    """İstenen değerlerden mevcut revizyondan farklı olanları döner; gerekçe ve en az bir fark zorunlu."""
    from catalog import spec
    from core.exceptions import BusinessRuleError
    from .repeat import requested_changes
    # Yalnız gönderilen alanlar talep sayılır; formda bulunmayan (gönderilmemiş) alan "silindi" anlamına gelmez.
    requested = {n: data.get(SPEC_PREFIX + n) for n in spec.CHANGEABLE if form.add_prefix(SPEC_PREFIX + n) in form.data}
    try:
        _rev, changed = requested_changes(product_or_line, requested)
    except BusinessRuleError as exc:
        form.add_error(None, exc.message)
        return {}
    if not changed:
        form.add_error("want_change" if "want_change" in form.fields else None,
                       "Farklı teknik özellik işaretli ama hiçbir değer değişmedi; farklı bir değer girin veya "
                       "işareti kaldırın.")
    if not (data.get("change_reason") or "").strip():
        form.add_error("change_reason", "Teknik talebin gerekçesini yazın (ör. müşteri 650 mm istedi).")
    return {k: new for k, (_old, new) in changed.items()}


class LineSpecRequestForm(IdemForm):
    """Taslak veya planlama bekleyen kalem için satışın teknik talebi."""

    def __init__(self, line, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.line = line
        self.spec_names = add_spec_request_fields(self, line.approved_revision or line.product.current_revision,
                                                  with_toggle=False)
        self.spec_changes = {}

    def clean(self):
        data = super().clean()
        self.spec_changes = clean_spec_request(self, data, self.line)
        return data


def products_for(customer):
    """Müşteriye bağlı aktif ürün kodları (form listesi ve ürün uç noktası aynı sorguyu kullanır)."""
    return Product.objects.active().filter(customer=customer).order_by("code")


class OrderCreateForm(IdemForm):
    """3 adımlı sipariş girişi: 1) müşteri ve ürün, 2) özellikler ve miktar, 3) teslimat ve kontrol.
    Önceki adımların değerleri gizli alanlarda taşınır ve her adımda sunucuda yeniden doğrulanır."""

    STEPS = {1: ("Müşteri ve ürün", ["customer", "product"]),
             2: ("Özellikler ve miktar", ["quantity", "unit", "due_date", "purpose", "charge", "customer_ref",
                                          "order_date", "notes"]),
             3: ("Teslimat ve kontrol", ["delivery_confirmed"])}

    customer = forms.ModelChoiceField(label="Müşteri", queryset=Customer.objects.active(),
                                      help_text="Yalnız erişiminiz olan müşteriler listelenir.")
    customer_ref = forms.CharField(label="Müşteri sipariş ref.", required=False,
                                   help_text="Müşterinin sipariş/PO numarası. Bazı müşterilerde zorunludur.")
    order_date = forms.DateField(label="Sipariş tarihi", widget=DATE, initial=timezone.localdate)
    due_date = forms.DateField(label="Termin (teslim tarihi)", widget=DATE, required=False,
                               help_text="Müşterinin istediği teslim tarihi. Bilinmiyorsa boş bırakın.")
    notes = forms.CharField(label="Not", widget=forms.Textarea(attrs={"rows": 2}), required=False)
    product = forms.ModelChoiceField(label="Ürün (ilk kalem)", queryset=Product.objects.none())
    quantity = TRDecimalField(label="Miktar", max_digits=14, decimal_places=3, min_value=Decimal("0.001"),
                              help_text="Ondalık için virgül kullanın (ör. 12,5). Kutu biriminde tam sayı girin.")
    unit = forms.ChoiceField(label="Birim", choices=Unit.choices, initial=Unit.BOX,
                             help_text="Rezervasyon ve sevkiyat yalnız kutu biriminde yapılabilir.")
    purpose = forms.ChoiceField(label="Sipariş amacı", choices=[("", "Seçin")] + list(OrderPurpose.choices),
                                help_text="Numune ve deneme de gerçek üretim ve teslimdir; stok ve izlenebilirlik "
                                          "kuralları aynen uygulanır.")
    charge = forms.ChoiceField(label="Ücret", required=False, choices=[("", "Belirtilmemiş")] + list(Charge.choices),
                               help_text="Amaçtan ayrı bilgidir (ör. ücretli numune). Fatura oluşturmaz.")
    delivery_confirmed = forms.BooleanField(
        label="Teslimat adresi ve koşullarını kontrol ettim", required=False,
        help_text="Bilgiler müşteri kartından alınır. Farklıysa önce müşteri kartını güncelleyin.")

    def __init__(self, *args, user=None, **kwargs):
        from accounts.permissions import scoped
        super().__init__(*args, **kwargs)
        self._customers = scoped(Customer.objects.active(), user, "order.create") if user else Customer.objects.none()
        self.fields["customer"].queryset = self._customers
        customer = self._selected_customer()
        field = self.fields["product"]
        # Ürün listesi sunucuda seçilen müşteriye göre kurulur; tarayıcıdaki filtre yalnız kolaylıktır.
        field.queryset = products_for(customer) if customer else Product.objects.none()
        field.widget.attrs.update({
            "data-customer-field": "customer",
            "data-products-url": reverse("orders:customer_products", args=[0]).replace("/0/", "/__ID__/"),
            "data-msg-no-customer": NO_CUSTOMER,
            "data-msg-empty": NO_PRODUCTS,
            "data-msg-ready": HAS_PRODUCTS,
            "data-msg-loading": "Ürünler yükleniyor…",
            "data-msg-error": "Ürün listesi yüklenemedi. Bağlantınızı kontrol edip tekrar deneyin.",
        })
        field.widget.attrs["data-loaded-customer"] = str(customer.pk) if customer else ""
        if customer is None:
            field.widget.attrs["disabled"] = "disabled"  # Yalnız HTML; Django alanı devre dışı değil.
            field.help_text = NO_CUSTOMER
        elif not field.queryset.exists():
            field.widget.attrs["disabled"] = "disabled"
            field.help_text = NO_PRODUCTS
        else:
            field.help_text = HAS_PRODUCTS
        # Farklı teknik özellik talebi (2. adım): izinli alanlar ürünün mevcut değerleriyle dolu gelir.
        product = self.selected_product() if self.is_bound else None
        self.spec_names = add_spec_request_fields(self, product.current_revision if product else None)
        self.STEPS = {**self.STEPS, 2: (self.STEPS[2][0], self.STEPS[2][1] + self.spec_names)}
        self.spec_changes = {}
        if self.is_bound and product and (self.data.get("adim") == "1"
                                          or not any(k.startswith(SPEC_PREFIX) for k in self.data)):
            # 1. adımdan gelişte (ürün yeni seçilmiş/değişmiş olabilir) talep alanları ürünün mevcut değerleriyle
            # doldurulur; form bağlı olduğu için initial kendiliğinden kullanılmaz.
            data = self.data.copy()
            for name in self.spec_names:
                if name.startswith(SPEC_PREFIX):
                    data[name] = _as_input(self.fields[name].initial)
            self.data = data

    def _selected_customer(self):
        value = self.data.get(self.add_prefix("customer")) if self.is_bound else self.initial.get("customer")
        if not value:
            return None
        try:
            return self._customers.get(pk=getattr(value, "pk", value))
        except (Customer.DoesNotExist, ValueError, TypeError):
            return None

    def clean(self):
        data = super().clean()
        customer, product = data.get("customer"), data.get("product")
        if customer and product and (product.customer_id != customer.pk or not product.is_active):
            self.add_error("product", "Bu ürün seçilen müşteriye ait aktif bir ürün değil.")
        qty_value, unit = data.get("quantity"), data.get("unit")
        if qty_value is not None and unit == Unit.BOX and qty_value != qty_value.to_integral_value():
            self.add_error("quantity", f"Kutu biriminde miktar tam sayı olmalı ({qty(qty_value)} girildi). Örnek: 12.")
        if self.final and not data.get("delivery_confirmed"):
            self.add_error("delivery_confirmed", "Kaydetmeden önce teslimat bilgilerini kontrol edip işaretleyin.")
        if product and data.get("want_change"):
            self.spec_changes = clean_spec_request(self, data, product)
        return data

    @property
    def final(self):
        return self.is_bound and self.data.get("git", "kaydet") == "kaydet"

    def step_errors(self, upto):
        names = [n for k, (_, fields) in self.STEPS.items() if k <= upto for n in fields]
        return {n: self.errors[n] for n in names if n in self.errors}

    def first_error_step(self):
        for k, (_, fields) in self.STEPS.items():
            if any(n in self.errors for n in fields):
                return k
        return 3

    def selected_product(self):
        customer = self._selected_customer()
        value = self.data.get(self.add_prefix("product")) if self.is_bound else None
        if not customer or not value:
            return None
        return products_for(customer).filter(pk=value).first() if str(value).isdigit() else None


class LineAddForm(IdemForm):
    product = forms.ModelChoiceField(label="Ürün", queryset=Product.objects.none())
    quantity = TRDecimalField(label="Miktar", max_digits=14, decimal_places=3, min_value=Decimal("0.001"))
    unit = forms.ChoiceField(label="Birim", choices=Unit.choices, initial=Unit.BOX)
    due_date = forms.DateField(label="Kalem termini", widget=DATE, required=False)

    def __init__(self, order, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["product"]
        field.queryset = products_for(order.customer)
        if not field.queryset.exists():
            field.widget.attrs["disabled"] = "disabled"
            field.help_text = NO_PRODUCTS
        else:
            field.help_text = f"Yalnız {order.customer.name} müşterisine ait aktif ürün kodları listelenir."


class ApproveLineForm(IdemForm):
    revision = forms.ModelChoiceField(label="Onaylanan reçete revizyonu", queryset=RecipeRevision.objects.none())
    note = forms.CharField(label="Planlama notu", widget=forms.Textarea(attrs={"rows": 2}), required=False)

    deviation_confirmed = forms.BooleanField(
        label="Beklenen revizyondan farklı revizyon seçtim; farkları kontrol ettim", required=False)
    deviation_reason = forms.CharField(
        label="Farklı revizyon gerekçesi", required=False, widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Yalnız beklenen revizyon dışında bir revizyon seçerken zorunlu (ör. müşteri yeni ölçüyü yazılı "
                  "kabul etti).")

    def __init__(self, line, *args, **kwargs):
        from .repeat import expected_revision
        super().__init__(*args, **kwargs)
        qs = RecipeRevision.objects.filter(recipe__product=line.product).order_by("-rev_no")
        self.expected = expected_revision(line)
        self.fields["revision"].queryset = qs
        self.fields["revision"].initial = self.expected or line.product.current_revision or qs.first()
        self.fields["revision"].label_from_instance = lambda r: (
            f"Rev.{r.rev_no} – {r.width_mm} mm / {r.thickness_micron} µ ({r.created_at:%d.%m.%Y})"
            + (" · beklenen" if self.expected and r.pk == self.expected.pk else ""))

    def clean(self):
        data = super().clean()
        rev = data.get("revision")
        if self.expected and rev and rev.pk != self.expected.pk:
            if not data.get("deviation_confirmed"):
                self.add_error("deviation_confirmed", f"Beklenen revizyon Rev.{self.expected.rev_no}. Rev.{rev.rev_no} "
                                                      "ile onaylamak için farkları kontrol ettiğinizi işaretleyin.")
            if not (data.get("deviation_reason") or "").strip():
                self.add_error("deviation_reason", "Farklı revizyon için gerekçe yazın.")
        return data


class LineChangeForm(IdemForm):
    quantity = TRDecimalField(label="Yeni miktar", max_digits=14, decimal_places=3, min_value=Decimal("0.001"))
    due_date = forms.DateField(label="Kalem termini", widget=DATE, required=False)
    reason = forms.CharField(label="Değişiklik gerekçesi", required=False, widget=forms.Textarea(attrs={"rows": 2}),
                             help_text="Onaylı siparişte zorunlu (ör. müşteri miktarı 500'den 400'e düşürdü).")
    confirm_impacts = forms.BooleanField(label="Etkilenen kayıtları gördüm; değişikliği kaydet", required=False)

    def __init__(self, line, *args, **kwargs):
        kwargs.setdefault("initial", {"quantity": line.quantity, "due_date": line.due_date})
        super().__init__(*args, **kwargs)


class CompatibilityForm(IdemForm):
    revision = forms.ModelChoiceField(label="Eşdeğer kabul edilecek kaynak revizyon", queryset=RecipeRevision.objects.none())
    reason = forms.CharField(label="Gerekçe", widget=forms.Textarea(attrs={"rows": 2}),
                             help_text="Ör. 'Müşteri %3 katkılı stoğu yazılı olarak kabul etti'. Onay yalnız bu kalem ve "
                                       "kalemin şu anki onaylı revizyonu için geçerlidir.")

    def __init__(self, line, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["revision"].queryset = RecipeRevision.objects.filter(recipe__product=line.product)
        self.fields["revision"].label_from_instance = lambda r: (
            f"Rev.{r.rev_no} · {qty(r.width_mm)} mm · {qty(r.thickness_micron)} µ · {r.color or '—'}")


class OverDeliveryForm(IdemForm):
    extra = forms.IntegerField(label="Geçerli ek miktar (kutu)", min_value=0,
                               help_text="Kalem miktarının üzerine izin verilen toplam. 0 = izni kaldır. Sevk edilmiş + "
                                         "ayrılmış miktarın altına indirilemez.")
    reason = forms.CharField(label="Gerekçe", widget=forms.Textarea(attrs={"rows": 2}))


class RepeatOrderForm(IdemForm):
    """Repete sihirbazı: 2) kalem seçimi + miktar/termin (+ isteğe bağlı değişiklik), 3) teslimat ve kontrol.
    Bilinen bilgiler yeniden yazdırılmaz: teknik değerler kaynak revizyondan hazır gelir."""

    STEP_ORDER_FIELDS = ["purpose", "charge", "customer_ref", "due_date", "notes", "delivery_confirmed"]

    purpose = OrderCreateForm.base_fields["purpose"]
    charge = OrderCreateForm.base_fields["charge"]
    customer_ref = OrderCreateForm.base_fields["customer_ref"]
    due_date = forms.DateField(label="Sipariş termini (genel)", widget=DATE, required=False,
                               help_text="Kalemde ayrı termin yoksa bu kullanılır. Eski siparişin tarihi taşınmaz.")
    notes = OrderCreateForm.base_fields["notes"]
    delivery_confirmed = OrderCreateForm.base_fields["delivery_confirmed"]

    def __init__(self, *args, candidates=(), **kwargs):
        import copy
        from catalog import spec
        from catalog.forms import spec_form_field
        from .repeat import base_values, source_warnings
        super().__init__(*args, **kwargs)
        for name in ("purpose", "charge", "customer_ref", "notes", "delivery_confirmed"):
            self.fields[name] = copy.deepcopy(self.fields[name])
        self.items = []
        for ln in candidates:
            p = ln.pk
            warnings = source_warnings(ln)
            blocked = not ln.product.is_active
            self.fields[f"sel_{p}"] = forms.BooleanField(label="Bu kalemi tekrarla", required=False,
                                                         disabled=blocked)
            self.fields[f"qty_{p}"] = TRDecimalField(label=f"Yeni miktar ({ln.get_unit_display()})", max_digits=14,
                                                     decimal_places=3, required=False, min_value=Decimal("0.001"),
                                                     help_text=f"Önceki miktar: {qty(ln.quantity)}. Yeni miktarı yazın.")
            self.fields[f"due_{p}"] = forms.DateField(label="Kalem termini", widget=DATE, required=False)
            self.fields[f"chg_{p}"] = forms.BooleanField(
                label="Teknik değişiklik istiyorum (revizyonlu repete)", required=False,
                help_text="İşaretlemezseniz aynı teknik özellikle tekrar edilir.")
            self.fields[f"reason_{p}"] = forms.CharField(label="Değişiklik gerekçesi", required=False,
                                                         widget=forms.Textarea(attrs={"rows": 2}))
            base = base_values(ln)
            names = []
            for f in spec.FIELDS:
                if f.name not in spec.CHANGEABLE:
                    continue
                field = spec_form_field(f)
                field.initial = base.get(f.name)
                self.fields[f"s{p}_{f.name}"] = field
                names.append(f.name)
            self.items.append({"line": ln, "warnings": warnings, "blocked": blocked, "spec_names": names})

    def item_fields(self, item):
        p = item["line"].pk
        return {k: self[f"{k}_{p}"] for k in ("sel", "qty", "due", "chg", "reason")}

    def item_spec(self, item):
        p = item["line"].pk
        return [self[f"s{p}_{n}"] for n in item["spec_names"]]

    def clean(self):
        data = super().clean()
        chosen = []
        for item in self.items:
            p = item["line"].pk
            if not data.get(f"sel_{p}"):
                continue
            if data.get(f"qty_{p}") is None:
                self.add_error(f"qty_{p}", "Tekrar edilen kalem için yeni miktarı yazın.")
            changes = {}
            if data.get(f"chg_{p}"):
                from .repeat import base_values, diff_values
                requested = {n: data.get(f"s{p}_{n}") for n in item["spec_names"]}
                changes = {k: new for k, (_old, new) in diff_values(base_values(item["line"]), requested).items()}
                if not changes:
                    self.add_error(f"chg_{p}", "Değişiklik işaretli ama hiçbir teknik değer değişmedi. Değeri "
                                               "değiştirin veya işareti kaldırın.")
                if not (data.get(f"reason_{p}") or "").strip():
                    self.add_error(f"reason_{p}", "Değişikliğin gerekçesini yazın (ör. müşteri boyu 500 mm istedi).")
            chosen.append({"source_line": item["line"], "quantity": data.get(f"qty_{p}"),
                           "due_date": data.get(f"due_{p}"), "changes": changes,
                           "reason": (data.get(f"reason_{p}") or "").strip(), "item": item})
        if not chosen and self.is_bound:
            self.add_error(None, "Tekrar edilecek en az bir kalem işaretleyin.")
        data["chosen"] = chosen
        return data

    def line_field_names(self):
        return [n for n in self.fields if n not in self.STEP_ORDER_FIELDS and n != "idem_key"]
