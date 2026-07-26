"""API views for the InvenTree Forecasting plugin."""

from typing import cast

import part.models as part_models
import tablib
from django.utils.translation import gettext_lazy as _
from InvenTree.helpers import DownloadFile
from InvenTree.mixins import RetrieveAPI
from rest_framework import permissions
from rest_framework.response import Response

from .forecast import PartForecast
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
                    _("Original Quantity"),
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
                        # Differs from 'Quantity' only when this entry's demand
                        # was wholly or partially covered by intermediate
                        # assembly stock further up the BOM chain.
                        entry.get("original_quantity", entry.get("quantity", 0)),
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

        # Do we account for stock availability of intermediate assemblies when calculating the forecast?
        consider_intermediate_stock = bool(
            data.get("consider_intermediate_stock", True)
        )

        # Generate all forecasting entries for this part
        forecast = PartForecast()

        entries = forecast.get_entries(
            part, include_variants=include_variants, include_upstream=include_upstream
        )

        if consider_intermediate_stock:
            entries = forecast.post_process_entries(entries)

        forecasting_data = {
            "part": part.pk,
            "in_stock": part.get_stock_count(include_variants=include_variants),
            "min_stock": getattr(part, "minimum_stock", 0),
            "max_stock": getattr(part, "maximum_stock", 0),
            "entries": entries,
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
