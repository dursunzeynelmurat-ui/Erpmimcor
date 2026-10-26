from decimal import Decimal

from django import forms
from django.urls import reverse
from django.utils import timezone

from catalog.models import Customer, Product, RecipeRevision, Unit
from core.forms import IdemForm

DEC = forms.NumberInput(attrs={"step": "any", "inputmode": "decimal"})
DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
NO_CUSTOMER = "Önce müşteri seçin."
NO_PRODUCTS = "Bu müşteriye ait aktif ürün bulunmuyor."
HAS_PRODUCTS = "Yalnız seçilen müşteriye ait aktif ürün kodları listelenir."


def products_for(customer):
    """Müşteriye bağlı aktif ürün kodları (form listesi ve ürün uç noktası aynı sorguyu kullanır)."""
    return Product.objects.active().filter(customer=customer).order_by("code")


class OrderCreateForm(IdemForm):
    customer = forms.ModelChoiceField(label="Müşteri", queryset=Customer.objects.active())
    customer_ref = forms.CharField(label="Müşteri sipariş ref.", required=False)
    order_date = forms.DateField(label="Sipariş tarihi", widget=DATE, initial=timezone.localdate)
    due_date = forms.DateField(label="Termin", widget=DATE, required=False)
    notes = forms.CharField(label="Not", widget=forms.Textarea(attrs={"rows": 2}), required=False)
    product = forms.ModelChoiceField(label="Ürün (ilk kalem)", queryset=Product.objects.none())
    quantity = forms.DecimalField(label="Miktar", max_digits=14, decimal_places=3, min_value=Decimal("0.001"), widget=DEC)
    unit = forms.ChoiceField(label="Birim", choices=Unit.choices, initial=Unit.BOX)

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
        return data


class LineAddForm(IdemForm):
    product = forms.ModelChoiceField(label="Ürün", queryset=Product.objects.none())
    quantity = forms.DecimalField(label="Miktar", max_digits=14, decimal_places=3, min_value=Decimal("0.001"), widget=DEC)
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

    def __init__(self, line, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = RecipeRevision.objects.filter(recipe__product=line.product).order_by("-rev_no")
        self.fields["revision"].queryset = qs
        self.fields["revision"].initial = qs.first()
        self.fields["revision"].label_from_instance = lambda r: (
            f"Rev.{r.rev_no} – {r.width_mm} mm / {r.thickness_micron} µ ({r.created_at:%d.%m.%Y})")


class LineChangeForm(IdemForm):
    quantity = forms.DecimalField(label="Yeni miktar", max_digits=14, decimal_places=3, min_value=Decimal("0.001"), widget=DEC)
    due_date = forms.DateField(label="Kalem termini", widget=DATE, required=False)
    reason = forms.CharField(label="Değişiklik gerekçesi", required=False, widget=forms.Textarea(attrs={"rows": 2}),
                             help_text="Onaylı siparişte zorunlu (ör. müşteri miktarı 500'den 400'e düşürdü).")
    confirm_impacts = forms.BooleanField(label="Etkilenen kayıtları gördüm; değişikliği kaydet", required=False)

    def __init__(self, line, *args, **kwargs):
        kwargs.setdefault("initial", {"quantity": line.quantity, "due_date": line.due_date})
        super().__init__(*args, **kwargs)
