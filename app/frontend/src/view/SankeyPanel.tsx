// The crossroads: imports by partner on the left ("Bought from"), flowing
// through Luxembourg (the hub column) to exports by partner on the right
// ("Sold to"). The hub is as tall as the larger side, so the deficit shows as
// the gap on the smaller side. Partners under 4% of their side fold into
// "others" (unless a mark names them), labels are spread apart with leader
// lines, and under 600 px the same data is drawn as a butterfly: bought to the
// left, sold to the right, one row per partner.
// A thread mark draws one coloured path through the hub (China -> Luxembourg ->
// France for smartphones); a highlight mark rings partner nodes. Clicking a
// partner highlights it as the person's own mark (agent "user").

import React from "react";
import { AboutTag, NumberCircle, problemCard, spreadLabels, SvgNumber, useWidth } from "./common";
import { formatEur, keyed, useFacts, fillTemplate, tryPath } from "./facts";
import { withParams } from "./api";
import { FONT_UI, NUM_STYLE, TOKENS, isAgentOwner } from "./tokens";
import { firstProblem, useQueries, useQuery } from "./useQuery";
import { OFF_CONTEXT_OPACITY, markOffContext, markPeriod, substituteQueries, useUserHighlight, usePageContext } from "./ViewStore";
import type { DrawnMark } from "./marks";
import type { Panel, Query } from "./types";

interface Row {
  code: string;
  name: string;
  value: number;
  other?: boolean;
}

interface Side {
  total: number;
  rows: Row[];
}

/** Below this share of its side, a partner folds into "others". */
const FOLD_SHARE = 0.04;

/** Partners of one side from a /insights/ranking (or /insights/flows side) response. */
function sideOf(data: any, flowsKey: "in" | "out" | undefined, keep: Set<string>): Side | null {
  const d = flowsKey && data && data[flowsKey] ? data[flowsKey] : data;
  if (!d || !Array.isArray(d.items)) return null;
  const items = d.items.filter((it: any) => typeof it?.value === "number" && it.value > 0);
  const named = items.reduce((s: number, it: any) => s + it.value, 0);
  const total = typeof d.total === "number" ? d.total : named + (d.other?.value || 0);
  const rows: Row[] = [];
  let folded = 0;
  let foldedCount = 0;
  items.forEach((it: any) => {
    const code = String(it.code);
    if (it.value < total * FOLD_SHARE && !keep.has(code)) {
      folded += it.value;
      foldedCount += 1;
    } else rows.push({ code, name: shortPartner(String(it.name ?? it.code)), value: it.value });
  });
  const rest = total - named + folded;
  if (rest > total * 0.0005) {
    const count = (typeof d.other?.count === "number" ? d.other.count : 0) + foldedCount;
    rows.push({ code: "OTHER", name: count ? `${count} others` : "Others", value: rest, other: true });
  }
  return { total, rows };
}

/** "United States of America" -> "United States"; "China (People's Republic of)" -> "China". */
function shortPartner(name: string): string {
  return name
    .replace(/\s*\(.*?\)\s*/g, " ")
    .replace(/ of America$/, "")
    .trim();
}

const W = 1000;
const TOP = 46;
const IN_LABEL_X = 0;
const NODE_W = 8;
const HUB_X0 = 480;
const HUB_X1 = 520;
const OUT_VALUE_X = 1000;
const GAP = 6;
const MIN_SLOT = 18;
const HUB_PX = 350;
const LABEL_GAP = 17;

function f(n: number): string {
  return n.toFixed(1);
}

/** A ribbon of height h from (x0, y0) to (x1, y1), as a filled cubic band. */
function ribbon(x0: number, y0: number, x1: number, y1: number, h: number): string {
  const xm = (x0 + x1) / 2;
  return (
    `M${x0} ${f(y0)} C${xm} ${f(y0)} ${xm} ${f(y1)} ${x1} ${f(y1)} ` +
    `L${x1} ${f(y1 + h)} C${xm} ${f(y1 + h)} ${xm} ${f(y0 + h)} ${x0} ${f(y0 + h)} Z`
  );
}

