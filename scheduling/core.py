"""Stock scheduling plugin for InvenTree."""

from plugin import InvenTreePlugin
from plugin.mixins import SettingsMixin, UrlsMixin, UserInterfaceMixin

from .version import PLUGIN_VERSION


class StockSchedulingPlugin(SettingsMixin, UrlsMixin, UserInterfaceMixin, InvenTreePlugin):
    """Stock scheduling plugin for InvenTree."""

    AUTHOR = "Oliver Walters"
    DESCRIPTION = "Stock scheduling plugin for InvenTree"
    VERSION = PLUGIN_VERSION

    MIN_VERSION = '0.17.0'

    NAME = "Stock Scheduling"
    SLUG = "scheduling"
    TITLE = "Stock Scheduling Plugin"

    SETTINGS = {}

    def setup_urls(self):
        """Returns the URLs defined by this plugin."""

        # TODO: Define the view class for the scheduling view
        return []

    def get_ui_panels(self, request, context=None, **kwargs):
        """Return the UI panels for the scheduling plugin."""
        
        # TODO: Define the UI panels for the scheduling plugin
        return []
