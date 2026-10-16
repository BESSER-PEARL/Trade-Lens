# The BESSER models in this project, and how each one is used

This project uses [BESSER](https://github.com/BESSER-PEARL/BESSER) (v8.1.0, plus the patches in
[`patches/besser/`](patches/besser)) in four ways. In each case a **B-UML model**, a model written in
BESSER's modelling language, is the source, and a generator or a BESSER pipeline turns it into
something the app runs:

| # | Model | BESSER metamodel | Made by | Turned into | Used by |
|---|---|---|---|---|---|
| 1 | [`model/lux_trade_model.py`](model/lux_trade_model.py) | Structural (class diagram) | **Imported** from Eurostat's SDMX metadata by our importer | Database, ORM, CRUD API, analytics API | The backend, the data loader |
| 2 | [`model/lux_trade_gui.py`](model/lux_trade_gui.py) | GUI | Written by hand | The first React page | The frontend's starting point |
| 3 | [`model/forecast_nn.py`](model/forecast_nn.py) | Neural network (NN) | Written by hand | PyTorch forecasters | `/insights/forecast?method=nn` |
| 4 | [`model/personas_user_model.py`](model/personas_user_model.py) | Object models of the User Modeling Language | Written by hand | Persona profiles | The page's "Reading as" and the agents |

A fifth use is the **agent framework**: the agent that answers questions on the page runs on the
BESSER Agentic Framework (BAF). It is written in Python against BAF's API rather than generated from
a model (section 5).

All models validate with BESSER's own checks (`validate()`, OCL invariants).

## All the models in one project, for the web editor

[`model/build_project.py`](model/build_project.py) bundles the models into one BESSER `Project`
("LuxTradeExplorer") and writes it in two forms:

| File | Holds | Open it in the [BESSER web editor](https://editor.besser-pearl.org) |
|---|---|---|
| [`model/lux_trade_project.json`](model/lux_trade_project.json) | **Everything**: the class diagram, the GUI model, the two neural networks, and the six readers as one user diagram (136 objects, 130 links) | with the editor's project import (a project file in the editor's JSON format) |
| [`model/lux_trade_project.py`](model/lux_trade_project.py) | The same as B-UML code, written by BESSER's project code builder, except the readers (see below) | with the editor's B-UML project import |

```
python model/build_project.py    # rebuild both files and check them with the editor's own converters
```

The script checks the JSON the way the editor reads it (its JSON-to-B-UML converter): every model comes
back, the readers with all their objects, links and attribute values, and the user model still
validates (`tests/test_besser_project.py`). Two BESSER 8.1.0 limits explain the two files: its project
code builder writes at most one user object model (so the six readers share one), and its B-UML
importer has no user-diagram type and its object converter drops objects without attributes and our
links; the JSON file therefore builds the user diagram straight from the model. Both are worth fixing
upstream.

```
Eurostat SDMX metadata ──(our importer)──► 1. class diagram ─┐
                                                              ├─► BESSER WebAppGenerator ─► app/ (first version)
                           2. GUI model (by hand) ────────────┘    + our InsightsGenerator ─► /insights API
3. NN models (by hand) ──► our TimeSeriesForecasterGenerator ─► app/backend/forecasting/generated/
4. user models (by hand) ──► BESSER's profile pipeline + recommender ─► contract/personas/ profiles
5. agent code on BAF (app/agents/lux_insights) ─► the agent on ws://localhost:8766
```

---

## 1. The class diagram: the data model, imported from SDMX

**File:** [`model/lux_trade_model.py`](model/lux_trade_model.py) (with its import mapping
[`model/lux_trade_mapping.json`](model/lux_trade_mapping.json)). Diagram:
[`docs/img/lux_trade_model.svg`](docs/img/lux_trade_model.svg).

