"""Basic regression tests for the core forecasting calculations in `forecast.py`."""

from datetime import date, timedelta

from build.models import Build
from build.status_codes import BuildStatus
from company.models import Company, SupplierPart
from order.models import (
    PurchaseOrder,
    PurchaseOrderLineItem,
    SalesOrder,
    SalesOrderLineItem,
)
from order.status_codes import PurchaseOrderStatus
from part.models import BomItem, Part

from InvenTree.unit_test import InvenTreeTestCase

from ..forecast import PartForecast

TOMORROW = date.today() + timedelta(days=1)
NEXT_WEEK = date.today() + timedelta(days=7)


class PartForecastTestCase(InvenTreeTestCase):
    """Base test case which creates a single part to forecast against."""

    def setUp(self):
        """Create a part and a fresh PartForecast calculator for each test."""
        super().setUp()

        self.part = Part.objects.create(
            name='Widget', description='A widget for forecasting tests'
        )
        # MPTT's `tree_id` is finalized by a post-save fixup that doesn't update
        # this in-memory instance - refresh so later BomItem creation sees the
        # correct value (a stale/duplicate tree_id trips the BOM 'recursive' check).
        self.part.refresh_from_db()
        self.forecast = PartForecast()


class PurchaseOrderEntryTests(PartForecastTestCase):
    """Tests for `generate_purchase_order_entries`."""

    def setUp(self):
        super().setUp()
        self.supplier = Company.objects.create(name='Supplier', is_supplier=True)
        self.supplier_part = SupplierPart.objects.create(
            part=self.part, supplier=self.supplier, SKU='SKU-1'
        )

    def test_open_order_increases_forecast(self):
        """An open purchase order line increases the forecast at its target date."""
        po = PurchaseOrder.objects.create(supplier=self.supplier, reference='PO-0001')
        PurchaseOrderLineItem.objects.create(
            order=po, part=self.supplier_part, quantity=100, target_date=NEXT_WEEK
        )

        entries = self.forecast.generate_purchase_order_entries(
            self.part, include_variants=False
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['quantity'], 100)
        self.assertEqual(entries[0]['date'], NEXT_WEEK)

    def test_received_quantity_is_offset(self):
        """Already-received stock is subtracted from the outstanding quantity."""
        po = PurchaseOrder.objects.create(supplier=self.supplier, reference='PO-0002')
        PurchaseOrderLineItem.objects.create(
            order=po, part=self.supplier_part, quantity=100, received=40
        )

        entries = self.forecast.generate_purchase_order_entries(
            self.part, include_variants=False
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['quantity'], 60)

    def test_fully_received_line_excluded(self):
        """A line which has been fully received generates no forecast entry."""
        po = PurchaseOrder.objects.create(supplier=self.supplier, reference='PO-0003')
        PurchaseOrderLineItem.objects.create(
            order=po, part=self.supplier_part, quantity=50, received=50
        )

        entries = self.forecast.generate_purchase_order_entries(
            self.part, include_variants=False
        )

        self.assertEqual(entries, [])

    def test_completed_order_excluded(self):
        """A completed purchase order does not contribute to the forecast."""
        po = PurchaseOrder.objects.create(supplier=self.supplier, reference='PO-0004')
        PurchaseOrderLineItem.objects.create(
            order=po, part=self.supplier_part, quantity=10
        )

        # Transition the order to 'complete' after the line item has been added
        # (a completed order is 'locked', and cannot have line items added to it)
        po.status = PurchaseOrderStatus.COMPLETE.value
        po.save()

        entries = self.forecast.generate_purchase_order_entries(
            self.part, include_variants=False
        )

        self.assertEqual(entries, [])

    def test_pack_quantity_applied(self):
        """The supplier part pack quantity is applied to the ordered quantity."""
        packed_supplier_part = SupplierPart.objects.create(
            part=self.part, supplier=self.supplier, SKU='SKU-PACK', pack_quantity='10'
        )
        po = PurchaseOrder.objects.create(supplier=self.supplier, reference='PO-0005')
        PurchaseOrderLineItem.objects.create(
            order=po, part=packed_supplier_part, quantity=3
        )

        entries = self.forecast.generate_purchase_order_entries(
            self.part, include_variants=False
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['quantity'], 30)


