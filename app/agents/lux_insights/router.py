# SPDX-License-Identifier: MIT
"""Which specialists answer a question, in which order, and what each is asked to do.

The router asks the LLM for a plan (JSON). When the LLM is not reachable, or answers nonsense, it falls
back to a small keyword table (KEYWORDS below), so the agent still answers the common questions.

    plan = route("Why did exports jump in 2025, and what comes next?", llm, page_text)
    plan.steps == [Step("trend_finder", "..."), Step("cause_finder", "..."), Step("forecaster", "...")]
"""
from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

from .settings import ensure_import_paths
from .settings import api_params

log = logging.getLogger("lux_insights.router")

SPECIALISTS = ("trend_finder", "cause_finder", "forecaster", "visualizer")
ROLES = SPECIALISTS + ("general",)

# The intent labels of Marcos's first router, kept for the page's mode_change messages.
MODES = {"trend_finder": "trend", "cause_finder": "cause", "forecaster": "forecast", "visualizer": "visual",
         "general": "general"}

PROMPT = """\
You route questions about Luxembourg's trade in goods to four agents that draw on a shared page, and you
plan who answers, in which order. Answer with JSON only:
{"plan": [{"agent": "<name>", "task": "<what this agent should do, one sentence>"}]}

The agents:
- trend_finder: what changed and why in the data (drills a rise or fall down to the product line, the partners
  it goes through, rewrites the headline), lasting shifts and change points since 1999, what grew fastest.
- cause_finder: lines changes up with dated events (agreements, tariffs, crises, the pandemic, wars); waterfalls of
  one year's change.
- forecaster: what to expect next year or later, with an uncertainty band and its backtest; seasonality; which
  forecasting method works best and how good or reliable the forecast is (the backtest of every method).
- visualizer: any chart (maps, routes, treemap, butterfly, bump, scatter, heatmap, lines of a partner or product),
  comparisons on one chart ("compare Luxembourg with Belgium", "Germany against France", "2025 against 2024",
  "exports against imports"), moving the page to another year ("show me 2020"), clearing the page.
- general: greetings, what the agents can do, anything off topic (never questions about forecasts or methods).

Rules:
- One to three agents, in the order their answers build on each other: trend_finder before cause_finder before
  forecaster; the visualizer after the analysts unless the question is only about showing something.
- "Why did X happen?" is the trend_finder (the data); add the cause_finder when the question asks about events,
  causes outside the data or what lined up; add the forecaster when it also asks what comes next.
- "Which events line up with the changes since 1999?": trend_finder (change points) then cause_finder. When the page
  already shows trend breaks (see below) and the question asks which events line up with them: the cause_finder
  alone, with the task "line the curated events up with every trend break on the page".
- "Compare ..." (countries, partners, products, two years, exports and imports) is the visualizer alone, unless the
  question also asks why or what comes next.
- A follow-up about a numbered mark ("tell me more about [2]", "remove the forecast", "update [1]") goes to the
  agent who drew it (see the page below), alone. "Keep ..." goes to the agent who drew it, alone.
- "Where do the goods go / come from?", "What does Luxembourg sell / buy?", "Bought vs sold", ranks over the years,
  "month by month", "where is Luxembourg specialised": the visualizer alone, with a NEW chart (maps, treemap,
  butterfly, bump, heatmap, scatter); never ask it to reuse the crossroads for these.
- "What did <an event: COVID-19, the financial crisis, Brexit, the war> do to trade?": the cause_finder alone.
- "What should we expect for YEAR?" / "what comes next": the forecaster, for exports only unless the question names
  imports. "Which forecasting method works best?", "how good is the forecast?": the forecaster alone.
- "Show me YEAR": the visualizer alone, to move the page (never to clear it). "Clear the page", "reset", "start
  over": the visualizer alone.
- "As a table", "year-on-year growth of ...", "the gap between ...": the visualizer alone.
- "When did exports shift for good?" / trend breaks: the trend_finder alone.
- "Which products / HS4 / HS6 / CN8 lines grew fastest (fell most) in YEAR?": the trend_finder alone (it draws the
  chart too). "Which <goods> lines drive imports from <partner>?", "<goods> exports by product": the visualizer alone.
- "Are there trends?", "Which partners / products grew fastest since YEAR (over the years)?": the trend_finder alone
  (it finds the lasting shifts and draws the growth per year ranking itself).
- Write each task with the details from the question (years, flows, partners, products). Say WHAT to answer, not
  which chart to reuse: the agents know their charts. The task comes from the user's question, never from an
  agent's earlier follow-up suggestion: "the breaks" means every break on the page, not the one an agent named.
- Keep the user's period word for word ("since 1999", "from 2010 to 2020"); when the question names none, add none
  (never "since 2015"): the data starts in 1999 and the agents start long-run views there.

The page now (who drew which number):
{page}
"""


