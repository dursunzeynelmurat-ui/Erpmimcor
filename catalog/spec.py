"""Ürün teknik özelliklerinin tek tanımı (alan, etiket, birim, kısa açıklama, bölüm, görünürlük koşulu).

Değerler `RecipeRevision` üzerinde tutulur (tek doğruluk kaynağı). Formlar, teknik özet kartı, teknik imza ve
revizyonlu repete değişiklik isteği bu listeyi kullanır. Seçimli alanlarda boş değer "Belirtilmemiş" demektir;
"Yok" ayrı ve açık bir seçimdir. Eski kayıtlarda bilinmeyen bilgi boş kalır (0 veya "yok" yazılmaz).
"""
from dataclasses import dataclass, field

from django.db import models

UNSPECIFIED = "Belirtilmemiş"


class Gusset(models.TextChoices):
    NONE = "yok", "Körük yok"
    SIDE = "yan", "Yan körük"
    BOTTOM = "taban", "Taban körüğü"


class WidthBasis(models.TextChoices):
    CLOSED = "kapali", "Kapalı en (körük içeri katlanmış hâliyle ölçülen en)"
    OPEN = "acik", "Açılmış toplam en (körükler açık hâliyle toplam en)"


class Appearance(models.TextChoices):
    CLEAR = "seffaf", "Şeffaf"
    NATURAL = "dogal", "Doğal"
    COLORED = "renkli", "Renkli"


class ColorSystem(models.TextChoices):
    RAL = "ral", "RAL"
    PANTONE = "pantone", "Pantone"
    CUSTOMER = "musteri", "Müşteri renk kodu"
    OTHER = "diger", "Diğer"


class YesNo(models.TextChoices):
    NO = "yok", "Yok"
    YES = "var", "Var"


class Plate(models.TextChoices):
    EXISTING = "mevcut", "Mevcut klişe kullanılacak"
    NEW = "yeni", "Yeni klişe hazırlanacak"


class PrintSides(models.TextChoices):
    ONE = "tek", "Tek yüz"
    TWO = "cift", "Çift yüz"


@dataclass(frozen=True)
class SpecField:
    name: str
    label: str
    unit: str = ""
    help: str = ""
    kind: str = "decimal"          # decimal | int | choice | text | color
    choices: type = None
    section: str = "olcu"
    show_if: tuple = ()            # (alan, (değerler...)) — koşul sağlanmazsa alan gizlenir ve kaydedilmez
    approval_required_if: tuple = ()  # onay (revizyon) için zorunlu olduğu koşul; () = isteğe bağlı
    print_critical: bool = False   # değişirse baskı onayları yeniden gerekir
    attrs: dict = field(default_factory=dict)


GUSSETED = ("gusset_type", (Gusset.SIDE, Gusset.BOTTOM))
PRINTED = ("print_status", (YesNo.YES,))

SECTIONS = [
    ("olcu", "Ölçüler"),
    ("gorunum", "Görünüm"),
    ("perfore", "Perfore"),
    ("baski", "Baskı"),
    ("paket", "Paketleme ve notlar"),
]

