# SPDX-License-Identifier: MIT
"""Products named in words: which code the user means, and the names an agent may give a code.

Two traps, both from one live answer ("a map of imports of nuclear" drew chapter 84 as "nuclear reactors"):

1. An HS2 chapter's official title often starts with goods it barely holds. Chapter 84 is officially "Nuclear
   reactors, boilers, machinery and mechanical appliances; parts thereof", but what Luxembourg trades under it is
   machinery (lifting and handling machinery, computers, ...). Chapters are named by their short name (HS_SHORT:
   "machinery (HS 84)"), and ``misnamed`` / ``rename`` catch a text that gives a code the words of an official
   title that are not about it ("nuclear reactors" for 84).
2. A word the user asks for ("nuclear") maps to the heading that is really about it (HS 8401, nuclear reactors and
   fuel elements), never to the chapter whose title holds the word (``SPECIFIC``, then the codelist). When that
   heading has no recorded trade, the answer says so plainly (``no_trade_sentence``) and draws nothing for it.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from .settings import ensure_import_paths

ensure_import_paths()

from agents.kit.view import HS_SHORT, chapter_name  # noqa: E402

# Words a user may ask for whose goods have a heading of their own, which is NOT the chapter whose official title
# holds the word: (the words, the headings that are really about them, the goods in words).
SPECIFIC: list[tuple[re.Pattern, tuple[str, ...], str]] = [
    (re.compile(r"\bnuclear\b|\breactors?\b|\bfuel\s+elements?\b", re.I), ("8401",),
     "nuclear reactors or fuel elements"),
    (re.compile(r"\bradioactiv\w*|\buranium\b|\bplutonium\b|\bthorium\b|\bisotopes?\b", re.I), ("2844",),
     "radioactive elements or isotopes"),
    (re.compile(r"\brailways?\b|\btramways?\b|\btrams?\b|\blocomotives?\b|\brolling[- ]stock\b", re.I), ("86",),
     "railway vehicles"),
    (re.compile(r"\bboilers?\b", re.I), ("8402", "8403"), "boilers"),
    (re.compile(r"\bsound\s+(?:recorders?|reproducers?)\b", re.I), ("8519",), "sound recorders"),
    # 'steel H-sections': the HS6 line of H sections (72163310, 72163390 the heavy ones) in heading 7216
    (re.compile(r"\bh[- ]?(?:sections?|beams?)\b", re.I), ("721633", "72163390"), "H sections of steel"),
]

# The words of an official chapter title that are not what the chapter mostly holds, with the headings they are
# really about (None: the official title itself, renamed whatever the code). Longest phrases first.
_OFFICIAL = {
    "84": r"nuclear\s+reactors?,?\s+boilers?,?\s+machinery\s+and\s+mechanical\s+appliances(?:\s*;?\s*(?:and\s+)?"
          r"parts\s+thereof)?",
    "85": r"electrical\s+machinery\s+and\s+equipment\s+and\s+parts\s+thereof\s*;?\s*sound\s+recorders[^.\n]*?"
          r"(?:accessories\s+of\s+such\s+articles|reproducers)",
    "87": r"vehicles\s+other\s+than\s+railway\s+or\s+tramway\s+rolling[- ]stock(?:,?\s+and\s+parts\s+and\s+"
          r"accessories\s+thereof)?",
    "27": r"mineral\s+fuels,?\s+mineral\s+oils\s+and\s+products\s+of\s+their\s+distillation\s*;?\s*bituminous\s+"
          r"substances\s*;?\s*(?:and\s+)?mineral\s+waxes",
    "28": r"inorganic\s+chemicals\s*;?\s*organic\s+or\s+inorganic\s+compounds\s+of\s+precious\s+metals[^.\n]*?"
          r"isotopes",
    "71": r"natural\s+or\s+cultured\s+pearls[^.\n]*?(?:coins?|imitation\s+jewell?ery)",
}
_MISLEADING: dict[str, list[tuple[str, Optional[tuple[str, ...]]]]] = {
    "84": [(r"nuclear\s+reactors?(?:\s*(?:,|and|or)\s*(?:and\s+|or\s+)?(?:fuel\s+elements|boilers?))*"
            r"(?:\s*(?:,|and)\s*(?:and\s+)?(?:other\s+)?machinery)?", ("8401",)),
           (r"nuclear(?:\s+(?:power|energy))?(?:\s+(?:equipment|goods|products|technology|materials?))?", ("8401",)),
           (r"reactors?", ("8401",)),
           (r"boilers?", ("8402", "8403", "8404"))],
    "85": [(r"(?:television\s+(?:image\s+and\s+sound\s+)?)?sound\s+recorders?(?:\s+and\s+reproducers?)?",
            ("8519", "8521")),
           (r"television\s+(?:image\s+and\s+sound\s+)?recorders?(?:\s+and\s+reproducers?)?", ("8521",))],
    "87": [(r"railway(?:\s+or\s+tramway)?(?:\s+(?:rolling[- ]stock|vehicles?))?|tramways?|rolling[- ]stock|"
            r"locomotives?|trains?", ("86",))],
    "27": [(r"bituminous\s+substances?|bitumen|mineral\s+wax(?:es)?", ("2712", "2713", "2714", "2715"))],
    "28": [(r"radioactive\s+(?:chemical\s+)?(?:elements?|isotopes?|materials?|substances?)(?:\s+(?:and|or)\s+"
            r"isotopes?)?|radioactive|isotopes?|uranium|plutonium", ("2844", "2845")),
           (r"rare[- ]earth(?:\s+(?:metals?|elements?))?", ("2805", "2846"))],
    "71": [(r"(?:natural\s+or\s+cultured\s+)?pearls?", ("7101",)), (r"imitation\s+jewell?ery", ("7117",)),
           (r"coins?", ("7118",))],
}
_RULES: dict[str, list[tuple[re.Pattern, Optional[tuple[str, ...]]]]] = {
    ch: ([(re.compile(rf"\b{_OFFICIAL[ch]}", re.I), None)] if ch in _OFFICIAL else [])
    + [(re.compile(rf"\b(?:{rx})\b", re.I), heads) for rx, heads in _MISLEADING.get(ch, [])]
    for ch in set(_OFFICIAL) | set(_MISLEADING)
}

# A sentence that says the goods were NOT traded, or explains the official title, may name them.
_EXPLAINS_RE = re.compile(r"\b(?:no|not|none|never|nothing|zero|neither|nor|without|absent|different|"
                          r"official\s+(?:title|name)|despite|although|though|but\s+(?:it|this|the\s+chapter)|"
                          r"recorded\s+no)\b|n't\b", re.I)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[_*]*[A-Z0-9€])")


def product_codes(values: Iterable) -> list[str]:
    """The HS/CN codes among values (digits, 2 to 8 long), in order, once each."""
    out = []
    for v in values or []:
        v = str(v or "")
        if v.isdigit() and 2 <= len(v) <= 8 and v not in out:
            out.append(v)
    return out


def specific_in(text: str) -> list[dict]:
    """The goods with a heading of their own a text asks for: [{words, headings, goods}] ('nuclear' -> 8401)."""
    out = []
    for rx, heads, goods in SPECIFIC:
        m = rx.search(text or "")
        if m:
            out.append({"words": m.group(0).lower(), "headings": list(heads), "goods": goods})
    return out


def asked_flows(question: str) -> list[str]:
    """The flows a question asks about: IMPORT, EXPORT, or both when it names neither (or both)."""
    q = question or ""
    imp = re.search(r"\bimport\w*|\bbu(?:y|ys|ying|ought)\b|\bpurchas\w*|\bcome\s+from\b|\bsuppl\w*", q, re.I)
    exp = re.search(r"\bexport\w*|\bsel(?:l|ls|ling)\b|\bsold\b|\bgo(?:es)?\s+to\b|\bbuyers?\b", q, re.I)
    if imp and not exp:
        return ["IMPORT"]
    if exp and not imp:
        return ["EXPORT"]
    return ["IMPORT", "EXPORT"]


def _ok_for(code: str, heads: Optional[tuple[str, ...]], codes: list[str]) -> bool:
    """A phrase about ``heads`` is fine when one of the codes is (inside) one of those headings."""
    if heads is None:
        return False
    return any(c.startswith(h) for c in codes for h in heads)


def misnamed(text: str, codes: Iterable) -> list[dict]:
    """Phrases of a text that give the product codes drawn the words of an official chapter title that are not
    about them: [{phrase, chapter, name, about, sentence}]. 'Nuclear reactors' with product 84 (machinery) is one;
    with 8401 it is not, nor in a sentence that says there was no such trade or explains the official title."""
    codes = product_codes(codes)
    chapters = [c for c in dict.fromkeys(c[:2] for c in codes) if c in _RULES]
    if not chapters or not text:
        return []
    out = []
    for sentence in SENTENCE_RE.split(text):
        if _EXPLAINS_RE.search(sentence):
            continue
        for ch in chapters:
            for rx, heads in _RULES[ch]:
                for m in rx.finditer(sentence):
                    if _ok_for(ch, heads, codes):
                        continue
                    if any(m.group(0).lower() in p["phrase"].lower() for p in out if p["sentence"] == sentence):
                        continue
                    out.append({"phrase": m.group(0), "chapter": ch, "name": chapter_name(ch),
                                "about": list(heads) if heads else None, "sentence": sentence})
    return out


def rename(text: str, codes: Iterable) -> str:
    """The text with those phrases replaced by the chapter's short name: 'imports of nuclear reactors' ->
    'imports of machinery (HS 84)'. A name already followed by its code is not repeated."""
    if not text:
        return text
    found = misnamed(text, codes)
    for p in found:
        name = p["name"]
        new_sentence = re.sub(re.escape(p["phrase"]), lambda m: _cased(name, m), p["sentence"], count=1)
        new_sentence = re.sub(rf"(\(HS {p['chapter']}\))(\s*\((?:HS|chapter)\s*{p['chapter']}\))+", r"\1",
                              new_sentence)
        text = text.replace(p["sentence"], new_sentence, 1)
        for q in found:   # later phrases of the same sentence are found in the new one
            if q["sentence"] == p["sentence"]:
                q["sentence"] = new_sentence
    return text


def _cased(name: str, m: re.Match) -> str:
    """Capitalised only where a sentence (or a title) starts."""
    start = not m.string[:m.start()].strip(" *_\"'([")
    return name[:1].upper() + name[1:] if start else name


def heading_words(label: str, limit: int = 50) -> str:
    """'Lifting, handling, loading or unloading machinery, e.g. lifts, ...' -> 'lifting, handling, loading or
    unloading machinery': a heading's goods said in a sentence (an HS2 chapter by its short name)."""
    label = str(label or "")
    for cut in (";", "(", ", e.g.", ", incl.", " and units thereof", " and parts thereof", ", n.e.s"):
        label = label.split(cut)[0]
    label = label.strip(" ,")
    if len(label) > limit:
        cut = label[:limit].rsplit(" ", 1)[0].rstrip(" ,")
        while re.search(r"\s(?:and|or|of|for|the|in|with|to|a|an|by|from|whether|not|e\.g\.)$", cut):
            cut = cut.rsplit(" ", 1)[0].rstrip(" ,")
        label = cut + "…"
    return label[:1].lower() + label[1:] if len(label) > 1 and not label[1].isupper() else label


def name_of(code: str, label: Optional[str] = None, limit: int = 70) -> str:
    """The name an agent gives a code: an HS2 chapter by its short name, any other code by its label's goods."""
    code = str(code)
    if code in HS_SHORT:
        return HS_SHORT[code]
    words = heading_words(label or "", limit)
    return (words[:1].upper() + words[1:]) if words else f"HS {code}"


