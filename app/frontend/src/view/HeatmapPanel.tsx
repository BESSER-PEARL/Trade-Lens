// Heatmap panel (contract 1.1), by options.kind:
// - month_year: `main` on /insights/series with freq=M; one row per year, one column per month.
// - product_year: `main` on /insights/matrix; one row per code, one column per year.
// - seasonal: `main` on /trends/seasonal-heatmap; one row per HS2 chapter, one column per
//   month, the average_effect_pct of Ivan's seasonal profile (diverging).
// options.scale: value (default) or deviation (each cell against its row's average).
// An optional `analysis` query on /trends/analyze only feeds the facts.
// Marks: trend_break (not on seasonal), in the agent's pen.

import React from "react";
import { problemCard } from "./common";
import { formatValue } from "./facts";
import type { DrawnMark } from "./marks";
import type { Panel } from "./types";
import { ChartFrame, EmptyNote, PanelCaption } from "./charts/frame";
import { MONTHS, MONTHS_LONG, itemLabel } from "./charts/format";
import { Heatmap } from "./charts/Heatmap";
import type { HeatBreak } from "./charts/Heatmap";
import { ChartKey, DataTable } from "./charts/parts";
import { partnerName, penMarks, problemOf, queryOf, usePanelData } from "./charts/panelData";
import { markOffContext, OFF_CONTEXT_OPACITY } from "./context";

type Grid = {
  rows: string[];
  cols: string[];
  values: Array<Array<number | null>>;
  rowLabel: (r: string) => string;
  colLabel: (c: string) => string;
  cellTitle?: (r: string, c: string) => string;
  format: string;
  scale: "sequential" | "diverging";
  reverse?: boolean;
  valueLabel: string;
  legendTitle: string;
};

function seriesRows(data: any): any[] {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.rows)) return data.rows;
  return [];
}

function num(v: any): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Each cell against its row's average: v / mean - 1. */
function deviations(values: Array<Array<number | null>>): Array<Array<number | null>> {
  return values.map((row) => {
    const xs = row.filter((v): v is number => v !== null);
    const mean = xs.length ? xs.reduce((s, v) => s + v, 0) / xs.length : 0;
    return row.map((v) => (v === null || !mean ? null : v / mean - 1));
  });
}

function monthYear(data: any, deviation: boolean, flowWord: string): Grid | null {
  const rows = seriesRows(data).filter((r) => r && /^\d{4}-\d{2}$/.test(String(r.period)) && num(r.value) !== null);
  if (!rows.length) return null;
  const years = rows.map((r) => parseInt(String(r.period).slice(0, 4), 10));
  const y0 = Math.min.apply(null, years);
  const y1 = Math.max.apply(null, years);
  const ys: string[] = [];
  for (let y = y0; y <= y1; y++) ys.push(String(y));
  const cols = MONTHS.map((_, i) => String(i + 1).padStart(2, "0"));
  const by: Record<string, number> = {};
  rows.forEach((r) => (by[String(r.period)] = r.value));
  let values = ys.map((y) => cols.map((m) => (by[`${y}-${m}`] !== undefined ? by[`${y}-${m}`] : null)));
  if (deviation) values = deviations(values);
  return {
    rows: ys,
    cols,
    values,
    rowLabel: (y) => y,
    colLabel: (m) => MONTHS[parseInt(m, 10) - 1],
    cellTitle: (y, m) => `${MONTHS_LONG[parseInt(m, 10) - 1]} ${y}`,
    format: deviation ? "pct_signed" : "eur",
    scale: deviation ? "diverging" : "sequential",
    valueLabel: deviation ? "against the year's average" : flowWord,
    legendTitle: deviation ? "Against the year's average" : `${flowWord.charAt(0).toUpperCase()}${flowWord.slice(1)} in a month`,
  };
}