@dataclass
class Step:
    agent: str
    task: str = ""


@dataclass
class Plan:
    steps: list[Step] = field(default_factory=list)
    source: str = "llm"      # llm, keywords, owner

    @property
    def agents(self) -> list[str]:
        return [s.agent for s in self.steps]


def _clean(steps: list[Step]) -> list[Step]:
    out: list[Step] = []
    for s in steps:
        if s.agent in ROLES and s.agent not in [o.agent for o in out]:
            out.append(s)
    if len(out) > 1:
        out = [s for s in out if s.agent != "general"]
    order = {a: i for i, a in enumerate(SPECIALISTS)}
    analysts = [s for s in out if s.agent in ("trend_finder", "cause_finder", "forecaster")]
    if len(analysts) > 1:  # analysts always build on each other in this order
        sorted_analysts = sorted(analysts, key=lambda s: order[s.agent])
        it = iter(sorted_analysts)
        out = [next(it) if s.agent in order and s in analysts else s for s in out]
    return out[:3]


def parse_plan(raw: str) -> Optional[Plan]:
    """The plan in an LLM answer, or None."""
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    items = data.get("plan") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    steps = []
    for it in items:
        if isinstance(it, str):
            steps.append(Step(it.strip()))
        elif isinstance(it, dict) and isinstance(it.get("agent"), str):
            steps.append(Step(it["agent"].strip(), str(it.get("task") or "")[:400]))
    steps = _clean(steps)
    return Plan(steps, "llm") if steps else None