def flows_words(flows: list[str]) -> str:
    words = {"IMPORT": ["imports"], "EXPORT": ["exports"], "BOTH": ["imports", "exports"]}
    out = list(dict.fromkeys(w for f in flows for w in words.get(str(f).upper(), ["trade"])))
    return " or ".join(out)


def no_trade_sentence(goods: str, codes: list[str], flows: list[str], year: str) -> str:
    """'Luxembourg recorded no imports of nuclear reactors or fuel elements (HS 8401) in 2025.'"""
    tag = ", ".join(f"CN8 {c}" if len(c) == 8 else f"HS {c}" for c in codes)
    return f"Luxembourg recorded no {flows_words(flows)} of {goods} ({tag}) in {year}."


NO_TRADE_RE = re.compile(r"\b(?:recorded\s+no|no\s+recorded|no\s+(?:imports?|exports?|trade)|did\s+not\s+"
                         r"(?:import|export|buy|sell|trade)|none\s+(?:recorded|at\s+all)|zero)\b", re.I)


def says_no_trade(text: str, goods_words: str) -> bool:
    """The reply says plainly that the goods were not traded (and names them)."""
    if not NO_TRADE_RE.search(text or ""):
        return False
    key = [w for w in re.findall(r"[a-z]{5,}", (goods_words or "").lower())][:1]
    return not key or key[0][:6] in (text or "").lower()