function productYear(data: any, deviation: boolean, flowWord: string, measureParam?: string): Grid | null {
  const periods: string[] = Array.isArray(data?.periods) ? data.periods.map(String) : [];
  const rows: any[] = Array.isArray(data?.rows) ? data.rows : [];
  if (!periods.length || !rows.length) return null;
  const by = String(data?.by || "product");
  const measure = String(data?.measure || measureParam || "value");
  const names: Record<string, string> = {};
  rows.forEach((r) => (names[String(r.code)] = by === "partner" ? partnerName(String(r.code), r.name) : itemLabel(String(r.code), r.name)));
  let values: Array<Array<number | null>> = rows.map((r) => periods.map((_, i) => num(Array.isArray(r.values) ? r.values[i] : null)));
  const dev = deviation && measure !== "rank" && measure !== "growth";
  if (dev) values = deviations(values);
  const fmt = dev ? "pct_signed" : measure === "share" ? "pct" : measure === "growth" ? "pct_signed" : measure === "rank" ? "ordinal" : "eur";
  const words: Record<string, string> = { value: flowWord, share: `share of ${flowWord}`, growth: "growth on the year before", rank: "rank" };
  return {
    rows: rows.map((r) => String(r.code)),
    cols: periods,
    values,
    rowLabel: (c) => names[c] || c,
    colLabel: (p) => p,   // full years; the chart thins the labels to fit
    cellTitle: (c, p) => `${names[c] || c}, ${p}`,
    format: fmt,
    scale: dev || measure === "growth" ? "diverging" : "sequential",
    reverse: !dev && measure === "rank",
    valueLabel: dev ? "against the row's average" : words[measure] || flowWord,
    legendTitle: dev ? "Against the row's average" : `${(words[measure] || flowWord).charAt(0).toUpperCase()}${(words[measure] || flowWord).slice(1)}`,
  };
}

function seasonal(data: any): Grid | null {
  const rows: any[] = Array.isArray(data?.rows) ? data.rows.filter((r: any) => Array.isArray(r.pattern) && r.pattern.length) : [];
  if (!rows.length) return null;
  const first: any[] = rows[0].pattern.slice().sort((a: any, b: any) => a.position - b.position);
  const cols = first.map((p) => String(p.position));
  const labels: Record<string, string> = {};
  first.forEach((p) => (labels[String(p.position)] = String(p.label || p.position)));
  const names: Record<string, string> = {};
  rows.forEach((r) => (names[String(r.code)] = itemLabel(String(r.code), r.name)));
  const values = rows.map((r) =>
    cols.map((c) => {
      const p = r.pattern.find((x: any) => String(x.position) === c);
      return p ? num(p.average_effect_pct) : null;
    }),
  );
  return {
    rows: rows.map((r) => String(r.code)),
    cols,
    values,
    rowLabel: (c) => names[c] || c,
    colLabel: (c) => labels[c] || c,
    cellTitle: (r, c) => `${names[r] || r}, a typical ${labels[c] || c}`,
    format: "percent_signed",
    scale: "diverging",
    valueLabel: "against a typical month",
    legendTitle: "Seasonal effect, against the yearly average",
  };
}