def _norm(text: str) -> str:
    """Lower case, ASCII, punctuation as spaces: 'COVID-19's' -> 'covid 19 s'."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text.lower())).strip()


# The keyword fallback, used only when the LLM gives no plan: the words that pick each specialist, in the
# order their answers build on each other. Matched against the normalised question.
KEYWORDS = {
    "trend_finder": r"\b(why|jump(?:ed|s)?|gr[eo]w(?:th|n|s)?|rise|rose|risen|increase[ds]?|drove|driv(?:e|er|ers|ing)|"
                    r"behind|went up|surge[ds]?|what changed|trend breaks?|change ?points?|for good|"
                    r"lasting (?:shifts?|changes?)|level shifts?|structural breaks?|step changes?)\b",
    "cause_finder": r"\b(events?|covid(?: 19)?|pandemic|lockdowns?|brexit|sanctions?|causes?|caused|tariffs?|war|"
                    r"crisis|shocks?)\b",
    "forecaster": r"\b(forecast\w*|next year|expect(?:ed)?|20(?:2[6-9]|3\d)|outlook|predict\w*|future|"
                  r"coming year|projections?|backtests?)\b",
    "visualizer": r"\b(maps?|where do(?:es)? (?:the |our |its )?(?:goods|exports|products|trade|imports|it|they) "
                  r"(?:go|come from)|routes?|heat ?maps?|tree ?maps?|products|rank(?:s|ed|ings?)?|butterfly|"
                  r"scatter|speciali[sz]\w*|compare[sd]?|comparing|comparison|side by side|show me|go to|"
                  r"clear|reset|start over|chart|plot|draw|month by month|monthly)\b",
}
_KEYWORDS_RE = {role: re.compile(words) for role, words in KEYWORDS.items()}

# 'Is any personal data used here?', 'Where does my question go?': the general agent's privacy facts.
PRIVACY_RE = re.compile(
    r"\b(personal data|personal information|privacy|private data|gdpr|data protection|"
    r"where does (?:my|the|a) question go|(?:is|are) my questions? (?:stored|kept|saved|logged)|"
    r"(?:store|keep|save|log)s? (?:my|the) questions?|profil(?:e|es|ing) (?:me|users?|people))\b")


def keyword_plan(question: str) -> Plan:
    """The specialists whose words the question uses (KEYWORDS), else the general agent."""
    q = _norm(question)
    steps = _clean([Step(role, f"Answer: {question}") for role, rx in _KEYWORDS_RE.items() if rx.search(q)])
    return Plan(steps or [Step("general", question)], "keywords")


DIRECT_RE = re.compile(r"^\s*(keep|pin|remove|drop|delete)\b[\s\[\]#,andmrkst]*\d", re.IGNORECASE)


def direct_plan(question: str) -> Optional[Plan]:
    """'keep 1', 'keep [1] and [3]', 'remove 2': the user's own action, done at once without the LLM (the page's
    Keep and Remove buttons do the same). Plan source 'direct', task 'keep:1,3' or 'remove:2'."""
    m = DIRECT_RE.match(question or "")
    if not m or len(question) > 40:
        return None
    nums = [int(n) for n in re.findall(r"\b(\d{1,2})\b", question)]
    verb = "keep" if m.group(1).lower() in ("keep", "pin") else "remove"
    return Plan([Step("visualizer", f"{verb}:{','.join(map(str, nums))}")], "direct") if nums else None


NUMBER_RE = re.compile(r"\[(\d{1,2})\]|\b(?:mark|number|note)\s+(\d{1,2})\b", re.IGNORECASE)


def owner_plan(question: str, spec: Optional[dict]) -> Optional[Plan]:
    """'Tell me more about [2]', 'remove [3]': the agent that drew that number, alone."""
    if not spec:
        return None
    from . import page
    nums = [int(a or b) for a, b in NUMBER_RE.findall(question)]
    owners = [page.owner_of_number(spec, n) for n in nums]
    owners = [o for o in owners if o in SPECIALISTS]
    if not owners or len(set(owners)) != 1:
        return None
    return Plan([Step(owners[0], f"The user refers to [{', '.join(map(str, nums))}], which you drew: {question}")],
                "owner")


def reader_of(spec: Optional[dict]) -> Optional[dict]:
    """The profile of the reader the page is drawn for (contract 1.4), or None."""
    ensure_import_paths()
    try:
        from agents.kit.personas import persona_of
        return persona_of(spec)
    except Exception as exc:  # no profiles: answer as for every reader
        log.warning("persona profiles unavailable: %s", exc)
        return None


def reader_rules(reader: Optional[dict]) -> str:
    """The router's lines about the reader: how many agents, and which one leads when in doubt."""
    if not reader:
        return ""
    ag = reader.get("agents") or {}
    cap = int(ag.get("max_per_question", 3))
    lead = ag.get("lead")
    return (f"\nThe reader is a {reader['label'].lower() if not reader['label'][:2].isupper() else reader['label']}"
            f" ({reader.get('tagline', '')}). Plan at most {cap} agent{'s' if cap != 1 else ''}"
            + (f"; when in doubt, {lead} answers" if lead else "") + ".\n")


def capped(plan: Plan, reader: Optional[dict]) -> Plan:
    """At most the reader's number of agents per question (contract 1.4): enforced here, not left to the
    prompt. The first ones stay: they are the ones the others build on."""
    cap = int(((reader or {}).get("agents") or {}).get("max_per_question", 3))
    if len(plan.steps) > cap:
        log.info("plan capped to %s agent(s) for the reader %s", cap, (reader or {}).get("id"))
        plan.steps = plan.steps[:max(1, cap)]
    return plan


