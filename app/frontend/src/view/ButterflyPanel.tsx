// Butterfly panel (contract 1.1): queries `in` (flow=IMPORT) and `out` (flow=EXPORT) on
// /insights/ranking or /insights/ranking/records, with the same by and period. Rows are the
// union of the codes of both sides, largest total first; options.limit caps them.
// Marks: highlight codes.

import React from "react";
import { problemCard } from "./common";
import { formatPeriod } from "./facts";
import type { DrawnMark } from "./marks";
import type { Panel } from "./types";
import { Butterfly } from "./charts/Butterfly";
import type { ButterflyRow } from "./charts/Butterfly";
import { ChartFrame, EmptyNote, PanelCaption } from "./charts/frame";
import { fmtEur, itemLabel } from "./charts/format";
import { useShowCodes } from "./codes";
import { ChartKey, DataTable, Swatch } from "./charts/parts";
import { partnerName, penMarks, problemOf, queryOf, usePanelData } from "./charts/panelData";
import { TOKENS } from "./tokens";
import { useUserHighlight } from "./ViewStore";

function itemsOf(data: any): any[] {
  const xs: any[] = Array.isArray(data) ? data : data && Array.isArray(data.items) ? data.items : [];
  return xs.filter((x) => x && x.code !== "OTHER" && typeof x.value === "number");
}

export const ButterflyPanel: React.FC<{ panel: Panel; marks?: DrawnMark[] }> = ({ panel, marks = [] }) => {
  const showCodes = useShowCodes(); // the reader's profile may hide HS codes (contract 1.4)
  const data = usePanelData(panel);
  const pick = useUserHighlight(panel.id);
  const problem = problemOf(data, ["in", "out"]);
  if (problem) return problemCard(problem, 300);

  const qi = queryOf(data, "in");
  const by = String(qi?.params?.by || "partner");
  const period = String(qi?.params?.period || "");
  const nameOf = (code: string, name: string) => (by === "partner" ? partnerName(code, name) : itemLabel(code, name, showCodes));
  const rowsBy: Record<string, ButterflyRow> = {};
  itemsOf(data.byKey.in?.data).forEach((x) => {
    rowsBy[x.code] = { code: String(x.code), name: nameOf(String(x.code), x.name), imports: x.value, exports: null };
  });
  itemsOf(data.byKey.out?.data).forEach((x) => {
    const prev = rowsBy[x.code];
    rowsBy[x.code] = { code: String(x.code), name: prev ? prev.name : nameOf(String(x.code), x.name), imports: prev ? prev.imports : null, exports: x.value };
  });
  const all = Object.keys(rowsBy)
    .map((k) => rowsBy[k])
    .sort((a, b) => (b.imports || 0) + (b.exports || 0) - (a.imports || 0) - (a.exports || 0));
  const o = panel.options || {};
  const limit = typeof o.limit === "number" ? o.limit : 12;
  const rows = all.slice(0, limit);
  const caption = <PanelCaption panel={panel} facts={data.facts} />;
  if (!rows.length) return <ChartFrame chart={<EmptyNote>No rows for {formatPeriod(period)}.</EmptyNote>} caption={caption} />;

  const pm = penMarks(marks, data.ctx, ["highlight"]);
  const oneSided = rows.some((r) => r.imports === null || r.exports === null);
  const title = `${panel.title}, ${formatPeriod(period)}: bought and sold by ${by === "partner" ? "partner" : "product"}`;

  return (
    <ChartFrame
      chart={<Butterfly rows={rows} title={title} marks={pm} leftLabel={by === "partner" ? "Bought from" : "Bought"} rightLabel={by === "partner" ? "Sold to" : "Sold"} onSelect={(r) => pick.toggle(r.code, r.name)} />}
      chartKey={
        <ChartKey marks={pm}>
          <Swatch color={TOKENS.steel} label="imports" />
          <Swatch color={TOKENS.sky} label="exports" />
          {all.length > rows.length && <span>{rows.length} of {all.length} rows</span>}
        </ChartKey>
      }
      note={oneSided ? "A dash: not among the largest on that side, so not in the answer." : undefined}
      caption={caption}
      table={
        <DataTable
          caption={title}
          columns={[by === "partner" ? "Partner" : "Product", "Bought", "Sold", "Balance"]}
          rows={all.map((r) => [
            r.name,
            r.imports === null ? "–" : fmtEur(r.imports),
            r.exports === null ? "–" : fmtEur(r.exports),
            r.imports !== null && r.exports !== null ? fmtEur(r.exports - r.imports, true) : "–",
          ])}
        />
      }
    />
  );
};
