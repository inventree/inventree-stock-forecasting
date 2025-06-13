"""Provide stock forecasting for InvenTree based on scheduled orders"""

from plugin import InvenTreePlugin

from plugin.mixins import SettingsMixin, UserInterfaceMixin

from . import PLUGIN_VERSION


class InvenTreeForecasting(SettingsMixin, UserInterfaceMixin, InvenTreePlugin):

    """InvenTreeForecasting - custom InvenTree plugin."""

    # Plugin metadata
    TITLE = "InvenTree Forecasting"
    NAME = "InvenTreeForecasting"
    SLUG = "inventree-forecasting"
    DESCRIPTION = "Provide stock forecasting for InvenTree based on scheduled orders"
    VERSION = PLUGIN_VERSION

    # Additional project information
    AUTHOR = "Oliver Walters"
    WEBSITE = "https://github.com/inventree/inventree-forecasting"
    LICENSE = "MIT"

    # Optionally specify supported InvenTree versions
    # MIN_VERSION = '0.18.0'
    # MAX_VERSION = '2.0.0'

    
    
    
    # Plugin settings (from SettingsMixin)
    # Ref: https://docs.inventree.org/en/stable/extend/plugins/settings/
    SETTINGS = {
        # Define your plugin settings here...
        'CUSTOM_VALUE': {
            'name': 'Custom Value',
            'description': 'A custom value',
            'validator': int,
            'default': 42,
        }
    }
    
    
    

    # User interface elements (from UserInterfaceMixin)
    # Ref: https://docs.inventree.org/en/stable/extend/plugins/ui/
    
    # Custom UI panels
    def get_ui_panels(self, request, context: dict, **kwargs):
        """Return a list of custom panels to be rendered in the InvenTree user interface."""

        panels = []

        # Only display this panel for the 'part' target
        if context.get('target_model') == 'part':
            panels.append({
                'key': 'inventree-forecasting-panel',
                'title': 'InvenTree Forecasting',
                'description': 'Custom panel description',
                'icon': 'ti:mood-smile:outline',
                'source': self.plugin_static_file('Panel.js:renderInvenTreeForecastingPanel'),
                'context': {
                    # Provide additional context data to the panel
                    'settings': self.get_settings_dict(),
                    'foo': 'bar'
                }
            })
        
        return panels
    
    
