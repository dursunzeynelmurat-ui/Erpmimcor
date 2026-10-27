"""Testler için kurgusal veri üreticileri (servisler üzerinden, audit dahil)."""
from decimal import Decimal as D

from django.utils import timezone

from accounts.models import Role, User
from catalog import services as cat
from catalog.models import Customer, Machine, MachineType, Material, MaterialKind, Product, Unit

PASSWORD = "Test-Parola-2026"


def user(role, no=None):
    from accounts.bootstrap import install_defaults
    install_defaults()
    no = no or {Role.ADMIN: "9001", Role.SALES: "9002", Role.PLANNING: "9003",
                Role.PRODUCTION: "9004", Role.WAREHOUSE: "9005"}[role]
    return User.objects.filter(personnel_no=no).first() or User.objects.create_user(
        no, PASSWORD, full_name=f"Test {role}", role=role)


def complete_card(actor, customer, tax_id):
    """Sipariş onayı ve sevkiyat için müşteri kartını kurgusal bilgilerle tamamlar."""
    from catalog import customers as cs
    cs.update_customer(actor, customer, {"tax_id_type": "vkn", "tax_id": tax_id, "tax_office": "Kurgu VD",
                                         "currency": "TRY", "payment_method": "havale", "payment_term_days": 30,
                                         "delivery_terms": "musteri"})
    cs.save_contact(actor, customer, {"name": "Kurgu Kişi", "department": "", "email": "", "phone": "",
                                      "is_primary": True, "is_active": True})
    cs.save_address(actor, customer, {"purpose": "her_ikisi", "name": "Merkez", "country": "TR", "region": "",
                                      "city": "Kurgu", "postal_code": "", "line": "Kurgu Sok. 1", "default_billing": True,
                                      "default_shipping": True, "is_active": True})


