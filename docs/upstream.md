# What we propose to merge into BESSER

Everything below was built or fixed during the STATEC Hackathon 2026 and is tested in this repo.
Each item is meant to be its own pull request against `BESSER-PEARL/BESSER` `development`, smallest
and least controversial first. The patches in [`patches/besser/`](../patches/besser) apply in order on
BESSER v8.1.0 (`scripts/setup.*` does it), and each already carries a commit message.

## 1. Fixes: ready as they are

| # | Patch | Problem | Tests |
| --- | --- | --- | --- |
| 1 | [`0001` packaging](../patches/besser/0001-fix-packaging-ship-nested-generator-templates-in-the.patch) | `pip install besser` (8.1.0) ships without the React / web-app templates: `package_data = *.j2` skips nested folders and non-`.j2` files, so `WebAppGenerator` fails for every PyPI user | Verified: a wheel built with the fix contains the 40 React template files (the PyPI 8.1.0 wheel has none) |
| 2 | [`0002` SQLAlchemy self-association](../patches/besser/0002-fix-sqlalchemy-set-remote_side-on-self-associations.patch) | A class associated with itself (`Product.parent` / `children`) generates ORM code that crashes at import (`remote_side` missing) | Covered by our loader tests (hierarchy table) |
| 3 | [`0003` Java shared-type collections](../patches/besser/0003-fix-java-role-based-helpers-when-two-collections-sha.patch) | Two associations to the same class (`reporter` and `partner` → `Country`) generate duplicate `addObservation()` methods: the Java does not compile | `tests/generators/java/test_java_shared_type_collections.py` |
| 4 | [`0004` agent chat line breaks](../patches/besser/0004-fix-react-keep-line-breaks-in-agent-chat-replies.patch) | Multi-line agent replies collapse into one paragraph in the generated React chat | Visual |
| 7 | [`0007` NN: recurrent outputs, training mode, multi-target regression](../patches/besser/0007-fix-nn-single-output-recurrent-layers-training-mode-.patch) | (a) every LSTM/GRU/RNN counts as multi-output, so `validate()` rejects a layer after one with `return_type="last"` or `"full"`, including BESSER's own reference model `tests/BUML/metamodel/nn/lstm.py` (the editor's `lstm_nn.json` template is marked `xfail(strict)` waiting for this); (b) the generated PyTorch `train()`/`evaluate()` never switch `model.train()`/`model.eval()`, so dropout stays on during evaluation; (c) regression labels are always unsqueezed, so a network with several outputs trains on a broadcast loss | `tests/generators/nn/test_nn_rnn_outputs_and_training_mode.py` (8 of its 10 tests fail on 8.1.0); BESSER's NN tests pass with it (66) |

## 2. Features: worth a design discussion with the maintainers

**5. [`0005` `DataBinding.endpoint`](../patches/besser/0005-feat-gui-DataBinding.endpoint.patch)**, could be two PRs:

- **`DataBinding.endpoint`.** A chart or metric card can read from any backend endpoint, not only the
  CRUD list of its class. Without it, a chart on a large table downloads every row (891,000 here) and
  aggregates in the browser. It is carried by `gui_model_to_code` and both web-editor converters. Small and general.
- **Converter fixes found on the way.** Metric-card titles were lost in `buml_to_json`, and chart-level
  bindings were dropped by `json_to_buml`.

An experimental `DataExplorer` component (map, rankings, drill-down) and an RFC on making the
statistics work BESSER-native are kept in the project's Git history; they are not proposed for now.

## 3. New modules: from `besser_sdmx/` in this repo

| # | Module | Where it would live in BESSER | Notes |
| --- | --- | --- | --- |
| 6 | **SDMX importer**: [`structure.py`](../besser_sdmx/importer/structure.py), [`sdmx_to_buml.py`](../besser_sdmx/importer/sdmx_to_buml.py), [`roles.py`](../besser_sdmx/importer/roles.py), [`client.py`](../besser_sdmx/importer/client.py) | `besser/utilities/sdmx_to_buml.py`, next to `kg_to_buml.py` and `image_to_buml.py` | **The most mergeable piece.** SDMX 2.1 dataflows become a validated B-UML class diagram: dimensions become enumerations or classes, nomenclatures a self-association. Tested on Eurostat, Eurostat Comext and STATEC's LUSTAT (.Stat Suite); other SDMX 2.1 providers (ECB, OECD) are expected to work but untested. Roles are written to `Metadata` as text today; generators read them from the import mapping JSON instead. A cleaner version stores them as a profile in the model. Natural web-editor feature: **"Import from SDMX"** next to "Import from image". No new dependency. |
| 7 | **`InsightsGenerator`** ([`generators/insights/`](../besser_sdmx/generators/insights)) | `besser/generators/stats_api/`, registered in `SUPPORTED_GENERATORS` | Analytics endpoints added to a generated FastAPI backend: rankings with significance thresholds, threshold ladders, time series (STL), movers, revealed comparative advantage. Today it is configured from the import mapping. To be a BESSER generator it should read the dimension roles from the model. Needs `statsmodels` only for seasonal adjustment (optional import). |
| 8 | **SDMX loader** ([`loader.py`](../besser_sdmx/loader/loader.py)) | `besser/utilities/sdmx_loader.py`, or kept as an example | Fills the database of any BESSER-generated backend from an SDMX API, using the generated ORM: the schema always matches the model. Splits oversized queries automatically. |
| 9 | **`TimeSeriesForecasterGenerator`** ([`generators/forecast_nn/`](../besser_sdmx/generators/forecast_nn)) | `besser/generators/nn/forecaster/`, next to `PytorchGenerator` | A B-UML `NN` model plus a small `ForecastConfig` (window, horizon, season, log, differencing, calendar features, seeds) becomes a runnable PyTorch forecaster: windowing, transforms and their inverse, seeded deterministic training with a masked loss, one or several series ("global" model), `predict` for any number of steps, `save`/`load`, a rolling-origin `backtest`, MASE, and a CSV command line. It reuses the stock PyTorch templates for the network, so every layer BESSER supports works; it checks that the first layer reads the window and the last returns the horizon. Generic: nothing in it is about trade. Upstream it would also want a web-editor entry (an NN diagram + "Generate forecaster"). See [forecasting_nn.md](forecasting_nn.md). |

## 4. For other BESSER repos

- **BAF (agent framework)**, found while prototyping a data assistant (since removed from this repo):
  - **Config template:** `db.monitoring.enabled` should default to `False` when no database is configured. The generated agent logs a connection error to a placeholder Postgres.
  - **Offline agents:** the generated agent imports `openai` unconditionally, so `[llms]` is required even for offline agents.
- **BESSER skills (`besser-user`, agents reference):** it documents `session.message` and `session.predicted_intent`. In BAF 4.5 these live on `session.event`, and a body must have the signature `(session: Session) -> None`.

## Not for upstream

These stay in this repo: the Luxembourg-specific GUI model ([`model/`](../model)), the data slices
([`model/lux_trade_slices.json`](../model/lux_trade_slices.json)), the findings analysis ([`notebooks/`](../notebooks)) and the generated app ([`app/`](../app)).