def omnibus_for(chapter: str, words: str) -> bool:
    """The chapter's official title holds these words, which it is not about ('nuclear' and 84)."""
    return any(heads and rx.search(words or "") for rx, heads in _RULES.get(str(chapter), []))


# --------------------------------------------------------------------------- codes that changed (HS revisions)
# A code created by a revision of the HS (2017, 2022) has no data before it; its goods were filed under another
# code. Smartphones got CN8 85171300 (HS6 851713) in 2022; until 2021 they were in 85171200 (851712), "telephones
# for cellular networks or for other wireless networks", with other mobile phones.
CONCORDANCE = {
    "85171300": {"before": "85171200", "until": "2021", "from": "2022", "goods": "mobile phones",
                 "now": "smartphones", "level": "CN8"},
    "851713": {"before": "851712", "until": "2021", "from": "2022", "goods": "mobile phones", "now": "smartphones",
               "level": "HS6"},
}


def before_name(c: dict) -> str:
    """How the earlier code's goods are named in a sentence: they are all of them, not the new code's goods alone
    ('mobile phones (the earlier code, which held smartphones with other mobile phones)')."""
    return f"{c['goods']} (the earlier code, which held {c['now']} with other {c['goods']})"


def predecessor(code: str) -> Optional[dict]:
    """{before, until, from, goods, now, level, before_name, say} for a code a revision created, else None."""
    c = CONCORDANCE.get(str(code))
    if not c:
        return None
    name = before_name(c)
    return {**c, "before_name": name,
            "say": (f"{c['level']} {code} ({c['now']}) starts in {c['from']}: the {c['from']} edition of the HS "
                    f"gave {c['now']} a code of their own. Until {c['until']} they were filed under "
                    f"{c['level']} {c['before']} with other {c['goods']} (telephones for cellular networks), so the "
                    f"figures before {c['from']} are all {c['goods']}, not {c['now']} alone: call them {name}, "
                    f"never '{c['now']} exports'")}


