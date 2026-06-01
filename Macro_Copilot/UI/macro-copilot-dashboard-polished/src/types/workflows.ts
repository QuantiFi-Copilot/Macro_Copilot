// ============================================================================
// Workflows + Tools catalogue types
// ----------------------------------------------------------------------------
// Mirrors the wire shapes the FastAPI surface in api/routes/workflows/* emits:
//
//   - GET  /api/v1/workflows                  -> WorkflowCatalogueResponse
//   - GET  /api/v1/workflows/{template_id}    -> WorkflowCardEnvelope
//   - GET  /api/v1/tools                      -> ToolCatalogueResponse
//   - GET  /api/v1/tools/{tool_name}          -> ToolCardEnvelope
//   - POST /api/v1/workflows/{id}/run         -> WorkflowExecuteEnvelope
//
// Keep this file in lockstep with the Pydantic models in
// `api/routes/workflows/catalogue.py` + `execute.py`.  Adding a new field
// on the backend = add it here too (typescript will surface the mismatch
// when the consumer tries to read it).
// ============================================================================

// ---------------------------------------------------------------------------
// Workflow templates
// ---------------------------------------------------------------------------

export type SlotDeclaration = {
  name: string;
  type: 'str' | 'int' | 'float' | 'bool' | 'dict' | 'list';
  required: boolean;
  description: string;
  default?: unknown;
};

export type WorkflowTemplateCard = {
  template_id: string;
  archetype: string;
  description: string;
  slot_schema: SlotDeclaration[];
  terminal_artifact_type: string;
  primitives_used: string[];
  operators_used: string[];
  node_count: number;
  edge_count: number;
  archetype_signature: string[];
};

export type WorkflowCatalogueResponse = {
  workflows: WorkflowTemplateCard[];
};

export type WorkflowCardEnvelope =
  | { ok: true; card: WorkflowTemplateCard }
  | { ok: false; error: string; known_template_ids?: string[] };

// ---------------------------------------------------------------------------
// Tools (primitive catalogue)
// ---------------------------------------------------------------------------

export type ToolFieldDescriptor = {
  name: string;
  type: string;
  required: boolean;
  default?: unknown;
  description?: string | null;
  examples?: unknown[] | null;
};

export type ToolConventionDescriptor = {
  name: string;
  value: unknown;
  source: string;
  rationale: string;
  valid_range?: unknown[] | null;
  valid_values?: unknown[] | null;
};

export type ToolMethodologyDescriptor = {
  what_it_does: string;
  assumptions: string[];
  citations: string[];
  planned_extensions: string[];
};

export type ToolCard = {
  tool_name: string;
  domain: string;
  category?: string | null;
  description: string;
  input_fields: ToolFieldDescriptor[];
  output_fields: ToolFieldDescriptor[];
  methodology: ToolMethodologyDescriptor;
  conventions: ToolConventionDescriptor[];
};

export type ToolCatalogueResponse = {
  tools: ToolCard[];
};

export type ToolCardEnvelope =
  | { ok: true; card: ToolCard }
  | { ok: false; error: string; known_tool_names?: string[] };

// ---------------------------------------------------------------------------
// Workflow execution envelope (mirrors _runner.run_template return shape)
// ---------------------------------------------------------------------------

/** Discriminator for the Series index — calendar dates vs synthetic-anchor
 *  event-relative offsets (e.g. conditional_aggregate output where the
 *  index is "days from event" not "calendar day").  When the backend
 *  reports `event_relative_offset`, the renderer should label rows as
 *  "Day N" using `offsets`, not the misleading 1970-anchored date. */
export type SeriesIndexKind = 'calendar' | 'event_relative_offset';

export type WorkflowTerminalArtifact = {
  type: string; // "Series" | "SeriesSet" | ...
  series_key?: string;
  units?: string | null;
  frequency?: string | null;
  n_rows: number;
  /** Calendar vs event-relative offset semantics — defaults to
   *  calendar when absent (older backends). */
  index_kind?: SeriesIndexKind;
  /** Calendar-shaped head/tail (date + value).  Present when
   *  `index_kind === "calendar"` (or absent). */
  first_row?: {
    date?: string;
    offset?: number | null;
    value: number | null;
  };
  last_row?: {
    date?: string;
    offset?: number | null;
    value: number | null;
  };
  summary_stats?: {
    mean?: number | null;
    std?: number | null;
    min?: number | null;
    max?: number | null;
    n_finite?: number;
  };
  // Event-relative-offset Series:
  /** ISO date string the offsets are encoded onto on the wire. */
  offset_anchor?: string;
  /** The integer event-relative offsets (e.g. [0,1,2,3,4,5] for a 5-day
   *  forward window).  Same length as `offset_rows`. */
  offsets?: number[];
  offset_unit?: 'days';
  /** Full offset → value pairs.  Small (<=30 typically), so the chat
   *  card can render every horizon as a row / bar inline. */
  offset_rows?: Array<{ offset: number; value: number | null }>;
  // SeriesSet-shaped:
  keys?: string[];
  units_by_key?: Record<string, string>;
  first_date?: string | null;
  last_date?: string | null;
  // EventSet-shaped:
  source_series_key?: string;
  n_dates?: number;
  n_events?: number;
};

