"""Core forecasting calculation logic for the InvenTree Forecasting plugin."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

import build.models as build_models
import build.status_codes as build_status
import order.models as order_models
import order.status_codes as order_status
import part.models as part_models
import part.serializers as part_serializers
from django.db.models import F, Model, Q
from django.utils.translation import gettext_lazy as _


class PartForecast:
    """Calculates forecasted stock levels for a given part, based on open orders."""

    def __init__(self):
        # Available stock for each intermediate assembly, used to offset demand.
        # Populated (in `generate_upstream_entries`) for every part visited
        # during the upstream walk, including the base part being forecasted -
        # but `post_process_entries` must never spend the base part's own
        # entry, since its stock is already reflected via the caller's
        # `in_stock` baseline and its own purchase/build order entries.
        self.assembly_stock = {}

        # Per-part query result caches, keyed by part pk. A part reached via
        # multiple independent BOM paths (e.g. a shared component under a
        # diamond dependency) is visited once per path, but its own direct
        # sales order lines / build line allocations / "who uses me" BOM
        # items don't depend on which path we arrived by - only fetch them
        # from the database once per distinct part, and reuse across visits.
        self._sales_order_line_cache = {}
        self._build_line_cache = {}
        self._used_in_bom_items_cache = {}

    def post_process_entries(self, entries: list) -> list:
        """Post-process the list of forecasting entries before returning to the user.

        At this point, the entries are sorted (by date),
        and we also have a complete picture of the stock availability of any intermediate assemblies

        Every entry is kept in the returned list, even one whose demand is
        entirely covered by intermediate assembly stock (`quantity` offset
        down to 0) - such an entry has no further effect on the forecasted
        part's own stock level, but it's still a real order the user may want
        visibility into. `quantity` always reflects the actual (post-offset)
        impact on the forecasted part; `original_quantity` (set once in
        `generate_entry` and never modified here) preserves what was
        originally required, so callers can detect - and flag - entries whose
        demand was wholly or partially absorbed upstream.
        """

        for entry in entries:
            quantity = entry.get("quantity", 0)

            if quantity >= 0:
                # Positive quantity values can be ignored for post-processing
                continue

            chain = entry.get("chain")

            if not chain or len(chain) <= 1:
                continue

            # Each chain entry stores the *cumulative* multiplier (relative to
            # the base part) up to that level, not the ratio between
            # consecutive levels - the entry's quantity was generated using
            # the last (highest-level) chain entry's cumulative multiplier, so
            # divide that back out to recover the raw outstanding quantity, in
            # units of that top-level part.
            top_multiplier = chain[-1][1]

            if top_multiplier <= 0:
                # Defensive check - this should not happen, but we want to avoid any potential issues with zero or negative multipliers
                continue

            # Start with the raw quantity required for the entry
            quantity = Decimal(quantity) / Decimal(top_multiplier)

            # For a "chained" entry we iterate backwards down the chain, and offset the quantity by the available stock at each level.
            #
            # chain[0] is always the base part being forecasted (the `part`
            # passed to `get_entries`), never a true intermediate assembly -
            # deliberately stop at idx=1 and never offset against
            # `assembly_stock[chain[0][0].pk]`. That part's current stock is
            # already the caller's `in_stock` baseline that entries are added
            # on top of (see ForecastingPanel's running total, and
            # `export_data`), and its incoming purchase/build orders already
            # appear as their own unmodified (chain=None) positive entries.
            # Offsetting here too would silently spend that same stock a
            # second (or third) time, shrinking shortfalls that happen to
            # route all the way back down to the base part.
            chain_length = len(chain)
            for idx in range(chain_length - 1, 0, -1):
                part, cumulative_multiplier = chain[idx]

                # How much intermediate stock is available?
                available = self.assembly_stock.get(part.pk, 0)

                # Offset the quantity by the available stock
                offset = min(available, -1 * quantity)
                self.assembly_stock[part.pk] = available - offset
                quantity += Decimal(offset)

                if quantity >= 0:
                    # If the quantity has been fully offset by available stock, we can stop processing this entry
                    quantity = 0
                    break

                # Convert the remaining shortfall down to the next (lower)
                # level's units, using the ratio between this level's and
                # the next level's *cumulative* multipliers - which is the
                # per-level BOM ratio between them. Using the cumulative
                # multiplier directly here (as opposed to this ratio)
                # double-counts every level above the base part.
                _, next_cumulative_multiplier = chain[idx - 1]
                level_ratio = cumulative_multiplier / next_cumulative_multiplier
                quantity *= Decimal(level_ratio)

            # Update the entry with the post-processed quantity. `original_quantity`
            # (set in `generate_entry`) is left untouched, so callers can tell this
            # entry's demand was (fully or partially) covered by intermediate stock.
            entry["quantity"] = quantity

        return entries

    def get_entries(
        self,
        part: part_models.Part,
        include_variants: bool = False,
        include_upstream: bool = False,
    ) -> list:
        """Fetch forecasting entries for the given part.

        Arguments:
            part (part_models.Part): The part for which to fetch forecasting entries.
            include_variants (bool): Whether to include variant parts in the stock count.
            include_upstream (bool): Whether to include upstream orders in the forecasting data.
        """
        entries = [
            *self.generate_purchase_order_entries(part, include_variants),
            *self.generate_build_order_entries(part, include_variants),
            *self.generate_upstream_entries(
                part,
                include_variants=include_variants,
                include_upstream=include_upstream,
            ),
        ]

        def sort_key(entry: dict):
            """Sort key for forecasting entries.

            - Entries with no date sort first
            - Then in increasing order of date
            - Then by (model_type, model_id, chain), as a deterministic
              tie-break for entries sharing the same date. Without this,
              entries with equal dates keep whatever order the upstream BOM
              walk happened to generate them in - which can vary based on
              traversal order and is not itself meaningful. This matters when
              post_process_entries offsets several same-date entries against a
              shared, limited stock pool: which entry gets processed (and
              offset) first can change the result, so a stable, explicit
              tie-break ensures the same input always produces the same
              output. `chain` is included because a single Build/Order can
              contribute more than one entry at the same tier (e.g. an
              assembly whose BOM needs two different sub-parts that are both
              reachable from the part being queried) - those entries share the
              same model_type/model_id, so chain (which differs per BOM path)
              is needed to fully distinguish them.
            """
            entry_date = entry["date"]
            chain = entry.get("chain") or []
            chain_key = tuple((chain_part.pk, qty) for chain_part, qty in chain)
            return (
                entry_date is not None,
                entry_date or date.min,
                entry["model_type"],
                entry["model_id"],
                chain_key,
            )

        # Sort by date (deterministically, including same-date tie-breaking)
        entries = sorted(entries, key=sort_key)

        return entries

    def generate_entry(
        self,
        instance: Model,
        quantity: float,
        date: date | None = None,
        part: part_models.Part | None = None,
        title: str = "",
        multiplier: float = 1.0,
        chain: list | None = None,
    ):
        """Generate a forecasting entry for a part.

        Arguments:
            part: The part for which to generate the entry.
            instance (Model): The model instance (e.g., PurchaseOrder) for which the entry is associated
            quantity (float): The forecasted quantity.
            date (date): The date for the forecast entry.
            title (str): Optional title for the entry.
            multiplier (float): A multiplier to apply to the quantity (e.g., to account for higher level assemblies)
        """

        # If a part is provided, serialize it for inclusion in the entry
        if part:
            part = part_serializers.PartBriefSerializer(part, pricing=False).data

        raw_quantity = float(quantity) * multiplier

        return {
            "date": date,
            "quantity": raw_quantity,
            # The quantity as originally required, before `post_process_entries`
            # may offset (part or all of) it against intermediate assembly
            # stock. Set once here and never touched again - `quantity` above
            # is the only field `post_process_entries` mutates - so the two
            # can be compared afterwards to show how much of an entry's demand
            # was silently covered by stock elsewhere in the chain.
            "original_quantity": raw_quantity,
            "label": instance.reference,
            "title": str(title),
            "model_type": instance.__class__.__name__.lower(),
            "model_id": instance.pk,
            "part": part,
            "chain": chain,
        }

    def generate_purchase_order_entries(
        self, part: part_models.Part, include_variants: bool
    ) -> list:
        """Generate forecasting entries for purchase orders related to the part.

        - We look at all pending purchase orders which might supply this part.
        - These orders will increase the forecasted quantity for the part.
        - We do not include purchase orders which are already completed or cancelled.
        """
        entries = []

        # Find all open purchase order line items
        po_lines = order_models.PurchaseOrderLineItem.objects.filter(
            order__status__in=order_status.PurchaseOrderStatusGroups.OPEN,
        ).select_related("order", "part", "part__part")

        if include_variants:
            # Filter lines to include any variants of the provided part
            variants = part.get_descendants(include_self=True)
            po_lines = po_lines.filter(part__part__in=variants)
        else:
            # Filter lines to only include the exact part
            po_lines = po_lines.filter(part__part=part)

        for line in po_lines:
            # Determine the expected delivery date and quantity
            # Account for supplier pack size
            target_date = line.target_date or line.order.target_date
            line_quantity = max(0, line.quantity - line.received)
            quantity = line.part.base_quantity(line_quantity)

            if abs(quantity) > 0:
                entries.append(
                    self.generate_entry(
                        line.order,
                        quantity,
                        target_date,
                        chain=None,
                        part=line.part.part if line.part and line.part.part else None,
                        title=_("Incoming Purchase Order"),
                    )
                )

        return entries

    def generate_sales_order_entries(
        self,
        part: part_models.Part,
        include_variants: bool,
        multiplier: float = 1.0,
        chain: list | None = None,
    ) -> list:
        """Generate forecasting entries for sales orders related to the part.

        Arguments:
            part (part_models.Part): The part for which to generate entries.
            include_variants (bool): Whether to include variant parts in the stock count.
            multiplier (float): A multiplier to apply to the quantity (e.g., to account for higher level assemblies).
        """
        entries = []

        if part.pk not in self._sales_order_line_cache:
            # Find all open sales order line items
            so_lines = order_models.SalesOrderLineItem.objects.filter(
                order__status__in=order_status.SalesOrderStatusGroups.OPEN
            ).select_related("order", "part")

            if include_variants:
                # Filter lines to include any variants of the provided part
                variants = part.get_descendants(include_self=True)
                so_lines = so_lines.filter(part__in=variants)
            else:
                # Filter lines to only include the exact part
                so_lines = so_lines.filter(part=part)

            self._sales_order_line_cache[part.pk] = list(so_lines)

        for line in self._sales_order_line_cache[part.pk]:
            target_date = line.target_date or line.order.target_date
            # Negative quantities indicate outgoing sales orders

            # The outstanding quantity which will be required
            outstanding = max(0, line.quantity - line.shipped)

            if abs(outstanding) > 0:
                entries.append(
                    self.generate_entry(
                        line.order,
                        -1 * outstanding,
                        target_date,
                        title=_("Outgoing Sales Order"),
                        multiplier=multiplier,
                        part=line.part,
                        chain=chain,
                    )
                )

        return entries

    def generate_build_order_entries(
        self, part: part_models.Part, include_variants: bool
    ) -> list:
        """Generate forecasting entries for build orders related to the part.

        This is a list of build orders which will *increase* the stock level of this part,
        as they represent assemblies of this part which are currently in progress.
        """
        entries = []

        # Find all open build orders
        build_orders = build_models.Build.objects.filter(
            status__in=build_status.BuildStatusGroups.ACTIVE_CODES
        ).select_related("part")

        if include_variants:
            # Filter builds to include any variants of the provided part
            variants = part.get_descendants(include_self=True)
            build_orders = build_orders.filter(part__in=variants)
        else:
            # Filter builds to only include the exact part
            build_orders = build_orders.filter(part=part)

        for build in build_orders:
            quantity = max(build.quantity - build.completed, 0)

            if abs(quantity) > 0:
                entries.append(
                    self.generate_entry(
                        build,
                        quantity,
                        build.target_date,
                        part=build.part,
                        title=_("Assembled via Build Order"),
                        chain=None,
                    )
                )

        return entries

    def generate_build_order_allocations(
        self,
        part: part_models.Part,
        include_variants: bool,
        multiplier: float = 1.0,
        chain: list | None = None,
    ) -> list:
        """Generate forecasting entries for build order allocations related to the part.

        This is essentially the amount of this part required to fulfill open build orders.

        Arguments:
            part (part_models.Part): The part for which to generate entries.
            include_variants (bool): Whether to include variant parts in the stock count.
            multiplier (float): A multiplier to apply to the required quantity (e.g., to account for higher level assemblies).
            chain: Optional list of parent assemblies and their quantities, used to provide context for the entry.

        Here we need some careful consideration:

        - 'Tracked' stock items are removed from stock when the individual Build Output is completed
        - 'Untracked' stock items are removed from stock when the Build Order is completed

        The 'simplest' approach here is to look at existing BuildItem allocations which reference this part,
        and "schedule" them for removal at the time of build order completion.

        This assumes that the user is responsible for correctly allocating parts.

        However, it has the added benefit of side-stepping the various BOM substitution options,
        and just looking at what stock items the user has actually allocated against the Build.
        """
        entries = []

        if part.pk not in self._build_line_cache:
            if include_variants:
                # If we are including variants, get all descendants of the part
                parts = list(part.get_descendants(include_self=True))
            else:
                # Only include the exact part
                parts = [part]

            # We now have a list of parts to check
            # For each part, look at any outstanding build lines which reference this part
            lines = build_models.BuildLine.objects.filter(
                bom_item__sub_part__in=parts,
                build__status__in=build_status.BuildStatusGroups.ACTIVE_CODES,
                consumed__lt=F("quantity"),
            ).select_related("build", "bom_item", "bom_item__part")

            self._build_line_cache[part.pk] = list(lines)

        for line in self._build_line_cache[part.pk]:
            remaining = max(0, line.quantity - line.consumed)

            if remaining > 0:
                entries.append(
                    self.generate_entry(
                        line.build,
                        -1 * remaining,
                        line.build.start_date or line.build.target_date,
                        title=_("Required for Build Order"),
                        part=line.bom_item.part,
                        multiplier=multiplier,
                        chain=chain,
                    )
                )

        return entries

    def generate_upstream_entries(
        self,
        part: part_models.Part,
        include_variants: bool = False,
        include_upstream: bool = False,
    ) -> dict:
        """Generate a forecasting entry for upstream orders related to the part.

        - This looks at forecasting for any assemblies which use this part - and any higher level assemblies too
        - For each of those assemblies, we look at any outstanding build orders or sales orders which require the part

        This is a two-pass operation, to keep the number of database queries
        roughly proportional to the *shape* of the BOM tree (its depth and
        breadth) rather than the number of individual (part, path) visits,
        which grows much faster in a tree with shared/diamond-shaped
        dependencies:

        1. `_discover_upstream_visits` walks the BOM tree upward, level by
           level, batching the "who uses me" lookup for an entire tier of
           parts into a single query instead of one query per part.
        2. The resulting distinct parts are used to bulk-prefetch sales order
           lines and build line allocations (again, one query each instead of
           one per part), before generating entries for every visit.
        """
        visits = self._discover_upstream_visits(part, include_upstream)

        distinct_parts = {}
        for visited_part, _multiplier, _chain in visits:
            distinct_parts.setdefault(visited_part.pk, visited_part)

        self._prefetch_sales_order_lines(distinct_parts.values(), include_variants)
        self._prefetch_build_lines(distinct_parts.values(), include_variants)

        entries = []

        for current_part, multiplier, chain in visits:
            if current_part.pk not in self.assembly_stock:
                # Calculate the available stock for a given assembly
                # For higher level entries, account for the "in stock" quantity
                # This includes stock on order, or being built
                in_stock = current_part.get_stock_count(include_variants=False)
                in_stock += current_part.on_order
                in_stock += current_part.quantity_being_built
                self.assembly_stock[current_part.pk] = in_stock

            # Add sales order requirements for this particular part
            entries += self.generate_sales_order_entries(
                current_part,
                include_variants,
                multiplier=multiplier,
                chain=chain,
            )

            # Add build order requirements for this particular part
            entries += self.generate_build_order_allocations(
                current_part,
                include_variants,
                multiplier=multiplier,
                chain=chain,
            )

        return entries

    def _discover_upstream_visits(
        self, part: part_models.Part, include_upstream: bool
    ) -> list:
        """Walk the BOM tree upward from `part`, discovering every (part,
        multiplier, chain) visit - a part reached via N independent BOM paths
        is visited N times, each with its own multiplier/chain, exactly as the
        single-node-at-a-time traversal this replaces did.

        The difference is *how* parents are discovered: instead of querying
        "who uses this part" one part at a time, parts are processed in BFS
        tiers, and each tier's "who uses any of these parts" lookup is done in
        a single batched query - so query count scales with the BOM tree's
        depth (number of tiers), not with the number of parts or visits.
        """
        visits = []

        # Each frontier entry is (part, multiplier, chain-not-yet-including-part)
        frontier = [(part, 1.0, [])]

        while frontier:
            extended_frontier = []
            for current_part, multiplier, chain in frontier:
                new_chain = [*chain, (current_part, multiplier)]
                visits.append((current_part, multiplier, new_chain))
                extended_frontier.append((current_part, new_chain))

            if not include_upstream:
                # Only the starting part itself is processed - no need to even
                # look up its parents, since they'd never be visited anyway.
                break

            items_by_sub_part = self._used_in_bom_items_for_parts([
                current_part for current_part, _chain in extended_frontier
            ])

            next_frontier = []

            for current_part, new_chain in extended_frontier:
                multiplier = new_chain[-1][1]

                for item in items_by_sub_part.get(current_part.pk, []):
                    bom_quantity = float(item.quantity) * float(multiplier)

                    # If the BOM Item is inherited by variants
                    if item.inherited:
                        parent_parts = list(
                            item.part.get_descendants(include_self=True).filter(
                                active=True
                            )
                        )
                    else:
                        parent_parts = [item.part]

                    for parent_part in parent_parts:
                        # Skip inactive parts
                        if not parent_part.active:
                            continue

                        next_frontier.append((parent_part, bom_quantity, new_chain))

            frontier = next_frontier

        return visits

    def _used_in_bom_items_for_parts(self, parts: list) -> dict:
        """Batch version of `Part.get_used_in_bom_item_filter()` for a whole
        tier of parts at once, in a single query.

        Returns {sub_part_pk: [BomItem, ...]}, matching (per sub-part) the same
        set of BomItem rows that calling `Part.get_used_in_bom_item_filter()`
        individually for each part in `parts` would have returned.
        """
        parts = list(parts)
        part_pks = {p.pk for p in parts}

        # For the "allow_variants" case: map each ancestor part to the parts in
        # this batch that are its descendants (i.e. variants further down the
        # same tree), so a matching BomItem can be attributed back to them.
        descendants_by_ancestor = defaultdict(list)
        for p in parts:
            try:
                ancestors = p.get_ancestors(include_self=False)
            except ValueError:
                # Part is not yet saved - no ancestors possible
                continue
            for ancestor in ancestors:
                descendants_by_ancestor[ancestor.pk].append(p)

        query = Q(sub_part__in=part_pks)
        if descendants_by_ancestor:
            query |= Q(
                allow_variants=True, sub_part_id__in=list(descendants_by_ancestor)
            )

        bom_items = (
            part_models.BomItem.objects.filter(query)
            .filter(part__active=True)
            .select_related("part", "sub_part")
        )

        result = defaultdict(list)
        for item in bom_items:
            if item.sub_part_id in part_pks:
                result[item.sub_part_id].append(item)

            if item.allow_variants and item.sub_part_id in descendants_by_ancestor:
                for descendant_part in descendants_by_ancestor[item.sub_part_id]:
                    result[descendant_part.pk].append(item)

        return result

    def _prefetch_sales_order_lines(self, parts, include_variants: bool):
        """Bulk-populate `self._sales_order_line_cache` for every part in `parts`
        which isn't already cached, using a single query for the whole batch
        instead of one query per part.
        """
        pending = [p for p in parts if p.pk not in self._sales_order_line_cache]

        if not pending:
            return

        search_pks_by_part, all_search_pks = self._resolve_variant_search_sets(
            pending, include_variants
        )

        so_lines = list(
            order_models.SalesOrderLineItem.objects.filter(
                order__status__in=order_status.SalesOrderStatusGroups.OPEN,
                part_id__in=all_search_pks,
            ).select_related("order", "part")
        )

        for p in pending:
            search_pks = search_pks_by_part[p.pk]
            self._sales_order_line_cache[p.pk] = [
                line for line in so_lines if line.part_id in search_pks
            ]

    def _prefetch_build_lines(self, parts, include_variants: bool):
        """Bulk-populate `self._build_line_cache` for every part in `parts`
        which isn't already cached, using a single query for the whole batch
        instead of one query per part.
        """
        pending = [p for p in parts if p.pk not in self._build_line_cache]

        if not pending:
            return

        search_pks_by_part, all_search_pks = self._resolve_variant_search_sets(
            pending, include_variants
        )

        lines = list(
            build_models.BuildLine.objects.filter(
                bom_item__sub_part_id__in=all_search_pks,
                build__status__in=build_status.BuildStatusGroups.ACTIVE_CODES,
                consumed__lt=F("quantity"),
            ).select_related("build", "bom_item", "bom_item__part")
        )

        for p in pending:
            search_pks = search_pks_by_part[p.pk]
            self._build_line_cache[p.pk] = [
                line for line in lines if line.bom_item.sub_part_id in search_pks
            ]

    def _resolve_variant_search_sets(self, parts: list, include_variants: bool):
        """For each part, resolve the set of part pks that should be searched on
        its behalf (itself, plus descendants if `include_variants`), and the
        union of all those sets across the whole batch.
        """
        search_pks_by_part = {}
        all_search_pks = set()

        for p in parts:
            if include_variants:
                search_pks = set(
                    p.get_descendants(include_self=True).values_list("pk", flat=True)
                )
            else:
                search_pks = {p.pk}

            search_pks_by_part[p.pk] = search_pks
            all_search_pks.update(search_pks)

        return search_pks_by_part, all_search_pks
