# SPDX-License-Identifier: MIT
"""LuxInsights: a multi-state BAF agent for Luxembourg trade analytics that draws on the page.

State machine (Marcos's design, now with pens):
    router        (initial) - plans who answers: one to three specialists, in order, each with a task
    trend_finder  - what changed, drilled down to the CN8 line; lasting shifts (Ivan's change points)
    cause_finder  - lines changes up with dated events; waterfalls of one year's change
    forecaster    - a forecast band with its backtest, said out loud
    visualizer    - any chart (maps, routes, treemap, heatmap, ...), another year, clearing the page
    general       - greetings and help

Each specialist reads numbers from /insights and /trends, draws marks and panels in its own pen
through POST /view/ops (typed tools on top of agents/kit, checked with the backend's validator), and
replies with one short markdown message that starts with its title and cites the numbers it drew
("[2]"). After a specialist, the next one in the plan runs (trend finder, then cause finder, then
forecaster for "why ... and what next"); after the last one, the agent waits for the next message.

Before each specialist the agent sends {"type": "mode_change", "mode", "agent", "label", "color",
"state": "working"} so the page can light up that agent's card, and {"type": "mode_change",
"mode": "idle"} when the turn is over. Replies are BAF's agent_reply_str; each specialist's reply message also
carries its decision record as an extra field, {"action": "agent_reply_str", "message": "<the reply, unchanged>",
"agent": "<role>", "what_happened": {...}} (record.py: the plan, the calculations run by their plain names, what
was drawn, the figures checked, the time taken). A page that does not read the field shows the reply as before.

Run (with .venv-agent, see scripts/setup_agent.sh or .ps1):
    .venv-agent/Scripts/python app/agents/lux_insights/lux_insights_agent.py --api http://localhost:8000 --port 8766
    .venv-agent/Scripts/python app/agents/lux_insights/lux_insights_agent.py --ask "Why did exports jump in 2025?"

Needs the backend (python run.py) and OPENAI_API_KEY, in the environment or in the repository's
gitignored .env. The key is never printed, logged or written into config.yaml.
"""
# No "from __future__ import annotations" here: BAF checks a state body's signature against the real
# Session class, and string annotations fail that check.
import argparse
import json
import logging
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))   # app/agents: the lux_insights package

from lux_insights.settings import CONFIG, Settings, api_params, openai_key, provider  # noqa: E402

log = logging.getLogger("lux_insights")


def make_llm(agent, settings: Settings, name: str = None):
    """BAF's LLMOpenAI with the key from the environment (never from config.yaml). ``name``: another model than
    the specialists' (the router's small fast one)."""
    from baf.nlp.llm.llm_openai_api import LLMOpenAI
    params = api_params(name or settings.model, 0.2, 900)
    other = provider() if settings.base_url else None
    if other:  # Mistral (local with Ollama, or its API) or any OpenAI-compatible server
        params["base_url"] = settings.base_url
        params["api_key"] = other["api_key"]
    else:
        key = openai_key()
        if key:
            params["api_key"] = key
    return LLMOpenAI(agent=agent, name=name or settings.model, parameters=params, num_previous_messages=1)


def make_router_llm(agent, settings: Settings, llm):
    """The router's LLM: a small fast model for the plan (gpt-4o-mini), the specialists' when it is the same."""
    return llm if settings.router_model == settings.model else make_llm(agent, settings, settings.router_model)


