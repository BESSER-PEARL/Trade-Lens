// The method catalogue: what each /insights and /trends endpoint calculates, in plain words and for
// statisticians. The assistant only chooses among these fixed methods and writes the words around them;
// every figure on the page is computed by one of them from the official data (Eurostat ds-045409).
//
// One entry per endpoint of ENDPOINT_PARAMS in app/backend/view/validate.py (tests/test_methods_catalogue.py
// checks that none is missing). The texts follow the code: app/backend/routers/insights.py,
// app/backend/trends/analysis.py and app/backend/forecasting (read them before changing a sentence).
// "plain" is one everyday sentence for any reader; "detail" is the technical method, with the settings
// the query actually used.
//
// Also here: the Data line of a set of queries, the "what was calculated and where it shows" phrase of
// each panel type and mark kind, and the origin of a single figure (the tooltip on a {fact}).

import type { Mark, Panel, Params, Query } from "./types";
import { formatPeriod } from "./facts";
import { isAgentOwner } from "./tokens";

export interface MethodText {
  /** Catalogue key: the endpoint, or the endpoint and a variant ("/insights/series#adjusted"). */
  key: string;
  /** Plain name, sentence case: "Ranking by share". */
  name: string;
  /** One everyday sentence. */
  plain: string;
  /** The technical method and the settings this query used, for statisticians. */
  detail: string;
  /** True when nothing is calculated (a curated list, the data catalogue). */
  noCalculation?: boolean;
}

interface Entry {
  name: string | ((p: Params) => string);
  plain: (p: Params) => string;
  detail: (p: Params) => string;
  noCalculation?: boolean;
}

// ------------------------------------------------------------------ words

const FLOW: Record<string, string> = { IMPORT: "imports", EXPORT: "exports", "1": "imports", "2": "exports", RE_EXPORT: "re-exports" };

/** "exports" (the default flow of the data), "imports", or "imports and exports" for a list. */
export function flowWord(p: Params): string {
  const raw = String(p.flow ?? "EXPORT");
  const words = raw.split(",").map((f) => FLOW[f.trim().toUpperCase()] || f.toLowerCase());
  return words.length > 1 ? words.join(" and ") : words[0];
}

/** Share as a cutoff: 0.01 -> "1%", 0.001 -> "0.1%". */
export function pctWord(v: number): string {
  const s = (v * 100).toPrecision(2).replace(/\.?0+$/, "");
  return `${parseFloat(s)}%`;
}

/** The level a product ranking is at: the children of `parent`, else `level`, else chapters. */
function productLevel(p: Params): number {
  if (p.level !== undefined && p.level !== "") return Number(p.level) || 1;
  const parent = String(p.parent ?? "");
  if (/^\d{2}$/.test(parent)) return 2;
  if (/^\d{4}$/.test(parent)) return 3;
  if (/^\d{6}$/.test(parent)) return 4;
  return 1;
}

const LEVEL_NAME = ["", "product chapters (HS2)", "product headings (HS4)", "product subheadings (HS6)", "product lines (CN8)"];
const LEVEL_ONE = ["", "chapter", "heading", "subheading", "product line"];

/** The significance ladder's default cutoff, as the backend applies it (_default_threshold). */
export function cutoffOf(p: Params): number {
  if (p.threshold !== undefined && p.threshold !== "" && p.threshold !== "auto" && Number.isFinite(Number(p.threshold))) return Number(p.threshold);
  if (String(p.by || "") === "product") return 0.01 / 10 ** (productLevel(p) - 1);
  return 0.01;
}

/** "partner countries", "product chapters (HS2)", "EU countries". */
export function byWord(p: Params, plural = true): string {
  const by = String(p.by || "");
  if (by === "partner") return plural ? "partner countries" : "partner country";
  if (by === "reporter") return plural ? "EU countries" : "EU country";
  if (by === "product") return plural ? LEVEL_NAME[productLevel(p)] || "products" : LEVEL_ONE[productLevel(p)] || "product";
  return plural ? `${by}s` : by;
}

function freqWord(p: Params): "yearly" | "monthly" | "quarterly" {
  const f = String(p.freq || "A").toUpperCase();
  return f === "M" ? "monthly" : f === "Q" ? "quarterly" : "yearly";
}

const when = (v: unknown) => formatPeriod(String(v));

/** The query's settings are listed under the method in the card itself, so the detail does not repeat them. */
function settingsList(_p: Params, _keys: string[]): string {
  return "";
}

