# SPDX-License-Identifier: MIT
"""One conversation with the four specialists: route a question, run each specialist, remember the turn.

The BAF states (lux_insights_agent.py) call ``plan`` and ``start`` in the router state and ``result`` in each
specialist state; ``answer`` does the whole turn at once (for ``--ask`` and the tests).

Speed: the plan comes from a small fast model (``router_llm``, gpt-4o-mini by default) and the specialists run
on the big one. ``start`` runs the plan's specialists at once when they do not build on each other (the
forecaster's band does not need the trend finder's thread), each in its own pen; one that builds on an earlier
one (the cause finder lines events up with the trend finder's breaks, the visualizer draws its thread on the map)
waits for it and reads its answer. The replies still come out in the plan's order.
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Iterator, Optional

from . import page, products
from . import methods as method_names
from . import record as decision_record
from .router import MODES, Plan, Step, direct_plan, owner_plan, route
from .specialists import TITLES, Reply, run_specialist, with_title
from .tools import AGENTS, HS_SHORT, Toolbox, partners_in, years_in

log = logging.getLogger("lux_insights.conversation")

MAX_HISTORY = 12        # entries kept
HISTORY_IN_PROMPT = 6   # entries shown to the model
ENTRY_CHARS = 500
FOLLOW_UP_WORDS = 10    # a question this short, with no partner, goods or year of its own, continues the last one
_GOODS_WORDS = {w for name in HS_SHORT.values() for w in [str(name).split()[0].lower().strip(',')] if len(w) >= 5}
_GOODS_WORDS -= {"other", "articles", "special", "miscellaneous", "metal", "coated", "printed"}


# Who answers a follow-up, by its words (first match wins): 'why?' is the cause finder's, 'are there trends?' the
# trend finder's, 'what next?' the forecaster's. Each works on the chart the last answer drew.
FOLLOW_AGENTS = [
    (re.compile(r"\b(why|cause[sd]?|reasons?|explain\w*|because|events?|tariffs?|sanctions?|agreements?|news)\b", re.I),
     "cause_finder",
     " Line the moves of that chart up with dated events for the same partner and goods (get_events, then ONE "
     "add_events with target set to that chart); say plainly the dates line up, which is not proof of cause."),
    (re.compile(r"\b(forecasts?|expect\w*|predict\w*|outlook|next (?:year|months?)|coming|will)\b", re.I),
     "forecaster",
     " Forecast that same series (the same partner, goods and frequency) and add the band on that chart."),
    (re.compile(r"\b(season\w*|months? (?:are|is) (?:high|low|strong|weak)|every year|calendar)\b", re.I),
     "trend_finder",
     " Show the seasonal pattern of that same series (the same partner, goods and start): a heatmap of its months by "
     "year (show_panel type='heatmap', freq M, the same partner and product, start the same year), and say which "
     "months are usually high and low, from analyze_trend's seasonal component, and how strong the pattern is."),
    (re.compile(r"\b(trends?|patterns?|shifts?|breaks?|growth|grow\w*|rising|falling|changes?)\b", re.I),
     "trend_finder",
     " Analyse that same series (analyze_trend on the same partner, goods and months): mark its lasting shifts "
     "(add_trend_breaks) and its trend line (add_trend_line) on that chart, and say its seasonal pattern."),
]


CONSISTENT = (" One measure of direction for the whole span, the same in every answer: the trend's change from "
              "analyze_trend (its trend_summary), over the span the chart shows; never call the series risen or fallen "
              "from its first and last month alone, nor set a first and a last month side by side as if they "
              "said it. When an earlier answer (this turn or the last ones) already gave the trend's change for this "
              "series, quote that same figure over that same span; never compute a second one over another span. A "
              "fitted trend line's {growth} is a rate a year, another figure: in the reply say the trend's change over "
              "the span (e.g. 'the trend fell 23.6% from January 2023 to July 2026'), and the line's rate, if at all, "
              "with 'a year'; never call the change over the span 'the fitted trend'. Never compare a first and a last "
              "month, nor one month with another, as a measure of direction.")


# The direction over a span is the analysts' (CONSISTENT); the visualizer draws the chart and leaves it to them.
ANALYSTS = ("trend_finder", "cause_finder", "forecaster")
DRAW_ONLY = (" Draw the chart and say what it shows; leave the direction over the span to the trend finder, and add "
             "no note about the trend.")


SPAN_RE = re.compile(r"\b(since|from (?:19|20)\d\d|over the (?:years|period)|months? since)\b", re.I)


def has_subject(question: str) -> bool:
    """Whether a question names its own subject: a partner, goods or a year ('Are there trends' names none)."""
    words = set(re.findall(r"[a-z-]+", (question or "").lower()))
    return bool(partners_in(question) or years_in(question) or products.specific_in(question)
                or any(w in words or w + "s" in words for w in _GOODS_WORDS))

# Who builds on whom within one turn: a specialist waits for these (when they are in the plan) and reads their
# answers; the others start at once. The forecaster and a second analyst never wait for each other.
DEPENDS_ON = {"cause_finder": {"trend_finder"}, "visualizer": {"trend_finder"}, "general": set()}


def mode_change(role: Optional[str], state: str = "working") -> dict:
    """The control message the page reads to light up the working agent's card (Marcos's mode_change, with
    the agent's name and pen added). ``role=None`` means every agent is done."""
    if role is None:
        return {"type": "mode_change", "mode": "idle", "agent": None, "state": "idle"}
    a = AGENTS.get(role, {})
    return {"type": "mode_change", "mode": MODES.get(role, "general"), "agent": role,
            "label": TITLES.get(role, role), "color": a.get("color"), "state": state}


