# SPDX-License-Identifier: MIT
"""Run the Luxembourg Trade Explorer locally: backend, frontend and the agent.

    python run.py              # backend :8000, frontend :3000, and the agent (see --agent)
    python run.py --agent real # insist on the agent (BAF + OpenAI, .venv-agent) on ws://localhost:8766
    python run.py --no-agent   # backend and frontend only (same as --agent none)
    python run.py --backend    # API only (docs at http://localhost:8000/docs)
    python run.py --port 8400 --frontend-port 3400   # when :8000 or :3000 is already in use

Only the agent's port moves to the next free one by itself. If :8000 or :3000 is taken, run.py stops
before starting anything and names the flag that picks another port.

--agent defaults to real when .venv-agent exists and a model provider is set in the environment or
in the gitignored .env (OPENAI_API_KEY, or LUX_LLM=mistral-local / mistral for Mistral), else none: the page then starts without the agent and
run.py says how to set it up (scripts/setup_agent.sh / .ps1, the key in .env).

The agent (app/agents/lux_insights) answers every question live in the page's "Ask the data" panel and
draws on the page through POST /view/ops. Needs Node.js 18+ for the frontend; the database (data/lux_trade.db) is loaded from Eurostat on the first launch.
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app"
DB = ROOT / "data" / "lux_trade.db"
AGENT_PY = ROOT / ".venv-agent" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
# The backend runs with the project's .venv when there is one (it has
# statsmodels and scikit-learn for the forecast), whatever Python started run.py.
VENV_PY = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
APP_PY = str(VENV_PY) if VENV_PY.exists() else sys.executable


def port_free(port: int) -> bool:
    """Nothing listens on localhost:port yet."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def free_port(port: int, tries: int = 20) -> int:
    """port, or the next free one after it (another program may already hold the default)."""
    for p in range(port, port + tries):
        if port_free(p):
            return p
    return port


SETUP_HINT = ("the agent needs .venv-agent (scripts/setup_agent.sh, or scripts\\setup_agent.ps1 on Windows) and an "
              "model provider in the gitignored .env at the repository root (see .env.example): OPENAI_API_KEY=sk-..., "
              "or LUX_LLM=mistral-local (Mistral with Ollama, on this machine) or LUX_LLM=mistral (Mistral's API).")


def real_agent_ready() -> bool:
    """The agent can run: .venv-agent exists and an OpenAI key is set (environment or .env)."""
    if not AGENT_PY.exists():
        return False
    placeholders = {"", "YOUR-API-KEY", "sk-..."}
    # An OpenAI key, or another provider (LUX_LLM=mistral-local / mistral, or LUX_LLM_BASE_URL): see .env.example.
    wanted = ("OPENAI_API_KEY", "LUX_LLM", "LUX_LLM_BASE_URL")
    if any(os.environ.get(n, "").strip() not in placeholders for n in wanted):
        return True
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
            name, _, value = line.partition("=")
            if name.strip().removeprefix("export ").strip() in wanted and value.strip().strip("\"'") not in placeholders:
                return True
    except OSError:
        pass
    return False


def wait_for(url: str, proc: subprocess.Popen, timeout: float = 90.0) -> bool:
    """True once url answers 200 (the backend is up), False on timeout or if proc died."""
    end = time.monotonic() + timeout
    while time.monotonic() < end and proc.poll() is None:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(1)
    return False