export function settingsOf(p: Params, keys: string[]): string {
  const parts = keys.filter((k) => p[k] !== undefined && p[k] !== "").map((k) => `${k} ${p[k]}`);
  return parts.length ? ` Settings of this query: ${parts.join(", ")}.` : "";
}

const LADDER_TEXT =
  "The significance ladder: a share of at least 1% of the total for partner countries and HS2 chapters, one decimal less per level of detail (0.1% for HS4 headings, 0.01% for HS6, 0.001% for CN8 lines).";

// ------------------------------------------------------------------ the catalogue

const RANKING: Entry = {
  name: "Ranking by share",
  plain: (p) =>
    `Sorts the ${byWord(p)} by their share of Luxembourg's ${flowWord(p)} and keeps those with at least ${pctWord(cutoffOf(p))}${
      p.max_items ? ` (at most ${p.max_items})` : ""
    }; the rest are added up as "other".`,
  detail: (p) =>
    `Members of one dimension ranked by value, with a significance cutoff: share of the total ≥ ${pctWord(cutoffOf(p))}${
      p.threshold !== undefined ? " (set by the query)" : ""
    }. ${LADDER_TEXT} Members below the cutoff, and those after max_items, go into "Other (n)"; trade that no member accounts for (confidential or not allocated, partner codes QP to QZ) is reported apart when it is above 0.5% of the total. Shares are of the dimension's total (partner WORLD, product TOTAL), or of the parent with relative_to=parent; with start and end the values are summed over the years. Countries only: the aggregates EU, EA, EZ, XX, ZZ are never ranked.` +
    settingsList(p, ["by", "level", "parent", "threshold", "max_items", "relative_to", "start", "end"]),
};

const SPECIALISATION: Entry = {
  name: "Revealed comparative advantage",
  plain: (p) =>
    `Luxembourg's share of each ${byWord(p, false)} in its ${flowWord(p)}, divided by the same share for the other EU countries together. Above 1, Luxembourg trades relatively more of it than its peers.`,
  detail: (p) =>
    `Balassa index: RCA = (x_LU,i / X_LU) / (x_peers,i / X_peers), where the peers are every other EU reporter in the data, summed, at HS2. The /records form keeps the items with RCA above 1, rounded to two decimals, largest first.` +
    settingsList(p, ["by", "limit"]),
};

