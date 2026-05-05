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

export type WorkflowTerminalArtifact = {
  type: string; // "Series" | "SeriesSet" | ...
  series_key?: string;
  units?: string | null;
  frequency?: string | null;
  n_rows: number;
  first_row?: { date: string; value: number | null };
  last_row?: { date: string; value: number | null };
  summary_stats?: {
    mean?: number | null;
    std?: number | null;
    min?: number | null;
    max?: number | null;
    n_finite?: number;
  };
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
  template_id: string;
  terminal_artifact?: WorkflowTerminalArtifact;
  workflow_lineage_summary?: string;
  error?: string;
  route?: {
    template_id: string;
    slot_values: Record<string, unknown>;
    rationale: string;
  };
};

// Discriminated union extension — any of these may arrive on the chat WS.
export type WorkflowServerEvent =
  | WorkflowRouteDecisionEvent
  | WorkflowStatusEvent
  | WorkflowResultEvent;
