"""Ustabaşı (sample.manage = atanan iş emri) numune oluşturma kapsamı.

Form, ürün sorgusu, kaynak kısayolları ve servis aynı kuralı kullanır: ustabaşı yalnız kendisine *şu anda* atanmış
iş emri bağlamında numune açar; müşteri/ürün seçeneği yalnız o iş emirlerinden gelir ve başka kayıtlara erişim vermez.
"""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from core.tests.factories import World, user
from production.models import WorkOrder
from samples.models import Sample
from warehouse.models import Pallet, PalletMovement

URL = reverse("samples:sample_create")


class ForemanSampleScopeTests(TestCase):
    def setUp(self):
        self.w = World()
        self.pallet = self.w.stock_pallet()                 # prod_a, iş emri self.w.foreman'a atanmış
        self.wo = self.pallet.work_order
        self.coil = self.pallet.pallet_coils.get().coil
        self.other_foreman = user(Role.PRODUCTION, "9014")
        self.other_pallet = self.w.stock_pallet(product=self.w.prod_b)
        self.other_wo = self.other_pallet.work_order
        WorkOrder.objects.filter(pk=self.other_wo.pk).update(assigned_foreman=self.other_foreman)
        self.client.force_login(self.w.foreman)

    def post(self, **over):
        data = {"idem_key": f"fs-{timezone.now().timestamp()}", "kind": "uretim_kontrol", "customer": self.w.cust_a.pk,
                "product": self.w.prod_a.pk, "work_order_code": self.wo.code, "quantity": "3", "unit": "rulo",
                "stock_source": "stok_disi", "occurred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M")}
        data.update(over)
        return self.client.post(URL, {k: v for k, v in data.items() if v is not None})

    # --- yetkili akış ----------------------------------------------------------------------------------------
    def test_form_offers_only_customers_and_products_of_assigned_work_orders(self):
        form = self.client.get(URL).context["form"]
        self.assertEqual(list(form.fields["customer"].queryset), [self.w.cust_a])
        response = self.client.get(reverse("samples:customer_products", args=[self.w.cust_a.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([p["id"] for p in response.json()["products"]], [self.w.prod_a.pk])

    def test_foreman_creates_sample_for_own_work_order_without_stock_change(self):
        before = (PalletMovement.objects.count(), Pallet.objects.get(pk=self.pallet.pk).box_count)
        response = self.post()
        sample = Sample.objects.get()
        self.assertRedirects(response, reverse("samples:sample_detail", args=[sample.pk]))
        self.assertEqual((sample.work_order, sample.customer, sample.product), (self.wo, self.w.cust_a, self.w.prod_a))
        self.assertEqual(before, (PalletMovement.objects.count(), Pallet.objects.get(pk=self.pallet.pk).box_count))
        self.assertIsNone(sample.stock_movement_id)

    def test_source_shortcuts_prefill_known_links(self):
        for param, obj, field, value in [("palet", self.pallet, "pallet_code", self.pallet.code),
                                         ("bobin", self.coil, "coil_code", self.coil.code),
                                         ("is_emri", self.wo, "work_order_code", self.wo.code)]:
            with self.subTest(param):
                response = self.client.get(URL, {param: obj.pk})
                self.assertEqual(response.status_code, 200)
                form = response.context["form"]
                self.assertEqual(form[field].value(), value)
                self.assertEqual(form["work_order_code"].value(), self.wo.code)
                self.assertEqual(form["customer"].value(), self.w.cust_a.pk)
                self.assertEqual(form["product"].value(), self.w.prod_a.pk)
        self.assertFalse(Sample.objects.exists())

    def test_prefilled_pallet_post_creates_sample(self):
        self.post(pallet_code=self.pallet.code, coil_code=self.coil.code)
        sample = Sample.objects.get()
        self.assertEqual((sample.pallet, sample.coil, sample.work_order), (self.pallet, self.coil, self.wo))

    def test_detail_pages_offer_sample_start_for_own_sources(self):
        self.assertContains(self.client.get(reverse("production:coil_detail", args=[self.coil.pk])),
                            "Bu bobinden numune başlat")
        self.assertContains(self.client.get(reverse("warehouse:pallet_detail", args=[self.pallet.pk])),
                            "Bu paletten numune başlat")
        self.assertContains(self.client.get(reverse("production:workorder_detail", args=[self.wo.pk])),
                            f"{URL}?is_emri={self.wo.pk}")

    # --- kapsam dışı ---------------------------------------------------------------------------------------
    def test_out_of_scope_records_hidden_and_unusable(self):
        form = self.client.get(URL).context["form"]
        self.assertNotIn(self.w.cust_b, form.fields["customer"].queryset)
        self.assertEqual(self.client.get(reverse("samples:customer_products", args=[self.w.cust_b.pk])).status_code,
                         404)
        for param, obj in [("palet", self.other_pallet), ("bobin", self.other_pallet.pallet_coils.get().coil),
                           ("is_emri", self.other_wo)]:
            self.assertEqual(self.client.get(URL, {param: obj.pk}).status_code, 404)
        # Ustabaşı sayfasında başka iş emrinin paletine numune düğmesi yok
        self.assertNotContains(self.client.get(reverse("warehouse:pallet_detail", args=[self.other_pallet.pk])),
                               "Bu paletten numune başlat")

    def test_tampered_post_cannot_reach_other_work_order_customer_or_product(self):
        attempts = {
            "başka iş emri": dict(customer=self.w.cust_b.pk, product=self.w.prod_b.pk,
                                  work_order_code=self.other_wo.code),
            "başka müşteri, kendi iş emri": dict(customer=self.w.cust_b.pk, product=self.w.prod_b.pk),
            "kendi müşteri, başka ürün": dict(product=self.w.prod_b.pk),
            "iş emri yok": dict(work_order_code=""),
            "başka iş emrinin paleti": dict(pallet_code=self.other_pallet.code),
            "başka iş emrinin bobini": dict(coil_code=self.other_pallet.pallet_coils.get().coil.code),
            "başka siparişin kalemi": dict(order_line_code=self.w.approved_line().code),
        }
        for label, over in attempts.items():
            with self.subTest(label):
                response = self.post(**over)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
        self.assertFalse(Sample.objects.exists())

    def test_unknown_and_foreign_codes_get_same_message(self):
        foreign = self.post(pallet_code=self.other_pallet.code).context["form"].errors["pallet_code"]
        unknown = self.post(pallet_code="YOK-123").context["form"].errors["pallet_code"]
        self.assertEqual([e.replace(self.other_pallet.code, "X") for e in foreign],
                         [e.replace("YOK-123", "X") for e in unknown])

    def test_stale_form_after_reassignment_is_rejected(self):
        form_page = self.client.get(URL, {"is_emri": self.wo.pk})
        self.assertEqual(form_page.status_code, 200)
        WorkOrder.objects.filter(pk=self.wo.pk).update(assigned_foreman=self.other_foreman)   # devir
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertFalse(Sample.objects.exists())

    def test_customer_option_grants_no_customer_or_order_access(self):
        self.assertIn(self.client.get(reverse("catalog:customer_detail", args=[self.w.cust_a.pk])).status_code,
                      (403, 404))
        self.assertIn(self.client.get(reverse("catalog:customer_list")).status_code, (200, 403))
        listing = self.client.get(reverse("catalog:customer_list"))
        if listing.status_code == 200:
            self.assertNotContains(listing, self.w.cust_a.name)

    def test_foreman_still_cannot_allocate_stock_or_decide(self):
        self.post()
        sample = Sample.objects.get()
        for action in ("stok", "karar"):
            response = self.client.post(reverse("samples:sample_action", args=[sample.pk, action]),
                                        {"idem_key": f"x-{action}", "pallet_code": self.pallet.code, "boxes": "1",
                                         "decision": "uygun", "reason": "x"})
            self.assertEqual(response.status_code, 403)
        self.assertEqual(Pallet.objects.get(pk=self.pallet.pk).box_count, self.pallet.box_count)


    def test_service_applies_same_rule_without_form(self):
        from decimal import Decimal
        from core.exceptions import BusinessRuleError
        from samples import services
        base = dict(kind="uretim_kontrol", quantity=Decimal("1"), unit="rulo", stock_source="stok_disi")
        for label, kw in {
            "iş emri yok": dict(customer=self.w.cust_a, product=self.w.prod_a),
            "başka iş emri": dict(customer=self.w.cust_b, product=self.w.prod_b, work_order=self.other_wo),
            "başka iş emrinin paleti": dict(customer=self.w.cust_a, product=self.w.prod_a, work_order=self.wo,
                                            pallet=self.other_pallet),
        }.items():
            with self.subTest(label), self.assertRaises(BusinessRuleError):
                services.create_sample(self.w.foreman, **base, **kw)
        sample = services.create_sample(self.w.foreman, **base, customer=self.w.cust_a, product=self.w.prod_a,
                                        work_order=self.wo, pallet=self.pallet)
        self.assertEqual(sample.work_order, self.wo)

    def test_revision_choices_limited_to_work_order_without_measurements(self):
        form = self.client.get(URL, {"is_emri": self.wo.pk}).context["form"]
        revisions = list(form.fields["revision"].queryset)
        self.assertEqual(revisions, [self.wo.recipe_revision])
        label = form.fields["revision"].label_from_instance(revisions[0])
        self.assertNotIn("mm", label)
        self.assertNotIn("µ", label)


class OtherRolesUnchangedTests(TestCase):
    """Depo (sample.manage=all) akışı değişmez: iş emri bağlantısı olmadan da müşteri/ürün seçer."""

    def setUp(self):
        self.w = World()
        self.client.force_login(self.w.depot)

    def test_depot_sees_all_customers_and_creates_without_work_order(self):
        form = self.client.get(URL).context["form"]
        self.assertEqual(set(form.fields["customer"].queryset), {self.w.cust_a, self.w.cust_b})
        response = self.client.post(URL, {"idem_key": "depo-1", "kind": "musteri_referans",
                                          "customer": self.w.cust_b.pk, "quantity": "1", "unit": "adet",
                                          "occurred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M")})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Sample.objects.get().customer, self.w.cust_b)