export const CATALOGUE: Record<string, Entry> = {
  "/insights/meta": {
    name: "Data catalogue",
    plain: () => "No calculation: the list of what the data holds (countries, products, periods, units).",
    detail: () => "Dimensions, their codes and the periods per frequency; complete_until is the last year with twelve months (later months are provisional).",
    noCalculation: true,
  },
  "/insights/ranking": RANKING,
  "/insights/ranking/records": { ...RANKING, name: "Ranking by share" },
  "/insights/ladder": {
    name: "Totals with the significance ladder",
    plain: (p) =>
      `Adds up all of Luxembourg's ${flowWord(p)} in the year, and counts how many ${byWord(p)} pass each cutoff (10%, 1%, 0.1% and so on) and how much of the value they cover.`,
    detail: (p) =>
      `total is the dimension's total code (partner WORLD, product TOTAL), so trade with no partner specified is included. n_items counts the members with a value above zero (countries only: the aggregates and the confidential codes QP to QZ are left out). For each cutoff in 10%, 1%, 0.1%, 0.01%, 0.001%: the number of members whose share of the total is at least the cutoff, and the share of the total they cover.` +
      settingsList(p, ["by", "level", "parent", "relative_to", "start", "end"]),
  },
  "/insights/series": {
    name: (p) => (p.adjust === true || p.adjust === "true" ? "Seasonal adjustment" : "Totals over time"),
    plain: (p) =>
      p.adjust === true || p.adjust === "true"
        ? `Adds up Luxembourg's ${flowWord(p)} for every month, then takes out the usual month-to-month pattern (the months that are high or low every year), learnt from the whole monthly history, not only the months shown. One-off rises and falls stay in.`
        : `Adds up Luxembourg's ${flowWord(p)} for every ${freqWord(p) === "monthly" ? "month" : freqWord(p) === "quarterly" ? "quarter" : "year"}. No modelling: one sum per period.`,
    detail: (p) =>
      (p.adjust === true || p.adjust === "true"
        ? "Sum of the values per month; adjusted = value − seasonal, the seasonal component of an STL decomposition (statsmodels STL, period 12, robust, LOESS defaults) fitted on the whole monthly history of the slice in the database, then cut to start..end, so the remainder (irregular) stays in the adjusted value. A month with no trade recorded counts as zero. Needs at least 60 months (five years) of history with trade recorded: with fewer, robust STL gives most months a zero remainder (adjusted = trend), so the series is refused as too short to adjust. The trend and seasonal components are returned as well; the chart draws the values as recorded faintly under the adjusted line."
        : "Sum of the values per period for the filtered slice (reporter LU, partner WORLD and product TOTAL unless the query says otherwise). No model, no smoothing.") +
      settingsList(p, ["freq", "start", "end", "adjust"]),
  },
  "/insights/movers": {
    name: "Movers",
    plain: (p) =>
      `The change from ${when(p.previous)} to ${when(p.period)} for each ${byWord(p, false)}, the largest rises and falls first. Those under ${pctWord(
        cutoffOf(p),
      )} of the total in both years are not listed, but they stay in the totals.`,
    detail: (p) =>
      `change = value(period) − value(previous) per member. A member is listed when the larger of its two values is at least ${pctWord(
        cutoffOf(p),
      )} of the total (${LADDER_TEXT.charAt(0).toLowerCase() + LADDER_TEXT.slice(1, -1)}). up and down hold the largest increases and decreases (limit each). total and previous_total are the totals of the whole selection, so previous_total plus every change, listed or not, equals total: a waterfall's "everything else" step is that remainder.` +
      settingsList(p, ["by", "period", "previous", "level", "parent", "threshold", "limit"]),
  },
  "/insights/compare": {
    name: "Values side by side",
    plain: (p) => `Puts the ${flowWord(p)} of several ${byWord(p)} in ${when(p.period)} side by side, each with its share of their sum.`,
    detail: (p) => "Value per member of the dimension in one period; share = value / sum of the members asked for (group)." + settingsList(p, ["by", "period", "group"]),
  },
  "/insights/compare/records": {
    name: "Growth among EU countries",
    plain: (p) =>
      `Each EU country's own reported ${flowWord(p)} in ${when(p.period)} against ${p.base ? when(p.base) : "the first year of the data"}, as a growth rate, ranked; Luxembourg's place is marked.`,
    detail: (p) =>
      "growth = 100 × (value(period) / value(base) − 1) for every reporting country (each country's own declarations to Eurostat, so the 27 are comparable), ranked by growth; base defaults to the first year in the data. Nominal euros: no price or exchange-rate adjustment." +
      settingsList(p, ["by", "period", "base", "limit"]),
  },
  "/insights/specialisation": SPECIALISATION,
  "/insights/specialisation/records": SPECIALISATION,
  "/insights/trend": {
    name: "Trend line",
    plain: (p) =>
      freqWord(p) === "yearly" && String(p.method || "auto") !== "stl"
        ? `Fits a straight line through the yearly ${flowWord(p)} on a growth scale, so its slope is a growth rate per year.`
        : `Takes the smooth path of the ${freqWord(p)} ${flowWord(p)} (the seasonal ups and downs removed, learnt from the whole history) and measures how steep it is over the months shown only.`,
    detail: (p) =>
      `method ${p.method || "auto"}: auto is ols_log for yearly data (least squares on ln(value), slope_per_year = exp(b) − 1) and stl for monthly or quarterly data with at least five years of history (the trend component of one STL decomposition, period 12, robust, of the slice's whole history, cut to start..end; the slope is a log-linear fit over start..end only); ols is a linear fit for series with zeros. cagr is from the first to the last value over start..end (of the STL trend for monthly data); r2 is the fit's R² over that span. Under five years of history auto uses ols_log.` +
      settingsList(p, ["freq", "start", "end", "method"]),
  },
  "/insights/trends": {
    name: "Fastest growing",
    plain: (p) =>
      `Growth per year between ${p.start ? when(p.start) : "the first year"} and ${p.end ? when(p.end) : "the last year"} (compound annual growth), for the ${byWord(p)} above the significance cutoff (at least ${pctWord(
        cutoffOf(p),
      )} of ${flowWord(p)} at the start or at the end), the fastest-growing first. Those with nothing at the start have no rate and are left out.`,
    detail: (p) =>
      `growth is the compound annual growth rate, (v_end / v_start)^(1/years) − 1; method=slope ranks by a log-linear least-squares fit over every period instead. Members below the significance ladder at both ends are not ranked, so tiny items with huge rates do not crowd the list. falling lists the steepest declines, new the members with no value at the start.` +
      settingsList(p, ["by", "start", "end", "level", "parent", "threshold", "limit", "method"]),
  },
  "/insights/forecast": {
    name: "Forecast with its test",
    plain: (p) =>
      String(p.method || "auto") === "auto" || p.method === "ensemble"
        ? `Two tested methods (Holt-Winters smoothing, and gradient boosting trained on every product chapter) each forecast the coming months, and their average is taken. The band is where ${Math.round(
            100 * (Number(p.interval) > 1 ? Number(p.interval) / 100 : Number(p.interval) || 0.8),
          )}% of the method's own past errors fell. The method was chosen by a test on the past, not by the assistant.`
        : `A forecast of the coming months by one named method (${p.method}), with a band where ${Math.round(
            100 * (Number(p.interval) > 1 ? Number(p.interval) / 100 : Number(p.interval) || 0.8),
          )}% of its own past errors fell.`,
    detail: (p) =>
      `method ${p.method || "auto"} (auto = ensemble): the mean of Holt-Winters exponential smoothing with a damped trend and gradient boosting on lags, seasons and working days, trained on every HS2 chapter of imports and exports (monthly) and retrained once a year on the data up to each December. Chosen by a rolling-origin backtest at 66 quarter ends, December 2009 to March 2026, 12 months ahead, against the seasonal naive forecast (docs/forecasting.md). The band is split conformal: the ${
        p.interval || 0.8
      } quantile of this series' own backtest errors at each horizon, no distributional assumption. Each call backtests again at the last 20 quarter ends (MASE, MAPE and coverage at 1, 3, 6 and 12 months). The COVID months are replaced for training only. Deterministic: the same data gives the same answer.` +
      settingsList(p, ["freq", "horizon", "interval", "start", "end", "aggregate", "backtest"]),
  },
  "/insights/events": {
    name: "Dated events",
    plain: () =>
      "No calculation: dated events from a curated list (agreements, tariffs, sanctions, crises), each with its source, placed on the timeline. Dates that line up are not proof of a cause.",
    detail: (p) =>
      "app/backend/events.json, filtered by overlap with the window, category, partner, HS chapter and flow; every row carries its source link. No statistic is computed." +
      settingsList(p, ["start", "end", "category", "limit"]),
    noCalculation: true,
  },
  "/insights/flows": {
    name: "Ranking of partners, both ways",
    plain: (p) =>
      `Two rankings by partner country for ${when(p.period)}: where Luxembourg's imports come from and where its exports go. Partners under ${pctWord(
        cutoffOf(p),
      )} of each total are added up as "other".`,
    detail: (p) =>
      `Each side is /insights/ranking by=partner (cutoff ${pctWord(cutoffOf(p))}, max_items ${p.max_items ?? 8}); the balance is exports minus imports; with previous, each item carries its value and change in that period.` +
      settingsList(p, ["period", "previous", "max_items", "threshold"]),
  },
  "/insights/matrix": {
    name: (p) => (p.measure === "rank" ? "Ranks over the years" : p.measure === "growth" ? "Growth over the years" : p.measure === "share" ? "Shares over the years" : "Values over the years"),
    plain: (p) =>
      `The largest ${byWord(p)} in rows and the years ${when(p.start)} to ${when(p.end)} in columns, with ${
        p.measure === "rank" ? "each one's rank" : p.measure === "growth" ? "each one's growth on the year before" : p.measure === "share" ? "each one's share of the total" : "the value"
      } in every cell.`,
    detail: (p) =>
      `Rows: the max_items members largest over the span (summed value), among those above the significance ladder in at least one period. measure ${
        p.measure || "value"
      }: value (euros), share (of the period's total), rank (1-based among every member with trade that period) or growth (on the previous period).` +
      settingsList(p, ["by", "start", "end", "level", "parent", "max_items", "measure", "threshold"]),
  },
  "/insights/tree": {
    name: "Product tree",
    plain: (p) =>
      `Splits the ${when(p.period)} ${flowWord(p)} into product chapters and what is inside them, as nested tiles sized by value; tiles under the cutoff are added up as "other".`,
    detail: (p) =>
      `HS2 chapters, their HS4 headings, their HS6 subheadings (depth ${p.depth ?? 2}). threshold auto follows the significance ladder: 1% of the total at the first level below the root, 0.1% at the second, 0.01% at the third. Children below it go into one OTHER child, which also holds what the listed children do not account for, so each node's children add up to its value.` +
      settingsList(p, ["period", "depth", "threshold"]),
  },
  "/trends/analyze": {
    name: "Seasonal adjustment and lasting shifts",
    plain: (p) =>
      `Takes the usual seasonal ups and downs out of the ${freqWord({ freq: p.freq ?? "M" })} ${flowWord(
        p,
      )} to see the underlying direction; the pattern is learnt from the whole history, not only the months shown. No working-day or Easter correction: this is not STATEC's official seasonal adjustment. Separately, it finds the months after which the level stayed different for good, on the standardised monthly values (not seasonally adjusted): lasting shifts, not one-off months. It says where the series changed, not why.`,
    detail: (p) =>
      `STL decomposition (statsmodels, period ${p.stl_period ?? 12}, robust, on levels; no calendar or working-day adjustment and no outlier pre-treatment, so not comparable with an official JDemetra+ adjustment) into trend, seasonal and remainder, fitted on the slice's whole history in the database and cut to start..end (at least five years of history; under that a centred moving average, and no seasonal adjustment: "too short to adjust"); direction from the trend's last value against its first over the span (stable when it changes less than 5%, volatile when the remainder's spread exceeds 35% of the level); seasonal strength (Hyndman and Athanasopoulos) is weak below 0.3, strong from 0.6. Change points: PELT (ruptures) with an rbf cost on the span's own standardised monthly values (not seasonally adjusted; searched on the span, so a shift is judged against the span's spread, not decades of growth), penalty ${
        p.penalty ?? 3
      } × ln(n), segments at least ${p.min_size ?? 6} periods long; the levels before and after are the means of the ${p.min_size ?? 6} periods on each side.` +
      settingsList(p, ["freq", "start", "end", "stl_period", "min_size", "penalty"]),
  },
  "/trends/seasonal-heatmap": {
    name: "Seasonal profile by product",
    plain: (p) => `For each of the ${p.top ?? 12} largest products, the typical effect of each month on its ${flowWord(p)}, in percent, from a seasonal adjustment.`,
    detail: (p) =>
      "Per product: STL decomposition (period 12 for monthly, 4 for quarterly data, robust) of the product's whole history, cut to start..end; the average seasonal effect of each month over that span as a percentage of the product's typical level. Products with under five years of history are left out." +
      settingsList(p, ["freq", "start", "end", "top"]),
  },
};

