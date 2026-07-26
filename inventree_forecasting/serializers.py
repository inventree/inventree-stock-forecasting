"""API serializers for the InvenTree Forecasting plugin."""

from django.utils.translation import gettext_lazy as _
from part.models import Part
from rest_framework import serializers


class PartForecastingRequestSerializer(serializers.Serializer):
    """Serializer for requesting forecasting data for a part."""

    class Meta:
        fields = ["part"]

    part = serializers.PrimaryKeyRelatedField(
        queryset=Part.objects.all(),
        many=False,
        required=True,
        label=_("Part"),
        help_text=_("The part for which to retrieve forecasting data"),
    )

    include_variants = serializers.BooleanField(required=False, default=False)

    include_upstream = serializers.BooleanField(required=False, default=False)

    consider_intermediate_stock = serializers.BooleanField(
        required=False, default=True, label=_("Consider Intermediate Stock")
    )

    export = serializers.ChoiceField(
        choices=[(choice, choice) for choice in ["csv", "tsv", "xls", "xlsx"]],
        required=False,
        label=_("Export Format"),
    )


class PartForecastingEntrySerializer(serializers.Serializer):
    """Serializer for a single entry in part forecasting data."""

    class Meta:
        fields = [
            "date",
            "quantity",
            "original_quantity",
            "title",
            "label",
            "part",
            "model_type",
            "model_id",
        ]

    date = serializers.DateField(
        label=_("Date"),
        help_text=_("The date for the forecast entry"),
        allow_null=True,
    )

    quantity = serializers.FloatField(
        label=_("Quantity"),
        help_text=_(
            "The forecasted quantity for this date, after offsetting against any available intermediate assembly stock"
        ),
    )

    original_quantity = serializers.FloatField(
        label=_("Original Quantity"),
        help_text=_(
            "The quantity originally required for this entry, before any offset against "
            "intermediate assembly stock. Differs from 'quantity' only when the entry's "
            "demand was wholly or partially covered by stock further up the BOM chain."
        ),
    )

    title = serializers.CharField(
        label=_("Title"),
        help_text=_("Description for the forecast entry"),
        allow_blank=True,
    )

    label = serializers.CharField(
        label=_("Label"),
        help_text=_("Label for the forecast entry"),
    )

    model_type = serializers.CharField(
        label=_("Model Type"),
        help_text=_("Type of model for the forecast entry"),
    )

    model_id = serializers.IntegerField(
        label=_("Model Type ID"),
        help_text=_("ID of the model type for the forecast entry"),
    )

    part = serializers.JSONField(
        label=_("Part"),
        help_text=_("Part associated with the forecast entry"),
        allow_null=True,
        required=False,
    )


class PartForecastingSerializer(serializers.Serializer):
    """Serializer for returning forecasting data for a part."""

    class Meta:
        fields = [
            "part",
            "in_stock",
            "min_stock",
            "max_stock",
            "entries",
            "export",
        ]

    part = serializers.PrimaryKeyRelatedField(
        label=_("Part"),
        queryset=Part.objects.all(),
        many=False,
    )

    in_stock = serializers.FloatField(
        label=_("In Stock"),
    )

    min_stock = serializers.FloatField(
        label=_("Minimum Stock"),
        help_text=_("Minimum stock level for the part"),
    )

    max_stock = serializers.FloatField(
        label=_("Maximum Stock"),
        help_text=_("Maximum stock level for the part"),
    )

    entries = PartForecastingEntrySerializer(
        many=True,
        label=_("Forecast Entries"),
    )
