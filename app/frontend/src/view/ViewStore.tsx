// The view store: holds the spec the backend pushes, and sends the page's own
// ops (keep, remove, clear) as agent "user". The browser never edits the spec
// locally; every change goes through POST /view/ops and comes back as a new
// version over SSE (GET /view/events, event "spec"), or by polling
// GET /view?since=N every 2 s when SSE is not available.

import { claim, useOwnershipVersion, visibleSpec } from "./ownership";
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { API_URL, VIEW_MOCK, apiHref, viewHref } from "./api";
import { contextOf, withPanelContext, type PageContext } from "./context";
import { agentInfos } from "./tokens";
import type { AgentInfo, RegisteredAgent, ViewOp, ViewResponse, ViewSpec, ViewStatus } from "./types";

// The page context (contract 1.1): every panel fills "$period", "$previous" and
// "$product" from spec.context before it fetches. Re-exported here so a panel
// needs one import: usePageContext() and substituteQueries(panel.queries, ctx).
export {
  contextOf,
  previousPeriod,
  substituteContext,
  substituteQuery,
  substituteQueries,
  substituteFacts,
  fillUrl,
  periodMatches,
  markOffContext,
  markPeriod,
  aboutTag,
  literalPeriodOf,
  OFF_CONTEXT_OPACITY,
  DEFAULT_CONTEXT,
  ownContextOf,
  withPanelContext,
  panelContextOf,
  placeholdersOf,
  rangeStart,
} from "./context";
export type { PageContext, PanelOwnContext } from "./context";

export interface SendOptions {
  /** Shown in the history and in spec.message. */
  message?: string;
  /** Agent name; the person at the page is "user". */
  agent?: string;
  /** Defaults to the version on screen; pass null to skip the staleness check. */
  baseVersion?: number | null;
}

export interface ViewStoreValue {
  spec: ViewSpec | null;
  /** spec.version, or -1 before the first spec arrives. */
  version: number;
  status: ViewStatus;
  /** Registered agents (GET /agents, refreshed every 30 s), or the four demo pens when none is registered. */
  agents: AgentInfo[];
  /** POSTs /view/ops; resolves (never rejects) with the response, and applies its spec. */
  send: (ops: ViewOp[], options?: SendOptions) => Promise<ViewResponse>;
  undo: () => Promise<ViewResponse>;
  redo: () => Promise<ViewResponse>;
  reset: () => Promise<ViewResponse>;
  /** Fetch GET /view now. */
  refresh: () => Promise<void>;
  /** True when the spec on screen is the fallback copy, not the backend's. */
  fallback: boolean;
}

const POLL_MS = 2000;
const AGENTS_MS = 30000;
const SSE_RETRY_MS = 15000;

const noop = async (): Promise<ViewResponse> => ({ ok: false, status: 0, detail: "No ViewStoreProvider" });

const ViewStoreContext = createContext<ViewStoreValue>({
  spec: null,
  version: -1,
  status: "offline",
  agents: agentInfos([]),
  send: noop,
  undo: noop,
  redo: noop,
  reset: noop,
  refresh: async () => undefined,
  fallback: false,
});

function isSpec(x: any): x is ViewSpec {
  return !!x && typeof x === "object" && x.spec_version === "1.0" && Array.isArray(x.panels) && !!x.layout;
}

/** A spec from any of the shapes the routes may answer with: the spec itself, or {spec}. */
function specOf(body: any): ViewSpec | null {
  if (isSpec(body)) return body;
  if (body && isSpec(body.spec)) return body.spec;
  return null;
}

