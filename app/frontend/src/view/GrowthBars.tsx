// Growth per year since a year (contract 1.7): a bar panel with options.measure "growth", one query
// "main" on /insights/trends. One row per partner or product, the fastest-growing first: its name, its
// growth per year ("+7.2% a year"), a bar from zero (to the left when it shrank), and under it its value
// in the first and the last year ("€39 m in 1999 → €235 m in 2025"). The endpoint ranks only what is
// above its significance cutoff, and items with nothing in the first year have no rate: the note under
// the bars says both. Marks: highlight rings rows by code, annotation labels a row; clicking a row
// highlights it as the person's own mark, as on any bar.

import React from "react";
import { AboutTag, FactText, NumberCircle, problemCard } from "./common";
import { useShowCodes } from "./codes";
import { formatValue, keyed, useFacts } from "./facts";
import { byWord, flowWord, pctWord, cutoffOf } from "./methods";
import { NUM_STYLE, TOKENS, toneColor } from "./tokens";
import { firstProblem, useQueries } from "./useQuery";
import { OFF_CONTEXT_OPACITY, markOffContext, markPeriod, substituteQueries, usePageContext, useUserHighlight } from "./ViewStore";
import type { DrawnMark } from "./marks";
import type { Panel } from "./types";
import { CHAPTER_SHORT } from "./charts/format";

interface GrowthRow {
  code: string;
  name: string;
  growth: number;
  first?: number;
  last?: number;
}

/** "Nuclear reactors, boilers, ...; parts thereof" -> its short name; product codes in front when the reader sees codes. */
function rowName(code: string, name: string, showCodes: boolean): string {
  const s = CHAPTER_SHORT[code] || (String(name || code).split(";")[0] || code);
  const short = s.length > 48 ? s.slice(0, 46).replace(/[ ,]+\S*$/, "") + "…" : s;
  return showCodes && /^\d{2,8}$/.test(code) ? `${code} ${short}` : short;
}

/** The rows of a /insights/trends answer: items with a growth rate (new items have none), as ranked. */
export function growthRows(data: any, limit?: number): GrowthRow[] {
  const items: any[] = data && Array.isArray(data.items) ? data.items : [];
  const rows = items
    .filter((r) => r && typeof r.growth === "number" && r.is_new !== true)
    .map((r) => ({
      code: String(r.code),
      name: String(r.name ?? r.code),
      growth: r.growth as number,
      first: typeof r.start_value === "number" ? r.start_value : undefined,
      last: typeof r.value === "number" ? r.value : undefined,
    }))
    .sort((a, b) => b.growth - a.growth);
  return limit ? rows.slice(0, limit) : rows;
}

const TWO_LINES: React.CSSProperties = {
  minWidth: 0,
  overflow: "hidden",
  display: "-webkit-box",
  WebkitLineClamp: 2,
  WebkitBoxOrient: "vertical",
  whiteSpace: "normal",
  overflowWrap: "anywhere",
};

