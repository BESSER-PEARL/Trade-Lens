// "Model card": how a forecast was made, for the machine-learning reader. A button in the header of every
// panel that shows a forecast (a query to the forecast method, or a forecast band drawn on it) opens it
// under the chart, like "How this was made" (MadeCard.tsx), and in the same style.
//
// Every figure comes from the forecast's own answer (its model_card), never from this file: the members
// and features follow the code, "this series" is the backtest the backend reruns on the series at every
// request (the last 20 quarter ends), and "the test that chose it" is the documented 66-quarter-end
// study. The two tests are always labelled apart. The card reads the chart's own query, so it opens from
// the cache at once; a first open shows "Testing the methods…" while the backend runs the backtest.

import React from "react";
import { useMadeOpen } from "./MadeCard";
import { substituteQuery } from "./context";
import { useQuery } from "./useQuery";
import { usePageContext, useViewStore } from "./ViewStore";
import type { Panel, Query, ViewSpec } from "./types";

const FORECAST = "/insights/forecast";

const openKey = (panelId: string) => `model:${panelId}`;
export const modelCardId = (panelId: string) => `model-card-${panelId}`;

/** The forecast query behind a panel: its own, or that of a forecast band drawn on it. */
export function forecastQueryOf(panel: Panel, spec: ViewSpec | null): Query | null {
  const own = (panel.queries || []).find((q) => q.endpoint === FORECAST);
  if (own) return own;
  const mark = (spec?.marks || []).find((m) => m.target === panel.id && m.kind === "forecast" && m.query?.endpoint === FORECAST);
  return mark?.query || null;
}

// ------------------------------------------------------------------ the toggle

/** The header button, next to "How this was made"; nothing on a panel with no forecast. */
export const ModelCardToggle: React.FC<{ panel: Panel }> = ({ panel }) => {
  const { spec } = useViewStore();
  const [open, set] = useMadeOpen(openKey(panel.id));
  if (!forecastQueryOf(panel, spec)) return null;
  return (
    <button
      type="button"
      className={`cx-pctl-btn${open ? " cx-pctl-btn--on" : ""} cx-made-toggle`}
      aria-expanded={open}
      aria-controls={modelCardId(panel.id)}
      title="The forecasting models, their inputs, how they were tested and where they fail"
      onClick={() => set()}
    >
      <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" focusable="false" style={{ flex: "none" }}>
        <rect x="2" y="2.5" width="12" height="11" rx="1.6" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <path d="M4.6 10.6l2.2-2.4 1.9 1.5 2.7-3.4" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span className="cx-made-toggle-label">Model card</span>
    </button>
  );
};

// ------------------------------------------------------------------ formats

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function month(p?: string | null): string {
  const m = /^(\d{4})-(\d{2})$/.exec(String(p || ""));
  return m ? `${MONTHS[Number(m[2]) - 1]} ${m[1]}` : String(p || "");
}

const num = (x?: number | null, d = 2) => (x === null || x === undefined || !isFinite(x) ? "n/a" : x.toFixed(d));

function pct(x?: number | null): string {
  if (x === null || x === undefined || !isFinite(x)) return "n/a";
  const v = Math.round(100 * x);
  return `${v < 0 ? "−" : ""}${Math.abs(v)}%`;
}

const horizonWord = (h: number) => (h === 1 ? "1 month" : `${h} months`);
const thousands = (n?: number) => (n === undefined ? "" : n.toLocaleString("en-GB"));

// ------------------------------------------------------------------ the answer's shape (model_card)

