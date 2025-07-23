[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PyPI](https://img.shields.io/pypi/v/inventree-order-history)](https://pypi.org/project/inventree-stock-forecasting/)
![PEP](https://github.com/inventree/inventree-stock-forecasting/actions/workflows/pep.yaml/badge.svg)


# InvenTreeForecasting

This [InvenTree](https://inventree.org) plugin provide stock forecasting based on scheduled orders.

## Description

This plugin uses open orders (sales orders, purchase orders, and build orders) to predict future stock levels and generate forecasts for parts in the system.

## Installation

### InvenTree Plugin Manager

The simplest way to install this plugin is from the InvenTree plugin interface. Enter the plugin name (`inventree-stock-forecasting`) and click the `Install` button:

![Install Plugin](docs/install.png)

### Command Line 

To install manually via the command line, run the following command:

```bash
pip install inventree-stock-forecasting
```

*Note: After the plugin is installed, it must be activated via the InvenTree plugin interface.*

## Setup

This plugin requires the following plugin integrations to be enabled, otherwise it will not function correctly:

- Interface integration
- URL intergration

![Setup Integrations](docs/integrations.png)

## Configuration

The plugin options can be configured via the InvenTree plugin interface. The following settings are available:

| Setting | Description |
| ------- | ----------- |
| Allowed Group | Specify a group which is allowed to view stock forecasting information. Leave blank to allow all users to view stock forecasting information. |

![Plugin Settings](docs/settings.png)

## Usage

With the plugin installed, navigate to a particular part to view the stock forecasting information. The plugin will display a forecast of future stock levels based on the current open orders:

![Forecasting](docs/forecasting.png)

## Caveats

### No Scheduled Orders

If there are no scheduled orders (sales orders, purchase orders, or build orders) for a part, the plugin will not be able to generate a forecast. In this case, the stock forecasting information will not be displayed.

### Insufficient Information

In some cases there may be insufficient information to generate a full stock forecast. This may be because the open orders to not have associated target dates, or becaused the scheduled order dates are in the past. In this case, the plugin will display a warning message indicating that the forecast chart cannot be generated:

![Insufficient Information](docs/insufficient.png)
