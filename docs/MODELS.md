# The models in Trade-Lens

Trade-Lens uses three kinds of models, and keeps them apart on purpose:

1. **A language model** that understands the question, chooses which agents and which methods to use, and
   words the answer. It does not calculate figures.
2. **Statistical and machine-learning methods** that calculate every figure on the page, the same way every
   time.
3. **BESSER models**: descriptions of the system (data, page, neural networks, readers) from which code was
   generated or checked.

File names are relative to the root of this folder.

---

## 1. The language model

### Which model

| Role | Default model | Where it is set |
|---|---|---|
| The four specialist agents (and a "general" agent for summaries such as "three key messages") | OpenAI **gpt-6-luna** | `DEFAULT_MODEL` in `app/agents/lux_insights/settings.py`; `llm_name` in `app/agents/lux_insights/config.yaml`; override with `LUX_AGENT_MODEL` |
| The router's plan only (which agents answer, with which task) | OpenAI **gpt-4o-mini** | `DEFAULT_ROUTER_MODEL` in `settings.py`; override with `LUX_ROUTER_MODEL` |

The calls go through the BESSER Agentic Framework's OpenAI client (`baf.nlp.llm.llm_openai_api.LLMOpenAI`,
`besser-agentic-framework` 4.5.2 with `openai` 1.93.0, see `requirements-agent.txt`). The specialists run at
temperature 0.2 with up to 900 output tokens per step, the router at temperature 0 with up to 300; for gpt-6
models the agent sends `reasoning_effort: "none"`, which those models need to accept function tools
(`api_params` in `settings.py`).

**Other providers.** Because the client speaks the OpenAI-compatible protocol, other providers work with one
line in `.env` (the `PROVIDERS` table in `settings.py`):

| Setting | Model | Where it runs |
|---|---|---|
| `LUX_LLM=mistral-local` | `mistral-small` through Ollama (`http://localhost:11434/v1`) | On your machine; no question leaves it |
| `LUX_LLM=mistral` and `MISTRAL_API_KEY` | `mistral-small-latest` (`https://api.mistral.ai/v1`) | Mistral's API |
| `LUX_LLM_BASE_URL` and `LUX_AGENT_MODEL` | any model that supports tool calls | Any OpenAI-compatible server (Ollama, LM Studio, vLLM) |

With another provider, the router uses the same model as the specialists unless `LUX_ROUTER_MODEL` says
otherwise. `tests/test_llm_providers.py` tests this configuration. The live site runs the agents on OpenAI.

### What the language model does

- **Routing.** The router (`app/agents/lux_insights/router.py`) asks the small model for a plan: one to three
  specialists, each with a task. A question that cites a mark ("[2]") goes to the agent that drew it. Some
  questions are planned in code (`router.rule_plan`): "Show me 2020" goes to the visualizer, a question about
  forecasting methods always reaches the forecaster. If the model gives no plan, a keyword table
  (`router.KEYWORDS`) picks the specialists. A reader's profile caps how many specialists answer.
- **Choosing methods and charts.** Each specialist (`specialists.py`) runs a tool loop and sees only its own
  tools (`ROLE_TOOLS` in `tools.py`): read tools that call the API's fixed methods, and act tools that draw
  (marks, panels, forecast bands, events). The model decides which tool to call with which parameters, for
  example which partner, which years, which chart type.
- **Wording.** It writes the reply, chart titles and notes, in the reader's register (the "Who is asking"
  section of its prompt comes from the reader's profile).

### What the language model does not do

- **It does not calculate or type figures on the page.** Charts and notes carry API queries and `{fact}`
  placeholders, never numbers; the page fills each figure from the API when it draws. The backend's validator
  (`app/backend/view/validate.py`) refuses a batch whose text, note or title holds a typed amount or
  percentage (error code `figure_in_text`), and pre-flights every new query against the API before accepting it.
