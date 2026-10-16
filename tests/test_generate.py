# SPDX-License-Identifier: MIT
"""generate.py still turns the models into an app, but never into the hand-maintained app/.

app/ was generated at commit cea06c4 and is now edited by hand, so the check is only that the models
generate (into a scratch folder), not that the output equals app/.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _run(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ}
    env.pop("PYTHONPATH", None)  # never pick up another BESSER checkout
    return subprocess.run([sys.executable, str(REPO / "generate.py"), *args], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=300)


@pytest.mark.parametrize("out", ["app", "app/backend"])
def test_refuses_to_write_into_app(out):
    r = _run("--out", out)
    assert r.returncode != 0
    assert "Refusing to write into" in r.stderr and "--force" in r.stderr


def test_generates_into_a_scratch_folder(tmp_path):
    pytest.importorskip("besser.generators.web_app")
    out = tmp_path / "generated"
    r = _run("--out", str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    for rel in ("backend/main_api.py", "backend/routers/insights.py", "frontend/package.json",
                "frontend/src/App.tsx"):
        assert (out / rel).is_file(), rel
