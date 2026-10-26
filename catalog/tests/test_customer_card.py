"""Müşteri kartı: kısa açılış, mükerrer kontrolü, aşama zorunlulukları, sipariş anlık kopyası, ticari bilgi
görünürlüğü, belge yükleme/indirme güvenliği ve eski `contact` verisinin taşınması."""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog import customers as cs
from catalog.models import Customer, CustomerAccess
from core.exceptions import BusinessRuleError
from core.tests.factories import World

PDF = b"%PDF-1.4\n%kurgusal\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


class CustomerCreateTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_short_create_gives_creator_access(self):
        c = cs.create_customer(self.w.sales, {"code": "C-NEW", "name": "Yeni Kurgu Plastik Ltd.", "country": "TR"})
        self.assertTrue(CustomerAccess.objects.filter(customer=c, user=self.w.sales, valid_until__isnull=True).exists())
        self.assertIn("Varsayılan fatura adresi", cs.readiness(c, "siparis"))

    def test_similar_name_warns_but_does_not_block(self):
        cs.create_customer(self.w.sales, {"code": "C-1", "name": "Akdeniz Ambalaj San. Tic. Ltd. Şti.", "country": "TR"})
        with self.assertRaisesMessage(BusinessRuleError, "Benzer ünvanlı"):
            cs.create_customer(self.w.sales, {"code": "C-2", "name": "Akdeniz Ambalaj Ltd", "country": "TR"})
        c2 = cs.create_customer(self.w.sales, {"code": "C-2", "name": "Akdeniz Ambalaj Ltd", "country": "TR"},
                                confirm_similar=True)
        self.assertEqual(c2.code, "C-2")

    def test_duplicate_tax_id_blocked(self):
        cs.create_customer(self.w.sales, {"code": "C-3", "name": "Bir Firma", "country": "TR",
                                          "tax_id_type": "vkn", "tax_id": "9990000099", "tax_office": "X"})
        with self.assertRaisesMessage(BusinessRuleError, "kayıtlı"):
            cs.create_customer(self.w.sales, {"code": "C-4", "name": "Tamamen Farklı Unvan", "country": "TR",
                                              "tax_id_type": "vkn", "tax_id": "9990000099", "tax_office": "X"})

    def test_turkish_format_only_for_turkey(self):
        self.assertIsNotNone(cs.tax_id_problem("TR", "firma", "vkn", "123", "X"))
        self.assertIsNotNone(cs.tax_id_problem("TR", "firma", "vkn", "1234567890", ""))  # vergi dairesi
        self.assertIsNone(cs.tax_id_problem("TR", "firma", "vkn", "1234567890", "X"))
        self.assertIsNone(cs.tax_id_problem("DE", "firma", "vat", "DE-anything-123", ""))
        self.assertIsNotNone(cs.tax_id_problem("DE", "firma", "vkn", "1234567890", "X"))


class ReadinessAndSnapshotTests(TestCase):
    def setUp(self):
        self.w = World()
        self.c = cs.create_customer(self.w.admin, {"code": "C-R", "name": "Hazırlık Kurgu Ltd.", "country": "TR"})
        from catalog.models import Product, Unit
        from catalog import services as cat
        self.prod = cat.create_definition(self.w.admin, Product, {"code": "R-FILM", "customer": self.c, "name": "Film",
                                                                  "unit": Unit.BOX})
        cat.create_revision(self.w.planner, self.prod, self.w.spec(), self.w.mix())
        cs.grant_access(self.w.admin, self.c, self.w.sales, timezone.localdate(), None, "test")

    def complete(self):
        cs.update_customer(self.w.admin, self.c, {"tax_id_type": "vkn", "tax_id": "9990000123", "tax_office": "K VD",
                                                  "currency": "TRY", "payment_method": "havale", "payment_term_days": 30,
                                                  "delivery_terms": "musteri"})
        cs.save_contact(self.w.admin, self.c, {"name": "Ali", "department": "", "email": "", "phone": "",
                                               "is_primary": True, "is_active": True})
        return cs.save_address(self.w.admin, self.c, {"purpose": "her_ikisi", "name": "Merkez", "country": "TR",
                                                      "region": "", "city": "Kurgu", "postal_code": "", "line": "A Sok. 1",
                                                      "default_billing": True, "default_shipping": True, "is_active": True})

    def test_stage_requirements(self):
        self.assertTrue(cs.readiness(self.c, "siparis"))
        self.assertEqual(cs.readiness(self.c, "taslak"), [])
        self.complete()
        self.assertEqual(cs.readiness(self.c, "siparis"), [])
        self.assertEqual(cs.readiness(self.c, "sevkiyat"), [])

    def test_order_keeps_snapshot_after_card_change(self):
        address = self.complete()
        order = self.w.order(product=self.prod)
        self.assertIn("A Sok. 1", order.shipping_address)
        self.assertEqual(order.payment_term_days, 30)
        cs.save_address(self.w.admin, self.c, {**{f: getattr(address, f) for f in (
            "purpose", "name", "country", "region", "city", "postal_code", "default_billing", "default_shipping",
            "is_active")}, "line": "B Cad. 99"}, address)
        cs.update_customer(self.w.admin, self.c, {"payment_term_days": 90})
        order.refresh_from_db()
        self.assertIn("A Sok. 1", order.shipping_address)
        self.assertEqual(order.payment_term_days, 30)

    def test_single_default_address_and_primary_contact(self):
        self.complete()
        cs.save_contact(self.w.admin, self.c, {"name": "Veli", "department": "", "email": "", "phone": "",
                                               "is_primary": True, "is_active": True})
        self.assertEqual(self.c.contacts.filter(is_primary=True).count(), 1)
        with self.assertRaises(BusinessRuleError):
            cs.save_address(self.w.admin, self.c, {"purpose": "teslimat", "name": "Depo", "country": "TR", "region": "",
                                                   "city": "", "postal_code": "", "line": "x", "default_billing": True,
                                                   "default_shipping": False, "is_active": True})

    def test_po_required_enforced_on_order(self):
        cs.update_customer(self.w.admin, self.c, {"po_required": True})
        with self.assertRaisesMessage(BusinessRuleError, "PO"):
            self.w.order(product=self.prod)


