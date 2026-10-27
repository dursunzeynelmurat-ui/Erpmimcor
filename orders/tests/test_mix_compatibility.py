"""Karışım farkı otomatik teknik eşdeğer sayılmaz; eşdeğerlik onayı kaynak revizyon + hedef kalem/revizyonla sınırlı."""
from decimal import Decimal as D

from django.test import TestCase

from catalog import services as cat
from core.exceptions import BusinessRuleError
from core.tests.factories import World, load
from core.tests.test_findings import stock_pallet_with_revision
from orders import services as orders
from warehouse import services as wh


class MixCompatibilityTests(TestCase):
    def setUp(self):
        self.w = World()
        self.line = self.w.approved_line(qty=D("10"))  # Rev.1: LDPE 98 / SLIP 2, 600 mm, 30 µ
        self.rev_mix = cat.create_revision(self.w.planner, self.w.prod_a, self.w.spec(),
                                           [(self.w.ldpe, D("97")), (self.w.slip, D("3"))], reason="Katkı %3")
        self.pallet_mix = stock_pallet_with_revision(self.w, self.rev_mix)

    def test_same_dimensions_different_additive_ratio_rejected(self):
        with self.assertRaisesMessage(BusinessRuleError, "karışım"):
            wh.reserve(self.w.depot, self.pallet_mix, self.line, 10)

    def test_ratio_notation_is_compared_numerically(self):
        same = cat.create_revision(self.w.planner, self.w.prod_a, self.w.spec(),
                                   [(self.w.slip, D("2.000")), (self.w.ldpe, D("98.0"))], reason="Aynı içerik")
        self.assertEqual(orders.tech_signature(same), orders.tech_signature(self.w.rev_a))
        wh.reserve(self.w.depot, stock_pallet_with_revision(self.w, same), self.line, 10)  # gereksiz ret yok

    def test_explicit_equivalence_allows_and_is_audited(self):
        orders.approve_compatibility(self.w.planner, self.line, self.rev_mix, "Müşteri %3 katkıyı yazılı onayladı")
        res = wh.reserve(self.w.depot, self.pallet_mix, self.line, 10)
        shipment = wh.create_shipment(self.w.depot, self.line.order)
        wh.add_shipment_line(self.w.depot, shipment, res, 10)
        wh.confirm_shipment(self.w.store, load(self.w.depot, shipment))

    def test_equivalence_scope_other_line_rejected(self):
        orders.approve_compatibility(self.w.planner, self.line, self.rev_mix, "Yalnız bu kalem")
        other = orders.approve_line(self.w.planner, self.w.order(qty=D("5")).lines.get(), self.w.rev_a)
        with self.assertRaisesMessage(BusinessRuleError, "karışım"):
            wh.reserve(self.w.depot, self.pallet_mix, other, 5)

    def test_equivalence_lapses_when_line_reapproved_to_new_revision(self):
        orders.approve_compatibility(self.w.planner, self.line, self.rev_mix, "Rev.1'e karşı onay")
        res = wh.reserve(self.w.depot, self.pallet_mix, self.line, 10)
        rev3 = cat.create_revision(self.w.planner, self.w.prod_a, self.w.spec(color="Mavi"), self.w.mix(), reason="Renk")
        orders.approve_line(self.w.planner, self.line, rev3)
        shipment = wh.create_shipment(self.w.depot, self.line.order)
        with self.assertRaises(BusinessRuleError):  # eski rezervasyon yeni hedef revizyona karşı yeniden kontrol edilir
            wh.add_shipment_line(self.w.depot, shipment, res, 10)

    def test_old_reservation_rechecked_at_confirm(self):
        orders.approve_compatibility(self.w.planner, self.line, self.rev_mix, "Geçici")
        res = wh.reserve(self.w.depot, self.pallet_mix, self.line, 10)
        shipment = wh.create_shipment(self.w.depot, self.line.order)
        wh.add_shipment_line(self.w.depot, shipment, res, 10)
        load(self.w.depot, shipment)
        orders.revoke_compatibility(self.w.planner, self.line.compatibility_exceptions.get(), "Müşteri vazgeçti")
        with self.assertRaisesMessage(BusinessRuleError, "karışım"):
            wh.confirm_shipment(self.w.store, shipment)

    def test_transfer_between_different_mixes_rejected(self):
        pallet_std = stock_pallet_with_revision(self.w, self.w.rev_a)
        with self.assertRaisesMessage(BusinessRuleError, "teknik"):
            wh.transfer_boxes(self.w.depot, self.pallet_mix, 5, "Birleştirme", target=pallet_std)

    def test_component_material_difference_rejected(self):
        from catalog.models import Material, MaterialKind
        anti = cat.create_definition(self.w.admin, Material, {"code": "ANTI", "name": "Antistatik",
                                                              "kind": MaterialKind.ADDITIVE, "unit": "kg"})
        rev = cat.create_revision(self.w.planner, self.w.prod_a, self.w.spec(),
                                  [(self.w.ldpe, D("98")), (anti, D("2"))], reason="Katkı türü")
        with self.assertRaisesMessage(BusinessRuleError, "karışım"):
            wh.reserve(self.w.depot, stock_pallet_with_revision(self.w, rev), self.line, 10)
