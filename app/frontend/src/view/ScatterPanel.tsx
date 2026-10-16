// Scatter panel (contract 1.1): `main` on /insights/specialisation (items: code, name, value,
// share, peer_share, rca), optional `growth` on /insights/movers (up, down and new) or
// /insights/trends (items), joined on code: a field not in main comes from the growth row
// with the same code. options: x, y (required), size (field names), labels (codes),
// log_x, log_y, reference_x, reference_y. Marks: highlight codes.

import React from "react";
import { problemCard } from "./common";
import { formatPeriod, formatValue } from "./facts";
import type { DrawnMark } from "./marks";
import type { Panel } from "./types";
import { ChartFrame, EmptyNote, PanelCaption } from "./charts/frame";
import { itemLabel } from "./charts/format";
import { useShowCodes } from "./codes";
import { ChartKey, DataTable, Swatch } from "./charts/parts";
import { toneColor } from "./charts/palette";
import { partnerName, penMarks, problemOf, queryOf, usePanelData } from "./charts/panelData";
import { Scatter } from "./charts/Scatter";
import { useUserHighlight } from "./ViewStore";
import type { ScatterPoint } from "./charts/Scatter";

const FIELDS: Record<string, { label: string; format: string; axis?: string }> = {
  rca: { label: "Specialisation against the EU27 (1 = as much)", format: "number" },
  share: { label: "Share of Luxembourg's total", format: "pct" },
  peer_share: { label: "Share in the EU27's total", format: "pct" },
  value: { label: "Value", format: "eur" },
  growth: { label: "Change on the year before", format: "pct_signed" },
  change: { label: "Change in euros", format: "eur_signed" },
  slope_per_year: { label: "Trend per year", format: "eur_signed" },
  previous: { label: "Value the year before", format: "eur" },
};

function fieldOf(name: string) {
  return FIELDS[name] || { label: name.replace(/_/g, " "), format: "number" };
}

/** Short tick labels: no euro sign, no decimals beyond what a tick needs. */
function tickFormat(format: string) {
  return (v: number) => {
    if (format === "eur" || format === "eur_signed") return formatValue(v, "eur").replace("€", "");
    if (format === "pct" || format === "pct_signed") return `${Math.round(v * 1000) / 10}%`;
    return formatValue(v, "number");
  };
}

function quadrantsFor(x: string, y: string): [string, string, string, string] | undefined {
  if (x === "rca" && (y === "growth" || y === "change" || y === "slope_per_year")) {
    return ["Growing, not a strength", "Strong and growing", "Neither", "Strong but shrinking"];
  }
  if (y === "rca" && (x === "growth" || x === "change")) {
    return ["Strong but shrinking", "Strong and growing", "Neither", "Growing, not a strength"];
  }
  return undefined;
}

export const ScatterPanel: React.FC<{ panel: Panel; marks?: DrawnMark[] }> = ({ panel, marks = [] }) => {
  const showCodes = useShowCodes(); // the reader's profile may hide HS codes (contract 1.4)
  const data = usePanelData(panel);
  const pick = useUserHighlight(panel.id);
  const problem = problemOf(data, ["main", "growth"]);
  if (problem) return problemCard(problem, 340);

  const o = panel.options || {};
  const xf = String(o.x || "rca");
  const yf = String(o.y || "growth");
  const zf = o.size ? String(o.size) : undefined;
  const q = queryOf(data, "main");
  const main: any = data.byKey.main?.data || {};
  const g: any = data.byKey.growth?.data;
  const by = String(main.by || q?.params?.by || "product");
  const period = String(main.period ?? q?.params?.period ?? "");
  const tone = q?.tone || (q?.params?.flow === "IMPORT" ? "imports" : "exports");

  const extra: Record<string, any> = {};
  if (g) {
    const rows: any[] = Array.isArray(g.items) ? g.items : ([] as any[]).concat(g.up || [], g.down || [], g.new || []);
    rows.forEach((r) => {
      if (r && r.code !== undefined && !extra[String(r.code)]) extra[String(r.code)] = r;
    });
  }
  const read = (item: any, f: string): number | undefined => {
    const v = item[f] !== undefined && item[f] !== null ? item[f] : extra[String(item.code)]?.[f];
    return typeof v === "number" && Number.isFinite(v) ? v : undefined;
  };
  const items: any[] = Array.isArray(main.items) ? main.items.filter((i: any) => i && i.code !== "OTHER") : [];
  const points: ScatterPoint[] = [];
  let missing = 0;
  items.forEach((i) => {
    const x = read(i, xf);
    const y = read(i, yf);
    if (x === undefined || y === undefined) {
      missing++;
      return;
    }
    const z = zf ? read(i, zf) : undefined;
    points.push({
      code: String(i.code),
      name: by === "partner" ? partnerName(String(i.code), i.name) : itemLabel(String(i.code), i.name, showCodes),
      x,
      y,
      size: z,
    });
  });
  const caption = <PanelCaption panel={panel} facts={data.facts} />;
  if (!points.length) {
    return (
      <ChartFrame
        chart={<EmptyNote>No code has both {fieldOf(xf).label.toLowerCase()} and {fieldOf(yf).label.toLowerCase()} for {formatPeriod(period)}.</EmptyNote>}
        caption={caption}
      />
    );
  }

  const X = fieldOf(xf);
  const Y = fieldOf(yf);
  const Z = zf ? fieldOf(zf) : undefined;
  const pm = penMarks(marks, data.ctx, ["highlight"]);
  const labels = Array.isArray(o.labels) ? (o.labels as string[]).map(String) : [];
  const title = `${panel.title}, ${formatPeriod(period)}: ${X.label} against ${Y.label.toLowerCase()}, one bubble per ${by === "partner" ? "partner" : "product"}`;

  return (
    <ChartFrame
      chart={
        <Scatter
          points={points}
          title={title}
          x={{ label: X.label, format: tickFormat(X.format), full: (v) => formatValue(v, X.format), log: !!o.log_x, reference: typeof o.reference_x === "number" ? o.reference_x : undefined }}
          y={{ label: Y.label, format: tickFormat(Y.format), full: (v) => formatValue(v, Y.format), log: !!o.log_y, reference: typeof o.reference_y === "number" ? o.reference_y : undefined }}
          size={Z ? { label: Z.label.toLowerCase(), format: (v) => formatValue(v, Z.format) } : undefined}
          tone={tone}
          labels={labels}
          marks={pm}
          onSelect={(p) => pick.toggle(p.code, p.name)}
          quadrants={quadrantsFor(xf, yf)}
        />
      }
      chartKey={
        <ChartKey marks={pm}>
          <Swatch color={toneColor(tone).fill} label={Z ? `bubble size: ${Z.label.toLowerCase()}` : "one per code"} shape="dot" />
          {(typeof o.reference_x === "number" || typeof o.reference_y === "number") && <span>Dashed: the reference lines</span>}
        </ChartKey>
      }
      note={missing ? `${missing} ${missing === 1 ? "code has" : "codes have"} no value for one of the two measures and ${missing === 1 ? "is" : "are"} left out.` : undefined}
      caption={caption}
      table={
        <DataTable
          caption={title}
          columns={[by === "partner" ? "Partner" : "Product", X.label, Y.label].concat(Z ? [Z.label] : [])}
          rows={points
            .slice()
            .sort((a, b) => b.y - a.y)
            .map((p) => [p.name, formatValue(p.x, X.format), formatValue(p.y, Y.format)].concat(Z ? [p.size !== undefined ? formatValue(p.size, Z.format) : "–"] : []))}
        />
      }
    />
  );
};
