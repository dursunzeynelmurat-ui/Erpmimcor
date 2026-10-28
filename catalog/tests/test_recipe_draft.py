"""Aşama 2: satır eklenebilen reçete taslağı, %100 onay şartı, parti kg hesabı, gizlilik ve eş zamanlı onay."""
import threading
import uuid
from decimal import Decimal as D

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from catalog import recipe_drafts as rd
from catalog import services as cat
from catalog.models import DraftStatus, Material, MaterialKind, RecipeDraft
from core.exceptions import BusinessRuleError
from core.tests.factories import World


def colorant(w):
    return cat.create_definition(w.admin, Material, {"code": "MB-MAVI", "name": "Mavi masterbatch",
                                                     "kind": MaterialKind.COLORANT, "unit": "kg"})


class DraftServiceTests(TestCase):
    def setUp(self):
        self.w = World()
        self.mb = colorant(self.w)
        self.wo = self.w.started_wo()  # Rev.1 ile başlamış iş emri

    def test_incomplete_saved_not_approved_then_approved(self):
        draft = rd.open_draft(self.w.planner, self.w.prod_a)
        self.assertEqual([(ln.material.code, ln.percentage) for ln in draft.lines.all()],
                         [("LDPE", D("98")), ("SLIP", D("2"))])
        spec = rd.spec_from_json(draft.spec_values)
        rd.save_draft(self.w.planner, draft, spec_values=spec, change_reason="Mavi renk",
                      lines=[(self.w.ldpe, D("92"), ""), (self.w.slip, D("2"), ""), (self.mb, D("3"), "Ön karışım")])
        with self.assertRaisesMessage(BusinessRuleError, "Karışım toplamı %97. Onaylamak için %100 olmalı."):
            rd.approve_draft(self.w.planner, draft)
        draft.refresh_from_db()
        self.assertEqual(draft.status, DraftStatus.OPEN)
        self.assertEqual(draft.lines.count(), 3)  # eksik taslak kaybolmadı
        rd.save_draft(self.w.planner, draft, spec_values=spec, change_reason="Mavi renk",
                      lines=[(self.w.ldpe, D("95"), ""), (self.w.slip, D("2"), ""), (self.mb, D("3"), "Ön karışım")])
        rev = rd.approve_draft(self.w.planner, draft)
        self.assertEqual(rev.rev_no, 2)
        self.assertEqual(rev.components.get(material=self.mb).application_note, "Ön karışım")
        self.wo.refresh_from_db()
        self.assertEqual(self.wo.recipe_revision.rev_no, 1)  # başlamış iş emri eski revizyonu korur
        with self.assertRaisesMessage(BusinessRuleError, "artık açık değil"):
            rd.approve_draft(self.w.planner, draft)

    def test_line_validation(self):
        draft = rd.open_draft(self.w.planner, self.w.prod_a)
        spec = rd.spec_from_json(draft.spec_values)
        litre = cat.create_definition(self.w.admin, Material, {"code": "SOLV", "name": "Solvent", "kind": "katki",
                                                               "unit": "lt"})
        for lines, msg in (([(self.w.ldpe, D("50"), ""), (self.w.ldpe, D("50"), "")], "iki kez"),
                           ([(self.w.ldpe, D("-1"), "")], "0 ile 100"),
                           ([(litre, D("10"), "")], "yalnız kg")):
            with self.assertRaisesMessage(BusinessRuleError, msg):
                rd.save_draft(self.w.planner, draft, spec_values=spec, lines=lines)
        rd.save_draft(self.w.planner, draft, spec_values=spec, lines=[(self.w.ldpe, None, "oran sonra")])
        with self.assertRaisesMessage(BusinessRuleError, "oran girilmedi"):
            rd.approve_draft(self.w.planner, draft)

    def test_batch_table_rounding_visible(self):
        draft = rd.open_draft(self.w.planner, self.w.prod_a)
        rd.save_draft(self.w.planner, draft, spec_values=rd.spec_from_json(draft.spec_values), batch_kg=D("250"),
                      lines=[(self.w.ldpe, D("33.333"), ""), (self.w.slip, D("33.333"), ""), (self.mb, D("33.334"), "")])
        t = rd.batch_table(list(draft.lines.all()), D("250"))
        self.assertEqual([kg for _, kg in t["rows"]], [D("83.333"), D("83.333"), D("83.335")])
        self.assertEqual((t["total_pct"], t["total_kg"], t["rounding_diff"]), (D("100"), D("250.001"), D("0.001")))
        t100 = rd.batch_table(list(draft.lines.all()), D("100"))
        self.assertEqual([kg for _, kg in t100["rows"]], [D("33.333"), D("33.333"), D("33.334")])
        self.assertIsNone(t100["rounding_diff"])

    def test_one_open_draft_per_product(self):
        a = rd.open_draft(self.w.planner, self.w.prod_a)
        self.assertEqual(rd.open_draft(self.w.planner, self.w.prod_a), a)

    def test_sales_cannot_open_or_save(self):
        with self.assertRaises(Exception):
            rd.open_draft(self.w.sales, self.w.prod_a)