EARLIER_AS_NOW_RE = re.compile(
    r"\b(?P<now>smartphones?|smartphone)(?P<flow>\s+(?:exports?|imports?|sales|purchases))?(?P<verb>\s+(?:were|was|"
    r"stood\s+at|reached|totalled|totaled|amounted\s+to))\s+(?P<fig>€\s?[\d.,]+\s?(?:bn|m|k)?)\s+in\s+"
    r"(?P<year>(?:19|20)\d\d)\b", re.IGNORECASE)


def earlier_as_now(text: str) -> str:
    """'Smartphone exports were €2.14 bn in 2015' (a year the smartphone code did not exist yet: those are all
    mobile phones) -> 'Mobile-phone exports (the earlier code, which held smartphones with other mobile phones)
    were €2.14 bn in 2015'. Years from the new code's first year on are left as they are."""
    c = CONCORDANCE["85171300"]

    def fix(m: re.Match) -> str:
        if m.group("year") >= c["from"]:
            return m.group(0)
        flow = (m.group("flow") or "").strip()
        head = "Mobile-phone " + flow.lower() if flow else "Mobile phones"
        if not m.group(0)[:1].isupper():
            head = head[:1].lower() + head[1:]
        return (f"{head} (the earlier code, which held {c['now']} with other {c['goods']}){m.group('verb')} "
                f"{m.group('fig')} in {m.group('year')}")
    return EARLIER_AS_NOW_RE.sub(fix, text or "")
