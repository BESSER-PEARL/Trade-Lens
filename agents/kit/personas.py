# SPDX-License-Identifier: MIT
"""Who is reading (contract 1.4): the persona profiles of contract/personas/, for agents.

    from agents.kit.personas import persona_of, profile
    p = persona_of(view.spec())           # the page's reader profile, or None (every reader)
    if p and p["answer"]["depth"] == "headline": ...

A profile changes depth, words, panels and questions, never a figure: every number still comes
from /insights, and the page fills it from {fact} placeholders.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

PERSONAS_DIR = Path(__file__).resolve().parents[2] / "contract" / "personas"

# The line every quotable sentence ends with (the profile's answer.quotable_sentence).
SOURCE_LINE = "Source: Eurostat ds-045409, data produced by STATEC."


@lru_cache(maxsize=1)
def profiles() -> dict[str, dict]:
    """The six profiles by id (an empty dict when the file is missing)."""
    path = PERSONAS_DIR / "persona_profiles.json"
    if not path.exists():
        return {}
    return {p["id"]: p for p in json.loads(path.read_text(encoding="utf-8"))["profiles"]}


def profile(persona_id: Optional[str]) -> Optional[dict]:
    """The profile of a persona id; None for no persona, ``everyone`` or an unknown id."""
    return profiles().get(persona_id) if persona_id else None


def persona_of(spec: Optional[dict]) -> Optional[dict]:
    """The profile of the reader the page is drawn for (spec.context.persona), or None."""
    return profile(((spec or {}).get("context") or {}).get("persona"))


def depth(p: Optional[dict]) -> str:
    """headline, brief or full; full when no persona (the page for every reader)."""
    return ((p or {}).get("answer") or {}).get("depth", "full")


def say(p: Optional[dict], term: str, default: Optional[str] = None) -> str:
    """The reader's word for a term (vocabulary: 'exports' -> 'goods sold abroad'), else ``default`` or the term."""
    return ((p or {}).get("vocabulary") or {}).get(term, default if default is not None else term)


def who_is_asking(p: Optional[dict]) -> str:
    """A "## Who is asking" section for a system prompt: the profile's snippet, its limits and its words.
    Empty for every reader (no persona)."""
    if not p:
        return ""
    a, c, ag = p.get("answer") or {}, p.get("charts") or {}, p.get("agents") or {}
    lines = [f"## Who is asking: {p['label']}", p.get("prompt_snippet", "").strip(), ""]
    lines.append(f"- Depth: {a.get('depth', 'full')}, at most {a.get('max_sentences', 6)} sentences in your reply"
                 f" (the page's notes do not count). Lead with the {str(a.get('lead_with', 'figure')).replace('_', ' ')}.")
    lines.append("- Codes: write HS/CN8 codes next to names." if a.get("show_codes")
                 else "- Codes: no HS or CN8 codes and no abbreviations; plain names of the goods.")
    if a.get("show_method"):
        lines.append("- Name the method and its parameters.")
    else:
        lines.append("- Do not name methods (STL, change points, MASE); say what the data shows.")
    lines.append({"inline": "- Sources: cite the API call behind each figure.",
                  "footer": f"- Sources: end with \"{SOURCE_LINE}\"",
                  "on_request": "- Sources: only when asked."}.get(a.get("show_sources"), ""))
    if a.get("quotable_sentence"):
        lines.append("- End with one sentence the reader can quote as is, then the source line.")
    unc = {"plain_range": "a likely range, not a promise", "interval": "the 80% interval and the scenarios",
           "backtest": "the interval and the backtest skill against the seasonal naive"}.get(a.get("uncertainty"))
    if unc:
        lines.append(f"- A forecast is told as {unc}.")
    if c.get("prefer"):
        lines.append(f"- Charts to prefer: {', '.join(c['prefer'])}" +
                     (f"; avoid: {', '.join(c['avoid'])}" if c.get("avoid") else "") +
                     f". At most {c.get('max_new_panels', 3)} new panel(s) per answer.")
    vocab = p.get("vocabulary") or {}
    if vocab:
        lines.append("- Words: " + "; ".join(f"say \"{v}\" for \"{k}\"" for k, v in vocab.items())
                     + " (in your words only, never inside a {placeholder}).")
    lines.append(f"- At most {ag.get('max_per_question', 3)} specialist(s) answer one question.")
    lines.append("- The figures never change with the reader: the same tools, the same numbers, the same "
                 "rules on figures; only the depth and the words change.")
    return "\n".join(x for x in lines if x is not None).strip()
