import time

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from accounts.middleware import SessionIdleTimeoutMiddleware
from accounts.models import Role
from core.tests.factories import PASSWORD, World, user


class LoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.u = user(Role.SALES)

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse("orders:order_list"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("accounts:login"), resp["Location"])

    def test_personnel_no_alone_is_not_enough(self):
        resp = self.client.post(reverse("accounts:login"), {"personnel_no": self.u.personnel_no, "password": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_wrong_password_rejected(self):
        self.client.post(reverse("accounts:login"), {"personnel_no": self.u.personnel_no, "password": "yanlis-parola"})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_correct_login(self):
        resp = self.client.post(reverse("accounts:login"), {"personnel_no": self.u.personnel_no, "password": PASSWORD})
        self.assertRedirects(resp, reverse("core:home"))

    def test_lockout_after_repeated_failures(self):
        for _ in range(settings.LOGIN_MAX_FAILURES):
            self.client.post(reverse("accounts:login"), {"personnel_no": self.u.personnel_no, "password": "x" * 9})
        resp = self.client.post(reverse("accounts:login"), {"personnel_no": self.u.personnel_no, "password": PASSWORD})
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(resp, "Çok sayıda hatalı deneme")

    def test_inactive_user_cannot_login(self):
        self.u.is_active = False
        self.u.save()
        self.client.post(reverse("accounts:login"), {"personnel_no": self.u.personnel_no, "password": PASSWORD})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_idle_session_expires_on_server(self):
        self.client.force_login(self.u)
        self.assertEqual(self.client.get(reverse("core:home")).status_code, 200)
        session = self.client.session
        session[SessionIdleTimeoutMiddleware.SESSION_KEY] = int(time.time()) - settings.SESSION_IDLE_MINUTES * 60 - 5
        session.save()
        resp = self.client.get(reverse("orders:order_list"))
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_requires_post_and_clears_session(self):
        self.client.force_login(self.u)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.client.post(reverse("accounts:logout"))
        self.assertNotIn("_auth_user_id", self.client.session)


class RoleAuthorizationTests(TestCase):
    """Sunucu tarafı yetki: buton gizlemek yetmez, doğrudan istek de reddedilir."""

    def setUp(self):
        self.w = World()

    def test_sales_cannot_start_production(self):
        wo = self.w.work_order()
        self.client.force_login(self.w.sales)
        resp = self.client.post(reverse("production:workorder_start", args=[wo.pk]), {"idem_key": "k1"})
        self.assertEqual(resp.status_code, 403)
        wo.refresh_from_db()
        self.assertEqual(wo.status, "planlandi")

    def test_sales_cannot_create_work_order(self):
        line = self.w.approved_line()
        self.client.force_login(self.w.sales)
        resp = self.client.post(reverse("production:workorder_create_for_line", args=[line.pk]),
                                {"idem_key": "k2", "target_quantity": "5", "unit": "kutu", "machine_1": self.w.ext.pk})
        self.assertEqual(resp.status_code, 403)

    def test_warehouse_cannot_change_recipe(self):
        self.client.force_login(self.w.depot)
        resp = self.client.get(reverse("catalog:revision_create", args=[self.w.prod_a.pk]))
        self.assertEqual(resp.status_code, 403)
        resp = self.client.post(reverse("catalog:revision_create", args=[self.w.prod_a.pk]), {"idem_key": "k3"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.w.prod_a.current_revision.rev_no, 1)

    def test_production_cannot_approve_planning(self):
        line = self.w.order().lines.get()
        self.client.force_login(self.w.foreman)
        resp = self.client.post(reverse("orders:line_approve", args=[line.pk]), {"idem_key": "k4", "revision": self.w.rev_a.pk})
        self.assertEqual(resp.status_code, 403)

    def test_non_admin_cannot_manage_users(self):
        self.client.force_login(self.w.planner)
        self.assertEqual(self.client.get(reverse("accounts:user_list")).status_code, 403)

    def test_sales_can_view_progress(self):
        self.w.work_order()
        self.client.force_login(self.w.sales)
        resp = self.client.get(reverse("orders:progress_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Gerçekleşen")
        self.assertNotContains(resp, "Üretimi başlat")
