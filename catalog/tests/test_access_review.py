"""Müşteri erişimi kaynağı ve yönetici incelemesi; süresi dolan vekâletin tüm yüzeylerde kapanması."""
import datetime
import uuid
from decimal import Decimal as D

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from catalog import customers as cs
from catalog.models import AccessSource, Customer, CustomerAccess
from core.exceptions import BusinessRuleError
from core.models import AuditLog
from core.tests.factories import PASSWORD, World


def sales(no, name):
    return User.objects.create_user(no, PASSWORD, full_name=name, role=Role.SALES)


class SourceClassificationMigrationTests(TransactionTestCase):
    """Yükseltme erişimleri kesin imzayla ayrılır; imzası tutmayan sistem kayıtları 'belirsiz' kalır, hiçbiri silinmez."""

    def test_classification(self):
        before = [("catalog", "0007_product_package"), ("accounts", "0005_remove_direct_lot_adjust")]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        U = apps.get_model("accounts", "User")
        C = apps.get_model("catalog", "Customer")
        A = apps.get_model("catalog", "CustomerAccess")
        admin = U.objects.create(personnel_no="9901", full_name="Y", password="x")
        s1 = U.objects.create(personnel_no="9902", full_name="S1", password="x")
        c1, c2, c3, c4 = (C.objects.create(code=f"K{i}", name=f"Kurgu {i}") for i in range(4))
        today = datetime.date(2026, 10, 1)
        A.objects.create(customer=c1, user=s1, valid_from=today,
                         reason="Yükseltme: önceki sürümde satış tüm müşterileri görüyordu")
        A.objects.create(customer=c2, user=s1, valid_from=today, granted_by=s1, reason="Müşteriyi açan kullanıcı")
        A.objects.create(customer=c3, user=s1, valid_from=today, granted_by=admin, valid_until=datetime.date(2026, 11, 1))
        A.objects.create(customer=c4, user=s1, valid_from=today, reason="Elle eklenmiş, veren yok")
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        got = {a.customer.code: (a.source, a.needs_review, a.revoked_at) for a in CustomerAccess.objects.all()}
        self.assertEqual(got, {"K0": ("tasima", True, None), "K1": ("yeni_musteri", False, None),
                               "K2": ("vekalet", False, None), "K3": ("belirsiz", True, None)})