FIELDS = [
    SpecField("width_mm", "En", "mm", "Filmin/torbanın eni. Körüklü üründe hangi en olduğunu aşağıda seçin."),
    SpecField("thickness_micron", "Hedef kalınlık", "µm", "Mikron. Örnek: 30 µm = 0,030 mm."),
    SpecField("product_length_mm", "Ürün / torba boyu", "mm", "Kesilmiş ürün veya torbanın boyu. Rulo ürünlerde boş "
              "bırakılabilir."),
    SpecField("length_m", "Rulo uzunluğu", "m", "Bir rulodaki film uzunluğu (metre)."),
    SpecField("gusset_type", "Körük", kind="choice", choices=Gusset, help="Körük yoksa 'Körük yok' seçin; "
              "bilinmiyorsa boş bırakın."),
    SpecField("gusset_depth_mm", "Körük derinliği", "mm", "Tek bir körüğün katlanmış derinliği.", show_if=GUSSETED,
              approval_required_if=GUSSETED),
    SpecField("width_basis", "En ölçüm tanımı", kind="choice", choices=WidthBasis,
              help="Körüklü üründe yukarıdaki en hangi ölçü? Sistem bu ikisi arasında dönüşüm yapmaz.",
              show_if=GUSSETED, approval_required_if=GUSSETED),
    SpecField("width_tol_mm", "En toleransı (±)", "mm", "Varsa kabul edilen sapma. Örnek: 2 → ±2 mm."),
    SpecField("thickness_tol_micron", "Kalınlık toleransı (±)", "µm", "Varsa kabul edilen sapma."),
    SpecField("length_tol_mm", "Boy toleransı (±)", "mm", "Varsa ürün/torba boyu için kabul edilen sapma."),
    SpecField("appearance", "Görünüm", kind="choice", choices=Appearance, section="gorunum"),
    SpecField("color", "Renk adı", kind="text", section="gorunum", help="Örnek: Şeffaf, Beyaz, Mavi."),
    SpecField("color_system", "Renk referans sistemi", kind="choice", choices=ColorSystem, section="gorunum"),
    SpecField("color_code", "Renk kodu", kind="text", section="gorunum", help="Örnek: RAL 5015 veya müşterinin kodu."),
    SpecField("color_preview", "Ekran önizleme rengi", kind="color", section="gorunum",
              help="İsteğe bağlı. Ekrandaki renk yalnız fikir verir; fiziksel renk onayı değildir."),
    SpecField("perforation", "Perfore", kind="choice", choices=YesNo, section="perfore",
              help="Perfore yoksa 'Yok' seçin; bilinmiyorsa boş bırakın."),
    SpecField("perforation_pitch_mm", "Perfore aralığı / koparma boyu", "mm", "İki perfore çizgisi arası uzunluk.",
              section="perfore", show_if=("perforation", (YesNo.YES,))),
    SpecField("perforation_spec", "Perfore notu", kind="text", section="perfore"),
    SpecField("print_status", "Baskı", kind="choice", choices=YesNo, section="baski", print_critical=True,
              help="Baskı varsa baskı bilgileri açılır."),
    SpecField("plate_status", "Klişe", kind="choice", choices=Plate, section="baski", show_if=PRINTED,
              print_critical=True),
    SpecField("plate_code", "Klişe kodu / referansı", kind="text", section="baski", show_if=PRINTED,
              print_critical=True),
    SpecField("cylinder_circumference_mm", "Kazan çevresi", "mm", "Baskı kazanının (silindirin) çevresi.",
              section="baski", show_if=PRINTED, print_critical=True),
    SpecField("print_repeat_mm", "Baskı tekrar boyu", "mm", "Aynı baskının bir sonraki tekrarına kadar olan boy.",
              section="baski", show_if=PRINTED, print_critical=True),
    SpecField("print_gap_mm", "İki baskı arası boşluk", "mm", "Bir baskının bitişiyle sonrakinin başlangıcı arası.",
              section="baski", show_if=PRINTED, print_critical=True),
    SpecField("print_color_count", "Baskı renk sayısı", kind="int", section="baski", show_if=PRINTED,
              print_critical=True),
    SpecField("print_colors", "Baskı renk referansları", kind="text", section="baski", show_if=PRINTED,
              print_critical=True, help="Örnek: Pantone 286 C, Siyah."),
    SpecField("print_sides", "Baskı yüzü", kind="choice", choices=PrintSides, section="baski", show_if=PRINTED,
              print_critical=True),
    SpecField("print_direction", "Baskı yönü", kind="text", section="baski", show_if=PRINTED, print_critical=True,
              help="Sarım/okuma yönü. Örnek: Rulodan çıkarken yazı okunur."),
    SpecField("print_spec", "Baskı notu", kind="text", section="baski"),
    SpecField("packing_notes", "Ambalaj / etiket notu (ürüne özel)", kind="text", section="paket",
              help="Müşteri kartındaki genel paketleme ve etiket talimatına ek, bu ürüne özel not."),
    SpecField("process_notes", "Proses notu", kind="text", section="paket"),
]
BY_NAME = {f.name: f for f in FIELDS}
FIELD_NAMES = [f.name for f in FIELDS]
PRINT_CRITICAL = [f.name for f in FIELDS if f.print_critical]
# Satışçının revizyonlu repete isteğinde değiştirebileceği alanlar (karışım ve proses notu yok)
CHANGEABLE = [n for n in FIELD_NAMES if n not in ("process_notes",)]


def visible(spec_field, values):
    if not spec_field.show_if:
        return True
    name, allowed = spec_field.show_if
    return values.get(name) in allowed


def clean_hidden(values):
    """Gizli koşullu alanları temizler: 'Baskı yok' seçildiyse eski baskı değerleri kaydedilmez."""
    out = dict(values)
    for f in FIELDS:
        if not visible(f, out):
            out[f.name] = None if f.kind in ("decimal", "int") else ""
    return out


def approval_problems(values):
    """Revizyon (üretim onayı) için eksikler; taslakta engel değildir."""
    problems = []
    for f in FIELDS:
        if f.approval_required_if:
            name, allowed = f.approval_required_if
            if values.get(name) in allowed and values.get(f.name) in (None, ""):
                problems.append((f.name, f"{f.label} körüklü üründe zorunludur."))
    return problems


def display(spec_field, value):
    """Kartta gösterim: boş → 'Belirtilmemiş'; seçimler etiketleriyle; birim yazılır."""
    from core.templatetags.mimcor import qty
    if value in (None, ""):
        return UNSPECIFIED
    if spec_field.kind == "choice":
        return dict(spec_field.choices.choices).get(value, value)
    if spec_field.kind in ("decimal", "int"):
        return f"{qty(value)} {spec_field.unit}".strip()
    return str(value)


def card(revision, include_hidden=False):
    """Teknik özet kartı bölümleri: [(başlık, [satır sözlüğü])]. Karışım içermez."""
    values = {n: getattr(revision, n) for n in FIELD_NAMES}
    out = []
    for key, title in SECTIONS:
        rows = []
        for f in FIELDS:
            if f.section != key or (not include_hidden and not visible(f, values)):
                continue
            if f.kind == "text" and not values[f.name] and f.name in ("perforation_spec", "print_spec",
                                                                       "process_notes", "packing_notes"):
                continue  # boş serbest notlar listelenmez
            rows.append({"name": f.name, "label": f.label, "value": display(f, values[f.name]),
                         "raw": values[f.name], "unspecified": values[f.name] in (None, ""), "help": f.help})
        out.append((title, rows))
    return out
