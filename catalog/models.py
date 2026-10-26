from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

QTY = {"max_digits": 14, "decimal_places": 3}
KG = {"max_digits": 12, "decimal_places": 3}


class ActiveQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)


class Customer(models.Model):
    code = models.CharField("Müşteri kodu", max_length=20, unique=True)
    name = models.CharField("Ünvan", max_length=200)
    contact = models.CharField("İlgili kişi / telefon", max_length=200, blank=True)
    is_active = models.BooleanField("Aktif", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ActiveQuerySet.as_manager()

    class Meta:
        ordering = ["code"]
        verbose_name = "Müşteri"
        verbose_name_plural = "Müşteriler"

    def __str__(self):
        return f"{self.code} – {self.name}"


class Unit(models.TextChoices):
    BOX = "kutu", "Kutu"
    KG = "kg", "kg"
    PIECE = "adet", "Adet"
    ROLL = "rulo", "Rulo"


class Product(models.Model):
    """Ürün kodu. Her kod tek müşteriye aittir; teknik olarak aynı ürün başka müşteride farklı koddur."""

    code = models.CharField("Ürün kodu", max_length=40, unique=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="products", verbose_name="Müşteri")
    name = models.CharField("Ürün adı", max_length=200)
    unit = models.CharField("Satış birimi", max_length=10, choices=Unit.choices, default=Unit.BOX)
    units_per_box = models.PositiveIntegerField("Kutu içi adet/rulo", null=True, blank=True)
    notes = models.TextField("Açıklama", blank=True)
    is_active = models.BooleanField("Aktif", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ActiveQuerySet.as_manager()

    class Meta:
        ordering = ["customer__code", "code"]
        verbose_name = "Ürün"
        verbose_name_plural = "Ürünler"

    def __str__(self):
        return f"{self.code} – {self.name}"

    @property
    def current_revision(self):
        return RecipeRevision.objects.filter(recipe__product=self).order_by("-rev_no").first()


class MachineType(models.TextChoices):
    EXTRUDER = "extruder", "Extruder"
    PRINT = "matbaa", "Matbaa"
    PERFORATION = "perfore", "Perfore"
    SLITTING = "dilimleme", "Dilimleme"


class Machine(models.Model):
    code = models.CharField("Makine kodu", max_length=20, unique=True)
    name = models.CharField("Makine adı", max_length=120)
    machine_type = models.CharField("Makine türü", max_length=20, choices=MachineType.choices)
    is_active = models.BooleanField("Aktif", default=True)

    objects = ActiveQuerySet.as_manager()

    class Meta:
        ordering = ["machine_type", "code"]
        verbose_name = "Makine"
        verbose_name_plural = "Makineler"

    def __str__(self):
        return f"{self.code} – {self.name} ({self.get_machine_type_display()})"


class MaterialKind(models.TextChoices):
    RAW = "hammadde", "Hammadde"
    ADDITIVE = "katki", "Katkı"


class Material(models.Model):
    """Hammadde/katkı tanımı (fiziksel lot değil)."""

    code = models.CharField("Malzeme kodu", max_length=30, unique=True)
    name = models.CharField("Malzeme adı", max_length=200)
    kind = models.CharField("Tür", max_length=20, choices=MaterialKind.choices)
    unit = models.CharField("Birim", max_length=10, default="kg")
    is_active = models.BooleanField("Aktif", default=True)

    objects = ActiveQuerySet.as_manager()

    class Meta:
        ordering = ["kind", "code"]
        verbose_name = "Malzeme"
        verbose_name_plural = "Malzemeler"

    def __str__(self):
        return f"{self.code} – {self.name}"


class MaterialLot(models.Model):
    """Fiziksel malzeme lotu. Miktar yalnızca LotMovement servisleriyle değişir."""

    code = models.CharField("Lot no", max_length=30, unique=True)
    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="lots", verbose_name="Malzeme")
    supplier_lot = models.CharField("Tedarikçi lot no", max_length=60, blank=True)
    quantity = models.DecimalField("Mevcut miktar", default=Decimal("0"), editable=False, **QTY)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]
        verbose_name = "Malzeme lotu"
        verbose_name_plural = "Malzeme lotları"
        constraints = [models.CheckConstraint(condition=Q(quantity__gte=0), name="lot_quantity_non_negative")]

    def __str__(self):
        return f"{self.code} ({self.material.code})"


class LotMovementKind(models.TextChoices):
    RECEIPT = "giris", "Giriş"
    ISSUE = "uretime_cikis", "Üretime çıkış"
    RETURN = "iade", "Üretimden iade"
    ADJUST = "sayim_duzeltme", "Sayım düzeltmesi"


class LotMovement(models.Model):
    lot = models.ForeignKey(MaterialLot, on_delete=models.PROTECT, related_name="movements")
    kind = models.CharField(max_length=20, choices=LotMovementKind.choices)
    quantity = models.DecimalField("Miktar (+/-)", **QTY)
    balance_after = models.DecimalField(**QTY)
    work_order = models.ForeignKey("production.WorkOrder", on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="lot_movements")
    reason = models.TextField(blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at", "-id"]


class Recipe(models.Model):
    product = models.OneToOneField(Product, on_delete=models.PROTECT, related_name="recipe")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Reçete {self.product.code}"


class ImmutableModel(models.Model):
    """Kaydedildikten sonra değiştirilemeyen kayıt (revizyon mantığı)."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Bu kayıt değiştirilemez; yeni revizyon oluşturun.")
        super().save(*args, **kwargs)


class RecipeRevision(ImmutableModel):
    recipe = models.ForeignKey(Recipe, on_delete=models.PROTECT, related_name="revisions")
    rev_no = models.PositiveIntegerField("Revizyon")
    width_mm = models.DecimalField("En (mm)", max_digits=8, decimal_places=2)
    thickness_micron = models.DecimalField("Kalınlık (mikron)", max_digits=8, decimal_places=2)
    length_m = models.DecimalField("Rulo boyu (m)", max_digits=10, decimal_places=2, null=True, blank=True)
    color = models.CharField("Renk", max_length=60, blank=True)
    print_spec = models.CharField("Baskı bilgisi", max_length=200, blank=True)
    perforation_spec = models.CharField("Perfore bilgisi", max_length=200, blank=True)
    process_notes = models.TextField("Proses notları", blank=True)
    change_reason = models.TextField("Değişiklik gerekçesi", blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["recipe", "-rev_no"]
        constraints = [models.UniqueConstraint(fields=["recipe", "rev_no"], name="uniq_recipe_rev")]

    def __str__(self):
        return f"{self.recipe.product.code} Rev.{self.rev_no}"

    @property
    def is_latest(self):
        return not self.recipe.revisions.filter(rev_no__gt=self.rev_no).exists()


class RecipeComponent(ImmutableModel):
    revision = models.ForeignKey(RecipeRevision, on_delete=models.PROTECT, related_name="components")
    material = models.ForeignKey(Material, on_delete=models.PROTECT)
    percentage = models.DecimalField("Oran (%)", max_digits=6, decimal_places=3,
                                     validators=[MinValueValidator(Decimal("0.001"))])

    class Meta:
        ordering = ["-percentage"]
        constraints = [models.UniqueConstraint(fields=["revision", "material"], name="uniq_component_material")]