// Contract 1.8: what a query reads (measure). The same words as the backend's catalogue (_MEASURE_PLAIN, _MEASURE_DETAIL).
const MEASURED = ["/insights/series", "/insights/ranking", "/insights/ranking/records", "/insights/movers", "/insights/compare"];
const MEASURE_NAME: Record<string, string> = {
  weight: "Weight in tonnes",
  units: "Count in the line's own unit",
  all: "Value, quantities and unit values",
};
const MEASURE_PLAIN: Record<string, string> = {
  weight: "Weighs the goods instead of valuing them: the net mass declared, in tonnes (Eurostat publishes it in units of 100 kg).",
  units:
    "Counts the goods in their own unit (number of items, litres, pairs...), as declared for each 8-digit product line; a 4- or 6-digit heading adds up its lines when they all count in the same unit.",
  all: "Gives every measure the data has for each row: value in euros, weight in tonnes, the count in the line's own unit, and the average value per tonne and per unit (value divided by quantity: an average over models and grades, not a price). A line without a published quantity shows its value alone.",
};
const MEASURE_DETAIL =
  "Eurostat ds-045409 publishes three indicators: VALUE_IN_EUROS, QUANTITY_IN_100KG (net mass; × 0.1 = tonnes) and SUPPLEMENTARY_QUANTITY (the CN8 line's supplementary unit: number of items, litres, pairs...; only CN8 lines have one, named from Comext's CN8-SUPPL_UNIT classification). An HS4 or HS6 code counts in units only when its CN8 lines in one unit hold at least 99.5% of its value; total goods and HS2 chapters have weight only. A quantity of zero or not published is shown as not published, never 0. Unit values: value / tonnes and value / units, averages that mix models or grades, so they are not prices.";

