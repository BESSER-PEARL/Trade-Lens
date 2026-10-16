// SPDX-License-Identifier: MIT
import React, { useEffect, useRef, useState } from "react";
import {
  fetchForecast, fetchSeasonalHeatmap, fetchTrendAnalysis, fetchTrendOptions,
  type ForecastParams, type ForecastResult,
  type SeasonalHeatmap as SeasonalHeatmapData, type TrendAnalysis, type TrendFilters, type TrendOptions,
} from "./api";
import { ChangepointsTable } from "./components/ChangepointsTable";
import { FilterPanel } from "./components/FilterPanel";
import { ForecastPanel } from "./components/ForecastPanel";
import { SeasonalHeatmap } from "./components/SeasonalHeatmap";
import { SeasonalityPanel } from "./components/SeasonalityPanel";
import { Segmented } from "./components/Segmented";
import { SummaryPanel } from "./components/SummaryPanel";
import { TrendChart } from "./components/TrendChart";
import { periodLabel, sentenceCase } from "./format";
import "./trends.css";

const DEBOUNCE_MS = 350;

// The change-point settings of the trend finder (agents' analyze_trend: penalty 1.5, regimes of at least
// 12 months), so the Trends tab opens on the same breaks as the Overview's trend.
const TREND_FINDER = { penalty: 1.5, minSize: 12 };

const errorText = (err: any, fallback: string): string => err?.response?.data?.detail ?? err?.message ?? fallback;

const lowerFirst = (s: string) => (/^[A-Z][a-z]/.test(s) ? s.charAt(0).toLowerCase() + s.slice(1) : s);

/** "Luxembourg's exports of all goods to the world": what the selected series is, in words. */
function subjectOf(options: TrendOptions | null, filters: TrendFilters | null): string {
  if (!options || !filters) return "The selected series";
  const reporter = sentenceCase(options.reporters.find((r) => r.code === filters.reporter)?.label ?? filters.reporter);
  const flowCode = (filters.flow || "").toUpperCase();
  const flowValue = options.flow?.values?.find((v) => v.code === filters.flow)?.label;
  const flow = flowCode.startsWith("EXP") ? "exports" : flowCode.startsWith("IMP") ? "imports" : (flowValue || "trade").toLowerCase();
  // A chapter name up to its first semicolon: "electrical machinery and equipment and parts thereof".
  const productLabel = sentenceCase(options.products.find((p) => p.code === filters.product)?.label ?? filters.product).split(";")[0];
  const product = filters.product === "TOTAL" ? "all goods" : lowerFirst(productLabel);
  const partnerLabel = options.partners.find((p) => p.code === filters.partner)?.label ?? filters.partner;
  const partner = filters.partner === "WORLD" || /^world/i.test(partnerLabel) ? "the world" : sentenceCase(partnerLabel);
  const prep = flow === "imports" ? "from" : "to";
  return `${reporter}'s ${flow} of ${product} ${prep} ${partner}`;
}

