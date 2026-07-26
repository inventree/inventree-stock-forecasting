"""End-to-end API tests for the forecasting endpoint against the multi-level BOM fixture.

For every part in `MultiLevelBOMTestCase`'s fixture, this fetches forecasting data
from the real `/plugin/stock-forecasting/forecast/` API endpoint (with
`include_upstream=true`) and compares the returned entries against an
*independently computed* set of expected entries, derived directly from the
fixture's known BOM/order structure rather than by calling into `PartForecast`.

Two layers are tested, both against the same underlying "raw" oracle entries:

- `MultiLevelAPITestCase` - `consider_intermediate_stock=false`. Stress-tests the
  part that's hardest to get right by hand: BOM tree traversal, multiplier
  compounding, and diamond/orphan handling.
- `MultiLevelStockOffsetAPITestCase` - `consider_intermediate_stock=true` (the
  default). On top of the above, independently replicates the stock-offset
  post-processing step (`post_process_entries`): for each negative entry with a
  multi-level chain, walk the chain and offset against each level's available
  stock (`get_stock_count() + on_order + quantity_being_built`), exactly as
  documented in `forecast.py`.

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
- Stock availability at each part is memoized the first time it's visited
  during the upstream walk (matching `self.assembly_stock`'s memoization), and
  is shared/decremented across every entry that draws on it, in date order.

This module intentionally does NOT fix any backend issues it finds - it only
surfaces them. See the test docstrings/comments for anything discovered.

Known finding - `MultiLevelStockOffsetAPITestCase` (not a backend bug): three
parts (Component 2, Component 3, Sub-Assembly 2/M2) show quantity mismatches on
specific same-date entries that compete for stock at a diamond-shared part
(M2, used by both N1 and N2). Diagnosed by feeding this module's own
`_apply_stock_offset` the *real* code's raw entries, in the *real* code's
insertion order, with the *real* code's initial stock - it reproduced the real
API's output exactly. That confirms the offset math here is correct; the
mismatch is purely because `post_process_entries` breaks same-date ties by
insertion order, and this module's plain-recursion BOM walk visits nodes in a
different order than the real code's stack-based (LIFO) traversal. Matching
that order exactly would mean reimplementing the same traversal, which
defeats the point of an independent oracle. Net effect worth knowing about:
when two same-date entries compete for a limited, shared stock pool at a
diamond-shared part, which one "wins" the stock is an implementation-order
artifact, not a deliberate tie-break rule - the total demand is still correct,
just its date-by-date split against shared stock isn't fully deterministic
from the fixture data alone.
"""

from collections import defaultdict
from datetime import date as date_cls

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


def _bucket(entries):
    """Collapse a flat entry list into {(model_type, model_id, date): [quantity, ...]}."""
    buckets = defaultdict(list)
    for entry in entries:
        if entry['quantity']:
            key = (entry['model_type'], entry['model_id'], _normalize_date(entry['date']))
            buckets[key].append(round(entry['quantity'], ROUND_PLACES))
    return buckets