interface NodeGeo extends Row {
  /** Node bar top and height. */
  y: number;
  h: number;
  /** Label centre (after spreading). */
  ty: number;
  /** Slot on the hub. */
  ly: number;
}

function stack(side: Side, k: number, startY: number): NodeGeo[] {
  let y = startY;
  let ly = TOP;
  const nodes = side.rows.map((r) => {
    const h = Math.max(r.value * k, 1.5);
    const slot = Math.max(h + GAP, MIN_SLOT);
    const node: NodeGeo = { ...r, y: y + (slot - GAP - h) / 2, h, ty: y + (slot - GAP) / 2, ly };
    y += slot;
    ly += r.value * k;
    return node;
  });
  const spread = spreadLabels(
    nodes.map((n) => n.y + n.h / 2),
    LABEL_GAP,
    startY + 6,
  );
  nodes.forEach((n, i) => (n.ty = spread[i]));
  return nodes;
}

/** The countries that border the page's reporter: the crossroads says which of them it sells more to. */
const NEIGHBOURS: Record<string, string[]> = { Luxembourg: ["BE", "DE", "FR"] };

/** options.neighbours, or the reporter's own neighbours on the page's own crossroads. */
function neighboursOf(panel: Panel, centerLabel: string): string[] {
  const o = panel.options?.neighbours;
  if (Array.isArray(o)) return o.map(String);
  return !isAgentOwner(panel.agent) ? NEIGHBOURS[centerLabel] || [] : [];
}

type PartnerValues = Record<string, { name: string; value: number }>;

function partnerValues(side: any): PartnerValues {
  const out: PartnerValues = {};
  (side && Array.isArray(side.items) ? side.items : []).forEach((it: any) => {
    if (typeof it?.value === "number") out[String(it.code)] = { name: shortPartner(String(it.name ?? it.code)), value: it.value };
  });
  return out;
}

/**
 * "France is the only neighbour Luxembourg sells more to than it buys from", from the partners'
 * imports and exports; undefined when a neighbour is missing on either side (no claim on half the data).
 */
export function neighbourCaption(center: string, neighbours: string[], bought: PartnerValues, sold: PartnerValues): string | undefined {
  if (!neighbours.length || neighbours.some((c) => !bought[c] || !sold[c])) return undefined;
  const surplus = neighbours.filter((c) => sold[c].value > bought[c].value).map((c) => sold[c].name);
  const and = (xs: string[]) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);
  if (surplus.length === 0) return `${center} buys more from each of its neighbours than it sells to them`;
  if (surplus.length === neighbours.length) return `${center} sells more to each of its neighbours than it buys from them`;
  if (surplus.length === 1) return `${surplus[0]} is the only neighbour ${center} sells more to than it buys from`;
  return `${and(surplus)} are the neighbours ${center} sells more to than it buys from`;
}

function findNode(nodes: NodeGeo[], code: string): NodeGeo | undefined {
  return nodes.find((n) => n.code === code) || nodes.find((n) => n.other);
}

export interface SankeyPanelProps {
  panel: Panel;
  marks?: DrawnMark[];
}

