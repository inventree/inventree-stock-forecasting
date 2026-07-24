"""End-to-end API tests for the forecasting endpoint against the multi-level BOM fixture.

For every part in `MultiLevelBOMTestCase`'s fixture, this fetches forecasting data
from the real `/plugin/stock-forecasting/forecast/` API endpoint (with
`include_upstream=true`) and compares the returned entries against an
*independently computed* set of expected entries, derived directly from the
fixture's known BOM/order structure rather than by calling into `PartForecast`.

Scope decision: comparisons are made with `consider_intermediate_stock=false`.
The stock-offset behaviour of `post_process_entries` already has focused,
hand-verified unit test coverage in `test_forecasting.py`; replicating that
stateful, order-dependent offset logic as a second independent oracle across a
four-tier diamond graph with dozens of orders would be both impractical and
itself error-prone. This module instead focuses on the part that's hardest to
get right by hand: BOM tree traversal, multiplier compounding, and diamond/
orphan handling.

Oracle semantics (matching the documented behaviour in `forecast.py`):

- Purchase orders and "this part is being built" build orders are only
  considered for the exact queried part (never for upstream ancestors).
- Sales orders and "this part is consumed by a build" allocations are
  considered at *every* tier reached during the upstream walk, including the
  queried part itself.
- A part reachable via multiple independent BOM paths (e.g. M2 under both N1
  and N2) is visited once per path, each contributing its own
  multiplier-scaled entries - by design, not a bug: the total demand for a
  shared component is the sum across all paths that lead to it.

This module intentionally does NOT fix any backend issues it finds - it only
surfaces them. See the test docstrings/comments for anything discovered.
"""

from collections import defaultdict

from build.models import Build, BuildLine
from build.status_codes import BuildStatusGroups
from django.db import connection
from django.db.models import F
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from order.models import PurchaseOrderLineItem, SalesOrderLineItem
from order.status_codes import PurchaseOrderStatusGroups, SalesOrderStatusGroups
from part.models import BomItem

from InvenTree.unit_test import InvenTreeAPITestCase

from .test_multi_level import MultiLevelBOMTestCase

ROUND_PLACES = 6