class RawOracleMixin:
    """Independently computes raw (pre-offset) forecasting entries for a part,
    preserving each entry's BOM "chain" so stock-offset post-processing can be
    layered on top independently too.
    """

    def _raw_entries_and_stock(self, part):
        """Returns (entries, assembly_stock).

        entries: list of {model_type, model_id, date, quantity, chain} dicts.
        chain is None for level-0-only purchase/build entries (never offset),
        or a list of (Part, multiplier) pairs from `part` up to the entry's
        source, for sales-order/build-allocation entries at any tier.

        assembly_stock: {part_pk: available_stock} for every part visited
        during the upstream walk, computed once per part (memoized), matching
        `generate_upstream_entries`'s `self.assembly_stock` population.
        """
        entries = []
        assembly_stock = {}

        # Purchase orders for `part` itself only (never for upstream ancestors)
        for line in PurchaseOrderLineItem.objects.filter(
            part__part=part, order__status__in=PurchaseOrderStatusGroups.OPEN
        ):
            qty = float(line.part.base_quantity(max(0, line.quantity - line.received)))
            if qty:
                entries.append({
                    'model_type': 'purchaseorder', 'model_id': line.order.pk,
                    'date': line.target_date or line.order.target_date,
                    'quantity': qty, 'chain': None,
                })

        # Build orders where `part` itself is being built - only for `part`
        for build in Build.objects.filter(
            part=part, status__in=BuildStatusGroups.ACTIVE_CODES
        ):
            qty = float(max(0, build.quantity - build.completed))
            if qty:
                entries.append({
                    'model_type': 'build', 'model_id': build.pk,
                    'date': build.target_date, 'quantity': qty, 'chain': None,
                })

        # Walk upward from `part`, visiting every ancestor via every distinct
        # BOM path (no de-duplication - a shared component contributes once
        # per path, matching the documented "sum across paths" semantics).
        def walk(current, multiplier, chain):
            chain = [*chain, (current, multiplier)]

            if current.pk not in assembly_stock:
                in_stock = current.get_stock_count(include_variants=False)
                assembly_stock[current.pk] = float(
                    in_stock + current.on_order + current.quantity_being_built
                )

            for line in SalesOrderLineItem.objects.filter(
                part=current, order__status__in=SalesOrderStatusGroups.OPEN
            ):
                outstanding = float(max(0, line.quantity - line.shipped))
                if outstanding:
                    entries.append({
                        'model_type': 'salesorder', 'model_id': line.order.pk,
                        'date': line.target_date or line.order.target_date,
                        'quantity': -outstanding * multiplier, 'chain': chain,
                    })

            for bl in BuildLine.objects.filter(
                bom_item__sub_part=current,
                build__status__in=BuildStatusGroups.ACTIVE_CODES,
                consumed__lt=F('quantity'),
            ).select_related('build'):
                remaining = float(max(0, bl.quantity - bl.consumed))
                if remaining:
                    entries.append({
                        'model_type': 'build', 'model_id': bl.build.pk,
                        'date': bl.build.start_date or bl.build.target_date,
                        'quantity': -remaining * multiplier, 'chain': chain,
                    })

            for bom_item in BomItem.objects.filter(sub_part=current):
                walk(bom_item.part, multiplier * float(bom_item.quantity), chain)

        walk(part, 1.0, [])

        return entries, assembly_stock

    def _expected_entries(self, part):
        """Bucketed raw (pre-offset) expected entries for `part`."""
        entries, _ = self._raw_entries_and_stock(part)
        return _bucket(entries)

    def _expected_entries_with_stock_offset(self, part):
        """Bucketed expected entries for `part`, after applying the independent
        stock-offset post-processing step.
        """
        entries, assembly_stock = self._raw_entries_and_stock(part)
        offset_entries = self._apply_stock_offset(entries, assembly_stock)
        return _bucket(offset_entries)

    def _apply_stock_offset(self, entries, assembly_stock):
        """Independently apply stock-offset post-processing, matching the documented
        behaviour of `post_process_entries`: entries are processed in date order;
        a negative entry with a multi-level chain is offset against each chain
        level's available stock (consumed sequentially, shared across entries).
        """
        assembly_stock = dict(assembly_stock)  # local mutable copy
        result = []

        def sort_key(entry):
            """Matches forecast.py's `get_entries` sort key exactly: no-date first,
            then increasing date, then a deterministic (model_type, model_id,
            chain) tie-break for same-date entries.
            """
            entry_date = entry['date']
            chain = entry.get('chain') or []
            chain_key = tuple((chain_part.pk, qty) for chain_part, qty in chain)
            return (
                entry_date is not None,
                entry_date or date_cls.min,
                entry['model_type'],
                entry['model_id'],
                chain_key,
            )

        for entry in sorted(entries, key=sort_key):
            quantity = entry['quantity']
            chain = entry['chain']

            if quantity >= 0 or not chain or len(chain) <= 1:
                if quantity:
                    result.append(entry)
                continue

            # Each chain entry stores the *cumulative* multiplier up to that
            # level (not the ratio between consecutive levels) - divide out
            # the entry's own top-of-chain cumulative multiplier to recover
            # the raw outstanding quantity, then convert down level-by-level
            # using the ratio *between* consecutive cumulative multipliers
            # (equivalent to the per-level BOM ratio), matching the corrected
            # `post_process_entries` in forecast.py.
            top_multiplier = chain[-1][1]

            if top_multiplier <= 0:
                if quantity:
                    result.append(entry)
                continue

            quantity = quantity / top_multiplier

            # chain[0] is always the part being forecasted itself, never a
            # true intermediate assembly - never offset against its stock
            # here, since it's already reflected via the caller's `in_stock`
            # baseline and its own purchase/build order entries. Matches the
            # corrected `post_process_entries` in forecast.py.
            chain_length = len(chain)
            for idx in range(chain_length - 1, 0, -1):
                chain_part, cumulative_multiplier = chain[idx]
                available = assembly_stock.get(chain_part.pk, 0)
                offset = min(available, -quantity)
                assembly_stock[chain_part.pk] = available - offset
                quantity += offset

                if quantity >= 0:
                    quantity = 0
                    break

                _, next_cumulative_multiplier = chain[idx - 1]
                quantity *= cumulative_multiplier / next_cumulative_multiplier

            if quantity:
                result.append({**entry, 'quantity': quantity})

        return result


