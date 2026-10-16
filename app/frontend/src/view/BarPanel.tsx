// Bar panels, in four quiet shapes chosen from the panel's options:
// - horizontal: a ranked list with thin bars (what Luxembourg sells);
// - reference (e.g. 1 for RCA): a dot on a track with the reference tick;
// - highlight_focus: every row as a dot on one track, the focus row (LU)
//   filled and named (among the 27 EU countries);
// - otherwise vertical bars, drawn by the generated BarChartComponent.
// - measure "growth" (contract 1.7): growth per year since a year, drawn by GrowthBars.
// Several queries (options.drill) draw side by side, one list per query.
// Marks: highlight rings rows by code; annotation points at a row by code.
// Clicking a row highlights it as the person's own mark (agent "user").
// Queries follow the page context ("$period" ...); a mark about another period
// is dimmed with an "about ..." tag.

import React from "react";
import { BarChartComponent, BarChartMark } from "../components/charts/BarChartComponent";
import { AboutTag, FactText, NumberCircle, codeCase, problemCard, useWidth } from "./common";
import { useShowCodes, withoutCodes } from "./codes";
import { formatEur, formatValue, keyed, useFacts } from "./facts";
import { FIGURE_STYLE, FONT_UI, NUM_STYLE, TOKENS, toneColor } from "./tokens";
import { firstProblem, useQueries } from "./useQuery";
import { OFF_CONTEXT_OPACITY, markOffContext, markPeriod, substituteQueries, usePageContext, useUserHighlight } from "./ViewStore";
import type { DrawnMark } from "./marks";
import { ComparePanel } from "./ComparePanel";
import { isComparison } from "./compare";
import { GrowthBars } from "./GrowthBars";
import type { Panel, Query } from "./types";
import { CHAPTER_SHORT as SHORT } from "./charts/format";

interface Row {
  code: string;
  name: string;
  value: number;
  focus: boolean;
  raw: any;
  /** What tells it apart from the other rows (distinctNames), when the names share a long wording. */
  short?: string;
}

/** Rows of a records-like response: an array, or {items}, {rows}. */
function rowsOf(data: any): any[] {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.items)) return data.items;
  if (data && Array.isArray(data.rows)) return data.rows;
  return [];
}


/** "Nuclear reactors, boilers, machinery and ...; parts thereof" -> a short name. */
export function shortName(name: string, code?: string): string {
  if (code && SHORT[code]) return SHORT[code];
  const s = String(name || "");
  const cut = s.indexOf(";");
  const first = cut > 0 ? s.slice(0, cut) : s;
  return first.length > 48 ? first.slice(0, 46).replace(/[ ,]+\S*$/, "") + "…" : first;
}

// The combined nomenclature's long wordings, in the words a reader uses (CN8 car lines, engines, sizes).
const PLAIN_WORDING: Array<[RegExp, string]> = [
  [/\bspark-ignition internal combustion reciprocating piston engine\b/gi, "petrol engine"],
  [/\bcompression-ignition internal combustion piston engine \(diesel or semi-diesel\)/gi, "diesel engine"],
  [/\bas motors for propulsion\b/gi, ""],
  [/\belectric motor for propulsion\b/gi, "electric motor"],
  [/,?\s*capable of being charged by plugging to external source of electric power/gi, ", plug-in"],
  [/\bof a cylinder capacity\s*/gi, ""],
  [/>\s*([\d.]+)\s*cm³\s*but\s*<=\s*([\d.]+)\s*cm³/g, "$1–$2 cm³"],
  [/<=\s*([\d.]+)\s*cm³/g, "up to $1 cm³"],
  [/>\s*([\d.]+)\s*cm³/g, "over $1 cm³"],
  [/(\d)\.(\d{3})\b/g, "$1,$2"],
];