def reply_with_record(session, text: str, record: dict = None) -> None:
    """BAF's agent_reply_str with the decision record as an extra field of the same message (``what_happened``).
    The message text is the reply, unchanged; anything unusual (another platform, no connection) falls back to
    the plain reply."""
    platform = getattr(session, "platform", None)
    conns = getattr(platform, "_connections", None)
    if not record or not isinstance(conns, dict) or session.id not in conns:
        session.reply(text)
        return
    try:
        from datetime import datetime
        from baf.core.message import Message, MessageType
        from baf.platforms.payload import Payload, PayloadAction, PayloadEncoder
        message = Message(t=MessageType.STR, content=text, is_user=False, timestamp=datetime.now())
        session.save_message(message)
        payload = Payload(action=PayloadAction.AGENT_REPLY_STR, message=text, timestamp=message.timestamp)
        payload.message = platform._agent.process(session=session, message=payload.message, is_user_message=False)
        body = json.loads(json.dumps(payload, cls=PayloadEncoder))
        body["agent"] = record.get("agent")
        body["what_happened"] = record
        conns[session.id].send(json.dumps(body, ensure_ascii=False))
    except Exception as exc:  # the record is a bonus: the reply always goes out
        log.warning("could not send the decision record (%s); sending the reply alone", exc)
        session.reply(text)


def build_agent(settings: Settings):
    """The BAF agent: a router state and one state per specialist, wired by the session's plan."""
    from baf.core.agent import Agent
    from baf.core.session import Session
    from baf.platforms.websocket import WEBSOCKET_HOST, WEBSOCKET_PORT

    from lux_insights.conversation import Conversation, mode_change
    from lux_insights.router import ROLES

    agent = Agent("LuxInsights")
    if CONFIG.exists():
        agent.load_properties(str(CONFIG))
    agent.set_property(WEBSOCKET_HOST, settings.host)
    agent.set_property(WEBSOCKET_PORT, settings.port)
    agent.use_websocket_platform(use_ui=False)
    llm = make_llm(agent, settings)
    router_llm = make_router_llm(agent, settings, llm)

    conversations: dict[str, Conversation] = {}   # one per page connection (BAF session)

    def conv(session: Session) -> Conversation:
        if session.id not in conversations:
            conversations[session.id] = Conversation(settings.api_url, llm, router_llm=router_llm)
        return conversations[session.id]

    def control(session: Session, payload: dict) -> None:
        session.reply(json.dumps(payload))

    # ---- router: plan who answers
    def router_body(session: Session) -> None:
        question = (getattr(session.event, "message", None) or "").strip()
        session.set("next", None)
        if not question:          # the session just opened: nothing to answer yet
            return
        plan = conv(session).plan(question)
        conv(session).start(question, plan)   # independent specialists start working now, together
        session.set("question", question)
        session.set("plan", [{"agent": s.agent, "task": s.task} for s in plan.steps])
        session.set("earlier", [])
        session.set("next", plan.steps[0].agent if plan.steps else None)

    # ---- one body per specialist
    def specialist_body(role: str):
        def body(session: Session) -> None:
            plan = list(session.get("plan") or [])
            step = plan.pop(0) if plan and plan[0]["agent"] == role else {"agent": role, "task": ""}
            session.set("plan", plan)
            session.set("next", None)
            control(session, mode_change(role))
            earlier = list(session.get("earlier") or [])
            record = None
            try:
                reply = conv(session).result(role, session.get("question") or "", step.get("task", ""), earlier)
                text, record = reply.text, reply.record
            except Exception as exc:  # one failing specialist must not end the turn
                log.exception("%s failed", role)
                text = f"**{role.replace('_', ' ').capitalize()}.** Something went wrong on my side ({type(exc).__name__})."
            if record and record.get("time") is not None:   # the wait the reader had, up to this message
                record["time"]["since_question"] = round(time.monotonic() - conv(session).asked_at, 1)
            reply_with_record(session, text, record)
            session.set("earlier", earlier + [text])
            nxt = plan[0]["agent"] if plan else None
            if nxt is None:
                control(session, mode_change(None))
            session.set("next", nxt)
        body.__name__ = f"{role}_body"
        return body

    router_state = agent.new_state("router", initial=True)
    router_state.set_body(router_body)
    states = {}
    for role in ROLES:
        states[role] = agent.new_state(role)
        states[role].set_body(specialist_body(role))

    def going_to(role: str):
        return lambda session: session.get("next") == role

    # router -> the first specialist of the plan; a specialist -> the next one; then wait for a message
    for role, state in states.items():
        router_state.when_condition(going_to(role)).go_to(state)
        for other, dest in states.items():
            if other != role:
                state.when_condition(going_to(other)).go_to(dest)
        state.when_event().go_to(router_state)
    router_state.when_event().go_to(router_state)
    return agent