const TrendsPage: React.FC = () => {
  const [options, setOptions] = useState<TrendOptions | null>(null);
  const [filters, setFilters] = useState<TrendFilters | null>(null);
  const [lastPeriods, setLastPeriods] = useState<Record<string, string>>({});
  const [analysis, setAnalysis] = useState<TrendAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [showAdjusted, setShowAdjusted] = useState(false);
  const [heatmap, setHeatmap] = useState<SeasonalHeatmapData | null>(null);
  const [heatmapLoading, setHeatmapLoading] = useState(false);
  const [heatmapError, setHeatmapError] = useState<string | null>(null);
  const [forecastParams, setForecastParams] = useState<ForecastParams>({ horizon: 12, aggregate: "M", interval: 0.8 });
  const [forecast, setForecast] = useState<ForecastResult | null>(null);
  const [forecastLoading, setForecastLoading] = useState(false);
  const [forecastError, setForecastError] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const heatmapDebounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const forecastDebounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // The filters the shown analysis answers, and a counter so a slow, stale answer never lands.
  const analysedKey = useRef("");
  const analysisRequest = useRef(0);
  const heatmapRequest = useRef(0);
  const forecastRequest = useRef(0);
  const optionsRef = useRef<TrendOptions | null>(null);

  useEffect(() => {
    fetchTrendOptions()
      .then((opts) => {
        optionsRef.current = opts;
        setOptions(opts);
        const freq = opts.periods.M ? "M" : Object.keys(opts.periods)[0];
        const span = opts.periods[freq] ?? [];
        setFilters({
          product: "TOTAL",
          reporter: opts.reporter_default ?? opts.reporters[0]?.code,
          partner: opts.partner_default ?? "WORLD",
          flow: opts.flow?.default,
          indicators: opts.indicators?.default,
          freq,
          start: span[0],
          end: span[span.length - 1],
          penalty: TREND_FINDER.penalty,
          minSize: TREND_FINDER.minSize,
        });
      })
      .catch((err) => setOptionsError(err?.message ?? "Could not load the filter options"));
  }, []);

  useEffect(() => {
    if (!filters) return;
    const key = JSON.stringify(filters);
    if (key === analysedKey.current) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      const id = ++analysisRequest.current;
      setLoading(true);
      setError(null);
      fetchTrendAnalysis(filters)
        .then((result) => {
          if (id !== analysisRequest.current) return;
          analysedKey.current = key;
          setAnalysis(result);
          // The period list of /trends/options ends with the headline series' last period (meta
          // trims months that only some partners have), but a filtered series can end earlier. When
          // the analysis of the full range ends earlier, that is the last period with data: cut the
          // list there and move To onto it, without a refetch.
          const span = optionsRef.current?.periods[filters.freq] ?? [];
          const listedLast = span[span.length - 1];
          if (filters.end && filters.end === listedLast && result.end && result.end.length === filters.end.length && result.end < filters.end) {
            const last = result.end;
            setLastPeriods((prev) => ({ ...prev, [filters.freq]: last }));
            const next = { ...filters, end: last };
            analysedKey.current = JSON.stringify(next);
            setFilters((prev) => (prev && JSON.stringify(prev) === key ? next : prev));
          }
        })
        .catch((err) => {
          if (id !== analysisRequest.current) return;
          setAnalysis(null);
          analysedKey.current = "";
          setError(errorText(err, "The trend analysis failed"));
        })
        .finally(() => { if (id === analysisRequest.current) setLoading(false); });
    }, DEBOUNCE_MS);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [filters]);

  useEffect(() => {
    if (!filters || filters.freq === "A") {
      setHeatmap(null);
      return;
    }
    if (heatmapDebounceRef.current) clearTimeout(heatmapDebounceRef.current);
    heatmapDebounceRef.current = setTimeout(() => {
      const id = ++heatmapRequest.current;
      setHeatmapLoading(true);
      setHeatmapError(null);
      fetchSeasonalHeatmap(filters)
        .then((data) => { if (id === heatmapRequest.current) setHeatmap(data); })
        .catch((err) => {
          if (id !== heatmapRequest.current) return;
          setHeatmap(null);
          setHeatmapError(errorText(err, "Could not load the seasons of the main products"));
        })
        .finally(() => { if (id === heatmapRequest.current) setHeatmapLoading(false); });
    }, DEBOUNCE_MS);
    return () => { if (heatmapDebounceRef.current) clearTimeout(heatmapDebounceRef.current); };
    // The heatmap compares products, so the product filter and the change-point settings do not change it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters?.reporter, filters?.partner, filters?.flow, filters?.indicators, filters?.freq, filters?.start, filters?.end]);

  useEffect(() => {
    if (!filters) return;
    if (forecastDebounceRef.current) clearTimeout(forecastDebounceRef.current);
    forecastDebounceRef.current = setTimeout(() => {
      const id = ++forecastRequest.current;
      setForecastLoading(true);
      setForecastError(null);
      fetchForecast(filters, forecastParams)
        .then((data) => { if (id === forecastRequest.current) setForecast(data); })
        .catch((err) => {
          if (id !== forecastRequest.current) return;
          setForecast(null);
          setForecastError(errorText(err, "The forecast failed"));
        })
        .finally(() => { if (id === forecastRequest.current) setForecastLoading(false); });
    }, DEBOUNCE_MS);
    return () => { if (forecastDebounceRef.current) clearTimeout(forecastDebounceRef.current); };
    // Only the "what" filters (product/reporter/partner/flow/indicators) should refetch the
    // forecast — period range and penalty are analyze-only controls; forecastParams is its own
    // dependency so the horizon/aggregate toggles refetch without waiting on filters to change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters?.product, filters?.reporter, filters?.partner, filters?.flow, filters?.indicators, forecastParams]);

  const subject = subjectOf(options, filters);
  const unit = options?.unit.symbol || "€";
  const flow = filters?.flow;

  return (
    <div className="tr-page">
      <main className="tr-main">
        <header className="tr-header">
          <h1 className="tr-title">Trends in Luxembourg's trade</h1>
          <p className="tr-lede">
            Pick a series by product, partner, flow and period. The analysis separates the trend from the
            season and marks where the level of the series shifted.
          </p>
          {optionsError && <p className="tr-error" role="alert">Could not load the filters: {optionsError}</p>}
          {options && filters && (
            <FilterPanel options={options} filters={filters} lastPeriods={lastPeriods} onChange={setFilters} status={loading ? "Analysing…" : ""} />
          )}
          {!options && !optionsError && <p className="tr-status" role="status">Loading the filters…</p>}
          {error && <p className="tr-error" role="alert">{error}</p>}
        </header>

        {analysis && (
          <>
            <div className="tr-busy" aria-busy={loading}>
              <SummaryPanel analysis={analysis} unitSymbol={unit} />
            </div>

            <section className="tr-section tr-busy" aria-labelledby="tr-series" aria-busy={loading}>
              <div className="tr-section-head">
                <div>
                  <h2 id="tr-series" className="tr-section-title">The series and its trend</h2>
                  <p className="tr-section-sub">
                    {subject}, {periodLabel(analysis.start)} to {periodLabel(analysis.end)}{unit === "€" ? ", in euros" : ""}
                  </p>
                </div>
                {analysis.seasonal_period && (
                  <Segmented
                    label="Show"
                    value={showAdjusted ? "adjusted" : "raw"}
                    options={[{ label: "As recorded", value: "raw" }, { label: "Seasonally adjusted", value: "adjusted" }]}
                    onChange={(v) => setShowAdjusted(v === "adjusted")}
                  />
                )}
              </div>
              <TrendChart analysis={analysis} showAdjusted={showAdjusted} flow={flow} unit={unit} subject={subject} />
              {showAdjusted && analysis.adjustment_note && (
                <p className="tr-quiet" role="status" style={{ color: "inherit" }}>
                  {analysis.adjustment_note} The series is shown as recorded.
                </p>
              )}
              <p className="tr-quiet">
                {showAdjusted && analysis.seasonal_period && !analysis.adjustment_note
                  ? "The repeating yearly pattern is taken out, so a real change stands apart from an ordinary high or low season. It is learnt from the whole monthly history, not only the months shown. "
                  : ""}
                The trend is the series with the {analysis.method === "stl" ? "season and the one-off spikes" : "ups and downs"} smoothed
                out; the numbered rules are the change points listed below.
              </p>
              <details className="tr-detail">
                <summary>For statisticians</summary>
                <p>
                  {analysis.method === "stl"
                    ? `Trend: STL decomposition (statsmodels, robust, period ${analysis.seasonal_period ?? 12}) into trend, seasonal and remainder, fitted on the slice's whole history${
                        analysis.fit_start ? ` (${analysis.fit_start} to ${analysis.fit_end})` : ""
                      } and cut to the span shown; adjusted = value − seasonal. Needs five years of history.`
                    : "Trend: a centred moving average (the series is annual, or under five years of history for STL)."}{" "}
                  Direction: the trend's last value against its first; volatile when the remainder's spread exceeds 35% of the
                  trend's mean level; stable within ±5%. Shape: the slopes of the trend's two halves compared.
                </p>
              </details>
            </section>

            <section className="tr-section tr-busy" aria-labelledby="tr-breaks" aria-busy={loading}>
              <div className="tr-section-head">
                <div>
                  <h2 id="tr-breaks" className="tr-section-title">Change points</h2>
                  <p className="tr-section-sub">Where the average level of the series shifted and stayed shifted</p>
                </div>
              </div>
              <ChangepointsTable changepoints={analysis.changepoints} unitSymbol={unit} />
              <p className="tr-quiet">
                {filters && filters.penalty === TREND_FINDER.penalty && filters.minSize === TREND_FINDER.minSize
                  ? "The same settings as the trend finder on the Overview (sensitivity 1.5, at least 12 months between breaks), so both pages mark the same breaks."
                  : `The trend finder on the Overview uses sensitivity 1.5; at ${filters?.penalty} this page marks ${filters && filters.penalty > TREND_FINDER.penalty ? "fewer" : "more"} breaks than it does.`}
              </p>
            </section>

            <section className="tr-section tr-busy" aria-labelledby="tr-season" aria-busy={loading}>
              <div className="tr-section-head">
                <div>
                  <h2 id="tr-season" className="tr-section-title">Seasonality</h2>
                  <p className="tr-section-sub">The typical shape of a year, averaged over every year in the period</p>
                </div>
              </div>
              <SeasonalityPanel analysis={analysis} flow={flow} />
            </section>

            <section className="tr-section" aria-labelledby="tr-heat">
              <div className="tr-section-head">
                <div>
                  <h2 id="tr-heat" className="tr-section-title">Seasons of the main products</h2>
                  <p className="tr-section-sub">The twelve largest product chapters for the same reporter, partner and flow</p>
                </div>
              </div>
              <SeasonalHeatmap data={heatmap} loading={heatmapLoading} error={heatmapError} flow={flow} />
            </section>

            <section className="tr-section" aria-labelledby="tr-forecast">
              <div className="tr-section-head">
                <div>
                  <h2 id="tr-forecast" className="tr-section-title">Forecast</h2>
                  <p className="tr-section-sub">
                    A statistical model fitted on the full monthly history of the selection. The period above does not change
                    it: the model needs at least three years of months.
                  </p>
                </div>
              </div>
              <ForecastPanel
                forecast={forecast}
                loading={forecastLoading}
                error={forecastError}
                params={forecastParams}
                onParamsChange={setForecastParams}
                unitSymbol={unit}
                flow={flow}
                subject={subject}
              />
            </section>
          </>
        )}

        <div className="tr-footer">
          <p>
            The trend smooths out the season and one-off spikes. A change point marks where the average level of the
            series shifted and stayed shifted: it says where, not why.
          </p>
          <details className="tr-detail">
            <summary>For statisticians</summary>
            <p>
              Trend: STL decomposition (statsmodels, robust) fitted on the whole monthly or quarterly history and cut to the span
              shown (five years of history at least), a centred moving average otherwise.
              Change points: a PELT search with an rbf cost (ruptures) on the span's own standardised values, at the sensitivity
              (penalty) and minimum run length set above. It flags candidate shifts in level; it does not explain their
              cause.{forecast?.method_label ? ` Forecast: ${forecast.method_label}.` : ""}
            </p>
          </details>
        </div>
      </main>
    </div>
  );
};

export default TrendsPage;