export const SankeyPanel: React.FC<SankeyPanelProps> = ({ panel, marks = [] }) => {
  const ctx = usePageContext();
  const queries = React.useMemo(() => substituteQueries(panel.queries, ctx), [panel.queries, ctx]);
  const qIn = queries.find((q) => q.key === "in") || queries[0];
  const qOut = queries.find((q) => q.key === "out") || queries[1];
  const states = useQueries([qIn, qOut]);
  const facts = useFacts(panel.facts, keyed(queries, states));
  const { ref, width } = useWidth<HTMLDivElement>(900);
  const user = useUserHighlight(panel.id);

  // Thread widths: the product's flows with the two partners, when a product is given.
  const threads = marks.filter((d) => d.mark.kind === "thread");
  const threadQueries: Array<Query | null> = [];
  threads.forEach((d) => {
    const product = d.mark.params?.product;
    if (product && qIn && qOut) {
      threadQueries.push(withParams(qIn, { product: String(product), max_items: 30 }));
      threadQueries.push(withParams(qOut, { product: String(product), max_items: 30 }));
    } else {
      threadQueries.push(null, null);
    }
  });
  const threadStates = useQueries(threadQueries);

  // Which neighbours Luxembourg sells more to than it buys from: /insights/flows for the year shown (a
  // From-To range reads the panel's own totals over the range, which /insights/flows does not sum).
  const centerName = String(panel.options?.center_label ?? "Luxembourg");
  const neighbours = neighboursOf(panel, centerName);
  const ranged = !!qIn?.params?.start && qIn.params.start !== qIn.params.period;
  const flowsQuery =
    neighbours.length && !ranged && qIn?.params?.period
      ? {
          endpoint: "/insights/flows",
          params: { period: qIn.params.period, max_items: 300, threshold: 0, ...(qIn.params.product ? { product: qIn.params.product } : {}) },
        }
      : null;
  const flows = useQuery(flowsQuery);

  const problem = firstProblem(states);
  if (problem) return <div ref={ref}>{problemCard(problem, 360)}</div>;

  // Partners a mark names never fold into "others".
  const keepIn = new Set<string>();
  const keepOut = new Set<string>();
  marks.forEach((d) => {
    const p = d.mark.params || {};
    if (d.mark.kind === "thread") {
      keepIn.add(String(p.in_code));
      keepOut.add(String(p.out_code));
    }
    if (d.mark.kind === "highlight") (p.codes || []).forEach((c: string) => (keepIn.add(c), keepOut.add(c)));
  });

  const left = sideOf(states[0].data, qIn?.endpoint === "/insights/flows" ? "in" : undefined, keepIn);
  const right = sideOf(states[1].data, qOut?.endpoint === "/insights/flows" ? "out" : undefined, keepOut);
  if (!left || !right) {
    return (
      <div ref={ref}>
        {problemCard({ kind: "error", state: { ...states[0], error: { status: 0, message: "The response has no items to draw", url: states[0].url } } })}
      </div>
    );
  }

  const centerLabel = String(panel.options?.center_label ?? "Luxembourg");
  const gapText = panel.text ? fillTemplate(panel.text, facts.values) : "";
  const neighbourText = !neighbours.length
    ? undefined
    : ranged
      ? neighbourCaption(centerLabel, neighbours, partnerValues(states[0].data), partnerValues(states[1].data))
      : flows.ready
        ? neighbourCaption(centerLabel, neighbours, partnerValues(flows.data?.in), partnerValues(flows.data?.out))
        : undefined;
  const inLabel = qIn?.label || "Bought from";
  const outLabel = qOut?.label || "Sold to";

  // Highlights and threads by node.
  const highlighted: Record<string, DrawnMark> = {};
  marks
    .filter((d) => d.mark.kind === "highlight")
    .forEach((d) => (d.mark.params?.codes || []).forEach((c: string) => (highlighted[c] = highlighted[c] || d)));
  const threadCodes: Record<string, DrawnMark> = {};
  threads.forEach((d) => {
    threadCodes[`in:${d.mark.params?.in_code}`] = d;
    threadCodes[`out:${d.mark.params?.out_code}`] = d;
  });
  const markOn = (side: "in" | "out", code: string) => threadCodes[`${side}:${code}`] || highlighted[code];
  const opacityOf = (d: DrawnMark | undefined) => (d && markOffContext(d.mark, ctx) ? OFF_CONTEXT_OPACITY : 1);

  const top3 = (s: Side) =>
    s.rows
      .filter((r) => !r.other)
      .slice(0, 3)
      .map((r) => `${r.name} ${formatEur(r.value)}`)
      .join(", ");
  const aria =
    `Flow diagram: ${formatEur(left.total)} bought, mostly from ${top3(left)}; ` +
    `through ${centerLabel}; ${formatEur(right.total)} sold, mostly to ${top3(right)}.` +
    (gapText ? ` ${gapText}.` : "");

  const pick = (code: string, name: string) => {
    if (code === "OTHER") return;
    user.toggle(code, name);
  };

  // ------------------------------------------------------------ narrow: the butterfly
  if (width < 600) {
    return (
      <div ref={ref}>
        <Butterfly
          left={left}
          right={right}
          inLabel={inLabel}
          outLabel={outLabel}
          markOn={markOn}
          opacityOf={opacityOf}
          gapText={gapText}
          caption={neighbourText}
          aria={aria}
          onPick={pick}
        />
      </div>
    );
  }

  // ------------------------------------------------------------ wide: the Sankey
  // Text sizes are in viewBox units; keep them readable when the chart is drawn narrower than 1000 px.
  const scale = Math.max(1, (1000 / Math.max(600, width)) * 0.92);
  const fs = (n: number) => n * scale;
  // Label columns grow with the text, so names and values never run into each other.
  const IN_VALUE_X = Math.round(152 * Math.min(scale, 1.4));
  const IN_NODE_X = IN_VALUE_X + 10;
  const OUT_NODE_X = W - Math.round(170 * Math.min(scale, 1.4));
  const OUT_LABEL_X = OUT_NODE_X + 18;
  const k = HUB_PX / Math.max(left.total, right.total, 1);
  const inNodes = stack(left, k, TOP - 20);
  const outNodes = stack(right, k, TOP - 24);
  const hubH = Math.max(left.total, right.total) * k;
  const inH = left.total * k;
  const outH = right.total * k;
  const lastY = Math.max(
    ...inNodes.map((n) => Math.max(n.y + n.h, n.ty + 6)),
    ...outNodes.map((n) => Math.max(n.y + n.h, n.ty + 6)),
    TOP + hubH,
  );
  const height = Math.ceil(lastY + 24);
  const showGap = panel.options?.show_gap !== false;
  const inDeficit = inH >= outH;
  const gapY0 = TOP + Math.min(inH, outH);
  const gapY1 = TOP + Math.max(inH, outH);
  const dim = threads.length > 0 ? 0.75 : 1;

  const node = (side: "in" | "out", n: NodeGeo) => {
    const m = markOn(side, n.code);
    const mine = user.markOf(n.code);
    const nodeX = side === "in" ? IN_NODE_X : OUT_NODE_X;
    const labelX = side === "in" ? IN_LABEL_X : OUT_LABEL_X;
    const valueX = side === "in" ? IN_VALUE_X : OUT_VALUE_X;
    const fill = side === "in" ? TOKENS.steelText : TOKENS.sky;
    const valueFill = side === "in" ? TOKENS.steelText : TOKENS.skyText;
    const moved = Math.abs(n.ty - (n.y + n.h / 2)) > 2;
    const leaderX0 = side === "in" ? IN_VALUE_X + 2 : nodeX + NODE_W + 1;
    const leaderX1 = side === "in" ? nodeX - 1 : OUT_LABEL_X - 4;
    const clickable = !n.other;
    const verb = side === "in" ? "bought from" : "sold to";
    return (
      <g
        key={`${side}-${n.code}`}
        data-mark={m ? m.mark.id : undefined}
        role={clickable ? "button" : undefined}
        tabIndex={clickable ? 0 : undefined}
        aria-label={clickable ? `${n.name}: ${formatEur(n.value)} ${verb}. ${mine ? "Take the highlight off" : "Highlight it"}` : undefined}
        aria-pressed={clickable ? !!mine : undefined}
        onClick={clickable ? () => pick(n.code, n.name) : undefined}
        onKeyDown={
          clickable
            ? (e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  pick(n.code, n.name);
                }
              }
            : undefined
        }
        className={clickable ? "cx-node" : undefined}
        style={{ cursor: clickable ? "pointer" : "default" }}
      >
        {clickable && <rect x={side === "in" ? IN_LABEL_X : OUT_NODE_X - 4} y={f(Math.min(n.y, n.ty - 9))} width={side === "in" ? IN_NODE_X + NODE_W + 4 : W - OUT_NODE_X + 4} height={f(Math.max(n.h, 18))} fill="transparent" />}
        <rect x={nodeX} y={f(n.y)} width={NODE_W} height={f(n.h)} fill={fill} />
        {m && highlighted[n.code] === m && (
          <rect x={nodeX - 3} y={f(n.y - 3)} width={NODE_W + 6} height={f(n.h + 6)} fill="none" stroke={m.color} strokeWidth={2.5} opacity={opacityOf(m)} />
        )}
        {moved && <path d={`M${leaderX0} ${f(n.ty - 4)} L${leaderX1} ${f(n.y + n.h / 2)}`} stroke={TOKENS.field} strokeWidth={1} fill="none" />}
        <text x={labelX} y={f(n.ty)} dy="0.35em" style={{ fontSize: fs(13), fill: m ? m.ink : TOKENS.ink, fontWeight: m ? 700 : n.other ? 400 : 500, opacity: m ? Math.max(0.6, opacityOf(m)) : 1 }}>
          {n.name}
        </text>
        <text x={valueX} y={f(n.ty)} dy="0.35em" textAnchor="end" style={{ fontSize: fs(13), fill: valueFill, ...NUM_STYLE }}>
          {formatEur(n.value)}
        </text>
      </g>
    );
  };

  return (
    <div ref={ref} style={{ minWidth: 0 }}>
      <svg viewBox={`0 0 ${W} ${height}`} role="group" aria-label={aria} style={{ width: "100%", height: "auto", display: "block", overflow: "visible", fontFamily: FONT_UI }}>
        <style>{`
          @keyframes vthread { from { clip-path: inset(0 100% 0 0); } to { clip-path: inset(0 0 0 0); } }
          .vthread { animation: vthread 1.6s cubic-bezier(.6,0,.2,1) .2s both; }
          .cx-node:hover text, .cx-node:focus-visible text { text-decoration: underline; }
          .cx-node:focus { outline: none; }
          .cx-node:focus-visible rect:first-child { stroke: ${TOKENS.sky}; stroke-width: 2; }
          @media (prefers-reduced-motion: reduce) { .vthread { animation: none; } }
        `}</style>
        <g aria-hidden="true">
          {inNodes.map((n) => (
            <path key={`ri-${n.code}`} d={ribbon(IN_NODE_X + NODE_W, n.y, HUB_X0, n.ly, n.h)} fill={TOKENS.steel} fillOpacity={(n.other ? 0.18 : 0.38) * dim} />
          ))}
          {outNodes.map((n) => (
            <path key={`ro-${n.code}`} d={ribbon(HUB_X1, n.ly, OUT_NODE_X, n.y, n.h)} fill={TOKENS.sky} fillOpacity={(n.other ? 0.18 : 0.38) * dim} />
          ))}

          {/* column headers */}
          <text x={IN_LABEL_X} y={10} style={{ fontSize: fs(13), fontWeight: 700, fill: TOKENS.steelText }}>
            {inLabel}
          </text>
          <text x={OUT_VALUE_X} y={10} textAnchor="end" style={{ fontSize: fs(13), fontWeight: 700, fill: TOKENS.skyText }}>
            {outLabel}
          </text>

          <rect x={HUB_X0} y={TOP} width={HUB_X1 - HUB_X0} height={f(hubH)} fill={TOKENS.ink} />
          <text x={(HUB_X0 + HUB_X1) / 2} y={TOP - 10} textAnchor="middle" style={{ fontSize: fs(14), fontWeight: 700, fill: TOKENS.ink }}>
            {centerLabel}
          </text>

          {showGap &&
            gapY1 - gapY0 > 4 &&
            (inDeficit ? (
              <g>
                <line x1={530} x2={530} y1={f(gapY0)} y2={f(gapY1)} stroke={TOKENS.ink} strokeWidth={1.25} />
                <line x1={524} x2={530} y1={f(gapY0)} y2={f(gapY0)} stroke={TOKENS.ink} strokeWidth={1.25} />
                <line x1={524} x2={530} y1={f(gapY1)} y2={f(gapY1)} stroke={TOKENS.ink} strokeWidth={1.25} />
                {gapText && (
                  <text x={540} y={f((gapY0 + gapY1) / 2 - (neighbourText ? 8 : 0))} dy="0.35em" style={{ fontSize: fs(15), fontWeight: 700, fill: TOKENS.ink }}>
                    {gapText}
                  </text>
                )}
                {gapText && neighbourText && (
                  <text x={540} y={f((gapY0 + gapY1) / 2 + 8 + fs(13))} dy="0.35em" data-caption="neighbours" style={{ fontSize: fs(13), fill: TOKENS.muted }}>
                    {neighbourText}
                  </text>
                )}
              </g>
            ) : (
              <g>
                <line x1={470} x2={470} y1={f(gapY0)} y2={f(gapY1)} stroke={TOKENS.ink} strokeWidth={1.25} />
                <line x1={470} x2={476} y1={f(gapY0)} y2={f(gapY0)} stroke={TOKENS.ink} strokeWidth={1.25} />
                <line x1={470} x2={476} y1={f(gapY1)} y2={f(gapY1)} stroke={TOKENS.ink} strokeWidth={1.25} />
                {gapText && (
                  <text x={460} y={f((gapY0 + gapY1) / 2 - (neighbourText ? 8 : 0))} dy="0.35em" textAnchor="end" style={{ fontSize: fs(15), fontWeight: 700, fill: TOKENS.ink }}>
                    {gapText}
                  </text>
                )}
                {gapText && neighbourText && (
                  <text x={460} y={f((gapY0 + gapY1) / 2 + 8 + fs(13))} dy="0.35em" textAnchor="end" data-caption="neighbours" style={{ fontSize: fs(13), fill: TOKENS.muted }}>
                    {neighbourText}
                  </text>
                )}
              </g>
            ))}
        </g>

        {inNodes.map((n) => node("in", n))}
        {outNodes.map((n) => node("out", n))}

        <g aria-hidden="true">
          {threads.map((d, i) => {
            const a = findNode(inNodes, String(d.mark.params?.in_code));
            const b = findNode(outNodes, String(d.mark.params?.out_code));
            if (!a || !b) return null;
            const sIn = threadStates[i * 2];
            const sOut = threadStates[i * 2 + 1];
            const vIn = sIn?.ready ? tryPath(sIn.data, `items.[code=${d.mark.params?.in_code}].value`) : undefined;
            const vOut = sOut?.ready ? tryPath(sOut.data, `items.[code=${d.mark.params?.out_code}].value`) : undefined;
            const fallbackH = 3;
            const tc = Math.min(typeof vIn === "number" ? Math.max(vIn * k, 2) : fallbackH, a.h);
            const tf = Math.min(typeof vOut === "number" ? Math.max(vOut * k, 2) : fallbackH, b.h);
            const cx = (HUB_X1 + OUT_NODE_X) / 2;
            const cy = (b.ly + b.y) / 2 + tf / 2;
            const off = markOffContext(d.mark, ctx);
            const title = `${d.mark.params?.label || "Thread"}: ${a.name} through ${centerLabel} to ${b.name}`;
            return (
              <g key={d.mark.id} className="vthread" data-mark={d.mark.id} opacity={off ? OFF_CONTEXT_OPACITY : 1}>
                <title>{title}</title>
                <path d={ribbon(IN_NODE_X + NODE_W, a.y, HUB_X0, a.ly, tc)} fill={d.color} />
                <path d={`M${HUB_X0} ${f(a.ly)} L${HUB_X1} ${f(b.ly)} L${HUB_X1} ${f(b.ly + tf)} L${HUB_X0} ${f(a.ly + tc)} Z`} fill={d.color} />
                <path d={ribbon(HUB_X1, b.ly, OUT_NODE_X, b.y, tf)} fill={d.color} />
                {d.showNumber && <SvgNumber x={cx} y={cy} n={d.number} color={d.color} r={11} />}
              </g>
            );
          })}

          {Object.keys(highlighted)
            .map((code) => highlighted[code])
            .filter((d, i, arr) => arr.indexOf(d) === i && d.showNumber)
            .map((d) => {
              const codes: string[] = d.mark.params?.codes || [];
              const n = inNodes.find((x) => codes.indexOf(x.code) >= 0);
              const o = outNodes.find((x) => codes.indexOf(x.code) >= 0);
              const op = opacityOf(d);
              if (o)
                return (
                  <g key={d.mark.id} opacity={op}>
                    <SvgNumber x={OUT_NODE_X - 14} y={o.y + o.h / 2} n={d.number} color={d.color} />
                  </g>
                );
              if (n)
                return (
                  <g key={d.mark.id} opacity={op}>
                    <SvgNumber x={IN_NODE_X + NODE_W + 14} y={n.y + n.h / 2} n={d.number} color={d.color} />
                  </g>
                );
              return null;
            })}
        </g>
      </svg>
      <OffThreads threads={threads.filter((d) => markOffContext(d.mark, ctx))} />
    </div>
  );
};