**What it describes.** The structure of Eurostat dataflow DS-045409 (EU trade by HS2-4-6 and CN8):
an `Observation` class with one attribute per measure and one reference per dimension; the shared
codelists become classes (`Country` for reporter and partner, `Product` for the HS2 → HS4 → HS6 → CN8
nomenclature, as a `parent`/`children` self-association with a `level`); small codelists such as the
flow and the frequency become enumerations. Each class keeps the SDMX dimension's role (geo,
hierarchy, unit, frequency) in its metadata.

**How it is made.** Nobody drew it. Our SDMX importer ([`besser_sdmx/importer/`](besser_sdmx/importer))
reads the dataflow's structure from the SDMX 2.1 API and builds the B-UML class diagram:

```
python -m besser_sdmx import eurostat-comext DS-045409 --out model
```

The same importer works on STATEC's LUSTAT (`python -m besser_sdmx import lustat DSD_INT_TRAD@DF_E4103`):
the model is built in under a second.

**How it is used.**
- BESSER's `WebAppGenerator` generated the database schema (SQLAlchemy), the Pydantic classes, the
  FastAPI CRUD routers and the first frontend from it ([`generate.py`](generate.py)).
- Our `InsightsGenerator` ([`besser_sdmx/generators/insights/`](besser_sdmx/generators/insights)) reads
  the dimension roles from the same model and adds the analytics API (`/insights/...`: rankings with
  the significance ladder, series with seasonal adjustment, movers, specialisation, peers).
- Our loader ([`besser_sdmx/loader/`](besser_sdmx/loader)) fills the generated database through the
  generated `sql_alchemy.py`, so the tables always match the model.

## 2. The GUI model: the first version of the page

**File:** [`model/lux_trade_gui.py`](model/lux_trade_gui.py).

**What it describes.** The Overview screen: metric cards, line and bar charts, their data bindings
and their styling (layout, colours, typography as BESSER `Styling` objects). Each chart is bound to
the `Observation` class and reads from the analytics API through `DataBinding.endpoint`, a BESSER
extension we contributed ([patch 0005](patches/besser/0005-feat-gui-DataBinding.endpoint.patch)).

**How it is used.** With the class diagram, it was the input of `WebAppGenerator`, which generated the
React page at commit `cea06c4`. Since then the frontend has been extended by hand into the
agent-driven page (the crossroads, the "Ask the data" panel, 15 chart types), so `generate.py` now
writes to `build/generated/` and no longer overwrites `app/`. The model stays as the record of the
generated starting point.

## 3. The neural-network models: a forecaster generated from B-UML

**Files:** [`model/forecast_nn.py`](model/forecast_nn.py) (the models),
[`besser_sdmx/generators/forecast_nn/`](besser_sdmx/generators/forecast_nn) (our generator),
[`app/backend/forecasting/generated/`](app/backend/forecasting/generated) (the generated code),
[`app/backend/forecasting/nn.py`](app/backend/forecasting/nn.py) (the adapter). Full write-up:
[`docs/forecasting_nn.md`](docs/forecasting_nn.md).

**What it describes.** Two small networks, written with BESSER's NN metamodel, that forecast the next
12 months of a monthly trade series from its last 24 months (on year-on-year log growth):

- `mlp`: Linear(24 lags + 2 calendar features → 32, ReLU) → Dropout(0.1) → Linear(32 → 12)
- `gru`: GRU(3 channels per month → 16, last step) → Linear(16 → 12)

**How it is turned into code.** BESSER's stock `PytorchGenerator` and `TFGenerator` write the network
classes, but not what a forecaster needs (windowing a time series, transforms, seeded training,
prediction, a backtest). Our `TimeSeriesForecasterGenerator` reuses BESSER's PyTorch templates for the
network and generates that pipeline around it. Patch
[0007](patches/besser/0007-fix-nn-single-output-recurrent-layers-training-mode-.patch) fixes three bugs
found in BESSER's NN generator on the way.

```
python model/forecast_nn.py              # validate, generate into build/nn/
python model/forecast_nn.py --install    # also refresh app/backend/forecasting/generated/
python scripts/backtest_nn.py            # reproduce the backtest table
```

