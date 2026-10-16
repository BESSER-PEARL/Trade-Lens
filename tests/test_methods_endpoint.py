# SPDX-License-Identifier: MIT
"""GET /insights/methods: the method catalogue served by the backend (app/backend/routers/insights.py, METHODS).

Every endpoint a panel or a mark may query (ENDPOINT_PARAMS in app/backend/view/validate.py) has an entry, its
inputs name every parameter the validator accepts for it, its plain sentence is free of developer vocabulary, and
its name is the one the page's own catalogue (app/frontend/src/view/methods.ts) gives it.
"""
import os
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "app" / "backend"
DB = REPO / "data" / "lux_trade.db"
METHODS_TS = REPO / "app" / "frontend" / "src" / "view" / "methods.ts"
OWN = ("main_api", "database", "sql_alchemy", "pydantic_classes", "bal_stdlib", "routers", "view")


def _drop_backend_modules() -> None:
    for name in list(sys.modules):
        if name.split(".")[0] in OWN:
            sys.modules.pop(name, None)


@pytest.fixture(scope="module")
def backend():
    saved = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}" if DB.exists() else "sqlite:///:memory:"
    _drop_backend_modules()
    sys.path.insert(0, str(BACKEND))
    try:
        from routers import insights
        from view.validate import ENDPOINT_PARAMS
        yield insights, ENDPOINT_PARAMS
    finally:
        sys.path.remove(str(BACKEND))
        _drop_backend_modules()
        if saved is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved


def test_every_endpoint_of_the_validator_has_a_method(backend):
    insights, endpoint_params = backend
    missing = sorted(set(endpoint_params) - set(insights.METHODS))
    assert not missing, f"/insights/methods has no entry for {missing}"


def test_each_entry_names_every_parameter_the_validator_accepts(backend):
    insights, endpoint_params = backend
    for endpoint, (required, optional) in endpoint_params.items():
        inputs = insights.METHODS[endpoint]["inputs"]
        missing = [p for p in (*required, *optional) if p not in inputs]
        assert not missing, f"{endpoint}: inputs lack {missing}"


def test_each_entry_is_complete_and_plain(backend):
    insights, _ = backend
    for endpoint, m in insights.METHODS.items():
        for key in ("name", "plain", "detail", "returns"):
            assert isinstance(m.get(key), str) and m[key].strip(), f"{endpoint}: no {key}"
        assert m["name"][0].isupper() and not m["name"].isupper(), f"{endpoint}: sentence case name"
        for text in [m["plain"]] + [v["plain"] for v in m.get("variants", [])]:
            assert not re.search(r"\bAPI\b|\bGET\b|/insights/|/trends/|\bPELT\b|\bSTL\b", text), (endpoint, text)


def _ts_names() -> dict[str, str]:
    """The static names of the page's catalogue: {endpoint: name}."""
    text = METHODS_TS.read_text(encoding="utf-8")
    consts = {c: n for c, n in re.findall(r"^const (\w+): Entry = \{\s*name: \"([^\"]+)\"", text, flags=re.M)}
    body = text[text.index("export const CATALOGUE"):]
    out = {}
    for endpoint, rest in re.findall(r'^\s{2}"(/[a-z/_-]+)":\s*(.+)$', body, flags=re.M):
        rest = rest.strip().rstrip(",")
        if rest in consts:
            out[endpoint] = consts[rest]
            continue
        m = re.search(rf'"{re.escape(endpoint)}":[^\n]*\n\s+name: "([^"]+)"', body)
        n = re.search(r'name: "([^"]+)"', rest)
        if n:
            out[endpoint] = n.group(1)
        elif m:
            out[endpoint] = m.group(1)
    return out


@pytest.mark.skipif(not METHODS_TS.exists(), reason="no page catalogue")
def test_names_match_the_page_catalogue(backend):
    insights, _ = backend
    ts = _ts_names()
    assert ts, "could not read the page's catalogue"
    differ = {e: (n, insights.METHODS[e]["name"]) for e, n in ts.items()
              if e in insights.METHODS and insights.METHODS[e]["name"] != n}
    assert not differ, f"names differ from methods.ts (page, backend): {differ}"


@pytest.mark.skipif(not DB.exists(), reason="no local database data/lux_trade.db")
def test_the_endpoint_serves_the_catalogue(backend):
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(BACKEND))
    try:
        import main_api
        with TestClient(main_api.app) as c:
            r = c.get("/insights/methods")
            assert r.status_code == 200
            body = r.json()
            assert body["count"] == len(body["methods"]) >= 19
            movers = next(m for m in body["methods"] if m["endpoint"] == "/insights/movers")
            assert movers["name"] == "Movers" and movers["inputs"]["previous"] and movers["filters"]["flow"]
            one = c.get("/insights/methods", params={"endpoint": "/trends/analyze"}).json()
            assert one["name"] == "Seasonal adjustment and lasting shifts"
            assert c.get("/insights/methods", params={"endpoint": "/nope"}).status_code == 404
    finally:
        sys.path.remove(str(BACKEND))
