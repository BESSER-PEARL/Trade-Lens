# data

Local data directory, gitignored. **`lux_trade.db` is built on the first launch**: `python run.py` loads it from
Eurostat's API (no account or key), then reuses it. About 510 MB, 2.3 million observations (Luxembourg as
reporter, plus the EU countries used in comparisons). It holds three measures: `VALUE_IN_EUROS`,
`QUANTITY_IN_100KG` (weight, shown in tonnes) and `SUPPLEMENTARY_QUANTITY` (the count in a CN8 line's own unit).

| File | What | Made by |
| --- | --- | --- |
| `lux_trade.db` | The SQLite database the app reads (built on the first launch; a fresh load of the slices below gives about 510 MB and 2.32 million observations, and grows as missing slices are fetched on demand) | `python -m besser_sdmx load` (about 20 minutes, most of it Eurostat serving the EU peers' monthly slice; see below) |
| `raw/` | The dataflow's SDMX structure and constraint, cached (~25 MB) | the same command |
| `lux_trade.loading.db` | A load in progress (`quickstart` renames it to `lux_trade.db` when it finishes) | `scripts/quickstart.*` |
| `view_state.json` | The page's current view spec (version 0 is `app/backend/view/baseline.json`) | the running backend |

To rebuild the database (after the full set-up in the README), run `./scripts/quickstart.sh` (Windows: `scripts\quickstart.ps1`), or with
`.venv` activated:

```bash
python -m besser_sdmx load --mapping model/lux_trade_mapping.json --orm app/backend/sql_alchemy.py \
    --slices model/lux_trade_slices.json --db data/lux_trade.db
```

Which slices of ds-045409 are loaded is set in [`model/lux_trade_slices.json`](../model/lux_trade_slices.json).
Only small, aggregated, non-sensitive samples may be committed here. Sources, licences and row
counts are in [DATA_CARD.md](../DATA_CARD.md).