/** Where two names part ways: the longest shared start (or end), cut back to a word boundary. */
function sharedEdge(names: string[], fromEnd: boolean): number {
  const a = names.map((n) => (fromEnd ? Array.from(n).reverse().join("") : n));
  let i = 0;
  while (a.every((s) => i < s.length && s[i] === a[0][i])) i += 1;
  const s = a[0].slice(0, i);
  const cut = Math.max(s.lastIndexOf(" "), s.lastIndexOf(","));
  return cut > 0 ? cut : 0;
}

/**
 * Siblings with one long official wording ("Motor cars and other motor vehicles principally designed for the
 * transport of <10 persons, … with only diesel engine …") are told apart by what differs: the shared start and end
 * are dropped, a trailing "(excl. …)" goes, and the nomenclature's long phrases get their plain words. The full
 * official name stays in the tooltip. Rows whose names are short or share little are returned as they are.
 */
export function distinctNames(names: string[]): string[] {
  if (names.length < 2) return names;
  const head = sharedEdge(names, false);
  const tail = sharedEdge(names, true);
  if (head < 25 && tail < 25) return names;
  return names.map((n) => {
    let s = n.slice(head >= 25 ? head : 0, tail >= 25 ? n.length - tail : n.length);
    s = s.replace(/\s*\(excl\.[^)]*\)?\s*$/i, "");
    PLAIN_WORDING.forEach(([rx, to]) => (s = s.replace(rx, to)));
    s = s.replace(/^[\s,;:-]+|[\s,;:-]+$/g, "").replace(/^(?:incl\.\s+|with\s+|of\s+|for\s+)/i, "").replace(/^(?:only|both)\s+/i, "");
    s = s.replace(/\s{2,}/g, " ").replace(/\s+,/g, ",");
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : n;
  });
}

/** A long name wraps to a second line before it is cut; the whole name is in the title tooltip. */
const TWO_LINES: React.CSSProperties = {
  minWidth: 0,
  overflow: "hidden",
  display: "-webkit-box",
  WebkitLineClamp: 2,
  WebkitBoxOrient: "vertical",
  whiteSpace: "normal",
  overflowWrap: "anywhere",
};

/** Product codes are shown in front of their name (84 Machinery); country codes are not. */
/** A row's label: "85 Electrical machinery", or the name alone for a reader whose profile hides codes. */
function displayName(r: Row, xField: string, showCodes = true): string {
  if (xField === "code") return r.code;
  const name = r.code && SHORT[r.code] ? SHORT[r.code] : shortName(r.short || r.name, r.code);
  return showCodes && /^\d{2,8}$/.test(r.code) ? `${r.code} ${name}` : name;
}

function toRows(data: any, xField: string, yField: string, exclude: string[], limit?: number): Row[] {
  const rows = rowsOf(data)
    .filter((r) => r && typeof r[yField] === "number" && exclude.indexOf(String(r.code)) < 0)
    .map((r): Row => ({
      code: String(r.code ?? r[xField] ?? ""),
      name: String(r.name ?? r[xField] ?? r.code ?? ""),
      value: r[yField] as number,
      focus: r.is_focus === true,
      raw: r,
    }));
  const kept = limit ? rows.slice(0, limit) : rows;
  const short = distinctNames(kept.map((r) => r.name));
  kept.forEach((r, i) => {
    if (short[i] !== r.name) r.short = short[i];
  });
  return kept;
}

interface RowMark {
  d: DrawnMark;
  kind: "highlight" | "annotation";
  off: boolean;
}

function marksByCode(marks: DrawnMark[], offOf: (d: DrawnMark) => boolean): Record<string, RowMark[]> {
  const out: Record<string, RowMark[]> = {};
  marks.forEach((d) => {
    const p = d.mark.params || {};
    const off = offOf(d);
    if (d.mark.kind === "highlight") (p.codes || []).forEach((c: string) => (out[c] = (out[c] || []).concat([{ d, kind: "highlight", off }])));
    if (d.mark.kind === "annotation" && p.x) out[String(p.x)] = (out[String(p.x)] || []).concat([{ d, kind: "annotation", off }]);
  });
  return out;
}

