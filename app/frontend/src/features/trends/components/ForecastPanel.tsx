// SPDX-License-Identifier: MIT
import React, { useEffect, useMemo, useState } from "react";
import type { ForecastParams, ForecastResult, ForecastScenario } from "../api";
import { money, periodLabel } from "../format";
import { ERROR_INK, TOKENS, flowTone } from "../styles";
import { LineChart, type ChartBand, type ChartSeries } from "./LineChart";
import { Segmented } from "./Segmented";

interface Props {
  forecast: ForecastResult | null;
  loading: boolean;
  error: string | null;
  params: ForecastParams;
  onParamsChange: (next: ForecastParams) => void;
  unitSymbol?: string;
  flow?: string;
  subject: string;
}

/** Recent history shown by default on a monthly forecast, so the forecast months stay readable. */
const RECENT_MONTHS = 72;
const RECENT_MONTHS_NARROW = 36;

function buildChart(forecast: ForecastResult, fullHistory: boolean, showReference: boolean, recent: number, flow?: string) {
  const tone = flowTone(flow);
  const monthly = forecast.history.length > 0 && /^\d{4}-\d{2}$/.test(forecast.history[0].period);
  const history = !fullHistory && monthly ? forecast.history.slice(-recent) : forecast.history;
  const h = history.length;
  const periods = history.map((p) => p.period).concat(forecast.forecast.map((p) => p.period));
  const nulls = (k: number) => new Array<number | null>(k).fill(null);
  const lastValue = h ? history[h - 1].value : null;

  const observed = history.map((p) => p.value as number | null).concat(nulls(forecast.forecast.length));
  // The forecast line starts on the last observed value so the two lines touch.
  const ahead = nulls(Math.max(0, h - 1)).concat(h ? [lastValue] : []).concat(forecast.forecast.map((p) => p.value));
  const lo = nulls(Math.max(0, h - 1)).concat(h ? [lastValue] : []).concat(forecast.forecast.map((p) => p.low ?? p.lo ?? null));
  const hi = nulls(Math.max(0, h - 1)).concat(h ? [lastValue] : []).concat(forecast.forecast.map((p) => p.high ?? p.hi ?? null));
  const refBy: Record<string, number> = {};
  forecast.reference.forEach((r) => { refBy[r.period] = r.value; });
  const reference = nulls(h).concat(forecast.forecast.map((p) => (typeof refBy[p.period] === "number" ? refBy[p.period] : null)));

  const series: ChartSeries[] = [
    { key: "observed", label: tone.name, color: tone.fill, text: tone.text, width: 1.5, values: observed, endLabel: false },
    { key: "forecast", label: "Forecast", color: tone.fill, text: tone.text, width: 2.5, dash: "6 4", values: ahead },
  ];
  const forecastLine = series[1];
  if (showReference) {
    series.push({ key: "reference", label: "A year earlier", color: TOKENS.ink, text: TOKENS.ink, width: 1.5, dash: "2 3", values: reference });
  }
  // The scenarios (contract 1.3): the one-off smartphone line stops or continues. Drawn as the Overview's band
  // draws them, in the forecaster's ink, from the last observed value to their own end value.
  const scenarios: ChartSeries[] = (forecast.scenarios || [])
    .filter((s) => (s.case === "stops" || s.case === "continues") && Array.isArray(s.rows) && s.rows.length)
    .map((s) => {
      const by: Record<string, number> = {};
      s.rows.forEach((r) => { if (typeof r.value === "number") by[r.period] = r.value; });
      const values = nulls(Math.max(0, h - 1)).concat(h ? [lastValue] : []).concat(forecast.forecast.map((p) => (typeof by[p.period] === "number" ? by[p.period] : null)));
      return {
        key: `scenario_${s.case}`,
        label: scenarioLabel(s),
        color: SCENARIO_PEN,
        text: SCENARIO_INK,
        width: 1.5,
        dash: s.case === "stops" ? "2 3" : "8 3 2 3",
        values,
      };
    })
    .filter((s) => s.values.slice(h).some((v) => typeof v === "number"));
  series.push(...scenarios);
  // With scenarios the model's own line reads the one-off line as part of the series: say so on the line itself.
  if (scenarios.length) forecastLine.label = CENTRAL_LABEL;
  const band: ChartBand = { lo, hi, color: tone.fill, label: `${Math.round(forecast.interval * 100)}% likely range` };
  return { periods, series, band, zoneFrom: h - 1, monthly, scenarios };
}

