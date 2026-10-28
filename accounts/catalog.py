"""Desteklenen işlem yetkileri kataloğu ve başlangıç rol şablonları.

Yetki kodu `<alan>.<işlem>` biçimindedir. Yönetici rollere yalnız buradaki kodları verebilir; her kodun
izin verilen kapsamları (`scopes`) bellidir. Yetkiler veri bütünlüğü kurallarını (negatif stok, hareketli kaydı
silme vb.) aşamaz; bu kurallar servis katmanında yetkiden bağımsız uygulanır.
"""
from dataclasses import dataclass


class Scope:
    ALL = "all"
    CUSTOMER = "customer"   # Kullanıcıya erişim verilmiş müşteriler (CustomerAccess)
    ASSIGNED = "assigned"   # Kullanıcıya (ustabaşı) atanmış iş emirleri
    MACHINE = "machine"     # Kullanıcının atandığı makinelerdeki operasyonlar
    SHIFT = "shift"         # Operasyonun sorumlu vardiyası kullanıcının vardiyası
    MACHINE_SHIFT = "machine_shift"  # Atandığı makine VE sorumlu vardiyası kendi vardiyası olan operasyonlar

    LABELS = {ALL: "Tüm kayıtlar", CUSTOMER: "Erişim verilen müşteriler", ASSIGNED: "Kendisine atanmış işler",
              MACHINE: "Atandığı makineler", SHIFT: "Kendi vardiyası",
              MACHINE_SHIFT: "Atandığı makine ve kendi vardiyası (ikisi birden)"}
    CHOICES = list(LABELS.items())


class Kind:
    VIEW, CREATE, CHANGE, APPROVE, CANCEL = "view", "create", "change", "approve", "cancel"
    CORRECTION_REQUEST, CORRECTION_APPROVE, PRINT, ADMIN = "correction_request", "correction_approve", "print", "admin"
    LABELS = {VIEW: "Görüntüleme", CREATE: "Oluşturma", CHANGE: "Değiştirme", APPROVE: "Onaylama", CANCEL: "İptal",
              CORRECTION_REQUEST: "Düzeltme talebi", CORRECTION_APPROVE: "Düzeltme onayı", PRINT: "Yazdırma",
              ADMIN: "Yönetim"}


@dataclass(frozen=True)
class Action:
    code: str
    label: str
    group: str
    kind: str
    scopes: tuple = (Scope.ALL,)
    admin_only: bool = False  # Yalnız yönetici verebilir; İK taslağına giremez.


A, C, AS, M, SH, MS = Scope.ALL, Scope.CUSTOMER, Scope.ASSIGNED, Scope.MACHINE, Scope.SHIFT, Scope.MACHINE_SHIFT
CUST = (A, C)
PROD = (A, AS, M, SH, MS)

