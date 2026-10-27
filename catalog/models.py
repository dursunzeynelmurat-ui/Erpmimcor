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


class EntityType(models.TextChoices):
    COMPANY = "firma", "Firma"
    PERSON = "sahis", "Şahıs"


class TaxIdType(models.TextChoices):
    VKN = "vkn", "Vergi kimlik no (TR, 10 hane)"
    TCKN = "tckn", "TC kimlik no (TR, 11 hane)"
    VAT = "vat", "KDV/VAT no (yabancı)"
    REG = "kayit", "Ticaret sicil / kayıt no (yabancı)"
    OTHER = "diger", "Diğer kimlik"


COUNTRIES = [("TR", "Türkiye"), ("DE", "Almanya"), ("NL", "Hollanda"), ("FR", "Fransa"), ("GB", "Birleşik Krallık"),
             ("IT", "İtalya"), ("ES", "İspanya"), ("BE", "Belçika"), ("AT", "Avusturya"), ("PL", "Polonya"),
             ("RO", "Romanya"), ("BG", "Bulgaristan"), ("GR", "Yunanistan"), ("AZ", "Azerbaycan"), ("GE", "Gürcistan"),
             ("IQ", "Irak"), ("AE", "Birleşik Arap Emirlikleri"), ("SA", "Suudi Arabistan"), ("US", "ABD"),
             ("XX", "Diğer")]
LANGUAGES = [("tr", "Türkçe"), ("en", "İngilizce"), ("de", "Almanca"), ("fr", "Fransızca"), ("ar", "Arapça"),
             ("ru", "Rusça")]
CURRENCIES = [("TRY", "TL"), ("EUR", "Euro"), ("USD", "ABD doları"), ("GBP", "Sterlin")]
PAYMENT_METHODS = [("havale", "Havale/EFT"), ("cek", "Çek"), ("senet", "Senet"), ("akreditif", "Akreditif"),
                   ("pesin", "Peşin"), ("diger", "Diğer")]
DELIVERY_TERMS = [("fabrika", "Fabrikada teslim (müşteri alır)"), ("musteri", "Müşteri adresine teslim"),
                  ("EXW", "EXW"), ("FCA", "FCA"), ("FOB", "FOB"), ("CPT", "CPT"), ("CIF", "CIF"),
                  ("DAP", "DAP"), ("DDP", "DDP")]


