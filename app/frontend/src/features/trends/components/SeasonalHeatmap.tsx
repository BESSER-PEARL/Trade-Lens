// SPDX-License-Identifier: MIT
import React from "react";
import type { SeasonalHeatmap as SeasonalHeatmapData } from "../api";
import { MINUS, signedPct } from "../format";
import { TOKENS, flowTone } from "../styles";
import { useWidth } from "./LineChart";

interface Props {
  data: SeasonalHeatmapData | null;
  loading: boolean;
  error: string | null;
  flow?: string;
}

// Above the product's own yearly level in the flow's colour, below it in ink, both fading to
// white at zero; capped at ±25% so one extreme month does not wash out every other cell.
const CAP = 25;

function mix(hex: string, t: number): number[] {
  const n = parseInt(hex.slice(1), 16);
  const ch = (shift: number) => Math.round(255 + (((n >> shift) & 255) - 255) * t);
  return [ch(16), ch(8), ch(0)];
}

function luminance(rgb: number[]): number {
  const lin = rgb.map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}

function cellStyle(pct: number, above: string): React.CSSProperties {
  const t = Math.min(1, Math.abs(pct) / CAP);
  const strength = pct >= 0 ? 0.08 + t * 0.92 : 0.06 + t * 0.62;
  const rgb = mix(pct >= 0 ? above : TOKENS.ink, strength);
  // Ink text wherever it reads better than white (sky is light enough for ink all the way).
  const L = luminance(rgb);
  const inkL = luminance(mix(TOKENS.ink, 1));
  const onWhite = 1.05 / (L + 0.05);
  const onInk = (L + 0.05) / (inkL + 0.05);
  return { background: `rgb(${rgb.join(", ")})`, color: onWhite > onInk ? "#FFFFFF" : TOKENS.ink };
}

const LONG: Record<string, string> = {
  Jan: "January", Feb: "February", Mar: "March", Apr: "April", May: "May", Jun: "June",
  Jul: "July", Aug: "August", Sep: "September", Oct: "October", Nov: "November", Dec: "December",
};

export const SeasonalHeatmap: React.FC<Props> = ({ data, loading, error, flow }) => {
  const { ref, width } = useWidth(900);
  const tone = flowTone(flow);
  if (loading && !data) return <p className="tr-status" role="status">Loading the seasonal profile of the main products…</p>;
  if (error) return <p className="tr-error" role="alert">{error}</p>;
  if (!data || data.rows.length === 0) {
    return (
      <p className="tr-quiet">
        No comparable products at this frequency: switch to monthly to compare the seasons of the main products.
      </p>
    );
  }
  const labels = data.rows[0]?.pattern.map((p) => p.label) ?? [];
  const narrow = width < 640;
  const unitName = data.cycle === 12 ? "month" : "quarter";
  const short = (pct: number) => {
    const r = Math.round(pct);
    return r === 0 ? "0" : `${r > 0 ? "+" : MINUS}${Math.abs(r)}${narrow ? "" : "%"}`;
  };

  return (
    <div ref={ref} className="tr-busy" aria-busy={loading} style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
      <div className="tr-heat-scale" aria-hidden="true">
        <span>Lower than usual</span>
        {[-25, -12, 0, 12, 25].map((v) => (
          <span key={v} aria-hidden="true" style={{ background: cellStyle(v, tone.fill).background, boxShadow: `inset 0 0 0 1px ${TOKENS.ruleSoft}` }} />
        ))}
        <span>Higher than usual</span>
        {narrow && <span style={{ color: TOKENS.muted }}>(in %)</span>}
      </div>
      <table className="tr-heat">
        <caption style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" }}>
          How far each product typically runs above or below its own yearly level in each {unitName}, in percent
        </caption>
        {!narrow && <colgroup><col style={{ width: "30%" }} />{labels.map((l) => <col key={l} />)}</colgroup>}
        <thead>
          <tr>
            {!narrow && <th scope="col" className="tr-heat-name" style={{ color: TOKENS.muted }}>Product</th>}
            {labels.map((l) => (
              <th key={l} scope="col" abbr={LONG[l] ?? l}>{narrow && labels.length === 12 ? l.charAt(0) : l}</th>
            ))}
          </tr>
        </thead>
        {data.rows.map((row) => {
          const cells = row.pattern.map((p) => (
            <td key={p.position} style={cellStyle(p.average_effect_pct, tone.fill)}
                title={`${row.name}, ${LONG[p.label] ?? p.label}: ${p.tag} (${signedPct(p.average_effect_pct)})`}>
              {short(p.average_effect_pct)}
            </td>
          ));
          return narrow ? (
            <tbody key={row.code}>
              <tr>
                <th scope="rowgroup" colSpan={labels.length} className="tr-heat-name" style={{ padding: "8px 0 2px" }} title={row.name}>
                  {row.name}
                </th>
              </tr>
              <tr>{cells}</tr>
            </tbody>
          ) : (
            <tbody key={row.code}>
              <tr>
                <th scope="row" className="tr-heat-name" title={row.name}>{row.name}</th>
                {cells}
              </tr>
            </tbody>
          );
        })}
      </table>
      <p className="tr-quiet">
        Each row is one of the main products, each column a {unitName}. Read across a row for a product's season;
        read down a column to see which products move together.
      </p>
    </div>
  );
};
