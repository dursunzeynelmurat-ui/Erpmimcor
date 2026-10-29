"""Demo son düzeltme 6: numune ayrıntısındaki işlem formları hatada yönlendirme yapmaz; girilen değerler ve alan
hataları korunur, ilgili bölüm açık kalır, diğer formların işlem anahtarları ayrı kalır; dosya yeniden seçilmelidir."""
import re
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from core.tests.factories import World
from samples import services as smp
from samples.models import CriterionUnit, PhysicalStatus, SampleKind, StockSource

from .test_samples import quality_user


class SampleFormErrorTests(TestCase):
    def setUp(self):
        self.w = World()
        self.pallet = self.w.stock_pallet(boxes=40)
        self.s = smp.create_sample(self.w.depot, kind=SampleKind.TO_CUSTOMER, customer=self.w.cust_a,
                                   product=self.w.prod_a, quantity=D("2"), unit="kutu", room="Lab",
                                   stock_source=StockSource.FROM_STOCK)
        self.quality = quality_user()
        self.crit = smp.create_criterion(self.quality, name="Kalınlık", unit=CriterionUnit.MICRON)

    def post(self, user, action, data):
        self.client.force_login(user)
        return self.client.post(reverse("samples:sample_action", args=[self.s.pk, action]),
                                {"idem_key": f"hata-{action}", **data})

    def assert_kept(self, resp, action, *texts, error=None):
        self.assertEqual(resp.status_code, 400, f"{action}: yönlendirme yerine form yeniden gösterilmeli")
        html = resp.content.decode()
        self.assertRegex(html, rf'<details[^>]*data-action="{action}"[^>]*\bopen\b', f"{action}: bölüm açık değil")
        for t in texts:
            self.assertIn(t, html, f"{action}: girilen değer kayboldu")
        if error:
            self.assertIn(error, html)
        self.assertIn('class="form-errors"', html)
        keys = re.findall(r'name="idem_key" value="([^"]+)"', html)
        self.assertEqual(len(keys), len(set(keys)), f"{action}: yinelenen işlem anahtarı")
        self.assertIn(f"hata-{action}", keys)  # hatalı formun anahtarı korunur (tekrar gönderim tek kayıt)
        return html

    def test_allocate_unknown_pallet(self):
        resp = self.post(self.w.depot, "stok", {"pallet_code": "PLT-YOK-999", "boxes": "2", "reason": "NOT-STOK"})
        self.assert_kept(resp, "stok", "PLT-YOK-999", "NOT-STOK", error="bulunamadı")

    def test_send_missing_counterpart(self):
        resp = self.post(self.w.depot, "gonder", {"counterpart": "", "note": "NOT-GONDER"})
        self.assert_kept(resp, "gonder", "NOT-GONDER", error="Bu alan zorunludur")
        self.s.refresh_from_db()
        self.assertEqual(self.s.physical_status, PhysicalStatus.STORED)

    def test_send_blocked_by_stock_rule_keeps_values(self):
        resp = self.post(self.w.depot, "gonder", {"counterpart": "Kurgu Kargo 77", "note": ""})
        self.assert_kept(resp, "gonder", "Kurgu Kargo 77", error="Stoktan ayır")

    def test_move_too_long_note(self):
        resp = self.post(self.w.depot, "konum", {"room": "Oda-KORU", "note": "x" * 250})
        self.assert_kept(resp, "konum", "Oda-KORU")

    def test_destroy_without_reason(self):
        self.assert_kept(self.post(self.w.depot, "imha", {"reason": ""}), "imha", error="Bu alan zorunludur")

    def test_measurement_bad_number(self):
        resp = self.post(self.quality, "olcum", {"criterion": self.crit.pk, "value": "abc", "note": "NOT-OLCUM"})
        self.assert_kept(resp, "olcum", "NOT-OLCUM", "abc")

    def test_decision_without_reason(self):
        resp = self.post(self.quality, "karar", {"decision": "uygun", "reason": "", "pallet_codes": "PLT-KORU-1"})
        self.assert_kept(resp, "karar", "PLT-KORU-1", error="Bu alan zorunludur")

    def test_attachment_without_file_asks_reselect(self):
        resp = self.post(self.w.depot, "ek", {"title": "Başlık-KORU"})
        self.assert_kept(resp, "ek", "Başlık-KORU", error="yeniden seçin")
