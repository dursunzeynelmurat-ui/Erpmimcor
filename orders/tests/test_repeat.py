"""Aşama 4: amaç/yöntem ayrımı, repete ve revizyonlu repete akışları."""
import threading
import uuid
from decimal import Decimal as D

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from catalog import customers as cs
from catalog import recipe_drafts as rd
from catalog.models import Product
from core.exceptions import BusinessRuleError
from core.tests.factories import PASSWORD, World, load
from orders import repeat
from orders import services as orders
from orders.models import ChangeStatus, CreationMethod, LineStatus, OrderStatus, SalesOrder
from warehouse import services as wh


def shipped_source(w):
    """Kaynak sipariş: onaylı, rezervasyonlu, sevk edilmiş kalem (kopyalanmaması gereken kayıtlarla)."""
    pallet = w.stock_pallet(boxes=40)
    order = w.order(qty=D("10"))
    line = orders.approve_line(w.planner, order.lines.get(), w.prod_a.current_revision)
    res = wh.reserve(w.depot, pallet, line, 10)
    s = wh.create_shipment(w.depot, order)
    wh.add_shipment_line(w.depot, s, res, 10)
    wh.confirm_shipment(w.store, load(w.depot, s))
    return order, line


class RepeatServiceTests(TestCase):
    def setUp(self):
        self.w = World()
        self.src_order, self.src = shipped_source(self.w)

    def test_repeat_copies_nothing_operational(self):
        order = repeat.create_repeat_order(
            self.w.sales, customer=self.w.cust_a, purpose="ticari", delivery_confirmed=True,
            items=[{"source_line": self.src, "quantity": D("25"), "due_date": None}])
        line = order.lines.get()
        self.assertEqual((order.creation_method, order.status, order.copied_from), (CreationMethod.REPEAT,
                                                                                    OrderStatus.DRAFT, self.src_order))
        self.assertEqual((line.source_line, line.quantity, line.status, line.approved_revision),
                         (self.src, D("25"), LineStatus.WAITING_PLANNING, None))
        self.assertFalse(line.reservations.exists() or line.shipment_lines.exists() or line.work_orders.exists())
        self.assertFalse(line.change_requests.exists())
        with self.assertRaisesMessage(BusinessRuleError, "Teslimat"):
            repeat.create_repeat_order(self.w.sales, customer=self.w.cust_a, purpose="ticari",
                                       items=[{"source_line": self.src, "quantity": D("5")}])

    def test_inactive_product_and_other_customer_rejected(self):
        other = User.objects.create_user("9941", PASSWORD, full_name="B satış", role=Role.SALES)
        cs.grant_access(self.w.admin, self.w.cust_b, other, timezone.localdate(), None, "B")
        with self.assertRaisesMessage(BusinessRuleError, "tekrar edilebilir kalemler arasında değil"):
            repeat.create_repeat_order(other, customer=self.w.cust_b, purpose="ticari", delivery_confirmed=True,
                                       items=[{"source_line": self.src, "quantity": D("5")}])
        from catalog import services as cat
        cat.set_active(self.w.admin, self.w.prod_a, False, "Kalktı")
        from orders.models import OrderLine
        self.assertIn("Ürün pasif", " ".join(repeat.source_warnings(OrderLine.objects.get(pk=self.src.pk))))
        with self.assertRaisesMessage(BusinessRuleError, "pasif"):
            repeat.create_repeat_order(self.w.sales, customer=self.w.cust_a, purpose="ticari", delivery_confirmed=True,
                                       items=[{"source_line": self.src, "quantity": D("5")}])

    def test_replacement_line_not_repeatable(self):
        self.assertNotIn(self.src.pk, [ln.pk for ln in repeat.candidate_lines(self.w.sales, self.w.cust_a)
                                       if ln.replacement_for_id])
        ids = [ln.pk for ln in repeat.candidate_lines(self.w.sales, self.w.cust_a)]
        self.assertIn(self.src.pk, ids)

    def test_revised_repeat_side_by_side_and_new_revision(self):
        order = repeat.create_repeat_order(
            self.w.sales, customer=self.w.cust_a, purpose="numune", charge="ucretsiz", delivery_confirmed=True,
            items=[{"source_line": self.src, "quantity": D("5"), "changes": {"width_mm": D("650"), "color": "Mavi"},
                    "reason": "Müşteri 650 mm mavi istedi"}])
        self.assertEqual(order.creation_method, CreationMethod.REVISED_REPEAT)
        line = order.lines.get()
        cr = line.change_requests.get()
        rows = {r["label"]: (r["old"], r["new"]) for r in repeat.comparison(cr)}
        self.assertEqual(rows["En"], ("600 mm", "650 mm"))
        self.assertEqual(rows["Renk adı"], ("Şeffaf", "Mavi"))
        orders.approve_order(self.w.sales, order)
        with self.assertRaisesMessage(BusinessRuleError, "karara bağlanmadan"):
            orders.approve_line(self.w.planner, line, self.w.prod_a.current_revision)
        with self.assertRaises(Exception):
            repeat.start_new_revision(self.w.sales, cr)  # satış karar veremez
        draft = repeat.start_new_revision(self.w.planner, cr)
        values = rd.spec_from_json(draft.spec_values)
        self.assertEqual((values["width_mm"], values["color"]), (D("650"), "Mavi"))
        self.assertEqual([(ln.material.code, ln.percentage) for ln in draft.lines.all()],
                         [("LDPE", D("98")), ("SLIP", D("2"))])  # karışım temel revizyondan
        rev = rd.approve_draft(self.w.planner, draft)
        cr.refresh_from_db()
        self.assertEqual((cr.status, cr.result_revision), (ChangeStatus.NEW_REVISION, rev))
        orders.approve_line(self.w.planner, line, rev)
        self.src.refresh_from_db()
        self.assertEqual(self.src.approved_revision.rev_no, 1)  # önceki sipariş etkilenmez

    def test_new_product_code_same_customer(self):
        order = repeat.create_repeat_order(
            self.w.sales, customer=self.w.cust_a, purpose="ticari", delivery_confirmed=True,
            items=[{"source_line": self.src, "quantity": D("5"), "changes": {"thickness_micron": D("40")},
                    "reason": "Kalın"}])
        cr = order.lines.get().change_requests.get()
        with self.assertRaisesMessage(BusinessRuleError, "zaten kullanılıyor"):
            repeat.start_new_product(self.w.planner, cr, code="A-FILM", name="")
        draft = repeat.start_new_product(self.w.planner, cr, code="A-FILM-40", name="Film 40µ")
        new = Product.objects.get(code="A-FILM-40")
        self.assertEqual(new.customer, self.w.cust_a)
        line = order.lines.get()
        self.assertEqual(line.product, new)
        self.src.refresh_from_db()
        self.assertEqual(self.src.product, self.w.prod_a)
        rd.approve_draft(self.w.planner, draft)
        cr.refresh_from_db()
        self.assertEqual(cr.status, ChangeStatus.NEW_PRODUCT)

    def test_sales_cannot_change_mix_or_unknown_fields(self):
        with self.assertRaisesMessage(BusinessRuleError, "değiştirilemez"):
            repeat.create_repeat_order(
                self.w.sales, customer=self.w.cust_a, purpose="ticari", delivery_confirmed=True,
                items=[{"source_line": self.src, "quantity": D("5"), "changes": {"karisim": "LDPE 100"}, "reason": "x"}])
        with self.assertRaisesMessage(BusinessRuleError, "gerekçe"):
            repeat.create_repeat_order(
                self.w.sales, customer=self.w.cust_a, purpose="ticari", delivery_confirmed=True,
                items=[{"source_line": self.src, "quantity": D("5"), "changes": {"width_mm": D("700")}}])

    def test_reject_keeps_old_spec(self):
        order = repeat.create_repeat_order(
            self.w.sales, customer=self.w.cust_a, purpose="ticari", delivery_confirmed=True,
            items=[{"source_line": self.src, "quantity": D("5"), "changes": {"width_mm": D("700")}, "reason": "x"}])
        orders.approve_order(self.w.sales, order)
        line = order.lines.get()
        repeat.reject(self.w.planner, line.change_requests.get(), "Makine eni yetmez")
        orders.approve_line(self.w.planner, line, self.w.prod_a.current_revision)