METHOD_RE = re.compile(r"\b(forecast\w*|predict\w*|backtest\w*)\b", re.IGNORECASE)
METHOD_ASK_RE = re.compile(r"\b(methods?|models?|best|accura\w*|reliab\w*|how\s+good|skill\w*|works?|trust\w*|"
                           r"backtest\w*|which)\b", re.IGNORECASE)


def asks_method(question: str) -> bool:
    """'Which forecasting method works best here?', 'How good is the forecast?': the forecaster's backtest."""
    return bool(METHOD_RE.search(question or "") and METHOD_ASK_RE.search(question or ""))


def asks_privacy(question: str) -> bool:
    """'Is any personal data used here?', 'Where does my question go?': the general agent's privacy facts."""
    return bool(PRIVACY_RE.search(_norm(question)))


# Four frequent questions, asked as they are: planned in code, so no router call (3 to 4 s) before the specialist
# starts, and the same specialist every time. Anything longer ('..., and what comes next?') goes to the router.
WHY_CHANGE_RE = re.compile(r"^\s*why\s+did\s+(?:luxembourg'?s\s+)?(exports|imports)\s+(jump|rise|grow|increase|"
                           r"fall|drop|decline|change|surge|shrink)\s+in\s+((?:19|20)\d\d)\s*\??\s*$", re.IGNORECASE)
SHIFT_RE = re.compile(r"^\s*when\s+did\s+(exports|imports)\s+shift\s+for\s+good\s*\??\s*$", re.IGNORECASE)
EXPECT_RE = re.compile(r"^\s*what\s+(?:should|do|can|could)\s+we\s+expect\s+(?:for\s+(exports|imports)\s+)?"
                       r"(?:for|in)\s+((?:20)\d\d)\s*\??\s*$", re.IGNORECASE)
WHERE_GO_RE = re.compile(r"^\s*where\s+do\s+(?:the\s+|luxembourg'?s\s+)?goods\s+go\s*\??\s*$", re.IGNORECASE)


def common_plan(question: str) -> Optional[Plan]:
    """The plan of one of the four frequent questions asked as it is, in code; None for anything else."""
    m = WHY_CHANGE_RE.match(question or "")
    if m:
        flow, year = m.group(1).lower(), m.group(3)
        return Plan([Step("trend_finder", f"Why did {flow} {m.group(2).lower()} in {year}: where the change came "
                                          f"from, down to the product line, against {int(year) - 1}.")], "rule")
    m = SHIFT_RE.match(question or "")
    if m:
        return Plan([Step("trend_finder", f"When {m.group(1).lower()} shifted for good: the lasting breaks in the "
                                          "monthly series since 1999.")], "rule")
    m = EXPECT_RE.match(question or "")
    if m:
        flow = (m.group(1) or "exports").lower()
        return Plan([Step("forecaster", f"What to expect for {flow} in {m.group(2)}: the forecast with its band, "
                                        "tested on the past.")], "rule")
    if WHERE_GO_RE.match(question or ""):
        return Plan([Step("visualizer", "Where Luxembourg's goods go: the export partners on a map, with the top "
                                        "three marked.")], "rule")
    return None


def rule_plan(question: str) -> Optional[Plan]:
    """Questions answered the same way every time, planned in code (no LLM, no wrong turn): 'Show me 2025' moves
    the page (the visualizer, never a clear), 'bought vs sold' is a butterfly, the four frequent questions."""
    from .tools import BY_PRODUCT_RE, TWO_WAYS_RE, partners_in, show_year
    common = common_plan(question)
    if common:
        return common
    if asks_privacy(question):
        return Plan([Step("general", "Privacy: answer with the facts of your prompt (where the question goes, what is "
                                     "kept, no personal data); draw nothing.")], "rule")
    year = show_year(question)
    if year:
        return Plan([Step("visualizer", f"Move the page to {year}: set_context(period={year}) and get_flows({year}), "
                                        "then highlight the largest partner on each side of the crossroads. Add no "
                                        "chart and never clear the page.")], "rule")
    if TWO_WAYS_RE.search(question or "") and not partners_in(question):
        by = "product" if BY_PRODUCT_RE.search(question) else "partner"
        return Plan([Step("visualizer", f"Bought against sold, {by} by {by}: a NEW butterfly chart (show_panel "
                                        f"type butterfly, by {by}), then highlight the {by} with the widest gap.")],
                    "rule")
    return None