ACTIONS = [
    # Sistem ve personel
    Action("system.admin", "Sistem yönetimi (son yönetici koruması bu yetkiye bakar)", "Sistem", Kind.ADMIN, admin_only=True),
    Action("user.manage", "Tüm kullanıcı ve rol atamalarını yönetme", "Sistem", Kind.ADMIN, admin_only=True),
    Action("role.manage", "Rol oluşturma, yetki seçme, etkinleştirme", "Sistem", Kind.ADMIN, admin_only=True),
    Action("role.approve", "İK rol taslağını onaylama", "Sistem", Kind.APPROVE, admin_only=True),
    Action("hr.user.manage", "Personel oluşturma, ekip/vardiya, yönetici dışı hesap kapatma", "İK", Kind.CHANGE),
    Action("hr.role.assign", "İK atayabilir rolleri atama", "İK", Kind.CHANGE),
    Action("role.draft", "Rol taslağı hazırlama", "İK", Kind.CREATE),
    Action("shift.manage", "Vardiya tanımlarını yönetme", "İK", Kind.CHANGE),
    # Müşteri
    Action("customer.view", "Müşteri kartını görme", "Müşteri", Kind.VIEW, CUST),
    Action("customer.create", "Müşteri açma", "Müşteri", Kind.CREATE),
    Action("customer.change", "Müşteri kartını değiştirme / pasife alma", "Müşteri", Kind.CHANGE, CUST),
    Action("customer.commercial.view", "Ticari bilgileri (vade, ödeme, para birimi) görme", "Müşteri", Kind.VIEW, CUST),
    Action("customer.document.view", "Müşteri belgelerini görme/indirme", "Müşteri", Kind.VIEW, CUST),
    Action("customer.document.manage", "Müşteri belgesi yükleme/pasife alma", "Müşteri", Kind.CHANGE, CUST),
    Action("customer.access.manage", "Satışçılara müşteri erişimi verme/kaldırma", "Müşteri", Kind.ADMIN, admin_only=True),
    # Ürün / reçete / tanımlar
    Action("product.view", "Ürün kodlarını görme", "Ürün ve reçete", Kind.VIEW, CUST),
    Action("product.create", "Ürün kodu açma", "Ürün ve reçete", Kind.CREATE, CUST),
    Action("product.change", "Ürün kodunu değiştirme / pasife alma", "Ürün ve reçete", Kind.CHANGE, CUST),
    Action("recipe.view", "Reçete arşivi ve revizyonlarını görme", "Ürün ve reçete", Kind.VIEW),
    Action("recipe.change", "Reçete revizyonu oluşturma", "Ürün ve reçete", Kind.CHANGE),
    Action("artwork.view", "Baskı görsellerini ve onay durumunu görme / indirme", "Ürün ve reçete", Kind.VIEW, CUST),
    Action("artwork.manage", "Baskı görseli taslak sürümü yükleme / kullanımdan kaldırma", "Ürün ve reçete",
           Kind.CHANGE, CUST),
    Action("artwork.approve", "Baskı görseline iç teknik onay verme (üretime uygun)", "Ürün ve reçete", Kind.APPROVE),
    Action("artwork.customer_approval", "Baskı görseli için müşteri onayını kaydetme", "Ürün ve reçete", Kind.CHANGE,
           CUST),
    Action("machine.view", "Makineleri görme", "Tanımlar", Kind.VIEW),
    Action("machine.manage", "Makine ekleme/değiştirme", "Tanımlar", Kind.CHANGE),
    Action("material.view", "Hammadde/katkı ve lotları görme", "Tanımlar", Kind.VIEW),
    Action("material.manage", "Hammadde/katkı tanımlama", "Tanımlar", Kind.CHANGE),
    Action("lot.receive", "Malzeme lot girişi", "Tanımlar", Kind.CREATE),
    # Sipariş
    Action("order.view", "Siparişleri ve üretim ilerlemesini görme", "Sipariş", Kind.VIEW, CUST),
    Action("order.create", "Sipariş ve kalem oluşturma", "Sipariş", Kind.CREATE, CUST),
    Action("order.change", "Sipariş/kalem değiştirme, kopyalama, taslak silme", "Sipariş", Kind.CHANGE, CUST),
    Action("order.approve", "Siparişi onaylama (taslaktan çıkarma)", "Sipariş", Kind.APPROVE, CUST),
    Action("order.cancel", "Sipariş/kalem iptali", "Sipariş", Kind.CANCEL, CUST),
    Action("planning.approve", "Teknik/reçete onayı", "Sipariş", Kind.APPROVE),
    Action("exception.approve", "Uyumluluk, fazla teslim ve rota istisnalarını onaylama", "Sipariş", Kind.APPROVE),
    # Üretim
    Action("workorder.view", "İş emirlerini görme", "Üretim", Kind.VIEW, PROD + (C,)),
    Action("workorder.create", "İş emri oluşturma/düzenleme, ustabaşı atama", "Üretim", Kind.CREATE),
    Action("workorder.release", "İş emrini serbest bırakma/geri çekme", "Üretim", Kind.APPROVE),
    Action("workorder.cancel", "İş emri iptali", "Üretim", Kind.CANCEL),
    Action("workorder.schedule", "Operasyonları tarih/makine/vardiya/sıraya dağıtma", "Üretim", Kind.CHANGE, PROD),
    Action("production.execute", "Üretim kaydı (başlat, bobin, malzeme, palet, kapanış)", "Üretim", Kind.CHANGE, PROD),
    Action("production.hold", "Duruş/devam ve vardiya devri", "Üretim", Kind.CHANGE, PROD),
    Action("instruction.view", "Operasyon teknik talimatını görme ve teslim alma", "Üretim", Kind.VIEW, PROD),
    Action("instruction.print", "Makine iş talimatı yazdırma", "Üretim", Kind.PRINT, PROD),
    Action("material.deviation", "Reçete dışı malzeme kullanımına gerekçeli izin", "Üretim", Kind.APPROVE),
    # Kalite
    Action("quality.decide", "Kalite: uygun/kontrol bekliyor kararı", "Kalite", Kind.APPROVE, PROD),
    Action("quality.block", "Kalite blokajı koyma", "Kalite", Kind.CHANGE, PROD),
    Action("quality.release", "Kalite blokajını gerekçeyle kaldırma", "Kalite", Kind.APPROVE, PROD),
    # Depo
    Action("stock.view", "Palet/stok görme", "Depo", Kind.VIEW, CUST),
    Action("warehouse.operate", "Depo kabul, tartım, aktarım, sayım, hasar, konum", "Depo", Kind.CHANGE),
    Action("reservation.manage", "Siparişe ayırma, azaltma, iptal", "Depo", Kind.CHANGE, CUST),
    Action("shipment.view", "Sevkiyatları görme", "Depo", Kind.VIEW, CUST),
    Action("shipment.manage", "Sevkiyat hazırlama, QR yükleme kontrolü, iade kabulü", "Depo", Kind.CHANGE),
    Action("shipment.approve", "Son sevk onayı (stok düşer)", "Depo", Kind.APPROVE),
    Action("shipment.undo", "Çıkış öncesi sevk geri alma", "Depo", Kind.CANCEL),
    Action("shipment.exit", "Fiziksel araç çıkışını kaydetme", "Depo", Kind.APPROVE),
    Action("label.print", "Palet/kutu etiketi yazdırma", "Depo", Kind.PRINT),
    Action("stocklevel.manage", "Hedef/alt stok seviyesi tanımlama", "Depo", Kind.CHANGE),
    # Düzeltme
    Action("correction.request", "Düzeltme talebi açma", "Düzeltme", Kind.CORRECTION_REQUEST, (A, C, AS, M, SH, MS)),
    Action("correction.approve", "Düzeltme talebini onaylama/reddetme", "Düzeltme", Kind.CORRECTION_APPROVE),
]
BY_CODE = {a.code: a for a in ACTIONS}