/** weight, units or all when a query reads quantities; null for euros (the default). */
export function measureOf(q: { endpoint: string; params?: Params }): string | null {
  const m = String(q.params?.measure ?? "");
  return MEASURED.indexOf(q.endpoint) >= 0 && MEASURE_NAME[m] ? m : null;
}

/** The catalogue entry of a query, with its sentences filled from the query's own parameters. */
export function methodOf(q: { endpoint: string; params?: Params }): MethodText {
  const p = q.params || {};
  const e = CATALOGUE[q.endpoint];
  if (!e) return { key: q.endpoint, name: "A calculation of the backend", plain: `Computed by ${q.endpoint} from the official data.`, detail: "" };
  const measure = measureOf(q);
  if (measure) {
    return {
      key: `${q.endpoint}#${measure}`,
      name: MEASURE_NAME[measure],
      plain: `${MEASURE_PLAIN[measure]} ${e.plain(p)}`,
      detail: `${e.detail(p)} ${MEASURE_DETAIL}`,
      noCalculation: e.noCalculation,
    };
  }
  const adjusted = q.endpoint === "/insights/series" && (p.adjust === true || p.adjust === "true");
  return {
    key: adjusted ? `${q.endpoint}#adjusted` : q.endpoint,
    name: typeof e.name === "function" ? e.name(p) : e.name,
    plain: e.plain(p),
    detail: e.detail(p),
    noCalculation: e.noCalculation,
  };
}

