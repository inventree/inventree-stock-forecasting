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
        ]

    part = serializers.PrimaryKeyRelatedField(
        queryset=Part.objects.all(),
        many=False,
    )

    entries = PartForecastingEntrySerializer(
        many=True,
        label=_('Forecast Entries'),
    )