/** The forecaster's pen and ink (view/tokens.ts AGENTS.forecaster): the scenarios are the forecaster's, as on the Overview. */
const SCENARIO_PEN = "#C17F12";
const SCENARIO_INK = "#8A5A12";

/** The model's own forecast when scenarios are drawn: it assumes the recent rise goes on (as the agents word it). */
const CENTRAL_LABEL = "Forecast, if the recent rise goes on";

/** "If smartphones stop" / "If smartphones stay at their recent level", as on the Overview's band. */
function scenarioLabel(s: ForecastScenario): string {
  const line = String(s.line || "").replace(/_/g, " ");
  if (line && s.case === "stops") return `If ${line} stop`;
  if (line && s.case === "continues") return `If ${line} stay at their recent level`;
  return s.label || s.id;
}

/** "2025-09" in a sentence from the backend -> "September 2025". */
const monthsInWords = (text: string) => text.replace(/\b\d{4}-(0[1-9]|1[0-2])\b/g, (m) => periodLabel(m, true));

/** The method against repeating the same month a year earlier, honestly: a gap under 5% of the naive error is a tie. */
function verdict(mape: number, naive: number): string {
  const gain = naive > 0 ? (naive - mape) / naive : 0;
  if (gain >= 0.05) return "Beats the naive guess.";
  if (gain <= -0.05) return "Does worse than the naive guess.";
  return "About the same as the naive guess.";
}

