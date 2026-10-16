// SPDX-License-Identifier: MIT
import React, { useState } from "react";
import type { SeasonalPatternPoint, TrendAnalysis } from "../api";
import { percent, signedPct } from "../format";
import { READOUT_STYLE, TOKENS, flowTone } from "../styles";
import { useWidth } from "./LineChart";

interface Props {
  analysis: TrendAnalysis;
  flow?: string;
}

const f = (v: number) => Math.round(v * 10) / 10;

function niceStep(span: number, count: number): number {
  if (!(span > 0)) return 1;
  const raw = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  return (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
}

const LONG_NAME: Record<string, string> = {
  Jan: "January", Feb: "February", Mar: "March", Apr: "April", May: "May", Jun: "June",
  Jul: "July", Aug: "August", Sep: "September", Oct: "October", Nov: "November", Dec: "December",
};

/** One column per month (or quarter): how far it typically runs above or below the yearly level. */
const SeasonColumns: React.FC<{ pattern: SeasonalPatternPoint[]; color: string; text: string }> = ({ pattern, color, text }) => {
  const { ref, width } = useWidth(720);
  const [hover, setHover] = useState<number | null>(null);
  const W = Math.max(280, width);
  const narrow = W < 560;
  const H = narrow ? 220 : 250;
  const values = pattern.map((p) => p.average_effect_pct);
  const lo = Math.min(0, ...values);
  const hi = Math.max(0, ...values);
  const step = niceStep(hi - lo || 1, narrow ? 4 : 5);
  const y0 = Math.floor(lo / step) * step;
  const y1 = Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  for (let t = y0; t <= y1 + step / 2; t += step) ticks.push(Math.round(t / step) * step);
  const tickText = (t: number) => (t === 0 ? "0%" : signedPct(t, step < 1 ? 1 : 0));
  const left = 46;
  const right = 8;
  const top = 18;
  const bottom = 26;
  const plotW = W - left - right;
  const plotH = H - top - bottom;
  const sy = (v: number) => top + plotH - ((v - y0) / (y1 - y0 || 1)) * plotH;
  const slot = plotW / pattern.length;
  const barW = Math.max(6, Math.min(44, slot * 0.66));
  const cx = (i: number) => left + slot * i + slot / 2;
  const showValues = slot >= 40;

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const i = Math.floor((px - left) / slot);
    setHover(i >= 0 && i < pattern.length ? i : null);
  };
  const onKey = (e: React.KeyboardEvent<SVGSVGElement>) => {
    if (["ArrowLeft", "ArrowRight", "Home", "End", "Escape"].indexOf(e.key) < 0) return;
    e.preventDefault();
    if (e.key === "Escape") { setHover(null); return; }
    const key = e.key;
    const last = pattern.length - 1;
    setHover((prev) => {
      const next = key === "Home" ? 0 : key === "End" ? last : key === "ArrowLeft" ? (prev === null ? last : prev - 1) : prev === null ? 0 : prev + 1;
      return Math.max(0, Math.min(last, next));
    });
  };

  const hp = hover !== null ? pattern[hover] : null;
  const aria =
    "How far each " + (pattern.length === 12 ? "month" : "period") + " typically runs from the yearly level: " +
    pattern.map((p) => `${LONG_NAME[p.label] ?? p.label} ${signedPct(p.average_effect_pct)}`).join(", ") + ".";

  return (
    <div ref={ref} className="tr-chart">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height={H}
        role="img"
        aria-label={`${aria} Use the left and right arrow keys to read each one.`}
        tabIndex={0}
        onPointerMove={onMove}
        onPointerDown={onMove}
        onPointerLeave={() => setHover(null)}
        onKeyDown={onKey}
        onBlur={() => setHover(null)}
      >
        <g aria-hidden="true">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={left} x2={left + plotW} y1={f(sy(t))} y2={f(sy(t))} stroke={t === 0 ? TOKENS.ink : TOKENS.grid} strokeWidth={1} />
              <text x={left - 8} y={f(sy(t) + 4)} textAnchor="end" style={{ fontSize: 12, fill: TOKENS.muted, fontVariantNumeric: "tabular-nums" }}>
                {tickText(t)}
              </text>
            </g>
          ))}
          {pattern.map((p, i) => {
            const v = p.average_effect_pct;
            const yTop = sy(Math.max(0, v));
            const h = Math.max(1, Math.abs(sy(v) - sy(0)));
            const active = hover === i;
            return (
              <g key={p.position}>
                {active && <rect x={f(left + slot * i)} y={top} width={f(slot)} height={plotH} fill={TOKENS.ink} fillOpacity={0.05} />}
                <rect x={f(cx(i) - barW / 2)} y={f(yTop)} width={f(barW)} height={f(h)} fill={color} fillOpacity={hover === null || active ? 1 : 0.55} rx={1.5} />
                {showValues && (
                  <text x={f(cx(i))} y={f(v >= 0 ? yTop - 5 : yTop + h + 13)} textAnchor="middle" style={{ fontSize: 11.5, fontWeight: 600, fill: text, fontVariantNumeric: "tabular-nums" }}>
                    {signedPct(v, Math.abs(v) < 10 ? 1 : 0)}
                  </text>
                )}
                <text x={f(cx(i))} y={H - 6} textAnchor="middle" style={{ fontSize: 12, fill: active ? TOKENS.ink : TOKENS.muted, fontWeight: active ? 700 : 400 }}>
                  {narrow && pattern.length === 12 ? p.label.charAt(0) : p.label}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
      <div role="status" aria-live="polite" style={hp === null ? { position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" } : {
        ...READOUT_STYLE,
        top: 0,
        left: cx(hover as number) > W - 200 ? undefined : Math.max(0, cx(hover as number) + barW / 2 + 8),
        right: cx(hover as number) > W - 200 ? Math.max(0, W - cx(hover as number) + barW / 2 + 8) : undefined,
      }}>
        {hp && (
          <>
            <div style={{ fontWeight: 700 }}>{LONG_NAME[hp.label] ?? hp.label}</div>
            <div style={{ whiteSpace: "nowrap" }}>
              <span style={{ fontWeight: 700 }}>{signedPct(hp.average_effect_pct)}</span>{" "}
              <span style={{ color: TOKENS.muted }}>{hp.tag}</span>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

// The typical shape of one cycle (e.g. "exports in December are usually higher than average"),
// averaged across every year in the selected range — not a single year's swing. Shown as a
// percentage above/below the series' own typical level, with a plain-language tag per bar, so a
// reader who has never heard of STL can still read it at a glance.
export const SeasonalityPanel: React.FC<Props> = ({ analysis, flow }) => {
  const { seasonal_pattern: pattern, seasonality_label, seasonality_summary } = analysis;
  const tone = flowTone(flow);

  if (pattern.length === 0) {
    return (
      <p className="tr-quiet">
        No seasonal cycle to show: this series is annual, or too short for the analysis to tell a
        repeating yearly pattern apart from the overall trend.
      </p>
    );
  }

  return (
    <>
      {seasonality_label && (
        <div className="tr-meter">
          <span>How seasonal is this?</span>
          <span
            className="tr-meter-track"
            role="meter"
            aria-label="Share of the movement that is seasonal"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(analysis.seasonal_strength * 100)}
            aria-valuetext={`${percent(analysis.seasonal_strength, 0)}, ${seasonality_label}`}
          >
            <span style={{ width: `${Math.round(analysis.seasonal_strength * 100)}%` }} />
          </span>
          <strong style={{ color: TOKENS.ink }}>
            {seasonality_label.charAt(0).toUpperCase() + seasonality_label.slice(1)}, {percent(analysis.seasonal_strength, 0)}
          </strong>
        </div>
      )}

      {seasonality_summary && <p className="tr-text">{seasonality_summary}</p>}

      <SeasonColumns pattern={pattern} color={tone.fill} text={tone.text} />
      <p className="tr-quiet">
        Each column is how far that {pattern.length === 12 ? "month" : "period"} typically runs from the series' level for
        the year, with the trend and one-off events taken out. Above the line: usually higher; below: usually lower.
      </p>
    </>
  );
};