interface ShapeProps {
  rows: Row[];
  format: string;
  xField: string;
  byCode: Record<string, RowMark[]>;
  once: (d: DrawnMark, code: string) => boolean;
  onPick: (r: Row) => void;
  picked: (code: string) => boolean;
  /** The goods the page is about, emphasised in ink. */
  selected?: string;
}

/** A row's own click target: the whole row is a button. */
const rowButton: React.CSSProperties = {
  all: "unset",
  boxSizing: "border-box",
  display: "flex",
  flexDirection: "column",
  width: "100%",
  cursor: "pointer",
};

// ---------------------------------------------------------------- shapes

/** The other measure of a row (contract 1.8): its value in euros beside a count or a weight, or its count beside a
 * value in euros, when the query read both. "" when there is none. */
function otherMeasure(r: Row, format: string): string {
  const raw = r.raw || {};
  if ((format === "tonnes" || format === "qty" || /^qty:/.test(format)) && typeof raw.value_eur === "number") return formatEur(raw.value_eur);
  if ((format === "eur" || format === "compact") && typeof raw.units === "number" && raw.unit) return formatValue(raw.units, `qty:${raw.unit}`);
  if ((format === "eur" || format === "compact") && typeof raw.weight === "number") return formatValue(raw.weight, "tonnes");
  return "";
}

const ListBars: React.FC<ShapeProps & { tone: { fill: string; text: string } }> = ({ rows, format, tone, xField, byCode, once, onPick, picked, selected }) => {
  const showCodes = useShowCodes();
  const max = Math.max.apply(null, rows.map((r) => Math.abs(r.value)).concat([0])) || 1;
  return (
    <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 10 }}>
      {rows.map((r) => {
        const ms = byCode[r.code] || [];
        const hl = ms.find((m) => m.kind === "highlight");
        const notes = ms.filter((m) => m.kind === "annotation");
        const sel = selected === r.code;
        const name = displayName(r, xField, showCodes);
        return (
          <li key={r.code} data-mark={hl ? hl.d.mark.id : undefined} style={{ opacity: hl?.off ? 0.75 : 1 }}>
            <button
              type="button"
              className="cx-row"
              aria-pressed={picked(r.code)}
              aria-label={`${name}: ${formatValue(r.value, format)}${otherMeasure(r, format) ? ` (${otherMeasure(r, format)})` : ""}. ${picked(r.code) ? "Take the highlight off" : "Highlight it"}`}
              onClick={() => onPick(r)}
              style={{ ...rowButton, gap: 4, fontSize: 14, paddingLeft: hl ? 10 : 0, borderLeft: hl ? `3px solid ${hl.off ? TOKENS.field : hl.d.color}` : undefined }}
            >
              <span style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "baseline" }}>
                <span
                  title={r.name}
                  className="cx-row-name"
                  style={{ ...TWO_LINES, color: hl ? hl.d.ink : TOKENS.ink, fontWeight: hl || sel ? 700 : 400 }}
                >
                  {name}
                </span>
                <span style={{ display: "flex", gap: 6, alignItems: "center", flex: "none" }}>
                  {ms.filter((m) => once(m.d, r.code)).map((m) => (
                    <NumberCircle key={m.d.mark.id} n={m.d.number} color={m.d.color} size={18} style={{ opacity: m.off ? OFF_CONTEXT_OPACITY : 1 }} />
                  ))}
                  <span style={{ ...NUM_STYLE, color: tone.text, fontWeight: 600 }}>{formatValue(r.value, format)}</span>
                  {otherMeasure(r, format) && (
                    <span style={{ ...NUM_STYLE, color: TOKENS.muted, fontSize: 12 }}>{otherMeasure(r, format)}</span>
                  )}
                </span>
              </span>
              <span style={{ height: 6, background: TOKENS.track, display: "block" }}>
                <span style={{ display: "block", height: "100%", width: `${(Math.abs(r.value) / max) * 100}%`, background: tone.fill }} />
              </span>
            </button>
            {notes.map((m) => (
              <span key={m.d.mark.id} data-mark={m.d.mark.id} style={{ display: "block", marginTop: 3, fontSize: 13, fontWeight: 600, color: m.d.ink, opacity: m.off ? 0.7 : 1 }}>
                {m.d.mark.params?.label}
                {m.off && markPeriod(m.d.mark) && <AboutTag period={String(markPeriod(m.d.mark))} style={{ marginLeft: 6 }} />}
              </span>
            ))}
          </li>
        );
      })}
    </ol>
  );
};

