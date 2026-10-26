from decimal import Decimal as D

from django.test import TestCase

from catalog.models import LotMovementKind
from core.exceptions import BusinessRuleError
from core.models import QualityStatus
from core.tests.factories import World
from orders.models import LineStatus
from production import services as prod
from production.models import CoilStatus, WorkOrderStatus
from warehouse import services as wh


class CoilGenealogyTests(TestCase):
    def setUp(self):
        self.w = World()
        self.wo = self.w.started_wo(machines=[self.w.ext, self.w.slit])
        self.ext_op, self.slit_op = self.wo.operations.order_by("seq")

    def test_extruder_coil_has_no_source_and_is_tracked(self):
        c = self.w.coil(self.wo, D("200"))
        self.assertTrue(c.code.startswith("BOB-"))
        self.assertEqual(c.remaining_kg, D("200"))
        self.assertEqual(c.machine, self.w.ext)

    def test_split_with_partial_consumption_keeps_links(self):
        src = self.w.coil(self.wo, D("200"))
        prod.start_operation(self.w.foreman, self.slit_op)
        out1 = self.w.coil(self.wo, D("80"), op_seq=2, sources=[(src, D("80"))])
        src.refresh_from_db()
        self.assertEqual(src.remaining_kg, D("120"))
        self.assertEqual(src.status, CoilStatus.PARTIAL)
        out2 = self.w.coil(self.wo, D("118"), op_seq=2, sources=[(src, D("120"))])
        src.refresh_from_db()
        self.assertEqual(src.remaining_kg, D("0"))
        self.assertEqual(src.status, CoilStatus.CONSUMED)
        self.assertEqual({lk.output for lk in src.output_links.all()}, {out1, out2})
        self.assertEqual(out1.source_links.get().source, src)

    def test_cannot_consume_more_than_remaining(self):
        src = self.w.coil(self.wo, D("50"))
        prod.start_operation(self.w.foreman, self.slit_op)
        with self.assertRaises(BusinessRuleError):
            self.w.coil(self.wo, D("40"), op_seq=2, sources=[(src, D("50.001"))])
        src.refresh_from_db()
        self.assertEqual(src.remaining_kg, D("50"))
        self.assertEqual(self.wo.coils.count(), 1)  # çıktı bobini de oluşmadı (atomik)

    def test_conversion_requires_source_and_output_not_heavier(self):
        src = self.w.coil(self.wo, D("50"))
        prod.start_operation(self.w.foreman, self.slit_op)
        with self.assertRaises(BusinessRuleError):
            self.w.coil(self.wo, D("10"), op_seq=2, sources=[])
        with self.assertRaises(BusinessRuleError):
            self.w.coil(self.wo, D("30"), op_seq=2, sources=[(src, D("20"))])

    def test_blocked_coil_cannot_be_source(self):
        src = self.w.coil(self.wo, D("50"))
        prod.set_coil_quality(self.w.foreman, src, QualityStatus.BLOCKED, "Delik")
        prod.start_operation(self.w.foreman, self.slit_op)
        with self.assertRaises(BusinessRuleError):
            self.w.coil(self.wo, D("10"), op_seq=2, sources=[(src, D("10"))])


class ClosureTests(TestCase):
    def setUp(self):
        self.w = World()
        self.wo = self.w.started_wo()

    def _produce(self, kg=D("95")):
        prod.issue_material(self.w.foreman, self.wo, self.w.lot, D("100"))
        coil = self.w.coil(self.wo, kg)
        wh.create_pallet_from_production(self.w.foreman, self.wo, 10, coils=[coil])
        prod.complete_operation(self.w.foreman, self.wo.operations.get())

    def test_balanced_closure(self):
        self._produce()
        prod.close_work_order(self.w.foreman, self.wo, scrap_kg=D("5"), remaining_confirmed=True)
        self.wo.refresh_from_db()
        self.assertEqual(self.wo.status, WorkOrderStatus.CLOSED)
        self.assertEqual(self.wo.closure.difference_kg, D("0"))
        self.assertEqual(self.wo.closure.good_boxes, 10)
        self.assertEqual(self.wo.order_line.status, LineStatus.PRODUCED)

    def test_unbalanced_closure_requires_reason(self):
        self._produce()
        with self.assertRaises(BusinessRuleError):
            prod.close_work_order(self.w.foreman, self.wo, scrap_kg=D("0"), remaining_confirmed=True)
        prod.close_work_order(self.w.foreman, self.wo, scrap_kg=D("0"), remaining_confirmed=True,
                              reason="Fire tartılamadı")
        self.assertEqual(self.wo.closure.difference_kg, D("5"))

    def test_remaining_material_must_be_confirmed_and_returnable(self):
        self._produce(D("60"))
        with self.assertRaises(BusinessRuleError):
            prod.return_material(self.w.foreman, self.wo, self.w.lot, D("100.001"))
        prod.return_material(self.w.foreman, self.wo, self.w.lot, D("35"))
        self.assertEqual(self.w.lot.movements.filter(kind=LotMovementKind.RETURN).count(), 1)
        with self.assertRaises(BusinessRuleError):
            prod.close_work_order(self.w.foreman, self.wo, scrap_kg=D("5"), remaining_confirmed=False)
        prod.close_work_order(self.w.foreman, self.wo, scrap_kg=D("5"), remaining_confirmed=True)
        self.assertEqual(self.wo.closure.issued_kg, D("65"))

    def test_open_operation_blocks_closure(self):
        prod.issue_material(self.w.foreman, self.wo, self.w.lot, D("10"))
        self.w.coil(self.wo, D("10"))
        with self.assertRaises(BusinessRuleError):
            prod.close_work_order(self.w.foreman, self.wo, scrap_kg=D("0"), remaining_confirmed=True)