def grouped():
    groups = {}
    for a in ACTIONS:
        groups.setdefault(a.group, []).append(a)
    return groups


# --- Başlangıç rol şablonları (migration ile veritabanına yazılır; sonradan yönetici değiştirebilir) ---------
ADMIN_ALL = {a.code: A for a in ACTIONS}
OPS_VIEW = {"customer.view": A, "product.view": A, "order.view": A, "workorder.view": A, "stock.view": A,
            "shipment.view": A, "machine.view": A, "material.view": A}

TEMPLATES = {
    "yonetici": ("Yönetici", False, ADMIN_ALL),
    "ik": ("İK", False, {"hr.user.manage": A, "hr.role.assign": A, "role.draft": A, "shift.manage": A}),
    "satis": ("Satış", True, {
        "customer.view": C, "customer.create": A, "customer.change": C, "customer.commercial.view": C,
        "customer.document.view": C, "customer.document.manage": C,
        "product.view": C, "product.create": C, "product.change": C,
        "order.view": C, "order.create": C, "order.change": C, "order.approve": C, "order.cancel": C,
        "workorder.view": C, "stock.view": C, "shipment.view": C, "reservation.manage": C,
        "correction.request": C, "machine.view": A,
        "artwork.view": C, "artwork.manage": C, "artwork.customer_approval": C}),
    "planlama": ("Planlama", True, {
        **OPS_VIEW, "product.create": A, "product.change": A, "recipe.view": A, "recipe.change": A,
        "machine.manage": A, "material.manage": A, "planning.approve": A, "exception.approve": A,
        "workorder.create": A, "workorder.release": A, "workorder.cancel": A, "workorder.schedule": A,
        "instruction.view": A, "instruction.print": A, "quality.release": A, "correction.request": A,
        "customer.document.view": A, "shift.manage": A, "stocklevel.manage": A, "material.deviation": A,
        "artwork.view": A, "artwork.manage": A, "artwork.approve": A, "artwork.customer_approval": A}),
    "uretim_yetkilisi": ("Üretim yetkilisi", True, {
        **OPS_VIEW, "workorder.schedule": A, "production.execute": A, "production.hold": A,
        "instruction.view": A, "instruction.print": A, "quality.decide": A, "quality.block": A,
        "correction.request": A, "correction.approve": A, "material.deviation": A, "customer.document.view": A,
        "artwork.view": A}),
    "ustabasi": ("Ustabaşı", True, {
        "workorder.view": AS, "workorder.schedule": AS, "production.execute": AS, "production.hold": AS,
        "instruction.view": AS, "instruction.print": AS, "quality.decide": AS, "quality.block": AS,
        "quality.release": AS, "machine.view": A, "material.view": A, "stock.view": A}),
    "operator": ("Usta / operatör", True, {
        # Geçmiş kayıt: atandığı makinelerdeki işler. Aktif teknik bilgi ve üretim kaydı: atandığı makine VE
        # operasyonun sorumlu vardiyası kendi vardiyası (vardiya devri teslim alınınca sorumluluk yeni vardiyaya geçer).
        "workorder.view": M, "production.execute": MS, "production.hold": MS, "instruction.view": MS,
        "quality.block": MS, "machine.view": A}),
    "kalite": ("Kalite sorumlusu", True, {
        "workorder.view": A, "stock.view": A, "quality.decide": A, "quality.block": A, "quality.release": A,
        "product.view": A, "machine.view": A}),
    "ambar_yetkilisi": ("Ambar yetkilisi", True, {
        **OPS_VIEW, "warehouse.operate": A, "reservation.manage": A, "shipment.manage": A, "shipment.approve": A,
        "shipment.undo": A, "shipment.exit": A,
        "label.print": A, "lot.receive": A, "quality.block": A, "correction.approve": A,
        "customer.document.view": A}),
    "depo": ("Depo personeli", True, {
        **OPS_VIEW, "warehouse.operate": A, "reservation.manage": A, "shipment.manage": A,
        "label.print": A, "lot.receive": A, "quality.block": A, "customer.document.view": A}),
}
