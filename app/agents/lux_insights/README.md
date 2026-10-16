# LuxInsights: the agent

A BESSER Agentic Framework (BAF) agent that answers questions about Luxembourg's trade in goods
in the page's "Ask the data" panel and draws the answer on the page. It speaks for four
specialists, each with its own pen: the trend finder, the cause finder, the forecaster and the
visualizer.

## Setup (once)

```bash
./scripts/setup_agent.sh            # Windows: powershell -File scripts\setup_agent.ps1
```

This creates **`.venv-agent`** (BAF pins versions the app does not use, so it has its own virtual
environment) from `requirements-agent.txt`: `besser-agentic-framework` 4.5.2 and `openai` 1.93.0.
Then put your key in the gitignored **`.env`** at the repository root (see `.env.example`):

```
OPENAI_API_KEY=sk-...
```

The key is read from the environment or `.env` at start. It is never printed, logged or written to
`config.yaml`. Optional settings: `LUX_AGENT_MODEL` (the specialists, default gpt-6-luna), `LUX_ROUTER_MODEL` (the
router's plan only, default gpt-4o-mini), `LUX_AGENT_PORT` (8766), `LUX_API_URL` (http://localhost:8000).

## Mistral, or a local model, instead of OpenAI

The agent talks to its model through BAF's OpenAI-compatible client, so any provider with an
OpenAI-compatible endpoint works. Set one line in `.env` (see `.env.example`):

| Setting | Model | Where it runs |
|---|---|---|
| (nothing, with `OPENAI_API_KEY`) | gpt-6-luna; the router's plan on gpt-4o-mini | OpenAI |
| `LUX_LLM=mistral-local` | `mistral-small` through [Ollama](https://ollama.com) (`ollama pull mistral-small`) | this machine: no question leaves it |
| `LUX_LLM=mistral` + `MISTRAL_API_KEY` | `mistral-small-latest` | Mistral's API (EU) |
| `LUX_LLM_BASE_URL=...` + `LUX_AGENT_MODEL=...` (+ `LUX_LLM_API_KEY` if needed) | any model | any OpenAI-compatible server: Ollama, LM Studio, vLLM |

With another provider the router plans with the same model unless `LUX_ROUTER_MODEL` says otherwise.
`python app/agents/lux_insights/lux_insights_agent.py --check` prints the provider and models in use.
Choose a model that supports tool calls (in Ollama: `mistral-small`, `mistral-nemo`); on a laptop
CPU expect answers to take much longer than with a hosted model. The figures stay safe whatever the
model: the page and the agent's checks refuse any number that did not come from the API.

## Run

```bash
python run.py                       # backend, page and the agent (when .venv-agent and a key exist)
python run.py --agent real          # insist on the agent: stop with the setup steps if it cannot run
```

Without `.venv-agent` or a key, `python run.py` starts the page without the agent and prints what to set up.

`run.py` starts the agent with `.venv-agent`'s Python on ws://localhost:8766 and points the page at
it. If another program holds 8766, it takes the next free port and says so.

On its own:

```bash
.venv-agent/Scripts/python app/agents/lux_insights/lux_insights_agent.py --api http://localhost:8000 --port 8766
.venv-agent/Scripts/python app/agents/lux_insights/lux_insights_agent.py --ask "Why did exports jump in 2025?"
.venv-agent/Scripts/python app/agents/lux_insights/lux_insights_agent.py --check     # no key, no backend needed
```

(`.venv-agent/bin/python` on macOS and Linux.)

## How it works

- **Router** (`router.py`): a small fast model (gpt-4o-mini) plans one to three specialists for a question,
  each with a task. A question that cites a mark (`[2]`) goes to the agent who drew it. If the LLM gives no plan,
  a small keyword table (`router.KEYWORDS`) picks the specialists.
- **At once** (`conversation.py`): specialists that do not build on each other work at the same time (the
  forecaster never waits); the cause finder waits for the trend finder's breaks and the visualizer for its
  thread (`DEPENDS_ON`), and reads their answers. BAF transitions still post the replies in the plan's order.
- **Specialists** (`specialists.py`): each sees only its own tools (`tools.py`, `ROLE_TOOLS`) and
  posts under its own name, so its marks come out in its pen. Read tools wrap `/insights` and Ivan's
  `/trends` and return figures already written out. Act tools build ops with `agents/kit`
  (marks, panels of every type, comparisons, forecast bands with scenarios, the page's year).
- **Checks**: each batch is validated locally with the backend's own validator, every `{fact}` is
  resolved against the API before sending, 409s are rebuilt and retried, 422s go back to the model.
  A reply that types a figure no tool returned in that turn is rewritten once; citations of numbers
  that are not on the page are removed. The same goes for tool talk (placeholders, errors), claims of marks
  not drawn in the turn, a line's share given a whole flow as base ("87% of imports from China"), and events
  said to line up with trend breaks more than 12 months away: asked to rewrite once, then fixed in code. The
  follow-up chip never repeats a question already asked; "Clear the page" is answered from what stayed.
- **Planned in code** (`router.rule_plan`): "Show me YEAR" (the visualizer moves the page, `clear_page` is
  refused unless the message asks to clear or reset), "bought vs sold" (a butterfly); a question about the
  forecasting methods always reaches the forecaster, whose `get_backtest` gives every method's skill at 1, 3,
  6 and 12 months (the ensemble default, the BESSER neural network, the seasonal naive benchmark).
- **Periods**: the data's coverage comes from `/insights/meta` (1999 to the last full year). A long-run view
  starts in 1999 unless the user asks for another period ("since 2010", "2015 = 100"), which the tools read
  from the question and keep; only HS6/CN8 lines start in 2015.
- **Events about one line** (US steel tariffs: HS 72, 73, 76 exported to the US; sanctions on Russia; the
  EU-UK agreement): `get_events` and `add_events` compute that line's own change from the event's scope in
  `/insights/events`, refuse `{exp_change}` on such an event, and the reply is checked for a total quoted as
  the event's figure.
- **Who is asking** (contract 1.4): when the page is drawn for a persona (`spec.context.persona`), the
  page summary names the reader, each specialist's prompt ends with a "Who is asking" section from the
  profile (`contract/personas/`: depth and sentences, codes or plain names, sources, vocabulary, charts
  to prefer and avoid; `agents.kit.personas.who_is_asking`), and the router keeps at most the profile's
  `max_per_question` specialists, in code. For a short-answer reader (the journalist, the citizen) the reply
  is held to `answer.max_sentences` in code: the model is asked once to shorten, then it is cut; codes go
  when the profile hides them. The rules on figures do not change with the reader.
- **Protocol**: the stock BESSER WebSocket. Replies are `agent_reply_str` markdown starting with
  `**<Title>.**`; before each specialist a `{"type": "mode_change", ...}` message lights up its card,
  and `{"type": "mode_change", "mode": "idle"}` ends the turn. The page turns `[n]` into numbered
  circles and a last line `_Next: ...?_` into a follow-up chip.
- **Decision record** (`record.py`): each specialist's reply message carries, as an extra field of the same
  `agent_reply_str` message, `"what_happened"`: the question, a one-sentence summary, the plan (who answers and
  why, in plain words), the calculations run (each by its plain name from the method catalogue, then the exact
  query), what was drawn (charts and numbered marks, with their titles and methods), the figures checked
  against the calculations (verified, sent back and rewritten, unmatched, filled in on the page) and the time
  taken; the exact queries and the tools last, under `for_statisticians`. The `message` text is unchanged, so a
  page that does not read the field shows the reply as before. `--ask ... --record` prints it.
- **Methods by name** (`methods.py`): the method catalogue comes from the backend (`GET /insights/methods`,
  the same names as the page's "How this was made" card); replies name methods in plain words ("change-point
  analysis of the monthly series"), never by a technical name or an API path.
- **An emptied page**: the user can remove every chart. The page summary says which page panels are gone;
  a thread on a missing crossroads goes on a map of the routes drawn for it, a band, events or a trend line
  on a line since 1999 drawn for them, a headline back at the top; "Show me YEAR" draws the year's partners
  as a butterfly when the crossroads is gone.

## Tests

`tests/test_lux_insights_agent.py` runs without network or LLM, from recorded API answers in
`testdata/insights.json`. Re-record against a running backend with:

```bash
python tests/test_lux_insights_agent.py --record http://localhost:8000
```
