#!/usr/bin/env bash
# Run the inventree_forecasting test suite against a local InvenTree dev instance.
#
# Assumes it's being run inside the standard InvenTree devcontainer, where a
# working InvenTree checkout + venv + database are already available. Installs
# this plugin editable into that venv, activates it as a mandatory plugin, and
# runs the test suite via InvenTree's own manage.py.
#
# Usage:
#   ./run_tests.sh                                                    # full suite
#   ./run_tests.sh inventree_forecasting.tests.test_forecasting.PurchaseOrderEntryTests
#   ./run_tests.sh inventree_forecasting.tests -v 2                   # extra manage.py test args
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${INVENTREE_VENV:-/home/inventree/dev/venv}"
BACKEND_DIR="${INVENTREE_BACKEND_DIR:-/home/inventree/src/backend}"

TEST_TARGET="${1:-inventree_forecasting.tests}"
if [ "$#" -gt 0 ]; then
    shift
fi

"$VENV/bin/pip" install -e "$PLUGIN_DIR" --quiet

cd "$BACKEND_DIR"

INVENTREE_PLUGINS_ENABLED=True \
INVENTREE_PLUGINS_MANDATORY=stock-forecasting \
INVENTREE_PLUGIN_TESTING=True \
INVENTREE_PLUGIN_TESTING_SETUP=True \
"$VENV/bin/python" InvenTree/manage.py test "$TEST_TARGET" --keepdb "$@"