def on_this_page(plan: Plan, question: str, spec: Optional[dict]) -> Plan:
    """A plan fixed in code, fitted to a page the user has emptied: 'Show me YEAR' highlights on the crossroads,
    and when the crossroads is gone it draws the year's partners as a new chart first."""
    from .tools import show_year
    year = show_year(question)
    if not (spec and year and plan.source == "rule"):
        return plan
    if any(p["id"] == "crossroads" for p in spec.get("panels", [])):
        return plan
    plan.steps = [Step("visualizer", f"Move the page to {year}: set_context(period={year}) and get_flows({year}). The "
                                     "crossroads is not on the page: draw the year's partners as ONE new chart "
                                     "(show_panel type butterfly, by partner, id two_ways_partner), then highlight the "
                                     "largest partner on each side on it. Never clear the page.")]
    return plan


def with_forecaster(plan: Plan, question: str) -> Plan:
    """A question about the forecasting methods goes to the forecaster, never to the general agent."""
    if not asks_method(question) or "forecaster" in plan.agents:
        return plan
    task = ("Which forecasting method works best: get_backtest, then answer from its table (each method's edge over "
            "the seasonal naive forecast at 1, 3, 6 and 12 months, the default ensemble, the neural network "
            "generated by BESSER).")
    steps = [s for s in plan.steps if s.agent != "general"]
    plan.steps = _clean(steps + [Step("forecaster", task)]) if steps else [Step("forecaster", task)]
    return plan


# The plan never waits long: on 15 Oct 2026 one router call hung ten minutes (the OpenAI client's default timeout)
# before a retry answered. Past ROUTER_SECONDS the question's words plan it.
ROUTER_SECONDS = float(os.environ.get("LUX_ROUTER_SECONDS", 8))
_router_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="lux-router")


def _predict_within(llm: Any, seconds: float, *args, **kwargs) -> Any:
    """llm.predict, or TimeoutError after ``seconds`` (the call goes on in the background and is forgotten)."""
    return _router_pool.submit(llm.predict, *args, **kwargs).result(timeout=seconds)


def route(question: str, llm: Any = None, page_text: str = "", spec: Optional[dict] = None,
          history: str = "") -> Plan:
    """The plan for a question: a direct keep/remove, the owner of a cited number, a plan fixed in code, else the
    LLM's plan, else keywords; never more agents than the reader's profile allows (max_per_question)."""
    plan = direct_plan(question) or owner_plan(question, spec) or rule_plan(question)
    if plan:
        return on_this_page(plan, question, spec)
    reader = reader_of(spec)
    if llm is not None:
        try:
            prompt = PROMPT.replace("{page}", page_text or "(empty)") + reader_rules(reader)
            if history:
                prompt += "\nThe conversation so far:\n" + history
            raw = _predict_within(llm, ROUTER_SECONDS, question,
                                  parameters=api_params(getattr(llm, "name", ""), 0, 300,
                                                        response_format={"type": "json_object"}),
                                  system_message=prompt)
            plan = parse_plan(raw)
            if plan:
                return capped(with_forecaster(plan, question), reader)
            log.warning("the router answered no plan: %r", (raw or "")[:200])
        except Exception as exc:   # a timeout too: the words of the question plan it
            log.warning("the router LLM failed (%s); using keywords", type(exc).__name__ if not str(exc) else exc)
    return capped(with_forecaster(keyword_plan(question), question), reader)