- **Its chat replies are checked.** Every figure written in a reply is compared with the figures the tools
  returned in that turn (`unverified_figures` and `unverified_in` in `specialists.py`). A reply with a figure
  no tool returned is sent back to the model once, to be rewritten with the tools' figures or without them.
  The same applies to ranks ("fourth"). The decision record under each reply (`record.py`, shown as "What
  happened") states how many figures were checked, how many were sent back and rewritten, and how many, if
  any, remained unmatched. Note that the chat check is a rewrite request and a disclosure, not a hard block;
  the hard block is on the page.
- **Further checks in code**: causal wording about events is refused (the cause finder may only say that
  dates line up), an event about one product line cannot be given the change of all trade, a figure's verb
  must agree with its sign ("fell by +8.6%" is refused), citations of marks that are not on the page are
  removed, and short-answer readers are cut to their maximum number of sentences.
- **It sees no personal data.** The request sent to the provider holds the question and a summary of the
  page; the reader is a role picked on the landing page, nothing about the person.

---

## 2. Statistical and machine-learning methods

All run in the backend (`app/backend/`), on the database, and are deterministic: the same data and the same
query give the same figure. The page's "How this was made" names the method of every chart; the method
catalogue is served at `GET /insights/methods`.

### Rankings and the significance ladder

`/insights/ranking`, `/insights/ladder` (`app/backend/routers/insights.py`, generated by our
`InsightsGenerator` and extended by hand). Partners or products are ranked by value; only those above a share
of the total are listed, the rest go into "Other". The cutoff drops one decimal per level of product detail
(HS2 at least 1%, HS4 0.1%, HS6 0.01%, CN8 0.001%), so each level keeps about 90% of the value in view.

### Change between two periods: movers and waterfalls

`/insights/movers` returns the largest increases and decreases between two periods, plus both periods' totals.
A waterfall starts at the earlier total, steps through the largest movers, adds one step for everything else
(the remainder, so the bars add up) and ends at the later total. Contributions are differences in euros.

### Growth over a span: compound annual growth

`/insights/trends` ranks partners or products by compound annual growth between a start and an end year:
`(end value / start value) ^ (1 / years) - 1`. Items with no value at the start have no rate and are listed
separately as new. Only items above the significance cutoff at the start or the end are ranked.

### Trend of a series

`/insights/trend`. For monthly data, the trend is the STL trend component (robust STL, period 12), and its
slope is a log-linear least-squares fit of that trend. For annual data, a least-squares fit on the logs
(`ols_log`), so the slope is a growth rate; a linear fit (`ols`) for series with zeros or negative values.

### Seasonal adjustment

`/insights/series?adjust=true` and the Trends page (`app/backend/trends/analysis.py`, `stl_on_history`).
Robust STL (statsmodels) with period 12 (monthly) or 4 (quarterly), **fitted once on the whole history of the
series in the database** (from 1999) and then cut to the period shown, so the first month shown is not an edge
of the fit. The adjusted value is the recorded value minus the seasonal component. A series with less than
five full cycles (60 months, 20 quarters) is refused with a message ("Too short to adjust") rather than
adjusted (`MIN_CYCLES` in `analysis.py`, `ADJUST_MIN_MONTHS` in `routers/insights.py`).

### Change points (lasting shifts in level)

`/trends/analyze` (`detect_changepoints` in `app/backend/trends/analysis.py`). The `ruptures` library's PELT
search with an RBF cost, on the standardised series, with a penalty of `penalty * log(n)` (default penalty
3.0, shortest regime 6 points). The trend finder uses penalty 1.5 and regimes of at least 12 months, and the
Trends page opens with the same settings. The method finds where a series changed, not why. The response also
gives the direction, the shape, trend and seasonal strength, and the typical seasonal pattern.

### Quantities and unit values

`measure=weight|units|all` on `/insights/series`, `/insights/ranking`, `/insights/movers` and
`/insights/compare` (contract 1.8). The dataset's three indicators are read as they are: `VALUE_IN_EUROS`,
`QUANTITY_IN_100KG` (converted to tonnes, x 0.1) and `SUPPLEMENTARY_QUANTITY` (a CN8 line's own unit; the
unit's name comes from Eurostat's Comext classification files, kept in `app/backend/suppl_units.json` by
`scripts/suppl_units.py`). A heading is counted in units only when its CN8 lines in one unit hold at least 99.5%
of its value; chapters and the total have a weight only. Unit values (euros per tonne, euros per unit) are value
divided by quantity, averages over models and grades, not prices. A quantity that is missing or zero is
reported as not published, never as 0.

### Specialisation (revealed comparative advantage)

`/insights/specialisation`: for each product, Luxembourg's share of that product in its total divided by the
EU peer group's share. Above 1, Luxembourg trades relatively more of that product than its peers.

### Cause finder: events

The cause finder does not use a statistical model. It places dated events from a curated list
(`app/backend/events.json`, 22 events, each with an official source; described in [events.md](events.md)),
served at `/insights/events`, on the series. For an event about one line (for example the US steel tariffs:
HS 72, 73 and 76 exported to the US) it computes that line's own change. It reports that dates line up, which
is **alignment, not causation**, and the agent's wording is checked in code so that it does not claim a cause.

### Forecasting

`/insights/forecast` (`app/backend/forecasting/`). Full write-up: [forecasting.md](forecasting.md).

- **Default: the average of two models** (`ENSEMBLE = ("ets", "gbm")` in `models.py`):
  - **Holt-Winters exponential smoothing** (statsmodels `ExponentialSmoothing`): damped additive trend and
    additive season, fitted on the logs of the series (so the season is multiplicative in euros), on the full
    history since 1999.
  - **Gradient boosting** (scikit-learn `HistGradientBoostingRegressor`), trained on all monthly HS2 chapter
    series at once (pooled). It learns the year-on-year growth of the target month from scale-free features
    (recent growth, 3-, 6- and 12-month average growth, the calendar month, the horizon, working days), so its
    forecast is the seasonal naive forecast plus a learned correction. One model covers horizons 1 to 12; it is
    retrained once a year, on the data up to each December.
- **Other methods** on request (`method=`): `ets`, `theta`, `airline` (SARIMA(0,1,1)(0,1,1)12), `gbm`,
  `stl_ets`, `snaive` (same month a year earlier), and `nn` (the BESSER neural network, below) when PyTorch is
  installed. Without scikit-learn the service falls back to Holt-Winters alone and says so in the label.
- **COVID months** (2020-03 to 2020-06) are replaced, for training only, by the same months a year earlier
  grown at the pace of the three months before.
- **80% interval, conformal**: the band is where 80% of the method's own backtest errors on this series fell
  at that horizon (finite-sample quantile; the band never narrows further ahead). No assumption about the
  error distribution.
- **Backtests against the seasonal naive benchmark**: rolling origin at quarter ends; every method is fitted
  only on the data up to each origin and scored 1 to 12 months ahead, with MASE (1.0 = as good as "same month
  last year"). The choice of the default came from 66 origins (December 2009 to March 2026) on the totals of
  exports and imports: ensemble MASE 0.67 at 1 month and 0.93 at 12 months, against 1.03 and 0.97 for the
  seasonal naive forecast. Each forecast response also re-runs the backtest on that series over its last 20
  quarter ends and returns a summary sentence and the skill at 1, 3, 6 and 12 months, which the forecaster
  quotes and the Model card shows.
- **Scenarios**: for total exports or chapter 85 against the world, two scenarios for the smartphone line
  (CN 8517 13 00) that appeared in September 2025: it stops, or it continues at its last three months' level.
- **Honest limits**: twelve months ahead, no method is much better than the seasonal naive forecast; breaks
  the past does not show (a new tariff, a lost customer) are not in the band.

### Is there a neural network?

Yes, as an option, not the default: the `nn` method, described with BESSER's neural-network metamodel (section
3.3). In the same backtest the pooled MLP scores MASE 0.80 at 1 month and 1.02 at 12 months, better than the
seasonal naive forecast up to six months ahead but not at twelve, and not better than the default ensemble
([forecasting_nn.md](forecasting_nn.md)). PyTorch is not in `requirements.txt`, so the method is offered only
when torch is installed.

---

## 3. The BESSER models

[BESSER](https://github.com/BESSER-PEARL/BESSER) v8.1.0 with the seven patches in `patches/besser/`. Each
model below is a B-UML model (BESSER's modelling language, written as Python). More detail:
[BESSER_model.md](../BESSER_model.md); what is BESSER's and what is ours: [architecture.md](architecture.md).

### 3.1 The domain model: a class diagram generated from Eurostat's description

- **File**: `model/lux_trade_model.py` (header: "Generated by BESSER 8.1.0"), with its mapping back to the SDMX
  dimensions in `model/lux_trade_mapping.json`; diagram in `docs/img/lux_trade_model.svg`.
- **How it was made**: our SDMX importer (`besser_sdmx/importer/`) reads the structure of Eurostat dataflow
  DS-045409 from the SDMX 2.1 API and builds the class diagram:
  `python -m besser_sdmx import eurostat-comext DS-045409 --name LuxTrade --class-name CXT_FREE_ISO=Country`.
  `Observation` has one attribute per measure and one reference per dimension; the shared codelists become
  classes (`Country`, `Product`, with the HS2 to CN8 hierarchy as a parent/children self-association); small
  codelists (frequency, flow, indicators) become enumerations. Each class keeps its SDMX role (geography,
  hierarchy, unit, frequency) and its SDMX URI in its metadata. The same importer works on STATEC's LUSTAT.
- **What it produced**: the database schema (SQLAlchemy), the Pydantic classes and the CRUD API through BESSER's
  `WebAppGenerator` (`generate.py`), and the analytics API through our `InsightsGenerator`
  (`besser_sdmx/generators/insights/`). The loader (`besser_sdmx/loader/`) fills the generated database through
  the generated ORM, so the tables match the model.

### 3.2 The GUI model and the generated app

- **File**: `model/lux_trade_gui.py` (BESSER GUI metamodel: screens, metric cards, line and bar charts, data
  bindings, styling). Charts read from the analytics API through `DataBinding.endpoint`, an extension we added
  to BESSER (patch 0005).
- **What it produced**: with the class diagram, the first version of `app/`. At commit `cea06c4` of the
  project's repository, the generated code was 4,532 lines of TypeScript in `app/frontend` and 2,651 lines of
  Python in `app/backend` (counted from that commit). Since then the app has been extended by hand (today about
  22,800 lines of TypeScript, 8,900 of Python in the backend and 8,300 in `app/agents`), so `generate.py` now
  writes into `build/generated/` and refuses to overwrite `app/`.

### 3.3 The neural-network models

- **File**: `model/forecast_nn.py`, using BESSER's NN metamodel (`besser.BUML.metamodel.nn`). Two networks
  that forecast the next 12 months from the last 24 (on year-on-year log growth):
  - `mlp`: Linear(24 lags + 2 calendar features to 32, ReLU), Dropout(0.1), Linear(32 to 12);
  - `gru`: GRU(3 channels per month to 16, last step), Linear(16 to 12).
- **Generator**: our `TimeSeriesForecasterGenerator` (`besser_sdmx/generators/forecast_nn/`) reuses BESSER's
  PyTorch templates for the network and generates the time-series pipeline around it (windowing, transforms,
  seeded training, prediction, backtest). Patch 0007 fixes three bugs found in BESSER's NN generator.
- **Generated code**: `app/backend/forecasting/generated/forecaster_mlp.py` and `forecaster_gru.py`; the adapter
  `app/backend/forecasting/nn.py` serves them as `method=nn` (`FORECAST_NN_NETWORK` picks `mlp`, the default,
  or `gru`). `python model/forecast_nn.py --install` regenerates them; `python scripts/backtest_nn.py`
  reproduces the backtest.

### 3.4 The readers: BESSER user models

- **File**: `model/personas_user_model.py`. Each of the six readers is an object model of BESSER's User
  Modeling Language (the reference model the BESSER web editor ships for user diagrams: age, languages,
  skills, preferences, goals).
- **Pipeline**, for each reader: the object model is built and validated (`ObjectModel.validate`: links,
  multiplicities, enumeration literals) and the reference model's four OCL invariants are checked (evaluated in
  Python in this file); it is rendered with BESSER's `JSONObjectGenerator`, normalised into BESSER's
  user-profile document, and run through BESSER's rule-based recommender
  (`build_manual_mapping_recommendation`). An adapter then derives what a user model can decide (statistical
  literacy, whether to show codes, depth, text size) and takes the rest (default panels, suggested questions)
  from a hand-written role template.
- **How the app uses it**: the page and the agents read the profiles in `contract/personas/persona_profiles.json`
  (the role templates; the page reads an identical copy in `app/frontend/src/view/personas/`). The user-model
  pipeline is run by the tests (`tests/test_personas.py`) to check that the profiles it derives agree with
  those files. These are proto-personas based on published user research, still to be validated with STATEC
  users.

### 3.5 The agents: built on the BESSER Agentic Framework

- **Files**: `app/agents/lux_insights/` (see its README).
- The agent is a BAF agent (`baf.core.agent.Agent`): a router state and one state per specialist, with BAF
  transitions between them, BAF's LLM client, and BAF's WebSocket platform carrying the chat to the page. The
  states and transitions are **written in Python against BAF's API** (`lux_insights_agent.py`), not generated
  from a B-UML agent model.

### 3.6 All the models in one BESSER project

`model/build_project.py` bundles the class diagram, the GUI model, the two networks and the six readers into
one BESSER `Project` and writes `model/lux_trade_project.json` (for the web editor's project import) and
`model/lux_trade_project.py` (B-UML code; the readers are only in the JSON file because BESSER 8.1.0's code
builder writes at most one user object model). Open it at https://editor.besser-pearl.org.

### 3.7 What we changed in BESSER

Seven patches in `patches/besser/`, applied by the full set-up to a clean BESSER v8.1.0: packaging of the
generator templates, SQLAlchemy self-associations, Java helpers, line breaks in agent chat replies,
`DataBinding.endpoint`, readable chart axes, and the NN generator fixes. They, the SDMX importer,
`InsightsGenerator` and `TimeSeriesForecasterGenerator` are proposed upstream ([upstream.md](upstream.md)).