/**
 * The threads drawn about another year, named under the diagram with their "about 2025" tag: the tag
 * sits outside the plot, so it never covers a ribbon.
 */
const OffThreads: React.FC<{ threads: DrawnMark[] }> = ({ threads }) => {
  if (!threads.length) return null;
  return (
    <p style={{ display: "flex", flexWrap: "wrap", gap: "4px 12px", alignItems: "center", margin: "6px 0 0", fontFamily: FONT_UI, fontSize: 13, color: TOKENS.muted }}>
      {threads.map((d) => {
        const about = markPeriod(d.mark);
        return (
          <span key={d.mark.id} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            {d.showNumber && <NumberCircle n={d.number} color={d.color} size={18} style={{ opacity: OFF_CONTEXT_OPACITY + 0.2 }} />}
            <span style={{ color: TOKENS.ink2, fontWeight: 600 }}>{String(d.mark.params?.label || "A thread")}</span>
            {about && <AboutTag period={String(about)} />}
          </span>
        );
      })}
      <span>Dimmed: drawn about another year than the one shown.</span>
    </p>
  );
};

// ---------------------------------------------------------------- the butterfly (narrow screens)

const Butterfly: React.FC<{
  left: Side;
  right: Side;
  inLabel: string;
  outLabel: string;
  markOn: (side: "in" | "out", code: string) => DrawnMark | undefined;
  opacityOf: (d: DrawnMark | undefined) => number;
  gapText: string;
  caption?: string;
  aria: string;
  onPick: (code: string, name: string) => void;
}> = ({ left, right, inLabel, outLabel, markOn, opacityOf, gapText, caption, aria, onPick }) => {
  const codes: string[] = [];
  const name: Record<string, string> = {};
  const vin: Record<string, number> = {};
  const vout: Record<string, number> = {};
  left.rows.forEach((r) => {
    if (!r.other && codes.indexOf(r.code) < 0) codes.push(r.code);
    name[r.code] = r.name;
    vin[r.code] = r.value;
  });
  right.rows.forEach((r) => {
    if (!r.other && codes.indexOf(r.code) < 0) codes.push(r.code);
    name[r.code] = name[r.code] || r.name;
    vout[r.code] = r.value;
  });
  codes.sort((a, b) => Math.max(vin[b] || 0, vout[b] || 0) - Math.max(vin[a] || 0, vout[a] || 0));
  const otherIn = left.rows.find((r) => r.other);
  const otherOut = right.rows.find((r) => r.other);
  const max = Math.max(1, ...codes.map((c) => Math.max(vin[c] || 0, vout[c] || 0)), otherIn?.value || 0, otherOut?.value || 0);

  const rowOf = (code: string, label: string, a?: number, b?: number, other?: boolean) => {
    const mi = markOn("in", code);
    const mo = markOn("out", code);
    const m = mi || mo;
    return (
      <li key={code} data-mark={m ? m.mark.id : undefined} style={{ display: "flex", flexDirection: "column", gap: 3, opacity: m ? Math.max(0.6, opacityOf(m)) : 1 }}>
        <button
          type="button"
          disabled={other}
          onClick={() => onPick(code, label)}
          style={{
            all: "unset",
            cursor: other ? "default" : "pointer",
            textAlign: "center",
            fontSize: 14,
            fontWeight: m ? 700 : 500,
            color: m ? m.ink : other ? TOKENS.muted : TOKENS.ink,
            minHeight: 24,
          }}
        >
          {label}
          {m && m.showNumber && <NumberDot n={m.number} color={m.color} />}
        </button>
        <span style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4, alignItems: "center" }}>
          <span style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: 13, color: TOKENS.steelText, whiteSpace: "nowrap", ...NUM_STYLE }}>{typeof a === "number" ? formatEur(a) : ""}</span>
            <span style={{ height: 10, width: `${((a || 0) / max) * 70}%`, background: mi ? mi.color : TOKENS.steel, opacity: other ? 0.5 : 1 }} />
          </span>
          <span style={{ display: "flex", justifyContent: "flex-start", alignItems: "center", gap: 6 }}>
            <span style={{ height: 10, width: `${((b || 0) / max) * 70}%`, background: mo ? mo.color : TOKENS.sky, opacity: other ? 0.5 : 1 }} />
            <span style={{ fontSize: 13, color: TOKENS.skyText, whiteSpace: "nowrap", ...NUM_STYLE }}>{typeof b === "number" ? formatEur(b) : ""}</span>
          </span>
        </span>
      </li>
    );
  };

  return (
    <div role="group" aria-label={aria} style={{ display: "flex", flexDirection: "column", gap: 10, fontFamily: FONT_UI }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4, fontSize: 14, fontWeight: 700 }}>
        <span style={{ textAlign: "right", color: TOKENS.steelText }}>
          {inLabel} <span style={{ ...NUM_STYLE, fontWeight: 600 }}>{formatEur(left.total)}</span>
        </span>
        <span style={{ color: TOKENS.skyText }}>
          {outLabel} <span style={{ ...NUM_STYLE, fontWeight: 600 }}>{formatEur(right.total)}</span>
        </span>
      </div>
      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 10 }}>
        {codes.map((c) => rowOf(c, name[c], vin[c], vout[c]))}
        {(otherIn || otherOut) && rowOf("OTHER", "All other partners", otherIn?.value, otherOut?.value, true)}
      </ol>
      {gapText && <span style={{ fontSize: 15, fontWeight: 700, color: TOKENS.ink, textAlign: "center" }}>{gapText}</span>}
      {caption && <span data-caption="neighbours" style={{ fontSize: 13, lineHeight: 1.45, color: TOKENS.muted, textAlign: "center" }}>{caption}</span>}
    </div>
  );
};

const NumberDot: React.FC<{ n: number; color: string }> = ({ n, color }) => (
  <span
    aria-hidden="true"
    style={{
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      width: 18,
      height: 18,
      marginLeft: 6,
      borderRadius: "50%",
      background: color,
      color: "#FFFFFF",
      fontSize: 11,
      fontWeight: 700,
      verticalAlign: 1,
      ...NUM_STYLE,
    }}
  >
    {n}
  </span>
);
