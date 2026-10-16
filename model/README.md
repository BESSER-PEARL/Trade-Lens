# model

The BESSER (B-UML) models of the project. [BESSER_model.md](../BESSER_model.md) explains each one and
how it is used.

- **Class diagram** ([`lux_trade_model.py`](lux_trade_model.py)): countries, product codes and trade
  flows, imported from Eurostat's SDMX metadata; [`lux_trade_mapping.json`](lux_trade_mapping.json)
  maps it back to the SDMX dimensions.
- **GUI model** ([`lux_trade_gui.py`](lux_trade_gui.py)): the first version of the Overview page.
- **Neural-network models** ([`forecast_nn.py`](forecast_nn.py)): the two networks of the `nn` forecast.
- **User models** ([`personas_user_model.py`](personas_user_model.py)): the six readers.
- **Data recipe** ([`lux_trade_slices.json`](lux_trade_slices.json), and the 1999-2014 top-up
  [`lux_trade_slices_history.json`](lux_trade_slices_history.json)): what the loader downloads.

**All of them in one project:** `python build_project.py` writes [`lux_trade_project.json`](lux_trade_project.json)
(every model, the readers as a user diagram) and [`lux_trade_project.py`](lux_trade_project.py) (B-UML), to
import into the [BESSER web editor](https://editor.besser-pearl.org). `app/` was generated from them at commit `cea06c4` and is maintained by hand since,
so `generate.py` now writes to `build/generated/`.
