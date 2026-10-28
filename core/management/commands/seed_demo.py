"""Tamamen kurgusal demo verisi yükler. Tekrar çalıştırılabilir (mevcut kayıtlar atlanır)."""
import os
from decimal import Decimal as D

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Role, User
from catalog import services as cat
from catalog.models import Customer, Machine, MachineType, Material, MaterialKind, Product, Unit
from orders import services as orders
from orders.models import SalesOrder

USERS = [  # (personel no, ad, rol şablonu, vardiya kodu)
    ("1001", "Ayşe Yönetici", Role.ADMIN, None),
    ("2001", "Selin Satış", Role.SALES, None),
    ("2002", "Kerem Satış", Role.SALES, None),
    ("3001", "Murat Planlama", Role.PLANNING, None),
    ("4001", "Hasan Ustabaşı", Role.PRODUCTION, "V1"),
    ("4002", "Elif Üretim Yetkilisi", Role.PRODUCTION_MANAGER, None),
    ("4003", "Can Operatör", Role.OPERATOR, "V1"),
    ("4005", "Ece Gece Operatörü", Role.OPERATOR, "V2"),
    ("4004", "Zeynep Kalite", Role.QUALITY, None),
    ("5001", "Deniz Depo", Role.WAREHOUSE, None),
    ("5002", "Burak Ambar", Role.WAREHOUSE_MANAGER, None),
    ("6001", "Gül İK", Role.HR, None),
]
# Tamamen kurgusal firma ve kimlik bilgileri (numaralar format örneğidir, gerçek kayıt değildir).
CUSTOMERS = [
    ("M-ORNEK", "Örnek Gıda Ambalaj Ltd. (kurgusal)", {
        "short_name": "Örnek Gıda", "country": "TR", "tax_id_type": "vkn", "tax_id": "9990000011", "tax_office": "Kurgu VD"}),
    ("M-DENEME", "Deneme Tekstil A.Ş. (kurgusal)", {
        "short_name": "Deneme Tekstil", "country": "TR", "tax_id_type": "vkn", "tax_id": "9990000022", "tax_office": "Örnek VD"}),
    ("M-KURGU", "Kurgu Kimya San. (kurgusal)", {
        "short_name": "Kurgu Kimya", "country": "TR", "tax_id_type": "vkn", "tax_id": "9990000033", "tax_office": "Deneme VD"}),
    ("M-NORD", "Nordfolie Beispiel GmbH (kurgusal)", {
        "short_name": "Nordfolie", "country": "DE", "language": "de", "tax_id_type": "vat", "tax_id": "DE000000000"}),
]
OPERATOR_MACHINES = {"4003": ["EXT-01", "PER-01"], "4005": ["EXT-01", "PER-01"]}
SALES_ACCESS = {"2001": ["M-ORNEK", "M-DENEME"], "2002": ["M-KURGU", "M-NORD"]}
# Aynı teknik ürün (30 µ şeffaf poşet filmi) iki müşteride farklı ürün kodlarıyla.
PRODUCTS = [
    ("ORN-PF-030", "M-ORNEK", "Şeffaf perfore poşet filmi 30µ", 20),
    ("DEN-PF-030", "M-DENEME", "Şeffaf perfore poşet filmi 30µ", 20),
    ("KRG-BS-050", "M-KURGU", "Baskılı streç 50µ", 12),
]
MACHINES = [
    ("EXT-01", "Extruder 1", MachineType.EXTRUDER), ("EXT-02", "Extruder 2", MachineType.EXTRUDER),
    ("MAT-01", "Flekso matbaa 1", MachineType.PRINT), ("PER-01", "Perfore 1", MachineType.PERFORATION),
    ("DIL-01", "Dilimleme 1", MachineType.SLITTING),
]
MATERIALS = [
    ("HM-LDPE", "LDPE granül (kurgusal tip)", MaterialKind.RAW),
    ("HM-LLDPE", "LLDPE granül (kurgusal tip)", MaterialKind.RAW),
    ("KT-SLIP", "Kaydırıcı katkı masterbatch", MaterialKind.ADDITIVE),
    ("KT-ANTIST", "Antistatik katkı masterbatch", MaterialKind.ADDITIVE),
]


