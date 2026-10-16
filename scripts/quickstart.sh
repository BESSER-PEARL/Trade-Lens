#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# From a fresh clone to the running app in one command: setup (once), data load (once), run.
#   ./scripts/quickstart.sh            # skips the steps already done
#   ./scripts/quickstart.sh --reload   # re-download the data from Eurostat
#   ./scripts/quickstart.sh --no-agent # any other flags go to run.py, e.g. --port 8400 --frontend-port 3400
set -euo pipefail
cd "$(dirname "$0")/.."
unset PYTHONPATH  # never pick up another BESSER checkout

PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python
if [ ! -f .venv/.setup-done ] || [ ! -d vendor/BESSER ]; then
  echo "== Setup (first run only: .venv, BESSER v8.1.0 + patches; 2-3 minutes)"
  bash scripts/setup.sh
  PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python
fi
RELOAD=0; RUN_ARGS=()
for a in "$@"; do if [ "$a" = "--reload" ]; then RELOAD=1; else RUN_ARGS+=("$a"); fi; done
if [ ! -f data/lux_trade.db ] || [ "$RELOAD" = 1 ]; then
  echo "== Loading the data live from the Eurostat API (1.15 million observations; up to 3 minutes)"
  # Into a temporary file first, so that an interrupted load is not taken for a finished one.
  rm -f data/lux_trade.loading.db
  "$PY" -m besser_sdmx load --mapping model/lux_trade_mapping.json --orm app/backend/sql_alchemy.py \
      --slices model/lux_trade_slices.json --db data/lux_trade.loading.db
  mv -f data/lux_trade.loading.db data/lux_trade.db
fi
echo "== Starting the backend, the frontend and the agent (Ctrl+C to stop)"
exec "$PY" run.py ${RUN_ARGS[@]+"${RUN_ARGS[@]}"}
