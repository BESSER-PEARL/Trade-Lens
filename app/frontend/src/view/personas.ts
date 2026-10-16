// Personas (contract 1.4): who the page is drawn for. spec.context.persona names a profile of
// contract/personas/persona_profiles.json (this folder holds a copy; tests/test_personas.py checks
// it). A profile chooses panels, titles, depth and questions; it never changes a figure: every
// number still comes from /insights through {fact} placeholders.

import profilesDoc from "./personas/persona_profiles.json";
import variantsDoc from "./personas/persona_variants.json";
import { CHAPTER_SHORT } from "./charts/format";
import { formatPeriod } from "./facts";
import type { Panel, ViewOp, ViewSpec } from "./types";

export interface PersonaProfile {
  id: string;
  label: string;
  tagline?: string;
  literacy: "expert" | "intermediate" | "general";
  default_view: {
    context: { period: string; product: string; flow?: "IMPORT" | "EXPORT" | "BOTH" };
    show_panels: string[];
    hide_panels?: string[];
    variant_ops?: string;
    panel_titles?: Record<string, string>;
  };
  agents: { enabled: string[]; lead?: string; max_per_question: number; show_agent_cards?: boolean };
  answer: { depth: string; max_sentences: number; [k: string]: unknown };
  suggested_questions: string[];
  hide?: string[];
  show?: string[];
  presentation: { interfaceStyle?: { size?: number; contrast?: "low" | "medium" | "high"; lineSpacing?: number }; [k: string]: unknown };
  [k: string]: unknown;
}

interface Variant {
  remove_on_leave: string[];
  message: string;
  ops: ViewOp[];
}

/** Listed first on the reader screen and in the "Reading as" menu; the others keep the file's order. */
const LISTED_FIRST = ["policy_adviser", "exporter"];
const rank = (id: string) => {
  const i = LISTED_FIRST.indexOf(id);
  return i < 0 ? LISTED_FIRST.length : i;
};
export const PERSONAS: PersonaProfile[] = ((profilesDoc as any).profiles as PersonaProfile[]).slice().sort((a, b) => rank(a.id) - rank(b.id));
const VARIANTS: Record<string, Variant> = (variantsDoc as any).variants;

/** The value of the "Reading as" control that means no persona: the page as it is for everyone. */
export const EVERYONE = "everyone";

export function profileOf(id: string | undefined | null): PersonaProfile | undefined {
  return id ? PERSONAS.find((p) => p.id === id) : undefined;
}

function variantOf(id: string | undefined | null): Variant | undefined {
  const p = profileOf(id);
  return p ? VARIANTS[p.default_view.variant_ops || p.id] : undefined;
}

/**
 * The one batch the "Reading as" control sends as agent "user": set_context with the persona and its
 * default context (a single year), the previous persona's unmarked panels removed, then the new persona's
 * panels. A switch never takes a mark off the page: a persona panel that carries a mark stays (as one of
 * the page's own panels), so every [n] keeps its number and its storyline.
 */
export function personaSwitchOps(spec: ViewSpec, to: string): ViewOp[] {
  const profile = profileOf(to);
  const ctx = profile ? profile.default_view.context : { period: String((spec as any)?.context?.period || "2025").slice(0, 4) };
  const ops: ViewOp[] = [{ op: "set_context", persona: to, ...ctx, start: ctx.period }];
  const here = new Set(spec.panels.map((p) => p.id));
  const leaving = new Set<string>();
  // The panels of the persona we leave (and of any other persona, should a page carry them).
  Object.values(VARIANTS).forEach((v) => v.remove_on_leave.forEach((id) => leaving.add(id)));
  const arriving = variantOf(to);
  const keep = new Set(arriving?.remove_on_leave || []);
  const marked = new Set((spec.marks || []).map((m) => m.target));
  spec.panels
    .filter((p) => leaving.has(p.id) && !keep.has(p.id) && p.agent === "user" && !marked.has(p.id))
    .forEach((p) => ops.push({ op: "remove_panel", id: p.id }));
  (arriving?.ops || []).forEach((op) => {
    // A persona panel already on the page is left as it is (its marks stay).
    if (op.op === "upsert_panel" && here.has(op.panel?.id)) return;
    ops.push(op);
  });
  return ops;
}

