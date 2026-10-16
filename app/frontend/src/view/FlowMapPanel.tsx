// Flow map panel (contract 1.1): one query `main` on /insights/flows. Import arcs (steel)
// come in from in.items, export arcs (sky) go out to out.items; the width is the value.
// options.flow picks imports, exports or both; options.extent world or europe.
// Marks: thread (in_code, out_code: one route through Luxembourg in the agent's pen) and
// highlight codes (countries outlined).

import React from "react";
import { problemCard } from "./common";
import { formatPeriod } from "./facts";
import type { DrawnMark } from "./marks";
import type { Panel } from "./types";
import { ChartFrame, EmptyNote, PanelCaption } from "./charts/frame";
import { fmtEur, fmtPct } from "./charts/format";
import { FlowMap, drawnFlows, widestFlow } from "./charts/map/FlowMap";
import type { FlowDatum, ThreadMark } from "./charts/map/FlowMap";
import { outsideFrame } from "./charts/map/geo";
import type { Extent } from "./charts/map/geo";
import { ChartKey, DataTable, Swatch } from "./charts/parts";
import { isCountryCode, partnerName, penMarks, problemOf, queryOf, usePanelData } from "./charts/panelData";
import { markOffContext, OFF_CONTEXT_OPACITY } from "./context";
import { useUserHighlight } from "./ViewStore";
import { TOKENS } from "./tokens";

function flowsOf(side: any): FlowDatum[] {
  const items: any[] = side && Array.isArray(side.items) ? side.items : [];
  return items
    .filter((i) => isCountryCode(i.code) && typeof i.value === "number")
    .map((i) => ({ code: i.code, name: partnerName(i.code, i.name), value: i.value, share: typeof i.share === "number" ? i.share : undefined }));
}

export const FlowMapPanel: React.FC<{ panel: Panel; marks?: DrawnMark[] }> = ({ panel, marks = [] }) => {
  const data = usePanelData(panel);
  const pick = useUserHighlight(panel.id);
  const problem = problemOf(data, ["main"]);
  if (problem) return problemCard(problem, 360);

  const o = panel.options || {};
  const q = queryOf(data, "main");
  const answer: any = data.byKey.main?.data || {};
  const show = o.flow === "imports" || o.flow === "exports" ? (o.flow as "imports" | "exports") : "both";
  const extent: Extent = o.extent === "world" ? "world" : "europe";
  const maxArcs = typeof q?.params?.max_items === "number" ? Math.min(20, q.params.max_items as number) : 10;
  const imports = flowsOf(answer.in);
  const exports = flowsOf(answer.out);
  const period = String(answer.period ?? q?.params?.period ?? "");
  const caption = <PanelCaption panel={panel} facts={data.facts} />;
  if (!imports.length && !exports.length) return <ChartFrame chart={<EmptyNote>No partner has data for {formatPeriod(period)}.</EmptyNote>} caption={caption} />;

  const threads: ThreadMark[] = marks
    .filter((d) => d.mark.kind === "thread")
    .map((d) => ({
      id: d.mark.id,
      in_code: d.mark.params?.in_code,
      out_code: d.mark.params?.out_code,
      label: String(d.mark.params?.label || "A route"),
      color: d.color,
      n: d.number,
      showNumber: d.showNumber,
      opacity: markOffContext(d.mark, data.ctx) ? OFF_CONTEXT_OPACITY : 1,
    }));
  const highlights = penMarks(marks, data.ctx, ["highlight"]);
  const keyMarks = penMarks(marks, data.ctx, ["thread", "highlight"]);
  const title = `${panel.title}, ${formatPeriod(period)}: arcs between Luxembourg and its largest partners`;
  // Partners beyond the frame end at an arrow on the map's edge: the key says so.
  const beyond = extent === "europe" && drawnFlows(imports, show !== "exports", maxArcs).concat(drawnFlows(exports, show !== "imports", maxArcs)).some((f) => outsideFrame(f.code, extent));

  // One table row per partner, both directions side by side.
  const byCode: Record<string, { name: string; bought?: number; sold?: number; inShare?: number; outShare?: number }> = {};
  imports.forEach((f) => (byCode[f.code] = { ...(byCode[f.code] || { name: f.name }), bought: f.value, inShare: f.share }));
  exports.forEach((f) => (byCode[f.code] = { ...(byCode[f.code] || { name: f.name }), sold: f.value, outShare: f.share }));
  const codes = Object.keys(byCode).sort((a, b) => (byCode[b].bought || 0) + (byCode[b].sold || 0) - (byCode[a].bought || 0) - (byCode[a].sold || 0));

  return (
    <ChartFrame
      chart={<FlowMap imports={imports} exports={exports} show={show} title={title} extent={extent} maxArcs={maxArcs} threads={threads} highlights={highlights} maxHeight={extent === "europe" ? 540 : 440} onSelect={(code, name) => pick.toggle(code, name)} />}
      chartKey={
        <ChartKey marks={keyMarks}>
          {show !== "exports" && <Swatch color={TOKENS.steel} label="bought from the partner" shape="line" />}
          {show !== "imports" && <Swatch color={TOKENS.sky} label="sold to the partner" shape="line" />}
          <span>Wider arcs carry more, up to {fmtEur(widestFlow(imports, exports, show, maxArcs))}</span>
          {beyond && <span>An arrow at the edge points to a partner beyond the map</span>}
        </ChartKey>
      }
      caption={caption}
      table={
        <DataTable
          caption={title}
          columns={["Partner", "Bought", "Share of imports", "Sold", "Share of exports"]}
          rows={codes.map((c, i) => {
            const r = byCode[c];
            return [
              `${i + 1}. ${r.name}`,
              r.bought !== undefined ? fmtEur(r.bought) : "–",
              r.inShare !== undefined ? fmtPct(r.inShare) : "–",
              r.sold !== undefined ? fmtEur(r.sold) : "–",
              r.outShare !== undefined ? fmtPct(r.outShare) : "–",
            ];
          })}
        />
      }
    />
  );
};
