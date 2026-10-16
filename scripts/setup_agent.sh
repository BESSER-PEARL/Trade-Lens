#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# One-time setup of the agent (app/agents/lux_insights): its own venv, .venv-agent, with BAF + OpenAI.
# BAF pins versions the app does not use (websockets 11, numpy 1.26, ...), so it does not share .venv.
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
[ -d .venv-agent ] || "$PYTHON" -m venv .venv-agent
PY=.venv-agent/bin/python; [ -x "$PY" ] || PY=.venv-agent/Scripts/python
"$PY" -m pip install --upgrade pip
"$PY" -m pip install -r requirements-agent.txt
"$PY" -c "import baf.core.agent, baf.library.state.reasoning_state_library, baf.nlp.llm.llm_openai_api, baf.platforms.websocket" \
  2>/dev/null && echo "BAF imports fine."

if [ ! -f .env ] || ! grep -q '^OPENAI_API_KEY=.\+' .env; then
  [ -f .env ] || cp .env.example .env
  echo "Put your OpenAI key in .env (OPENAI_API_KEY=...); it is gitignored."
fi
echo "Setup done. Run: python run.py   (the agent starts on ws://localhost:8766 when a key is set)"