export const ForecastPanel: React.FC<Props> = ({ forecast, loading, error, params, onParamsChange, unitSymbol = "€", flow, subject }) => {
  const [showReference, setShowReference] = useState(false);
  const [fullHistory, setFullHistory] = useState(false);
  const [narrow, setNarrow] = useState(() => typeof window !== "undefined" && window.matchMedia("(max-width: 560px)").matches);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 560px)");
    const on = () => setNarrow(mq.matches);
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  const recent = narrow ? RECENT_MONTHS_NARROW : RECENT_MONTHS;
  const chart = useMemo(
    () => (forecast && forecast.loaded && forecast.forecast.length ? buildChart(forecast, fullHistory, showReference, recent, flow) : null),
    [forecast, fullHistory, showReference, recent, flow],
  );

  const controls = (
    <div className="tr-tools">
      <Segmented
        label="Horizon"
        value={params.horizon}
        options={[6, 12, 24].map((h) => ({ label: `${h} months`, value: h }))}
        onChange={(horizon) => onParamsChange({ ...params, horizon })}
      />
      <Segmented
        label="Steps"
        value={params.aggregate}
        options={[{ label: "Monthly", value: "M" as const }, { label: "Annual", value: "A" as const }]}
        onChange={(aggregate) => onParamsChange({ ...params, aggregate })}
      />
      <label className="tr-check">
        <input type="checkbox" checked={showReference} onChange={(e) => setShowReference(e.target.checked)} />
        Compare with the same period a year earlier
      </label>
      {(!chart || chart.monthly) && (
        <label className="tr-check">
          <input type="checkbox" checked={fullHistory} onChange={(e) => setFullHistory(e.target.checked)} />
          Show the full history
        </label>
      )}
    </div>
  );

  if (loading && !forecast) {
    return <>{controls}<p className="tr-status" role="status">Fitting the forecast model…</p></>;
  }
  if (error) {
    return <>{controls}<p className="tr-error" role="alert">{error}</p></>;
  }
  if (!forecast || !forecast.loaded || !chart) {
    return <>{controls}<p className="tr-quiet">Not enough consecutive monthly history for a forecast: it needs at least 36 months.</p></>;
  }

  const bt = forecast.backtest;
  const h12 = bt && typeof bt.mape_h12 === "number" && typeof bt.naive_mape_h12 === "number" ? { m: bt.mape_h12, n: bt.naive_mape_h12 } : null;
  const h1 = bt && typeof bt.mape_h1 === "number" && typeof bt.naive_mape_h1 === "number" ? { m: bt.mape_h1, n: bt.naive_mape_h1 } : null;
  const pct1 = (v: number) => `${(v * 100).toFixed(1)}%`;
  const coverage = bt && typeof bt.coverage === "number" ? bt.coverage : null;
  const last = forecast.forecast[forecast.forecast.length - 1];
  const aria =
    `Forecast of ${subject} after ${periodLabel(forecast.last_observed, true)}: ` +
    `${money(last.value, unitSymbol)} in ${periodLabel(last.period, true)}, likely between ${money(last.low ?? last.lo, unitSymbol)} and ${money(last.high ?? last.hi, unitSymbol)}.`;

  return (
    <>
      {controls}
      <div className="tr-busy" aria-busy={loading}>
        <LineChart
          periods={chart.periods}
          series={chart.series}
          band={chart.band}
          zoneFrom={chart.zoneFrom}
          unit={unitSymbol}
          height={340}
          ariaLabel={aria}
        />
      </div>
      <ul className="tr-key" aria-label="Key">
        <li><span aria-hidden="true" style={{ width: 18, borderTop: `2px solid ${chart.series[0].color}` }} />Observed</li>
        <li><span aria-hidden="true" style={{ width: 18, borderTop: `2.5px dashed ${chart.series[1].color}` }} />{chart.series[1].label}</li>
        <li><span aria-hidden="true" style={{ width: 18, height: 10, background: chart.band.color, opacity: 0.25 }} />{chart.band.label}</li>
        {showReference && <li><span aria-hidden="true" style={{ width: 18, borderTop: `2px dotted ${TOKENS.ink}` }} />Same period a year earlier</li>}
        {chart.scenarios.map((s) => (
          <li key={s.key}><span aria-hidden="true" style={{ width: 18, borderTop: `2px ${s.key.endsWith("stops") ? "dotted" : "dashed"} ${s.color}` }} />{s.label}</li>
        ))}
      </ul>
      {chart.scenarios.length > 0 && (
        <p className="tr-quiet">
          The forecast line assumes the recent rise goes on: it partly repeats the peak of a one-off product line, the
          smartphone line. The two scenarios, if the smartphone line stops or stays at its recent level, are the honest
          range for the months ahead.
        </p>
      )}
      {bt && (
        <p className="tr-text">
          <strong>{h12 ? verdict(h12.m, h12.n) : "No test on the past."}</strong>{" "}
          {h12 && (
            <span style={{ color: TOKENS.ink2 }}>
              Twelve months ahead it was off by {pct1(h12.m)} on average, against {pct1(h12.n)} for repeating the same
              month a year earlier.
              {h1 && h1.m < h1.n * 0.95 ? ` One month ahead it does better: ${pct1(h1.m)} against ${pct1(h1.n)}.` : ""}
            </span>
          )}
        </p>
      )}
      <p className="tr-quiet">
        {coverage !== null
          ? `The shaded band held ${Math.round(coverage * 100)}% of the actual months when the model was tested on the past of this series. `
          : `The shaded band is where the model expects ${Math.round(forecast.interval * 10)} months in 10 to fall. `}
        It does not cover breaks the past does not show, such as a new tariff or a lost customer.
      </p>
      {forecast.caveat && (
        <details className="tr-detail">
          <summary>For statisticians</summary>
          <p>
            {forecast.method_label ? `Method: ${forecast.method_label}. ` : ""}
            {monthsInWords(forecast.caveat)}
          </p>
        </details>
      )}
      {forecast.error && <p className="tr-error" style={{ color: ERROR_INK }}>{forecast.error}</p>}
    </>
  );
};
