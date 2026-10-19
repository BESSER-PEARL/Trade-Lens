import React, { CSSProperties, useEffect, useMemo, useRef, useState } from "react";
import { AgentBar } from "./AgentBar";
import { revealMarks, useViewStore, usePageContext, markOffContext, markPeriod, withPanelContext } from "../view/ViewStore";
import { agentSocketHref, apiHref } from "../view/api";
import { formatPeriod, keepFigures, shortenReply } from "../view/facts";
import { AboutTag } from "../view/common";
import { MarkText, markSources, noteSubject, storylines, threadWords, type Storyline } from "../view/marks";
import { AGENT_COLORS, AGENT_ORDER } from "../view/tokens";
import { profileOf } from "../view/personas";
import { noteReply, notePrompt } from "../view/panelPrompts";
import { AiHypothesis, CAUSE_FINDER } from "../view/AiHypothesis";
import { ASK_EVENT, sendPendingAsk } from "./Welcome";
import { MarksMadeCard, madeId } from "../view/MadeCard";
import { WhatHappened, readRecord, type WhatHappenedRecord } from "../view/WhatHappened";
import type { AgentInfo, Mark, Panel, ViewOp, ViewResponse } from "../view/types";

// "Ask the data": the one raised surface of the page. It holds the agents and
// their pens, the chat (stock BESSER WebSocket protocol), one note per mark on
// the page, and the Keep / Remove decisions, which go to POST /view/ops as the
// agent "user". Text written by agents is set in Newsreader; every figure in a
// note comes from a {fact} placeholder resolved against /insights, never from
// the agent itself.
//
// Replies: both agent_reply_str (BAF) and agent_reply_markdown (the stock
// BESSER WebSocket protocol, which may also say which agent answered). A reply shows under its
// agent's name and pen; "[2]" becomes the numbered circle of storyline 2, and
// clicking it brings that mark into view on the page. A suggested follow-up
// question becomes a chip that asks it. {"type": "mode_change", "mode": ...}
// messages light up the card of the agent at work.

export interface PageNotice {
  text: string;
  error?: boolean;
  /** A passing confirmation, not a problem. */
  quiet?: boolean;
}

interface Props {
  id: string;
  /** Connect only after the reader picker has finished preparing the page. */
  enabled?: boolean;
  "agent-name"?: string;
  "agent-title"?: string;
  styles?: CSSProperties;
  /** What the page's own controls last did (shown on the status line). */
  notice?: PageNotice | null;
}

const PEN_WORD: Record<string, string> = {
  trend_finder: "red",
  cause_finder: "purple",
  forecaster: "amber",
  visualizer: "green",
};

const CHAT_KEY = "ask-the-data:conversation";

const DEFAULT_SUGGESTIONS = ["Why did exports jump in 2025?", "Where do the goods go?", "When did exports shift for good?"];

const NUMBER_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];

// Words that do not tell two questions apart ("Who are our biggest customers?" asks what "Who are Luxembourg's
// biggest customers?" asks).
const PLAIN_WORDS = new Set(["what", "which", "does", "have", "with", "from", "that", "this", "were", "will", "luxembourg", "luxembourgs", "about", "show", "there", "their", "they", "your", "ours"]);

