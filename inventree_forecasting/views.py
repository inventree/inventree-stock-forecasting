"""API views for the InvenTree Forecasting plugin."""

import functools
from datetime import date
from typing import Optional, cast

from django.db.models import F, Model
from django.utils.translation import gettext_lazy as _

import tablib


from rest_framework import permissions
from rest_framework.response import Response

import build.models as build_models
import build.status_codes as build_status
import order.models as order_models
import order.status_codes as order_status
import part.models as part_models
import part.serializers as part_serializers
from InvenTree.helpers import DownloadFile
from InvenTree.mixins import RetrieveAPI

from .serializers import PartForecastingRequestSerializer, PartForecastingSerializer


class PartForecastingView(RetrieveAPI):
    """API view for retrieving part forecasting data."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PartForecastingSerializer

    def export_data(
        self,
        part: part_models.Part,
        entries: list,
        include_variants: bool = False,
        export_format: str = "csv",
    ):
        """Export the forecasting data to file for download.

        Arguments:
            part (part_models.Part): The part for which the data is being exported.
            entries (list): The list of forecasting entries to export.
            include_variants (bool): Whether to include variant parts in the stock count.
            export_format (str): The format to export the data in (e.g., 'csv', 'tsv', 'xls', 'xlsx').

        """
        # Construct the set of headers
        headers = list(
            map(
                str,
                [
                    _("Date"),
                    _("Label"),
                    _("Title"),
                    _("Model Type"),
                    _("Model ID"),
                    _("Quantity"),
                    _("Stock Level"),
                ],
            )
        )

        dataset = tablib.Dataset(headers=headers)

        # Track quantity over time
        stock = float(part.get_stock_count(include_variants=include_variants))

        for entry in entries:
            stock += entry.get("quantity", 0)
            row = list(
                map(
                    str,
                    [
                        entry.get("date", ""),
                        entry.get("label", ""),
                        entry.get("title", ""),
                        entry.get("model_type", ""),
                        entry.get("model_id", ""),
                        entry.get("quantity", 0),
                        stock,
                    ],
                )
            )
            dataset.append(row)

        data = dataset.export(export_format)

        return DownloadFile(
            data,
            filename=f"InvenTree_Stock_Forecasting_{part.pk}.{export_format}",
        )

    def get(self, request, *args, **kwargs):
        """Handle GET request to retrieve forecasting data for a specific part."""
        request_serializer = PartForecastingRequestSerializer(data=request.query_params)
        request_serializer.is_valid(raise_exception=True)

        data = cast(dict, request_serializer.validated_data)

        part = data.get("part")

        # Do we include forecasting entries for part variants?
        include_variants = bool(data.get("include_variants", False))

        # Do we include forecasting entries for upstream orders?
        include_upstream = bool(data.get("include_upstream", False))

        forecasting_data = {
            "part": part.pk,
            "in_stock": part.get_stock_count(include_variants=include_variants),
            "min_stock": getattr(part, "minimum_stock", 0),
            "max_stock": getattr(part, "maximum_stock", 0),
            "entries": self.get_entries(
                part,
                include_variants=include_variants,
                include_upstream=include_upstream,
            ),
        }

        response_serializer = self.serializer_class(data=forecasting_data)
        response_serializer.is_valid(raise_exception=True)

        if export_format := data.get("export"):
            # If an export format is specified, export the data
            return self.export_data(
                part,
                response_serializer.data["entries"],
                export_format=export_format,
                include_variants=include_variants,
            )

        return Response(response_serializer.data, status=200)

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

        def compare_entries(entry_1: dict, entry_2: dict) -> int:
            """Comparison function for two forecasting entries, to assist in sorting.

            - Sort in increasing order of date
            - Account for the fact that either date may be None
            """
            date_1 = entry_1["date"]
            date_2 = entry_2["date"]

            if date_1 is None:
                return -1
            elif date_2 is None:
                return 1

            return -1 if date_1 < date_2 else 1

        # Sort by date
        entries = sorted(entries, key=functools.cmp_to_key(compare_entries))

        return entries

    def generate_entry(
        self,
        instance: Model,
        quantity: float,
        date: Optional[date] = None,
        part: Optional[part_models.Part] = None,
        title: str = "",
        multiplier: float = 1.0,
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
            part = part_serializers.PartBriefSerializer(part).data

        return {
            "date": date,
            "quantity": float(quantity) * multiplier,
            "label": getattr(instance, "reference", str(instance)),
            "title": str(title),
            "model_type": instance.__class__.__name__.lower(),
            "model_id": instance.pk,
            "part": part,
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
        )

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
                        part=line.part.part if line.part and line.part.part else None,
                        title=_("Incoming Purchase Order"),
                    )
                )

        return entries

    def generate_sales_order_entries(
        self, part: part_models.Part, include_variants: bool, multiplier: float = 1.0, assembly_stock: Optional[dict] = None
    ) -> list:
        """Generate forecasting entries for sales orders related to the part.

        Arguments:
            part (part_models.Part): The part for which to generate entries.
            include_variants (bool): Whether to include variant parts in the stock count.
            multiplier (float): A multiplier to apply to the quantity (e.g., to account for
            assembly_stock: A dictionary mapping part PKs to their current stock level, to allow "offsetting" of sales order requirements based on available stock.
        """
        entries = []

        # Find all open sales order line items
        so_lines = order_models.SalesOrderLineItem.objects.filter(
            order__status__in=order_status.SalesOrderStatusGroups.OPEN
        )

        if include_variants:
            # Filter lines to include any variants of the provided part
            variants = part.get_descendants(include_self=True)
            so_lines = so_lines.filter(part__in=variants)
        else:
            # Filter lines to only include the exact part
            so_lines = so_lines.filter(part=part)

        for line in so_lines:
            target_date = line.target_date or line.order.target_date
            # Negative quantities indicate outgoing sales orders

            # The outstanding quantity which will be required
            outstanding = max(0, line.quantity - line.shipped)

            # If this is a higher level assembly, we can reduce the outstanding requirement, based on the available stock for this assembly
            if assembly_stock:
                available = assembly_stock.get(part.pk, 0)
                adjustment = min(available, outstanding)
                assembly_stock[line.part.pk] = available - adjustment
                outstanding -= adjustment
                outstanding = max(0, outstanding)

            if abs(outstanding) > 0:
                entries.append(
                    self.generate_entry(
                        line.order,
                        -1 * outstanding,
                        target_date,
                        title=_("Outgoing Sales Order"),
                        multiplier=multiplier,
                        part=line.part,
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
        )

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
                    )
                )

        return entries

    def generate_build_order_allocations(
        self, part: part_models.Part, include_variants: bool, multiplier: float = 1.0, assembly_stock: Optional[dict] = None
    ) -> list:
        """Generate forecasting entries for build order allocations related to the part.

        This is essentially the amount of this part required to fulfill open build orders.

        Arguments:
            part (part_models.Part): The part for which to generate entries.
            include_variants (bool): Whether to include variant parts in the stock count.
            multiplier (float): A multiplier to apply to the required quantity (e.g., to account for higher level assemblies)
            assembly_stock (dict): A dictionary mapping part PKs to their current stock level, to allow "offsetting" of build order requirements based on available stock.
            
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
        ).select_related("bom_item", "build", "bom_item__part")
        for line in lines:
            remaining = max(0, line.quantity - line.consumed)

            # If this is a higher level assembly, we can reduce the required quantity, based on the available stock for this assembly
            if assembly_stock:
                available = assembly_stock.get(part.pk, 0)
                adjustment = min(available, remaining)
                assembly_stock[line.part.pk] = available - adjustment
                remaining -= adjustment
                remaining = max(0, remaining)

            if remaining > 0:
                entries.append(
                    self.generate_entry(
                        line.build,
                        -1 * remaining,
                        line.build.start_date or line.build.target_date,
                        title=_("Required for Build Order"),
                        part=line.bom_item.part,
                        multiplier=multiplier,
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

        # Keep track of the stock level for higher level assemblies
        assembly_stock = {}

        # Start with the bottom level part, and work upwards through the assembly tree
        parts_to_process = [(part, 0, 1.0)]

        while parts_to_process:
            current_part, level, multiplier = parts_to_process.pop()


            if current_part.pk not in assembly_stock:
                # Calculate the available stock for a given assembly
                # For higher level entries, account for the "in stock" quantity
                # This includes stock on order, or being built
                in_stock = current_part.get_stock_count(include_variants=False)
                in_stock += current_part.on_order
                in_stock += current_part.quantity_being_built
                assembly_stock[current_part.pk] = in_stock

            # Add sales order requirements for this particular part
            entries += self.generate_sales_order_entries(
                current_part, include_variants, multiplier=multiplier, assembly_stock=assembly_stock if level > 0 else None
            )

            # Add build order requirements for this particular part
            entries += self.generate_build_order_allocations(
                current_part, include_variants, multiplier=multiplier, assembly_stock=assembly_stock if level > 0 else None
            )

            # Find any assembly parts which use this one
            bom_items = part_models.BomItem.objects.filter(
                current_part.get_used_in_bom_item_filter(
                    include_variants=True, include_substitutes=False
                )
            )

            for item in bom_items:
                bom_quantity = float(item.quantity) * float(multiplier)

                # If the BOM Item is inherited by variants
                if item.inherited:
                    parent_parts = list(item.part.get_descendants(include_self=True))
                else:
                    parent_parts = [item.part]

                # Add this assembly to the list of parts to process
                for parent_part in parent_parts:
                    parts_to_process.append((
                        parent_part,
                        level + 1,
                        bom_quantity,
                    ))

            # No further processing if we are not including upstream assemblies
            if not include_upstream:
                break

        return entries