export const GrowthBars: React.FC<{ panel: Panel; marks?: DrawnMark[] }> = ({ panel, marks = [] }) => {
  const showCodes = useShowCodes();
  const ctx = usePageContext();
  const o = panel.options || {};
  const limit = typeof o.limit === "number" ? o.limit : undefined;
  const queries = React.useMemo(() => substituteQueries(panel.queries, ctx), [panel.queries, ctx]);
  const states = useQueries(queries);
  const facts = useFacts(panel.facts, keyed(queries, states));
  const user = useUserHighlight(panel.id);
  const q = queries.find((x) => x.key === "main") || queries[0];
  const i = Math.max(0, queries.indexOf(q));
  const problem = firstProblem(states.slice(i, i + 1));
  if (problem) return problemCard(problem, 160);

  const data: any = states[i]?.data || {};
  const params = q?.params || {};
  const rows = growthRows(data, limit);
  const first = String(data.start ?? params.start ?? "");
  const last = String(data.end ?? params.end ?? "");
  const tone = toneColor(q?.tone || (String(params.flow) === "IMPORT" ? "imports" : "exports"));
  const fmt = String(o.format || "pct_signed");

  const byCode: Record<string, Array<{ d: DrawnMark; kind: string; off: boolean }>> = {};
  marks.forEach((d) => {
    const p = d.mark.params || {};
    const off = markOffContext(d.mark, ctx);
    if (d.mark.kind === "highlight") (p.codes || []).forEach((c: string) => (byCode[c] = (byCode[c] || []).concat([{ d, kind: "highlight", off }])));
    if (d.mark.kind === "annotation" && p.x) byCode[String(p.x)] = (byCode[String(p.x)] || []).concat([{ d, kind: "annotation", off }]);
  });
  const seen = new Set<string>();
  const once = (d: DrawnMark) => {
    if (!d.showNumber || seen.has(d.mark.id)) return false;
    seen.add(d.mark.id);
    return true;
  };

  const hi = Math.max(0, ...rows.map((r) => r.growth));
  const lo = Math.min(0, ...rows.map((r) => r.growth));
  const span = hi - lo || 1;
  const zero = (-lo / span) * 100; // where 0 sits on the track, in %

  const threshold = typeof data.threshold === "number" ? data.threshold : cutoffOf(params);
  const who = byWord({ ...params, level: data.level ?? params.level });
  const count = typeof data.n_significant === "number" ? `the ${data.n_significant} ` : "the ";
  const note =
    rows.length > 0
      ? `Average growth per year from ${first} to ${last}, compounded. Ranked among ${count}${who} with at least ${pctWord(threshold)} of ${flowWord(params)} in ${first} or ${last}; those with nothing in ${first} have no rate and are left out.`
      : "";

  if (rows.length === 0) {
    return <span style={{ fontSize: 13, color: TOKENS.muted }}>No {who} above the cutoff with a value in both {first} and {last}.</span>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 12 }}>
        {rows.map((r) => {
          const ms = byCode[r.code] || [];
          const hl = ms.find((m) => m.kind === "highlight");
          const notes = ms.filter((m) => m.kind === "annotation");
          const name = rowName(r.code, r.name, showCodes);
          const rate = `${formatValue(r.growth, fmt)} a year`;
          const values = r.first !== undefined && r.last !== undefined ? `${formatValue(r.first, "eur")} in ${first} → ${formatValue(r.last, "eur")} in ${last}` : "";
          const width = (Math.abs(r.growth) / span) * 100;
          const left = r.growth >= 0 ? zero : zero - width;
          const picked = !!user.markOf(r.code);
          return (
            <li key={r.code} data-mark={hl ? hl.d.mark.id : undefined} style={{ opacity: hl?.off ? 0.75 : 1 }}>
              <button
                type="button"
                className="cx-row"
                aria-pressed={picked}
                aria-label={`${name}: ${rate}${values ? `, ${values}` : ""}. ${picked ? "Take the highlight off" : "Highlight it"}`}
                title={values ? `${r.name}: ${values}` : r.name}
                onClick={() => user.toggle(r.code, name)}
                style={{
                  all: "unset",
                  boxSizing: "border-box",
                  display: "flex",
                  flexDirection: "column",
                  width: "100%",
                  cursor: "pointer",
                  gap: 4,
                  fontSize: 14,
                  paddingLeft: hl ? 10 : 0,
                  borderLeft: hl ? `3px solid ${hl.off ? TOKENS.field : hl.d.color}` : undefined,
                }}
              >
                <span style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "baseline" }}>
                  <span className="cx-row-name" style={{ ...TWO_LINES, color: hl ? hl.d.ink : TOKENS.ink, fontWeight: hl ? 700 : 400 }}>
                    {name}
                  </span>
                  <span style={{ display: "flex", gap: 6, alignItems: "center", flex: "none" }}>
                    {ms.filter((m) => once(m.d)).map((m) => (
                      <NumberCircle key={m.d.mark.id} n={m.d.number} color={m.d.color} size={18} style={{ opacity: m.off ? OFF_CONTEXT_OPACITY : 1 }} />
                    ))}
                    <span style={{ ...NUM_STYLE, color: r.growth >= 0 ? tone.text : TOKENS.steelText, fontWeight: 600 }}>{rate}</span>
                  </span>
                </span>
                <span style={{ position: "relative", height: 6, background: TOKENS.track, display: "block" }}>
                  <span style={{ position: "absolute", top: 0, bottom: 0, left: `${left}%`, width: `${width}%`, background: r.growth >= 0 ? tone.fill : TOKENS.steel }} />
                  {lo < 0 && <span aria-hidden="true" style={{ position: "absolute", top: -3, bottom: -3, left: `calc(${zero}% - 1px)`, width: 2, background: TOKENS.muted }} />}
                </span>
                {values && <span style={{ ...NUM_STYLE, fontSize: 12.5, color: TOKENS.muted }}>{values}</span>}
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
      <span style={{ fontSize: 12.5, color: TOKENS.muted, lineHeight: 1.45 }}>{note}</span>
      {panel.text && (
        <span style={{ fontSize: 13.5, color: TOKENS.ink2, lineHeight: 1.45 }}>
          <FactText template={panel.text} facts={facts} />
        </span>
      )}
    </div>
  );
};
