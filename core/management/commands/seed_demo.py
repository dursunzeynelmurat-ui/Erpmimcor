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

USERS = [
    ("1001", "Ayşe Yönetici", Role.ADMIN),
    ("2001", "Selin Satış", Role.SALES),
    ("3001", "Murat Planlama", Role.PLANNING),
    ("4001", "Hasan Ustabaşı", Role.PRODUCTION),
    ("5001", "Deniz Depo", Role.WAREHOUSE),
]
CUSTOMERS = [
    ("M-ORNEK", "Örnek Gıda Ambalaj Ltd. (kurgusal)"),
    ("M-DENEME", "Deneme Tekstil A.Ş. (kurgusal)"),
    ("M-KURGU", "Kurgu Kimya San. (kurgusal)"),
]
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
        for no, name, role in USERS:
            if not User.objects.filter(personnel_no=no).exists():
                User.objects.create_user(no, password, full_name=name, role=role,
                                         is_staff=(role == Role.ADMIN), is_superuser=(role == Role.ADMIN))
                self.stdout.write(f"Kullanıcı: {no} {name} ({role.label})")
        admin = User.objects.get(personnel_no="1001")
        planner = User.objects.get(personnel_no="3001")

        def ensure(model, code, **data):
            obj = model.objects.filter(code=code).first()
            return obj or cat.create_definition(admin, model, {"code": code, **data})

        customers = {c: ensure(Customer, c, name=n, contact="") for c, n in CUSTOMERS}
        products = {}
        for code, cust, name, per_box in PRODUCTS:
            products[code] = ensure(Product, code, customer=customers[cust], name=name, unit=Unit.BOX,
                                    units_per_box=per_box, notes="Kurgusal demo ürünü")
        for code, name, mtype in MACHINES:
            ensure(Machine, code, name=name, machine_type=mtype)
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

        if not SalesOrder.objects.exists():
            sales = User.objects.get(personnel_no="2001")
            orders.create_order(sales, customer=customers["M-DENEME"], order_date=timezone.localdate(),
                                customer_ref="DT-PO-1001 (kurgusal)",
                                lines=[{"product": products["DEN-PF-030"], "quantity": D("60"), "unit": Unit.BOX}])

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
                                    target_quantity=D("40"), unit=Unit.BOX, notes="Müşteri stoğu (kurgusal demo)")
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