// ------------------------------------------------------------------ URLs

/** A relative query URL back into {endpoint, params}: "/insights/ranking?by=partner&period=2025". */
export function parseUrl(url: string): { endpoint: string; params: Params } {
  const [path, query = ""] = String(url).split("?");
  const params: Params = {};
  query.split("&").forEach((kv) => {
    if (!kv) return;
    const i = kv.indexOf("=");
    const k = decodeURIComponent((i < 0 ? kv : kv.slice(0, i)).replace(/\+/g, " "));
    const v = i < 0 ? "" : decodeURIComponent(kv.slice(i + 1).replace(/\+/g, " "));
    params[k] = v === "true" ? true : v === "false" ? false : v;
  });
  return { endpoint: path, params };
}

// ------------------------------------------------------------------ the data line

const EU_ISO: Record<string, string> = { EL: "GR", UK: "GB", XS: "RS" };
let regionNames: { of: (c: string) => string | undefined } | null = null;

/** "Germany" for DE (Eurostat's EL is Greece); the code itself when the browser has no name. */
export function countryName(code: string): string {
  const c = String(code || "").toUpperCase();
  if (c === "WORLD") return "the world";
  if (!/^[A-Z]{2}$/.test(c)) return c;
  try {
    if (!regionNames && typeof Intl !== "undefined" && (Intl as any).DisplayNames) regionNames = new (Intl as any).DisplayNames(["en"], { type: "region" });
    return regionNames?.of(EU_ISO[c] || c) || c;
  } catch {
    return c;
  }
}

function periodsOf(q: { endpoint: string; params?: Params }): string | undefined {
  const p = q.params || {};
  if (p.previous !== undefined && p.period !== undefined) return `${when(p.previous)} to ${when(p.period)}`;
  if (p.base !== undefined && p.period !== undefined) return `${when(p.base)} to ${when(p.period)}`;
  if (p.start !== undefined && p.end !== undefined && String(p.start) !== String(p.end)) return `${when(p.start)} to ${when(p.end)}`;
  if (p.period !== undefined) return when(p.period);
  if (q.endpoint === "/insights/forecast") {
    const from = p.start !== undefined ? when(p.start) : "1999";
    return `monthly since ${from}, forecast ${p.horizon ?? 12} months ahead`;
  }
  if (q.endpoint === "/insights/events") return p.start ? `from ${when(p.start)}` : undefined;
  if (p.start !== undefined) return `${freqWord(p)}, from ${when(p.start)} to the latest ${freqWord(p) === "monthly" ? "month" : "year"}`;
  if (q.endpoint === "/insights/series" || q.endpoint === "/insights/trend" || q.endpoint === "/trends/analyze" || q.endpoint === "/trends/seasonal-heatmap")
    return `${q.endpoint.startsWith("/trends") ? freqWord({ freq: p.freq ?? "M" }) : freqWord(p)}, every period in the data`;
  return undefined;
}

export interface DataLine {
  /** Each phrase on its own: the source, the periods, the flows, the goods, the partners, the unit. */
  parts: string[];
}

/**
 * The Data row of a set of queries (already filled from the page context): "Eurostat ds-045409 (official,
 * produced with STATEC)", the periods, the flows, the goods and partners, nominal euros. `productName`
 * names a product code ("85" -> "electrical machinery").
 */
