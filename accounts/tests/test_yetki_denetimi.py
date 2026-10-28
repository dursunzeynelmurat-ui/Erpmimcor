"""Kabul denetimi: yükseltilmiş kullanıcıların etkin yetkileri ve açık oturumda erişim kaldırma.

- `yetki_denetimi` salt okunurdur; taşınan geniş müşteri erişimi, eski sevk onayı yetkisi kalmış depo rolü, çoklu rol
  genişlemesi ve vardiyasız operatör raporlanır, hiçbir kayıt değişmez.
- Yönetici ekranından erişim daraltılınca/rol düzenlenince aynı kullanıcının açık oturumu ve önceden açılmış formu
  işlem yapamaz (yetki her istekte veritabanından okunur)."""
import io
import json
import uuid
from decimal import Decimal as D

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts import services as acc
from accounts.models import AccessRole, Role, RolePermission, User, UserMachine, UserRole
from catalog.models import AccessSource, CustomerAccess
from core.models import AuditLog
from core.tests.factories import PASSWORD, World, load
from warehouse import services as wh
from warehouse.models import ShipmentStatus


def audit_json(**kw):
    out = io.StringIO()
    call_command("yetki_denetimi", json=True, stdout=out, **kw)
    return {u["personel_no"]: u for u in json.loads(out.getvalue())["kullanicilar"]}


def levels(report):
    return [(f["duzey"], f["bulgu"]) for f in report["bulgular"]]


class UpgradedStateFixture:
    def build(self):
        self.w = World()
        # Taşınan geniş erişim: satışçı C-B'yi toplu taşımadan görüyor (inceleme bekliyor)
        CustomerAccess.objects.filter(user=self.w.sales, customer=self.w.cust_b).update(
            source=AccessSource.MIGRATED, needs_review=True)
        # Eski sürümden kalan: depo rolünde son sevk onayı
        self.depo_role = AccessRole.objects.get(code="depo")
        RolePermission.objects.create(role=self.depo_role, action="shipment.approve", scope="all")
        # Çoklu rol: operatöre ek vardiya kapsamlı talimat rolü
        self.op = User.objects.create_user("9891", PASSWORD, full_name="Op. vardiyasız", role=Role.OPERATOR)
        UserMachine.objects.create(user=self.op, machine=self.w.ext)
        extra = acc.create_role(self.w.admin, code="vardiya_talimat", name="Vardiya talimat", description="",
                                hr_assignable=False, perms={"instruction.view": "shift"})
        acc.grant_role(self.w.admin, self.op, extra, "Yardımcı")


class AuditCommandTests(UpgradedStateFixture, TestCase):
    def setUp(self):
        self.build()

    def test_report_explains_and_flags_without_changing_data(self):
        counts = lambda: (AuditLog.objects.count(), RolePermission.objects.count(),  # noqa: E731
                          CustomerAccess.objects.count(), UserRole.objects.count())
        before = counts()
        rep = audit_json()
        self.assertEqual(counts(), before)  # salt okunur

        sales = rep[self.w.sales.personnel_no]
        self.assertIn(("customer.view", {"customer": ["satis"]}),
                      [(a, s) for a, s in sales["etkin_yetkiler"].items() if a == "customer.view"])
        self.assertTrue(any(lv == "İNCELE" and "C-B" in t for lv, t in levels(sales)))
        access = next(c for c in sales["musteri_erisimleri"] if c["musteri"] == "C-B")
        self.assertEqual((access["kaynak"], access["inceleme_bekliyor"]), ("tasima", True))

        depot = rep[self.w.depot.personnel_no]
        self.assertEqual(depot["etkin_yetkiler"]["shipment.approve"], {"all": ["depo"]})
        self.assertTrue(any(lv == "KURAL" and "shipment.approve" in t and "depo" in t for lv, t in levels(depot)))
        store = rep[self.w.store.personnel_no]  # ambar yetkilisinde aynı yetki kural ihlali değildir
        self.assertFalse(any(lv == "KURAL" for lv, _ in levels(store)))

        op = rep["9891"]
        self.assertEqual(op["etkin_yetkiler"]["instruction.view"],
                         {"machine_shift": ["operator"], "shift": ["vardiya_talimat"]})
        self.assertTrue(any(lv == "İNCELE" and "instruction.view" in t for lv, t in levels(op)))
        self.assertTrue(any(lv == "ENGEL" and "Vardiyası tanımsız" in t for lv, t in levels(op)))
        self.assertTrue(any(lv == "BİLGİ" and "vardiya_talimat" in t for lv, t in levels(op)))

    def test_customer_explanation_and_text_output(self):
        rep = audit_json(kullanici=self.w.sales.personnel_no, musteri="C-B")
        text = rep[self.w.sales.personnel_no]["musteri_aciklama"]["C-B"]
        self.assertIn("Görür", text)
        self.assertIn("Eski sistemden taşınan", text)
        out = io.StringIO()
        call_command("yetki_denetimi", kullanici=self.w.depot.personnel_no, stdout=out)
        self.assertIn("shipment.approve: all ← depo", out.getvalue())
        self.assertIn("[KURAL]", out.getvalue())

    def test_customised_template_role_is_info_not_error(self):
        RolePermission.objects.filter(role__code="satis", action="correction.request").delete()
        rep = audit_json(kullanici=self.w.sales.personnel_no)
        lv = levels(rep[self.w.sales.personnel_no])
        self.assertTrue(any(level == "BİLGİ" and "şablondan farklı" in t for level, t in lv))
        self.assertFalse(any(level == "KURAL" for level, _ in lv))


