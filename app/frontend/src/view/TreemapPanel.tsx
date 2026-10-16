// Treemap panel (contract 1.1): one query `main` on /insights/tree (by=product, period,
// depth 1-3, threshold auto: the backend folds small children into an OTHER child).
// Two levels show at a time; clicking a tile opens its group, the breadcrumb goes back.
// Opening a group is also said to the agents: the page sends one highlight mark as agent
// "user" on this panel (added once, then updated, removed when back at the top), so an
// agent reading the spec knows what the reader is looking at. Marks: highlight codes.

import React, { useState } from "react";
import { problemCard } from "./common";
import { formatPeriod } from "./facts";
import type { DrawnMark } from "./marks";
import type { Panel } from "./types";
import { ChartFrame, EmptyNote, PanelCaption } from "./charts/frame";
import { fmtEur, fmtPct, itemLabel } from "./charts/format";
import { ChartKey, DataTable, RampLegend, Swatch } from "./charts/parts";
import { rampFor } from "./charts/palette";
import { penMarks, problemOf, queryOf, usePanelData } from "./charts/panelData";
import { Treemap, isOther } from "./charts/Treemap";
import type { TreeNode } from "./charts/Treemap";
import { FONT_UI, TOKENS } from "./tokens";
import { useViewStore } from "./ViewStore";
import { useShowCodes } from "./codes";

function toNode(x: any): TreeNode {
  return {
    code: String(x?.code ?? ""),
    name: String(x?.name ?? x?.code ?? ""),
    value: typeof x?.value === "number" ? x.value : 0,
    share: typeof x?.share === "number" ? x.share : undefined,
    children: Array.isArray(x?.children) ? x.children.map(toNode) : [],
  };
}

function nodeLabel(n: TreeNode, showCodes = true): string {
  if (isOther(n.code)) return /~rest$/.test(n.code) ? n.name : "Other goods";
  return itemLabel(n.code, n.name, showCodes);
}

