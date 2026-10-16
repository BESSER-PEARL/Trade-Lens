#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# One-time setup: Python venv, BESSER v8.1.0 + our patch (vendor/), project dependencies.
set -euo pipefail
cd "$(dirname "$0")/.."
unset PYTHONPATH  # never pick up another BESSER checkout

# The first Python 3.11+ that actually runs ($PYTHON, python3, python, py). On Windows, "python3" is
# often the Microsoft Store stub: it is on PATH but does not run, so test each one, not just find it.
pick_python() {
  for c in ${PYTHON:-} python3 python py; do
    if "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then echo "$c"; return; fi
  done
  echo "No Python 3.11+ found (tried: ${PYTHON:-} python3 python py). Install one, or set PYTHON=/path/to/python." >&2
  exit 1
}
PYTHON="$(pick_python)"

[ -d .venv ] || "$PYTHON" -m venv .venv
rm -f .venv/.setup-done  # written last: quickstart re-runs a setup that did not finish
PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python

if [ ! -d vendor/BESSER ]; then
  git clone --depth 1 --branch v8.1.0 https://github.com/BESSER-PEARL/BESSER.git vendor/BESSER
fi
# Always start from clean v8.1.0 and apply the current patch (re-running setup picks up patch updates).
git -C vendor/BESSER checkout -q -- .
git -C vendor/BESSER clean -qfd
for p in patches/besser/*.patch; do git -C vendor/BESSER apply "../../$p"; done
"$PY" -m pip install --upgrade pip
"$PY" -m pip install -e vendor/BESSER
"$PY" -m pip install -r requirements.txt
touch .venv/.setup-done
echo "Setup done. Activate with: source .venv/bin/activate  (Windows: .venv\Scripts\activate)"
