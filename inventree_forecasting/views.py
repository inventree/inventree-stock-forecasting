"""API views for the InvenTree Forecasting plugin."""

import functools
from datetime import date
from typing import Optional, cast

from django.db.models import Model
from django.utils.translation import gettext_lazy as _

import tablib
from rest_framework import permissions
from rest_framework.response import Response

import build.models as build_models
import build.status_codes as build_status
import order.models as order_models
import order.status_codes as order_status
import part.models as part_models
from InvenTree.helpers import DownloadFile
from InvenTree.mixins import RetrieveAPI

from .serializers import PartForecastingRequestSerializer, PartForecastingSerializer


class PartForecastingView(RetrieveAPI):
    """API view for retrieving part forecasting data."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PartForecastingSerializer

    def export_data(self, part: part_models.Part, entries: list, export_format: str = 'csv'):
        """Export the forecasting data to file for download."""
        # Construct the set of headers
        headers = list(map(str, [
            _('Date'),
            _('Label'),
            _('Title'),
            _('Model Type'),
            _('Model ID'),
            _('Quantity'),
            _('Stock Level')
        ]))

        dataset = tablib.Dataset(headers=headers)

        # Track quantity over time
        stock = float(part.get_stock_count(include_variants=False))

        for entry in entries:
            stock += entry.get('quantity', 0)
            row = list(map(str, [
                entry.get('date', ''),
                entry.get('label', ''),
                entry.get('title', ''),
                entry.get('model_type', ''),
                entry.get('model_id', ''),
                entry.get('quantity', 0),
                stock
            ]))
            dataset.append(row)

        data = dataset.export(export_format)

        return DownloadFile(
            data,
            filename=f'InvenTree_Stock_Forecasting_{part.pk}.{export_format}',
        )

    def get(self, request, *args, **kwargs):
        """Handle GET request to retrieve forecasting data for a specific part."""
        request_serializer = PartForecastingRequestSerializer(data=request.query_params)
        request_serializer.is_valid(raise_exception=True)

        data = cast(dict, request_serializer.validated_data)

        # TODO: Add support for "variants"

        part = data.get('part')

        # Here you would typically fetch the forecasting data for the part
        # For demonstration purposes, we return a mock response
        forecasting_data = {
            'part': part.pk,
            'in_stock': part.get_stock_count(include_variants=False),
            'min_stock': getattr(part, 'minimum_stock', 0),
            'max_stock': getattr(part, 'maximum_stock', 0),
            'entries': self.get_entries(part),
        }

        response_serializer = self.serializer_class(data=forecasting_data)
        response_serializer.is_valid(raise_exception=True)

        if export_format := data.get('export'):
            # If an export format is specified, export the data
            return self.export_data(part, response_serializer.data['entries'], export_format)

        return Response(response_serializer.data, status=200)

    def get_entries(self, part: part_models.Part) -> list:
        """Fetch forecasting entries for the given part."""
        entries = [
            *self.generate_purchase_order_entries(part),
            *self.generate_sales_order_entries(part),
            *self.generate_build_order_entries(part),
            *self.generate_build_order_allocations(part),
        ]

        def compare_entries(entry_1: dict, entry_2: dict) -> int:
            """Comparison function for two forecasting entries, to assist in sorting.
            
            - Sort in increasing order of date
            - Account for the fact that either date may be None
            """
            date_1 = entry_1['date']
            date_2 = entry_2['date']

            if date_1 is None:
                return -1
            elif date_2 is None:
                return 1

            return -1 if date_1 < date_2 else 1

        # Sort by date
        entries = sorted(entries, key=functools.cmp_to_key(compare_entries))

        return entries

    def generate_entry(self,
        instance: Model,
        quantity: float,
        date: Optional[date] = None,
        title: str = ''
    ):
        """Generate a forecasting entry for a part.
        
        Arguments:
            instance (Model): The model instance (e.g., PurchaseOrder) for which the entry is associated
            quantity (float): The forecasted quantity.
            date (date): The date for the forecast entry.
            title (str): Optional title for the entry.
        """
        return {
            'date': date,
            'quantity': float(quantity),
            'label': getattr(instance, 'reference', str(instance)),
            'title': str(title),
            'model_type': instance.__class__.__name__.lower(),
            'model_id': instance.pk,
        }

    def generate_purchase_order_entries(self, part: part_models.Part) -> list:
        """Generate forecasting entries for purchase orders related to the part.
        
        - We look at all pending purchase orders which might supply this part.
        - These orders will increase the forecasted quantity for the part.
        - We do not include purchase orders which are already completed or cancelled.
        """
        entries = []

        po_lines = order_models.PurchaseOrderLineItem.objects.filter(
            part__part=part,
            order__status__in=order_status.PurchaseOrderStatusGroups.OPEN
        )

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
                        title=_("Incoming Purchase Order"),
                    )
                )

        return entries

    def generate_sales_order_entries(self, part: part_models.Part) -> list:
        """Generate forecasting entries for sales orders related to the part."""
        entries = []

        so_lines = order_models.SalesOrderLineItem.objects.filter(
            part=part,
            order__status__in=order_status.SalesOrderStatusGroups.OPEN
        )

        for line in so_lines:
            target_date = line.target_date or line.order.target_date
            # Negative quantities indicate outgoing sales orders
            quantity = -1 * max(0, line.quantity - line.shipped)

            if abs(quantity) > 0:
                entries.append(
                    self.generate_entry(
                        line.order,
                        quantity,
                        target_date,
                        title=_("Outgoing Sales Order"),
                    )
                )

        return entries

    def generate_build_order_entries(self, part: part_models.Part) -> list:
        """Generate forecasting entries for build orders related to the part."""
        entries = []

        build_orders = build_models.Build.objects.filter(
            part=part,
            status__in=build_status.BuildStatusGroups.ACTIVE_CODES
        )

        for build in build_orders:
            quantity = max(build.quantity - build.completed, 0)

            if abs(quantity) > 0:
                entries.append(
                    self.generate_entry(
                        build,
                        quantity,
                        build.target_date,
                        title=_("Assembled via Build Order")
                    )
                )

        return entries

    def generate_build_order_allocations(self, part: part_models.Part) -> list:
        """Generate forecasting entries for build order allocations related to the part.
        
        This is essentially the amount of this part required to fulfill open build orders.

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

        bom_items = part_models.BomItem.objects.filter(part.get_used_in_bom_item_filter())

        # Track all outstanding build orders
        observed_builds = set()

        for bom_item in bom_items:
            if bom_item.inherited:
                # An "inherited" BOM item filters down to variant parts also
                children = bom_item.part.get_descendants(include_self=True)
                builds = build_models.Build.objects.filter(
                    status__in=build_status.BuildStatusGroups.ACTIVE_CODES, part__in=children
                )
            else:
                builds = build_models.Build.objects.filter(
                    status__in=build_status.BuildStatusGroups.ACTIVE_CODES, part=bom_item.part
                )

            for build in builds:
                # Ensure we don't double-count the same build
                if build.pk in observed_builds:
                    continue

                observed_builds.add(build.pk)

                if bom_item.sub_part.trackable:
                    # Trackable parts are allocated against the output
                    required_quantity = build.remaining * bom_item.quantity
                else:
                    # Non-trackable parts are allocated against the build itself
                    required_quantity = build.quantity * bom_item.quantity

                # Grab all allocations against the specified BomItem
                allocations = build_models.BuildItem.objects.filter(
                    build_line__bom_item=bom_item,
                    build_line__build=build,
                )

                # Total allocated for this part
                part_allocated_quantity = 0
                total_allocated_quantity = 0

                for allocation in allocations:
                    total_allocated_quantity += allocation.quantity

                    if allocation.stock_item.part == part:
                        part_allocated_quantity += allocation.quantity

                if part_allocated_quantity > 0:
                    entries.append(
                        self.generate_entry(
                            build,
                            -1 * part_allocated_quantity,
                            build.target_date,
                            title=_("Allocated to Build Order"),
                        )
                    )

                # If the allocated quantity is not sufficient, add a "speculative" quantity for the build order
                if required_quantity > total_allocated_quantity:
                    entries.append(
                        self.generate_entry(
                            build,
                            -1 * (required_quantity - total_allocated_quantity),
                            build.target_date,
                            title=_("Required for Build Order"),
                        )
                    )

        return entries