class Customer(models.Model):
    """Müşteri kartı. İlk açılış kısadır; diğer bölümler sonradan tamamlanır. Aşama zorunlulukları
    catalog.customer_rules.readiness() ile kontrol edilir."""

    code = models.CharField("Müşteri kodu", max_length=20, unique=True)
    name = models.CharField("Resmî ünvan", max_length=200)
    short_name = models.CharField("Kısa ad", max_length=60, blank=True)
    entity_type = models.CharField("Tür", max_length=10, choices=EntityType.choices, default=EntityType.COMPANY)
    country = models.CharField("Ülke", max_length=2, choices=COUNTRIES, default="TR")
    language = models.CharField("İletişim dili", max_length=5, choices=LANGUAGES, default="tr")
    website = models.URLField("Web sitesi", blank=True)
    tax_id_type = models.CharField("Kimlik türü", max_length=10, choices=TaxIdType.choices, blank=True)
    tax_id = models.CharField("Vergi/kayıt no", max_length=40, blank=True)
    tax_office = models.CharField("Vergi dairesi", max_length=80, blank=True)
    currency = models.CharField("Para birimi", max_length=3, choices=CURRENCIES, blank=True)
    payment_method = models.CharField("Ödeme şekli", max_length=20, choices=PAYMENT_METHODS, blank=True)
    payment_term_days = models.PositiveIntegerField("Vade (gün)", null=True, blank=True)
    delivery_terms = models.CharField("Teslim şekli", max_length=20, choices=DELIVERY_TERMS, blank=True)
    delivery_place = models.CharField("Teslim yeri", max_length=200, blank=True)
    po_required = models.BooleanField("Siparişte müşteri PO/referans zorunlu", default=False)
    receiving_hours = models.CharField("Teslim alma saatleri", max_length=120, blank=True)
    appointment_required = models.BooleanField("Teslimat için randevu gerekir", default=False)
    packing_instructions = models.TextField("Paketleme talimatı", blank=True)
    labeling_instructions = models.TextField("Etiketleme talimatı", blank=True)
    notes = models.TextField("Not", blank=True)
    is_active = models.BooleanField("Aktif", default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ActiveQuerySet.as_manager()

    class Meta:
        ordering = ["code"]
        verbose_name = "Müşteri"
        verbose_name_plural = "Müşteriler"
        constraints = [models.UniqueConstraint(fields=["country", "tax_id_type", "tax_id"],
                                               condition=~Q(tax_id=""), name="uniq_customer_tax_id")]

    def __str__(self):
        return f"{self.code} – {self.name}"

    @property
    def display_name(self):
        return self.short_name or self.name


class CustomerContact(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="contacts")
    name = models.CharField("Ad soyad", max_length=120)
    department = models.CharField("Görev / departman", max_length=120, blank=True)
    email = models.EmailField("E-posta", blank=True)
    phone = models.CharField("Telefon", max_length=40, blank=True)
    is_primary = models.BooleanField("Ana kişi", default=False)
    is_active = models.BooleanField("Aktif", default=True)

    class Meta:
        ordering = ["-is_primary", "name"]
        constraints = [models.UniqueConstraint(fields=["customer"], condition=Q(is_primary=True, is_active=True),
                                               name="one_primary_contact")]

    def __str__(self):
        return self.name


class AddressPurpose(models.TextChoices):
    BILLING = "fatura", "Fatura"
    SHIPPING = "teslimat", "Teslimat"
    BOTH = "her_ikisi", "Fatura ve teslimat"


class CustomerAddress(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="addresses")
    purpose = models.CharField("Kullanım", max_length=10, choices=AddressPurpose.choices)
    name = models.CharField("Adres adı", max_length=80)
    country = models.CharField("Ülke", max_length=2, choices=COUNTRIES, default="TR")
    region = models.CharField("Şehir / bölge", max_length=80, blank=True)
    city = models.CharField("İlçe / şehir", max_length=80, blank=True)
    postal_code = models.CharField("Posta kodu", max_length=20, blank=True)
    line = models.TextField("Açık adres")
    default_billing = models.BooleanField("Varsayılan fatura adresi", default=False)
    default_shipping = models.BooleanField("Varsayılan teslimat adresi", default=False)
    is_active = models.BooleanField("Aktif", default=True)

    class Meta:
        ordering = ["-default_billing", "-default_shipping", "name"]
        constraints = [
            models.UniqueConstraint(fields=["customer"], condition=Q(default_billing=True, is_active=True),
                                    name="one_default_billing"),
            models.UniqueConstraint(fields=["customer"], condition=Q(default_shipping=True, is_active=True),
                                    name="one_default_shipping"),
        ]

    def __str__(self):
        return f"{self.name}: {self.as_text()}"

    def as_text(self):
        parts = [self.line, " ".join(p for p in (self.postal_code, self.city) if p), self.region,
                 self.get_country_display()]
        return ", ".join(p for p in parts if p)


class DocumentKind(models.TextChoices):
    INSTRUCTION = "talimat", "Müşteri talimatı"
    CONTRACT = "sozlesme", "Sözleşme"
    LABEL_SAMPLE = "etiket", "Etiket örneği"
    OTHER = "diger", "Diğer"


class DocumentVisibility(models.TextChoices):
    COMMERCIAL = "ticari", "Yalnız ticari yetkililer"
    OPERATIONS = "operasyon", "Operasyon (üretim/depo) de görebilir"


def customer_document_path(instance, filename):
    import uuid
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"musteri-belgeleri/{instance.customer_id}/{uuid.uuid4().hex}.{ext}"


class CustomerDocument(models.Model):
    """Belge sürümleri silinmez; yeni sürüm yeni kayıt, eskisi pasife alınabilir."""

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="documents")
    kind = models.CharField("Belge türü", max_length=10, choices=DocumentKind.choices)
    title = models.CharField("Başlık", max_length=120)
    version = models.CharField("Sürüm", max_length=20, default="1")
    valid_from = models.DateField("Geçerlilik tarihi", null=True, blank=True)
    visibility = models.CharField("Görünürlük", max_length=10, choices=DocumentVisibility.choices,
                                  default=DocumentVisibility.COMMERCIAL)
    file = models.FileField(upload_to=customer_document_path)
    original_name = models.CharField(max_length=200)
    content_type = models.CharField(max_length=60)
    size = models.PositiveIntegerField()
    is_active = models.BooleanField("Aktif", default=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "title", "-uploaded_at"]

    def __str__(self):
        return f"{self.title} (s.{self.version})"


