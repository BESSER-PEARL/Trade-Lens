# Events: where they come from and which sources we can use

The cause finder lines trade moves up with dated events: agreements, tariffs, sanctions, the
pandemic, wars. The events are not taken from a dataset. The team wrote each one by hand in
[`app/backend/events.json`](../app/backend/events.json), and each row links to an official
primary source. The backend serves them at `GET /insights/events`. The trade figures next to them
come from the Eurostat database (see [DATA_CARD.md](../DATA_CARD.md)).

## What a row holds

```json
{
  "id": "us-steel-tariffs-2018",
  "title": "US Section 232 tariffs on EU steel and aluminium",
  "label": "US steel tariffs",
  "date": "2018-06-01",
  "end_date": "2021-12-31",
  "kind": "tariff",
  "scope": {"partners": ["US"], "products": ["72", "73", "76"], "flows": ["EXPORT"]},
  "description": "The United States applies tariffs of 25% on steel and 10% on aluminium ...",
  "source_url": "https://www.govinfo.gov/content/pkg/DCPD-201800387/html/DCPD-201800387.htm",
  "confidence": "curated"
}
```

- `kind`: one of `agreement`, `dispute`, `tariff`, `sanction`, `war`, `disaster`, `pandemic`,
  `policy`, `shock`.
- `scope`: the partners (ISO codes), HS2 chapters and flows (`EXPORT`, `IMPORT`) the event is
  about. Empty lists mean all trade (the pandemic, the Suez Canal blockage). The cause finder uses
  the scope to quote the change of that line of trade, not of all exports.
- `description` is our own one-line wording. The row stores facts (a date, a scope) and a link,
  not text copied from the source.

## The 22 events and their sources

The list starts in 2017, so earlier moves (2001, 2005, 2009) have no event, and the agent says so.

| Date | Event | Source | Link |
| --- | --- | --- | --- |
| 2017-01-20 | Trump takes office | US National Archives | https://www.archives.gov/electoral-college/2016 |
| 2017-09-21 | CETA applied | European Commission (DG Trade) | https://policy.trade.ec.europa.eu/eu-trade-relationships-country-and-region/countries-and-regions/canada/eu-canada-agreement_en |
| 2018-06-01 | US steel tariffs | govinfo.gov (Presidential proclamation) | https://www.govinfo.gov/content/pkg/DCPD-201800387/html/DCPD-201800387.htm |
| 2018-06-22 | EU rebalancing duties | EUR-Lex, Regulation (EU) 2018/886 | https://eur-lex.europa.eu/eli/reg_impl/2018/886/oj |
| 2018-07-18 | EU steel safeguard | EUR-Lex, Regulation (EU) 2018/1013 | https://eur-lex.europa.eu/eli/reg_impl/2018/1013/oj |
| 2019-02-01 | EU-Japan agreement | European Commission (DG Trade) | https://policy.trade.ec.europa.eu/eu-trade-relationships-country-and-region/countries-and-regions/japan/eu-japan-agreement_en |
| 2020-01-31 | Brexit | EUR-Lex, Withdrawal Agreement | https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:12020W/TXT |
| 2020-03-11 | COVID-19 | WHO timeline | https://www.who.int/news/item/29-06-2020-covidtimeline |
| 2020-03-16 | Luxembourg lockdown | Legilux (Journal officiel du Luxembourg) | https://legilux.public.lu/eli/etat/leg/rgd/2020/03/18/a165/jo |
| 2021-01-01 | EU-UK trade agreement | European Commission | https://commission.europa.eu/strategy-and-policy/relations-united-kingdom/eu-uk-trade-and-cooperation-agreement_en |
| 2021-03-23 | Suez Canal blocked | UNCTAD | https://resilientmaritimelogistics.unctad.org/guidebook/case-study-3-port-port-said-egypt |
| 2022-01-01 | US-EU steel truce | EUR-Lex, Regulation (EU) 2021/2083 | https://eur-lex.europa.eu/eli/reg_impl/2021/2083/oj |
| 2022-02-24 | Sanctions on Russia | EUR-Lex, Regulation (EU) 2022/328 | https://eur-lex.europa.eu/eli/reg/2022/328/oj |
| 2022-08-26 | Gas price peak | ESMA | https://www.esma.europa.eu/sites/default/files/2023-10/ESMA50-524821-2963_TRV_Article_the_August_2022_surge_in_the_price_of_natural_gas_futures.pdf |
| 2023-10-01 | CBAM reporting | European Commission (DG TAXUD) | https://taxation-customs.ec.europa.eu/carbon-border-adjustment-mechanism_en |
| 2023-11-19 | Red Sea disruption | EUR-Lex, Decision (CFSP) 2024/583 | https://eur-lex.europa.eu/eli/dec/2024/583/oj |
| 2024-10-30 | EU duties on Chinese EVs | EUR-Lex, Regulation (EU) 2024/2754 | https://eur-lex.europa.eu/eli/reg_impl/2024/2754/oj |
| 2025-01-20 | Trump's second term | US National Archives | https://www.archives.gov/electoral-college/2024 |
| 2025-03-12 | US steel tariffs 2025 | US Federal Register | https://www.federalregister.gov/documents/2025/02/18/2025-02833/adjusting-imports-of-steel-into-the-united-states |
| 2025-04-05 | US 10% tariffs | White House (Presidential action) | https://www.whitehouse.gov/presidential-actions/2025/04/regulating-imports-with-a-reciprocal-tariff-to-rectify-trade-practices-that-contribute-to-large-and-persistent-annual-united-states-goods-trade-deficits/ |
| 2025-06-04 | US steel tariffs 50% | White House (Presidential action) | https://www.whitehouse.gov/presidential-actions/2025/06/adjusting-imports-of-aluminum-and-steel-into-the-united-states/ |
| 2025-08-21 | EU-US 15% deal | European Commission (DG Trade) | https://policy.trade.ec.europa.eu/news/joint-statement-united-states-european-union-framework-agreement-reciprocal-fair-and-balanced-trade-2025-08-21_en |

