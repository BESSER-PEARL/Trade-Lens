// "What happened" under an agent's reply in the Ask panel: the decision record the agent sends with the reply
// (app/agents/lux_insights/record.py, the "what_happened" field of the agent_reply_str message). Plain words
// first: who answered and why, the calculations by their plain names, what was drawn, the figures checked;
// the exact queries last, folded, for statisticians. A reply without a record shows nothing extra.
import React, { useState } from "react";
import { apiHref } from "./api";
import { madeId } from "./MadeCard";

export interface WhatHappenedRecord {
  type: "what_happened";
  version: number;
  agent?: string;
  agent_title?: string;
  question?: string;
  summary?: string;
  plan?: { how?: string; steps?: Array<{ agent: string; title?: string; why?: string; this_one?: boolean; asked?: string }> };
  /** The calculations grouped by method, with how many of each. */
  methods?: Array<{ name?: string; plain?: string; count?: number; no_calculation?: boolean }>;
  calculations?: Array<{ name?: string; plain?: string; endpoint?: string; url?: string; no_calculation?: boolean }>;
  drawn?: Array<Record<string, any>>;
  replaced?: string[];
  checked?: { in_reply?: number; verified?: number; corrected?: number; unmatched?: number; on_page?: number; words?: string };
  time?: { seconds?: number; since_question?: number | null };
  for_statisticians?: { plan_source?: string; steps?: number; tools?: string[]; refused?: string[]; queries?: string[]; page_version?: number | null };
}

/** The record, if the message carries a well-formed one. */
export function readRecord(payload: Record<string, unknown>): WhatHappenedRecord | undefined {
  const r = payload.what_happened as any;
  return r && typeof r === "object" && r.type === "what_happened" ? (r as WhatHappenedRecord) : undefined;
}

const plural = (n: number, one: string, many = one + "s") => `${n} ${n === 1 ? one : many}`;

const quoted = (s?: string | null) => (s ? `“${s}”` : "");

/** One drawn item in plain words. */
function drawnText(d: Record<string, any>): string {
  switch (d.what) {
    case "chart": {
      const kind = String(d.chart || "chart");
      // "the 'In short' headline" already names itself; "waterfall" takes an article and its title.
      const head = /^the /i.test(kind) ? capital(kind) : `A ${kind} ${quoted(d.title)}`.trim();
      return head + (d.method ? `, from ${lowerFirst(d.method)}` : "");
    }
    case "mark":
      return (
        `Mark ${d.number ?? ""}: a ${d.mark || "note"}`.replace("  ", " ") +
        (d.label ? ` ${quoted(d.label)}` : "") +
        (d.on_title ? ` on ${quoted(d.on_title)}` : "") +
        (d.method ? `, from ${lowerFirst(d.method)}` : "")
      );
    case "title":
      return `The page title: ${quoted(d.text)}`;
    case "page_year":
      return capital(d.text || "Moved the page to another year");
    case "removed":
      return `Removed mark${(d.numbers || []).length === 1 ? "" : "s"} ${(d.numbers || []).join(", ")}`;
    case "cleared":
      return "Cleared what the agents drew" + (d.kept ? ", keeping what you kept" : "");
    default:
      return capital(String(d.text || d.what || ""));
  }
}

function lowerFirst(s: string): string {
  return s && /^[A-Z][a-z]/.test(s) ? s[0].toLowerCase() + s.slice(1) : s;
}

