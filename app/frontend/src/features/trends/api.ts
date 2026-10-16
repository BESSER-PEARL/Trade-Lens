// SPDX-License-Identifier: MIT
// Data access for the Trends page: talks to the hand-written /trends/* backend endpoints
// (app/backend/trends/), kept separate from the generated dashboard's data bindings.
import axios from "axios";

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

export interface DimensionValue {
  code: string;
  label: string;
  /** Partners: "world", "country", "former" (a former country) or "other" (not a country). */
  group?: string;
  /** Products: the official chapter name, when the label is a short one. */
  full?: string;
}

export interface DimensionMeta {
  id: string;
  label: string;
  kind: "enum" | "class" | "time";
  default?: string;
  default_label?: string;
  constant: boolean;
  not_applicable: boolean;
  values?: DimensionValue[];
}

export interface TrendOptions {
  products: DimensionValue[];
  reporters: DimensionValue[];
  partners: DimensionValue[];
  flow?: DimensionMeta;
  indicators?: DimensionMeta;
  reporter_default?: string;
  partner_default?: string;
  periods: Record<string, string[]>;
  unit: { symbol: string; multiplier: number; label: string };
}

export interface SeriesPoint {
  period: string;
  value: number;
  trend: number;
  resid: number;
  seasonal: number | null;
  adjusted: number | null;
}

export interface Changepoint {
  period: string;
  index: number;
  before_mean: number;
  after_mean: number;
  change: number;
  relative_change: number | null;
}

export type TrendDirection = "increasing" | "decreasing" | "stable" | "volatile";
export type TrendShape = "steady" | "accelerating" | "decelerating" | "reversing";
export type StrengthLabel = "weak" | "moderate" | "strong";

export interface SeasonalPatternPoint {
  position: number;
  label: string;
  average_effect: number;
  average_effect_pct: number;
  tag: string;
}

export interface TrendAnalysis {
  method: "stl" | "moving_average";
  seasonal_period: number | null;
  /** The history the seasonal adjustment was fitted on (the whole slice), cut to start..end. */
  fit_start?: string | null;
  fit_end?: string | null;
  /** "Too short to adjust: ..." when the history is under five years: no seasonal adjustment then. */
  adjustment_note?: string | null;
  direction: TrendDirection;
  shape: TrendShape;
  slope: number;
  /** The trend's last value against its first (0.5 = +50%); null when the trend starts at zero or below. */
  relative_change: number | null;
  trend_start?: number;
  trend_end?: number;
  /** "Over the span the trend rose, ...": the direction and shape in plain words. */
  trend_summary?: string;
  /** The product filter for prose: "goods", "rubber". */
  goods?: string;
  volatility: number;
  trend_strength: number;
  seasonal_strength: number;
  seasonality_label: StrengthLabel | null;
  seasonality_summary: string | null;
  seasonal_pattern: SeasonalPatternPoint[];
  seasonal_peak: SeasonalPatternPoint | null;
  seasonal_trough: SeasonalPatternPoint | null;
  points: SeriesPoint[];
  changepoints: Changepoint[];
  n_points: number;
  start: string;
  end: string;
  fetched_live: boolean;
}

export interface TrendFilters {
  product: string;
  reporter: string;
  partner: string;
  flow?: string;
  indicators?: string;
  freq: string;
  start?: string;
  end?: string;
  penalty: number;
  minSize: number;
}

export async function fetchTrendOptions(): Promise<TrendOptions> {
  const { data } = await axios.get(`${API_BASE}/trends/options`);
  return data;
}

export interface SeasonalHeatmapRow {
  code: string;
  name: string;
  pattern: SeasonalPatternPoint[];
}

export interface SeasonalHeatmap {
  cycle: number;
  rows: SeasonalHeatmapRow[];
}

export async function fetchSeasonalHeatmap(filters: TrendFilters, top = 12): Promise<SeasonalHeatmap> {
  const params: Record<string, string> = {
    reporter: filters.reporter,
    partner: filters.partner,
    freq: filters.freq,
    top: String(top),
  };
  if (filters.flow) params.flow = filters.flow;
  if (filters.indicators) params.indicators = filters.indicators;
  if (filters.start) params.start = filters.start;
  if (filters.end) params.end = filters.end;
  const { data } = await axios.get(`${API_BASE}/trends/seasonal-heatmap`, { params });
  return data;
}

export interface ForecastPoint {
  period: string;
  value: number;
  lo?: number;
  hi?: number;
  low?: number;
  high?: number;
}

export interface ForecastBacktest {
  origins: number;
  first_origin: string;
  last_origin: string;
  mape_h1: number | null;
  mape_h12: number | null;
  naive_mape_h1: number | null;
  naive_mape_h12: number | null;
  period: string | null;
  mape: number | null;
  naive_mape: number | null;
  /** Share of the backtest's actual months inside the band. */
  coverage?: number | null;
}

export interface ForecastScenario {
  id: string;
  case: "stops" | "continues" | string;
  label: string;
  line?: string;
  line_label?: string;
  since?: string;
  rows: ForecastPoint[];
}

export interface ForecastResult {
  method: string;
  method_label: string;
  freq: "M" | "A";
  interval: number;
  horizon: number;
  last_observed: string;
  n_obs: number;
  history: ForecastPoint[];
  forecast: ForecastPoint[];
  reference: ForecastPoint[];
  backtest: ForecastBacktest | null;
  /** Known one-off lines (today the smartphone line): the forecast if it stops or continues (contract 1.3). */
  scenarios?: ForecastScenario[];
  caveat: string;
  loaded: boolean;
  fetched_live: boolean;
  error?: string;
}

export interface ForecastParams {
  horizon: number;        // months ahead (1-36)
  aggregate: "M" | "A";   // output row granularity
  interval: number;       // coverage, 0.5-0.99 (0.8 = 80%)
}

// Reuses the existing /insights/forecast endpoint (STL + damped-trend ETS, simulated
// confidence band, rolling-origin backtest vs. a seasonal-naive baseline) built for the agent
// system's forecast panels — same model, same filters as /trends/analyze.
export async function fetchForecast(filters: TrendFilters, params: ForecastParams): Promise<ForecastResult> {
  const query: Record<string, string> = {
    horizon: String(params.horizon),
    aggregate: params.aggregate,
    interval: String(params.interval),
    reporter: filters.reporter,
    partner: filters.partner,
    product: filters.product,
  };
  if (filters.flow) query.flow = filters.flow;
  if (filters.indicators) query.indicators = filters.indicators;
  const { data } = await axios.get(`${API_BASE}/insights/forecast`, { params: query });
  return data;
}

export async function fetchTrendAnalysis(filters: TrendFilters): Promise<TrendAnalysis> {
  const params: Record<string, string> = {
    product: filters.product,
    reporter: filters.reporter,
    partner: filters.partner,
    freq: filters.freq,
    penalty: String(filters.penalty),
    min_size: String(filters.minSize),
  };
  if (filters.flow) params.flow = filters.flow;
  if (filters.indicators) params.indicators = filters.indicators;
  if (filters.start) params.start = filters.start;
  if (filters.end) params.end = filters.end;
  const { data } = await axios.get(`${API_BASE}/trends/analyze`, { params });
  return data;
}