def _normalize_date(value):
    """Normalize a date value (native `date` object or ISO string) to a plain string,
    so oracle-computed dates and API-serialized dates compare equal.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


class MultiLevelAPITestCase(MultiLevelBOMTestCase, InvenTreeAPITestCase):
    """Combines the multi-level BOM fixture with API test helpers."""

    def setUp(self):
        super().setUp()
        self.url = reverse('plugin:stock-forecasting:part-forecasting')
        self.query_counts = {}

    # -- Independent oracle -------------------------------------------------

    def _expected_entries(self, part):
        """Independently compute expected (pre-offset) forecasting entries for `part`.

        Returns a dict of {(model_type, model_id, date): [quantity, ...]}.
        """
        buckets = defaultdict(list)

        def add(model_type, model_id, date, quantity):
            if quantity:
                key = (model_type, model_id, _normalize_date(date))
                buckets[key].append(round(quantity, ROUND_PLACES))

        # Purchase orders for `part` itself only (never for upstream ancestors)
        for line in PurchaseOrderLineItem.objects.filter(
            part__part=part, order__status__in=PurchaseOrderStatusGroups.OPEN
        ):
            qty = line.part.base_quantity(max(0, line.quantity - line.received))
            add(
                'purchaseorder', line.order.pk,
                line.target_date or line.order.target_date,
                float(qty),
            )

        # Build orders where `part` itself is being built - only for `part`
        for build in Build.objects.filter(
            part=part, status__in=BuildStatusGroups.ACTIVE_CODES
        ):
            qty = max(0, build.quantity - build.completed)
            add('build', build.pk, build.target_date, float(qty))

        # Walk upward from `part`, visiting every ancestor via every distinct
        # BOM path (no de-duplication - a shared component contributes once
        # per path, matching the documented "sum across paths" semantics).
        def walk(current, multiplier):
            for line in SalesOrderLineItem.objects.filter(
                part=current, order__status__in=SalesOrderStatusGroups.OPEN
            ):
                outstanding = float(max(0, line.quantity - line.shipped))
                add(
                    'salesorder', line.order.pk,
                    line.target_date or line.order.target_date,
                    -outstanding * multiplier,
                )

            for bl in BuildLine.objects.filter(
                bom_item__sub_part=current,
                build__status__in=BuildStatusGroups.ACTIVE_CODES,
                consumed__lt=F('quantity'),
            ).select_related('build'):
                remaining = float(max(0, bl.quantity - bl.consumed))
                add(
                    'build', bl.build.pk,
                    bl.build.start_date or bl.build.target_date,
                    -remaining * multiplier,
                )

            for bom_item in BomItem.objects.filter(sub_part=current):
                walk(bom_item.part, multiplier * float(bom_item.quantity))

        walk(part, 1.0)

        return buckets

    # -- API fetch ------------------------------------------------------

    def _actual_entries(self, part):
        """Fetch forecasting entries for `part` from the real API endpoint.

        Bypasses `self.get()`'s built-in query-count assertion (default budget
        100) so a part which exceeds it doesn't block the entry-level
        comparison - the query count is captured and recorded separately in
        `self.query_counts` instead, so it still shows up as a finding.
        """
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(
                self.url,
                data={
                    'part': part.pk,
                    'include_upstream': True,
                    'include_variants': False,
                    'consider_intermediate_stock': False,
                },
                format='json',
            )

        self.query_counts[part.name] = len(ctx.captured_queries)
        self.assertEqual(response.status_code, 200)

        buckets = defaultdict(list)
        for entry in response.data['entries']:
            key = (entry['model_type'], entry['model_id'], _normalize_date(entry['date']))
            buckets[key].append(round(float(entry['quantity']), ROUND_PLACES))

        return buckets

    # -- Comparison -------------------------------------------------------

    def _compare(self, part, actual, expected):
        """Compare actual vs expected entry buckets, reporting a clear diff on mismatch."""
        actual_keys = set(actual.keys())
        expected_keys = set(expected.keys())

        missing = expected_keys - actual_keys  # expected, but API didn't return
        extra = actual_keys - expected_keys  # API returned, but not expected

        quantity_mismatches = {}
        for key in actual_keys & expected_keys:
            a = sorted(actual[key])
            e = sorted(expected[key])
            if a != e:
                quantity_mismatches[key] = {'actual': a, 'expected': e}

        if missing or extra or quantity_mismatches:
            lines = [f'Mismatch for part {part.name!r} (pk={part.pk}):']
            if missing:
                lines.append(f'  MISSING from API response ({len(missing)}):')
                for key in sorted(missing):
                    lines.append(f'    {key}: expected {sorted(expected[key])}')
            if extra:
                lines.append(f'  UNEXPECTED in API response ({len(extra)}):')
                for key in sorted(extra):
                    lines.append(f'    {key}: got {sorted(actual[key])}')
            if quantity_mismatches:
                lines.append(f'  QUANTITY MISMATCH ({len(quantity_mismatches)}):')
                for key, diff in sorted(quantity_mismatches.items()):
                    lines.append(f'    {key}: expected {diff["expected"]}, got {diff["actual"]}')

            self.fail('\n'.join(lines))

    # -- Test entry point ---------------------------------------------------

    def test_forecast_matches_expected_for_every_part(self):
        """For every part in the fixture, compare the API's forecast against the oracle."""
        entry_counts = {}

        for part in self.all_parts:
            with self.subTest(part=part.name):
                actual = self._actual_entries(part)
                expected = self._expected_entries(part)
                entry_counts[part.name] = (
                    sum(len(v) for v in actual.values()),
                    sum(len(v) for v in expected.values()),
                )
                self._compare(part, actual, expected)

        print('\nEntry counts per part (actual, expected) - sanity check against a hollow pass:')
        for name, (n_actual, n_expected) in entry_counts.items():
            print(f'  {name}: actual={n_actual}, expected={n_expected}')

        print('\nQuery counts per part (InvenTreeAPITestCase default budget is 100):')
        for name, count in self.query_counts.items():
            flag = ' <-- exceeds default budget' if count >= 100 else ''
            print(f'  {name}: {count}{flag}')