export type WorkflowExecuteEnvelope = {
  ok: boolean;
  template_id: string;
  terminal_artifact?: WorkflowTerminalArtifact;
  workflow_lineage_summary?: string;
  error?: string;
};

// ---------------------------------------------------------------------------
// Workflow chat events (extension of the existing copilot ServerEvent union)
// ---------------------------------------------------------------------------

export type WorkflowRouteAction = 'route' | 'out_of_scope' | 'clarify';

export type WorkflowRouteDecisionEvent = {
  type: 'workflow_route_decision';
  action: WorkflowRouteAction;
  template_id: string | null;
  slot_values: Record<string, unknown>;
  rationale: string;
  clarification_question: string | null;
  adjustments: string[];
};

export type WorkflowStatusEvent = {
  type: 'workflow_status';
  status: 'running' | 'complete' | 'error';
};

export type WorkflowResultEvent = {
  type: 'workflow_result';
  ok: boolean;
  /** PR-11B: nullable to carry the open-DAG variant.  The template
   *  lane emits a real template_id string; the open-DAG lane emits
   *  ``null`` (no template).  Frontend useCopilot reducer accepts
   *  null as the open-DAG route + builds the WorkflowTurnPayload
   *  normally with template_id threaded through. */
  template_id: string | null;
  terminal_artifact?: WorkflowTerminalArtifact;
  workflow_lineage_summary?: string;
  error?: string;
  route?: {
    /** PR-11B: nullable to mirror the open-DAG variant carried on
     *  ``WorkflowResultEvent.template_id``. */
    template_id: string | null;
    slot_values: Record<string, unknown>;
    rationale: string;
  };
  // PR A persistence handles — emitted when the chat path opted into
  // ``persist=True`` and the runner successfully wrote the executed
  // workflow as a slug-routed workspace.  All fields are optional so
  // older backends (or persistence failures) degrade gracefully:
  // the chat still renders the result inline; Build's "Open in
  // Build" affordance only appears when ``workspace.slug`` is set.
  terminal_artifact_hash?: string | null;
  node_artifact_hashes?: Record<string, string> | null;
  dag_hash?: string | null;
  workspace?: {
    id: string;
    slug: string;
    name: string | null;
    dag_hash: string;
    url: string;
  } | null;
  persistence?: {
    ok: boolean;
    error?: string;
  } | null;
};

// Discriminated union extension — any of these may arrive on the chat WS.
export type WorkflowServerEvent =
  | WorkflowRouteDecisionEvent
  | WorkflowStatusEvent
  | WorkflowResultEvent;

// ---------------------------------------------------------------------------
// Primitive direct-run envelope (POST /api/v1/tools/{tool_name}/run)
// ---------------------------------------------------------------------------
//
// Shape mirrors api/routes/workflows/execute.py::run_primitive_tool:
//
//   { ok: true,  tool_name: "...", output: <raw primitive *Output dict> }
//   { ok: false, tool_name: "...", error: "..." }
//
// `output` is intentionally untyped — every primitive's *Output schema is
// different.  Consumers call into typed helpers to render the relevant
// shape (TimeSeries → line chart, scalar → KPI tile, etc).

export type PrimitiveRunResult =
  | {
      ok: true;
      tool_name: string;
      output: Record<string, unknown>;
    }
  | {
      ok: false;
      tool_name: string;
      error: string;
      known_tool_names?: string[];
    };

// One row of a canonical TimeSeries payload (date + value, plus optional
// auxiliary fields the primitive's *Output may include).  Most rates
// primitives emit `time_series` with this shape.
export type PrimitiveTimeSeriesRow = {
  date: string;
  value?: number | null;
  spread_bps?: number | null;
  z_score?: number | null;
  yield_pct?: number | null;
  forward_pct?: number | null;
  change_z_score?: number | null;
  [k: string]: unknown;
};
