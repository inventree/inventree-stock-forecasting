"""API views for the InvenTree Forecasting plugin."""

from typing import cast

from rest_framework import permissions
from rest_framework.response import Response

from InvenTree.mixins import RetrieveAPI

from .serializers import PartForecastingSerializer, PartForecastingRequestSerializer


class PartForecastingView(RetrieveAPI):
    """API view for retrieving part forecasting data."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PartForecastingSerializer

    def get(self, request, *args, **kwargs):
        """Handle GET request to retrieve forecasting data for a specific part."""

        request_serializer = PartForecastingRequestSerializer(data=request.query_params)
        request_serializer.is_valid(raise_exception=True)

        data = cast(dict, request_serializer.validated_data)

        part = data.get('part')

        # Here you would typically fetch the forecasting data for the part
        # For demonstration purposes, we return a mock response
        forecasting_data = {
            'part': part.pk,
            'entries': [],
        }

        response_serializer = self.serializer_class(data=forecasting_data)
        response_serializer.is_valid(raise_exception=True)
        
        return Response(response_serializer.data, status=200)