def load_database() -> None:
    """The database is not in the repository: on the first launch, load it from Eurostat's API (no account or key),
    as scripts/quickstart does, into a temporary file renamed when the load is complete."""
    loading = DB.with_name("lux_trade.loading.db")
    print("\n  No database yet (data/lux_trade.db): loading it from Eurostat's API, once. This takes a few minutes,"
          "\n  up to about 20 when Eurostat is slow. The next launches start at once.\n", flush=True)
    loading.unlink(missing_ok=True)
    env = {**os.environ}
    env.pop("PYTHONPATH", None)
    cmd = [APP_PY, "-m", "besser_sdmx", "load", "--mapping", "model/lux_trade_mapping.json",
           "--orm", "app/backend/sql_alchemy.py", "--slices", "model/lux_trade_slices.json", "--db", str(loading)]
    if subprocess.run(cmd, cwd=ROOT, env=env).returncode != 0 or not loading.exists():
        sys.exit("The data could not be loaded from Eurostat (network?). Run the app again to retry.")
    loading.replace(DB)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--backend", action="store_true", help="start only the API")
    p.add_argument("--no-agent", action="store_true", help="do not start any agent (same as --agent none)")
    p.add_argument("--agent", choices=("real", "none"), default=None,
                   help="real: the BAF agent (.venv-agent, a model provider in .env); none: no agent; "
                        "default: real when it can run, else none")
    p.add_argument("--port", type=int, default=8000, help="backend port (default 8000)")
    p.add_argument("--frontend-port", type=int, default=3000, help="frontend port (default 3000)")
    p.add_argument("--agent-port", type=int, default=None,
                   help="the agent's WebSocket port (default 8766)")
    args = p.parse_args()
    agent = "none" if args.no_agent else (args.agent or ("real" if real_agent_ready() else "none"))
    if agent == "real" and not real_agent_ready():
        sys.exit("No agent: " + SETUP_HINT)
    if agent == "none" and args.agent is None and not args.no_agent and not args.backend:
        print("\n  No agent: " + SETUP_HINT + "\n  Starting the page without it.\n", flush=True)
    args.no_agent = agent == "none"
    if args.agent_port is None:
        default = 8766
        args.agent_port = free_port(default)
        if args.agent_port != default and not args.no_agent:
            print(f"  Port {default} is taken by another program: the agent uses {args.agent_port}.")

    if not (APP / "backend" / "main_api.py").exists():
        sys.exit("No app found in app/ (it is committed: check out the repository again).")
    if not DB.exists():
        load_database()
    # A busy backend or frontend port would otherwise end in a vague error, or in a page served by
    # another copy of the app: stop here and say which flag picks another port.
    busy = [(name, port, flag) for name, port, flag in
            [("backend", args.port, "--port"), ("frontend", args.frontend_port, "--frontend-port")]
            if not (name == "frontend" and args.backend) and not port_free(port)]
    if busy:
        sys.exit("\n".join(f"Port {port} ({name}) is already in use, maybe by another copy of the app. "
                           f"Stop it, or pick another port, e.g. {flag} {free_port(port + 1)}."
                           for name, port, flag in busy))

    env = {**os.environ, "DATABASE_URL": f"sqlite:///{DB.as_posix()}"}
    env.pop("PYTHONPATH", None)  # never pick up another BESSER checkout
    env.setdefault("VIEW_STATE_PATH", str(ROOT / "data" / "view_state.json"))
    # Eight agent charts on the page before a new one takes the place of the oldest one not kept.
    env.setdefault("VIEW_MAX_AGENT_PANELS", "8")
    api = f"http://localhost:{args.port}"
    procs = [subprocess.Popen(
        [APP_PY, "-m", "uvicorn", "main_api:app", "--app-dir", str(APP / "backend"), "--port", str(args.port),
         # An open SSE stream (/view/events) would otherwise hold a shutdown for minutes.
         "--timeout-graceful-shutdown", "3"],
        cwd=ROOT, env=env,
    )]

    try:
        if not args.backend:
            npm = shutil.which("npm")
            if not npm:
                sys.exit("npm not found: install Node.js 18+ or use --backend")
            frontend = APP / "frontend"
            if not (frontend / "node_modules").exists():
                subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=frontend, check=True)
            fenv = {**os.environ, "VITE_API_URL": api, "VITE_AGENT_URL": f"ws://localhost:{args.agent_port}"}
            procs.append(subprocess.Popen([npm, "run", "dev", "--", "--port", str(args.frontend_port), "--strictPort"],
                                          cwd=frontend, env=fenv))

        if not args.backend and not args.no_agent:
            # The agent registers its specialists at start-up, so wait for the API first.
            if wait_for(f"{api}/insights/meta", procs[0]):
                agent_py = str(APP / "agents" / "lux_insights" / "lux_insights_agent.py")
                cmd = [str(AGENT_PY), agent_py, "--api", api, "--port", str(args.agent_port)]
                procs.append(subprocess.Popen(cmd, cwd=ROOT, env=env))
            else:
                print(f"\n  The API at {api} did not come up, so the agent was not started.\n")

        print(f"\n  Overview:   http://localhost:{args.frontend_port}/\n"
              f"  Trends:     http://localhost:{args.frontend_port}/trends\n"
              f"  API docs:   {api}/docs\n"
              f"  View spec:  {api}/view\n"
              + ("" if args.backend or args.no_agent
                 else f"  Agent:      ws://localhost:{args.agent_port}\n"),
              flush=True)

        while all(proc.poll() is None for proc in procs):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for proc in procs:
            proc.terminate()


if __name__ == "__main__":
    main()
