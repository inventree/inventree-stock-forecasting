"""Core forecasting calculation logic for the InvenTree Forecasting plugin."""

from datetime import date
from decimal import Decimal
from math import prod

import build.models as build_models
import build.status_codes as build_status
import order.models as order_models
import order.status_codes as order_status
import part.models as part_models
import part.serializers as part_serializers
from django.db.models import F, Model
from django.utils.translation import gettext_lazy as _


class PartForecast:
    """Calculates forecasted stock levels for a given part, based on open orders."""

    def __init__(self):
        # Available stock for each intermediate assembly, used to offset demand.
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
        """

        for idx, entry in enumerate(entries):
            quantity = entry.get("quantity", 0)

            if quantity >= 0:
                # Positive quantity values can be ignored for post-processing
                continue

            chain = entry.get("chain")

            if not chain or len(chain) <= 1:
                continue

            # Work out the total chain multiplier for this entry
            chain_multiplier = prod([q for p, q in chain])

            if chain_multiplier <= 0:
                # Defensive check - this should not happen, but we want to avoid any potential issues with zero or negative multipliers
                continue

            # Start with the raw quantity required for the entry
            quantity = Decimal(quantity) / Decimal(chain_multiplier)

            # For a "chained" entry we iterate backwards down the chain, and offset the quantity by the available stock at each level
            for part, qty in reversed(chain):
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
                else:
                    quantity *= Decimal(qty)

            # Update the entry with the post-processed quantity
            entry["quantity"] = quantity

        # Return ONLY entries with a non-zero quantity
        return [entry for entry in entries if entry.get("quantity", 0) != 0]

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

        return {
            "date": date,
            "quantity": float(quantity) * multiplier,
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
        """

        entries = []

        # Start with the bottom level part, and work upwards through the assembly tree
        parts_to_process = [(part, 0, 1.0, [])]

        while parts_to_process:
            current_part, level, multiplier, chain = parts_to_process.pop()

            # No further processing if we are not including upstream assemblies
            if level > 0 and not include_upstream:
                continue

            chain = [*chain, (current_part, multiplier)]

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

            # Find any assembly parts which use this one. This doesn't depend on
            # multiplier/chain, only on current_part, so it's cached per-part -
            # a part reached via multiple BOM paths (e.g. a shared component
            # under a diamond dependency) would otherwise re-run this query,
            # and the inherited-BOM variant expansion below, once per visit.
            if current_part.pk not in self._used_in_bom_items_cache:
                bom_items = (
                    part_models.BomItem.objects.filter(
                        current_part.get_used_in_bom_item_filter(
                            include_variants=True, include_substitutes=False
                        )
                    )
                    .filter(part__active=True)
                    .select_related("part")
                )

                parent_part_quantities = []

                for item in bom_items:
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

                        parent_part_quantities.append((
                            parent_part,
                            float(item.quantity),
                        ))

                self._used_in_bom_items_cache[current_part.pk] = parent_part_quantities

            for parent_part, item_quantity in self._used_in_bom_items_cache[
                current_part.pk
            ]:
                parts_to_process.append((
                    parent_part,
                    level + 1,
                    item_quantity * float(multiplier),
                    chain,
                ))

        return entries
