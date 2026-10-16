"""The page's method catalogue (app/frontend/src/view/methods.ts) names every endpoint a panel or a mark
may query, so every "How this was made" card can say in plain words which fixed method computed its
figures. A new endpoint in ENDPOINT_PARAMS needs its entry there."""
import re
from pathlib import Path

from app.backend.view.validate import ENDPOINT_PARAMS

METHODS_TS = Path(__file__).resolve().parents[1] / "app" / "frontend" / "src" / "view" / "methods.ts"


def _catalogue_keys() -> set[str]:
    text = METHODS_TS.read_text(encoding="utf-8")
    body = text[text.index("export const CATALOGUE"):]
    return set(re.findall(r'^\s{2}"(/[a-z/_-]+)":', body, flags=re.M))


def test_every_endpoint_has_a_method_entry():
    missing = sorted(set(ENDPOINT_PARAMS) - _catalogue_keys())
    assert not missing, f"methods.ts has no entry for {missing}"


def test_no_entry_for_an_unknown_endpoint():
    extra = sorted(_catalogue_keys() - set(ENDPOINT_PARAMS))
    assert not extra, f"methods.ts describes endpoints the validator does not know: {extra}"


def test_plain_words_avoid_developer_vocabulary():
    # The plain sentences are for every reader: no "API", no "GET", no endpoint paths.
    text = METHODS_TS.read_text(encoding="utf-8")
    body = text[text.index("const RANKING"):text.index("export function methodOf")]
    plains = [chunk.split("detail:")[0] for chunk in body.split("plain:")[1:]]
    assert plains, "no plain sentences found"
    for p in plains:
        assert not re.search(r"\bAPI\b|\bGET\b|/insights/|/trends/", p), p
