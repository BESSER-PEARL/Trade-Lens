// Choropleth panel (contract 1.1): one query `main`. Measure share or value: /insights/ranking
// by partner (items: code, name, value, share). Measure balance: /insights/flows (exports in
// out.items minus imports in in.items, per partner). Partners are ISO 2 codes; OTHER and
// aggregates are not drawn. Marks: highlight codes, outlined in the pen.
// The map colours every partner with trade: besides `main` (which the caption's facts read, cut
// at the panel's max_items) the panel asks the same question once more without a cut, so a small
// partner (Ukraine, Moldova) is never grey as if Luxembourg did not trade with it.

import React from "react";
import { problemCard } from "./common";
import { formatPeriod } from "./facts";
import type { DrawnMark } from "./marks";
import type { Panel, Query } from "./types";
import { ChartFrame, EmptyNote, PanelCaption } from "./charts/frame";
import { fmtEur, fmtPct } from "./charts/format";
import { Choropleth } from "./charts/map/Choropleth";
import type { ChoroplethDatum, ChoroplethMeasure } from "./charts/map/Choropleth";
import { centroidOf, outsideFrame } from "./charts/map/geo";
import type { Extent } from "./charts/map/geo";
import { ChartKey, DataTable } from "./charts/parts";
import { isCountryCode, partnerName, penMarks, problemOf, queryOf, usePanelData } from "./charts/panelData";
import { useUserHighlight } from "./ViewStore";

function itemsOf(x: any): any[] {
  return x && Array.isArray(x.items) ? x.items : [];
}

/** Rows of the map from the answer, by measure. */
export function choroplethData(answer: any, measure: ChoroplethMeasure): ChoroplethDatum[] {
  if (measure === "balance") {
    // A balance needs both sides: a partner listed on one side only (cut by max_items) is
    // left out rather than drawn as if the other side were zero.
    const bought: Record<string, number> = {};
    itemsOf(answer?.in).forEach((i: any) => {
      if (isCountryCode(i.code) && typeof i.value === "number") bought[i.code] = i.value;
    });
    return itemsOf(answer?.out)
      .filter((i: any) => isCountryCode(i.code) && typeof i.value === "number" && bought[i.code] !== undefined)
      .map((i: any) => ({
        code: i.code,
        name: partnerName(i.code, i.name),
        value: i.value - bought[i.code],
        imports: bought[i.code],
        exports: i.value,
      }));
  }
  return itemsOf(answer)
    .filter((i: any) => isCountryCode(i.code) && typeof i.value === "number")
    .map((i: any) => ({ code: i.code, name: partnerName(i.code, i.name), value: i.value, share: typeof i.share === "number" ? i.share : undefined }));
}

/** Every partner: no max_items cut and no significance threshold. */
const ALL_PARTNERS = 400;

/** The panel with a second query `all`: `main` without its cut, for the colours (and the table). */
export function withAllPartners(panel: Panel): Panel {
  const main = (panel.queries || []).find((q) => q.key === "main");
  if (!main || (main.endpoint !== "/insights/ranking" && main.endpoint !== "/insights/flows")) return panel;
  if (main.endpoint === "/insights/ranking" && String(main.params?.by || "") !== "partner") return panel;
  const all: Query = { ...main, key: "all", params: { ...main.params, max_items: ALL_PARTNERS, threshold: 0 } };
  return { ...panel, queries: [...(panel.queries || []).filter((q) => q.key !== "all"), all] };
}

export const ChoroplethPanel: React.FC<{ panel: Panel; marks?: DrawnMark[] }> = ({ panel: given, marks = [] }) => {
  const panel = React.useMemo(() => withAllPartners(given), [given]);
  const data = usePanelData(panel);
  const pick = useUserHighlight(panel.id);
  const problem = problemOf(data, ["main"]);
  if (problem) return problemCard(problem, 360);

  const o = panel.options || {};
  const q = queryOf(data, "main");
  // The uncut answer when it is in; until then (or if it fails) the panel's own.
  const all = data.byKey.all;
  const answer: any = all?.ready ? all.data : data.byKey.main?.data;
  const measure: ChoroplethMeasure = o.measure === "balance" || o.measure === "value" ? (o.measure as ChoroplethMeasure) : q?.endpoint === "/insights/flows" ? "balance" : "share";
  const extent: Extent = o.extent === "world" ? "world" : "europe";
  const flow = q?.params?.flow;
  const tone = q?.tone || (flow === "IMPORT" ? "imports" : "exports");
  const what = measure === "balance" ? "balance" : tone === "imports" ? "imports" : "exports";
  const rows = choroplethData(answer, measure);
  const period = String(answer?.period ?? q?.params?.period ?? "");
  const caption = <PanelCaption panel={panel} facts={data.facts} />;
  if (!rows.length) return <ChartFrame chart={<EmptyNote>No partner has data for {formatPeriod(period)}.</EmptyNote>} caption={caption} />;

  const pm = penMarks(marks, data.ctx, ["highlight"]);
  const sorted = rows.slice().sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
  const outside = sorted.filter((d) => centroidOf(d.code) && outsideFrame(d.code, extent)).slice(0, 4);
  const offMap = sorted.filter((d) => !centroidOf(d.code));
  const title = `${panel.title}, ${formatPeriod(period)}: a map of ${measure === "balance" ? "the balance" : `${what} by partner`}`;

  const note: string[] = [];
  if (outside.length && extent === "europe") {
    note.push(
      `Beyond the frame: ${outside
        .map((d) => `${d.name} ${measure === "balance" ? fmtEur(d.value, true) : measure === "share" && d.share !== undefined ? fmtPct(d.share) : fmtEur(d.value)}`)
        .join(", ")}.`,
    );
  }
  if (offMap.length) note.push(offMap.length === 1 ? `${offMap[0].name} is too small for the map; it is in the table.` : `${offMap.length} partners too small for the map are in the table.`);

  return (
    <ChartFrame
      chart={<Choropleth data={rows} measure={measure} tone={tone} title={title} extent={extent} marks={pm} valueLabel={what} maxHeight={extent === "europe" ? 540 : 440} onSelect={(code, name) => pick.toggle(code, name)} />}
      chartKey={pm.some((m) => m.showNumber || m.label) ? <ChartKey marks={pm} /> : undefined}
      note={note.length ? note.join(" ") : undefined}
      caption={caption}
      table={
        <DataTable
          caption={title}
          columns={measure === "balance" ? ["Partner", "Sold", "Bought", "Balance"] : measure === "share" ? ["Partner", what.charAt(0).toUpperCase() + what.slice(1), "Share"] : ["Partner", what.charAt(0).toUpperCase() + what.slice(1)]}
          rows={sorted.map((d, i) =>
            measure === "balance"
              ? [`${i + 1}. ${d.name}`, fmtEur(d.exports ?? 0), fmtEur(d.imports ?? 0), fmtEur(d.value, true)]
              : measure === "share"
                ? [`${i + 1}. ${d.name}`, fmtEur(d.value), d.share !== undefined ? fmtPct(d.share) : "–"]
                : [`${i + 1}. ${d.name}`, fmtEur(d.value)],
          )}
        />
      }
    />
  );
};
