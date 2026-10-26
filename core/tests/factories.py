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