class SalesOrderEntryTests(PartForecastTestCase):
    """Tests for `generate_sales_order_entries`."""

    def setUp(self):
        super().setUp()
        self.customer = Company.objects.create(name='Customer', is_customer=True)

    def test_open_order_decreases_forecast(self):
        """An open sales order line decreases the forecast at its target date."""
        so = SalesOrder.objects.create(customer=self.customer, reference='SO-0001')
        SalesOrderLineItem.objects.create(
            order=so, part=self.part, quantity=25, target_date=NEXT_WEEK
        )

        entries = self.forecast.generate_sales_order_entries(
            self.part, include_variants=False
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['quantity'], -25)
        self.assertEqual(entries[0]['date'], NEXT_WEEK)

    def test_shipped_quantity_is_offset(self):
        """Already-shipped stock is subtracted from the outstanding quantity."""
        so = SalesOrder.objects.create(customer=self.customer, reference='SO-0002')
        SalesOrderLineItem.objects.create(
            order=so, part=self.part, quantity=25, shipped=10
        )

        entries = self.forecast.generate_sales_order_entries(
            self.part, include_variants=False
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['quantity'], -15)

    def test_fully_shipped_line_excluded(self):
        """A line which has been fully shipped generates no forecast entry."""
        so = SalesOrder.objects.create(customer=self.customer, reference='SO-0003')
        SalesOrderLineItem.objects.create(
            order=so, part=self.part, quantity=25, shipped=25
        )

        entries = self.forecast.generate_sales_order_entries(
            self.part, include_variants=False
        )

        self.assertEqual(entries, [])


class BuildOrderEntryTests(PartForecastTestCase):
    """Tests for `generate_build_order_entries`."""

    def test_active_build_increases_forecast(self):
        """An active build order increases the forecast by its outstanding quantity."""
        build = Build.objects.create(
            part=self.part, quantity=50, reference='BO-0001', target_date=NEXT_WEEK
        )

        entries = self.forecast.generate_build_order_entries(
            self.part, include_variants=False
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['quantity'], 50)
        self.assertEqual(entries[0]['date'], NEXT_WEEK)
        self.assertEqual(entries[0]['model_id'], build.pk)

    def test_completed_quantity_is_offset(self):
        """Completed build output is subtracted from the outstanding quantity."""
        Build.objects.create(
            part=self.part, quantity=50, reference='BO-0002', completed=20
        )

        entries = self.forecast.generate_build_order_entries(
            self.part, include_variants=False
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['quantity'], 30)

    def test_cancelled_build_excluded(self):
        """A cancelled build order does not contribute to the forecast."""
        Build.objects.create(
            part=self.part,
            quantity=50,
            reference='BO-0003',
            status=BuildStatus.CANCELLED.value,
        )

        entries = self.forecast.generate_build_order_entries(
            self.part, include_variants=False
        )

        self.assertEqual(entries, [])