class RepeatWizardViewTests(TestCase):
    def setUp(self):
        self.w = World()
        self.src_order, self.src = shipped_source(self.w)
        self.client.force_login(self.w.sales)
        self.url = reverse("orders:repeat_order")

    def test_wizard_preselects_source_and_creates_once(self):
        r = self.client.get(self.url + f"?musteri={self.w.cust_a.pk}&kaynak={self.src_order.pk}")
        self.assertContains(r, self.src.code)
        self.assertContains(r, "Rev.1")
        base = {"idem_key": "rep-1", "musteri": self.w.cust_a.pk, f"sel_{self.src.pk}": "on",
                f"qty_{self.src.pk}": "12", "purpose": "deneme"}
        r = self.client.post(self.url, {**base, "git": "ileri"})
        self.assertEqual(r.context["step"], 3)
        self.assertContains(r, "Aynı teknik özellikle repete")
        r = self.client.post(self.url, {**base, "git": "kaydet"})
        self.assertEqual(r.context["step"], 3)  # teslimat doğrulaması olmadan kayıt yok
        self.assertEqual(SalesOrder.objects.filter(creation_method="repete").count(), 0)
        for _ in range(2):  # çift gönderim
            self.client.post(self.url, {**base, "git": "kaydet", "delivery_confirmed": "on"})
        self.assertEqual(SalesOrder.objects.filter(creation_method="repete").count(), 1)

    def test_revised_wizard_shows_changes_to_planning(self):
        p = self.src.pk
        base = {"idem_key": "rep-2", "musteri": self.w.cust_a.pk, f"sel_{p}": "on", f"qty_{p}": "8",
                "purpose": "ticari", f"chg_{p}": "on", f"s{p}_width_mm": "650", f"s{p}_thickness_micron": "30",
                f"s{p}_color": "Şeffaf", f"s{p}_length_m": "", f"s{p}_gusset_type": "yan"}
        r = self.client.post(self.url, {**base, "git": "ileri"})
        self.assertEqual(r.context["step"], 2)  # gerekçe yok; körük için alanlar kalır
        self.assertContains(r, "gerekçesini yazın")
        base[f"reason_{p}"] = "Müşteri yan körük istedi"
        r = self.client.post(self.url, {**base, "git": "ileri"})
        self.assertEqual(r.context["step"], 3)
        self.assertContains(r, "Revizyonlu repete")
        self.assertContains(r, "650 mm")
        r = self.client.post(self.url, {**base, "git": "kaydet", "delivery_confirmed": "on"})
        order = SalesOrder.objects.get(creation_method="revizyonlu_repete")
        cr = order.lines.get().change_requests.get()
        self.client.force_login(self.w.planner)
        page = self.client.get(reverse("orders:change_request", args=[cr.pk]))
        self.assertContains(page, "600 mm")
        self.assertContains(page, "650 mm")
        self.assertContains(page, "Yan körük")
        self.assertContains(page, "Müşteri yan körük istedi")
        self.assertNotContains(page, "LDPE")

    def test_other_customer_hidden(self):
        other = User.objects.create_user("9942", PASSWORD, full_name="B satış", role=Role.SALES)
        cs.grant_access(self.w.admin, self.w.cust_b, other, timezone.localdate(), None, "B")
        self.client.force_login(other)
        r = self.client.get(self.url + f"?musteri={self.w.cust_a.pk}")
        self.assertEqual(r.context["step"], 1)  # yetkisiz müşteri seçilemez
        r = self.client.post(self.url, {"idem_key": uuid.uuid4().hex, "musteri": self.w.cust_b.pk,
                                        f"sel_{self.src.pk}": "on", f"qty_{self.src.pk}": "5", "purpose": "ticari",
                                        "git": "kaydet", "delivery_confirmed": "on"})
        self.assertFalse(SalesOrder.objects.filter(creation_method__in=["repete", "revizyonlu_repete"]).exists())


class ConcurrentDecisionTests(TransactionTestCase):
    def test_parallel_new_revision_decisions_single_draft(self):
        w = World()
        _, src = shipped_source(w)
        order = repeat.create_repeat_order(
            w.sales, customer=w.cust_a, purpose="ticari", delivery_confirmed=True,
            items=[{"source_line": src, "quantity": D("5"), "changes": {"width_mm": D("650")}, "reason": "x"}])
        cr = order.lines.get().change_requests.get()
        barrier, out = threading.Barrier(2), []

        def run():
            try:
                barrier.wait()
                out.append(repeat.start_new_revision(w.planner, cr).pk)
            except BusinessRuleError as exc:
                out.append(exc.message)
            finally:
                connection.close()
        ts = [threading.Thread(target=run) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        from catalog.models import RecipeDraft
        self.assertEqual(RecipeDraft.objects.filter(product=w.prod_a).count(), 1, out)
