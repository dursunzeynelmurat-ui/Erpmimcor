"""Demo son düzeltme 5: makine talimatında reçete bileşeninin uygulama notu (extruder karışımında) ve ürüne özel
ambalaj/etiket notu (son operasyonda, müşteri genel talimatından ayrı başlıkla). Matbaa ve diğer operasyonlara karışım
girmez; basılmış eski talimatın içeriği geriye dönük değişmez."""
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from catalog import services as cat
from catalog.models import Customer
from core.tests.factories import World
from orders import services as orders
from production import instructions

NOTE = "UYGNOT-SLIP-ILK-10-DK"
PACK = "URUNE-OZEL-AMBALAJ-KOLI-ICI-KAGIT"
GENERAL = "MUSTERI-GENEL-PAKET-STREC"


class InstructionNotesTests(TestCase):
    def setUp(self):
        self.w = World()
        Customer.objects.filter(pk=self.w.cust_a.pk).update(packing_instructions=GENERAL)
        rev = cat.create_revision(self.w.planner, self.w.prod_a, self.w.spec(packing_notes=PACK), self.w.mix(),
                                  reason="Notlu revizyon", notes={self.w.slip.pk: NOTE})
        order = self.w.order(qty=D("10"))
        line = orders.approve_line(self.w.planner, order.lines.get(), rev)
        self.wo = self.w.work_order(line=line, machines=[self.w.ext, self.w.prn, self.w.slit])
        self.ext, self.prn, self.last = [self.wo.operations.get(seq=n) for n in (1, 2, 3)]

    def test_extruder_mix_carries_application_note_print_has_no_mix(self):
        mix = {m["malzeme"]: m for m in instructions.technical_info(self.ext)["karisim"]}
        self.assertEqual(mix["SLIP"]["not"], NOTE)
        self.assertNotIn("not", mix["LDPE"])  # boş not yazılmaz
        for op in (self.prn, self.last):
            self.assertNotIn("karisim", instructions.technical_info(op))

    def test_last_operation_has_product_and_customer_packing_separately(self):
        last = instructions.build_snapshot(self.last)
        self.assertEqual((last["urun_ambalaj"], last["paketleme"]), (PACK, GENERAL))
        first = instructions.build_snapshot(self.ext)
        self.assertNotIn("urun_ambalaj", first)

    def test_html_and_print_views_show_notes(self):
        ins_ext = instructions.issue_for_print(self.w.foreman, self.ext)
        ins_last = instructions.issue_for_print(self.w.foreman, self.last)
        self.client.force_login(self.w.foreman)
        html = self.client.get(reverse("production:instruction_view", args=[ins_ext.pk])).content.decode()
        self.assertIn(NOTE, html)
        html = self.client.get(reverse("production:instruction_view", args=[ins_last.pk])).content.decode()
        self.assertIn("Ürüne özel ambalaj / etiket notu", html)
        self.assertIn("Müşteri genel paketleme talimatı", html)
        self.assertIn(PACK, html)
        self.assertIn(GENERAL, html)
        self.client.force_login(self.w.planner)
        html = self.client.get(reverse("production:workorder_detail", args=[self.wo.pk])).content.decode()
        self.assertIn(NOTE, html)
        self.assertIn(PACK, html)

    def test_printed_instruction_is_not_rewritten(self):
        v1 = instructions.issue_for_print(self.w.foreman, self.last)
        Customer.objects.filter(pk=self.w.cust_a.pk).update(packing_instructions="YENI-GENEL-TALIMAT")
        v2 = instructions.issue_for_print(self.w.foreman, self.last)
        v1.refresh_from_db()
        self.assertEqual((v1.version, v2.version), (1, 2))
        self.assertEqual(v1.snapshot["paketleme"], GENERAL)
        self.assertEqual(v2.snapshot["paketleme"], "YENI-GENEL-TALIMAT")