interface HorizonRow {
  h: number;
  mase: number | null;
  naive_mase: number | null;
  skill: number | null;
  n?: number | null;
}
interface StudyRow {
  key: string;
  name: string;
  mase_h1: number;
  mase_h3: number;
  mase_h6: number;
  mase_h12: number;
  mase: number;
  run: string;
}
interface LiveMethod {
  method: string;
  name: string;
  chosen: boolean;
  mase: number | null;
  mase_h1: number | null;
  mase_h12: number | null;
}
interface ModelCardData {
  method: string;
  members: Array<{ method: string; name: string; detail: string }>;
  combination: string | null;
  target: string | null;
  features: string[];
  training: {
    first: string;
    last: string;
    months: number;
    breaks: string[][];
    pooled?: boolean;
    cutoff?: string;
    pool_series?: number;
    pool_examples?: number;
    window_years?: number;
    gbm_params?: { trees: number; learning_rate: number; max_depth: number; min_samples_leaf: number; l2_regularization: number; loss: string };
  };
  study: {
    origins: number;
    first_origin: string;
    last_origin: string;
    horizon: number;
    series: string;
    forecasts: string;
    skill: HorizonRow[];
    methods: StudyRow[];
    tried: Array<{ key: string; name: string; mase: number }>;
    band_coverage: number;
    band_level: number;
  };
  live: { origins: number; first_origin: string; last_origin: string; horizons: HorizonRow[]; methods: LiveMethod[]; best: string | null; mase: number | null };
  band: { level: number; method: string; coverage: number | null; checks: number | null };
  limits: string[];
}

// ------------------------------------------------------------------ pieces

