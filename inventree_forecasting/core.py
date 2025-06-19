"""Provide stock forecasting for InvenTree based on scheduled orders"""

from django.utils.translation import gettext_lazy as _

from plugin import InvenTreePlugin
from plugin.mixins import SettingsMixin, UrlsMixin, UserInterfaceMixin

from . import PLUGIN_VERSION


class InvenTreeForecasting(
    SettingsMixin, UrlsMixin, UserInterfaceMixin, InvenTreePlugin
):
    """InvenTreeForecasting - custom InvenTree plugin."""

    # Plugin metadata
    TITLE = "InvenTree Forecasting"
    NAME = "InvenTreeForecasting"
    SLUG = "stock-forecasting"
    DESCRIPTION = "Provide stock forecasting based on scheduled orders"
    VERSION = PLUGIN_VERSION

    # Additional project information
    AUTHOR = "Oliver Walters"
    WEBSITE = "https://github.com/inventree/inventree-forecasting"
    LICENSE = "MIT"

    MIN_VERSION = "0.18.0"  # Minimum InvenTree version required for this plugin

    # Plugin settings (from SettingsMixin)
    # Ref: https://docs.inventree.org/en/stable/extend/plugins/settings/
    SETTINGS = {
        # Define your plugin settings here...
        "CUSTOM_VALUE": {
            "name": "Custom Value",
            "description": "A custom value",
            "validator": int,
            "default": 42,
        }
    }

    # User interface elements (from UserInterfaceMixin)
    # Ref: https://docs.inventree.org/en/stable/extend/plugins/ui/

    # Custom UI panels
    def get_ui_panels(self, request, context: dict, **kwargs):
        """Return a list of custom panels to be rendered in the InvenTree user interface."""
        panels = []

        # TODO: Hide for users who are *not* in the correct group

        # Only display this panel for the 'part' target
        if context.get("target_model") == "part":
            panels.append({
                "key": "stock-forecasting",
                "title": _("Stock Forecasting"),
                "description": _("Stock level forecasting"),
                "icon": "ti:calendar-time:outline",
                "source": self.plugin_static_file(
                    "ForecastingPanel.js:renderInvenTreeForecastingPanel"
                ),
                "context": {
                    # Provide additional context data to the panel
                    "settings": self.get_settings_dict(),
                    "foo": "bar",
                },
            })

        return panels

    def setup_urls(self):
        """Returns the URLs defined by this plugin."""
        from django.urls import path

        from .views import PartForecastingView

        return [
            path("forecast/", PartForecastingView.as_view(), name="part-forecasting"),
        ]
