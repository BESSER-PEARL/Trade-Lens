// Bump panel (contract 1.1): one query `main` on /insights/matrix with measure=rank
// ({periods, rows: [{code, name, values}]}, values[i] the 1-based rank in periods[i]).
// options.limit caps the lines; options.labels names the codes drawn in the data colour.
// Marks: highlight codes (their lines in the pen).

import React from "react";
import { problemCard } from "./common";
import type { DrawnMark } from "./marks";
import type { Panel } from "./types";
import { Bump } from "./charts/Bump";
import type { BumpSeries } from "./charts/Bump";
import { ChartFrame, EmptyNote, PanelCaption } from "./charts/frame";
import { itemLabel } from "./charts/format";
import { useShowCodes } from "./codes";
import { ChartKey, DataTable, Swatch } from "./charts/parts";
import { toneColor } from "./charts/palette";
import { partnerName, penMarks, problemOf, queryOf, usePanelData } from "./charts/panelData";
import { TOKENS } from "./tokens";
import { useUserHighlight } from "./ViewStore";

export const BumpPanel: React.FC<{ panel: Panel; marks?: DrawnMark[] }> = ({ panel, marks = [] }) => {
  const showCodes = useShowCodes(); // the reader's profile may hide HS codes (contract 1.4)
  const data = usePanelData(panel);
  const pick = useUserHighlight(panel.id);
  const problem = problemOf(data, ["main"]);
  if (problem) return problemCard(problem, 300);

  const q = queryOf(data, "main");
  const answer: any = data.byKey.main?.data || {};
  const periods: string[] = Array.isArray(answer.periods) ? answer.periods.map(String) : [];
  const by = String(answer.by || q?.params?.by || "partner");
  const o = panel.options || {};
  const limit = typeof o.limit === "number" ? o.limit : 10;
  const series: BumpSeries[] = (Array.isArray(answer.rows) ? answer.rows : [])
    .filter((r: any) => r && r.code !== "OTHER")
    .slice(0, limit)
    .map((r: any) => ({
      code: String(r.code),
      name: by === "partner" ? partnerName(String(r.code), r.name) : itemLabel(String(r.code), r.name, showCodes),
      ranks: periods.map((_, i) => (Array.isArray(r.values) && typeof r.values[i] === "number" ? Math.round(r.values[i]) : null)),
    }));
  const caption = <PanelCaption panel={panel} facts={data.facts} />;
  if (!periods.length || !series.length) return <ChartFrame chart={<EmptyNote>No ranks for this span.</EmptyNote>} caption={caption} />;
  const notRank = String(answer.measure || q?.params?.measure || "rank") !== "rank";

  const tone = q?.tone || (q?.params?.flow === "IMPORT" ? "imports" : "exports");
  let emphasis = Array.isArray(o.labels) ? (o.labels as string[]).map(String) : [];
  if (!emphasis.length) {
    const lastRank = (s: BumpSeries) => s.ranks[s.ranks.length - 1] ?? 999;
    emphasis = series.slice().sort((a, b) => lastRank(a) - lastRank(b)).slice(0, 3).map((s) => s.code);
  }
  const pm = penMarks(marks, data.ctx, ["highlight"]);
  const flow = q?.params?.flow === "IMPORT" ? "imports" : q?.params?.flow === "EXPORT" ? "exports" : "trade";
  const title = `${panel.title}: rank of each ${by === "partner" ? "partner" : "product"} in Luxembourg's ${flow}, ${periods[0]} to ${periods[periods.length - 1]}`;

  return (
    <ChartFrame
      chart={<Bump periods={periods} series={series} title={title} tone={tone} emphasis={emphasis} marks={pm} onSelect={(s) => pick.toggle(s.code, s.name)} />}
      chartKey={
        <ChartKey marks={pm}>
          <Swatch color={toneColor(tone).fill} label={emphasis.map((c) => series.find((s) => s.code === c)?.name || c).join(", ")} shape="line" />
          <Swatch color={TOKENS.field} label="the others" shape="line" />
          <span>Rank 1 is the largest {by === "partner" ? "partner" : "product"} that year</span>
        </ChartKey>
      }
      note={notRank ? "This query does not ask for ranks (measure=rank), so the lines show its values as they are." : undefined}
      caption={caption}
      table={
        <DataTable
          caption={title}
          columns={[by === "partner" ? "Partner" : "Product"].concat(periods)}
          rows={series.map((s) => [s.name as string | number].concat(s.ranks.map((r) => (r === null ? "–" : r))))}
        />
      }
    />
  );
};
