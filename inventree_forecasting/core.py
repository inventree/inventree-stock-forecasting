"""Provide stock forecasting for InvenTree based on scheduled orders"""

from django.contrib.auth.models import Group
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
    SETTINGS = {
        "USER_GROUP": {
            "name": _("Allowed Group"),
            "description": _(
                "The user group that is allowed to view stock forecasting"
            ),
            "model": "auth.group",
        }
    }

    # User interface elements (from UserInterfaceMixin)

    # Custom UI panels
    def get_ui_panels(self, request, context: dict, **kwargs):
        """Return a list of custom panels to be rendered in the InvenTree user interface."""

        from part.models import Part

        panels = []

        allowed_user = True

        # Hide for users who are *not* in the correct group
        if user_group_id := self.get_setting("USER_GROUP", backup_value=None):
            user_group = Group.objects.filter(id=user_group_id).first()

            if user_group is not None and user_group not in request.user.groups.all():
                allowed_user = False

        # Only display this panel for the 'part' target
        if allowed_user and context.get("target_model") == "part":

            if part_id := context.get("target_id", None):
                try:
                    part = Part.objects.filter(id=part_id).first()
                except Exception:
                    part = None
                
                # A valid (non-virtual) part is required
                if part and not part.virtual:
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