class ReviewFlowTests(TestCase):
    def setUp(self):
        self.w = World()
        self.s2 = sales("9921", "İkinci Satış")
        # Yükseltmeden gelmiş gibi: sistem verdi, inceleme bekliyor
        self.legacy = [CustomerAccess.objects.create(customer=c, user=self.s2, valid_from=timezone.localdate(),
                                                     source=AccessSource.MIGRATED, needs_review=True,
                                                     reason="Yükseltme: önceki sürümde satış tüm müşterileri görüyordu")
                       for c in (self.w.cust_a, self.w.cust_b)]
        self.client.force_login(self.w.admin)
        self.url = reverse("catalog:access_review")

    def test_preview_changes_nothing_then_apply_revokes_without_deleting_history(self):
        order = self.w.order(customer=self.w.cust_b, product=self.w.prod_b)
        resp = self.client.post(self.url, {"adim": "onizle", "islem": "kaldir", "reason": "Sorumlu değil",
                                           "ids": [self.legacy[1].pk]})
        self.assertContains(resp, "Önizleme")
        self.assertContains(resp, "C-B")
        self.legacy[1].refresh_from_db()
        self.assertIsNone(self.legacy[1].revoked_at)
        self.client.post(self.url, {"adim": "uygula", "islem": "kaldir", "reason": "Sorumlu değil",
                                    "ids": [self.legacy[1].pk], "idem_key": uuid.uuid4().hex})
        self.legacy[1].refresh_from_db()
        self.assertIsNotNone(self.legacy[1].revoked_at)
        self.assertTrue(Customer.objects.filter(pk=self.w.cust_b.pk).exists())
        order.refresh_from_db()
        self.assertTrue(AuditLog.objects.filter(action="customer.access_revoke").exists())
        self.client.force_login(self.s2)
        self.assertEqual(self.client.get(reverse("catalog:customer_detail", args=[self.w.cust_b.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("catalog:customer_detail", args=[self.w.cust_a.pk])).status_code, 200)

    def test_confirm_and_convert_to_temporary(self):
        until = timezone.localdate() + datetime.timedelta(days=30)
        cs.review_accesses(self.w.admin, [self.legacy[0].pk], "sureli", "Vekâlet 30 gün", until)
        cs.review_accesses(self.w.admin, [self.legacy[1].pk], "onayla", "Sorumlu satışçı")
        for a in self.legacy:
            a.refresh_from_db()
            self.assertFalse(a.needs_review)
        self.assertEqual(self.legacy[0].valid_until, until)
        with self.assertRaisesMessage(BusinessRuleError, "bitiş"):
            cs.review_accesses(self.w.admin, [self.legacy[1].pk], "sureli", "x", timezone.localdate() - datetime.timedelta(days=1))

    def test_only_access_managers(self):
        self.client.force_login(self.w.sales)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        with self.assertRaises(Exception):
            cs.review_accesses(self.w.sales, [self.legacy[0].pk], "kaldir", "x")

    def test_pending_reviews_are_listed_for_admin(self):
        self.assertContains(self.client.get(self.url), "İnceleme bekliyor")
        self.assertContains(self.client.get(reverse("core:home")), "İncelenmemiş müşteri erişimleri")

    def test_creator_and_admin_access_kept(self):
        c = cs.create_customer(self.w.sales, {"code": "C-YENI", "name": "Marmara Örnek Ambalaj", "country": "TR"})
        a = CustomerAccess.objects.get(customer=c, user=self.w.sales)
        self.assertEqual((a.source, a.needs_review), (AccessSource.NEW_CUSTOMER, False))
        self.assertEqual(self.client.get(reverse("catalog:customer_detail", args=[c.pk])).status_code, 200)  # yönetici


@override_settings(MEDIA_ROOT="/tmp/claude-0/mimcor-test-media")
class ExpiredDeputyEverywhereTests(TestCase):
    """Dün biten vekâlet: liste, detay, ürün endpoint'i, belge, sipariş, stok, sevkiyat ve palet çıktısında kapalı."""

    def test_expired(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from warehouse import services as wh
        w = World()
        deputy = sales("9931", "Vekil Satış")
        today = timezone.localdate()
        acc = cs.grant_access(w.admin, w.cust_a, deputy, today - datetime.timedelta(days=10),
                              today - datetime.timedelta(days=1), "İzin vekâleti")
        self.assertEqual(acc.source, AccessSource.DEPUTY)
        doc = cs.upload_document(w.sales, w.cust_a, kind="talimat", title="T", version="1", valid_from=None,
                                 visibility="operasyon", upload=SimpleUploadedFile(
                                     "t.pdf", b"%PDF-1.4\n%x\n%%EOF", content_type="application/pdf"))
        order = w.order(qty=D("5"))
        pallet = w.stock_pallet(boxes=10)
        res = wh.reserve(w.depot, pallet, order.lines.get(), 5)
        shipment = wh.create_shipment(w.depot, order)
        wh.add_shipment_line(w.depot, shipment, res, 5)
        self.client.force_login(deputy)
        for url in (reverse("catalog:customer_detail", args=[w.cust_a.pk]),
                    reverse("catalog:document_download", args=[w.cust_a.pk, doc.pk]),
                    reverse("orders:customer_products", args=[w.cust_a.pk]),
                    reverse("orders:order_detail", args=[order.pk]),
                    reverse("warehouse:pallet_detail", args=[pallet.pk]),
                    reverse("warehouse:pallet_label", args=[pallet.pk]),
                    reverse("warehouse:shipment_detail", args=[shipment.pk])):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        for url in (reverse("catalog:customer_list"), reverse("orders:order_list"), reverse("warehouse:stock_summary"),
                    reverse("warehouse:shipment_list")):
            self.assertNotContains(self.client.get(url), "C-A", msg_prefix=url)


class TwoSalespeopleIsolationTests(TestCase):
    def test_each_sees_only_own_customers(self):
        w = World()
        other = sales("9941", "Diğer Satış")
        c = cs.create_customer(other, {"code": "C-ZZ", "name": "Başka Kurgu Plastik", "country": "TR"})
        self.client.force_login(w.sales)
        self.assertNotContains(self.client.get(reverse("catalog:customer_list")), "C-ZZ")
        self.assertEqual(self.client.get(reverse("orders:customer_products", args=[c.pk])).status_code, 404)
        self.client.force_login(other)
        page = self.client.get(reverse("catalog:customer_list"))
        self.assertContains(page, "C-ZZ")
        self.assertNotContains(page, "C-A")