class BuildOrderAllocationTests(PartForecastTestCase):
    """Tests for `generate_build_order_allocations`."""

    def setUp(self):
        super().setUp()
        self.assembly = Part.objects.create(
            name='Assembly', description='Assembly which consumes the test part', assembly=True
        )
        # Creating 'assembly' can shift the `tree_id` MPTT assigned to `self.part`
        # in the base setUp() - refresh both before the BomItem 'recursive' check.
        self.part.refresh_from_db()
        self.assembly.refresh_from_db()
        self.bom_item = BomItem.objects.create(
            part=self.assembly, sub_part=self.part, quantity=2
        )

    def test_unconsumed_allocation_decreases_forecast(self):
        """An open build line for this part decreases the forecast by the outstanding quantity."""
        build = Build.objects.create(
            part=self.assembly, quantity=10, reference='BO-0010', start_date=NEXT_WEEK
        )
        line = build.build_lines.get(bom_item=self.bom_item)
        self.assertEqual(line.quantity, 20)

        entries = self.forecast.generate_build_order_allocations(
            self.part, include_variants=False
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['quantity'], -20)
        self.assertEqual(entries[0]['date'], NEXT_WEEK)

    def test_consumed_quantity_is_offset(self):
        """Already-consumed stock is subtracted from the required quantity."""
        build = Build.objects.create(part=self.assembly, quantity=10, reference='BO-0011')
        line = build.build_lines.get(bom_item=self.bom_item)
        line.consumed = 5
        line.save()

        entries = self.forecast.generate_build_order_allocations(
            self.part, include_variants=False
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['quantity'], -15)

    def test_fully_consumed_line_excluded(self):
        """A build line which has been fully consumed generates no forecast entry."""
        build = Build.objects.create(part=self.assembly, quantity=10, reference='BO-0012')
        line = build.build_lines.get(bom_item=self.bom_item)
        line.consumed = 20
        line.save()

        entries = self.forecast.generate_build_order_allocations(
            self.part, include_variants=False
        )

        self.assertEqual(entries, [])


class UpstreamEntryTests(PartForecastTestCase):
    """Tests for `generate_upstream_entries` - the multi-level BOM traversal."""

    def setUp(self):
        super().setUp()

        # Build a two-level assembly tree: self.part -> intermediate -> top
        # (self.part is a sub-component of 'intermediate', which is itself
        # a sub-component of 'top')
        self.intermediate = Part.objects.create(
            name='Intermediate', description='Mid-level assembly', assembly=True
        )
        self.top = Part.objects.create(
            name='Top', description='Top-level assembly', assembly=True
        )

        # Creating each new root Part can shift MPTT `tree_id` values assigned
        # to earlier ones - refresh all of them now that no more Parts will be
        # created, right before they're used in BomItem's 'recursive' check.
        self.part.refresh_from_db()
        self.intermediate.refresh_from_db()
        self.top.refresh_from_db()

        # 2x self.part required per 'intermediate'
        BomItem.objects.create(part=self.intermediate, sub_part=self.part, quantity=2)
        # 3x 'intermediate' required per 'top'
        BomItem.objects.create(part=self.top, sub_part=self.intermediate, quantity=3)

        self.customer = Company.objects.create(name='Customer', is_customer=True)

    def test_multiplier_compounds_across_levels(self):
        """Demand for the top-level assembly should propagate down with a compounded multiplier."""
        so = SalesOrder.objects.create(customer=self.customer, reference='SO-0010')
        SalesOrderLineItem.objects.create(order=so, part=self.top, quantity=5)

        entries = self.forecast.generate_upstream_entries(
            self.part, include_variants=False, include_upstream=True
        )

        # 5 units of 'top' -> 15 units of 'intermediate' -> 30 units of 'self.part'
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['quantity'], -30)

        chain = entries[0]['chain']
        self.assertEqual(len(chain), 3)
        chain_parts = [p.pk for p, _q in chain]
        self.assertEqual(
            chain_parts, [self.part.pk, self.intermediate.pk, self.top.pk]
        )

    def test_upstream_disabled_stops_propagation(self):
        """With include_upstream=False, only the exact part's own entries are returned."""
        so = SalesOrder.objects.create(customer=self.customer, reference='SO-0011')
        SalesOrderLineItem.objects.create(order=so, part=self.top, quantity=5)

        entries = self.forecast.generate_upstream_entries(
            self.part, include_variants=False, include_upstream=False
        )

        self.assertEqual(entries, [])

    def test_direct_order_still_included_when_upstream_disabled(self):
        """A sales order directly against the queried part is still included at level 0."""
        so = SalesOrder.objects.create(customer=self.customer, reference='SO-0012')
        SalesOrderLineItem.objects.create(order=so, part=self.part, quantity=8)

        entries = self.forecast.generate_upstream_entries(
            self.part, include_variants=False, include_upstream=False
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['quantity'], -8)