class Conversation:
    def __init__(self, api_url: str, llm: Any, boxes: Optional[dict[str, Toolbox]] = None, max_steps: int = 12,
                 router_llm: Any = None, parallel: bool = True, session: Optional[str] = None):
        self.api_url = api_url
        self.session = session   # the visitor's page the specialists draw on (None: the shared page)
        self.llm = llm
        self.router_llm = router_llm or llm   # the plan: a small fast model is enough
        self.max_steps = max_steps
        self.boxes = boxes or {}
        self.history: list[dict] = []
        self.questions: list[str] = []   # every question of the conversation (the follow-up never repeats one)
        self._before_turn = ""    # the conversation before the question being answered
        self._plan: Optional[Plan] = None
        self._goods: Optional[dict] = None   # goods with a heading of their own the question asks for (asked_product)
        self.parallel = parallel
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="lux-specialist")
        self.asked_at = time.monotonic()   # when the question being answered came in
        self._futures: dict[str, Future] = {}
        self._subject_q: Optional[str] = None   # the last question that named its own subject
        self.last_panels: list[str] = []         # the charts the last answers drew (a follow-up marks them)
        self._spans: dict = {}                   # the series analysed this turn and their span (Toolbox.shared_spans)

    def box(self, role: str) -> Toolbox:
        if role not in self.boxes:
            self.boxes[role] = Toolbox(self.api_url, role if role in AGENTS else "visualizer", session=self.session)
        self.boxes[role].shared_spans = self._spans
        return self.boxes[role]

    def history_text(self) -> str:
        lines = []
        for e in self.history[-HISTORY_IN_PROMPT:]:
            text = e["content"] if len(e["content"]) <= ENTRY_CHARS else e["content"][:ENTRY_CHARS] + " ..."
            lines.append(f"{'User' if e['role'] == 'user' else 'Agent'}: {text}")
        return "\n".join(lines)

    def remember(self, role: str, content: str) -> None:
        if role == "user":
            self.questions = (self.questions + [content])[-200:]
        self.history.append({"role": role, "content": content})
        del self.history[:-MAX_HISTORY]

    def plan(self, question: str) -> Plan:
        self.asked_at = time.monotonic()
        self._spans.clear()   # a new question may ask for another span
        spec = None
        page_text = ""
        try:
            spec = self.box("visualizer").spec()
            page_text = page.summary(spec)
        except Exception as exc:
            log.warning("could not read the page: %s", exc)
        self._before_turn = self.history_text()
        self._goods = self.asked_goods(question, spec)
        if self._goods and self._goods["no_trade"]:
            # goods Luxembourg did not trade ('a map of imports of nuclear'): said plainly, no chart, no LLM
            plan = Plan([Step("visualizer", "no_trade")], "no_trade")
        else:
            plan = route(question, self.router_llm, page_text, spec, self._before_turn)
            follow = self.follow_up_hint(question, spec)
            if follow and plan.source not in ("direct", "owner"):
                # the follow-up's own specialists, on the subject and the chart of the last answer: "is there a
                # trend, and why?" is the trend finder's then the cause finder's (the cause finder builds on it)
                chosen: dict[str, str] = {}
                for rx, a, extra in FOLLOW_AGENTS:
                    if rx.search(question) and a not in chosen:
                        chosen[a] = extra
                order = [a for a in ("trend_finder", "cause_finder", "forecaster") if a in chosen]
                if order:
                    plan = Plan([Step(a, question + follow + chosen[a] + CONSISTENT) for a in order], "rule")
                else:
                    for s in plan.steps:
                        s.task = (s.task or question) + follow
            if plan.source not in ("direct", "owner") and SPAN_RE.search(question):
                for s in plan.steps:   # a span asked for: its direction said the same way by every analyst
                    if s.agent in ANALYSTS:
                        if CONSISTENT not in (s.task or ""):
                            s.task = (s.task or question) + CONSISTENT
                    elif s.agent == "visualizer" and DRAW_ONLY not in (s.task or ""):
                        s.task = (s.task or question) + DRAW_ONLY
            if self._goods and plan.source not in ("direct", "owner"):
                g = self._goods
                hint = (f" The user's '{g['words']}' means {', '.join('HS ' + c for c in g['codes'])} ({g['goods']})"
                        + (f", not chapter {g['chapter']} ({products.chapter_name(g['chapter'])}), whose official "
                           "title only starts with those words" if g.get("chapter") else "")
                        + ": use that code and name.")
                for s in plan.steps:
                    s.task = (s.task or question) + hint
        log.info("plan (%s): %s", plan.source, [(s.agent, s.task[:80]) for s in plan.steps])
        if has_subject(question):
            self._subject_q = question
        self.remember("user", question)
        self._plan = plan
        return plan

    def follow_up_hint(self, question: str, spec: Optional[dict] = None) -> str:
        """A short question with no subject of its own ('Are there trends', 'why?', 'and imports?') after one that
        had one: keep that subject and work on the chart its answer drew, rather than a new chart about all trade."""
        if not self._subject_q or has_subject(question) or len((question or "").split()) > FOLLOW_UP_WORDS:
            return ""
        if direct_plan(question) or owner_plan(question, spec):
            return ""
        live = {p["id"] for p in (spec or {}).get("panels", [])}
        charts = [p for p in self.last_panels if p in live]
        hint = (f" This is a follow-up to the earlier question \"{self._subject_q}\": keep its subject (the same "
                "partner, goods, flow and span); never answer about all of Luxembourg's trade instead.")
        if charts:
            hint += (f" Work on the chart its answer drew ({', '.join(charts)}): mark it (a trend line, the lasting "
                     "shifts, a note) with target set to it, and draw no new chart unless that one cannot show it.")
        return hint

    def asked_goods(self, question: str, spec: Optional[dict] = None) -> Optional[dict]:
        """Goods with a heading of their own that the question asks for, checked against the data (Toolbox.
        asked_product), unless the question is a keep/remove or about a numbered mark."""
        if not products.specific_in(question) or direct_plan(question) or owner_plan(question, spec):
            return None
        try:
            return self.box("visualizer").asked_product(question)
        except Exception as exc:  # the API is down: the specialists look the goods up themselves
            log.warning("could not look the goods up: %s", exc)
            return None

    def no_trade(self, question: str) -> Reply:
        """The plain answer to a question about goods Luxembourg did not trade, written in code from the data."""
        box = self.box("visualizer")
        box.begin(question)
        text = with_title(box.no_trade_reply(self._goods or {}, question), TITLES["visualizer"])
        return Reply(agent="visualizer", text=text, sources=list(box.turn.sources))

    def direct(self, task: str) -> Reply:
        """'keep:1,3' or 'remove:2': the user's own keep or remove, at once (no LLM)."""
        verb, _, nums = task.partition(":")
        numbers = [int(n) for n in nums.split(",") if n.strip().isdigit()]
        box = self.box("visualizer")
        box.begin()
        out = box.keep_marks(numbers, remove=verb == "remove")
        cite = lambda ns: " ".join(f"[{n}]" for n in ns)  # noqa: E731
        parts = []
        if out.get("kept") or out.get("already_kept"):
            ns = (out.get("kept") or []) + (out.get("already_kept") or [])
            parts.append(f"Kept {cite(ns)}: {'it stays' if len(ns) == 1 else 'they stay'} when the page is cleared.")
        if out.get("removed"):
            parts.append(f"Removed {cite(out['removed'])}; the numbers after it move up.")
        if out.get("unknown"):
            parts.append(f"There is no {cite(out['unknown'])} on the page.")
        if not out.get("ok"):
            parts.append("The page did not take the change: " + "; ".join(map(str, out.get("errors") or []))[:200])
        title = TITLES["visualizer"]
        return Reply(agent="visualizer", text=f"**{title}.** " + (" ".join(parts) or "Nothing to change."),
                     version=out.get("version"), actions=list(box.turn.actions))

    def _answer(self, role: str, question: str, task: str = "", earlier: Optional[list[str]] = None) -> Reply:
        started = time.monotonic()
        if self._plan is not None and self._plan.source == "direct" and ":" in task:
            reply = self.direct(task)
        elif self._plan is not None and self._plan.source == "no_trade" and self._goods:
            reply = self.no_trade(question)
        else:
            asked = list(self.questions) or [e["content"] for e in self.history if e["role"] == "user"] or [question]
            others = [s.agent for s in (self._plan.steps if self._plan else []) if s.agent != role]
            reply = run_specialist(role, question, self.llm, self.box(role), earlier=earlier or [],
                                   history=self._before_turn, task=task or None, max_steps=self.max_steps,
                                   asked=asked, others=others)
            log.info("%s replied in %s steps (marks %s, numbers %s, panels %s, unverified %s)", role, reply.steps,
                     reply.marks, reply.numbers, reply.panels, reply.unverified)
        if not reply.seconds:
            reply.seconds = round(time.monotonic() - started, 1)
        if getattr(reply, "panels", None):
            self.last_panels = list(reply.panels)
        reply.record = self.record(role, question, reply)
        return reply

    def record(self, role: str, question: str, reply: Reply) -> Optional[dict]:
        """The decision record of a reply (record.build), for the page; None if it cannot be built (the reply
        still goes out)."""
        box = self.box(role if reply.agent == role else reply.agent)
        try:
            try:
                catalogue = method_names.fetch(box.api)
            except Exception:  # the names built in
                catalogue = None
            plan = self._plan or Plan([Step(role, question)])
            return decision_record.build(question=question, role=reply.agent, reply=reply, turn=box.turn,
                                         plan_steps=[(s.agent, s.task) for s in plan.steps],
                                         plan_source=plan.source, catalogue=catalogue,
                                         since_question=time.monotonic() - self.asked_at,
                                         names=dict(getattr(box, "_names", {}) or {}))
        except Exception as exc:  # never let the record stop a reply
            log.warning("no decision record for %s: %s", role, exc)
            return None

    def run(self, role: str, question: str, task: str = "", earlier: Optional[list[str]] = None) -> Reply:
        """One specialist, now, in this thread."""
        reply = self._answer(role, question, task, earlier)
        self.remember("assistant", reply.text)
        return reply

    def start(self, question: str, plan: Optional[Plan] = None) -> None:
        """Start the plan's specialists: at once when they do not build on each other, else after the ones they
        build on (DEPENDS_ON), with their answers. ``result(role)`` hands each reply over in the plan's order."""
        plan = plan or self._plan or Plan([])
        self._futures = {}
        for i, step in enumerate(plan.steps):
            before = [s.agent for s in plan.steps[:i]]
            waits = before if not self.parallel else [a for a in before if a in DEPENDS_ON.get(step.agent, set())]
            deps = [self._futures[a] for a in waits]
            self._futures[step.agent] = self._pool.submit(self._after, deps, step.agent, question, step.task)

    def _after(self, deps: list[Future], role: str, question: str, task: str) -> Reply:
        earlier = []
        for f in deps:
            try:
                earlier.append(f.result().text)
            except Exception:  # the one it builds on failed: answer without it
                pass
        return self._answer(role, question, task, earlier)

    def result(self, role: str, question: str = "", task: str = "", earlier: Optional[list[str]] = None) -> Reply:
        """The reply of a specialist started by ``start`` (waiting for it), or, if none was started, run it now."""
        fut = self._futures.pop(role, None)
        reply = fut.result() if fut is not None else self._answer(role, question, task, earlier)
        self.remember("assistant", reply.text)
        return reply

    def answer(self, question: str) -> Iterator[Reply]:
        """The replies to a question, one per specialist in the plan's order, each after its batch is on the page.
        Specialists that do not build on each other work at the same time."""
        plan = self.plan(question)
        self.start(question, plan)
        for step in plan.steps:
            yield self.result(step.agent, question, step.task)