export function dataLine(queries: Array<{ endpoint: string; params?: Params }>, productName: (code: string) => string = (c) => c): DataLine {
  const real = queries.filter((q) => q.endpoint !== "/insights/meta");
  const periods: string[] = [];
  const flows: string[] = [];
  const goods: string[] = [];
  const partners: string[] = [];
  const bys: string[] = [];
  let events = false;
  real.forEach((q) => {
    const p = q.params || {};
    if (q.endpoint === "/insights/events") {
      events = true;
      return;
    }
    const per = periodsOf(q);
    if (per && periods.indexOf(per) < 0) periods.push(per);
    const fw = q.endpoint === "/insights/flows" ? "imports and exports" : flowWord(p);
    fw.split(" and ").forEach((f) => flows.indexOf(f) < 0 && flows.push(f));
    const product = String(p.product ?? "TOTAL");
    const g = product === "TOTAL" ? "all goods" : product.split(",").map((c) => productName(c)).join(", ");
    if (String(p.by) !== "product" || product !== "TOTAL") goods.indexOf(g) < 0 && goods.push(g);
    if (p.parent) {
      const inside = `inside ${productName(String(p.parent))}`;
      goods.indexOf(inside) < 0 && goods.push(inside);
    }
    if (p.partner && String(p.partner) !== "WORLD") {
      const names = String(p.partner).split(",").map(countryName).join(", ");
      partners.indexOf(names) < 0 && partners.push(names);
    }
    if (p.by) {
      const b = q.endpoint === "/insights/flows" ? "by partner country" : `by ${byWord(p, false)}`;
      bys.indexOf(b) < 0 && bys.push(b);
    }
  });
  const parts = ["Eurostat ds-045409 (official, produced with STATEC)"];
  // "2015 to 2025" already says 2025: a period another one contains is not said twice
  const said = periods.filter((p) => !periods.some((q) => q !== p && ` ${q.replace(/[,;]/g, " ")} `.indexOf(` ${p} `) >= 0));
  if (said.length) parts.push(said.join(" and "));
  if (flows.length) parts.push(`${flows.join(" and ")}${bys.length ? " " + bys.join(" and ") : ""}`);
  if (goods.length) parts.push(goods.join(" and "));
  if (partners.length) parts.push(`with ${partners.join(" and ")}`);
  if (flows.length) parts.push("nominal euros");
  if (events) parts.push("a curated list of dated events, each with its source");
  return { parts };
}

// ------------------------------------------------------------------ what was calculated, where it shows

const yes = (v: unknown) => v === true || v === "true";

/** "What was calculated, and where it shows on this chart", for a panel. */
export function calculatedOf(panel: Panel): string {
  const o = panel.options || {};
  const qs = panel.queries || [];
  const has = (e: string) => qs.some((q) => q.endpoint === e);
  const isHeadline = panel.id === "headline";
  switch (panel.type) {
    case "kpi":
      return "The year's total: the big figure. The number of partner countries with any trade: the caption.";
    case "text":
      return isHeadline && !isAgentOwner(panel.agent)
        ? "Two totals, what was bought and what was sold, and their difference (sold minus bought): the figures in the sentence."
        : "Each figure in the text is read back from the calculation named above; hover over a figure to see which.";
    case "flow":
      return `Each partner's value and share: the width of its ribbon. A partner gets a ribbon of its own when it has at least 4% of its side (and is among the partners the query keeps); the rest: one "others" ribbon on each side.${
        o.show_gap ? " The difference of the two totals: the gap, labelled with what was bought more than sold." : ""
      }`;
    case "line": {
      if (o.compare !== undefined) return compareCalc(String(o.compare));
      const adj = qs.some((q) => yes(q.params?.adjust));
      const series = qs.map((q) => (q.label || q.key || "").toLowerCase()).filter(Boolean);
      return `${adj ? "The seasonally adjusted value" : "One total"} per ${qs.some((q) => String(q.params?.freq) === "M") ? "month" : "year"}${
        series.length ? ` for ${series.join(" and ")}` : ""
      }: the ${series.length > 1 ? "lines" : "line"}.${
        adj ? ` The values as recorded: the faint ${series.length > 1 ? "lines" : "line"} under it; the notes and any forecast are about those.` : ""
      }${o.gap_fill && qs.length >= 2 ? " The space between the two lines is their difference." : ""}`;
    }
    case "bar":
      if (o.compare !== undefined) return compareCalc(String(o.compare));
      if (o.measure === "growth" || has("/insights/trends"))
        return "Each item's growth per year between the first and the last year (compound annual growth): the length of its bar and the figure beside it. Its value in the first and the last year: the line under the bar.";
      if (has("/insights/specialisation/records") || has("/insights/specialisation"))
        return "One ratio per product chapter: the dots on each track. The grey tick is 1, where Luxembourg trades as much of it as its peers.";
      if (has("/insights/compare/records")) return "Each country's growth: the dots on one track, Luxembourg's filled and named. Its rank and growth: the sentence.";
      if (has("/insights/ladder"))
        return "Each chapter's share: the bars. How many chapters pass the cutoff, and how much of the value they cover: the sentence under them.";
      return `Each item's ${String(o.y || "value") === "share" ? "share" : String(o.y || "value")}: the bars.`;
    case "table":
      return "The rows and figures of the table, as the calculation returns them.";
    case "waterfall":
      return 'The two totals: the first and the last bar. The change of each item: the steps between them. Everything not listed: one "everything else" step, so the bars add up.';
    case "choropleth":
      return "Each country's value and share: its shading on the map. Aggregates and trade with no partner specified are in the totals, never on the map.";
    case "flowmap":
      return "The largest partners on each side, with their values: the arcs; an arc's width is the value.";
    case "heatmap": {
      const kind = String(o.kind || "month_year");
      const caption = qs.some((q) => q.endpoint === "/trends/analyze") ? " The direction and the seasonal pattern in the caption come from the seasonal adjustment." : "";
      if (kind === "product_year") return `One value per product and year: one cell, darker is more.${caption}`;
      if (kind === "seasonal") return `The typical effect of each month, per product: one cell, deeper colour is a larger effect.${caption}`;
      return `One total per month: one cell, a row per year, darker is more.${caption}`;
    }
    case "treemap":
      return 'The value of each chapter and of what is inside it: the area of its tile; tiles under the cutoff: one "other" tile.';
    case "butterfly":
      return "Imports by partner: the bars on the left; exports by partner: the bars on the right.";
    case "bump":
      return "Each item's rank in each year: the lines, the top rank at the top.";
    case "scatter":
      return "Each product's comparative advantage and growth: the position of its dot.";
    case "compare":
      return compareCalc(String(o.compare || "absolute"));
    default:
      return "The figures on this chart, as the calculation returns them.";
  }
}