/**
 * The reader a panel was drawn for, when it is one of the panels the "Reading as" control adds
 * (persona_variants.json): sent as agent "user", but no click of the person's added it. Undefined for
 * any other panel.
 */
export function personaOfPanel(panel: Pick<Panel, "id" | "agent"> | undefined): PersonaProfile | undefined {
  if (!panel || panel.agent !== "user") return undefined;
  const key = Object.keys(VARIANTS).find((k) =>
    VARIANTS[k].remove_on_leave.indexOf(panel.id) >= 0 ||
    VARIANTS[k].ops.some((op) => op.op === "upsert_panel" && (op as any).panel?.id === panel.id),
  );
  return key ? PERSONAS.find((p) => (p.default_view.variant_ops || p.id) === key) : undefined;
}

export function switchMessage(to: string): string {
  const p = profileOf(to);
  return p ? `The page for ${aReader(p.label)}` : "The page for every reader";
}

/** "Journalist" -> "journalist", "STATEC analyst" stays (an acronym keeps its case). */
export function readerName(label: string): string {
  return /^[A-Z]{2}/.test(label) ? label : label.charAt(0).toLowerCase() + label.slice(1);
}

/** "a journalist", "a business owner", "a STATEC analyst". */
export function aReader(label: string): string {
  const l = readerName(label);
  return `${/^[aeiou]/i.test(l) ? "an" : "a"} ${l}`;
}

/** Baseline panels this reader folds under "More": never deleted, and never folded while they carry marks. */
export function foldedPanels(profile: PersonaProfile | undefined, spec: ViewSpec | null): string[] {
  if (!profile || !spec) return [];
  const marked = new Set((spec.marks || []).map((m) => m.target));
  return (profile.default_view.hide_panels || []).filter((id) => spec.panels.some((p) => p.id === id) && !marked.has(id));
}

/** A display title for a page panel (never written into the spec). */
export function personaTitle(profile: PersonaProfile | undefined, panel: Panel | undefined): string | undefined {
  if (!panel) return undefined;
  const own = panel.agent === "baseline" || panel.agent === "user" || !panel.agent;
  return (own && profile?.default_view.panel_titles?.[panel.id]) || panel.title;
}

/** The page's reading style: text a little larger and darker greys when the reader's profile asks. */
export function readingStyle(profile: PersonaProfile | undefined): { zoom: number; contrast: string; leading?: number } {
  const st = profile?.presentation?.interfaceStyle || {};
  const size = typeof st.size === "number" ? st.size : 16;
  return { zoom: size >= 18 ? Math.round((size / 18) * 100) / 100 : 1, contrast: st.contrast || "medium", leading: st.lineSpacing };
}

/**
 * A persona title that names what the chart shows: "this product group" becomes the goods the panel
 * follows (rubber, Luxembourg's goods) and "this year against last year" the years (2025 against 2024).
 * The spec keeps the generic wording (a title holds no typed figure); the page says it plainly.
 */
export function namedTitle(title: string | undefined, ctx: { period: string; product: string }): string | undefined {
  if (!title) return title;
  const codes = String(ctx.product || "TOTAL").split(",").filter(Boolean);
  const goods =
    !codes.length || codes[0] === "TOTAL"
      ? "Luxembourg's goods"
      : codes.map((c) => (CHAPTER_SHORT[c] ? CHAPTER_SHORT[c].toLowerCase() : `HS ${c}`)).join(", ").replace(/, ([^,]*)$/, " and $1");
  const m = /^(\d{4})(-\d{2})?$/.exec(String(ctx.period || ""));
  const years = m ? `${formatPeriod(ctx.period)} against ${formatPeriod(`${Number(m[1]) - 1}${m[2] || ""}`)}` : "";
  let out = title.replace(/\bthis product group\b/i, goods);
  if (years) out = out.replace(/\bthis year against last year\b/i, years);
  return out;
}
