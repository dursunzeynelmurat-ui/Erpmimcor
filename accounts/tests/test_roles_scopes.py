"""Yönetilebilir roller, kapsam birleşimi, müşteri izolasyonu, oturumda yetki düşürme, son yönetici ve İK sınırları."""
import time
from decimal import Decimal as D

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts import services as acc
from accounts.middleware import ABSOLUTE_KEY, SessionIdleTimeoutMiddleware
from accounts.models import AccessRole, Role, RoleStatus, User, UserRole
from accounts.permissions import can, scoped
from production.models import WorkOrder
from catalog import customers as cs
from core.exceptions import BusinessRuleError
from core.models import AuditLog
from core.tests.factories import PASSWORD, World
from orders.models import SalesOrder
from warehouse import services as wh


def role(code):
    return AccessRole.objects.get(code=code)


class ScopeIsolationTests(TestCase):
    """Satışçı yalnız erişimi olan müşterinin verisini görür: liste, doğrudan URL, endpoint, QR, dashboard."""

    def setUp(self):
        self.w = World()
        self.other_sales = User.objects.create_user("9102", PASSWORD, full_name="Diğer Satış", role=Role.SALES)
        cs.grant_access(self.w.admin, self.w.cust_b, self.other_sales, timezone.localdate(), None, "test")
        self.order_a = self.w.order(product=self.w.prod_a)
        self.pallet_b = self.w.stock_pallet(product=self.w.prod_b)
        # 9102 yalnız B'yi görür
        self.client.force_login(self.other_sales)

    def test_lists_are_scoped(self):
        resp = self.client.get(reverse("orders:order_list"))
        self.assertNotContains(resp, self.order_a.code)
        resp = self.client.get(reverse("catalog:customer_list"))
        self.assertContains(resp, self.w.cust_b.code)
        self.assertNotContains(resp, self.w.cust_a.code)
        resp = self.client.get(reverse("warehouse:pallet_list"))
        self.assertContains(resp, self.pallet_b.code)

    def test_direct_urls_return_404_outside_scope(self):
        self.assertEqual(self.client.get(reverse("orders:order_detail", args=[self.order_a.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("catalog:customer_detail", args=[self.w.cust_a.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("orders:customer_products", args=[self.w.cust_a.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("catalog:definition_edit", args=["urunler", self.w.prod_a.pk])).status_code, 404)

    def test_qr_and_pallet_of_other_customer_hidden(self):
        pallet_a = self.w.stock_pallet(product=self.w.prod_a)
        self.assertEqual(self.client.get(reverse("warehouse:qr_open", args=[pallet_a.qr_token])).status_code, 404)
        self.assertEqual(self.client.get(reverse("warehouse:pallet_detail", args=[pallet_a.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("warehouse:pallet_label", args=[pallet_a.pk])).status_code, 404)

    def test_service_rejects_order_for_unassigned_customer(self):
        from orders import services as orders
        with self.assertRaises(BusinessRuleError):
            orders.create_order(self.other_sales, customer=self.w.cust_a, order_date=timezone.localdate(),
                                lines=[{"product": self.w.prod_a, "quantity": D("1"), "unit": "kutu"}])

    def test_reservation_service_scoped(self):
        from django.core.exceptions import PermissionDenied
        pallet_a = self.w.stock_pallet(product=self.w.prod_a)
        with self.assertRaises(PermissionDenied):
            wh.reserve(self.other_sales, pallet_a, self.order_a.lines.get(), 1)

    def test_temporary_access_expires_and_history_kept(self):
        access = cs.grant_access(self.w.admin, self.w.cust_a, self.other_sales,
                                 timezone.localdate() - timezone.timedelta(days=10),
                                 timezone.localdate() - timezone.timedelta(days=1), "geçici")
        self.assertFalse(access.is_current)
        self.assertEqual(self.client.get(reverse("orders:order_detail", args=[self.order_a.pk])).status_code, 404)
        cs.grant_access(self.w.admin, self.w.cust_a, self.other_sales, timezone.localdate(),
                        timezone.localdate() + timezone.timedelta(days=2), "geçici")
        self.assertEqual(self.client.get(reverse("orders:order_detail", args=[self.order_a.pk])).status_code, 200)
        self.assertTrue(SalesOrder.objects.filter(pk=self.order_a.pk).exists())

    def test_operational_roles_not_tied_to_customer_ownership(self):
        self.assertTrue(can(self.w.planner, "order.view", self.order_a))
        self.assertTrue(can(self.w.depot, "stock.view", self.pallet_b))
        self.assertFalse(can(self.w.depot, "customer.commercial.view", self.w.cust_a))


class RoleUnionTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_union_of_roles_widens_only_where_granted(self):
        u = User.objects.create_user("9201", PASSWORD, full_name="Çift rol", role=Role.SALES)
        order_b = self.w.order(product=self.w.prod_b)
        self.assertFalse(can(u, "order.view", order_b))
        UserRole.objects.create(user=u, role=role("planlama"))
        self.assertTrue(can(u, "order.view", order_b))  # planlama order.view = tüm kayıtlar
        self.assertFalse(can(u, "customer.commercial.view", self.w.cust_b))  # ticari kapsam hâlâ müşteri bazlı

    def test_scope_without_rule_grants_nothing(self):
        r = acc.create_role(self.w.admin, code="makine-satis", name="Deneme", perms={"workorder.view": "machine"})
        u = User.objects.create_user("9202", PASSWORD, full_name="Makinesiz")
        UserRole.objects.create(user=u, role=r)
        self.w.work_order()
        self.assertEqual(scoped(WorkOrder.objects.all(), u, "workorder.view").count(), 0)

    def test_superuser_flag_grants_no_app_permission(self):
        u = User.objects.create_user("9203", PASSWORD, full_name="Süper", is_superuser=True, is_staff=True)
        self.assertFalse(can(u, "order.view"))
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("orders:order_list")).status_code, 403)
        self.assertNotEqual(self.client.get("/yonetim/").status_code, 200)

    def test_unsupported_action_or_scope_rejected(self):
        with self.assertRaises(BusinessRuleError):
            acc.create_role(self.w.admin, code="x1", name="x", perms={"siparis.sil": "all"})
        with self.assertRaises(BusinessRuleError):
            acc.create_role(self.w.admin, code="x2", name="x", perms={"recipe.view": "customer"})


class SessionRevocationTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_revoked_role_applies_to_open_session(self):
        self.client.force_login(self.w.planner)
        self.assertEqual(self.client.get(reverse("orders:planning_queue")).status_code, 200)
        ur = UserRole.objects.get(user=self.w.planner, revoked_at__isnull=True)
        acc.revoke_role(self.w.admin, ur, "görev değişikliği")
        self.assertEqual(self.client.get(reverse("orders:planning_queue")).status_code, 403)
        self.assertTrue(AuditLog.objects.filter(action="user.role_revoke", object_code=self.w.planner.personnel_no).exists())

    def test_deactivated_account_logged_out(self):
        self.client.force_login(self.w.planner)
        acc.update_user(self.w.admin, self.w.planner, full_name="x", team="", shift=None, is_active=False,
                        reason="işten ayrıldı")
        resp = self.client.get(reverse("orders:order_list"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/giris/", resp["Location"])
        self.assertTrue(AuditLog.objects.filter(action="user.deactivate").exists())

    def test_absolute_timeout_not_extended_by_activity(self):
        self.client.force_login(self.w.sales)
        session = self.client.session
        session[ABSOLUTE_KEY] = int(time.time()) - settings.SESSION_COOKIE_AGE - 5
        session[SessionIdleTimeoutMiddleware.SESSION_KEY] = int(time.time())  # az önce aktif
        session.save()
        resp = self.client.get(reverse("orders:order_list"))
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_active_use_within_limit_keeps_login_time(self):
        self.client.force_login(self.w.sales)
        first = self.client.session[ABSOLUTE_KEY]
        self.client.get(reverse("orders:order_list"))
        self.assertEqual(self.client.session[ABSOLUTE_KEY], first)


class LastAdminTests(TestCase):
    def setUp(self):
        self.w = World()
        self.admin_ur = UserRole.objects.get(user=self.w.admin, role__code="yonetici")
        self.second = User.objects.create_user("9301", PASSWORD, full_name="Yönetici 2")

    def test_admin_cannot_remove_own_admin_role(self):
        with self.assertRaisesMessage(BusinessRuleError, "Kendi rol"):
            acc.revoke_role(self.w.admin, self.admin_ur, "deneme")

    def test_removing_other_admin_allowed_only_while_one_remains(self):
        acc.grant_role(self.w.admin, self.second, role("yonetici"), "ikinci")
        acc.revoke_role(self.second, self.admin_ur, "devir")  # 9301 yönetici kalır
        self.assertEqual(acc.admin_user_ids(), {self.second.pk})

    def test_cannot_deactivate_or_disable_only_admin(self):
        acc.grant_role(self.w.admin, self.second, role("yonetici"), "ikinci yönetici")
        second_ur = UserRole.objects.get(user=self.second, role__code="yonetici")
        acc.revoke_role(self.w.admin, second_ur, "geri alındı")  # 9001 hâlâ yönetici: izinli
        with self.assertRaises(BusinessRuleError):
            acc.set_role_status(self.w.admin, role("yonetici"), RoleStatus.INACTIVE, "deneme")
        perms = dict(role("yonetici").permissions.values_list("action", "scope"))
        perms.pop("system.admin")
        with self.assertRaises(BusinessRuleError):
            acc.update_role(self.w.admin, role("yonetici"), name="Yönetici", description="", hr_assignable=False,
                            perms=perms)
        self.assertTrue(role("yonetici").permissions.filter(action="system.admin").exists())

    def test_admin_can_be_deactivated_if_another_admin_remains(self):
        acc.grant_role(self.w.admin, self.second, role("yonetici"), "ikinci yönetici")
        acc.update_user(self.second, self.w.admin, full_name="x", team="", shift=None, is_active=False, reason="test")
        self.assertFalse(User.objects.get(pk=self.w.admin.pk).is_active)


class HrBoundaryTests(TestCase):
    def setUp(self):
        self.w = World()
        self.hr = User.objects.create_user("9401", PASSWORD, full_name="İK", role=Role.HR)

    def test_hr_creates_personnel_with_assignable_role(self):
        u = acc.create_user(self.hr, personnel_no="9402", full_name="Yeni Operatör", password="Uzun-Parola-12",
                            team="Extruder", roles=[role("operator")])
        self.assertTrue(u.active_roles().filter(code="operator").exists())

    def test_hr_cannot_assign_admin_or_non_assignable_role(self):
        for code in ("yonetici", "ik"):
            with self.assertRaises(BusinessRuleError):
                acc.create_user(self.hr, personnel_no=f"95{code[:2]}", full_name="x", password="Uzun-Parola-12",
                                roles=[role(code)])

    def test_hr_cannot_change_own_roles_or_admin_accounts(self):
        with self.assertRaises(BusinessRuleError):
            acc.grant_role(self.hr, self.hr, role("planlama"), "")
        with self.assertRaises(BusinessRuleError):
            acc.update_user(self.hr, self.w.admin, full_name="x", team="", shift=None, is_active=False, reason="x")

    def test_hr_deactivates_non_admin_with_reason(self):
        with self.assertRaises(BusinessRuleError):
            acc.update_user(self.hr, self.w.sales, full_name=self.w.sales.full_name, team="", shift=None,
                            is_active=False, reason="")
        acc.update_user(self.hr, self.w.sales, full_name=self.w.sales.full_name, team="", shift=None,
                        is_active=False, reason="ayrıldı")
        self.assertFalse(User.objects.get(pk=self.w.sales.pk).is_active)

    def test_hr_draft_role_needs_admin_approval(self):
        with self.assertRaises(BusinessRuleError):
            acc.create_role(self.hr, code="taslak1", name="Taslak", perms={"system.admin": "all"})
        draft = acc.create_role(self.hr, code="taslak2", name="Vardiya şefi", perms={"workorder.view": "shift"})
        self.assertEqual(draft.status, RoleStatus.DRAFT)
        target = User.objects.create_user("9403", PASSWORD, full_name="Hedef")
        with self.assertRaises(BusinessRuleError):
            acc.grant_role(self.w.admin, target, draft, "")
        acc.approve_role(self.w.admin, draft)
        acc.grant_role(self.w.admin, target, AccessRole.objects.get(pk=draft.pk), "")
        self.assertTrue(AuditLog.objects.filter(action="role.approve").exists())

    def test_hr_pages(self):
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(reverse("accounts:user_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("accounts:role_create")).status_code, 200)
        self.assertEqual(self.client.get(reverse("orders:order_list")).status_code, 403)
        self.client.force_login(self.w.admin)
        self.assertEqual(self.client.get(reverse("accounts:role_edit", args=[role("satis").pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("accounts:user_edit", args=[self.hr.pk])).status_code, 200)