const DotPlot: React.FC<ShapeProps & { reference: number }> = ({ rows, format, reference, xField, byCode, once, onPick, picked, selected }) => {
  const showCodes = useShowCodes();
  const max = Math.max.apply(null, rows.map((r) => r.value).concat([reference])) * 1.03 || 1;
  const pos = (v: number) => `${Math.max(0, Math.min(100, (v / max) * 100)).toFixed(1)}%`;
  return (
    <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 14 }}>
      {rows.map((r) => {
        const ms = byCode[r.code] || [];
        const hl = ms.find((m) => m.kind === "highlight") || ms[0];
        const name = displayName(r, xField, showCodes);
        return (
          <li key={r.code} data-mark={hl ? hl.d.mark.id : undefined}>
            <button
              type="button"
              className="cx-row"
              aria-pressed={picked(r.code)}
              aria-label={`${name}: ${formatValue(r.value, format)}. ${picked(r.code) ? "Take the highlight off" : "Highlight it"}`}
              onClick={() => onPick(r)}
              style={{ ...rowButton, gap: 6, fontSize: 14 }}
            >
              <span style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "baseline" }}>
                <span
                  title={r.name}
                  className="cx-row-name"
                  style={{ ...TWO_LINES, color: hl ? hl.d.ink : TOKENS.ink, fontWeight: hl || selected === r.code ? 700 : 400 }}
                >
                  {name}
                </span>
                <span style={{ display: "flex", gap: 6, alignItems: "center", flex: "none" }}>
                  {ms.filter((m) => once(m.d, r.code)).map((m) => (
                    <NumberCircle key={m.d.mark.id} n={m.d.number} color={m.d.color} size={18} style={{ opacity: m.off ? OFF_CONTEXT_OPACITY : 1 }} />
                  ))}
                  <span style={{ ...FIGURE_STYLE, fontSize: 20, lineHeight: 1, letterSpacing: 0 }}>{formatValue(r.value, format)}</span>
                </span>
              </span>
              <span style={{ position: "relative", height: 12, display: "block" }}>
                <span style={{ position: "absolute", top: 5, left: 0, right: 0, height: 2, background: TOKENS.track }} />
                <span style={{ position: "absolute", top: 0, left: pos(reference), width: 2, height: 12, background: TOKENS.muted }} />
                <span
                  style={{
                    position: "absolute",
                    top: 1,
                    left: `calc(${pos(r.value)} - 5px)`,
                    width: 10,
                    height: 10,
                    borderRadius: "50%",
                    background: TOKENS.ink,
                    boxShadow: hl ? `0 0 0 3px ${hl.off ? TOKENS.field : hl.d.color}` : undefined,
                  }}
                />
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
};

/** "Luxembourg ◀" (the API marks the focus) -> "Luxembourg". */
function cleanName(name: string): string {
  return String(name || "").replace(/\s*\(.*\)\s*$/, "").replace(/[\s◀◄←<*]+$/u, "").trim();
}

/** Growth reads with its sign. */
function signed(format: string): string {
  return format === "percent" ? "percent_signed" : format === "pct" ? "pct_signed" : format;
}

/**
 * Every row as a dot on one track, by value; the focus row (Luxembourg) is
 * filled in ink and named. Dots that would overlap move to a lane above or
 * below. The track runs from the smallest to the largest value, with a tick at 0.
 */
const DotStrip: React.FC<ShapeProps> = ({ rows, format, byCode, once, onPick, picked }) => {
  const { ref, width } = useWidth<HTMLDivElement>(300);
  const values = rows.map((r) => r.value);
  const lo = Math.min(0, ...values);
  const hi = Math.max(0, ...values);
  const span = hi - lo || 1;
  const pad = 8;
  const px = (v: number) => pad + ((v - lo) / span) * Math.max(10, width - pad * 2);
  const order = rows.map((r, i) => ({ r, i, x: px(r.value) })).sort((a, b) => a.x - b.x);
  const laneEnd = [-Infinity, -Infinity, -Infinity];
  const laneY = [0, -9, 9];
  const lane: Record<string, number> = {};
  const focus = rows.find((r) => r.focus);
  // The focus (Luxembourg) owns the middle lane where it sits: the other dots there go above or below it.
  const focusX = focus ? px(focus.value) : undefined;
  order.forEach(({ r, x }) => {
    if (r.focus) {
      lane[r.code] = 0;
      laneEnd[0] = Math.max(laneEnd[0], x + 4);
      return;
    }
    const nearFocus = focusX !== undefined && Math.abs(x - focusX) < 13;
    const lanes = nearFocus ? [1, 2] : [0, 1, 2];
    let l = lanes.find((k) => laneEnd[k] < x - 10);
    if (l === undefined) l = lanes.reduce((a, b) => (laneEnd[b] < laneEnd[a] ? b : a));
    laneEnd[l] = x;
    lane[r.code] = l;
  });
  const rankOf = (r: Row) => rows.indexOf(r) + 1;
  const minRow = order[0]?.r;
  const maxRow = order[order.length - 1]?.r;
  const H = 54;
  const trackY = 34;
  return (
    <div ref={ref} style={{ position: "relative", height: H, marginTop: 4 }}>
      <span aria-hidden="true" style={{ position: "absolute", left: pad, right: pad, top: trackY - 1, height: 2, background: TOKENS.track }} />
      <span aria-hidden="true" title="0" style={{ position: "absolute", left: px(0) - 1, top: trackY - 8, width: 2, height: 16, background: TOKENS.muted }} />
      {order.map(({ r, x }) => {
        const ms = byCode[r.code] || [];
        const m = ms[0];
        const isFocus = r.focus;
        const size = isFocus ? 14 : 10;
        const y = trackY + (isFocus ? 0 : laneY[lane[r.code]]);
        const label = `${rankOf(r)}. ${cleanName(r.name)}: ${formatValue(r.value, signed(format))}`;
        return (
          <button
            key={r.code}
            type="button"
            title={label}
            aria-label={`${label}. ${picked(r.code) ? "Take the highlight off" : "Highlight it"}`}
            aria-pressed={picked(r.code)}
            data-mark={m ? m.d.mark.id : undefined}
            onClick={() => onPick(r)}
            className="cx-dot"
            style={{
              all: "unset",
              position: "absolute",
              left: x - size / 2,
              top: y - size / 2,
              width: size,
              height: size,
              borderRadius: "50%",
              boxSizing: "border-box",
              cursor: "pointer",
              background: isFocus ? TOKENS.ink : m ? m.d.color : "rgba(255,255,255,0.85)",
              border: `1.5px solid ${isFocus ? TOKENS.ink : m ? m.d.color : TOKENS.dotEmpty}`,
              boxShadow: m && isFocus ? `0 0 0 2.5px ${m.d.color}` : undefined,
              opacity: m?.off ? 0.6 : 1,
              zIndex: isFocus ? 2 : m ? 1 : 0,
            }}
          />
        );
      })}
      {focus && (
        <span
          aria-hidden="true"
          style={{
            position: "absolute",
            top: 0,
            left: Math.min(Math.max(0, px(focus.value) - 40), Math.max(0, width - 120)),
            fontSize: 13,
            fontWeight: 700,
            color: TOKENS.ink,
            whiteSpace: "nowrap",
            ...NUM_STYLE,
          }}
        >
          {cleanName(focus.name)} {formatValue(focus.value, signed(format))}
        </span>
      )}
      {rows
        .filter((r) => (byCode[r.code] || []).some((m) => once(m.d, r.code)))
        .map((r) => {
          const m = (byCode[r.code] || []).find((x) => once(x.d, r.code))!;
          return (
            <span key={`n-${r.code}`} style={{ position: "absolute", left: px(r.value) + 6, top: trackY + 8 }}>
              <NumberCircle n={m.d.number} color={m.d.color} size={16} />
            </span>
          );
        })}
      {minRow && maxRow && (
        <span aria-hidden="true" style={{ position: "absolute", left: 0, right: 0, top: trackY + 12, display: "flex", justifyContent: "space-between", fontSize: 12, color: TOKENS.muted, ...NUM_STYLE }}>
          {/* the ends of the strip, unless one is the focus: its name and figure are already above */}
          <span>{minRow.focus ? "" : `${minRow.code} ${formatValue(minRow.value, signed(format))}`}</span>
          <span>{maxRow.focus ? "" : `${maxRow.code} ${formatValue(maxRow.value, signed(format))}`}</span>
        </span>
      )}
    </div>
  );
};

// ---------------------------------------------------------------- panel

export interface BarPanelProps {
  panel: Panel;
  marks?: DrawnMark[];
}

/** A bar panel. With comparison options (contract 1.2: two snapshots a and b) it is drawn by ComparePanel; with
 * options.measure "growth" (contract 1.7: growth per year since a year, /insights/trends) by GrowthBars. */
export const BarPanel: React.FC<BarPanelProps> = (props) =>
  isComparison(props.panel) ? (
    <ComparePanel panel={props.panel} marks={props.marks} />
  ) : props.panel.options?.measure === "growth" ? (
    <GrowthBars panel={props.panel} marks={props.marks} />
  ) : (
    <Bars {...props} />
  );

const Bars: React.FC<BarPanelProps> = ({ panel, marks = [] }) => {
  const showCodes = useShowCodes(); // the reader's profile may hide HS codes (contract 1.4)
  const ctx = usePageContext();
  const o = panel.options || {};
  const xField = String(o.x || "name");
  const yField = String(o.y || "value");
  // A panel without options (an agent's plain 'bar') still reads as money or shares, never raw numbers.
  const format = String(o.format || (yField === "value" || yField === "change" ? "eur" : yField === "share" || yField === "growth" ? "percent" : "number"));
  const exclude = Array.isArray(o.exclude_codes) ? (o.exclude_codes as string[]) : [];
  const limit = typeof o.limit === "number" ? o.limit : undefined;
  const queries = React.useMemo(() => substituteQueries(panel.queries, ctx), [panel.queries, ctx]);
  const states = useQueries(queries);
  const facts = useFacts(panel.facts, keyed(queries, states));
  const user = useUserHighlight(panel.id);

  // Facts-only queries (e.g. "detail" for the peer count, "ladder" for the caption) do not draw bars.
  const drawn: Array<{ q: Query; i: number }> = queries
    .map((q, i) => ({ q, i }))
    .filter(({ q }, idx) => idx === 0 || !!o.drill || q.tone !== undefined);
  const problem = firstProblem(drawn.map(({ i }) => states[i]));
  if (problem) return problemCard(problem, 160);

  const byCode = marksByCode(marks, (d) => markOffContext(d.mark, ctx));
  const focusMode = !!o.highlight_focus;
  const reference = typeof o.reference === "number" ? o.reference : undefined;
  // The ranked list (direct labels, the page's type) unless the panel asks for vertical bars.
  const horizontal = o.orientation !== "vertical";
  const selected = ctx.product !== "TOTAL" ? ctx.product : undefined;

  let caption: React.ReactNode = null;
  if (panel.text) {
    // "{rank} of {count} for export growth ...": the standing is the figure, the rest its caption.
    const lead = focusMode ? /^\{rank\} of \{count\}\s*/.exec(panel.text) : null;
    caption = lead ? (
      <p style={{ margin: 0, fontSize: 15, lineHeight: 1.5, color: TOKENS.ink }}>
        <span style={{ ...FIGURE_STYLE, display: "block", fontSize: 30, lineHeight: 1, marginBottom: 4 }}>
          <FactText template="{rank} of {count}" facts={facts} />
        </span>
        <FactText template={panel.text.slice(lead[0].length)} facts={facts} />
      </p>
    ) : (
      <span style={{ fontSize: focusMode ? 15 : 13.5, color: focusMode ? TOKENS.ink : TOKENS.ink2, lineHeight: 1.45 }}>
        <FactText template={panel.text} facts={facts} emphasis={focusMode} />
      </span>
    );
  }

  // Each mark's number circle sits on the first row (in drawing order) it touches.
  // A pure lookup, so it survives React rendering a component twice.
  const firstSpot: Record<string, string> = {};
  drawn.forEach(({ i }) =>
    toRows(states[i].data, xField, yField, exclude, limit).forEach((r) =>
      (byCode[r.code] || []).forEach((m) => {
        if (firstSpot[m.d.mark.id] === undefined) firstSpot[m.d.mark.id] = `${i}|${r.code}`;
      }),
    ),
  );
  const onPick = (r: Row) => user.toggle(r.code, displayName(r, xField === "code" ? "name" : xField, showCodes));
  const picked = (code: string) => !!user.markOf(code);

  const one = (q: Query, i: number) => {
    const rows = toRows(states[i].data, xField, yField, exclude, limit);
    const tone = toneColor(q.tone || "exports");
    const once = (d: DrawnMark, code: string) => d.showNumber && firstSpot[d.mark.id] === `${i}|${code}`;
    const shape = { rows, format, xField, byCode, once, onPick, picked, selected };
    if (rows.length === 0) {
      return <span style={{ fontSize: 13, color: TOKENS.muted }}>No rows for {q.label || "this query"}.</span>;
    }
    if (focusMode) return <DotStrip {...shape} />;
    if (reference !== undefined) return <DotPlot {...shape} reference={reference} />;
    if (horizontal) return <ListBars {...shape} tone={tone} />;
    const barMarks: BarChartMark[] = marks
      .filter((d) => d.mark.kind === "highlight" || d.mark.kind === "annotation")
      .map((d) => ({
        id: d.mark.id,
        kind: d.mark.kind as "highlight" | "annotation",
        color: d.color,
        ink: d.ink,
        number: d.number,
        showNumber: d.showNumber,
        codes: d.mark.params?.codes,
        x: d.mark.params?.x,
        label: d.mark.params?.label,
      }));
    const data = rows.map((r) => ({ code: r.code, name: displayName(r, xField, showCodes), value: r.value }));
    return (
      <BarChartComponent
        id={`view-${panel.id}-${q.key || i}`}
        data={data}
        labelField="name"
        dataField="value"
        color={tone.fill}
        marks={barMarks}
        styles={{ minHeight: 0, marginBottom: 0 }}
        options={{ orientation: "vertical", showLegend: false, gridColor: TOKENS.grid, animate: false }}
      />
    );
  };

  if (drawn.length > 1) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 28 }}>
          {drawn.map(({ q, i }) => (
            <div key={q.key || i} style={{ display: "flex", flexDirection: "column", gap: 10, minWidth: 0 }}>
              {q.label && <span style={{ fontFamily: FONT_UI, fontSize: 14, fontWeight: 700, color: toneColor(q.tone).text }}>{showCodes ? codeCase(q.label) : withoutCodes(q.label)}</span>}
              {one(q, i)}
            </div>
          ))}
        </div>
        {caption}
      </div>
    );
  }

  const { q, i } = drawn[0] || { q: queries[0], i: 0 };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {q ? one(q, i) : null}
      {caption}
    </div>
  );
};
