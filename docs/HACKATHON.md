# STATEC Hackathon 2026: what was asked, and how Trade-Lens answers it

STATEC Hackathon 2026, GovTech Lab Luxembourg, 14 to 16 October 2026. Team BESSER, Luxembourg Institute of
Science and Technology (LIST).

## The challenge, in short

The organisers asked teams to build visualisation methods and tools, static or interactive, that give the most
effective picture of Luxembourg's external trade in goods, using Eurostat's dataset DS-045409 ("EU trade since
1988 by HS2-4-6 and CN8"). The brief listed example questions:

1. Who are Luxembourg's main trading partners, for exports and imports, and how have they changed over time?
2. Which categories of goods does Luxembourg export and import, and how has that changed?
3. For a given category of goods, who are the main partners?
4. For a given partner, which goods are traded (at the 2-digit level), and how can finer detail, down to
   8-digit codes, be shown while highlighting only the significant flows?
5. How does Luxembourg's trade compare with other countries'?
6. Are there seasonal patterns, overall or for some products, and how can the evolution be shown with purely
   seasonal effects filtered out?

Teams were free to add anything relevant. Three considerations were set: the focus is Luxembourg; the result
includes a time dimension (comparing points in time); and it includes a geographical dimension (for example
comparing Luxembourg with other countries).

**Deliverables**: a five-minute pitch (idea, approach, mini demo) and reproducible artefacts (a notebook,
scripts or an app, a requirements file, a licence, a README), shared through a public Git repository or on
request.

**Evaluation criteria**: reproducibility and openness; clarity and quality of the presentation; use of data to
create new insights; innovation and experimentation; and a working proof of concept.

**Licensing terms** (summary): teams keep ownership and give STATEC a free, non-exclusive, perpetual licence to
use and present the project; code carries an open-source licence with an SPDX identifier (EUPL-1.2 preferred,
Apache-2.0 or MIT accepted); no personal or confidential data; open data keeps its licence and is cited in a data card.

## How Trade-Lens answers the questions

| # | The brief asks | How you get it in Trade-Lens |
|---|---|---|
| 1 | Main partners, exports and imports, over time | "Who are Luxembourg's biggest customers?" draws partners on a map or a bought-against-sold butterfly; "How have the partners' ranks changed since 1999?" draws a bump chart of ranks; "Which partners grew fastest since 1999?" draws growth bars (compound growth per year). The full overview has the crossroads: imports by partner, through Luxembourg, to exports by partner |
| 2 | Main goods, over time | "What does Luxembourg sell?" draws a treemap of chapters and headings; heatmaps of products by year; a waterfall of what made up a change between two years |
| 3 | Partners for a given product | Any chart can be set to a product (each chart's Goods menu, or ask: "Who buys Luxembourg's rubber products?") |
| 4 | Goods for a partner, from HS2 down to CN8, only the significant flows | Drill-down from chapter (HS2) to heading (HS4) to the 8-digit line (CN8), in bars or by opening treemap tiles. The **significance ladder** decides what is shown: at least 1% of the total for chapters and partners, one decimal less per level of detail (0.1% for HS4, 0.01% for HS6, 0.001% for CN8), the rest in "Other". Each level keeps about 90% of the value in view, where a single fixed cutoff would keep 90% at HS2 but only about a third at CN8 |
| 5 | Comparison with other countries | Luxembourg against EU countries as reporters, in euros, rebased to 100 or as growth, in total or in each country's own trade with one partner ("exports to the US: is this specific to Luxembourg?"; the US, China, the UK and Switzerland are loaded, others fetched on demand); specialisation (revealed comparative advantage) against the EU peer group; Luxembourg's rank among its peers |
| 6 | Seasonality, and trends without it | A **Seasonally adjusted** switch on every monthly line, using robust STL fitted on the whole monthly history since 1999; seasonal heatmaps of the main chapters; the Trends page with the trend, change points and the typical shape of the year, for any product, partner and reporter |
| | Time dimension | Monthly and annual data from 1999; every chart has its own year or range; "Show me 2020" moves the page; forecasts for the next months |
| | Geographical dimension | Choropleth maps of partners, flow maps of routes through Luxembourg, comparisons with EU countries |
| | Focus on Luxembourg | Luxembourg is the reporter throughout; other countries appear as partners or as peers for comparison |

Beyond the questions, Trade-Lens adds the four agents (visualizer, trend finder, cause finder, forecaster),
the six readers, the event list for the cause finder, and the forecast with its honest backtest. Findings drawn
from the data with these tools are in [findings.md](findings.md); for example, two thirds of the 2025 export
rise is one product line, smartphones, bought mostly from China and sold mostly to France.

## The advantages, point by point

### Trust: no typed numbers, fixed methods, provenance

- The language model never types a figure on the page. Charts and notes carry queries and placeholders, and
  the page fills every figure from the API; the backend refuses text with a typed amount or percentage.
- Figures in chat replies are checked against what the methods returned in that turn; a reply with an
  unmatched figure is sent back to be rewritten, and the record under the reply discloses the result.
- Every figure comes from a fixed, documented method (ranking with a significance cutoff, compound growth,
  robust STL, PELT change points, Holt-Winters plus gradient boosting with conformal bands): the same data
  gives the same figure. See [MODELS.md](MODELS.md).
- "How this was made" under every chart, text and note names the method, the data, who drew it and what the
  assistant did; "For statisticians" gives the exact query to rerun. Forecasts have a Model card with their
  backtest against the seasonal naive benchmark, including where they do not help.
- The cause finder says when dates line up and that this is not proof of cause; causal wording is refused in
  code.

### Access for people who are not statisticians

- Six readers (STATEC analyst, policy adviser, journalist, business owner, researcher, curious citizen) change
  the charts that open first, the suggested questions, the vocabulary, the length of the answers and whether
  codes are shown, never the figures.
- Questions in plain words; suggested questions and follow-up chips; a notebook that keeps each question with
  its charts and the reader's own notes; a PDF export that carries the provenance of every figure.

### Official data only

- Every figure comes from Eurostat's DS-045409, which includes the data STATEC produces for Luxembourg. The
  events placed by the cause finder were written by the team, each with an official source
  ([events.md](events.md)). The data card ([DATA_CARD.md](../DATA_CARD.md)) gives sources, slices,
  transformations and the licence.

### Open source

- Code, models and generated artefacts under the MIT License: the licence text is in `LICENSE`, and the source
  files carry `SPDX-License-Identifier: MIT`.
  Dependencies are open source; BESSER is MIT-licensed and our patches to it are offered upstream.

### Low-code with BESSER: model-driven and regenerable

- The data model is generated from Eurostat's own SDMX description, so a change in the dataset's structure is
  a re-import, not a redesign; the same importer works on STATEC's LUSTAT.
- The first app (database, API, first page) was generated from the models; `python generate.py` still
  regenerates it into `build/generated/`.
- The agents run on BESSER's agent framework; the forecaster's neural networks and the readers are BESSER
  models too. All models open as one project in the BESSER web editor. See [MODELS.md](MODELS.md).

### Runs from this folder, and online

- `python run.py` starts the app; on the first launch it loads the database from Eurostat's API. The full set-up
  reproduces everything from scratch (BESSER, patches, a fresh data load from Eurostat, tests).
- Offline tests (`python -m pytest`) cover the page contract, the methods, the agents (with recorded API
  answers, no language model needed) and the BESSER models.
- `notebooks/findings.ipynb` reproduces the key findings from the running API, with its outputs saved;
  `notebooks/key_findings.py` writes [findings.md](findings.md) from the database.
- Deployed at https://statec.besser-pearl.org ([deploy/README.md](../deploy/README.md)).

### How this maps to the evaluation criteria

| Criterion | What to look at |
|---|---|
| Reproducibility and openness | MIT License, the README's quick start (the database loads on the first launch), the tests, the notebook, "For statisticians" queries, the data card |
| Clarity of the presentation | One page per reader, plain-word questions, and the live site to try during the talk |
| Use of data | The significance ladder, seasonal adjustment on the full history, change points, specialisation, the smartphone finding, the forecast with its backtest |
| Innovation and experimentation | Agents that choose methods but never type numbers; readers as BESSER user models; a forecaster's neural network as a BESSER model; a class diagram generated from SDMX |
| Working proof of concept | The live site and `python run.py` |

## Limits: what Trade-Lens does not do, or does not claim

- **It runs on OpenAI today.** The agents use OpenAI's gpt-6-luna (and gpt-4o-mini for the router's plan).
  Mistral, local through Ollama or through Mistral's API, works with one line of configuration, but it is an
  option, not what runs on the live site. A question is sent to the model provider with a summary
  of the page; nothing about the person.
- **The data is about Luxembourg, not the whole EU dataset.** The database holds about 2.33 million figures:
  about 1.36 million with Luxembourg as reporter (Luxembourg reports on its own from 1999) and the rest for the
  EU countries used in comparisons, including their own trade with four partners. It is loaded as slices of DS-045409, not the whole dataset; missing slices
  are fetched from Eurostat on demand. Not "hundreds of millions of rows".
- **Values are nominal euros.** No price index or deflation and no per-capita measures (the dataset has no
  population). Quantities (weight, and a count for goods with their own unit) sit next to values, but unit
  values are averages over models and grades, not prices, and some quantities are not published. The latest months are provisional and Eurostat revises them; the database is a snapshot of 15
  October 2026.
- **Alignment is not causation.** The cause finder lines dates up with changes; it does not prove why trade
  moved. For example, exports to the US as a whole barely moved in 2025 (-0.6%); the fall is in iron, steel and
  aluminium.
- **Forecasts are uncertain.** Twelve months ahead no method is much better than "same month last year", and a
  break the past does not show (a new tariff, a new product line) is not in the band. The 2025 smartphone jump
  is handled with two explicit scenarios, not predicted.
- **The neural network is optional and not the best method.** It beats the seasonal naive forecast up to six
  months ahead but not the default ensemble, and it needs PyTorch, which is not in `requirements.txt`.
- **Chat replies are checked, not blocked.** On the page a typed figure is refused outright; in the chat a
  reply with an unmatched figure is sent back once and, if a figure still does not match, the record under the
  reply says so.
- **No notebook export for rerunning.** A notebook can be saved as a PDF (with the queries of every figure),
  but not exported as a file to reopen or rerun in the app.
- **Not in the app:** no "explainer" agent beyond the four specialists, and no reader beyond the six listed.
- **One shared page per backend.** The page state (and the chosen reader) lives on the backend, so every
  browser on one backend sees the same page; "Clear my charts" and the overview switch are per browser, but
  Undo and Redo act on the shared page. A multi-user deployment would need a page per session.
- **Readers are proto-personas.** They are based on published user research, not yet validated with STATEC's
  users.
- **The app was not written by hand from scratch, nor generated entirely.** BESSER generated the first version
  (about 4,500 lines of TypeScript and 2,700 of Python at commit `cea06c4`); the agent layer, the charts and the
  forecasting were written on top.

## A real product

Trade-Lens is not a mock-up: it is a working product, deployed at https://statec.besser-pearl.org, where every
question is answered live by the real agents, on Eurostat's official data, with every figure calculated by the
methods described in [MODELS.md](MODELS.md).
