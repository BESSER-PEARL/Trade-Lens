# besser_sdmx

Our code around [BESSER](https://github.com/BESSER-PEARL/BESSER): from Eurostat's SDMX description
of the data to a BESSER class diagram, and from Eurostat's API to the database of the app BESSER
generates.

```
SDMX API ──► importer/ ──► BESSER class diagram (model/lux_trade_model.py) ──┐
                                                                             ├─► BESSER WebAppGenerator ─► app/
                           BESSER GUI model (model/lux_trade_gui.py) ────────┘   + generators/insights ─► /insights API
SDMX API ──► loader/ ──► the database BESSER generated from the class diagram
```

| Part | What it does | Upstream candidate |
| --- | --- | --- |
| [`importer/`](importer) | Reads an SDMX dataflow's structure live ([`client.py`](importer/client.py), [`live.py`](importer/live.py), [`structure.py`](importer/structure.py)), detects each dimension's role ([`roles.py`](importer/roles.py)) and builds the B-UML class diagram plus the import mapping ([`sdmx_to_buml.py`](importer/sdmx_to_buml.py)) | `besser/utilities/sdmx_to_buml.py` |
| [`loader/`](loader) | Downloads data live from the SDMX API into the database BESSER generated (through the `sql_alchemy.py` BESSER generated, so tables always match the model); records loaded slices | `besser/utilities/sdmx_loader.py`, or an example |
| [`generators/insights/`](generators/insights) | `InsightsGenerator`: a new BESSER generator that adds the `/insights` analytics API to a generated backend | `besser/generators/stats_api/` |
| [`generators/forecast_nn/`](generators/forecast_nn) | `TimeSeriesForecasterGenerator`: a B-UML neural network model (BESSER's NN metamodel) becomes a runnable PyTorch time-series forecaster (windowing, transforms, seeded training, predict, backtest, CLI); used for the `nn` forecast method ([docs/forecasting_nn.md](../docs/forecasting_nn.md)) | `besser/generators/nn/forecaster/` |
| [`patches.py`](patches.py) | Fixes stock BESSER 8.1.0 output (self-associations); a no-op with our patched BESSER | — |

The app itself (`../app`) is produced by BESSER's existing `WebAppGenerator` from the class diagram
and the GUI model.

Command line (`python -m besser_sdmx …`): `import` (SDMX structure → class diagram) and `load`
(SDMX data → database).
