// ============================================================================
// Library manifest types — 1:1 with the backend response
// ----------------------------------------------------------------------------
// Backend: api/routes/library/manifest.py
// Endpoint: GET /api/v1/library/manifest
// Source: parsed from manifesto/03_tool_manifest/<agent>/*.yml
//
// Keep these types aligned with the Pydantic models on the backend
// (LibraryManifestResponse, AgentManifest, ManifestTool, ToolImplementation).
// Schema drift is visible at runtime — the manifest endpoint will return
// fields the frontend doesn't know about; consumers should treat the
// response as a typed snapshot of YAML, not as a hand-rolled API.
// ============================================================================

export type ToolImplementation = {
  mcp_server: string;
  tool_function: string;
  /** YAML key is `schema`; backend renames to `schema_` to avoid the
   *  pydantic.BaseModel.schema clash and re-aliases it back via
   *  populate_by_name.  Consumers see `schema_` either way. */
  schema_: string;
  compute: string;
  config: string;
};

export type ManifestTool = {
  name: string;
  domain: string;
  sub_agent: string;
  bucket: string; // "1A" | "1B" | "2"
  category: string; // see CATEGORY_LABELS below
  status: string;
  implementation: ToolImplementation;
  one_liner: string;
  bucket_rationale: string;
  pm_overridable: string[];
  related_tools: string[];
  workflows: string[];
  references: string[];
  built_date: string;
  validation_status: string;
};

export type AgentManifest = {
  agent: string;
  tool_count: number;
  tools: ManifestTool[];
  sub_agent_counts: Record<string, number>;
  category_counts: Record<string, number>;
};

export type LibraryManifestResponse = {
  agents: Record<string, AgentManifest>;
  total_tools: number;
};

// ----------------------------------------------------------------------------
// User-facing labels for the manifest's machine keys.  The backend
// keeps them lowercase + snake_case (curve_shape, etc.); the UI
// renders the friendly form via these maps.

export const CATEGORY_LABELS: Record<string, string> = {
  snapshots:          'Snapshots',
  curve_shape:        'Curve shape',
  cross_market_rv:    'Cross-market RV',
  forwards_classify:  'Forwards & classify',
  screening:          'Screening',
  rolling_analytics:  'Rolling analytics',
  model_fits:         'Model fits',
};

/** Maps each category to one of the three semantic colors used across
 *  the product (data / analysis / anomaly) so cards + chips stay
 *  visually consistent with the widget engine. */
export const CATEGORY_TONE: Record<string, 'data' | 'analysis' | 'anomaly'> = {
  snapshots:          'data',
  curve_shape:        'data',
  cross_market_rv:    'analysis',
  forwards_classify:  'analysis',
  screening:          'anomaly',
  rolling_analytics:  'analysis',
  model_fits:         'analysis',
};

export const SUB_AGENT_LABELS: Record<string, string> = {
  sovereign_bonds: 'Sovereign Bonds',
  ois:             'OIS',
};

export const AGENT_LABELS: Record<string, string> = {
  rates_agent: 'Rates Agent',
};

export const BUCKET_LABELS: Record<string, string> = {
  '1A': 'Deterministic',
  '1B': 'Statistical fit',
  '2':  'Open',
};
