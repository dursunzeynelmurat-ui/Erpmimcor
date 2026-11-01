from django.test import TestCase
from core.tests.factories import World
from orders.forms import LineSpecRequestForm


class ConditionalRequestTests(TestCase):
    def setUp(self):
        self.w = World()
        self.line = self.w.order(draft=True).lines.get()

    def test_hidden_dependents_cannot_keep_stale_values(self):
        form = LineSpecRequestForm(self.line, data={
            'idem_key': 'conditional-test', 'change_reason': 'Kurgu müşteri baskı istemiyor',
            's_print_status': 'yok', 's_print_colors': '4',
            's_gusset_type': 'yok', 's_gusset_depth_mm': '40',
            's_perforation': 'yok', 's_perforation_pitch_mm': '500'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn('gusset_depth_mm', form.spec_changes)
        self.assertNotIn('perforation_pitch_mm', form.spec_changes)
        self.assertNotIn('print_colors', form.spec_changes)
        self.assertEqual(form.spec_changes['print_status'], 'yok')

    def test_internal_recipe_fields_are_not_in_request_form(self):
        form = LineSpecRequestForm(self.line)
        self.assertNotIn('s_process_notes', form.fields)
        self.assertEqual(form.fields['s_gusset_depth_mm'].widget.attrs['data-show-if'], 's_gusset_type:yan,taban')

    def test_turning_off_gusset_requests_clear_without_mutating_revision(self):
        from catalog.models import RecipeRevision
        RecipeRevision.objects.filter(pk=self.w.rev_a.pk).update(gusset_type='yan', gusset_depth_mm=40, width_basis='kapali')
        self.line.product.refresh_from_db()
        form = LineSpecRequestForm(self.line, data={
            'idem_key': 'remove-gusset', 'change_reason': 'Kurgusal yeni ölçü', 's_gusset_type': 'yok'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.spec_changes['gusset_depth_mm'])
        self.w.rev_a.refresh_from_db()
        self.assertEqual(self.w.rev_a.gusset_depth_mm, 40)
