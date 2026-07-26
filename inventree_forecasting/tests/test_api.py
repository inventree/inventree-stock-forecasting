"""API endpoint tests for the InvenTree Forecasting plugin."""

from datetime import date, timedelta

from django.urls import reverse

from build.models import Build
from company.models import Company, SupplierPart
from order.models import PurchaseOrder, PurchaseOrderLineItem, SalesOrder, SalesOrderLineItem
from part.models import BomItem, Part

from common.models import InvenTreeSetting
from InvenTree.unit_test import InvenTreeAPITestCase

NEXT_WEEK = date.today() + timedelta(days=7)


class PartForecastingAPITestCase(InvenTreeAPITestCase):
    """Base test case for the '/plugin/stock-forecasting/forecast/' endpoint."""

    def setUp(self):
        """Ensure the plugin's URLs are registered, and create a part to forecast against."""
        super().setUp()

        # Ensure plugin URLs are registered even if INVENTREE_PLUGIN_TESTING_SETUP
        # is not set in the environment this test happens to run under.
        InvenTreeSetting.set_setting('ENABLE_PLUGINS_URL', True, None)

        self.part = Part.objects.create(
            name='Widget',
            description='A widget for forecasting API tests',
            minimum_stock=5,
            maximum_stock=100,
        )

        self.url = reverse('plugin:stock-forecasting:part-forecasting')


class ResponseShapeTests(PartForecastingAPITestCase):
    """Tests for the overall shape/content of a successful response."""

    def test_basic_response(self):
        """A basic request returns the expected top-level fields."""
        response = self.get(self.url, data={'part': self.part.pk}, expected_code=200)

        data = response.data
        self.assertEqual(data['part'], self.part.pk)
        self.assertEqual(float(data['in_stock']), 0)
        self.assertEqual(float(data['min_stock']), 5)
        self.assertEqual(float(data['max_stock']), 100)
        self.assertEqual(data['entries'], [])

    def test_entries_are_sorted_and_shaped(self):
        """Entries from multiple sources are merged, sorted, and correctly shaped."""
        supplier = Company.objects.create(name='Supplier', is_supplier=True)
        supplier_part = SupplierPart.objects.create(
            part=self.part, supplier=supplier, SKU='SKU-1'
        )
        po = PurchaseOrder.objects.create(supplier=supplier, reference='PO-0001')
        PurchaseOrderLineItem.objects.create(
            order=po, part=supplier_part, quantity=10, target_date=NEXT_WEEK
        )
        build = Build.objects.create(
            part=self.part,
            quantity=5,
            reference='BO-0001',
            target_date=date.today() + timedelta(days=1),
        )

        response = self.get(self.url, data={'part': self.part.pk}, expected_code=200)

        entries = response.data['entries']
        self.assertEqual(len(entries), 2)

        # Entries should be sorted by date (build entry first, PO entry second)
        self.assertEqual(entries[0]['model_type'], 'build')
        self.assertEqual(entries[0]['model_id'], build.pk)
        self.assertEqual(float(entries[0]['quantity']), 5)

        self.assertEqual(entries[1]['model_type'], 'purchaseorder')
        self.assertEqual(float(entries[1]['quantity']), 10)

        for key in ['date', 'quantity', 'title', 'label', 'model_type', 'model_id']:
            self.assertIn(key, entries[0])


class RequestValidationTests(PartForecastingAPITestCase):
    """Tests for request-parameter validation."""

    def test_missing_part_returns_400(self):
        """A request with no 'part' parameter is rejected."""
        self.get(self.url, data={}, expected_code=400)

    def test_invalid_part_returns_400(self):
        """A request referencing a non-existent part is rejected."""
        self.get(self.url, data={'part': 999999}, expected_code=400)

    def test_invalid_export_format_returns_400(self):
        """An unsupported export format is rejected."""
        self.get(
            self.url,
            data={'part': self.part.pk, 'export': 'pdf'},
            expected_code=400,
        )

    def test_unauthenticated_request_rejected(self):
        """An unauthenticated request is rejected."""
        self.logout()
        response = self.client.get(self.url, data={'part': self.part.pk})
        self.assertIn(response.status_code, (401, 403))