class Command(BaseCommand):
    help = "Kurgusal demo verisini (kullanıcılar, tanımlar, reçeteler, örnek sipariş) yükler."

    def handle(self, *args, **opts):
        password = os.environ.get("DEMO_USER_PASSWORD")
        if not password:
            raise CommandError("DEMO_USER_PASSWORD ortam değişkeni tanımlı değil (.env dosyasına ekleyin).")
        with transaction.atomic():
            self.seed(password)
        self.stdout.write(self.style.SUCCESS("Demo verisi hazır."))

    def seed(self, password):
        from accounts.bootstrap import install_defaults
        from accounts.models import Shift
        install_defaults()
        for no, name, role, shift in USERS:
            if not User.objects.filter(personnel_no=no).exists():
                User.objects.create_user(no, password, full_name=name, role=role,
                                         shift=Shift.objects.filter(code=shift).first() if shift else None,
                                         is_staff=(role == Role.ADMIN))
                self.stdout.write(f"Kullanıcı: {no} {name} ({role.label})")
        admin = User.objects.get(personnel_no="1001")
        planner = User.objects.get(personnel_no="3001")

        def ensure(model, code, **data):
            obj = model.objects.filter(code=code).first()
            return obj or cat.create_definition(admin, model, {"code": code, **data})

        customers = {c: ensure(Customer, c, name=n, **extra) for c, n, extra in CUSTOMERS}
        self.seed_customer_cards(admin, customers)
        products = {}
        for code, cust, name, per_box in PRODUCTS:
            products[code] = ensure(Product, code, customer=customers[cust], name=name, unit=Unit.BOX,
                                    units_per_box=per_box, notes="Kurgusal demo ürünü")
        for code, name, mtype in MACHINES:
            ensure(Machine, code, name=name, machine_type=mtype)
        from accounts.models import UserMachine
        for no, codes in OPERATOR_MACHINES.items():  # operatör erişimi: atandığı makine VE kendi vardiyası
            for code in codes:
                UserMachine.objects.get_or_create(user=User.objects.get(personnel_no=no),
                                                  machine=Machine.objects.get(code=code))
        materials = {code: ensure(Material, code, name=name, kind=kind, unit="kg") for code, name, kind in MATERIALS}

        if not materials["HM-LDPE"].lots.exists():
            for code, qty in [("HM-LDPE", D("2500")), ("HM-LLDPE", D("1500")), ("KT-SLIP", D("100")), ("KT-ANTIST", D("80"))]:
                cat.receive_lot(admin, materials[code], qty, supplier_lot=f"TED-{code}-001")

        film_spec = {"width_mm": D("600"), "thickness_micron": D("30"), "length_m": D("500"), "color": "Şeffaf",
                     "print_spec": "", "perforation_spec": "Her 250 mm'de bir", "process_notes": "Kurgusal proses notu"}
        film_mix = [(materials["HM-LDPE"], D("70")), (materials["HM-LLDPE"], D("28")), (materials["KT-SLIP"], D("2"))]
        for code in ("ORN-PF-030", "DEN-PF-030"):
            if products[code].current_revision is None:
                cat.create_revision(planner, products[code], film_spec, film_mix)
        # ORN-PF-030 için Rev.2: revizyon geçmişini göstermek amacıyla
        if products["ORN-PF-030"].current_revision.rev_no == 1:
            cat.create_revision(planner, products["ORN-PF-030"], {**film_spec, "thickness_micron": D("32")},
                                [(materials["HM-LDPE"], D("68")), (materials["HM-LLDPE"], D("29")),
                                 (materials["KT-SLIP"], D("2")), (materials["KT-ANTIST"], D("1"))],
                                reason="Müşteri talebiyle kalınlık 32 µ, antistatik eklendi (kurgusal)")
        if products["KRG-BS-050"].current_revision is None:
            cat.create_revision(planner, products["KRG-BS-050"],
                                {**film_spec, "width_mm": D("500"), "thickness_micron": D("50"), "color": "Mavi baskılı",
                                 "print_spec": "2 renk logo (kurgusal)", "perforation_spec": ""},
                                [(materials["HM-LLDPE"], D("97")), (materials["KT-SLIP"], D("3"))])

        self.seed_stock_pallet(products["DEN-PF-030"], materials)
        self.seed_technical(customers, materials)

        if not SalesOrder.objects.exists():
            sales = User.objects.get(personnel_no="2001")
            order = orders.create_order(sales, customer=customers["M-DENEME"], order_date=timezone.localdate(),
                                        customer_ref="DT-PO-1001 (kurgusal)",
                                        lines=[{"product": products["DEN-PF-030"], "quantity": D("60"), "unit": Unit.BOX}])
            orders.approve_order(sales, order)  # örnek sipariş onaylı; yeni siparişler taslak açılır

    def seed_technical(self, customers, materials):
        """Kurgusal körüklü baskılı ürün (onaylı görselli), renklendirici, numune kriteri ve referans numune."""
        import io
        import segno
        from django.core.files.uploadedfile import SimpleUploadedFile
        from catalog import artwork as art
        from catalog.models import ArtworkApprovalKind
        from samples import services as smp
        from samples.models import CriterionUnit, Sample, SampleCriterion, SampleKind

        admin, planner = User.objects.get(personnel_no="1001"), User.objects.get(personnel_no="3001")
        sales, quality = User.objects.get(personnel_no="2001"), User.objects.get(personnel_no="4004")
        colorant = Material.objects.filter(code="KT-MAVI").first() or cat.create_definition(
            admin, Material, {"code": "KT-MAVI", "name": "Mavi masterbatch (kurgusal)", "kind": MaterialKind.COLORANT,
                              "unit": "kg"})
        if not colorant.lots.exists():
            cat.receive_lot(admin, colorant, D("50"), supplier_lot="TED-KT-MAVI-001")
        product = Product.objects.filter(code="ORN-KB-045").first() or cat.create_definition(admin, Product, {
            "code": "ORN-KB-045", "customer": customers["M-ORNEK"], "name": "Körüklü baskılı poşet 45µ (kurgusal)",
            "unit": Unit.BOX, "units_per_box": 500, "notes": "Kurgusal demo ürünü"})
        if product.current_revision is None:
            cat.create_revision(planner, product, {
                "width_mm": D("400"), "thickness_micron": D("45"), "product_length_mm": D("550"), "length_m": None,
                "gusset_type": "yan", "gusset_depth_mm": D("60"), "width_basis": "kapali", "width_tol_mm": D("2"),
                "thickness_tol_micron": D("3"), "appearance": "renkli", "color": "Mavi", "color_system": "ral",
                "color_code": "RAL 5015", "color_preview": "#1E5AA8", "perforation": "yok", "print_status": "var",
                "plate_status": "yeni", "plate_code": "KL-ORN-01", "cylinder_circumference_mm": D("600"),
                "print_repeat_mm": D("550"), "print_gap_mm": D("50"), "print_color_count": 2,
                "print_colors": "Pantone 286 C, Siyah (kurgusal)", "print_sides": "tek",
                "print_direction": "Rulodan çıkarken yazı okunur", "process_notes": "Kurgusal proses notu"},
                [(materials["HM-LDPE"], D("60")), (materials["HM-LLDPE"], D("36")), (colorant, D("3")),
                 (materials["KT-SLIP"], D("1"))])
        if not product.artworks.exists():
            buf = io.BytesIO()
            segno.make("ORN-KB-045 KURGUSAL BASKI GORSELI").save(buf, kind="png", scale=6)
            a = art.upload_artwork(sales, product, SimpleUploadedFile("orn-kb-045.png", buf.getvalue()),
                                   note="Kurgusal demo görseli")
            art.approve(planner, a, ArtworkApprovalKind.TECHNICAL)
            art.approve(sales, a, ArtworkApprovalKind.CUSTOMER, evidence="Kurgusal demo: e-posta onayı",
                        customer_contact="Örnek Gıda Satın Alma")
        if not SampleCriterion.objects.exists():
            smp.create_criterion(quality, name="Kalınlık", unit=CriterionUnit.MICRON,
                                 description="Kurgusal demo kriteri; kabul aralığını yetkili kişi tanımlar.")
        if not Sample.objects.exists():
            smp.create_sample(quality, kind=SampleKind.CUSTOMER_REFERENCE, customer=customers["M-ORNEK"],
                              product=product, quantity=D("5"), unit="adet", counterpart="Örnek Gıda Satın Alma",
                              room="Kalite lab", cabinet="D1", shelf="R2", box="K1",
                              notes="Müşterinin referans poşetleri (kurgusal)", shared_note="Referans numune alındı.")

    def seed_stock_pallet(self, product, materials):
        """Demo B başlangıcı: müşteri stoğu için üretilmiş, kabul edilmiş, uygun, 40 kutulu palet."""
        from core.models import QualityStatus
        from production import services as prod
        from warehouse import services as wh
        from warehouse.models import Pallet

        if Pallet.objects.exists():
            return
        planner = User.objects.get(personnel_no="3001")
        foreman = User.objects.get(personnel_no="4001")
        depot = User.objects.get(personnel_no="5001")
        ext = Machine.objects.get(code="EXT-01")
        per = Machine.objects.get(code="PER-01")
        wo = prod.create_work_order(planner, product=product, revision=product.current_revision, machines=[ext, per],
                                    target_quantity=D("40"), unit=Unit.BOX, notes="Müşteri stoğu (kurgusal demo)",
                                    foreman=foreman)
        from accounts.models import Shift
        from production import lifecycle
        shift = Shift.objects.filter(code="V1").first()
        for op in wo.operations.order_by("seq"):
            lifecycle.schedule_operation(foreman, op, planned_date=timezone.localdate(), shift=shift, machine=op.machine,
                                         queue_position=1)
        lifecycle.release(planner, wo)
        prod.start_work_order(foreman, wo)
        lot = materials["HM-LDPE"].lots.first()
        prod.issue_material(foreman, wo, lot, D("130"))
        ext_op, per_op = wo.operations.order_by("seq")
        c1 = prod.record_coil(foreman, ext_op, weight_kg=D("64.5"))
        c2 = prod.record_coil(foreman, ext_op, weight_kg=D("63.0"))
        prod.start_operation(foreman, per_op)
        prod.complete_operation(foreman, ext_op)
        o1 = prod.record_coil(foreman, per_op, weight_kg=D("64.0"), sources=[(c1, D("64.5"))])
        o2 = prod.record_coil(foreman, per_op, weight_kg=D("62.6"), sources=[(c2, D("63.0"))])
        pallet = wh.create_pallet_from_production(foreman, wo, 40, coils=[o1, o2])
        prod.complete_operation(foreman, per_op)
        prod.close_work_order(foreman, wo, scrap_kg=D("3.4"), remaining_confirmed=True)
        wh.accept_pallet(depot, pallet, {"gross_kg": D("152.4"), "pallet_tare_kg": D("22"), "location": "A-01"})
        wh.record_label_print(depot, pallet)
        wh.set_pallet_quality(foreman, pallet, QualityStatus.OK, "Görsel ve ölçü kontrolü uygun (kurgusal)")
        self.stdout.write(f"Demo B stok paleti: {pallet.code} ({product.code}, 40 kutu)")
        wh.set_stock_level(planner, product, 60, 100)  # ihtiyaç listesi örneği (kurgusal seviye)

    def seed_customer_cards(self, admin, customers):
        """Kurgusal kart bilgileri, kişiler, adresler ve satışçı erişimleri (yalnız ilk kurulumda)."""
        from catalog import customers as cs
        from catalog.models import CustomerAccess

        for code, customer in customers.items():
            if customer.contacts.exists():
                continue
            foreign = customer.country != "TR"
            cs.update_customer(admin, customer, {
                "currency": "EUR" if foreign else "TRY", "payment_method": "havale",
                "payment_term_days": 60 if foreign else 30, "delivery_terms": "DAP" if foreign else "musteri",
                "receiving_hours": "Hafta içi 08:00–16:00", "packing_instructions": "Kutular palete 5 kat dizilir (kurgusal).",
                "labeling_instructions": "Her kutuda ürün kodu ve palet no (kurgusal)."})
            cs.save_contact(admin, customer, {"name": f"{customer.short_name} Satın Alma", "department": "Satın alma",
                                              "email": f"satinalma@{code.lower()}.example", "phone": "+90 000 000 00 00",
                                              "is_primary": True, "is_active": True})
            cs.save_address(admin, customer, {"purpose": "her_ikisi", "name": "Merkez", "country": customer.country,
                                              "region": "Kurgu Bölge", "city": "Örnek İlçe", "postal_code": "00000",
                                              "line": "Kurgusal Cad. No:1", "default_billing": True,
                                              "default_shipping": True, "is_active": True})
        today = timezone.localdate()
        for no, codes in SALES_ACCESS.items():
            user = User.objects.get(personnel_no=no)
            for code in codes:
                if not CustomerAccess.objects.filter(user=user, customer=customers[code], revoked_at__isnull=True).exists():
                    cs.grant_access(admin, customers[code], user, today, None, "Demo: müşteri sorumlusu")
        # Geçici erişim örneği: 2002, M-ORNEK'i 7 gün görebilir.
        kerem = User.objects.get(personnel_no="2002")
        if not CustomerAccess.objects.filter(user=kerem, customer=customers["M-ORNEK"]).exists():
            cs.grant_access(admin, customers["M-ORNEK"], kerem, today, today + timezone.timedelta(days=7),
                            "Demo: izin dönüşünde geçici destek")
