"""Demo son düzeltme 4: numune bilinen fiziksel kaynağın (bobin/palet) üretim revizyonuna bağlanır; ürünün güncel
revizyonu tahmin olarak kullanılmaz. Çelişen bağlantılar reddedilir; birden fazla revizyondan oluşan palette seçim
veya açık belirsizlik istenir; sonradan eklenen revizyon eski numuneyi değiştirmez."""
from decimal import Decimal as D

from django.test import TestCase

from catalog import services as cat
from core.exceptions import BusinessRuleError
from core.tests.factories import World
from samples import services as smp
from samples.models import RevisionBasis, SampleKind, StockSource
from warehouse import services as wh


class SampleRevisionTests(TestCase):
    def setUp(self):
        self.w = World()
        self.rev30 = self.w.prod_a.current_revision
        self.pallet30 = self.w.stock_pallet(boxes=40)
        self.rev70 = cat.create_revision(self.w.planner, self.w.prod_a, self.w.spec(thickness_micron=D("70")),
                                         self.w.mix(), reason="Yeni iş için 70 µ (kurgusal)")

    def create(self, **kw):
        base = {"kind": SampleKind.RETAINED, "customer": self.w.cust_a, "product": self.w.prod_a,
                "quantity": D("2"), "unit": "kutu", "room": "Numune odası"}
        return smp.create_sample(self.w.depot, **{**base, **kw})

    def test_old_pallet_keeps_its_production_revision(self):
        s = self.create(pallet=self.pallet30)
        self.assertEqual(s.revision, self.rev30)
        self.assertEqual(s.revision_basis, RevisionBasis.PHYSICAL)

    def test_explicit_revision_conflicting_with_pallet_rejected(self):
        with self.assertRaisesMessage(BusinessRuleError, f"Rev.{self.rev30.rev_no}"):
            self.create(pallet=self.pallet30, revision=self.rev70)

    def test_mixed_pallet_requires_choice_or_explicit_uncertainty(self):
        # Teknik imzası aynı (yalnız iç proses notu farklı) revizyonların paletleri aktarımla birleşebilir.
        rev30b = cat.create_revision(self.w.planner, self.w.prod_a, self.w.spec(process_notes="Soğutma 2 kademe"),
                                     self.w.mix(), reason="Proses notu (kurgusal)")
        mixed = self.w.stock_pallet(boxes=10)
        wh.transfer_boxes(self.w.depot, self.pallet30, 5, "Kurgu: birleştirme", target=mixed)
        with self.assertRaisesMessage(BusinessRuleError, "birden fazla"):
            self.create(pallet=mixed)
        with self.assertRaisesMessage(BusinessRuleError, "olası revizyonları arasında değil"):
            self.create(pallet=mixed, revision=self.rev70)
        chosen = self.create(pallet=mixed, revision=rev30b)
        self.assertEqual((chosen.revision, chosen.revision_basis), (rev30b, RevisionBasis.USER))
        unknown = self.create(pallet=mixed, revision_uncertain=True)
        self.assertEqual((unknown.revision, unknown.revision_basis), (None, RevisionBasis.UNCERTAIN))

    def test_conflicting_links_rejected(self):
        wo70 = self.w.started_wo(stock=True)
        coil70 = self.w.coil(wo70)
        with self.assertRaisesMessage(BusinessRuleError, "palette değil"):
            self.create(pallet=self.pallet30, coil=coil70)
        with self.assertRaisesMessage(BusinessRuleError, "palet"):
            self.create(pallet=self.pallet30, work_order=wo70)
        line1, line2 = self.w.approved_line(), self.w.approved_line()
        wo1 = self.w.work_order(line=line1, machines=[self.w.free_extruder()])
        with self.assertRaisesMessage(BusinessRuleError, "sipariş kalemi"):
            self.create(work_order=wo1, order_line=line2)

    def test_later_allocation_from_other_revision_pallet_rejected(self):
        s = self.create(pallet=self.pallet30, stock_source=StockSource.FROM_STOCK)
        pallet70 = self.w.stock_pallet(boxes=10)
        with self.assertRaisesMessage(BusinessRuleError, "revizyon"):
            smp.allocate_from_pallet(self.w.depot, s, pallet70, 2)
        smp.allocate_from_pallet(self.w.depot, s, self.pallet30, 2)

    def test_new_revision_does_not_change_existing_sample(self):
        s = self.create(pallet=self.pallet30)
        cat.create_revision(self.w.planner, self.w.prod_a, self.w.spec(thickness_micron=D("90")), self.w.mix(),
                            reason="Başka iş")
        s.refresh_from_db()
        self.assertEqual(s.revision, self.rev30)

    def test_reference_without_links_uses_current_with_explicit_basis(self):
        s = smp.create_sample(self.w.depot, kind=SampleKind.CUSTOMER_REFERENCE, customer=self.w.cust_a,
                              product=self.w.prod_a, quantity=D("1"), unit="adet")
        self.assertEqual((s.revision, s.revision_basis), (self.rev70, RevisionBasis.PRODUCT_CURRENT))

    def test_form_page_shows_basis_and_mixed_pallet_error(self):
        from django.urls import reverse
        from samples.models import Sample
        self.client.force_login(self.w.depot)
        data = {"idem_key": "rev-1", "kind": SampleKind.RETAINED, "customer": self.w.cust_a.pk,
                "product": self.w.prod_a.pk, "pallet_code": self.pallet30.code, "occurred_at": "2026-10-30T10:00",
                "quantity": "2", "unit": "kutu", "stock_source": StockSource.NOT_FROM_STOCK}
        self.client.post(reverse("samples:sample_create"), data)
        s = Sample.objects.get()
        self.assertEqual(s.revision, self.rev30)
        detail = self.client.get(reverse("samples:sample_detail", args=[s.pk]))
        self.assertContains(detail, f"Rev.{self.rev30.rev_no}")
        self.assertContains(detail, "Fiziksel kaynağın")