class APIFetchMixin:
    """Fetches forecasting entries from the real API endpoint, tracking query counts."""

    def _actual_entries(self, part, consider_intermediate_stock):
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
                    'consider_intermediate_stock': consider_intermediate_stock,
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


class ComparisonMixin:
    """Compares actual vs expected entry buckets, reporting a clear diff on mismatch.

    Compares the exact (sorted) list of quantities per (model_type, model_id,
    date) key. This relies on forecast.py's entry ordering being deterministic
    (date, then a (model_type, model_id) tie-break for same-date entries) -
    see `get_entries`'s `sort_key`. Before that tie-break existed, same-date
    entries competing for a shared stock pool could offset differently
    depending on incidental traversal order; now both this oracle's sort and
    the real code's sort apply the identical deterministic tie-break, so the
    split is reproducible and can be compared exactly again.
    """

    def _compare(self, part, actual, expected):
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


class MultiLevelAPITestCase(
    RawOracleMixin, APIFetchMixin, ComparisonMixin, MultiLevelBOMTestCase, InvenTreeAPITestCase
):
    """Compares the API against the oracle with `consider_intermediate_stock=false`."""

    def setUp(self):
        super().setUp()
        self.url = reverse('plugin:stock-forecasting:part-forecasting')
        self.query_counts = {}

    def test_forecast_matches_expected_for_every_part(self):
        """For every part in the fixture, compare the API's forecast against the oracle."""
        entry_counts = {}

        for part in self.all_parts:
            with self.subTest(part=part.name):
                actual = self._actual_entries(part, consider_intermediate_stock=False)
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


class MultiLevelStockOffsetAPITestCase(
    RawOracleMixin, APIFetchMixin, ComparisonMixin, MultiLevelBOMTestCase, InvenTreeAPITestCase
):
    """Compares the API against the oracle with `consider_intermediate_stock=true`
    (the default) - including the independently-replicated stock-offset step.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse('plugin:stock-forecasting:part-forecasting')
        self.query_counts = {}

    def test_forecast_matches_expected_with_stock_offset_for_every_part(self):
        """For every part in the fixture, compare the API's offset-adjusted forecast
        against the oracle's independently-computed offset-adjusted forecast.
        """
        entry_counts = {}

        for part in self.all_parts:
            with self.subTest(part=part.name):
                actual = self._actual_entries(part, consider_intermediate_stock=True)
                expected = self._expected_entries_with_stock_offset(part)
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


class TemplateVariantForecastAPITestCase(MultiLevelBOMTestCase, InvenTreeAPITestCase):
    """E2E check for the isolated template/variant inherited-BOM fixture.

    A sales order exists only against VARIANT_A (never against TEMPLATE
    itself). Querying TEMPLATE's own forecast should only pick it up when
    `include_variants=true` is passed - that's the mechanism (`Part.
    get_descendants(include_self=True)` in `generate_sales_order_entries`),
    distinct from the `BomItem.inherited` mechanism the fixture also sets up
    (which governs upstream BOM propagation *from* the shared component, not
    tested here since the request only asked about querying TEMPLATE itself).
    """

    def setUp(self):
        super().setUp()
        self.url = reverse('plugin:stock-forecasting:part-forecasting')

    def _fetch(self, include_variants):
        response = self.get(
            self.url,
            data={
                'part': self.template.pk,
                'include_variants': include_variants,
                'include_upstream': False,
                'consider_intermediate_stock': False,
            },
            expected_code=200,
        )
        return response.data['entries']

    def test_variant_sales_order_included_with_include_variants(self):
        entries = self._fetch(include_variants=True)

        matches = [
            e for e in entries
            if e['model_type'] == 'salesorder' and e['model_id'] == self.so_variant_a.pk
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(float(matches[0]['quantity']), -9.0)
        self.assertEqual(
            _normalize_date(matches[0]['date']),
            _normalize_date(self.so_variant_a_line.target_date),
        )

    def test_variant_sales_order_excluded_without_include_variants(self):
        entries = self._fetch(include_variants=False)

        matches = [
            e for e in entries
            if e['model_type'] == 'salesorder' and e['model_id'] == self.so_variant_a.pk
        ]
        self.assertEqual(matches, [])