def ask(settings: Settings, questions: list[str], show_record: bool = False) -> int:
    """Answer questions in the terminal, with the same router and specialists (no WebSocket)."""
    from baf.core.agent import Agent

    # A Windows console (cp1252) cannot print every sign a reply holds ("−", "→"): never crash on one.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    from lux_insights.conversation import Conversation
    agent = Agent("LuxInsightsAsk")
    llm = make_llm(agent, settings)
    llm.initialize()
    router_llm = make_router_llm(agent, settings, llm)
    if router_llm is not llm:
        router_llm.initialize()
    c = Conversation(settings.api_url, llm, router_llm=router_llm)
    for q in questions:
        print(f"\n>>> {q}", flush=True)
        for reply in c.answer(q):
            print(f"\n[{reply.agent}] marks={reply.numbers} panels={reply.panels} steps={reply.steps}"
                  + (f" unverified={reply.unverified}" if reply.unverified else ""))
            print(reply.text, flush=True)
            if show_record and reply.record:
                print("--- what_happened ---", flush=True)
                print(json.dumps(reply.record, ensure_ascii=False, indent=1), flush=True)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="LuxInsights, the agent (BAF + OpenAI).")
    p.add_argument("--api", help="backend URL (default LUX_API_URL or http://localhost:8000)")
    p.add_argument("--port", type=int, help="WebSocket port (default LUX_AGENT_PORT, config.yaml or 8766)")
    p.add_argument("--model", help="OpenAI model (default LUX_AGENT_MODEL, config.yaml or gpt-6-luna)")
    p.add_argument("--router-model", help="OpenAI model for the router's plan only (default LUX_ROUTER_MODEL "
                                         "or gpt-4o-mini)")
    p.add_argument("--ask", nargs="+", metavar="QUESTION", help="answer these questions in the terminal and exit")
    p.add_argument("--no-register", action="store_true", help="do not register the four agents on the backend")
    p.add_argument("--record", action="store_true", help="with --ask: print each reply's decision record (JSON)")
    p.add_argument("--check", action="store_true", help="build the agent (states, transitions), print them and exit; "
                                                         "needs no key and no backend")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose or not (args.ask or args.check) else logging.WARNING,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings.load(api_url=args.api, port=args.port, model=args.model, router_model=args.router_model)
    if args.check:
        agent = build_agent(settings)
        for state in agent.states:
            dests = [t.dest.name for t in state.transitions]
            print(f"{state.name}{' (initial)' if state.initial else ''} -> {', '.join(dests)}")
        print(f"key: {'set' if settings.has_key else 'missing'}; port {settings.port}; backend {settings.api_url}; "
              f"llm {settings.llm_name}{' at ' + settings.base_url if settings.base_url else ''}; "
              f"model {settings.model}; router {settings.router_model}")
        return 0
    if not settings.has_key:
        print("No model provider: set OPENAI_API_KEY, or LUX_LLM=mistral-local (Ollama) / LUX_LLM=mistral, "
              "in the environment or in the repository's .env (see .env.example).", file=sys.stderr)
        return 2
    if not args.no_register:
        from lux_insights.registry import register_all
        _, warnings = register_all(settings.api_url)
        for w in warnings:
            log.warning(w)
    if args.ask:
        return ask(settings, args.ask, show_record=args.record)
    agent = build_agent(settings)
    log.info("LuxInsights on ws://%s:%s, backend %s, llm %s%s, model %s, router %s", settings.host, settings.port,
             settings.api_url, settings.llm_name, f" at {settings.base_url}" if settings.base_url else "",
             settings.model, settings.router_model)
    agent.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