class PostProcessEntriesTests(PartForecastTestCase):
    """Table-driven tests for `post_process_entries`, using hand-built entry dicts."""

    def entry(self, quantity, chain=None):
        """Construct a minimal forecast entry dict for testing."""
        return {
            'date': NEXT_WEEK,
            'quantity': quantity,
            'label': 'test',
            'title': 'test',
            'model_type': 'testmodel',
            'model_id': 1,
            'part': None,
            'chain': chain,
        }

    def test_positive_quantity_passthrough(self):
        """Positive (incoming) quantities are never post-processed."""
        entries = [self.entry(10, chain=[(self.part, 1.0)])]

        result = self.forecast.post_process_entries(entries)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['quantity'], 10)

    def test_short_chain_passthrough(self):
        """A chain of length <= 1 is not offset against intermediate stock."""
        entries = [self.entry(-10, chain=[(self.part, 1.0)])]

        result = self.forecast.post_process_entries(entries)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['quantity'], -10)

    def test_full_offset_drops_entry(self):
        """An entry fully offset by available intermediate stock is dropped."""
        intermediate = Part.objects.create(
            name='Intermediate 2', description='x', assembly=True
        )
        # -20 units required at the bottom level, chain multiplier = 2
        # -> 10 units required of the intermediate part
        self.forecast.assembly_stock = {intermediate.pk: 10}
        entries = [self.entry(-20, chain=[(self.part, 1.0), (intermediate, 2.0)])]

        result = self.forecast.post_process_entries(entries)

        self.assertEqual(result, [])
        # All 10 available units of intermediate stock were consumed
        self.assertEqual(self.forecast.assembly_stock[intermediate.pk], 0)

    def test_partial_offset_reduces_entry(self):
        """An entry partially offset by available stock retains the remaining quantity."""
        intermediate = Part.objects.create(
            name='Intermediate 3', description='x', assembly=True
        )
        # -20 units required at the bottom level, chain multiplier = 2
        # -> 10 units required of the intermediate part, only 4 available
        self.forecast.assembly_stock = {intermediate.pk: 4}
        entries = [self.entry(-20, chain=[(self.part, 1.0), (intermediate, 2.0)])]

        result = self.forecast.post_process_entries(entries)

        self.assertEqual(len(result), 1)
        # 6 units of intermediate remain unfulfilled -> 6 * 2 = 12 units of the bottom part
        self.assertEqual(result[0]['quantity'], -12)
        self.assertEqual(self.forecast.assembly_stock[intermediate.pk], 0)

    def test_base_part_own_stock_is_never_used_as_an_offset(self):
        """The base part being forecasted (chain[0]) must never have its own
        stock spent to offset a chain-derived shortfall, no matter how much
        is available - only genuinely intermediate levels (chain[1:]) count.

        This matters because the base part's stock is already reflected
        elsewhere: as the starting point the API/frontend adds every entry's
        quantity on top of (`in_stock`), and (for on_order/being-built stock)
        as its own separate, unmodified purchase/build order entries. Letting
        `post_process_entries` also spend `assembly_stock[chain[0]]` credits
        that same stock a second time, silently shrinking - or, as here,
        completely erasing - a real shortfall for any chain that happens to
        cascade all the way back down to the base part.
        """
        intermediate = Part.objects.create(
            name='Intermediate 6', description='x', assembly=True
        )
        # -20 units required at the bottom level, chain multiplier = 2
        # -> 10 units required of the intermediate part, none available there,
        # but the base part itself has plenty of "stock" recorded.
        self.forecast.assembly_stock = {self.part.pk: 1000, intermediate.pk: 0}
        entries = [self.entry(-20, chain=[(self.part, 1.0), (intermediate, 2.0)])]

        result = self.forecast.post_process_entries(entries)

        self.assertEqual(len(result), 1)
        # Fully unfulfilled at the intermediate level -> the full -20 remains,
        # NOT offset (or dropped entirely) by the base part's own stock.
        self.assertEqual(result[0]['quantity'], -20)
        # The base part's stock entry is left completely untouched.
        self.assertEqual(self.forecast.assembly_stock[self.part.pk], 1000)

    def test_three_tier_chain_offsets_use_per_level_ratios(self):
        """A chain spanning 2+ upstream levels must convert stock offsets using the
        per-level BOM ratio between consecutive chain entries, not the cumulative
        (from-target-part) multiplier stored alongside each entry.

        Chain: self.part -> intermediate (2x per intermediate) -> top (3x
        intermediate per top, i.e. 6x self.part per top, cumulative).

        A sales order for 10 units of 'top', with 4 units of 'top' and 0 units
        of 'intermediate' in stock, and none of 'self.part' either:

        - 10 top short, offset by 4 in stock -> 6 top still short
        - convert to intermediate using the *per-level* ratio (top:intermediate
          = 3) -> 18 intermediate short, offset by 0 in stock -> still 18 short
        - convert to self.part using the *per-level* ratio (intermediate:part
          = 2) -> 36 units of self.part short

        The current implementation instead multiplies by the *cumulative*
        multiplier at each step (2.0, then 6.0), which - combined with the
        already-wrong `chain_multiplier` used to derive the initial raw
        quantity - under-reports the shortfall as only 12 units instead of 36,
        silently absorbing a real shortfall for this higher-level (grandparent)
        assembly. Chains of length 2 (a single upstream level) don't show this,
        because cumulative and per-level multipliers coincide when there's only
        one link - see test_partial_offset_reduces_entry above.
        """
        intermediate = Part.objects.create(
            name='Intermediate 5', description='x', assembly=True
        )
        top = Part.objects.create(name='Top 5', description='x', assembly=True)

        # -60 units required at the bottom level: 10 units of 'top' outstanding,
        # cumulative multiplier of 6 (2x self.part per intermediate, 3x
        # intermediate per top -> 6x self.part per top).
        self.forecast.assembly_stock = {top.pk: 4, intermediate.pk: 0}
        entries = [
            self.entry(
                -60, chain=[(self.part, 1.0), (intermediate, 2.0), (top, 6.0)]
            )
        ]

        result = self.forecast.post_process_entries(entries)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['quantity'], -36)
        self.assertEqual(self.forecast.assembly_stock[top.pk], 0)
        self.assertEqual(self.forecast.assembly_stock[intermediate.pk], 0)

    def test_stock_pool_shared_across_entries(self):
        """Available intermediate stock is decremented across sequential entries."""
        intermediate = Part.objects.create(
            name='Intermediate 4', description='x', assembly=True
        )
        # Each -10 entry normalizes to 5 units of 'intermediate' demand (chain
        # multiplier = 2) - 5 units of stock exactly covers the first entry,
        # leaving nothing for the second.
        self.forecast.assembly_stock = {intermediate.pk: 5}
        entries = [
            self.entry(-10, chain=[(self.part, 1.0), (intermediate, 2.0)]),
            self.entry(-10, chain=[(self.part, 1.0), (intermediate, 2.0)]),
        ]

        result = self.forecast.post_process_entries(entries)

        # First entry consumes all 5 available units of stock and is dropped.
        # Second entry has no stock left, so passes through unchanged.
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['quantity'], -10)
        self.assertEqual(self.forecast.assembly_stock[intermediate.pk], 0)


class GetEntriesTests(PartForecastTestCase):
    """Sanity tests for `get_entries` sorting/combination behaviour."""

    def test_entries_sorted_by_date(self):
        """Entries from different sources are merged and sorted by date."""
        supplier = Company.objects.create(name='Supplier', is_supplier=True)
        supplier_part = SupplierPart.objects.create(
            part=self.part, supplier=supplier, SKU='SKU-1'
        )
        po = PurchaseOrder.objects.create(supplier=supplier, reference='PO-0020')
        PurchaseOrderLineItem.objects.create(
            order=po, part=supplier_part, quantity=10, target_date=NEXT_WEEK
        )
        Build.objects.create(
            part=self.part, quantity=5, reference='BO-0020', target_date=TOMORROW
        )

        entries = self.forecast.get_entries(self.part)

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]['date'], TOMORROW)
        self.assertEqual(entries[1]['date'], NEXT_WEEK)
