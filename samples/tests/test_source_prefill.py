from django.test import TestCase
from django.urls import reverse

from core.tests.factories import World
from samples.models import Sample
from warehouse.models import PalletMovement


class SourcePrefillTests(TestCase):
    def setUp(self):
        self.w = World()
        self.pallet = self.w.stock_pallet()
        self.url = reverse('samples:sample_create')

    def test_get_prefills_scoped_source_without_stock_or_sample_write(self):
        self.client.force_login(self.w.depot)
        before = (Sample.objects.count(), PalletMovement.objects.count())
        response = self.client.get(self.url, {'palet': self.pallet.pk})
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertEqual(form['pallet_code'].value(), self.pallet.code)
        self.assertEqual(form['product'].value(), self.pallet.product_id)
        self.assertIn(self.pallet.product, form.fields['product'].queryset)
        self.assertEqual(before, (Sample.objects.count(), PalletMovement.objects.count()))

    def test_sales_cannot_use_prefill_to_manage_samples(self):
        self.client.force_login(self.w.sales)
        self.assertEqual(self.client.get(self.url, {'palet': self.pallet.pk}).status_code, 403)

    def test_invalid_or_missing_source_returns_404(self):
        self.client.force_login(self.w.depot)
        for value in ['abc', '99999999']:
            self.assertEqual(self.client.get(self.url, {'palet': value}).status_code, 404)

    def test_failed_post_keeps_user_values_instead_of_get_source(self):
        self.client.force_login(self.w.depot)
        response = self.client.post(self.url + '?palet=' + str(self.pallet.pk), {
            'idem_key': 'ui-source-error', 'customer': self.w.cust_b.pk, 'product': self.w.prod_b.pk,
            'pallet_code': 'KULLANICININ-KODU', 'quantity': '2', 'unit': 'adet', 'room': 'Kurgu Oda'})
        self.assertEqual(response.context['form']['pallet_code'].value(), 'KULLANICININ-KODU')
        self.assertEqual(response.context['form']['room'].value(), 'Kurgu Oda')
        self.assertFalse(Sample.objects.exists())

    def test_customer_scope_cannot_prefill_other_customer_source(self):
        from accounts.models import AccessRole, RolePermission
        from catalog.models import CustomerAccess
        role = AccessRole.objects.get(code='satis')
        RolePermission.objects.create(role=role, action='sample.manage', scope='customer')
        CustomerAccess.objects.filter(user=self.w.sales, customer=self.w.cust_a).delete()
        self.client.force_login(self.w.sales)
        response = self.client.get(self.url, {'palet': self.pallet.pk})
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, self.pallet.code, status_code=404)

    def test_coil_source_prefill(self):
        self.client.force_login(self.w.depot)
        coil = self.pallet.pallet_coils.get().coil
        response = self.client.get(self.url, {'bobin': coil.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form']['coil_code'].value(), coil.code)
