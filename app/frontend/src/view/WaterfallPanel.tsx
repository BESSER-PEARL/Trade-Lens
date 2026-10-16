// Waterfall panel (contract 1.1): one query `main` on /insights/movers. The bars start at
// previous_total, step through the largest movers (up and down), close with one step for
// everything else (total - previous_total - the steps shown) and end at total.
// Marks: annotation (x = a row code) and highlight codes outline their rows in the pen.

import React from "react";
import { problemCard } from "./common";
import { formatPeriod } from "./facts";
import type { DrawnMark } from "./marks";
import type { Panel } from "./types";
import { EmptyNote, ChartFrame, PanelCaption } from "./charts/frame";
import { fmtEur, itemLabel } from "./charts/format";
import { useShowCodes } from "./codes";
import { ChartKey, DataTable, Swatch } from "./charts/parts";
import { toneColor } from "./charts/palette";
import { partnerName, penMarks, problemOf, queryOf, usePanelData } from "./charts/panelData";
import { Waterfall, waterfallRows } from "./charts/Waterfall";
import type { WaterfallStep } from "./charts/Waterfall";
import { TOKENS } from "./tokens";

function flowWord(flow: unknown): string {
  return flow === "IMPORT" ? "Imports" : flow === "EXPORT" ? "Exports" : "Trade";
}

export const WaterfallPanel: React.FC<{ panel: Panel; marks?: DrawnMark[] }> = ({ panel, marks = [] }) => {
  const showCodes = useShowCodes(); // the reader's profile may hide HS codes (contract 1.4)
  const data = usePanelData(panel);
  const problem = problemOf(data, ["main"]);
  if (problem) return problemCard(problem, 280);

  const q = queryOf(data, "main");
  const m: any = data.byKey.main?.data || {};
  const o = panel.options || {};
  const limit = typeof o.limit === "number" ? o.limit : 8;
  const showOther = o.show_other !== false;
  const tone = q?.tone || (q?.params?.flow === "IMPORT" ? "imports" : "exports");
  const by = String(q?.params?.by || m.by || "product");
  const nameOf = (code: string, name: string) => (by === "partner" ? partnerName(code, name) : itemLabel(code, name, showCodes));

  const seen: Record<string, boolean> = {};
  const steps: WaterfallStep[] = [];
  ([] as any[]).concat(m.up || [], m.down || [], m.new || []).forEach((x: any) => {
    if (!x || seen[x.code] || typeof x.change !== "number") return;
    seen[x.code] = true;
    steps.push({ code: String(x.code), name: nameOf(String(x.code), x.name), value: x.change, previous: x.previous, current: x.value });
  });

  const period = String(m.period ?? q?.params?.period ?? "");
  const previous = String(m.previous ?? q?.params?.previous ?? "");
  const word = flowWord(q?.params?.flow);
  const hasTotals = typeof m.total === "number" && typeof m.previous_total === "number";
  const start = hasTotals ? { label: `${word}, ${formatPeriod(previous)}`, value: m.previous_total as number } : null;
  const end = hasTotals ? { label: `${word}, ${formatPeriod(period)}`, value: m.total as number } : null;
  const caption = <PanelCaption panel={panel} facts={data.facts} />;

  if (!steps.length) {
    return <ChartFrame chart={<EmptyNote>Nothing moved between {formatPeriod(previous)} and {formatPeriod(period)} for this selection.</EmptyNote>} caption={caption} />;
  }

  const rows = waterfallRows(start, end, steps, limit, showOther);
  const pm = penMarks(marks, data.ctx, ["annotation", "highlight"]);
  const colour = toneColor(tone).fill;
  const title = `${panel.title}: waterfall from ${formatPeriod(previous)} to ${formatPeriod(period)}`;

  return (
    <ChartFrame
      chart={<Waterfall title={title} rows={rows} tone={tone} marks={pm} />}
      chartKey={
        <ChartKey marks={pm}>
          <Swatch color={colour} label="rise" />
          <Swatch color={colour} label="fall" shape="hatch" />
          <Swatch color={TOKENS.ink} label="total" />
          {showOther && hasTotals && <Swatch color={TOKENS.field} label="everything else" />}
        </ChartKey>
      }
      note={
        hasTotals
          ? undefined
          : "The totals of the two periods are not in this answer yet, so the chart shows only the change of the items listed."
      }
      caption={caption}
      table={
        <DataTable
          caption={title}
          columns={["Item", "Change", "Running total"]}
          rows={rows.map((r) => [r.name, r.kind === "total" ? fmtEur(r.delta) : fmtEur(r.delta, true), r.kind === "total" ? fmtEur(r.to) : fmtEur(r.to)])}
        />
      }
    />
  );
};
