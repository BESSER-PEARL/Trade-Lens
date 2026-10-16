// Types of the view spec 1.0 (contract/view-spec.schema.json) and of the
// /view and /agents routes. The browser only renders the spec: panels and
// marks carry queries on /insights, never data.

export type Scalar = string | number | boolean;
export type Params = Record<string, Scalar>;

export type Endpoint =
  | "/insights/meta"
  | "/insights/ranking"
  | "/insights/ranking/records"
  | "/insights/ladder"
  | "/insights/series"
  | "/insights/movers"
  | "/insights/compare"
  | "/insights/compare/records"
  | "/insights/specialisation"
  | "/insights/specialisation/records"
  | "/insights/trend"
  | "/insights/trends"
  | "/insights/forecast"
  | "/insights/events"
  | "/insights/flows";

export type Tone = "exports" | "imports" | "neutral";

export interface Query {
  key?: string;
  endpoint: Endpoint | string;
  params: Params;
  label?: string;
  tone?: Tone;
}

export type FactFormat =
  | "eur"
  | "eur_signed"
  | "pct"
  | "pct_signed"
  | "percent"
  | "percent_signed"
  | "number"
  | "int"
  | "ordinal"
  | "period"
  // A month of the year: 8 or "Aug" -> August (a seasonal peak or trough).
  | "month"
  | "text"
  // Contract 1.8, quantities: a count (12,400), the same with its unit ("qty:cars" -> 12,400 cars), tonnes (35,000 t).
  | "qty"
  | `qty:${string}`
  | "tonnes"
  // Aliases the renderer also accepts.
  | "compact"
  | "raw";

export interface QueryFact {
  query: string;
  path: string;
  format?: FactFormat;
}
export interface ApiFact {
  endpoint: Endpoint | string;
  params: Params;
  path: string;
  format?: FactFormat;
}
export interface OpFact {
  op: "diff" | "sum" | "ratio" | "growth" | "abs";
  of: string[];
  format?: FactFormat;
}
export type Fact = QueryFact | ApiFact | OpFact;
export type Facts = Record<string, Fact>;

export interface Source {
  url: string;
  label?: string;
}

export interface Provenance {
  agent?: string;
  run_id?: string;
  created_at?: string;
  sources?: Source[];
}

export type PanelType = "kpi" | "bar" | "line" | "table" | "flow" | "text";

export type OptionValue = Scalar | string[];

export interface Panel {
  id: string;
  type: PanelType | string;
  title: string;
  subtitle?: string;
  text?: string;
  facts?: Facts;
  value?: string;
  queries: Query[];
  options?: Record<string, OptionValue>;
  pinned?: boolean;
  agent?: string;
  provenance?: Provenance;
  /** Contract 1.5: the panel's own year, range or goods, which win over spec.context for this panel. */
  context?: { period?: string; start?: string; product?: string };
}

export type MarkKind = "thread" | "trend" | "event" | "forecast" | "annotation" | "highlight";

export interface Mark {
  id: string;
  n: number;
  /** Contract 1.6: the page number of the mark's storyline, given by the backend and kept (never renumbered). */
  story_n?: number;
  kind: MarkKind | string;
  agent: string;
  target: string;
  series?: string;
  note: string;
  facts?: Facts;
  params: Record<string, any>;
  query?: Query;
  status: "proposed" | "kept";
  thread?: string;
  provenance: Provenance;
}

export interface LayoutRow {
  label?: string;
  panels: string[];
}

export interface ViewSpec {
  spec_version: "1.0";
  version: number;
  title: string;
  updated_at?: string;
  updated_by?: string;
  message?: string;
  next_mark_n: number;
  /** Contract 1.6: the number the next new storyline gets. */
  next_story_n?: number;
  panels: Panel[];
  layout: { rows: LayoutRow[] };
  marks: Mark[];
}

/** One op of POST /view/ops (contract/ops.schema.json). Kept loose on purpose. */
export type ViewOp = { op: string } & Record<string, any>;

export interface OpError {
  path: string;
  code: string;
  message: string;
}

/** What send / undo / redo / reset resolve to. Never throws on 4xx. */
export interface ViewResponse {
  ok: boolean;
  /** HTTP status; 0 when the backend could not be reached. */
  status: number;
  version?: number;
  assigned?: Record<string, { id: string; n: number }>;
  warnings?: string[];
  errors?: OpError[];
  detail?: string;
  spec?: ViewSpec;
}

export interface AgentCapability {
  kind: string;
  produces?: string[];
  description?: string;
  inputs?: string[];
}

/** An agent as GET /agents returns it. */
export interface RegisteredAgent {
  name: string;
  title?: string;
  color?: string;
  capabilities?: AgentCapability[];
  examples?: string[];
  last_seen?: string;
}

/** An agent as the page shows it: the registry entry with its pen and ink filled in. */
export interface AgentInfo {
  name: string;
  title: string;
  /** Pen: marks, squares, number circles. */
  color: string;
  /** Ink: text written in the agent's colour. */
  ink: string;
  /** One line on what it can do. */
  can: string;
  examples: string[];
  /** True when it is in the GET /agents registry (seen in the last 60 s). */
  online: boolean;
}

export type ViewStatus = "live" | "polling" | "offline";