class AccessSource(models.TextChoices):
    NEW_CUSTOMER = "yeni_musteri", "Müşteriyi açan kullanıcı"
    ADMIN = "yonetici", "Yönetici ataması (kalıcı)"
    DEPUTY = "vekalet", "Süreli erişim / vekâlet"
    MIGRATED = "tasima", "Eski sistemden taşınan (toplu)"
    UNKNOWN = "belirsiz", "Kaynağı belirsiz"


class CustomerAccess(models.Model):
    """Satışçının müşteriye erişimi. Süresi dolan erişim sorguda kendiliğinden düşer; kayıt silinmez."""

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="accesses")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="customer_accesses")
    valid_from = models.DateField("Başlangıç")
    valid_until = models.DateField("Bitiş (boşsa kalıcı)", null=True, blank=True)
    reason = models.TextField("Gerekçe", blank=True)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    granted_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    source = models.CharField("Kaynak", max_length=15, choices=AccessSource.choices, default=AccessSource.ADMIN)
    needs_review = models.BooleanField("Yönetici incelemesi bekliyor", default=False)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField("İnceleme notu", max_length=200, blank=True)

    class Meta:
        ordering = ["customer", "-granted_at"]
        constraints = [models.CheckConstraint(condition=Q(valid_until__isnull=True) | Q(valid_until__gte=models.F("valid_from")),
                                              name="access_dates_ordered")]

    @property
    def is_current(self):
        from django.utils import timezone
        today = timezone.localdate()
        return (self.revoked_at is None and self.valid_from <= today
                and (self.valid_until is None or self.valid_until >= today))


class Unit(models.TextChoices):
    BOX = "kutu", "Kutu"
    KG = "kg", "kg"
    PIECE = "adet", "Adet"
    ROLL = "rulo", "Rulo"


class PackageType(models.TextChoices):
    BOX = "kutu", "Kutu"
    ROLL = "rulo", "Rulo"


class Product(models.Model):
    """Ürün kodu. Her kod tek müşteriye aittir; teknik olarak aynı ürün başka müşteride farklı koddur."""

    code = models.CharField("Ürün kodu", max_length=40, unique=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="products", verbose_name="Müşteri")
    name = models.CharField("Ürün adı", max_length=200)
    unit = models.CharField("Satış birimi", max_length=10, choices=Unit.choices, default=Unit.BOX)
    units_per_box = models.PositiveIntegerField("Paket içi adet/rulo", null=True, blank=True)
    package_type = models.CharField("Paket tipi", max_length=10, choices=PackageType.choices, default=PackageType.BOX)
    nominal_package_kg = models.DecimalField("Tahmini paket ağırlığı (kg)", max_digits=10, decimal_places=3, null=True,
                                             blank=True, help_text="Yalnız bilgi amaçlı tahmin; sevk/stok kg'ye "
                                                                   "çevrilmez, tartım yerine geçmez.")
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


class MaterialUnit(models.TextChoices):
    KG = "kg", "kg"
    LITRE = "lt", "litre"
    PIECE = "adet", "adet"


class Material(models.Model):
    """Hammadde/katkı tanımı (fiziksel lot değil). Birim ilk hareketten sonra değiştirilemez;
    kapanış dengesi yalnız kg birimli malzemeyle kurulur, diğer birimler ayrı listelenir."""

    code = models.CharField("Malzeme kodu", max_length=30, unique=True)
    name = models.CharField("Malzeme adı", max_length=200)
    kind = models.CharField("Tür", max_length=20, choices=MaterialKind.choices)
    unit = models.CharField("Birim", max_length=10, choices=MaterialUnit.choices, default=MaterialUnit.KG)
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
