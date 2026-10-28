"""Kabul denetimi: malzeme dengesi ve tolerans sınırları.

Denge: fark = net verilen malzeme − (sağlam + blokeli + ara stok + fire). Net verilen = kg birimli lotlardan bu iş
emrine çıkış − lota iade. Tolerans tabanı yalnız net verilen malzemedir; çıktı veya fire büyüdükçe sınır büyümez.
%3 bir demo kuralıdır (WORK_ORDER_BALANCE_TOLERANCE_PCT), fabrika standardı değildir. Sınır dışı fark otomatik kabul
edilmez: kapanışta gerekçe, kapanıştan sonraki düzeltmede yetkili değerlendirmesi gerekir."""
from decimal import Decimal as D

from django.test import TestCase, override_settings

from core.exceptions import BusinessRuleError
from core.tests.factories import World
from corrections import services as cs
from corrections.models import CorrectionKind
from production import services as prod
from production.tests.test_closure_corrections import closed_work_order, users


@override_settings(WORK_ORDER_BALANCE_TOLERANCE_PCT=3)
class ToleranceBoundaryTests(TestCase):
    def setUp(self):
        self.w = World()

    def run_wo(self, issue=D("100"), returned=None, coil_kg=D("97")):
        wo = self.w.started_wo(machines=[self.w.free_extruder()])
        if issue:
            prod.issue_material(self.w.foreman, wo, self.w.lot, issue)
        if returned:
            prod.return_material(self.w.foreman, wo, self.w.lot, returned)
        self.w.coil(wo, kg=coil_kg)
        prod.complete_operation(self.w.foreman, wo.operations.get())
        wo.refresh_from_db()
        return wo

    def close(self, wo, scrap=D("0"), reason=""):
        return prod.close_work_order(self.w.foreman, wo, scrap_kg=scrap, remaining_confirmed=True, reason=reason)

    def test_100_kg_limit_is_3_kg(self):
        wo = self.run_wo(coil_kg=D("97"))
        self.assertEqual(prod.closure_summary(wo)["issued"], D("100"))
        self.close(wo)  # fark tam 3,000 kg: sınır içinde, gerekçe gerekmez
        self.assertEqual(wo.closure.difference_kg, D("3"))

    def test_just_over_limit_requires_reason(self):
        wo = self.run_wo(coil_kg=D("96.999"))
        with self.assertRaisesMessage(BusinessRuleError, "fark 3.001"):
            self.close(wo)
        self.close(wo, reason="Tartı farkı, ustabaşı kontrol etti")
        self.assertEqual(wo.closure.reason, "Tartı farkı, ustabaşı kontrol etti")

    def test_limit_does_not_grow_with_output(self):
        """Çıktı verilen malzemeden fazlaysa (110 kg) sınır 3,3 kg'ye çıkmaz; −10 kg fark gerekçe ister."""
        wo = self.run_wo(coil_kg=D("110"))
        with self.assertRaisesMessage(BusinessRuleError, "fark -10"):
            self.close(wo)
        wo = self.run_wo(coil_kg=D("103"))
        self.close(wo)  # −3 kg: sınırda
        wo = self.run_wo(coil_kg=D("103.001"))
        with self.assertRaises(BusinessRuleError):
            self.close(wo)

    def test_recorded_scrap_counts_once(self):
        wo = self.run_wo(coil_kg=D("90"))
        with self.assertRaises(BusinessRuleError):
            self.close(wo, scrap=D("6.9"))  # 100 − 96,9 = 3,1
        self.close(wo, scrap=D("7"))
        self.assertEqual((wo.closure.scrap_kg, wo.closure.difference_kg), (D("7"), D("3")))

    def test_partial_return_lowers_base(self):
        wo = self.run_wo(issue=D("100"), returned=D("20"), coil_kg=D("77.6"))
        s = prod.closure_summary(wo)
        self.assertEqual(s["issued"], D("80"))
        self.close(wo)  # 80 × %3 = 2,4
        wo = self.run_wo(issue=D("100"), returned=D("20"), coil_kg=D("77.5"))
        with self.assertRaisesMessage(BusinessRuleError, "fark 2.5"):
            self.close(wo)

    def test_zero_input_requires_reason(self):
        wo = self.run_wo(issue=None, coil_kg=D("10"))
        with self.assertRaisesMessage(BusinessRuleError, "malzeme çıkışı kaydedilmemiş"):
            self.close(wo)
        self.close(wo, reason="Makine başındaki önceki işten kalan malzeme; çıkış ayrıca düzeltilecek")
        self.assertEqual(wo.closure.issued_kg, D("0"))


@override_settings(WORK_ORDER_BALANCE_TOLERANCE_PCT=3)
class CorrectionAfterClosureTests(TestCase):
    def setUp(self):
        self.w = World()
        self.mgr, self.appr = users()
        self.wo, self.mv, self.coil, self.pallet = closed_work_order(self.w)  # 100 verilen, 95 sağlam, 5 fire

    def test_correction_within_tolerance_shown_without_review(self):
        cr = cs.request_correction(self.mgr, CorrectionKind.MATERIAL_ISSUE, self.mv, D("102"), "Fiş 102 kg")
        cs.approve(self.appr, cr)
        self.wo.refresh_from_db()
        st = prod.closure_status(self.wo)
        self.assertTrue(st["changed"])
        self.assertEqual((st["current"]["difference"], st["limit"]), (D("2"), D("3.06")))
        self.assertFalse(st["needs_review"])
        self.assertEqual(self.wo.closure.issued_kg, D("100"))

    def test_correction_outside_tolerance_needs_review_and_is_not_auto_accepted(self):
        cr = cs.request_correction(self.mgr, CorrectionKind.MATERIAL_ISSUE, self.mv, D("110"), "Fiş 110 kg")
        cs.approve(self.appr, cr)
        self.wo.refresh_from_db()
        st = prod.closure_status(self.wo)
        # 3,3 kg = düzeltilmiş net verilen 110 kg × %3 (taban çıktı değil, verilen malzemedir)
        self.assertEqual((st["current"]["issued"], st["current"]["difference"], st["limit"]),
                         (D("110"), D("10"), D("3.3")))
        self.assertTrue(st["needs_review"])
        self.assertEqual((self.wo.closure.issued_kg, self.wo.closure.difference_kg), (D("100"), D("0")))