export const TreemapPanel: React.FC<{ panel: Panel; marks?: DrawnMark[] }> = ({ panel, marks = [] }) => {
  const data = usePanelData(panel);
  const { spec, send } = useViewStore();
  const [path, setPath] = useState<string[]>([]);
  // A reader whose profile hides codes (the citizen) reads the names alone: "Electrical machinery", not "85 ...".
  const showCodes = useShowCodes();
  const label = (n: TreeNode) => nodeLabel(n, showCodes);
  const problem = problemOf(data, ["main"]);
  if (problem) return problemCard(problem, 340);

  const q = queryOf(data, "main");
  const answer: any = data.byKey.main?.data || {};
  const tone = q?.tone || (q?.params?.flow === "IMPORT" ? "imports" : "exports");
  const period = String(answer.period ?? q?.params?.period ?? "");
  const rootProduct = String(answer.root || q?.params?.product || "TOTAL");
  const rootPlain = itemLabel(rootProduct, "", false);
  const rootName =
    rootProduct === "TOTAL" ? "All goods" : showCodes ? `Goods ${rootProduct}` : rootPlain !== rootProduct ? rootPlain : "These goods";
  const total = typeof answer.total === "number" ? answer.total : 0;
  const root: TreeNode = { code: rootProduct, name: rootName, value: total, children: (answer.children || []).map(toNode) };
  const caption = <PanelCaption panel={panel} facts={data.facts} />;
  if (!root.children || !root.children.length) {
    return <ChartFrame chart={<EmptyNote>No goods to show for {formatPeriod(period)}.</EmptyNote>} caption={caption} />;
  }

  // Follow the path as far as the data allows.
  const trail: TreeNode[] = [root];
  for (let i = 0; i < path.length; i++) {
    const next = (trail[trail.length - 1].children || []).find((c) => c.code === path[i]);
    if (!next) break;
    trail.push(next);
  }
  const node = trail[trail.length - 1];
  const o = panel.options || {};
  // The backend's ladder already folded the long tail; the limit only guards against clutter.
  const limit = typeof o.limit === "number" ? o.limit : 24;

  const tell = (target: TreeNode | null) => {
    const mine = (spec?.marks || []).find((m) => m.target === panel.id && m.agent === "user" && m.kind === "highlight" && m.status !== "kept");
    if (!target) {
      if (mine) send([{ op: "remove_mark", id: mine.id }], { message: "Back to all goods", baseVersion: null });
      return;
    }
    // A name safe in a note: no {fact} braces, no figures the validator reads as typed-in amounts.
    const name = label(target).replace(/[{}]/g, "").replace(/%/g, " percent").replace(/[€$£]/g, "").replace(/\s+/g, " ").trim().slice(0, 80);
    const params = { codes: [target.code], label: `Opened: ${name}` };
    const note = `You opened ${name}.`;
    if (mine) send([{ op: "update_mark", id: mine.id, set: { params, note } }], { message: note, baseVersion: null });
    else send([{ op: "add_mark", mark: { kind: "highlight", target: panel.id, params, note } }], { message: note, baseVersion: null });
  };
  const openTo = (codes: string[], target: TreeNode | null) => {
    setPath(codes);
    tell(target);
  };

  const pm = penMarks(marks.filter((d) => !(d.mark.agent === "user" && d.mark.kind === "highlight")), data.ctx, ["highlight"]);
  const ramp = rampFor(tone);
  const title = `${panel.title}, ${formatPeriod(period)}: ${label(node)} as nested tiles`;
  const kids = (node.children || []).slice().sort((a, b) => b.value - a.value);

  const crumbs = (
    <nav aria-label="Where you are in the goods" style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 4, fontFamily: FONT_UI, fontSize: 13.5 }}>
      {trail.map((n, i) => {
        const last = i === trail.length - 1;
        return (
          <React.Fragment key={n.code + i}>
            {i > 0 && (
              <span aria-hidden="true" style={{ color: TOKENS.field, padding: "0 2px" }}>
                /
              </span>
            )}
            {last ? (
              <span aria-current="page" style={{ fontWeight: 700, color: TOKENS.ink }}>
                {i === 0 ? n.name : label(n)}
              </span>
            ) : (
              <button
                type="button"
                onClick={() => openTo(path.slice(0, i), i === 0 ? null : trail[i])}
                style={{
                  font: "inherit",
                  color: TOKENS.skyText,
                  fontWeight: 600,
                  background: "none",
                  border: 0,
                  padding: "6px 2px",
                  minHeight: 32,
                  cursor: "pointer",
                  textDecoration: "underline",
                  textUnderlineOffset: 3,
                }}
              >
                {i === 0 ? n.name : label(n)}
              </button>
            )}
          </React.Fragment>
        );
      })}
      {trail.length > 1 && <span style={{ color: TOKENS.muted, marginLeft: 8 }}>{fmtEur(node.value)}, {fmtPct(total ? node.value / total : 0)} of the total</span>}
    </nav>
  );

  return (
    <ChartFrame
      chart={
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {crumbs}
          <Treemap
            node={node}
            total={total}
            title={title}
            tone={tone}
            marks={pm}
            limit={limit}
            label={label}
            onOpen={(child) => openTo(trail.slice(1).map((n) => n.code).concat([child.code]), child)}
          />
        </div>
      }
      chartKey={
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <RampLegend title="Share of the total" steps={ramp.slice(1, 6)} labels={["0.3%", "1%", "3%", "10%", ""]} />
          <ChartKey marks={pm}>
            <Swatch color={ramp[2]} label="other goods, folded by the significance ladder" shape="hatch" />
          </ChartKey>
        </div>
      }
      note={kids.some((k) => k.children && k.children.length) ? "Click a tile, or press Enter on it, to open its group." : undefined}
      caption={caption}
      table={
        <DataTable
          caption={title}
          columns={["Goods", "Value", "Share of the total"]}
          rows={kids.reduce((acc: (string | number)[][], k) => {
            acc.push([label(k), fmtEur(k.value), fmtPct(total ? k.value / total : 0)]);
            (k.children || [])
              .slice()
              .sort((a, b) => b.value - a.value)
              .forEach((c) => acc.push([`  ${label(c)}`, fmtEur(c.value), fmtPct(total ? c.value / total : 0)]));
            return acc;
          }, [])}
        />
      }
    />
  );
};