class QueryParamTests(PartForecastingAPITestCase):
    """Tests for the boolean query parameters which alter forecasting behaviour."""

    def setUp(self):
        super().setUp()

        self.variant_template = Part.objects.create(
            name='Widget Template', description='Variant template', is_template=True
        )
        self.part.variant_of = self.variant_template
        self.part.save()

        self.customer = Company.objects.create(name='Customer', is_customer=True)

    def test_include_variants_toggle(self):
        """Sales orders against a variant part are only included when include_variants=True."""
        variant = Part.objects.create(
            name='Widget Variant', description='A variant', variant_of=self.variant_template
        )
        so = SalesOrder.objects.create(customer=self.customer, reference='SO-0001')
        SalesOrderLineItem.objects.create(order=so, part=variant, quantity=8)

        # Query against the *template* part, which is the parent of both self.part and 'variant'
        response = self.get(
            self.url,
            data={'part': self.variant_template.pk, 'include_variants': False},
            expected_code=200,
        )
        self.assertEqual(response.data['entries'], [])

        response = self.get(
            self.url,
            data={'part': self.variant_template.pk, 'include_variants': True},
            expected_code=200,
        )
        self.assertEqual(len(response.data['entries']), 1)
        self.assertEqual(float(response.data['entries'][0]['quantity']), -8)

    def test_include_upstream_toggle(self):
        """Sales orders against an upstream assembly are only included when include_upstream=True."""
        assembly = Part.objects.create(
            name='Assembly', description='Uses the test part', assembly=True
        )
        assembly.refresh_from_db()
        self.part.refresh_from_db()
        BomItem.objects.create(part=assembly, sub_part=self.part, quantity=2)

        so = SalesOrder.objects.create(customer=self.customer, reference='SO-0002')
        SalesOrderLineItem.objects.create(order=so, part=assembly, quantity=3)

        response = self.get(
            self.url,
            data={'part': self.part.pk, 'include_upstream': False},
            expected_code=200,
        )
        self.assertEqual(response.data['entries'], [])

        response = self.get(
            self.url,
            data={'part': self.part.pk, 'include_upstream': True},
            expected_code=200,
        )
        self.assertEqual(len(response.data['entries']), 1)
        # 3 units of 'assembly' required -> 6 units of the sub-part
        self.assertEqual(float(response.data['entries'][0]['quantity']), -6)

    def test_consider_intermediate_stock_toggle(self):
        """When enabled, available intermediate stock offsets upstream demand."""
        from stock.models import StockItem

        assembly = Part.objects.create(
            name='Assembly 2', description='Uses the test part', assembly=True
        )
        assembly.refresh_from_db()
        self.part.refresh_from_db()
        BomItem.objects.create(part=assembly, sub_part=self.part, quantity=1)

        # The assembly already has enough stock on hand to cover the sales order
        StockItem.objects.create(part=assembly, quantity=10)

        so = SalesOrder.objects.create(customer=self.customer, reference='SO-0003')
        SalesOrderLineItem.objects.create(order=so, part=assembly, quantity=5)

        # With intermediate stock considered (default), demand is fully offset -
        # the entry is still returned (so the order remains visible to the
        # user), but with its quantity zeroed out and 'original_quantity'
        # showing what it would have been without the offset.
        response = self.get(
            self.url,
            data={
                'part': self.part.pk,
                'include_upstream': True,
                'consider_intermediate_stock': True,
            },
            expected_code=200,
        )
        self.assertEqual(len(response.data['entries']), 1)
        self.assertEqual(float(response.data['entries'][0]['quantity']), 0)
        self.assertEqual(float(response.data['entries'][0]['original_quantity']), -5)

        # With intermediate stock ignored, the raw (unoffset) demand is returned
        response = self.get(
            self.url,
            data={
                'part': self.part.pk,
                'include_upstream': True,
                'consider_intermediate_stock': False,
            },
            expected_code=200,
        )
        self.assertEqual(len(response.data['entries']), 1)
        self.assertEqual(float(response.data['entries'][0]['quantity']), -5)


class ExportTests(PartForecastingAPITestCase):
    """Tests for the 'export' functionality of the forecasting endpoint."""

    def setUp(self):
        super().setUp()
        supplier = Company.objects.create(name='Supplier', is_supplier=True)
        supplier_part = SupplierPart.objects.create(
            part=self.part, supplier=supplier, SKU='SKU-1'
        )
        po = PurchaseOrder.objects.create(supplier=supplier, reference='PO-0010')
        PurchaseOrderLineItem.objects.create(
            order=po, part=supplier_part, quantity=10, target_date=NEXT_WEEK
        )

    def test_csv_export(self):
        """Exporting as CSV returns a downloadable file with the expected name and content."""
        file = self.download_file(
            self.url,
            data={'part': self.part.pk, 'export': 'csv'},
            expected_code=200,
            expected_fn=rf'InvenTree_Stock_Forecasting_{self.part.pk}\.csv',
        )

        content = file.read()
        self.assertIn('Date', content)
        self.assertIn('Quantity', content)
        self.assertIn('PO-0010', content)

    def test_tsv_export(self):
        """Exporting as TSV returns a downloadable file with the expected name."""
        self.download_file(
            self.url,
            data={'part': self.part.pk, 'export': 'tsv'},
            expected_code=200,
            expected_fn=rf'InvenTree_Stock_Forecasting_{self.part.pk}\.tsv',
        )

    def test_xlsx_export(self):
        """Exporting as XLSX returns a downloadable file with the expected name."""
        self.download_file(
            self.url,
            data={'part': self.part.pk, 'export': 'xlsx'},
            expected_code=200,
            expected_fn=rf'InvenTree_Stock_Forecasting_{self.part.pk}\.xlsx',
            decode=False,
        )
