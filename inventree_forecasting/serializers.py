"""API serializers for the InvenTree Forecasting plugin."""

from django.utils.translation import gettext_lazy as _

from rest_framework import serializers

from part.models import Part


class PartForecastingRequestSerializer(serializers.Serializer):
    """Serializer for requesting forecasting data for a part."""

    class Meta:
        fields = [
            'part'
        ]

    part = serializers.PrimaryKeyRelatedField(
        queryset=Part.objects.all(),
        many=False,
        required=True,
        label=_('Part'),
        help_text=_('The part for which to retrieve forecasting data')
    )

    export = serializers.ChoiceField(
        choices=[(choice, choice) for choice in ['csv', 'tsv', 'xls', 'xlsx']],
        required=False,
        label=_('Export Format')
    )

class PartForecastingEntrySerializer(serializers.Serializer):
    """Serializer for a single entry in part forecasting data."""

    class Meta:
        fields = [
            'date',
            'quantity',
            'title',
            'label',
            'model_type',
            'model_id',
        ]

    date = serializers.DateField(
        label=_('Date'),
        help_text=_('The date for the forecast entry'),
        allow_null=True,
    )

    quantity = serializers.IntegerField(
        label=_('Quantity'),
        help_text=_('The forecasted quantity for this date'),
    )

    title = serializers.CharField(
        label=_('Title'),
        help_text=_('Description for the forecast entry'),
        allow_blank=True,
    )

    label = serializers.CharField(
        label=_('Label'),
        help_text=_('Label for the forecast entry'),
    )

    model_type = serializers.CharField(
        label=_('Model Type'),
        help_text=_('Type of model for the forecast entry'),
    )

    model_id = serializers.IntegerField(
        label=_('Model Type ID'),
        help_text=_('ID of the model type for the forecast entry'),
    )


class PartForecastingSerializer(serializers.Serializer):
    """Serializer for returning forecasting data for a part."""

    class Meta:
        fields = [
            'part',
            'in_stock',
            'min_stock',
            'max_stock',
            'entries',
            'export',
        ]

    part = serializers.PrimaryKeyRelatedField(
        label=_('Part'),
        queryset=Part.objects.all(),
        many=False,
    )

    in_stock = serializers.FloatField(
        label=_('In Stock'),
    )

    min_stock = serializers.FloatField(
        label=_('Minimum Stock'),
        help_text=_('Minimum stock level for the part'),
    )

    max_stock = serializers.FloatField(
        label=_('Maximum Stock'),
        help_text=_('Maximum stock level for the part'),
    )

    entries = PartForecastingEntrySerializer(
        many=True,
        label=_('Forecast Entries'),
    )