export const HeatmapPanel: React.FC<{ panel: Panel; marks?: DrawnMark[] }> = ({ panel, marks = [] }) => {
  const data = usePanelData(panel);
  const problem = problemOf(data, ["main"]);
  if (problem) return problemCard(problem, 320);

  const o = panel.options || {};
  const kind = String(o.kind || "month_year");
  const deviation = o.scale === "deviation";
  const q = queryOf(data, "main");
  const tone = q?.tone || (q?.params?.flow === "IMPORT" ? "imports" : "exports");
  const flowWord = q?.params?.flow === "IMPORT" ? "imports" : q?.params?.flow === "EXPORT" ? "exports" : "trade";
  const answer = data.byKey.main?.data;
  const grid =
    kind === "seasonal"
      ? seasonal(answer)
      : kind === "product_year"
        ? productYear(answer, deviation, flowWord, q?.params?.measure as string | undefined)
        : monthYear(answer, deviation, flowWord);
  const caption = <PanelCaption panel={panel} facts={data.facts} />;
  if (!grid) return <ChartFrame chart={<EmptyNote>No values for this grid.</EmptyNote>} caption={caption} />;

  const breaks: HeatBreak[] =
    kind === "seasonal"
      ? []
      : marks
          .filter((d) => d.mark.kind === "trend_break" && d.mark.params?.at)
          .map((d) => {
            const at = String(d.mark.params.at);
            const m = /^(\d{4})(?:-(\d{2}))?$/.exec(at);
            const base = { id: d.mark.id, color: d.color, n: d.number, showNumber: d.showNumber, label: d.mark.params?.label, opacity: markOffContext(d.mark, data.ctx) ? OFF_CONTEXT_OPACITY : 1 };
            if (kind === "product_year") return { ...base, mode: "column" as const, col: m ? m[1] : at };
            return { ...base, mode: "staircase" as const, row: m ? m[1] : at, col: m && m[2] ? m[2] : "01" };
          });
  // A break at a month is named by its month: "December 2021", never a bare "2021" or "2021-12".
  const breakMonth = (at: unknown) => {
    const m = /^(\d{4})-(\d{2})$/.exec(String(at || ""));
    return m ? `${MONTHS_LONG[Number(m[2]) - 1]} ${m[1]}` : undefined;
  };
  const keyMarks = penMarks(marks.filter((d) => d.mark.kind === "trend_break"), data.ctx, ["trend_break"]).map((k) => {
    const d = marks.find((x) => x.mark.id === k.id);
    const full = breakMonth(d?.mark.params?.at);
    const bare = !k.label || /^\d{4}(-\d{2})?$/.test(k.label.trim());
    return full && bare ? { ...k, label: full } : full && k.label && !k.label.includes(full) ? { ...k, label: k.label.replace(/\b\d{4}-\d{2}\b/, full) } : k;
  });
  const fmt = (v: number) => formatValue(v, grid.format);
  const title =
    kind === "seasonal"
      ? `${panel.title}: the typical year of each chapter`
      : kind === "product_year"
        ? `${panel.title}: ${grid.rows.length} rows by ${grid.cols.length} years`
        : `${panel.title}: ${grid.rows.length} years by 12 months`;
  const cellExtra =
    kind === "month_year" && !deviation
      ? (ri: number, ci: number) => {
          const row = grid.values[ri].filter((v): v is number => v !== null);
          const mean = row.length ? row.reduce((s, v) => s + v, 0) / row.length : 0;
          const v = grid.values[ri][ci];
          return v !== null && mean ? [{ value: formatValue(v / mean - 1, "pct_signed"), label: "against the year's average" }] : [];
        }
      : undefined;

  return (
    <ChartFrame
      chart={
        <Heatmap
          rows={grid.rows}
          cols={grid.cols}
          values={grid.values}
          title={title}
          tone={tone}
          scale={grid.scale}
          reverse={grid.reverse}
          format={fmt}
          rowLabel={grid.rowLabel}
          colLabel={grid.colLabel}
          cellTitle={grid.cellTitle}
          cellExtra={cellExtra}
          valueLabel={grid.valueLabel}
          legendTitle={grid.legendTitle}
          breaks={breaks}
          rowHeight={kind === "month_year" ? 16 : undefined}
        />
      }
      chartKey={keyMarks.length ? <ChartKey marks={keyMarks} /> : undefined}
      caption={caption}
      table={
        <DataTable
          caption={title}
          columns={[""].concat(grid.cols.map((c) => (kind === "product_year" ? c : grid.colLabel(c))))}
          rows={grid.rows.map((r, i) => [grid.rowLabel(r)].concat(grid.values[i].map((v) => (v === null ? "–" : fmt(v)))))}
        />
      }
    />
  );
};