class CommercialVisibilityTests(TestCase):
    def setUp(self):
        self.w = World()
        cs.update_customer(self.w.admin, self.w.cust_a, {"payment_term_days": 47, "currency": "EUR"})

    def test_operations_do_not_see_commercial_section(self):
        self.client.force_login(self.w.depot)
        resp = self.client.get(reverse("catalog:customer_detail", args=[self.w.cust_a.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "47 gün")
        self.assertEqual(self.client.get(reverse("catalog:customer_section_edit",
                                                 args=[self.w.cust_a.pk, "ticari"])).status_code, 403)

    def test_sales_sees_commercial_for_own_customer(self):
        self.client.force_login(self.w.sales)
        self.assertContains(self.client.get(reverse("catalog:customer_detail", args=[self.w.cust_a.pk])), "47 gün")

    def test_no_balance_shown(self):
        self.client.force_login(self.w.admin)
        resp = self.client.get(reverse("catalog:customer_detail", args=[self.w.cust_a.pk]))
        self.assertNotContains(resp, "Borç yok")
        self.assertContains(resp, "finans hareketi tutulmuyor")


@override_settings(MEDIA_ROOT="/tmp/claude-0/mimcor-test-media")
class DocumentTests(TestCase):
    def setUp(self):
        self.w = World()

    def upload(self, name="talimat.pdf", content=PDF, visibility="operasyon", user=None):
        return cs.upload_document(user or self.w.sales, self.w.cust_a, kind="talimat", title="Paketleme talimatı",
                                  version="2", valid_from=None, visibility=visibility,
                                  upload=SimpleUploadedFile(name, content, content_type="application/pdf"))

    def test_type_signature_and_size_checks(self):
        with self.assertRaisesMessage(BusinessRuleError, "Yalnız PDF"):
            self.upload(name="kotu.exe", content=b"MZ\x90\x00")
        with self.assertRaisesMessage(BusinessRuleError, "uyuşmuyor"):
            self.upload(name="sahte.pdf", content=b"<script>alert(1)</script>")
        with self.settings(CUSTOMER_DOCUMENT_MAX_BYTES=10):
            with self.assertRaisesMessage(BusinessRuleError, "en fazla"):
                self.upload()

    def test_download_is_attachment_with_nosniff_and_respects_visibility(self):
        doc = self.upload()
        secret = self.upload(visibility="ticari")
        self.client.force_login(self.w.depot)
        resp = self.client.get(reverse("catalog:document_download", args=[self.w.cust_a.pk, doc.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("attachment", resp["Content-Disposition"])
        self.assertEqual(resp["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("talimat", doc.file.name)  # saklanan ad rastgele; özgün ad yalnız indirmede kullanılır
        self.assertEqual(self.client.get(reverse("catalog:document_download",
                                                 args=[self.w.cust_a.pk, secret.pk])).status_code, 404)

    def test_no_access_no_document(self):
        doc = self.upload()
        from accounts.models import Role, User
        other = User.objects.create_user("9601", "Uzun-Parola-12", full_name="x", role=Role.SALES)
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("catalog:document_download",
                                                 args=[self.w.cust_a.pk, doc.pk])).status_code, 404)


class ContactMigrationTests(TransactionTestCase):
    """Eski serbest metin `contact` alanı kaybolmadan ana ilgili kişiye taşınır."""

    migrate_from = [("catalog", "0003_customer_card"), ("accounts", "0004_remove_legacy_role")]
    migrate_to = [("catalog", "0005_remove_customer_contact_text")]

    def test_contact_text_moved_to_primary_contact(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        OldCustomer = old_apps.get_model("catalog", "Customer")
        OldCustomer.objects.create(code="ESKI-1", name="Eski Kurgu Ltd.", contact="Ayşe Hanım 0 000 000 00 00")
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_to)
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        c = Customer.objects.get(code="ESKI-1")
        self.assertEqual(c.contacts.get(is_primary=True).name, "Ayşe Hanım 0 000 000 00 00")
