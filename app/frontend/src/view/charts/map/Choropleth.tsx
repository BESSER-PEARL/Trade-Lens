// Choropleth: partners on the map, shaded by their share of a flow (log steps from 0.001%
// to 10%), by value (log steps of the largest value), or by balance (sky for a surplus,
// steel for a deficit, the lightest steps near zero). The data colour is the flow's ramp;
// agents' highlight marks are outlined in their pen by WorldMapBase.

import React, { useMemo } from "react";
import { fmtEur, fmtPct } from "../format";
import { FONT_UI, MAP_EMPTY, SKY_RAMP, STEEL_RAMP, TOKENS, rampFor, toneColor } from "../palette";
import type { PenMark } from "../palette";
import { RampLegend } from "../parts";
import { toIso, worldGeometry } from "./geo";
import type { Country, Extent } from "./geo";
import { WorldMapBase } from "./WorldMapBase";

export interface ChoroplethDatum {
  /** Eurostat partner code (FR, EL, UK...). */
  code: string;
  /** Euros; signed in balance mode (exports minus imports). */
  value: number;
  /** Share of the total, a fraction (share mode). */
  share?: number;
  name: string;
  /** Balance mode: what was bought and sold. */
  imports?: number;
  exports?: number;
}

export type ChoroplethMeasure = "share" | "value" | "balance";

export interface ChoroplethProps {
  data: ChoroplethDatum[];
  measure?: ChoroplethMeasure;
  tone?: string;
  title: string;
  extent?: Extent;
  maxHeight?: number;
  marks?: PenMark[];
  /** "exports", "imports": what a value is called in the readout. */
  valueLabel?: string;
  /** Click or Enter on a partner (its Eurostat code). */
  onSelect?: (code: string, name: string) => void;
}

/** Log steps: 0.001% -> 0.01% -> 0.1% -> 1% -> 10% and above. */
const SHARE_CUTS = [0.00001, 0.0001, 0.001, 0.01, 0.1];
const SHARE_LABELS = ["0.001%", "0.01%", "0.1%", "1%", "10%", ""];
/** Value steps as fractions of the largest value. */
const VALUE_CUTS = [0.0001, 0.001, 0.01, 0.05, 0.25];

function stepOf(x: number, cuts: number[]): number {
  if (!(x > 0)) return -1;
  let i = 0;
  while (i < cuts.length && x >= cuts[i]) i++;
  return i; // 0..5
}

/** The colour of a balance, three steps per sign around the lightest. */
export function balanceFill(v: number, maxAbs: number): string {
  const t = maxAbs ? Math.abs(v) / maxAbs : 0;
  const step = t < 0.02 ? 0 : t < 0.1 ? 1 : t < 0.35 ? 2 : 3;
  const side = v >= 0 ? SKY_RAMP : STEEL_RAMP;
  return [side[1], side[3], side[4], side[6]][step];
}

export const Choropleth: React.FC<ChoroplethProps> = ({ data, measure = "share", tone = "exports", title, extent = "europe", maxHeight, marks = [], valueLabel, onSelect }) => {
  const world = worldGeometry();
  const ramp = rampFor(tone);
  const byIso = useMemo(() => {
    const m = new Map<string, ChoroplethDatum>();
    data.forEach((d) => m.set(toIso(d.code), d));
    return m;
  }, [data]);
  const maxAbs = useMemo(() => data.reduce((m, d) => Math.max(m, Math.abs(d.value)), 0), [data]);
  const what = valueLabel || (measure === "balance" ? "balance" : tone === "imports" ? "imports" : "exports");

  const fill = (c: Country): string => {
    const d = c.code ? byIso.get(c.code) : undefined;
    if (!d) return MAP_EMPTY;
    if (measure === "balance") return balanceFill(d.value, maxAbs);
    const s = measure === "share" ? stepOf(d.share ?? 0, SHARE_CUTS) : stepOf(maxAbs ? d.value / maxAbs : 0, VALUE_CUTS);
    return s < 0 ? ramp[0] : ramp[Math.min(6, s + 1)];
  };
  const nameOf = (c: Country) => byIso.get(c.code || "")?.name || c.name;
  const label = (c: Country) => {
    const d = byIso.get(c.code || "");
    if (!d) return c.name;
    if (measure === "balance") return `${d.name}, balance ${fmtEur(d.value, true)}`;
    return `${d.name}, ${fmtEur(d.value)} of ${what}${d.share !== undefined ? `, ${fmtPct(d.share)}` : ""}`;
  };
  const tip = (c: Country) => {
    const d = byIso.get(c.code || "");
    if (!d) return [];
    if (measure === "balance") {
      return [
        { value: fmtEur(d.value, true), label: d.value >= 0 ? "sold more than bought" : "bought more than sold", swatch: d.value >= 0 ? TOKENS.sky : TOKENS.steel },
        ...(d.exports !== undefined ? [{ value: fmtEur(d.exports), label: "sold", swatch: TOKENS.sky }] : []),
        ...(d.imports !== undefined ? [{ value: fmtEur(d.imports), label: "bought", swatch: TOKENS.steel }] : []),
      ];
    }
    return [
      { value: fmtEur(d.value), label: what, swatch: toneColor(tone).fill },
      ...(d.share !== undefined ? [{ value: fmtPct(d.share), label: "of the total" }] : []),
    ];
  };
  const order = useMemo(() => data.slice().sort((a, b) => Math.abs(b.value) - Math.abs(a.value)).map((d) => d.code), [data]);
  const onMap = data.filter((d) => world.byCode.has(toIso(d.code))).length;

  const legend =
    measure === "balance" ? (
      <RampLegend
        title="Balance"
        steps={[STEEL_RAMP[6], STEEL_RAMP[4], STEEL_RAMP[3], STEEL_RAMP[1], SKY_RAMP[1], SKY_RAMP[3], SKY_RAMP[4], SKY_RAMP[6]]}
        labels={[fmtEur(-maxAbs), "", "", "0", "", "", "", fmtEur(maxAbs, true)]}
        note="Steel: Luxembourg buys more from the partner than it sells. Sky: it sells more."
      />
    ) : measure === "share" ? (
      <RampLegend title={`Share of ${what}`} steps={ramp.slice(1)} labels={SHARE_LABELS} />
    ) : (
      <RampLegend title={what.charAt(0).toUpperCase() + what.slice(1)} steps={ramp.slice(1)} labels={VALUE_CUTS.map((c) => fmtEur(c * maxAbs)).concat([""])} />
    );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <WorldMapBase
        title={`${title}. ${onMap} partners on the map; the table lists them all.`}
        extent={extent}
        maxHeight={maxHeight}
        fill={fill}
        active={(c) => !!c.code && byIso.has(c.code)}
        label={label}
        name={nameOf}
        tip={tip}
        order={order}
        highlights={marks}
        onSelect={
          onSelect
            ? (iso) => {
                const d = byIso.get(iso);
                if (d) onSelect(d.code, d.name);
              }
            : undefined
        }
      />
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-start", gap: "6px 18px" }}>
        {legend}
        {/* A grey partner is one Luxembourg did not trade with in the period, or one the data does not name. */}
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12.5, color: TOKENS.muted, fontFamily: FONT_UI }}>
          <span aria-hidden="true" style={{ width: 14, height: 10, borderRadius: 2, background: MAP_EMPTY, border: `1px solid ${TOKENS.rule}`, boxSizing: "border-box" }} />
          no trade, or not shown
        </span>
      </div>
    </div>
  );
};