class World:
    """Bir müşteri, iki ürün (iki farklı müşteride), makineler, malzemeler, reçete."""

    def __init__(self):
        self.admin = user(Role.ADMIN)
        self.sales = user(Role.SALES)
        self.planner = user(Role.PLANNING)
        self.foreman = user(Role.PRODUCTION)
        self.depot = user(Role.WAREHOUSE)
        a = self.admin
        self.cust_a = cat.create_definition(a, Customer, {"code": "C-A", "name": "Kurgu A"})
        self.cust_b = cat.create_definition(a, Customer, {"code": "C-B", "name": "Kurgu B"})
        from catalog import customers as cs
        for i, c in enumerate((self.cust_a, self.cust_b)):
            cs.grant_access(a, c, self.sales, timezone.localdate(), None, "Test")
            complete_card(a, c, f"99900001{i}{i}")
        self.prod_a = cat.create_definition(a, Product, {"code": "A-FILM", "customer": self.cust_a, "name": "Film",
                                                         "unit": Unit.BOX})
        self.prod_b = cat.create_definition(a, Product, {"code": "B-FILM", "customer": self.cust_b, "name": "Film",
                                                         "unit": Unit.BOX})
        self.ext = cat.create_definition(a, Machine, {"code": "E1", "name": "Ext", "machine_type": MachineType.EXTRUDER})
        self.prn = cat.create_definition(a, Machine, {"code": "P1", "name": "Mat", "machine_type": MachineType.PRINT})
        self.slit = cat.create_definition(a, Machine, {"code": "D1", "name": "Dil", "machine_type": MachineType.SLITTING})
        self.ldpe = cat.create_definition(a, Material, {"code": "LDPE", "name": "LDPE", "kind": MaterialKind.RAW, "unit": "kg"})
        self.slip = cat.create_definition(a, Material, {"code": "SLIP", "name": "Slip", "kind": MaterialKind.ADDITIVE, "unit": "kg"})
        self.lot = cat.receive_lot(a, self.ldpe, D("1000"), supplier_lot="TED-TEST-1")
        self.rev_a = cat.create_revision(self.planner, self.prod_a, self.spec(), self.mix())
        self.rev_b = cat.create_revision(self.planner, self.prod_b, self.spec(), self.mix())

    def spec(self, **over):
        return {"width_mm": D("600"), "thickness_micron": D("30"), "length_m": None, "color": "Şeffaf",
                "print_spec": "", "perforation_spec": "", "process_notes": "", **over}

    def mix(self):
        return [(self.ldpe, D("98")), (self.slip, D("2"))]

    def order(self, product=None, qty=D("50"), customer=None, draft=False):
        from orders import services as orders
        product = product or self.prod_a
        order = orders.create_order(self.sales, customer=customer or product.customer, order_date=timezone.localdate(),
                                    lines=[{"product": product, "quantity": qty, "unit": Unit.BOX}])
        return order if draft else orders.approve_order(self.sales, order)

    def approved_line(self, **kw):
        from orders import services as orders
        line = self.order(**kw).lines.get()
        return orders.approve_line(self.planner, line, line.product.current_revision)

    def free_extruder(self):
        """Tek aktif iş kuralı gereği aynı anda çalışan test işleri için boş bir extruder (gerekirse yenisi)."""
        from production.models import OperationStatus
        busy = set(Machine.objects.filter(operations__status=OperationStatus.RUNNING).values_list("pk", flat=True))
        for m in Machine.objects.filter(machine_type=MachineType.EXTRUDER, is_active=True).order_by("pk"):
            if m.pk not in busy:
                return m
        n = Machine.objects.filter(machine_type=MachineType.EXTRUDER).count() + 1
        return cat.create_definition(self.admin, Machine, {"code": f"E{n}", "name": f"Ext {n}",
                                                           "machine_type": MachineType.EXTRUDER})

    def work_order(self, line=None, machines=None, qty=D("50")):
        from production import services as prod
        line = line or self.approved_line()
        from production import lifecycle
        wo = prod.create_work_order(self.planner, order_line=line, machines=machines or [self.free_extruder(), self.prn],
                                    target_quantity=qty, unit=Unit.BOX, foreman=self.foreman)
        return lifecycle.release(self.planner, wo)

    # --- M2 yardımcıları ---------------------------------------------------------
    def started_wo(self, product=None, line=None, machines=None, stock=False):
        from production import services as prod
        product = product or self.prod_a
        if stock:
            wo = prod.create_work_order(self.planner, product=product, revision=product.current_revision,
                                        machines=machines or [self.free_extruder()], target_quantity=D("40"), unit=Unit.BOX,
                                        foreman=self.foreman)
            from production import lifecycle
            lifecycle.release(self.planner, wo)
        else:
            wo = self.work_order(line=line or self.approved_line(product=product), machines=machines or [self.ext])
        prod.start_work_order(self.foreman, wo)
        wo.refresh_from_db()
        return wo

    def coil(self, wo, kg=D("100"), op_seq=1, sources=()):
        from production import services as prod
        op = wo.operations.get(seq=op_seq)
        return prod.record_coil(self.foreman, op, weight_kg=kg, sources=sources)

    def stock_pallet(self, boxes=40, product=None, accept=True, quality_ok=True):
        from core.models import QualityStatus
        from warehouse import services as wh
        wo = self.started_wo(product=product, stock=True)
        coil = self.coil(wo)
        pallet = wh.create_pallet_from_production(self.foreman, wo, boxes, coils=[coil])
        if accept:
            wh.accept_pallet(self.depot, pallet, {"gross_kg": D("125")})
        if quality_ok:
            wh.set_pallet_quality(self.foreman, pallet, QualityStatus.OK, "Kontrol edildi")
        pallet.refresh_from_db()
        return pallet


def load(actor, shipment):
    """Test yardımcısı: sevkiyatı 'hazırlandı' yapar ve satırlardaki paletleri okutur (idempotent).
    Sevk onayı ayrıca confirm_shipment ile verilir."""
    from warehouse import services as wh
    from warehouse.models import ShipmentStatus
    shipment.refresh_from_db()
    if shipment.status == ShipmentStatus.DRAFT and shipment.lines.exists():
        wh.prepare_shipment(actor, shipment)
        shipment.refresh_from_db()
    if shipment.status == ShipmentStatus.PREPARED:
        for p in wh.load_check(shipment)["missing"]:
            wh.scan_pallet(actor, shipment, p.qr_token)
    shipment.refresh_from_db()
    return shipment