function questionWords(q: string): Set<string> {
  return new Set(
    q
      .toLowerCase()
      .replace(/['’]s\b/g, "")
      .split(/[^a-z0-9]+/)
      .filter((w) => w.length > 3 && !PLAIN_WORDS.has(w)),
  );
}

/** Two questions that ask the same thing in other words. */
function sameQuestion(a: string, b: string): boolean {
  const wa = questionWords(a);
  const wb = questionWords(b);
  if (!wa.size || !wb.size) return a.trim().toLowerCase() === b.trim().toLowerCase();
  let common = 0;
  wa.forEach((w) => wb.has(w) && (common += 1));
  return common / Math.min(wa.size, wb.size) >= 0.75;
}

/** The modes of the agent's router, by the agent they stand for. */
const MODE_AGENT: Record<string, string> = {
  trend: "trend_finder",
  trend_finder: "trend_finder",
  cause: "cause_finder",
  cause_finder: "cause_finder",
  forecast: "forecaster",
  forecaster: "forecaster",
  visual: "visualizer",
  visualize: "visualizer",
  visualizer: "visualizer",
};

interface Active {
  /** The agent at work (its registry name), when the message names one. */
  agent?: string;
  mode: string;
  label?: string;
}

/**
 * A {"type": "mode_change"} control message: who is working now, or null when
 * the turn is over ("mode": "idle" or "state": "idle").
 */
function readModeChange(ctrl: any): Active | null {
  const mode = String(ctrl?.mode || "");
  if (mode === "idle" || ctrl?.state === "idle") return null;
  const agent = typeof ctrl?.agent === "string" && ctrl.agent ? ctrl.agent : MODE_AGENT[mode];
  return { agent, mode, label: typeof ctrl?.label === "string" ? ctrl.label : undefined };
}

/** Teal is too light as a fill behind white text; it fills with its ink. */
function fillOf(color: string | undefined): string {
  return color && color.toLowerCase() === "#0f7b6c" ? "#0B6156" : color || "#14202E";
}

// ------------------------------------------------------------------ agents

/** The store's agents, plus any of the four demo pens that have not registered (shown as not running). */
function withDemoAgents(agents: AgentInfo[]): AgentInfo[] {
  const out = [...agents];
  for (const name of AGENT_ORDER) {
    if (!out.some((a) => a.name === name)) {
      const f = AGENT_COLORS[name];
      out.push({ name, title: f.title, color: f.color, ink: f.ink, can: f.can, examples: [], online: false });
    }
  }
  const rank = (n: string) => (AGENT_ORDER.indexOf(n) < 0 ? AGENT_ORDER.length : AGENT_ORDER.indexOf(n));
  return out.sort((a, b) => rank(a.name) - rank(b.name));
}

function penStyle(agent: { color?: string; ink?: string } | undefined): CSSProperties {
  return {
    ["--pen" as any]: agent?.color || "#14202E",
    ["--pen-fill" as any]: fillOf(agent?.color),
    ["--agent-ink" as any]: agent?.ink || "#14202E",
  };
}

// ------------------------------------------------------------- storylines

function panelName(panel: Panel | undefined): string {
  if (!panel) return "the page";
  if (panel.type === "flow") return "the crossroads";
  if (panel.id === "trend") return "the trend";
  return `“${panel.title}”`;
}

function count(n: number, one: string, many: string): string {
  return `${NUMBER_WORDS[n] ?? n} ${n === 1 ? one : many}`;
}

function drawnLine(marks: Mark[], panels: Map<string, Panel>): string {
  const parts: string[] = [];
  const byKind = new Map<string, Mark[]>();
  for (const m of marks) {
    const k = `${m.kind}|${m.target}`;
    byKind.set(k, [...(byKind.get(k) || []), m]);
  }
  byKind.forEach((ms) => {
    const m = ms[0];
    const where = panelName(panels.get(m.target));
    const pen = PEN_WORD[m.agent] ? `${PEN_WORD[m.agent]} ` : "";
    switch (m.kind) {
      case "thread":
        // never "through": the statistics do not link what is bought to what is sold again
        parts.push(`${pen}thread on ${where}: ${threadWords(m)}`);
        break;
      case "event":
        parts.push(ms.length === 1 ? `an event in ${formatPeriod(String(m.params?.at ?? ""))} on ${where}` : `${count(ms.length, "event", "events")} on ${where}`);
        break;
      case "forecast":
        parts.push(`${pen}forecast band on ${where}`);
        break;
      case "trend":
        parts.push(`${pen}trend line on ${where}`);
        break;
      case "trend_break":
        parts.push(ms.length === 1 ? `a trend break in ${formatPeriod(String(m.params?.at ?? ""))} on ${where}` : `${count(ms.length, "trend break", "trend breaks")} on ${where}`);
        break;
      case "annotation":
        parts.push(ms.length === 1 ? `a note on ${noteSubject(m)}, on ${where}` : `${count(ms.length, "note", "notes")} on ${where}`);
        break;
      case "highlight": {
        const codes = ms.reduce((s, x) => s + (Array.isArray(x.params?.codes) ? x.params.codes.length : 0), 0);
        if (codes) parts.push(`${pen}highlight on ${count(codes, "item", "items")} of ${where}`);
        else parts.push(`${pen}highlighted span from ${m.params?.from} to ${m.params?.to} on ${where}`);
        break;
      }
      default:
        parts.push(`${m.kind} on ${where}`);
    }
  });
  return `Drawn: ${parts.join("; ")}`;
}

function errorText(r: ViewResponse | undefined): string {
  if (!r) return "The page did not answer.";
  if (r.status === 0) return "The page service is not reachable.";
  if (r.status === 409) return "The page changed meanwhile; try again.";
  return r.errors?.[0]?.message || r.detail || `The page refused the change (${r.status}).`;
}

function reveal(story: Storyline, color: string) {
  revealMarks(
    story.marks.map((m) => m.id),
    story.marks[0]?.target,
    color,
  );
}

const NoteCard: React.FC<{
  story: Storyline;
  agent: AgentInfo | undefined;
  panels: Map<string, Panel>;
  send: (ops: ViewOp[]) => Promise<ViewResponse>;
}> = ({ story, agent, panels, send }) => {
  const ctx = usePageContext();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [made, setMade] = useState(false);
  const allKept = story.status === "kept";
  const isUser = story.agent === "user";
  const name = isUser ? "You" : agent?.title || story.agent.replace(/_/g, " ");
  const color = isUser ? "#14202E" : agent?.color || AGENT_COLORS[story.agent]?.color || "#14202E";
  // The API calls behind the storyline, with the labels the agents gave them.
  const labels: Record<string, string> = {};
  story.marks.forEach((m) =>
    (m.provenance?.sources || []).forEach((s) => {
      if (s.label && !labels[s.url]) labels[s.url] = s.label;
    }),
  );
  const sources = story.marks
    .flatMap((m) => markSources(m))
    .filter((u, i, all) => all.indexOf(u) === i)
    .map((url) => ({ url, label: labels[url] }));
  const headId = `note-${story.number}`;
  // a mark is about another year than its chart shows: the chart's own context (1.5), else the page's
  const offMark = story.marks.find((m) => markOffContext(m, withPanelContext(ctx, panels.get(m.target))));
  const about = offMark ? markPeriod(offMark) : undefined;

  const act = async (op: "keep_mark" | "remove_mark") => {
    const ids = story.marks.filter((m) => op === "remove_mark" || m.status !== "kept").map((m) => m.id);
    if (!ids.length) return;
    setBusy(true);
    setError(null);
    try {
      const r = await send(ids.map((id) => ({ op, id })));
      if (!r || !r.ok) setError(errorText(r));
    } catch {
      setError(errorText(undefined));
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className={`ask-note${about ? " ask-note--off" : ""}`} style={penStyle(isUser ? { color, ink: color } : agent)} aria-labelledby={headId}>
      <span className="ask-note-head" id={headId}>
        <button type="button" className="ask-n ask-n--btn" onClick={() => reveal(story, color)} title="Show it on the page" aria-label={`Show mark ${story.number} on the page`}>
          {story.number}
        </button>
        {name}
        {about && <AboutTag period={about} />}
        {allKept && <span className="ask-kept">Kept</span>}
      </span>
      {story.marks.length === 1 ? (
        <p className="ask-note-text">
          <MarkText mark={story.marks[0]} />
        </p>
      ) : (
        <ul className="ask-note-list">
          {story.marks.map((m) => (
            <li key={m.id}>
              {typeof m.params?.label === "string" && <strong>{m.params.label}. </strong>}
              <MarkText mark={m} />
            </li>
          ))}
        </ul>
      )}
      <span className="ask-drawn">{drawnLine(story.marks, panels)}</span>
      <div className="ask-actions">
        {!allKept && !isUser && (
          <button type="button" className="cx-btn cx-btn--ink" disabled={busy} onClick={() => act("keep_mark")}>
            Keep
          </button>
        )}
        <button type="button" className="cx-btn" disabled={busy} onClick={() => act("remove_mark")}>
          Remove from the page
        </button>
        <button
          type="button"
          className={`ask-made-btn${made ? " is-open" : ""}`}
          aria-expanded={made}
          aria-controls={madeId(`note-${story.key}`)}
          onClick={() => setMade((o) => !o)}
        >
          How this was made{sources.length > 0 ? ` (${sources.length} ${sources.length === 1 ? "calculation" : "calculations"})` : ""}
        </button>
      </div>
      {made && <MarksMadeCard id={`note-${story.key}`} marks={story.marks} onClose={() => setMade(false)} />}
      {error && (
        <p className="ask-error" role="alert">
          {error}
        </p>
      )}
    </article>
  );
};

// ---------------------------------------------------------- markdown-ish

interface InlineCtx {
  /** Storyline by its page number, for "[2]". */
  story: (n: number) => Storyline | undefined;
  penOf: (agent: string) => string;
}

/** A relative /insights or /trends URL, or an absolute http(s) one, as a safe link target. */
function safeHref(href: string): string | null {
  if (/^https?:\/\//i.test(href)) return href;
  if (/^\/(insights|trends|view|agents)\b/.test(href)) return apiHref(href);
  return null;
}

/** "[2]" in a reply: the numbered circle of storyline 2, in its agent's pen; a click brings it into view. */
const MarkRef: React.FC<{ n: number; ctx: InlineCtx }> = ({ n, ctx }) => {
  const story = ctx.story(n);
  if (!story) return <>[{n}]</>;
  const color = story.agent === "user" ? "#14202E" : ctx.penOf(story.agent);
  return (
    <button
      type="button"
      className="ask-ref"
      style={{ background: fillOf(color) }}
      onClick={() => reveal(story, color)}
      title="Show it on the page"
      aria-label={`Mark ${n}: show it on the page`}
    >
      {n}
    </button>
  );
};

function inline(text: string, ctx: InlineCtx, keyBase = 0): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  // Italics may hold `code` with underscores in it (_1 API call: `GET /trends/analyze?min_size=12`_).
  const re = /(\*\*[^*]+\*\*|(?<![\w*])\*(?:`[^`\n]*`|[^*\n`])+\*(?![\w*])|(?<![\w])_(?:`[^`\n]*`|[^_\n`])+_(?![\w])|`[^`]+`|\[[^\]]+\]\([^)\s]+\)|\[\d{1,2}\])/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = keyBase;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(<strong key={i++}>{inline(tok.slice(2, -2), ctx, i * 100)}</strong>);
    else if (tok.startsWith("`")) {
      const body = tok.slice(1, -1);
      const url = /^(?:GET\s+)?(\/(?:insights|trends)\S*)$/.exec(body);
      out.push(
        url ? (
          <a key={i++} className="ask-code-link" href={apiHref(url[1])} target="_blank" rel="noreferrer">
            <code>{body}</code>
          </a>
        ) : (
          <code key={i++}>{body}</code>
        ),
      );
    } else if (/^\[\d{1,2}\]$/.test(tok)) out.push(<MarkRef key={i++} n={parseInt(tok.slice(1, -1), 10)} ctx={ctx} />);
    else if (tok.startsWith("[")) {
      const lm = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(tok)!;
      const href = safeHref(lm[2]);
      out.push(
        href ? (
          <a key={i++} href={href} target="_blank" rel="noreferrer">
            {lm[1]}
          </a>
        ) : (
          lm[1]
        ),
      );
    } else out.push(<em key={i++}>{inline(tok.slice(1, -1), ctx, i * 100)}</em>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** A reply's text outside the chat (under a chart): the same markdown, its [n] marks left as plain numbers. */
const NO_STORY: InlineCtx = { story: () => undefined, penOf: () => "#14202E" };
export const ReplyText: React.FC<{ text: string }> = ({ text }) => <Markdownish text={text} ctx={NO_STORY} />;

/** Paragraphs, line breaks, bullet and numbered lists, headings as bold lines, **bold**, _italic_, `code`, links and [n] marks. */
const Markdownish: React.FC<{ text: string; ctx: InlineCtx }> = ({ text, ctx }) => {
  const blocks: React.ReactNode[] = [];
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  let para: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  const flushPara = () => {
    if (para.length) {
      const k = blocks.length;
      blocks.push(
        <p key={k}>
          {para.map((l, j) => (
            <React.Fragment key={j}>
              {j > 0 && <br />}
              {inline(l, ctx)}
            </React.Fragment>
          ))}
        </p>,
      );
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      const k = blocks.length;
      const items = list.items.map((it, j) => <li key={j}>{inline(it, ctx)}</li>);
      blocks.push(list.ordered ? <ol key={k}>{items}</ol> : <ul key={k}>{items}</ul>);
      list = null;
    }
  };
  for (const line of lines) {
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    const heading = /^\s*#{1,6}\s+(.*)$/.exec(line);
    const quote = /^\s*>\s?(.*)$/.exec(line);
    if (quote) {
      // A sentence the reader can quote as is (a journalist's profile asks for one).
      flushPara();
      flushList();
      blocks.push(
        <blockquote key={blocks.length} className="ask-quote">
          {inline(quote[1], ctx)}
        </blockquote>,
      );
    } else if (heading) {
      flushPara();
      flushList();
      blocks.push(
        <p key={blocks.length} className="ask-reply-h">
          {inline(heading[1], ctx)}
        </p>,
      );
    } else if (bullet || numbered) {
      flushPara();
      const ordered = !!numbered;
      if (list && list.ordered !== ordered) flushList();
      if (!list) list = { ordered, items: [] };
      list.items.push((bullet || numbered)![1]);
    } else if (!line.trim()) {
      flushPara();
      flushList();
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara();
  flushList();
  return <>{blocks}</>;
};

// ------------------------------------------------------------- replies

interface ChatMessage {
  id: number;
  text: string;
  sender: "user" | "agent";
  /** The agent's registry name, when known. */
  agent?: string;
  /** Its title and pen, as the reply or the registry gives them. */
  title?: string;
  color?: string;
  ink?: string;
  /** A question the agent suggests asking next. */
  followUp?: string;
  /** The agent's decision record for this reply ("What happened"), when it sent one. */
  record?: WhatHappenedRecord;
}

const FOLLOW_UP = /^\s*[_*]{0,2}\s*(?:next(?: question)?|try(?: asking)?|you (?:could|can|might) (?:also )?ask|follow[- ]?up|ask next|a good next question(?: is)?|want more\??)\s*[:\-–—]\s*[_*]{0,2}\s*["“']?(.+?\?)["”']?\s*[_*]{0,2}\s*$/i;

/** The reply without its suggested follow-up line, and that question. */
function splitFollowUp(text: string, payload: Record<string, unknown>): { body: string; followUp?: string } {
  const given = payload.follow_up ?? payload.followup ?? payload.suggestion ?? (Array.isArray(payload.suggestions) ? payload.suggestions[0] : undefined);
  if (typeof given === "string" && given.trim()) return { body: text, followUp: given.replace(/[\s_*?]+$/, "").trim() + "?" };
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  for (let i = lines.length - 1; i >= Math.max(0, lines.length - 3); i--) {
    if (!lines[i].trim()) continue;
    const m = FOLLOW_UP.exec(lines[i]);
    if (m) {
      lines.splice(i, 1);
      // "X?_?" (a doubled italic ending) is the question "X?".
      return { body: lines.join("\n").trim(), followUp: m[1].replace(/[\s_*?]+$/, "").trim() + "?" };
    }
    break;
  }
  return { body: text };
}

/** "**Trend finder.** Exports rose ..." -> "Exports rose ...", when the name is shown above the reply anyway. */
function stripLeadName(text: string, title?: string): string {
  if (!title) return text;
  const m = /^\s*\*\*([^*]+?)\.?\*\*\.?\s*/.exec(text);
  if (m && m[1].trim().toLowerCase().replace(/\.$/, "") === title.toLowerCase()) return text.slice(m[0].length);
  return text;
}

// --------------------------------------------------------------- the panel

export const AgentComponent: React.FC<Props> = ({
  id,
  enabled = true,
  "agent-name": agentName = "",
  "agent-title": agentTitle = "Ask the data",
  styles,
  notice,
}) => {
  const store = useViewStore();
  const spec = store.spec;
  const send = store.send;
  const viewStatus = store.status;

  // The conversation survives a reload of this tab (a per-viewer convenience; the page itself is the spec).
  const [messages, setMessages] = useState<ChatMessage[]>(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(CHAT_KEY) || "[]");
      return Array.isArray(saved) ? saved.filter((m) => m && typeof m.text === "string") : [];
    } catch {
      return [];
    }
  });
  const [inputMessage, setInputMessage] = useState("");
  const [isConnected, setIsConnected] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [active, setActive] = useState<Active | null>(null);
  const [clearing, setClearing] = useState(false);
  const [clearError, setClearError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const panelRef = useRef<HTMLElement>(null);
  const cardsRef = useRef<HTMLUListElement>(null);
  const activeRef = useRef<Active | null>(null);
  activeRef.current = active;
  const nextId = useRef(messages.reduce((n, m) => Math.max(n, m.id + 1), 1));
  useEffect(() => {
    try {
      sessionStorage.setItem(CHAT_KEY, JSON.stringify(messages.slice(-40)));
    } catch {
      /* private window or storage off: the conversation lives as long as the page */
    }
  }, [messages]);

  // The reader the page is drawn for (contract 1.4): their questions, and the agents they see.
  const pageCtx = usePageContext();
  const persona = profileOf(pageCtx.persona);
  const agents = useMemo(() => withDemoAgents(store.agents), [store.agents]);
  const shownAgents = useMemo(
    () => (persona ? agents.filter((a) => persona.agents.enabled.indexOf(a.name) >= 0) : agents),
    [agents, persona],
  );
  const showCards = !persona || persona.agents.show_agent_cards !== false;
  const agentByName = useMemo(() => new Map(agents.map((a) => [a.name, a])), [agents]);
  const agentsRef = useRef(agentByName);
  agentsRef.current = agentByName;
  const anyOnline = agents.some((a) => a.online);
  const panels = useMemo(() => new Map((spec?.panels || []).map((p) => [p.id, p])), [spec]);
  const stories = useMemo(() => storylines(spec?.marks || []).list, [spec]);
  const proposed = stories.filter((s) => s.agent !== "user" && s.marks.some((m) => m.status !== "kept")).length;

  const inlineCtx: InlineCtx = useMemo(
    () => ({
      // numbers are stable (contract 1.6): "[3]" is storyline 3, or nothing once it is gone
      story: (n: number) => stories.find((s) => s.number === n),
      penOf: (agent: string) => agentByName.get(agent)?.color || AGENT_COLORS[agent]?.color || "#14202E",
    }),
    [stories, agentByName],
  );

  // Where the conversation stood when the page was last drawn for another reader (the first message id after it).
  const since = useRef<{ persona?: string; id: number }>({ persona: persona?.id, id: 0 });
  if (since.current.persona !== persona?.id) since.current = { persona: persona?.id, id: nextId.current };

  const suggestions = useMemo(() => {
    if (persona) return persona.suggested_questions.slice(0, 4);
    const fromAgents = agents.filter((a) => a.online).flatMap((a) => a.examples || []);
    const list = Array.from(new Set(fromAgents.length ? fromAgents : DEFAULT_SUGGESTIONS));
    return list.slice(0, 3);
  }, [agents, persona]);

  // GUI components store ``agent-name`` verbatim from the diagram title (e.g.
  // "Library Agent"), but BUML Agent names — and therefore the JSON map keys
  // — are Python-safe identifiers ("Library_Agent"). We do one normalization:
  // space→underscore. Backend emits exactly one canonical key per agent, so
  // this lookup is deterministic.
  const agentUrl = useMemo(() => {
    const raw = import.meta.env.VITE_AGENT_URLS as string | undefined;
    if (raw && agentName) {
      try {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") {
          const key = agentName in parsed ? agentName : agentName.replace(/\s+/g, "_");
          if (parsed[key]) {
            return agentSocketHref(parsed[key] as string);
          }
        }
      } catch {
        // Fall through to the single-URL variable if the map can't be parsed.
      }
    }
    const url = (import.meta.env.VITE_AGENT_URL as string | undefined) || "ws://localhost:8766";
    // A path ("/ws") is on the page's own host: ws:// on http, wss:// on https (a deployment behind nginx).
    if (url.startsWith("/")) return agentSocketHref(`${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}${url}`);
    return agentSocketHref(url);
  }, [agentName]);

  useEffect(() => {
    // Auto-reconnect with linear-then-capped backoff: 2s, 4s, 6s, ... capped
    // at 30s per attempt, up to 60 attempts. ``cancelled`` flips on cleanup so
    // we stop retrying after unmount (including React strict-mode double-mount).
    if (!enabled) return;
    let cancelled = false;
    let retry = 0;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (cancelled) return;
      setIsConnecting(true);

      let ws: WebSocket;
      try {
        ws = new WebSocket(agentUrl);
      } catch (error) {
        console.error("Failed to open WebSocket:", error);
        scheduleRetry();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        if (cancelled || wsRef.current !== ws) return;
        retry = 0;
        setIsConnected(true);
        setIsConnecting(false);
      };

      ws.onmessage = (event) => {
        if (cancelled || wsRef.current !== ws) return;
        let payload: Record<string, unknown>;
        try {
          payload = JSON.parse(event.data);
        } catch {
          return; // raw strings are not part of the BAF protocol
        }
        if (payload && (payload as any).type === "mode_change") {
          const next = readModeChange(payload);
          setActive(next);
          setWaiting(!!next);
          return;
        }
        // Final text replies only: agent_reply_str (BAF) or agent_reply_markdown (stock BESSER protocol).
        if (payload?.action !== "agent_reply_str" && payload?.action !== "agent_reply_markdown") return;
        const text = typeof payload.message === "string" ? payload.message : "";
        if (!text.trim()) return;
        // The router's {"type": "mode_change"} control message: which specialist is at work.
        try {
          const ctrl = JSON.parse(text);
          if (ctrl && typeof ctrl === "object" && ctrl.type === "mode_change") {
            const next = readModeChange(ctrl);
            setActive(next);
            setWaiting(!!next);
            return;
          }
        } catch {
          /* plain text: a reply */
        }
        const working = activeRef.current;
        const agent = typeof payload.agent === "string" ? payload.agent : working?.agent && working.agent !== "general" ? working.agent : undefined;
        const known = agent ? agentsRef.current.get(agent) : undefined;
        const title = (typeof payload.title === "string" && payload.title) || known?.title || (agent ? AGENT_COLORS[agent]?.title : undefined);
        const { body, followUp } = splitFollowUp(text, payload);
        // The agent that answered is done; the next one announces itself with its own mode_change.
        setWaiting(false);
        setActive((cur) => (cur && (!agent || cur.agent === agent) ? null : cur));
        noteReply(title || working?.label || "Agent", stripLeadName(body, title));
        setMessages((prev) => [
          ...prev,
          {
            id: nextId.current++,
            text: stripLeadName(body, title),
            sender: "agent",
            agent,
            title: title || working?.label || undefined,
            color: (typeof payload.color === "string" && payload.color) || known?.color || (agent ? AGENT_COLORS[agent]?.color : undefined),
            ink: (typeof payload.ink === "string" && payload.ink) || known?.ink || (agent ? AGENT_COLORS[agent]?.ink : undefined),
            followUp,
            record: readRecord(payload),
          },
        ]);
      };

      ws.onerror = () => {
        // onclose follows and schedules the retry.
      };

      ws.onclose = () => {
        if (cancelled || wsRef.current !== ws) return;
        setIsConnected(false);
        setWaiting(false);
        setActive(null);
        wsRef.current = null;
        scheduleRetry();
      };
    };

    const scheduleRetry = () => {
      retry += 1;
      if (retry > 60) {
        setIsConnecting(false);
        return;
      }
      const delay = Math.min(30000, 2000 * retry);
      setIsConnecting(true);
      retryTimer = setTimeout(connect, delay);
    };

    connect();

    return () => {
      cancelled = true;
      setIsConnected(false);
      setWaiting(false);
      setActive(null);
      if (retryTimer) clearTimeout(retryTimer);
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [agentName, agentUrl, enabled]);

  // Bring the newest message or note into view, inside the panel only (it
  // scrolls when docked; stacked under the page it does not, and the window
  // must never jump). Marks already there on load do not count as new.
  const lastMarkN = stories.length ? Math.max(...stories.flatMap((s) => s.marks.map((m) => m.n))) : 0;
  const seenMarkN = useRef<number | null>(null);
  useEffect(() => {
    if (spec && seenMarkN.current === null) {
      seenMarkN.current = lastMarkN;
      return;
    }
    if (seenMarkN.current !== null && lastMarkN > seenMarkN.current) {
      seenMarkN.current = lastMarkN;
      setWaiting(false);
    }
    const body = panelRef.current;
    if (body && body.scrollHeight > body.clientHeight + 1 && (messages.length > 0 || lastMarkN > 0)) {
      body.scrollTo({ top: body.scrollHeight, behavior: "smooth" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages.length, lastMarkN, waiting]);

  // A question with no answer in 90 s stops showing as in progress.
  useEffect(() => {
    if (!waiting && !active) return;
    const t = setTimeout(() => {
      setWaiting(false);
      setActive(null);
    }, 90000);
    return () => clearTimeout(t);
  }, [waiting, active]);

  // One question at a time: the chat is off while an agent is at work.
  const working = waiting || !!active;
  const ask = (text: string): boolean => {
    const question = text.trim();
    const ws = wsRef.current;
    if (!enabled || !question || !ws || !isConnected || ws.readyState !== WebSocket.OPEN || working) return false;
    try {
      // BESSER framework expects this exact format.
      ws.send(JSON.stringify({ action: "user_message", message: question }));
      setMessages((prev) => [...prev, { id: nextId.current++, text: question, sender: "user" }]);
      notePrompt(question);
      setWaiting(true);
    } catch (error) {
      console.error("Failed to send message:", error);
      return false;
    }
    setInputMessage("");
    return true;
  };

  // A starter question elsewhere on the page ("Start your dashboard") asks through this panel.
  const askRef = useRef(ask);
  askRef.current = ask;
  useEffect(() => {
    const on = (e: Event) => {
      const q = (e as CustomEvent).detail;
      if (typeof q === "string") askRef.current(q);
    };
    window.addEventListener(ASK_EVENT, on);
    return () => window.removeEventListener(ASK_EVENT, on);
  }, []);
  // A question picked on the reader screen waits across the reload of a first landing: asked once the agents
  // are connected and the page is in (so what they draw is filed under the question).
  useEffect(() => {
    if (!enabled || !isConnected || working || !spec || store.fallback) return;
    sendPendingAsk((q) => askRef.current(q));
  }, [enabled, isConnected, working, spec, store.fallback]);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    ask(inputMessage);
  };

  const clearMarks = async () => {
    setClearing(true);
    setClearError(null);
    const r = await send([{ op: "clear", scope: "marks" }], { message: "Cleared the agent marks" });
    setClearing(false);
    if (!r.ok) setClearError(errorText(r));
  };

  const howMany = NUMBER_WORDS[shownAgents.length] || String(shownAgents.length);
  const intro = showCards
    ? `${howMany.charAt(0).toUpperCase()}${howMany.slice(1)} ${shownAgents.length === 1 ? "agent" : "agents"} can change this page, ` +
      "each with its own pen colour. Every numbered mark on the page matches a note below."
    : "Ask a question: one answer comes back here, and what it draws on the page is numbered in a note below.";

  const chatState = isConnected ? "connected" : isConnecting ? "connecting" : "offline";
  // "Show me 2025" while the page already shows 2025 is no next step.
  const shownYear = String(pageCtx.period || "").slice(0, 4);
  const offered = (q: string) => {
    const m = /^\s*show me\s+(?:the page (?:for|in|of)\s+)?((?:19|20)\d{2})\s*\??\s*$/i.exec(q);
    return !(m && m[1] === shownYear);
  };
  const followUp = [...messages].reverse().find((m) => m.sender === "agent")?.followUp;
  const lastFollowUp = followUp && offered(followUp) ? followUp : undefined;
  // A suggested question already asked in this conversation is not offered again, except that a
  // persona's own questions are a new reader's: they count only what was asked since the switch to it.
  const askedFrom = persona && since.current.persona === persona.id ? since.current.id : 0;
  const asked = new Set(
    messages.filter((m) => m.sender === "user" && m.id >= askedFrom).map((m) => m.text.trim().toLowerCase()),
  );
  const activeAgent = active?.agent ? agentByName.get(active.agent) : undefined;
  const counted = !(persona?.hide || []).includes("proposed_marks_counter");
  const clearLabel = proposed <= 1 || !counted ? "Clear agent marks" : `Clear the ${proposed} agent marks`;

  // One quiet status line: the page's last action when it has something to say, else the connections.
  let status: React.ReactNode;
  if (clearError || notice?.error) {
    status = (
      <span className="ask-status-error" role="alert">
        {clearError || notice?.text}
      </span>
    );
  } else {
    const view = viewStatus === "live" ? "page live" : viewStatus === "polling" ? "page checks for updates" : "page service offline, showing the starting page";
    const chat = chatState === "connected" ? "Agents connected" : chatState === "connecting" ? "Connecting to the agents" : "Agents not reachable";
    status = (
      <span>
        <span className={`ask-dot${chatState === "connected" && viewStatus !== "offline" ? "" : chatState === "offline" || viewStatus === "offline" ? " ask-dot--off" : " ask-dot--wait"}`} />
        {notice?.quiet && notice.text ? notice.text : `${chat}, ${view}.`}
      </span>
    );
  }

  return (
    <aside id={id} ref={panelRef} className="ask" aria-label={agentTitle} style={styles}>
      {showCards && (
        <AgentBar agents={shownAgents} working={active?.agent} anyOnline={anyOnline} cards={cardsRef} inputId={`${id}-question`} pen={penStyle} />
      )}
      <div className="ask-head">
        <h2 className="ask-title cond">{agentTitle}</h2>
        {persona && (
          <p className="ask-for">
            Answers for: <strong>{persona.label}</strong>
            {persona.tagline && <span className="ask-for-tag">{persona.tagline}. Same figures for every reader.</span>}
          </p>
        )}
        <p className="ask-intro">{intro}</p>
        {showCards && (
        <ul className="ask-agents" aria-label="Agents" ref={cardsRef}>
          {shownAgents.map((a) => {
            const off = anyOnline && !a.online;
            const working = !!active && active.agent === a.name;
            return (
              <li key={a.name} className={`ask-agent${off ? " ask-agent--off" : ""}${working ? " ask-agent--active" : ""}`} style={penStyle(a)} aria-current={working ? "true" : undefined}>
                <span className="ask-agent-name">
                  <span className="ask-swatch" aria-hidden="true" />
                  {a.title}
                </span>
                <span className="ask-agent-can">{a.can}</span>
                {working && (
                  <span className="ask-agent-working" role="status">
                    <span className="ask-pulse" aria-hidden="true" />
                    Working on your question
                  </span>
                )}
                {off && !working && <span className="ask-agent-state">Not running</span>}
              </li>
            );
          })}
        </ul>
        )}
      </div>

      <div className="ask-body">
        {messages.length === 0 && stories.length === 0 && (
          <p className="ask-empty">Ask about a partner, a product or a year. The agents answer here and draw on the page.</p>
        )}
        {/* The numbered notes live under their charts (one place, not two): the panel holds the conversation. */}
        {(messages.length > 0 || waiting) && (
          <div className="ask-log" role="log" aria-live="polite" aria-label="Conversation">
            {messages.map((msg) => {
              if (msg.sender === "user") {
                return (
                  <p key={msg.id} className="ask-q">
                    {msg.text}
                  </p>
                );
              }
              const { shown, truncated } = shortenReply(keepFigures(msg.text));
              return (
                <article key={msg.id} className="ask-reply" style={penStyle({ color: msg.color, ink: msg.ink })}>
                  <span className="ask-reply-who">
                    <span className="ask-swatch" aria-hidden="true" />
                    {msg.title || "The agents"}
                    {msg.agent === CAUSE_FINDER && <AiHypothesis />}
                  </span>
                  <div className="ask-reply-text">
                    <Markdownish text={shown} ctx={inlineCtx} />
                  </div>
                  {truncated && <p className="ask-reply-more">See “More about this chart”, under the chart, for the full explanation.</p>}
                  {msg.record && <WhatHappened id={msg.id} record={msg.record} />}
                </article>
              );
            })}
            {waiting && (
              <p className="ask-thinking" style={activeAgent ? penStyle(activeAgent) : undefined}>
                {activeAgent ? `The ${activeAgent.title.toLowerCase()} is looking at the data.` : active?.label ? `${active.label} is looking at the data.` : "The agents are looking at the data."}
              </p>
            )}
            {lastFollowUp && !waiting && (
              <div className="ask-chips" role="group" aria-label="Suggested next question">
                <button type="button" className="ask-chip ask-chip--next" disabled={!isConnected || working} onClick={() => ask(lastFollowUp)}>
                  {lastFollowUp}
                </button>
              </div>
            )}
          </div>
        )}

        {suggestions.length > 0 && (
          <div className="ask-chips ask-chips--suggested" role="group" aria-label="Suggested questions">
            {suggestions
              // never one already asked in other words ("Who are our biggest customers?" / "...Luxembourg's biggest
              // customers?"), nor the agent's own next question; once the conversation runs, two at most
              .filter((s) => (s !== lastFollowUp || waiting) && !asked.has(s.toLowerCase()) && offered(s))
              .filter((s) => !Array.from(asked).some((a) => sameQuestion(a, s)) && !(lastFollowUp && !waiting && sameQuestion(lastFollowUp, s)))
              .slice(0, messages.some((m) => m.sender === "agent") ? 2 : 4)
              .map((s) => (
              <button key={s} type="button" className="ask-chip" disabled={!isConnected || working} onClick={() => ask(s)}>
                {s}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="ask-foot">
        <form className="ask-form" onSubmit={onSubmit}>
          <label htmlFor={`${id}-question`}>Your question</label>
          <span className="ask-input-row">
            <input
              id={`${id}-question`}
              className="ask-input"
              type="text"
              value={inputMessage}
              onChange={(e) => setInputMessage(e.target.value)}
              placeholder={working ? "Working on your question…" : "A partner, a product or a year"}
              autoComplete="off"
              disabled={working}
              aria-busy={working}
            />
            <button type="submit" className="ask-send" disabled={!isConnected || working || !inputMessage.trim()}>
              Ask
            </button>
          </span>
        </form>
        <p className="ask-status" aria-live="polite">
          {status}
        </p>
      </div>
    </aside>
  );
};

export default AgentComponent;