**How it is used.** The backend serves it as `/insights/forecast?method=nn` (when torch is installed).
In the same rolling backtest as the other methods it beats the "same month last year" benchmark up to
six months ahead, but not twelve months ahead (1.02 against 0.96), and not the default ensemble (Holt-Winters + gradient boosting), so it is offered as an option, labelled
honestly, and the forecaster agent shows it in its backtest table.

## 4. The user models: the readers, as BESSER user diagrams

**Files:** [`model/personas_user_model.py`](model/personas_user_model.py) (the models and the pipeline),
[`contract/personas/`](contract/personas) (the profiles, their schema and the page variants; the page
reads an identical copy in `app/frontend/src/view/personas/`).

**What it describes.** Six readers of official trade statistics: a STATEC analyst, a policy adviser,
a journalist, an exporter, a researcher and a curious citizen. Each one is an
object model of BESSER's User Modeling Language, the reference model the web editor uses for user
diagrams (age, languages, skills, preferences, goals).

**How it is turned into profiles.** For each reader the file:

1. builds the object model and validates it (links, multiplicities, enumeration literals) and checks
   the reference model's OCL invariants;
2. renders it with BESSER's `JSONObjectGenerator` and normalises it into BESSER's user-profile
   document;
3. runs BESSER's rule-based recommender on it;
4. derives the persona profile the app uses: what a user model can decide (statistical literacy,
   whether to show codes, depth, text size) comes from the model; what it cannot (the default panels,
   the suggested questions) comes from a hand-written role template.

```
python model/personas_user_model.py              # validate and print one line per reader
python model/personas_user_model.py --out DIR    # also write BESSER's documents
```

**How it is used.** The page's "Reading as" control sets the persona in the page context; the page
folds or adds panels and adapts its text, and every agent receives a "who is asking" section that
sets its depth, vocabulary and length. The numbers never change with the reader, only the words.

## 5. The agent: built on the BESSER Agentic Framework

**Files:** [`app/agents/lux_insights/`](app/agents/lux_insights) (see its README).

The agent is a BAF agent: a router state plans which specialists answer (trend finder, cause finder,
forecaster, visualizer, general), each specialist state runs a tool loop with an OpenAI model
(gpt-6-luna by default, the router planning with gpt-4o-mini; or Mistral, run locally with Ollama or
through Mistral's API, with one line in `.env`: see the agent's README), and BAF's WebSocket platform carries the chat to the
page. Its states and transitions are written in Python against BAF's API
([`lux_insights_agent.py`](app/agents/lux_insights/lux_insights_agent.py)), not generated from an
agent model. The agent never edits the page: it sends typed, validated operations through the
contract in [`docs/agents.md`](docs/agents.md).

## What is BESSER's and what is ours

| | From BESSER | Ours |
|---|---|---|
| Class diagram | Metamodel, validation, `WebAppGenerator` | The SDMX importer that builds the model; `InsightsGenerator`; the loader |
| GUI model | GUI metamodel, React generation | The Overview model; `DataBinding.endpoint` (patch 0005); the hand-built page since `cea06c4` |
| NN models | NN metamodel, `PytorchGenerator` / `TFGenerator` | The two networks; `TimeSeriesForecasterGenerator`; patch 0007 |
| User models | User Modeling Language, `JSONObjectGenerator`, profile normaliser, recommender | The six readers; the adapter to persona profiles |
| Agent | BAF: states, transitions, LLM wrapper, WebSocket platform | The router, the specialists, their tools and the page contract |

The seven patches in [`patches/besser/`](patches/besser) (packaging, SQLAlchemy self-associations, Java
helpers, chat line breaks, `DataBinding.endpoint`, chart axes, the NN generator) and the new modules
(the SDMX importer, `InsightsGenerator`, `TimeSeriesForecasterGenerator`) are proposed upstream; see
[`docs/upstream.md`](docs/upstream.md).
