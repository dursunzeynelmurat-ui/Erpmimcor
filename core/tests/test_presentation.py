from html.parser import HTMLParser
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from core.tests.factories import World, load
from core.templatetags.mimcor import field_label, history_value
from orders.selectors import line_progress
from warehouse import services as wh


class Controls(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids, self.keys = [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get('id'):
            self.ids.append(a['id'])
        if a.get('name') == 'idem_key':
            self.keys.append(a.get('value'))


class PresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.w = World()

    def test_pallet_forms_have_unique_labels_and_request_keys(self):
        pallet = self.w.stock_pallet()
        self.client.force_login(self.w.admin)
        response = self.client.get(reverse('warehouse:pallet_detail', args=[pallet.pk]))
        controls = Controls(response.content.decode())
        self.assertEqual(len(controls.ids), len(set(controls.ids)))
        self.assertGreaterEqual(len(controls.keys), 6)
        self.assertEqual(len(controls.keys), len(set(controls.keys)))
        self.assertContains(response, 'Kalite gerekçesi:')
        self.assertContains(response, 'data-review=')

    def test_reservations_are_separate_from_production_and_shipping(self):
        line = self.w.order(qty=Decimal('10')).lines.get()
        pallet = self.w.stock_pallet()
        reservation = wh.reserve(self.w.sales, pallet, line, 10)
        row = line_progress(line)
        self.assertEqual((row['produced'], row['reserved'], row['shipped']), (0, 10, 0))
        shipment = wh.create_shipment(self.w.depot, line.order)
        wh.add_shipment_line(self.w.depot, shipment, reservation, 4)
        wh.confirm_shipment(self.w.store, load(self.w.depot, shipment))
        row = line_progress(line)
        self.assertEqual((row['produced'], row['reserved'], row['shipped']), (0, 6, 4))

    def test_unsupported_unit_does_not_present_box_count_as_production(self):
        line = self.w.order().lines.get()
        line.unit = 'adet'
        line.save()
        self.client.force_login(self.w.sales)
        response = self.client.get(reverse('orders:order_detail', args=[line.order_id]))
        self.assertContains(response, 'Bu birimde üretim özeti desteklenmiyor.')
        self.assertNotContains(response, '<progress')

    def test_invalid_transfer_keeps_input_and_key_and_explains_action(self):
        pallet = self.w.stock_pallet()
        self.client.force_login(self.w.depot)
        response = self.client.post(reverse('warehouse:pallet_transfer', args=[pallet.pk]),
                                    {'quantity': '999', 'reason': 'Sayım sonrası taşıma', 'target_code': '', 'idem_key': 'ui-retained'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Başka palete aktarım')
        self.assertContains(response, 'value="999"')
        self.assertContains(response, 'Sayım sonrası taşıma')
        self.assertContains(response, 'value="ui-retained"')
        pallet.refresh_from_db()
        self.assertEqual(pallet.box_count, 40)

    def test_order_pagination_retains_search(self):
        for _ in range(31):
            self.w.order()
        self.client.force_login(self.w.sales)
        response = self.client.get(reverse('orders:order_list'), {'q': 'Kurgu', 'page': 2})
        self.assertEqual(len(response.context['orders']), 1)
        self.assertContains(response, 'q=Kurgu&amp;page=1')

    def test_history_translates_without_mutating_raw_values(self):
        self.assertEqual(field_label('box_count'), 'Kutu adedi')
        self.assertEqual(history_value('kontrol_bekliyor', 'quality_status'), 'Kontrol bekliyor')
        self.assertEqual(history_value('12.500', 'gross_kg'), '12,5')
        self.assertEqual(history_value(None, 'gross_kg'), '—')

    def test_dashboard_is_role_specific(self):
        self.w.order()
        for user, visible, hidden in [(self.w.sales, 'Son açık siparişler', 'Hazırlanan sevkiyatlar'),
                                     (self.w.depot, 'Depo kabulü bekleyenler', 'Son açık siparişler'),
                                     (self.w.planner, 'Teknik onay bekleyenler', 'Son açık siparişler'),
                                     (self.w.foreman, 'Aktif iş emirleri', 'Son açık siparişler')]:
            self.client.force_login(user)
            response = self.client.get('/')
            self.assertContains(response, visible)
            self.assertNotContains(response, hidden)

    def test_custom_workorder_form_renders_idempotency_field(self):
        line = self.w.approved_line()
        self.client.force_login(self.w.planner)
        response = self.client.get(reverse('production:workorder_create_for_line', args=[line.pk]))
        controls = Controls(response.content.decode())
        self.assertEqual(len(controls.keys), 2)  # logout + work order
        self.assertTrue(all(controls.keys))

    def test_cancel_error_keeps_reason_and_form_key(self):
        line = self.w.order().lines.get()
        pallet = self.w.stock_pallet()
        res = wh.reserve(self.w.sales, pallet, line, 10)
        shipment = wh.create_shipment(self.w.depot, line.order)
        wh.add_shipment_line(self.w.depot, shipment, res, 10)
        self.client.force_login(self.w.sales)
        response = self.client.post(reverse('warehouse:reservation_cancel', args=[res.pk]),
                                    {'reason': 'Müşteri talebi', 'idem_key': 'cancel-ui-key'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Müşteri talebi')
        self.assertContains(response, 'value="cancel-ui-key"')
        self.assertContains(response, 'önce sevkiyattan çıkarın')
        res.refresh_from_db()
        self.assertEqual(res.status, 'aktif')