## Sources we can use

These are the sources to use for new events. All of them are official, public and free to read.
We only take facts from them (a date, who and what it concerns) and a link, and write the
description ourselves.

| Source | What it gives | Access | Reuse |
| --- | --- | --- | --- |
| **EUR-Lex** | EU legal acts: trade agreements, duties, safeguards, sanctions, with their dates in force | Website; machine access through the Publications Office's Cellar SPARQL endpoint (https://publications.europa.eu/webapi/rdf/sparql) | EU reuse policy (Commission Decision 2011/833/EU): reuse allowed with attribution |
| **European Commission** (DG Trade, DG TAXUD) | Agreements in force, trade defence, CBAM, joint statements | Website | Same EU reuse policy |
| **US Federal Register** | US proclamations and rules (Section 232 tariffs, reciprocal tariffs) | Website and a free JSON API with no key (https://www.federalregister.gov/developers/documentation/api/v1) | US federal government works are in the public domain |
| **govinfo.gov / White House / National Archives** | Presidential documents and terms of office | Website (govinfo also has an API) | Public domain (US federal works) |
| **Legilux** | Luxembourg law: the 2020 lockdown, national measures | Website | Official journal of Luxembourg, public |
| **WHO, UNCTAD, ESMA** | Dates of the pandemic, shipping disruptions, energy market shocks | Website | We cite a date and link only; check each site's terms before quoting any text |

The open path to a fuller list is to query EUR-Lex (Cellar SPARQL) and the Federal Register API
for trade acts by date, then check and scope each candidate by hand before adding it to
`events.json`.

## Sources we do not use

These databases cover events well but restrict redistribution. Nothing from them is in this
repository or served by the app. We can read them as references, but we don't copy rows from
them.

| Database | Covers | Why not |
| --- | --- | --- |
| WTO Regional Trade Agreements database (RTA-IS) | Trade agreements and their entry-into-force dates | Terms restrict redistribution |
| DESTA (Design of Trade Agreements) | Trade agreements, academic dataset | Terms restrict redistribution |
| Global Trade Alert | Tariffs and trade measures by country and HS code | Terms restrict redistribution |
| EM-DAT | Natural and technological disasters | Registration, non-commercial use, restricts redistribution |

## Adding an event

1. Find the event in one of the sources we can use, and link the most specific page (the legal
   act or the proclamation, not a general overview page).
2. Add a row to `app/backend/events.json` with the fields above. Set its `scope` to the partners,
   HS2 chapters and flows it is about, and leave the lists empty only for events about all trade.
3. Restart the backend: it reads the file once per process. The agent also caches
   `/insights/events` for 30 minutes (`LUX_INSIGHTS_CACHE_SECONDS`), so restart it too.
4. Update the event count in `DATA_CARD.md`.

The agents only say that an event's date lines up with a move in trade, never that it caused it.