const SkillTable: React.FC<{ caption: string; rows: HorizonRow[]; who: string; origins?: boolean }> = ({ caption, rows, who, origins }) => (
  <div className="cx-model-tablewrap">
    <table className="cx-model-table">
      <caption>{caption}</caption>
      <thead>
        <tr>
          <th scope="col">Horizon</th>
          <th scope="col">{who} MASE</th>
          <th scope="col">Seasonal naive</th>
          <th scope="col">Skill</th>
          {origins && <th scope="col">Origins scored</th>}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.h}>
            <th scope="row">{horizonWord(r.h)}</th>
            <td>{num(r.mase)}</td>
            <td>{num(r.naive_mase)}</td>
            <td className="cx-model-skill">{pct(r.skill)}</td>
            {origins && <td>{r.n ?? "n/a"}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

const STUDY_SHOWN = ["ensemble", "ets", "gbm", "theta", "airline", "stl_ets", "nn_mlp", "nn_gru", "nn_mlp_local", "ensemble_nn", "snaive"];

const OthersTable: React.FC<{ rows: StudyRow[] }> = ({ rows }) => {
  const shown = STUDY_SHOWN.map((k) => rows.find((r) => r.key === k)).filter(Boolean) as StudyRow[];
  return (
    <div className="cx-model-tablewrap">
      <table className="cx-model-table cx-model-table--wide">
        <caption>MASE in the test that chose it (66 quarter ends)</caption>
        <thead>
          <tr>
            <th scope="col">Method</th>
            <th scope="col">1 month</th>
            <th scope="col">12 months</th>
            <th scope="col">Average</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((r) => (
            <tr key={r.key} className={r.key === "ensemble" ? "cx-model-chosen" : undefined}>
              <th scope="row">{r.name.charAt(0).toUpperCase() + r.name.slice(1)}</th>
              <td>{num(r.mase_h1)}</td>
              <td>{num(r.mase_h12)}</td>
              <td>{num(r.mase)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

// ------------------------------------------------------------------ the card

export const ModelCardBody: React.FC<{ card: ModelCardData }> = ({ card }) => {
  const { training: tr, study: st, live, band } = card;
  const ensemble = card.method === "ensemble";
  const who = ensemble ? "Ensemble" : "Method";
  const rows = Object.fromEntries(st.methods.map((r) => [r.key, r])) as Record<string, StudyRow>;
  const mlp = rows.nn_mlp;
  const naiveNn = rows.snaive_nn;
  const ensNn = rows.ensemble_nn;
  const chosen = live.methods.find((m) => m.method === card.method);
  const best = live.methods.find((m) => m.method === live.best);
  const liveSorted = [...live.methods].filter((m) => m.mase !== null).sort((a, b) => (a.mase as number) - (b.mase as number));
  const p = tr.gbm_params;
  const hasGbm = card.members.some((m) => m.method === "gbm");

  return (
    <dl className="cx-made-rows cx-model-rows">
      <dt>Models</dt>
      <dd>
        {card.members.map((m) => (
          <span key={m.method} className="cx-made-method">
            <b>{m.name.charAt(0).toUpperCase() + m.name.slice(1)}:</b> {m.detail}.
          </span>
        ))}
        {card.combination && <span className="cx-made-method">The forecast is {card.combination}: the two models make different mistakes, so their average beats both.</span>}
      </dd>

      <dt>Inputs</dt>
      <dd>
        {card.members.some((m) => m.method === "ets") && <span className="cx-made-method">Holt-Winters reads only the series itself, month by month.</span>}
        {hasGbm && card.features.length > 0 && (
          <>
            <span className="cx-made-method">
              Gradient boosting predicts {card.target} from {card.features.length} scale-free features, computed only from months up to the forecast origin:
            </span>
            <ul className="cx-model-list">
              {card.features.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          </>
        )}
      </dd>

      <dt>Trained on</dt>
      <dd>
        <span className="cx-made-method">
          This series from {month(tr.first)} to {month(tr.last)} ({tr.months} months), refitted whenever a month is added.
        </span>
        {hasGbm && tr.pooled && (
          <span className="cx-made-method">
            Gradient boosting is pooled: one model for {tr.pool_series} monthly series (every HS2 chapter of exports and imports), {thousands(tr.pool_examples)} (origin,
            horizon) examples from the last {tr.window_years} years. It is retrained once a year; the one in use was trained on the data up to {month(tr.cutoff)}.
          </span>
        )}
        {hasGbm && tr.pooled === false && <span className="cx-made-method">Gradient boosting is trained on this series alone (the pool of chapters is not available).</span>}
        {p && (
          <span className="cx-made-method cx-model-tech">
            {p.trees} trees, learning rate {p.learning_rate}, depth {p.max_depth}, at least {p.min_samples_leaf} examples per leaf, L2 {p.l2_regularization}, {p.loss} loss.
          </span>
        )}
        {tr.breaks.length > 0 && (
          <span className="cx-made-method">
            The COVID months ({tr.breaks.map((b) => `${month(b[0])} to ${month(b[1])}`).join(", ")}) are replaced for training by the same months a year earlier, grown at
            the pace just before.
          </span>
        )}
      </dd>

      <dt>Tested how</dt>
      <dd>
        <span className="cx-made-method">
          Rolling origin: at each quarter end every method is refitted on the months up to it only, forecasts the next {st.horizon} months and is scored against what
          happened. The score is MASE, the error divided by the in-sample error of the seasonal naive forecast (the same month a year earlier), so 1.0 is as good as
          the naive forecast and lower is better.
        </span>
        <span className="cx-made-method">
          <b>The test that chose the method:</b> {st.origins} quarter ends from {month(st.first_origin)} to {month(st.last_origin)}, on {st.series} ({st.forecasts}).
        </span>
        <span className="cx-made-method">
          <b>This series:</b> rerun at every request on its last {live.origins} quarter ends, {month(live.first_origin)} to {month(live.last_origin)}.
        </span>
      </dd>

      <dt>Skill by horizon</dt>
      <dd>
        <span className="cx-made-method">Skill is how much smaller the error is than the seasonal naive one: 1 minus the ratio of the two MASE.</span>
        {ensemble && (
          <SkillTable caption={`The test that chose it: ${st.origins} quarter ends, ${month(st.first_origin)} to ${month(st.last_origin)}`} rows={st.skill} who={who} />
        )}
        <SkillTable caption={`This series: the last ${live.origins} quarter ends, ${month(live.first_origin)} to ${month(live.last_origin)}`} rows={live.horizons} who={who} origins />
        <span className="cx-made-method cx-model-note">
          Fewer origins further ahead: the last quarter ends have no 12 months of actual data after them yet.
          {ensemble && best && chosen && best.method !== card.method && best.mase !== null && chosen.mase !== null
            ? ` On this series ${best.name} did a little better on average over these ${live.origins} quarter ends (MASE ${num(best.mase)} against ${num(
                chosen.mase,
              )}); the ensemble stays the default because it won the longer test.`
            : ""}
        </span>
      </dd>

      <dt>Band</dt>
      <dd>
        <span className="cx-made-method">
          {band.method.charAt(0).toUpperCase() + band.method.slice(1)}, {pct(band.level)}: at each horizon the band reaches, on each side of the forecast, the {pct(band.level)} quantile
          (finite-sample) of this series' own absolute backtest errors, and it never narrows further ahead. No assumption about the shape of the errors.
        </span>
        {band.coverage !== null && (
          <span className="cx-made-method">
            Checked forward in time (each origin's band calibrated only on the errors known then), it held <b>{pct(band.coverage)}</b> of {band.checks} forecast months on
            this series.{ensemble ? ` In the test that chose it, the ${pct(st.band_level)} band held ${pct(st.band_coverage)} on the totals.` : ""}
          </span>
        )}
      </dd>

      <dt>Other methods tried</dt>
      <dd>
        <OthersTable rows={st.methods} />
        {mlp && naiveNn && (
          <span className="cx-made-method">
            <b>The neural networks are a BESSER model:</b> an MLP and a GRU described with BESSER's neural-network metamodel and generated into PyTorch code by a BESSER
            generator, trained on the same pool of chapters. The MLP beats the seasonal naive forecast at short horizons ({num(mlp.mase_h1)} against {num(naiveNn.mase_h1)}{" "}
            at 1 month) but not at 12 months ({num(mlp.mase_h12)} against {num(naiveNn.mase_h12)}), and not the ensemble ({num(mlp.mase)} against{" "}
            {num(rows.ensemble?.mase)} on average)
            {ensNn ? `; as a third member it does not help (${num(ensNn.mase)})` : ""}. It is offered as a separate method, not the default.
          </span>
        )}
        {st.tried.length > 0 && (
          <span className="cx-made-method">
            Also tried, average MASE: {st.tried.map((t) => `${t.name} ${num(t.mase)}`).join("; ")}.
          </span>
        )}
        {liveSorted.length > 1 && (
          <span className="cx-made-method">
            On this series, average MASE over its last {live.origins} quarter ends: {liveSorted.map((m) => `${m.name} ${num(m.mase)}`).join(", ")}.
          </span>
        )}
      </dd>

      <dt>Limits</dt>
      <dd>
        <ul className="cx-model-list cx-model-limits">
          {card.limits.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      </dd>
    </dl>
  );
};

/** The card of one panel, open while its toggle is (drawn by Panel under the chart). */
export const PanelModelCard: React.FC<{ panel: Panel }> = ({ panel }) => {
  const { spec } = useViewStore();
  const ctx = usePageContext();
  const [open, set] = useMadeOpen(openKey(panel.id));
  const q = forecastQueryOf(panel, spec);
  // The chart's own query, so the answer is already in the cache.
  const state = useQuery(open && q ? substituteQuery(q, ctx) : null);
  if (!open || !q) return null;
  const id = modelCardId(panel.id);
  const card: ModelCardData | undefined = state.data?.model_card;
  return (
    <section id={id} className="cx-made cx-model" aria-labelledby={`${id}-head`} aria-busy={state.loading || undefined}>
      <div className="cx-made-title">
        <h4 id={`${id}-head`}>Model card</h4>
        <button type="button" className="cx-made-close" onClick={() => set(false)}>
          Close
        </button>
      </div>
      {card ? (
        <ModelCardBody card={card} />
      ) : state.error ? (
        <p className="cx-made-tech" role="alert">
          The forecast could not be tested right now: {state.error.message || "the backend did not answer"}.
        </p>
      ) : state.loading || !state.data ? (
        <p className="cx-model-loading" role="status">
          <span className="cx-model-spinner" aria-hidden="true" />
          Testing the methods…
        </p>
      ) : (
        <p className="cx-made-tech">This forecast came without a model card; the backend may be an older version.</p>
      )}
    </section>
  );
};
