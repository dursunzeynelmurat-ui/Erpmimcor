"""Aşama 1: sipariş/ürün teknik özellikleri, Türkçe ondalık, belirtilmemiş ↔ yok ayrımı, 3 adımlı sipariş girişi."""
import uuid
from decimal import Decimal as D

from django import forms
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog import services as cat
from catalog import spec
from catalog.forms import RevisionForm
from core.exceptions import BusinessRuleError
from core.forms import TRDecimalField
from core.tests.factories import World
from orders import services as orders
from orders.models import CreationMethod, OrderPurpose, SalesOrder


class TurkishDecimalTests(TestCase):
    def test_comma_and_dot(self):
        f = TRDecimalField(max_digits=8, decimal_places=2, required=False)
        self.assertEqual(f.clean("12,5"), D("12.5"))  # 125 değil
        self.assertEqual(f.clean("12.5"), D("12.5"))
        self.assertEqual(f.clean(" 0,75 "), D("0.75"))
        for bad in ("1.250,5", "1,2,3", "1.250"):
            with self.assertRaises(forms.ValidationError):
                f.clean(bad)

    def test_widget_shows_comma(self):
        self.assertIn('value="12,5"', str(TRDecimalField(max_digits=8, decimal_places=2).widget.render("x", D("12.50"))))


GUSSET_PRINT = {"width_mm": D("400"), "thickness_micron": D("40"), "product_length_mm": D("550"), "length_m": None,
                "gusset_type": "yan", "gusset_depth_mm": D("60"), "width_basis": "kapali", "appearance": "renkli",
                "color": "Mavi", "color_system": "ral", "color_code": "RAL 5015", "perforation": "yok",
                "print_status": "var", "plate_status": "yeni", "plate_code": "KL-KURGU-01",
                "cylinder_circumference_mm": D("600"), "print_repeat_mm": D("550"), "print_gap_mm": D("50"),
                "print_color_count": 2, "print_colors": "Pantone 286 C, Siyah", "print_sides": "tek",
                "print_direction": "Rulodan çıkarken okunur"}


class SpecRuleTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_old_revision_shows_unspecified_not_none(self):
        rev = self.w.prod_a.current_revision  # yeni alanlar boş
        rows = {r["label"]: r for _, rs in spec.card(rev, include_hidden=True) for r in rs}
        self.assertEqual(rows["Körük"]["value"], spec.UNSPECIFIED)
        self.assertEqual(rows["Baskı"]["value"], spec.UNSPECIFIED)
        self.assertEqual(rows["Perfore"]["value"], spec.UNSPECIFIED)

    def test_gusseted_requires_width_definition(self):
        data = {**GUSSET_PRINT, "width_basis": ""}
        with self.assertRaisesMessage(BusinessRuleError, "En ölçüm tanımı"):
            cat.create_revision(self.w.planner, self.w.prod_a, data, self.w.mix(), "Körüklü")
        rev = cat.create_revision(self.w.planner, self.w.prod_a, GUSSET_PRINT, self.w.mix(), "Körüklü")
        self.assertEqual((rev.gusset_type, rev.width_basis, rev.gusset_depth_mm), ("yan", "kapali", D("60")))

    def test_hidden_values_not_saved(self):
        data = {**GUSSET_PRINT, "print_status": "yok", "gusset_type": "yok"}
        rev = cat.create_revision(self.w.planner, self.w.prod_a, data, self.w.mix(), "Baskısız")
        self.assertEqual((rev.plate_code, rev.cylinder_circumference_mm, rev.gusset_depth_mm, rev.width_basis),
                         ("", None, None, ""))
        self.assertEqual((rev.print_status, rev.gusset_type), ("yok", "yok"))  # açık "yok", belirtilmemiş değil

    def test_unknown_field_and_bad_choice_rejected(self):
        with self.assertRaises(BusinessRuleError):
            cat.create_revision(self.w.planner, self.w.prod_a, {**GUSSET_PRINT, "renk_tonu": "x"}, self.w.mix(), "x")
        with self.assertRaises(BusinessRuleError):
            cat.create_revision(self.w.planner, self.w.prod_a, {**GUSSET_PRINT, "gusset_type": "ust"}, self.w.mix(), "x")

    def test_form_turkish_decimal_and_errors(self):
        data = {"width_mm": "400,5", "thickness_micron": "40", "gusset_type": "yan", "material_0": self.w.ldpe.pk,
                "pct_0": "98", "material_1": self.w.slip.pk, "pct_1": "2", "change_reason": "x", "idem_key": "k"}
        form = RevisionForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn("gusset_depth_mm", form.errors)
        self.assertIn("width_basis", form.errors)
        form = RevisionForm({**data, "gusset_depth_mm": "60", "width_basis": "acik", "color_preview": "mavi"})
        self.assertFalse(form.is_valid())
        self.assertIn("#RRGGBB", str(form.errors["color_preview"]))
        form = RevisionForm({**data, "gusset_depth_mm": "60", "width_basis": "acik"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.spec()["width_mm"], D("400.5"))

    def test_signature_detects_new_spec_difference(self):
        old = self.w.prod_a.current_revision
        rev = cat.create_revision(self.w.planner, self.w.prod_a, {**GUSSET_PRINT, "width_mm": D("600"),
                                                                    "thickness_micron": D("30")}, self.w.mix(), "Körük")
        self.assertNotEqual(orders.tech_signature(old), orders.tech_signature(rev))
        same = cat.create_revision(self.w.planner, self.w.prod_a, {"width_mm": D("600"), "thickness_micron": D("30"),
                                                                     "length_m": None, "color": "Şeffaf"},
                                   self.w.mix(), "Aynı")
        self.assertEqual(orders.tech_signature(old), orders.tech_signature(same))


class OrderWizardTests(TestCase):
    def setUp(self):
        self.w = World()
        self.rev = cat.create_revision(self.w.planner, self.w.prod_a, {**GUSSET_PRINT}, self.w.mix(), "Körüklü baskılı")
        self.client.force_login(self.w.sales)
        self.url = reverse("orders:order_create")
        self.base = {"idem_key": uuid.uuid4().hex, "customer": self.w.cust_a.pk, "product": self.w.prod_a.pk,
                     "order_date": timezone.localdate().isoformat(), "unit": "kutu"}

    def step(self, adim, git, **data):
        return self.client.post(self.url, {**self.base, "adim": str(adim), "git": git, **data})

    def test_three_steps(self):
        r = self.client.get(self.url)
        self.assertEqual(r.context["step"], 1)
        r = self.step(1, "ileri")
        self.assertEqual(r.context["step"], 2)
        self.assertContains(r, "Yan körük")
        self.assertContains(r, "Kapalı en")
        self.assertContains(r, "KL-KURGU-01")
        self.assertNotContains(r, "Bu alan zorunludur")  # yeni açılan adımda hata gösterilmez
        self.assertNotContains(r, "LDPE")  # karışım satışa sızmaz
        r = self.step(2, "ileri", quantity="12,5")
        self.assertEqual(r.context["step"], 2)  # kutu biriminde ondalık reddedilir ama bilgiler korunur
        self.assertContains(r, 'value="12,5"')
        r = self.step(2, "ileri", quantity="40", purpose="numune", charge="ucretsiz")
        self.assertEqual(r.context["step"], 3)
        self.assertContains(r, "Numune")
        self.assertContains(r, "Düzenle")
        r = self.step(3, "kaydet", quantity="40", purpose="numune", charge="ucretsiz")
        self.assertEqual(r.context["step"], 3)
        self.assertContains(r, "teslimat bilgilerini kontrol")
        self.assertFalse(SalesOrder.objects.exists())
        r = self.step(3, "kaydet", quantity="40", purpose="numune", charge="ucretsiz", delivery_confirmed="on")
        o = SalesOrder.objects.get()
        self.assertRedirects(r, reverse("orders:order_detail", args=[o.pk]), fetch_redirect_response=False)
        self.assertEqual((o.purpose, o.charge, o.creation_method), (OrderPurpose.SAMPLE, "ucretsiz", CreationMethod.NEW))
        page = self.client.get(r["Location"])
        self.assertContains(page, "Sıradaki adım")

    def test_other_customers_product_rejected_at_step_one(self):
        r = self.step(1, "ileri", product=self.w.prod_b.pk)
        self.assertEqual(r.context["step"], 1)
        self.assertIn("product", r.context["form"].errors)
        r = self.step(3, "kaydet", product=self.w.prod_b.pk, quantity="5", purpose="ticari", delivery_confirmed="on")
        self.assertFalse(SalesOrder.objects.exists())

    def test_order_change_does_not_touch_product_revision(self):
        order = orders.approve_order(self.w.sales, orders.create_order(
            self.w.sales, customer=self.w.cust_a, order_date=timezone.localdate(), purpose="ticari",
            lines=[{"product": self.w.prod_a, "quantity": D("10"), "unit": "kutu"}]))
        line = orders.approve_line(self.w.planner, order.lines.get(), self.rev)
        orders.change_line(self.w.sales, line, quantity=D("20"), due_date=None, reason="Müşteri artırdı")
        new = cat.create_revision(self.w.planner, self.w.prod_a, {**GUSSET_PRINT, "plate_code": "KL-2"}, self.w.mix(),
                                  "Yeni klişe")
        line.refresh_from_db()
        self.assertEqual(line.approved_revision, self.rev)  # kalem kendi revizyonunu korur
        self.assertNotEqual(new, self.rev)
        detail = self.client.get(reverse("orders:order_detail", args=[order.pk]))
        self.assertContains(detail, "KL-KURGU-01")
        self.assertNotContains(detail, "KL-2")
