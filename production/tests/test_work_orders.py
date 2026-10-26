from decimal import Decimal as D

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from catalog import services as cat
from catalog.models import Unit
from core.exceptions import BusinessRuleError
from core.models import AuditLog
from core.tests.factories import World
from orders.models import LineStatus, OrderLine, SalesOrder
from production import services as prod
from production.models import OperationStatus, WorkOrderPurpose, WorkOrderStatus


class WorkOrderTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_stock_production_without_sales_order(self):
        before = SalesOrder.objects.count()
        wo = prod.create_work_order(self.w.planner, product=self.w.prod_a, revision=self.w.rev_a,
                                    machines=[self.w.ext], target_quantity=D("40"), unit=Unit.BOX)
        self.assertEqual(wo.purpose, WorkOrderPurpose.STOCK)
        self.assertIsNone(wo.order_line)
        self.assertEqual(SalesOrder.objects.count(), before)

    def test_unapproved_line_cannot_get_work_order(self):
        line = self.w.order().lines.get()
        with self.assertRaises(BusinessRuleError):
            prod.create_work_order(self.w.planner, order_line=line, machines=[self.w.ext], target_quantity=D("1"),
                                   unit=Unit.BOX)

    def test_work_order_keeps_its_revision_after_recipe_change(self):
        wo = self.w.work_order()
        rev2 = cat.create_revision(self.w.planner, self.w.prod_a, self.w.spec(thickness_micron=D("40")), self.w.mix(),
                                   reason="değişiklik")
        wo.refresh_from_db()
        self.assertEqual(wo.recipe_revision, self.w.rev_a)
        self.assertNotEqual(wo.recipe_revision, rev2)

    def test_first_operation_must_be_extruder(self):
        line = self.w.approved_line()
        with self.assertRaises(BusinessRuleError):
            prod.create_work_order(self.w.planner, order_line=line, machines=[self.w.prn, self.w.ext],
                                   target_quantity=D("1"), unit=Unit.BOX)

    def test_full_flow_order_planning_foreman(self):
        wo = self.w.work_order(machines=[self.w.ext, self.w.prn, self.w.slit])
        ops = list(wo.operations.order_by("seq"))
        self.assertEqual([o.machine_type for o in ops], ["extruder", "matbaa", "dilimleme"])
        prod.start_work_order(self.w.foreman, wo)
        wo.refresh_from_db()
        self.assertEqual(wo.status, WorkOrderStatus.STARTED)
        self.assertEqual(OrderLine.objects.get(pk=wo.order_line_id).status, LineStatus.IN_PRODUCTION)
        ops[0].refresh_from_db()
        self.assertEqual(ops[0].status, OperationStatus.RUNNING)
        with self.assertRaises(BusinessRuleError):
            prod.start_operation(self.w.foreman, ops[2])  # 2. operasyon başlamadan 3. başlayamaz
        prod.start_operation(self.w.foreman, ops[1])
        with self.assertRaises(BusinessRuleError):
            prod.complete_operation(self.w.foreman, ops[1])  # önce extruder tamamlanmalı
        prod.complete_operation(self.w.foreman, ops[0])
        prod.complete_operation(self.w.foreman, ops[1])
        self.assertTrue(AuditLog.objects.filter(action="operation.complete", user=self.w.foreman).exists())

    def test_cannot_start_twice(self):
        wo = self.w.work_order()
        prod.start_work_order(self.w.foreman, wo)
        with self.assertRaises(BusinessRuleError):
            prod.start_work_order(self.w.foreman, wo)

    def test_service_level_permission(self):
        wo = self.w.work_order()
        with self.assertRaises(PermissionDenied):
            prod.start_work_order(self.w.sales, wo)
        with self.assertRaises(PermissionDenied):
            prod.create_work_order(self.w.depot, product=self.w.prod_a, revision=self.w.rev_a, machines=[self.w.ext],
                                   target_quantity=D("1"), unit=Unit.BOX)


class WorkOrderViewTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_planner_creates_work_order_via_form(self):
        line = self.w.approved_line()
        self.client.force_login(self.w.planner)
        resp = self.client.post(reverse("production:workorder_create_for_line", args=[line.pk]), {
            "idem_key": "wo-1", "target_quantity": "50", "unit": "kutu",
            "machine_1": self.w.ext.pk, "machine_2": self.w.prn.pk, "foreman": self.w.foreman.pk})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(line.work_orders.count(), 1)

    def test_double_click_start_is_idempotent(self):
        wo = self.w.work_order()
        self.client.force_login(self.w.foreman)
        url = reverse("production:workorder_start", args=[wo.pk])
        self.client.post(url, {"idem_key": "start-1"})
        resp = self.client.post(url, {"idem_key": "start-1"}, follow=True)
        self.assertContains(resp, "daha önce kaydedilmişti")
        self.assertEqual(AuditLog.objects.filter(action="workorder.start").count(), 1)

    def test_pages_render(self):
        """Her sayfa yetkili rolde açılır; yetkisiz rolde 403 (kapsam dışı kayıt 404) döner — boş/çöken sayfa yok."""
        from accounts.permissions import can
        wo = self.w.work_order()
        pages = [(reverse("core:home"), None, None), (reverse("orders:order_list"), "order.view", None),
                 (reverse("orders:progress_list"), "order.view", None),
                 (reverse("production:workorder_list"), "workorder.view", None),
                 (reverse("production:workorder_detail", args=[wo.pk]), "workorder.view", wo),
                 (reverse("orders:order_detail", args=[wo.order_line.order.pk]), "order.view", wo.order_line.order),
                 (reverse("catalog:definition_list", args=["urunler"]), "product.view", None),
                 (reverse("catalog:recipe_detail", args=[self.w.prod_a.pk]), "recipe.view", None),
                 (reverse("catalog:lot_list"), "material.view", None)]
        for u in (self.w.admin, self.w.sales, self.w.planner, self.w.foreman, self.w.depot):
            self.client.force_login(u)
            for url, perm, obj in pages:
                code = self.client.get(url).status_code
                if perm is None or can(u, perm, obj):
                    self.assertEqual(code, 200, f"{u.personnel_no} {url}")
                else:
                    self.assertIn(code, (403, 404), f"{u.personnel_no} {url}")
        # Satış reçeteyi göremez (reçete erişimi yalnız yönetici ve planlama)
        self.client.force_login(self.w.sales)
        self.assertEqual(self.client.get(reverse("catalog:recipe_detail", args=[self.w.prod_a.pk])).status_code, 403)