function capital(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

/** "4 calculations, 3 things drawn, 6 figures checked": the toggle's short count. */
export function recordCount(r: WhatHappenedRecord): string {
  // The methods carry how many times each ran; the calculations list each run.
  const calcs = r.methods?.length
    ? r.methods.filter((m) => !m.no_calculation).reduce((n, m) => n + (m.count || 1), 0)
    : (r.calculations || []).filter((c) => !c.no_calculation).length;
  const drawn = (r.drawn || []).filter((d) => ["chart", "mark", "title", "page_year"].includes(d.what)).length;
  const checked = r.checked?.verified ?? 0;
  const parts = [calcs ? plural(calcs, "calculation") : "no calculation"];
  if (drawn) parts.push(`${plural(drawn, "thing")} drawn`);
  if (checked) parts.push(`${plural(checked, "figure")} checked`);
  return parts.join(", ");
}

export const WhatHappened: React.FC<{ id: string | number; record: WhatHappenedRecord }> = ({ id, record: r }) => {
  const [open, setOpen] = useState(false);
  const key = `reply-${id}`;
  const headId = `${madeId(key)}-head`;
  const steps = r.plan?.steps || [];
  const others = steps.filter((s) => !s.this_one);
  const calcs = r.calculations || [];
  // One line per method ("Movers, 5 times"), else one per calculation.
  const methods = r.methods?.length ? r.methods : calcs.map((c) => ({ name: c.name, plain: c.plain, count: 1 }));
  const drawn = (r.drawn || []).map(drawnText).filter(Boolean);
  const queries = r.for_statisticians?.queries || calcs.map((c) => c.url).filter(Boolean) as string[];
  const secs = r.time?.since_question ?? r.time?.seconds;
  const unmatched = r.checked?.unmatched ?? 0;

  return (
    <>
      <button
        type="button"
        className={`ask-made-btn${open ? " is-open" : ""}`}
        aria-expanded={open}
        aria-controls={madeId(key)}
        onClick={() => setOpen((o) => !o)}
      >
        What happened ({recordCount(r)})
      </button>
      {open && (
        <section id={madeId(key)} className="cx-made ask-happened" aria-labelledby={headId}>
          <div className="cx-made-title">
            <h4 id={headId}>What happened</h4>
            <button type="button" className="cx-made-close" onClick={() => setOpen(false)}>
              Close
            </button>
          </div>
          {r.summary && <p className="ask-happened-summary">{r.summary}</p>}
          <dl className="cx-made-rows">
            <dt>Who answered</dt>
            <dd>
              {r.plan?.how ? `${r.plan.how} ` : ""}
              {r.agent_title ? <b>The {r.agent_title.toLowerCase()}</b> : "This agent"}
              {steps.find((s) => s.this_one)?.why ? `: ${steps.find((s) => s.this_one)!.why}.` : "."}
              {others.length > 0 && <> Also asked: {others.map((s) => (s.title || s.agent).toLowerCase()).join(", ")}.</>}
            </dd>
            <dt>Calculated</dt>
            <dd>
              {methods.length === 0 ? (
                "No calculation: the reply needed none."
              ) : (
                <ul className="ask-happened-list">
                  {methods.map((m, i) => (
                    <li key={(m.name || "") + i}>
                      <b>{m.name}</b>
                      {(m.count || 1) > 1 ? `, ${m.count} times` : ""}. {m.plain}
                    </li>
                  ))}
                </ul>
              )}
            </dd>
            <dt>Drawn</dt>
            <dd>
              {drawn.length === 0 ? (
                "Nothing on the page."
              ) : (
                <ul className="ask-happened-list">
                  {drawn.map((t, i) => (
                    <li key={i}>{t}.</li>
                  ))}
                </ul>
              )}
              {(r.replaced || []).length > 0 && <> It took the place of {(r.replaced || []).map((t) => quoted(t)).join(", ")}.</>}
            </dd>
            <dt>Checked</dt>
            <dd>
              {unmatched === 0 && (r.checked?.in_reply ?? 0) > 0 && <span className="cx-badge cx-badge--calc">All matched</span>}
              {r.checked?.words || "No figure in the reply."}
            </dd>
            {typeof secs === "number" && secs > 0 && (
              <>
                <dt>Time</dt>
                <dd>{Math.round(secs)} s from your question to this reply.</dd>
              </>
            )}
          </dl>
          {queries.length > 0 && (
            <details className="cx-made-detail">
              <summary>For statisticians</summary>
              <div className="cx-made-detail-body">
                <ul className="cx-made-queries">
                  {queries.map((u) => (
                    <li key={u} className="cx-made-query">
                      <code>GET {u}</code>{" "}
                      <a href={apiHref(u)} target="_blank" rel="noreferrer">
                        the answer
                      </a>
                    </li>
                  ))}
                </ul>
                {r.for_statisticians?.steps ? (
                  <p className="cx-made-tech">
                    {plural(r.for_statisticians.steps, "model step")}
                    {r.for_statisticians.tools?.length ? `; tools: ${r.for_statisticians.tools.join(", ")}` : ""}
                    {r.for_statisticians.page_version ? `; page version ${r.for_statisticians.page_version}` : ""}.
                  </p>
                ) : null}
              </div>
            </details>
          )}
        </section>
      )}
    </>
  );
};

export default WhatHappened;
