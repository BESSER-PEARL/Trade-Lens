import React, { useEffect, useRef, useState } from "react";
import { EVERYONE, PERSONAS, aReader } from "../view/personas";
import { CARDS, type AgentKey } from "../view/personaCards";
import { AGENT_COLORS, AGENT_ORDER } from "../view/tokens";
import { APP_NAME } from "../view/branding";
import "./PersonaGate.css";

const DATASET_URL = "https://ec.europa.eu/eurostat/databrowser/view/ds-045409/legacyMultiFreq/table?category=ext_go.ext_go_detail";

interface Props {
  /** The reader the page is drawn for now (EVERYONE when none). */
  current: string;
  busy: boolean;
  /** A reader was chosen (a profile id, or EVERYONE), and perhaps a question to start with; ``fly`` plays the
   *  hand-over to the chat once the page under the landing reads for that reader (before it reloads). */
  onChoose: (id: string, question?: string, fly?: () => Promise<void>) => void;
  /** Leave without changing anything; absent on the first visit. */
  onClose?: () => void;
}

/** The first screen: persona cards preview the questions each specialist can answer for that reader. */
export function PersonaGate({ current, busy, onChoose, onClose }: Props) {
  const [focus, setFocus] = useState<string>(current !== EVERYONE ? current : PERSONAS[0].id);
  // A reader pointed at (or already chosen): a question typed below is asked for it; otherwise for every reader.
  const [touched, setTouched] = useState(current !== EVERYONE);
  const [draft, setDraft] = useState("");
  const [leaving, setLeaving] = useState(false);
  const frameRef = useRef<HTMLDivElement>(null);
  const boxRef = useRef<HTMLFormElement>(null);
  const profile = PERSONAS.find((p) => p.id === focus) || PERSONAS[0];
  const card = CARDS[profile.id];

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose?.();
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const point = (id: string) => {
    setFocus(id);
    setTouched(true);
  };

  // Start with a question: the box it came from flies to where "Ask the data" sits on the page and becomes it,
  // then the page for the reader loads with the question already asked (onChoose leaves it pending).
  const startWith = (reader: string, question: string, from: HTMLElement | null) => {
    const q = question.trim();
    if (!q || busy || leaving) return;
    setLeaving(true);
    const still = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!from || still || typeof document.body.animate !== "function") return onChoose(reader, q);
    const frame = frameRef.current;
    onChoose(reader, q, () => flyToChat(from, q, frame));
  };

  return (
    <div className="pg" role="dialog" aria-modal="true" aria-labelledby="pg-title" style={{ ["--accent" as any]: card?.accent }}>
      <div className="pg-scroll">
        <div className={"pg-frame" + (leaving ? " is-leaving" : "")} ref={frameRef}>
          <header className="pg-hero">
            <h1 id="pg-title" className="pg-title">
              <span className="pg-brand">{APP_NAME}</span>{" "}
              Find your way through Luxembourg's trade data for{" "}
              <span className="pg-need" key={profile.id}>{card?.need}</span>.
            </h1>
          </header>

          <aside className="pg-source" aria-label="Where the figures come from">
            <span className="pg-source-icon" aria-hidden="true">
              <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                <ellipse cx="12" cy="5.5" rx="7.5" ry="3" />
                <path d="M4.5 5.5v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-6" />
                <path d="M4.5 11.5v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-6" />
              </svg>
            </span>
            <span className="pg-source-body">
              <strong className="pg-source-title">Built on official Eurostat data</strong>
            </span>
            <a className="pg-source-link" href={DATASET_URL} target="_blank" rel="noreferrer">
              Dataset DS-045409 <span aria-hidden="true">↗</span>
            </a>
          </aside>

          <h2 className="pg-pick">Who is reading today?</h2>

          <div className="pg-grid" role="radiogroup" aria-label="Readers">
            {PERSONAS.map((p) => {
              const c = CARDS[p.id];
              const on = p.id === focus;
              return (
                <button
                  key={p.id}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  className={"pg-card" + (on ? " is-on" : "")}
                  style={{ ["--accent" as any]: c?.accent }}
                  disabled={busy || leaving}
                  onClick={() => onChoose(p.id)}
                  onMouseEnter={() => point(p.id)}
                  onFocus={() => point(p.id)}
                >
                  <span className="pg-badge" aria-hidden="true">{c?.mark || p.label.slice(0, 2).toUpperCase()}</span>
                  <span className="pg-card-name">{p.label}</span>
                  <span className="pg-card-tag">{c?.role}</span>
                  {p.id === current && <span className="pg-current">Current</span>}
                </button>
              );
            })}
          </div>

          <section className="pg-agents" aria-live="polite" aria-label={`What ${aReader(profile.label)} can ask the four experts`}>
            <h3 className="pg-agents-title" key={profile.id}>
              Four experts. <span>Questions fit for {aReader(profile.label)}:</span>
            </h3>
            <div className="pg-agent-grid" key={`g-${profile.id}`}>
              {AGENT_ORDER.map((key) => {
                const a = AGENT_COLORS[key];
                const qs = card?.ask[key as AgentKey] || [];
                return (
                  <article key={key} className="pg-agent" style={{ ["--pen" as any]: a.color, ["--ink-pen" as any]: a.ink }}>
                    <h4 className="pg-agent-name">
                      <span className="pg-agent-dot" aria-hidden="true" />
                      {a.title}
                    </h4>
                    <p className="pg-agent-can">{a.can}</p>
                    <ul className="pg-agent-qs">
                      {qs.map((q) => (
                        <li key={q}>
                          <button type="button" className="pg-q" disabled={busy || leaving} onClick={(e) => startWith(profile.id, q, e.currentTarget)} aria-label={`Read as ${aReader(profile.label)} and ask: ${q}`}>
                            <span className="pg-q-text">{q}</span>
                            <span className="pg-q-go" aria-hidden="true">→</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </article>
                );
              })}
            </div>
          </section>

          <div className="pg-actions">
            <button type="button" className="pg-skip" disabled={busy || leaving} onClick={() => onChoose(EVERYONE)}>
              Not sure? Show the standard page
            </button>
            {onClose && (
              <button type="button" className="pg-skip" onClick={onClose}>
                Cancel
              </button>
            )}
          </div>
          <form
            ref={boxRef}
            className="pg-ask"
            onSubmit={(e) => {
              e.preventDefault();
              startWith(touched ? focus : EVERYONE, draft, boxRef.current);
            }}
          >
            <label className="pg-ask-label" htmlFor="pg-ask-input">
              Or just ask, in your own words{touched ? `, as ${aReader(profile.label)}` : ""}
            </label>
            <div className="pg-ask-row">
              <input
                id="pg-ask-input"
                className="pg-ask-input"
                type="text"
                autoComplete="off"
                placeholder="A partner, a product or a year"
                value={draft}
                disabled={busy || leaving}
                onChange={(e) => setDraft(e.target.value)}
              />
              <button type="submit" className="pg-ask-go" disabled={busy || leaving || !draft.trim()}>
                Ask
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}

const HANDOFF = "lux.chatHandoff";

type Rect = { left: number; top: number; width: number; height: number };

const rectOf = (el: Element): Rect => {
  const r = el.getBoundingClientRect();
  return { left: r.left, top: r.top, width: r.width, height: r.height };
};

/** The real "Ask the data" panel of the page under the landing, when it is on screen. */
function chatPanel(): HTMLElement | null {
  const el = document.querySelector<HTMLElement>("aside.ask");
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && r.top < window.innerHeight ? el : null;
}

/** Where the panel sits when it cannot be measured (docked on the right from 1140 px, below the page under). */
function chatSlot(): Rect {
  const w = window.innerWidth;
  const h = window.innerHeight;
  const pad = Math.min(40, Math.max(16, w * 0.03));
  if (w >= 1140) {
    const wrap = Math.min(1400, w - 2 * pad);
    const width = 380;
    return { left: (w - wrap) / 2 + wrap - width, top: 28, width, height: h - 48 };
  }
  return { left: pad, top: Math.round(h * 0.42), width: w - 2 * pad, height: Math.round(h * 0.58) - pad };
}

/**
 * A copy of the real chat panel as it will look once the question is asked: the page's own markup (the reader's
 * header, its suggestions, the question box), the earlier conversation taken out, and the question with "looking
 * at the data" where the conversation will hold them. Wrapped in the page's root attributes, so its styles apply.
 */
function panelCopy(real: HTMLElement | null, question: string): HTMLDivElement {
  const wrap = document.createElement("div");
  wrap.className = "pg-fly";
  wrap.setAttribute("aria-hidden", "true");
  const page = document.querySelector<HTMLElement>(".cx-page");
  const ctx = document.createElement("div");
  ctx.className = page?.className || "cx-page";
  for (const name of ["data-persona", "data-contrast", "data-hide", "style"]) {
    const v = page?.getAttribute(name);
    if (v) ctx.setAttribute(name, v);
  }
  Object.assign(ctx.style, { padding: "0", height: "100%", background: "transparent" });
  let panel: HTMLElement;
  if (real) {
    panel = real.cloneNode(true) as HTMLElement;
    panel.querySelectorAll("[id]").forEach((el) => el.removeAttribute("id"));
    panel.removeAttribute("id");
    panel.querySelectorAll(".ask-log, .ask-empty").forEach((el) => el.remove());
    // as the chat looks while it works on the question: chips and the box greyed, the box saying so
    panel.querySelectorAll("button").forEach((el) => el.setAttribute("disabled", ""));
    panel.querySelectorAll<HTMLInputElement>("input.ask-input").forEach((el) => {
      el.value = "";
      el.setAttribute("placeholder", "Working on your question…");
      el.setAttribute("disabled", "");
    });
  } else {
    panel = document.createElement("aside");
    panel.className = "ask";
    const head = document.createElement("div");
    head.className = "ask-head";
    const title = document.createElement("h2");
    title.className = "ask-title cond";
    title.textContent = "Ask the data";
    head.append(title);
    const body = document.createElement("div");
    body.className = "ask-body";
    panel.append(head, body);
  }
  Object.assign(panel.style, { position: "static", width: "100%", height: "100%", maxHeight: "none", overflow: "hidden" });
  const log = document.createElement("div");
  log.className = "ask-log";
  const q = document.createElement("p");
  q.className = "ask-q";
  q.textContent = question;
  const wait = document.createElement("p");
  wait.className = "ask-thinking";
  wait.textContent = "The agents are looking at the data.";
  log.append(q, wait);
  const body = panel.querySelector(".ask-body");
  if (body) body.insertBefore(log, body.firstChild);
  else panel.append(log);
  ctx.append(panel);
  wrap.append(ctx);
  return wrap;
}

function place(el: HTMLElement, r: Rect) {
  Object.assign(el.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` });
}

/**
 * The box (the composer, or the question clicked) turns into the chat panel: a copy of the real panel, as it will
 * look with the question asked, grows from the box to the panel's place while the landing steps back. Resolves
 * when it lands; the copy stays (the page reloads under it, and the next page dissolves it onto the real panel).
 */
function flyToChat(from: HTMLElement, question: string, frame: HTMLElement | null): Promise<void> {
  const a = from.getBoundingClientRect();
  const real = chatPanel();
  const b: Rect = real ? rectOf(real) : chatSlot();
  const card = panelCopy(real, question);
  place(card, b);
  document.body.appendChild(card);
  const inner = card.firstElementChild as HTMLElement;
  const ease = "cubic-bezier(0.2, 0.75, 0.15, 1)";
  card.animate(
    [
      {
        transform: `translate(${a.left - b.left}px, ${a.top - b.top}px) scale(${Math.max(0.05, a.width / b.width)}, ${Math.max(0.03, a.height / b.height)})`,
        borderRadius: "10px",
      },
      { transform: "translate(0, 0) scale(1, 1)", borderRadius: "16px" },
    ],
    { duration: 560, easing: ease, fill: "both" },
  );
  inner.animate([{ opacity: 0 }, { opacity: 0, offset: 0.55 }, { opacity: 1 }], { duration: 560, easing: "ease-out", fill: "both" });
  frame?.animate([{ opacity: 1 }, { opacity: 0.12 }], { duration: 420, easing: "ease-out", fill: "forwards" });
  return new Promise((resolve) =>
    window.setTimeout(() => {
      try {
        // the next page puts the same copy back over its real panel and dissolves it there
        sessionStorage.setItem(HANDOFF, JSON.stringify({ html: card.innerHTML, rect: b, at: Date.now() }));
      } catch {
        /* blocked storage: no hand-over, the panel simply appears */
      }
      // without a reload the real chat takes over: the copy steps aside
      window.setTimeout(() => {
        card.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 260, fill: "forwards" }).onfinish = () => card.remove();
      }, 2500);
      resolve();
    }, 600),
  );
}

/**
 * On the page that loads after the landing: the copy of the chat panel is put back exactly where it landed, over
 * the real panel, and dissolves once the real panel shows the question, so the hand-over shows nothing moving.
 */
export function playChatHandoff(): void {
  let saved: { html?: string; rect?: Rect; at?: number } | null = null;
  try {
    saved = JSON.parse(sessionStorage.getItem(HANDOFF) || "null");
    sessionStorage.removeItem(HANDOFF);
  } catch {
    saved = null;
  }
  if (!saved?.html || !saved.rect || !saved.at || Date.now() - saved.at > 15000) return;
  const card = document.createElement("div");
  card.className = "pg-fly";
  card.setAttribute("aria-hidden", "true");
  card.innerHTML = saved.html;
  const real = chatPanel();
  place(card, real ? rectOf(real) : saved.rect);
  document.body.appendChild(card);
  const started = Date.now();
  const dissolve = () => {
    const asked = document.querySelector("aside.ask .ask-q");
    if (!asked && Date.now() - started < 4000) {
      window.setTimeout(dissolve, 120);
      return;
    }
    if (typeof card.animate !== "function") {
      card.remove();
      return;
    }
    card.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 320, easing: "ease-out", fill: "forwards" }).onfinish = () => card.remove();
  };
  dissolve();
}