class OpenSessionRevocationTests(UpgradedStateFixture, TestCase):
    def setUp(self):
        self.build()

    def test_sales_access_removed_on_review_screen_closes_open_session(self):
        order = self.w.order(product=self.w.prod_b, qty=D("10"))
        line = order.lines.get()
        sales = self.client_class()
        sales.force_login(self.w.sales)
        self.assertEqual(sales.get(reverse("orders:order_detail", args=[order.pk])).status_code, 200)
        self.assertEqual(sales.get(reverse("orders:line_change", args=[line.pk])).status_code, 200)  # form açık
        admin = self.client_class()
        admin.force_login(self.w.admin)
        access = CustomerAccess.objects.get(user=self.w.sales, customer=self.w.cust_b, revoked_at__isnull=True)
        admin.post(reverse("catalog:access_review"), {"ids": [access.pk], "islem": "kaldir", "reason": "Bölge değişti",
                                                      "adim": "uygula", "idem_key": uuid.uuid4().hex})
        access.refresh_from_db()
        self.assertIsNotNone(access.revoked_at)
        # aynı açık oturum: detay, liste, arama, eski form
        self.assertEqual(sales.get(reverse("orders:order_detail", args=[order.pk])).status_code, 404)
        self.assertNotContains(sales.get(reverse("orders:order_list")), order.code)
        found = sales.get(reverse("orders:order_list") + "?q=" + order.code).context["orders"]
        self.assertEqual(list(found), [])  # arama kutusu yazılanı gösterir; sonuç boş
        resp = sales.post(reverse("orders:line_change", args=[line.pk]),
                          {"idem_key": uuid.uuid4().hex, "quantity": "5", "reason": "Eski form", "confirm_impacts": "on"})
        self.assertEqual(resp.status_code, 404)
        line.refresh_from_db()
        self.assertEqual(line.quantity, 10)
        # C-A erişimi sürüyor
        other = self.w.order(qty=D("5"))
        self.assertEqual(sales.get(reverse("orders:order_detail", args=[other.pk])).status_code, 200)

    def test_depot_role_edited_by_admin_open_form_cannot_approve(self):
        pallet = self.w.stock_pallet(boxes=10)
        order = self.w.order(qty=D("5"))
        res = wh.reserve(self.w.depot, pallet, order.lines.get(), 5)
        s = wh.create_shipment(self.w.depot, order)
        wh.add_shipment_line(self.w.depot, s, res, 5)
        load(self.w.depot, s)
        depot = self.client_class()
        depot.force_login(self.w.depot)
        self.assertContains(depot.get(reverse("warehouse:shipment_detail", args=[s.pk])), "onayla")  # eski yetkiyle
        admin = self.client_class()
        admin.force_login(self.w.admin)
        admin.post(reverse("accounts:role_action", args=[self.depo_role.pk, "sablon"]),
                   {"reason": "Sevk onayı ambar yetkilisine ait", "idem_key": uuid.uuid4().hex})
        self.assertFalse(self.depo_role.permissions.filter(action="shipment.approve").exists())
        resp = depot.post(reverse("warehouse:shipment_confirm", args=[s.pk]), {"idem_key": uuid.uuid4().hex})
        self.assertEqual(resp.status_code, 403)
        s.refresh_from_db()
        self.assertEqual(s.status, ShipmentStatus.PREPARED)
        self.assertIsNone(s.shipped_at)
