# Data Card

## Sources

| Field | Value |
| --- | --- |
| Main dataset | [ds-045409 — EU trade since 1988 by HS2-4-6 and CN8](https://ec.europa.eu/eurostat/databrowser/view/ds-045409/legacyMultiFreq/table?category=ext_go.ext_go_detail) |
| Publisher | Eurostat (Comext), compiled from national data incl. STATEC for Luxembourg |
| Concept | Eurostat's **Community concept**: it includes quasi-transit (goods imported and cleared in Luxembourg, then shipped on, without a Luxembourg owner). STATEC's **national concept** leaves quasi-transit out, so its figures differ: 2025 exports €15.08 bn (+1.6%) nationally against €16.25 bn (+8.6%) here. Most of the 2025 smartphone flow (China in, France out) is quasi-transit. |
| Access | Eurostat Comext SDMX 2.1 API, no key required: `https://ec.europa.eu/eurostat/api/comext/dissemination/sdmx/2.1` (structure and SDMX-CSV data) |
| Licence | [Eurostat copyright notice](https://ec.europa.eu/eurostat/help/copyright-notice), reuse under CC BY 4.0 with attribution |
| Version used | Release of 15 September 2026, pulled live in October 2026 |
| Also used (to show the importer is generic) | STATEC [LUSTAT](https://lustat.statec.lu) dataflow `DSD_INT_TRAD@DF_E4103`, "Foreign trade of Luxembourg by partner countries", via `https://lustat.statec.lu/rest` (SDMX 2.1, .Stat Suite). Not used for the findings. STATEC's terms of use for LUSTAT apply (attribution to STATEC) |
| Also used (to show the importer is generic) | Eurostat `NAMA_10_GDP` (GDP and main components), same Eurostat licence |
| Dated events (agents' context) | `app/backend/events.json`, served by `/insights/events`: 17 events (trade agreements, tariffs, sanctions, COVID-19, shipping disruptions), curated by hand by the team, with a `source_url` on every row (EUR-Lex, WHO, the European Commission, the US Federal Register and others) |

### Events: what they are and are not

- **Curated, not a dataset extract.** Each row (id, label, date, end date, kind, scope by partner,
  HS2 chapter and flow, a one-line description, a source URL, a confidence level) was written by the
  team. The agents use them only to say that dates match ("not proof of cause").
- **Sources:** every row links to a source. Some rows (CETA, the EU-Japan agreement, the
  Luxembourg lockdown, the Ever Given, the US-EU steel truce, the gas price peak, the Red Sea) still
  point to the Commission's general negotiations page and should get a more specific source.
- **Not redistributed:** no data from the WTO Regional Trade Agreements database (RTA-IS), DESTA,
  Global Trade Alert (GTA) or EM-DAT is included in this repository or served by the app; their terms
  restrict redistribution. They are possible references for a fuller events list, not inputs.

## What is loaded: slices, not the whole dataset

The full ds-045409 (every EU reporter × ~250 partners × ~40,000 product codes × 35+ years, monthly
and annual) is far larger than the app needs. `python -m besser_sdmx load` downloads only the
slices listed in [`model/lux_trade_slices.json`](model/lux_trade_slices.json), live from the API, into
`data/lux_trade.db` (not committed; about 510 MB with its indexes, plus ~25 MB of cached structure in
`data/raw/`; rebuilt in about 20 minutes, most of it Eurostat serving the monthly peers slice). Row counts as
loaded on 14 October 2026, and on 15 October for the EU peers' trade with four partners:

| Slice | Frequency | Years | Rows |
| --- | --- | --- | --- |
| Luxembourg × every partner × total + every HS2 chapter, imports and exports | Annual | 1999–2025 | 262,231 |
| Luxembourg × world × every product code (HS2, HS4, HS6, CN8) | Annual | 2015–2025 | 291,970 |
| Luxembourg × top 8 partners (BE, DE, FR, NL, IT, ES, US, CN) × every product code | Annual | 2023–2025 | 349,304 |
| Luxembourg × world × total + every HS2 chapter | Monthly | 1999-01 – latest | 64,116 |
| Luxembourg × every partner × total goods | Monthly | 2015-01 – latest | 41,069 |
| 26 other EU members × world × total + every HS2 chapter (comparison, specialisation) | Annual | 1999–2025 | 136,775 |
| 26 other EU members × US, CN, GB, CH × total + every HS2 chapter (their own trade with one partner) | Annual | 2015–2025 | 206,408 |
| BE, NL, DE, FR, IT, ES, AT × US, CN, GB, CH, world × total + every HS2 chapter | Monthly | 2019-01 – latest | 607,014 |
| The 19 other EU members × US, CN, GB, CH, world × total goods | Monthly | 2019-01 – latest | 17,268 |
| Luxembourg × world × every product code, quantities (weight and supplementary units) | Annual | 2015–2025 | 339,632 |
| **Total** | | | **2,315,787** |

To get it: `./scripts/quickstart.sh` (Windows: `scripts\quickstart.ps1`) loads it on its first run, or
run the `python -m besser_sdmx load ...` command of the README's Quick start. No account or key is
needed. The database is not in the Git repository: Eurostat's API is the only source.

**This release folder includes a snapshot**, `data/lux_trade.db`, copied with SQLite's backup API from the
database the team used on 15 October 2026 (de-duplicated, with the series-key index): about 514 MB and
2,325,643 observations (1,358,172 with Luxembourg as reporter; 1,982,647 values in euros, 295,867 weights and
47,129 supplementary quantities). It holds the slices above, the 1999-2014 top-up of
[`model/lux_trade_slices_history.json`](model/lux_trade_slices_history.json) and the slices fetched on demand
while the app was used (140 slices recorded in `_loaded_slice`). Every value in it comes from the same
Eurostat API, loaded the same way as described on this card.

### Measures: value, weight and units

ds-045409 holds three indicators (its content constraint lists exactly these; the other codes of the shared
`CXT_INDICATORS` codelist, such as PRODCOM's production quantities, belong to other datasets):

| Code | Plain name | Unit | Filled for Luxembourg (exports to the world, 2025, CN8 lines with a value) |
| --- | --- | --- | --- |
| `VALUE_IN_EUROS` | Value | euros | every line (6,567) |
| `QUANTITY_IN_100KG` | Weight (net mass) | 100 kg, shown in tonnes (× 0.1) | 6,503 lines (99.0%; 99.9% of the value); also every HS2/HS4/HS6 code and the total |
| `SUPPLEMENTARY_QUANTITY` | Count in the line's own unit | per CN8 line: number of items, litres, pairs, m², ... | 1,793 lines (27.3%; 36.7% of the value): the lines the CN gives a supplementary unit (99.9% of them) |

- The **unit of a supplementary quantity** is not in the dataset (its SDMX structure has no unit attribute). It
  comes from Eurostat's Comext classification files `CN8-SUPPL_UNIT.txt` and `SUPPL_UNIT.txt` (bulk download,
  same licence), kept in `app/backend/suppl_units.json` by `scripts/suppl_units.py` (4,515 CN8 lines, 30 units).
- Only CN8 lines have a supplementary quantity. An HS4 or HS6 code is counted in units when its CN8 lines in one
  unit hold at least 99.5% of its value (cars, HS 8703: 28 lines, all "number of items"); total goods and HS2
  chapters have a weight only.
- **Unit values** (euros per tonne, euros per unit) are value ÷ quantity, computed by the API: averages over
  models and grades, not prices.
- A quantity of zero or not published (confidential, or no unit for the line) is shown as "not published",
  never 0.
- Loaded: Luxembourg × world × every product code, 2015–2025. Anything else (a partner, a month, another year) is
  fetched from Eurostat on its first ask, like any slice.

What is **not** loaded:
- product detail below HS2 for partners outside the top 8, or before 2023;
- monthly series by partner × chapter for Luxembourg;
- other countries' own trade with partners other than the US, China, the UK and Switzerland (and before 2015,
  or by product below HS2): "Spain's exports of coffee to China" is fetched on its first ask.

When the app needs such detail (for example CN8 products for Ireland), it fetches that slice live
from the API on demand, stores it and records it in the `_loaded_slice` table, so "not loaded" is
never shown as zero.

## Dimensions

| Dimension | Values used |
| --- | --- |
| `freq` | `A` (annual), `M` (monthly) |
| `reporter` | `LU`, plus 26 EU members for comparison (their total trade, and their own trade with the US, China, the UK and Switzerland) |
| `partner` | Individual countries and aggregates (`WORLD`, `EU27_2020`, `EXT_EU27_2020`, …) |
| `product` | `TOTAL`, HS2 / HS4 / HS6 and CN8 codes |
| `flow` | `1` import, `2` export |
| `indicators` | `VALUE_IN_EUROS`, `QUANTITY_IN_100KG`, `SUPPLEMENTARY_QUANTITY` (see Measures) |

## Transformations

1. **Structure:** read the dataflow's SDMX structure into a B-UML domain model.
   - The product codelist is turned into a hierarchy by code prefix (HS2 → HS4 → HS6 → CN8).
   - Partner aggregates (WORLD, EU27…) are kept as codes and excluded from rankings.
2. **Load:** download the slices above as SDMX-CSV into the database generated from that model.
   - Queries that are too large are split by year, then by halving code lists.
   - Overlapping slices are de-duplicated; loading more slices into an existing database (`--append`) replaces
     a row already there (from an earlier load or a live fetch) instead of doubling it.
   - One index on the whole series key (product, partner, reporter, flow, frequency, indicator, period) makes a
     slice a lookup, not a scan.
3. **Compute, at query time in the generated API:**
   - shares, significance thresholds and the "Other" bucket;
   - STL seasonal adjustment of monthly series;
   - year-on-year movers;
   - revealed comparative advantage vs the 26 EU peers;
   - trends (a least-squares fit on the logs of annual series, the STL trend of monthly ones) and
     growth rankings;
   - forecasts: STL plus damped-trend exponential smoothing on the logs of monthly series, with an
     80% band from simulations and a backtest against the seasonal naive forecast (12 months
     ahead it does not beat it on exports, and the forecaster says so).

## Known limitations

- **Provisional data:** the latest periods (2026 months) are provisional and revised by Eurostat in
  later releases. STL estimates are least stable at the ends of a series.
- **Partial current year:** annual comparisons use complete years only (up to 2025).
- **Partner definitions change over time,** e.g. Belgium includes Luxembourg before 1999.
- **Nominal euros,** not adjusted for inflation.
- **"Not specified" partner codes:** Eurostat reports part of trade under codes such as
  - `QV` (countries not specified, intra-EU): €202 m of 2025 exports;
  - `QW` (not specified, extra-EU): €49 m;
  - `QS` (stores and provisions): €2.5 m.

  They match the ISO-like pattern but are excluded from rankings and the map; they are part of the
  totals and therefore of "Other".
- **Confidential trade** is reported under special codes and cannot be attributed to a product or partner.

## Privacy

The datasets contain only aggregated official statistics. No personal or confidential data is
stored in this repository; the local database is not committed.