function runId(): string {
  return `user-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

async function readJson(res: Response): Promise<any> {
  try {
    return await res.json();
  } catch {
    return null;
  }
}

export const ViewStoreProvider: React.FC<{ children?: React.ReactNode }> = ({ children }) => {
  const [spec, setSpecState] = useState<ViewSpec | null>(null);
  const [status, setStatus] = useState<ViewStatus>("offline");
  const [registry, setRegistry] = useState<RegisteredAgent[]>([]);
  const [fallback, setFallback] = useState(false);
  const specRef = useRef<ViewSpec | null>(null);
  const fallbackRef = useRef(false);
  fallbackRef.current = fallback;

  /** Applies a spec from the backend. `force` takes it even when its version is not newer (SSE, undo, reset). */
  const applySpec = useCallback((next: ViewSpec | null, force = false) => {
    if (!next) return;
    const cur = specRef.current;
    if (!force && cur && next.version === cur.version) return;
    claim(next); // before the page sees it: what this browser's questions drew is its own
    specRef.current = next;
    fallbackRef.current = false;
    setSpecState(next);
    setFallback(false);
  }, []);

  const loadFallback = useCallback(async () => {
    // Mock mode shows public/mock/view.json (a spec with marks); otherwise, while
    // the backend's /view is unreachable, the baseline copy keeps the page up.
    if (specRef.current) return;
    try {
      const res = await fetch(VIEW_MOCK ? "/mock/view.json" : "/mock/baseline.json");
      if (!res.ok) return;
      const body = specOf(await readJson(res));
      if (body && !specRef.current) {
        specRef.current = body;
        fallbackRef.current = true;
        setSpecState(body);
        setFallback(true);
      }
    } catch {
      // nothing to show yet
    }
  }, []);

  const refresh = useCallback(async () => {
    if (VIEW_MOCK) return;
    try {
      const res = await fetch(viewHref("/view"), { headers: { Accept: "application/json" } });
      if (!res.ok) throw new Error(String(res.status));
      applySpec(specOf(await readJson(res)), true);
    } catch {
      setStatus("offline");
      await loadFallback();
    }
  }, [applySpec, loadFallback]);

  // ------------------------------------------------------------ spec: SSE, polling fallback
  useEffect(() => {
    if (VIEW_MOCK) {
      // Mock mode: public/mock/view.json, no backend /view.
      loadFallback();
      return;
    }
    let disposed = false;
    let es: EventSource | null = null;
    let pollTimer: number | undefined;
    let retryTimer: number | undefined;

    const poll = async () => {
      if (disposed) return;
      const since = specRef.current && !fallbackRef.current ? specRef.current.version : -1;
      let failed = false;
      try {
        const res = await fetch(viewHref(since >= 0 ? `/view?since=${since}` : "/view"), {
          headers: { Accept: "application/json" },
        });
        if (res.status === 204 || res.status === 304) {
          if (!disposed && !es) setStatus("polling");
        } else if (res.ok) {
          const body = await readJson(res);
          const next = specOf(body);
          if (next) applySpec(next, next.version !== specRef.current?.version);
          if (!disposed && !es) setStatus("polling");
        } else {
          throw new Error(String(res.status));
        }
      } catch {
        failed = true;
        if (!disposed && !es) {
          setStatus("offline");
          loadFallback();
        }
      }
      // Slower while the backend is away.
      if (!disposed && !es) pollTimer = window.setTimeout(poll, failed ? POLL_MS * 3 : POLL_MS);
    };

    const openSse = () => {
      if (disposed || typeof EventSource === "undefined") return;
      let opened = false;
      try {
        es = new EventSource(viewHref("/view/events"));
      } catch {
        es = null;
        return;
      }
      es.addEventListener("open", () => {
        opened = true;
        window.clearTimeout(pollTimer);
        pollTimer = undefined;
        setStatus("live");
      });
      es.addEventListener("spec", (ev: Event) => {
        try {
          const next = specOf(JSON.parse((ev as MessageEvent).data));
          applySpec(next, true);
          setStatus("live");
        } catch {
          // a bad frame; the next one or the poll will catch up
        }
      });
      es.addEventListener("error", () => {
        // Fall back to polling, and try SSE again later.
        if (es) es.close();
        es = null;
        if (disposed) return;
        if (!opened) setStatus(specRef.current && !fallbackRef.current ? "polling" : "offline");
        window.clearTimeout(pollTimer);
        pollTimer = window.setTimeout(poll, opened ? 0 : POLL_MS);
        window.clearTimeout(retryTimer);
        retryTimer = window.setTimeout(openSse, SSE_RETRY_MS);
      });
    };

    refresh().then(() => {
      if (!disposed) openSse();
    });

    return () => {
      disposed = true;
      if (es) es.close();
      window.clearTimeout(pollTimer);
      window.clearTimeout(retryTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ------------------------------------------------------------ agents
  useEffect(() => {
    if (VIEW_MOCK) return;
    let disposed = false;
    const load = async () => {
      try {
        const res = await fetch(apiHref("/agents"), { headers: { Accept: "application/json" } });
        if (!res.ok) throw new Error(String(res.status));
        const body = await readJson(res);
        const list: RegisteredAgent[] = Array.isArray(body) ? body : Array.isArray(body?.agents) ? body.agents : [];
        if (!disposed) setRegistry(list.filter((a) => a && typeof a.name === "string"));
      } catch {
        if (!disposed) setRegistry([]);
      }
    };
    load();
    const t = window.setInterval(load, AGENTS_MS);
    return () => {
      disposed = true;
      window.clearInterval(t);
    };
  }, []);

  // ------------------------------------------------------------ writes
  const post = useCallback(
    async (path: string, body?: unknown): Promise<ViewResponse> => {
      if (VIEW_MOCK) return { ok: false, status: 0, detail: "Mock mode (VITE_VIEW_MOCK=1): the spec is read-only" };
      try {
        const res = await fetch(viewHref(path), {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: body === undefined ? undefined : JSON.stringify(body),
        });
        const json = (await readJson(res)) || {};
        const next = specOf(json);
        // 200 and 409 both carry the current spec.
        if (next) applySpec(next, true);
        const detail = typeof json.detail === "string" ? json.detail : undefined;
        return {
          ok: res.ok,
          status: res.status,
          version: typeof json.version === "number" ? json.version : next?.version,
          assigned: json.assigned,
          warnings: json.warnings,
          errors: Array.isArray(json.errors) ? json.errors : Array.isArray(json.detail?.errors) ? json.detail.errors : undefined,
          detail: detail || (res.ok ? undefined : res.statusText),
          spec: next || undefined,
        };
      } catch (e: any) {
        return { ok: false, status: 0, detail: `The backend could not be reached at ${API_URL}` };
      }
    },
    [applySpec],
  );

  const send = useCallback(
    (ops: ViewOp[], options?: SendOptions) => {
      const cur = specRef.current;
      const base =
        options?.baseVersion === null ? undefined : options?.baseVersion ?? (cur && !fallbackRef.current ? cur.version : undefined);
      const envelope: Record<string, unknown> = {
        agent: { name: options?.agent || "user", run_id: runId() },
        ops,
      };
      if (base !== undefined) envelope.base_version = base;
      if (options?.message) envelope.message = options.message;
      return post("/view/ops", envelope);
    },
    [post],
  );
  const undo = useCallback(() => post("/view/undo"), [post]);
  const redo = useCallback(() => post("/view/redo"), [post]);
  const reset = useCallback(() => post("/view/reset"), [post]);

  const agents = useMemo(() => agentInfos(registry), [registry]);
  // A page cleared on the backend (by an earlier session or an agent) does not carry over: a session that
  // opens on an empty page gets the starting page back.
  const checked = useRef(false);
  useEffect(() => {
    if (!spec || fallback || checked.current) return;
    checked.current = true;
    if (spec.panels.length === 0) post("/view/reset");
  }, [spec, fallback, post]);
  const mineVersion = useOwnershipVersion();
  // Agent charts and notes other browsers asked for stay on the backend but not on this page.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const shown = useMemo(() => visibleSpec(spec, fallback), [spec, fallback, mineVersion]);

  const value = useMemo<ViewStoreValue>(
    () => ({
      spec: shown,
      version: spec ? spec.version : -1,
      status,
      agents,
      send,
      undo,
      redo,
      reset,
      refresh,
      fallback,
    }),
    [spec, shown, status, agents, send, undo, redo, reset, refresh, fallback],
  );

  return <ViewStoreContext.Provider value={value}>{children}</ViewStoreContext.Provider>;
};

export function useViewStore(): ViewStoreValue {
  return useContext(ViewStoreContext);
}

/** The context of the spec on screen (the page's): {period, product, flow?}, with the defaults (2025, TOTAL). */
export function useSpecContext(): PageContext {
  const { spec } = useContext(ViewStoreContext);
  const raw = (spec as any)?.context;
  const key = raw ? `${raw.period}|${raw.product}|${raw.flow || ""}|${raw.start || ""}|${raw.persona || ""}` : "";
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => contextOf(spec), [key]);
}

// Contract 1.5: inside a panel, the context is the panel's (the page's with its own year and goods on top),
// so its queries, facts, marks and "How this was made" card all read the same year. PanelContextScope sets it.
const PanelContextCtx = createContext<PageContext | null>(null);

/** Children read the panel's own context through usePageContext(). */
export const PanelContextScope: React.FC<{ panel: { context?: unknown } | null | undefined; children?: React.ReactNode }> = ({ panel, children }) => {
  const page = useSpecContext();
  const own = (panel as any)?.context;
  const key = own ? `${own.period || ""}|${own.start || ""}|${own.product || ""}` : "";
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const value = useMemo(() => withPanelContext(page, panel), [page, key]);
  return <PanelContextCtx.Provider value={value}>{children}</PanelContextCtx.Provider>;
};

/**
 * The context to fill placeholders from: inside a panel, the panel's own (contract 1.5); elsewhere the
 * page's: {period, product, flow?}, with the defaults (2025, TOTAL).
 */
export function usePageContext(): PageContext {
  const page = useSpecContext();
  const panel = useContext(PanelContextCtx);
  return panel || page;
}

/** The context a mark's facts and query are filled from: its panel's (contract 1.5), wherever the note is shown. */
export function useMarkContext(mark: { target?: string } | null | undefined): PageContext {
  const { spec } = useContext(ViewStoreContext);
  const page = useSpecContext();
  const panel = spec?.panels.find((p) => p.id === mark?.target);
  const own = (panel as any)?.context;
  const key = own ? `${own.period || ""}|${own.start || ""}|${own.product || ""}` : "";
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => withPanelContext(page, panel), [page, key]);
}

/** A name safe in a mark note: no {fact} braces, no figures read as typed-in amounts. */
function safeName(name: string): string {
  return String(name || "")
    .replace(/[{}]/g, "")
    .replace(/%/g, " percent")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 80);
}

/**
 * The person's own highlight: clicking a bar, a node or a country rings it in
 * ink and tells the agents what the person is looking at. A second click on
 * the same code takes it off again. Sent as agent "user", so "Clear agent
 * marks" leaves it.
 */
export function useUserHighlight(panelId: string): {
  /** The user's highlight mark on this code of this panel, if any. */
  markOf: (code: string) => string | undefined;
  toggle: (code: string, name?: string) => Promise<ViewResponse | null>;
} {
  const { spec, send } = useContext(ViewStoreContext);
  const ctx = usePageContext();
  const mine = (spec?.marks || []).filter((m) => m.agent === "user" && m.kind === "highlight" && m.target === panelId);
  const markOf = (code: string) => mine.find((m) => Array.isArray(m.params?.codes) && m.params.codes.length === 1 && m.params.codes[0] === code)?.id;
  const toggle = async (code: string, name?: string) => {
    if (!spec) return null;
    const existing = markOf(code);
    // The person's own picks never conflict with what changed meanwhile: no staleness check.
    if (existing) return send([{ op: "remove_mark", id: existing }], { message: `Took the highlight off ${safeName(name || code)}`, baseVersion: null });
    const label = safeName(name || code);
    return send(
      [
        {
          op: "add_mark",
          mark: {
            kind: "highlight",
            target: panelId,
            params: { codes: [code], label },
            note: `Picked on the page: ${label}.`,
            period: ctx.period,
          },
        },
      ],
      { message: `Highlighted ${label}`, baseVersion: null },
    );
  };
  return { markOf, toggle };
}

/**
 * Brings a mark (or, failing that, its panel) into view and flashes it in its
 * agent's pen. Mark elements carry data-mark="<id>"; panels data-panel="<id>".
 * Used by the [n] references in the chat and by the notes.
 */
export function revealMarks(markIds: string[], targetPanel?: string, color?: string): boolean {
  if (typeof document === "undefined") return false;
  const els: Element[] = [];
  markIds.forEach((id) => document.querySelectorAll(`[data-mark="${CSS.escape(id)}"]`).forEach((el) => els.push(el)));
  const panel = targetPanel ? document.querySelector(`[data-panel="${CSS.escape(targetPanel)}"]`) : null;
  const first = els[0] || panel;
  if (!first) return false;
  const reduce = !!window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  (first as HTMLElement).scrollIntoView?.({ behavior: reduce ? "auto" : "smooth", block: "center" });
  const flash = (el: Element, cls: string) => {
    if (color) (el as HTMLElement).style?.setProperty("--flash", color);
    el.classList.remove(cls);
    // Restart the animation.
    void (el as HTMLElement).getBoundingClientRect?.();
    el.classList.add(cls);
    window.setTimeout(() => el.classList.remove(cls), 2200);
  };
  els.forEach((el) => flash(el, "cx-flash-mark"));
  if (panel) flash(panel, "cx-flash-panel");
  return true;
}
