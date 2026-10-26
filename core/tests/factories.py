"""Testler için kurgusal veri üreticileri (servisler üzerinden, audit dahil)."""
from decimal import Decimal as D

from django.utils import timezone

from accounts.models import Role, User
from catalog import services as cat
from catalog.models import Customer, Machine, MachineType, Material, MaterialKind, Product, Unit

PASSWORD = "Test-Parola-2026"


def user(role, no=None):
    no = no or {Role.ADMIN: "9001", Role.SALES: "9002", Role.PLANNING: "9003",
                Role.PRODUCTION: "9004", Role.WAREHOUSE: "9005"}[role]
    return User.objects.filter(personnel_no=no).first() or User.objects.create_user(
        no, PASSWORD, full_name=f"Test {role}", role=role)


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
        self.prod_a = cat.create_definition(a, Product, {"code": "A-FILM", "customer": self.cust_a, "name": "Film",
                                                         "unit": Unit.BOX})
        self.prod_b = cat.create_definition(a, Product, {"code": "B-FILM", "customer": self.cust_b, "name": "Film",
                                                         "unit": Unit.BOX})
        self.ext = cat.create_definition(a, Machine, {"code": "E1", "name": "Ext", "machine_type": MachineType.EXTRUDER})
        self.prn = cat.create_definition(a, Machine, {"code": "P1", "name": "Mat", "machine_type": MachineType.PRINT})
        self.slit = cat.create_definition(a, Machine, {"code": "D1", "name": "Dil", "machine_type": MachineType.SLITTING})
        self.ldpe = cat.create_definition(a, Material, {"code": "LDPE", "name": "LDPE", "kind": MaterialKind.RAW, "unit": "kg"})
        self.slip = cat.create_definition(a, Material, {"code": "SLIP", "name": "Slip", "kind": MaterialKind.ADDITIVE, "unit": "kg"})
        self.lot = cat.receive_lot(a, self.ldpe, D("1000"))
        self.rev_a = cat.create_revision(self.planner, self.prod_a, self.spec(), self.mix())
        self.rev_b = cat.create_revision(self.planner, self.prod_b, self.spec(), self.mix())

    def spec(self, **over):
        return {"width_mm": D("600"), "thickness_micron": D("30"), "length_m": None, "color": "Şeffaf",
                "print_spec": "", "perforation_spec": "", "process_notes": "", **over}

    def mix(self):
        return [(self.ldpe, D("98")), (self.slip, D("2"))]

    def order(self, product=None, qty=D("50"), customer=None):
        from orders import services as orders
        product = product or self.prod_a
        return orders.create_order(self.sales, customer=customer or product.customer, order_date=timezone.localdate(),
                                   lines=[{"product": product, "quantity": qty, "unit": Unit.BOX}])

    def approved_line(self, **kw):
        from orders import services as orders
        line = self.order(**kw).lines.get()
        return orders.approve_line(self.planner, line, line.product.current_revision)

    def work_order(self, line=None, machines=None, qty=D("50")):
        from production import services as prod
        line = line or self.approved_line()
        return prod.create_work_order(self.planner, order_line=line, machines=machines or [self.ext, self.prn],
                                      target_quantity=qty, unit=Unit.BOX)

    # --- M2 yardımcıları ---------------------------------------------------------
    def started_wo(self, product=None, line=None, machines=None, stock=False):
        from production import services as prod
        product = product or self.prod_a
        if stock:
            wo = prod.create_work_order(self.planner, product=product, revision=product.current_revision,
                                        machines=machines or [self.ext], target_quantity=D("40"), unit=Unit.BOX)
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