class DraftViewTests(TestCase):
    def setUp(self):
        self.w = World()
        self.mb = colorant(self.w)

    def post(self, draft, git, rows, **extra):
        data = {"idem_key": uuid.uuid4().hex, "git": git, "satir_sayisi": str(len(rows)), "width_mm": "600",
                "thickness_micron": "30", "batch_kg": "100", "change_reason": "Renk", "color": "Mavi", **extra}
        for i, (m, p) in enumerate(rows):
            data[f"material_{i}"], data[f"pct_{i}"] = m.pk, p
        return self.client.post(reverse("catalog:recipe_draft", args=[draft.pk]), data, follow=True)

    def test_editor_flow_and_messages(self):
        self.client.force_login(self.w.planner)
        r = self.client.post(reverse("catalog:revision_create", args=[self.w.prod_a.pk]), {"idem_key": "o1"}, follow=True)
        draft = RecipeDraft.objects.get()
        self.assertContains(r, "Malzeme ekle")
        r = self.post(draft, "onayla", [(self.w.ldpe, "92,5"), (self.w.slip, "2"), (self.mb, "3")])
        self.assertContains(r, "Karışım toplamı %97,5. Onaylamak için %100 olmalı.")
        self.assertEqual([ln.percentage for ln in RecipeDraft.objects.get().lines.all()], [D("92.5"), D("2"), D("3")])
        r = self.post(draft, "satir", [(self.w.ldpe, "92,5"), (self.w.slip, "2"), (self.mb, "3")])
        self.assertEqual(len(r.context["form"].line_rows()), 4)  # JS'siz "Malzeme ekle" boş satır açar
        r = self.post(draft, "onayla", [(self.w.ldpe, "95"), (self.w.slip, "2"), (self.mb, "3")])
        self.assertContains(r, "Reçete onaylandı")
        self.assertEqual(self.w.prod_a.current_revision.rev_no, 2)
        self.assertContains(r, "MB-MAVI")

    def test_composition_hidden_from_others(self):
        draft = rd.open_draft(self.w.planner, self.w.prod_a)
        for u in (self.w.sales, self.w.foreman, self.w.depot):
            self.client.force_login(u)
            self.assertEqual(self.client.get(reverse("catalog:recipe_draft", args=[draft.pk])).status_code, 403)
            self.assertEqual(self.client.get(reverse("catalog:recipe_detail", args=[self.w.prod_a.pk])).status_code, 403)
        order = self.w.order()
        self.client.force_login(self.w.sales)
        page = self.client.get(reverse("orders:order_detail", args=[order.pk]))
        self.assertContains(page, "Teknik özellikleri göster")
        self.assertNotContains(page, "LDPE")
        self.assertNotContains(page, "%98")


class ConcurrentDraftApprovalTests(TransactionTestCase):
    def test_two_approvals_one_revision(self):
        w = World()
        draft = rd.open_draft(w.planner, w.prod_a)
        rd.save_draft(w.planner, draft, spec_values=rd.spec_from_json(draft.spec_values), change_reason="Aynı",
                      lines=[(w.ldpe, D("97"), ""), (w.slip, D("3"), "")])
        barrier, out = threading.Barrier(2), []

        def run():
            try:
                barrier.wait()
                rd.approve_draft(w.planner, draft)
                out.append("ok")
            except BusinessRuleError as exc:
                out.append(exc.message)
            finally:
                connection.close()
        ts = [threading.Thread(target=run) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(out.count("ok"), 1, out)
        self.assertEqual(w.prod_a.recipe.revisions.count(), 2)