function compareCalc(mode: string): string {
  switch (mode) {
    case "index":
      return "Each series divided by its own value in the base year, times 100, so small and large fit one scale: the lines. The page does this division, the same way every time.";
    case "share":
      return "Each series as a share of the reference (or of their sum): the lines.";
    case "yoy":
      return "Each series' change on the same period a year earlier: the lines or bars.";
    case "diff":
      return "Each series minus the reference: the lines or bars.";
    default:
      return "The value of each series: the lines or bars, side by side.";
  }
}

/** "What was calculated, and where it shows", for a mark. */
export function markCalculated(mark: Mark): string {
  switch (mark.kind) {
    case "thread":
      return "The values along one route, from a supplier through Luxembourg to a buyer: the thread drawn over the crossroads.";
    case "trend":
      return "The fitted trend and its slope: the line drawn over the chart and the figures in the note.";
    case "event":
      return "Nothing new is calculated: the event's date places it on the timeline; any change in the note is read from the yearly totals.";
    case "forecast":
      return "The forecast, its low and high values and its test scores: the hatched band and the figures in the note.";
    case "trend_break":
      return "The break month and the typical level before and after it: the rule on the chart and the figures in the note.";
    case "annotation":
      return "Nothing new: the note points at a value already on the chart and reads its figure from the chart's own calculation.";
    case "highlight":
      return "Nothing new: the highlight points at rows already on the chart; any figure is read from the chart's own calculation.";
    default:
      return "The figures in the note, read back from the calculation named above.";
  }
}

/** The queries behind a mark: its own query and inline facts (provenance sources are URLs). */
export function markQueries(mark: Mark): Query[] {
  const out: Query[] = [];
  if (mark.query) out.push(mark.query);
  Object.keys(mark.facts || {}).forEach((k) => {
    const f: any = (mark.facts as any)[k];
    if (f && f.endpoint) out.push({ endpoint: f.endpoint, params: f.params || {} });
  });
  (mark.provenance?.sources || []).forEach((s) => s.url && s.url.startsWith("/") && out.push(parseUrl(s.url) as Query));
  return out;
}

// ------------------------------------------------------------------ one figure

/** The tooltip of one figure: which calculation it is read from. */
export function factOrigin(endpoint: string | undefined, params: Params | undefined, path?: string): string {
  if (!endpoint) return "Read from a calculation";
  const m = methodOf({ endpoint, params });
  const d = dataLine([{ endpoint, params }]).parts.slice(1, -1).join(", ");
  return `From: ${m.name.toLowerCase()}${d ? `, ${d}` : ""}${path ? ` (${path})` : ""}. Not typed by the assistant.`;
}
