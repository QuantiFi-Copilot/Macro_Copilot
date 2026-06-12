// ============================================================================
// Workspace API client
// ----------------------------------------------------------------------------
// Thin fetch wrappers against /api/v1/workspace + /api/v1/artifacts.
// Mirrors the discipline of services/ratesApi.ts + workflowsApi.ts.
//
// Phase 0 PR 10 — workspace persistence + URL routing.  Workspaces are
// identified by their slug in the URL; renames update the display name
// only, the slug (and therefore the URL) is stable.
//
// PR3 — adds ``getArtifactPayload`` for the ``/artifacts/{hash}/payload``
// surface so Build widgets can render a persisted artifact's full
// deserialised contents without re-running the underlying primitive.
// ============================================================================

import {
  isArtifactPayloadResponseShape,
  type ArtifactPayloadResponse,
} from '@/types/artifacts';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';
const PREFIX = `${API_BASE}/api/v1`;

async function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(
      `API ${res.status}: ${res.statusText}${body ? ` — ${body}` : ''}`,
    );
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Response shapes
// ---------------------------------------------------------------------------
// These mirror the Pydantic models in api/routes/workspace.py +
// api/routes/_replay_helpers.py.  Kept in this module rather than a
// shared types/ file because the workspace surface is its own
// vertical for Phase 0 — Phase 3's redesign will refactor it.

export interface ArtifactSummary {
  hash: string;
  artifact_type: string;
  units: string | null;
  frequency: string | null;
  row_count: number | null;
  byte_size: number;
  payload_uri: string | null;
  inline: boolean;
  created_at: string;
  preview_index: string[];
  preview_values: Array<number | null>;
}

export interface NodeSummary {
  node_id: string;
  kind: string;
  name: string;
  params: Record<string, unknown>;
  artifact_hash: string | null;
  artifact: ArtifactSummary | null;
}

export interface EdgeSummary {
  from_node: string;
  to_node: string;
  slot_name: string;
}

export interface WorkspaceDetail {
  workspace_id: string;
  slug: string;
  name: string | null;
  dag_hash: string;
  focus_node: string | null;
  parent_workspace_id: string | null;
  schema_version: number;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  nodes: NodeSummary[];
  edges: EdgeSummary[];
  // PR B — surfaced so the Build UI knows whether the fork
  // affordance is available.  Both non-null = forkable; either
  // null = locked (legacy workspace).
  template_id: string | null;
  bound_slot_values: Record<string, unknown> | null;
  // Phase D / D9 — the open-DAG intent-audit sidecar (additive;
  // null on pre-audit rows and lanes without an IntentChain).  The
  // build page renders "what I understood / checked / fixed" from
  // this: the DAG tab's understanding header, per-leaf selector
  // verdicts in the inspector, and the self-correction trace on the
  // Notes tab.
  run_audit?: WorkspaceRunAudit | null;
}

/** Phase D / D9 — the persisted intent-audit sidecar (versioned).
 *  The intent chain is the backend ``IntentChain.model_dump(mode=
 *  "json")`` — typed loosely here (renderers read only the stable,
 *  documented spine and treat everything as optional; the chain is
 *  audit metadata, never re-executed — FP9/P4). */
export interface WorkspaceRunAudit {
  schema_version: number;
  expected_answer_shape?: string | string[] | null;
  intent_chain?: {
    user_prompt?: string;
    router?: {
      intent_tag?: string;
      rationale?: string;
      decomposition?: Array<{
        name?: string;
        nl_description?: string;
        domain_hint?: string;
      }>;
    };
    selectors?: Array<{
      leaf_id?: string;
      domain?: string;
      bound_tool_name?: string;
      declared_semantic_role?: string;
      declared_output_meaning?: string;
      fit_confidence?: number;
      rationale?: string;
      refusal?: string | null;
    }>;
    composer?: {
      workflow_id?: string;
      operator_names?: string[];
      terminal_operator_name?: string;
      terminal_artifact_type?: string;
      rationale?: string;
      refusal?: string | null;
    };
    gate?: {
      status?: string;
      reason?: string;
      clarification_question?: string | null;
    };
  } | null;
  recompose_trace?: Array<{
    attempt_index?: number;
    failed_status?: string;
    reason?: string;
  }>;
}

export interface CreateWorkspaceResponse {
  workspace_id: string;
  slug: string;
  name: string | null;
  dag_hash: string;
  url: string;
}

// ----------------------------------------------------------------------------
// List surface — powers the Build sidebar's "Recent / All workspaces" lists.
// PR A.
// ----------------------------------------------------------------------------

/** Filter keys the server accepts on ``GET /workspace?filter=...``.
 *  ``recent`` (default) orders by last_accessed_at, COALESCED to
 *  updated_at so never-opened workspaces still surface.  ``all``
 *  orders by updated_at only.  ``pinned`` / ``shared`` are forward-
 *  compat with the sidebar's section taxonomy and currently fall
 *  through to ``recent`` ordering server-side. */
export type WorkspaceListFilter = 'recent' | 'all' | 'pinned' | 'shared';

export interface WorkspaceListItem {
  workspace_id: string;
  slug: string;
  name: string | null;
  dag_hash: string;
  focus_node: string | null;
  parent_workspace_id: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceListResponse {
  items: WorkspaceListItem[];
  limit: number;
  offset: number;
  filter: WorkspaceListFilter;
}

export interface MethodologyDiff {
  yaml_path: string;
  original_version_id: number;
  original_content_hash: string;
  current_version_id: number | null;
  current_content_hash: string | null;
  file_exists_on_disk: boolean;
  fields_changed: string[];
}

export interface ReconstructedMethodology {
  yaml_path: string;
  version_id: number;
  yaml_content_hash: string;
  tool_config_round_trip_ok: boolean;
  tool_config_hash: string | null;
}

export interface WorkspaceReplay {
  workspace_id: string;
  slug: string;
  dag_hash: string;
  mode: 'original' | 'current';
  produced_under_commits: string[];
  current_commit: string | null;
  commit_differs: boolean;
  node_artifact_hashes: string[];
  methodology_version_ids: number[];
  methodology_diffs: MethodologyDiff[];
  reconstructed: ReconstructedMethodology[];
  notes: string[];
}

export interface ArtifactReplay {
  artifact_hash: string;
  mode: 'original' | 'current';
  produced_under_commit: string | null;
  current_commit: string | null;
  commit_differs: boolean;
  methodology_version_ids: number[];
  methodology_diffs: MethodologyDiff[];
  reconstructed: ReconstructedMethodology[];
  notes: string[];
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export async function createWorkspace(args: {
  dag_hash: string;
  name?: string | null;
  created_by?: string | null;
  focus_node?: string | null;
}): Promise<CreateWorkspaceResponse> {
  return fetchJSON<CreateWorkspaceResponse>(`${PREFIX}/workspace`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      dag_hash: args.dag_hash,
      name: args.name ?? null,
      created_by: args.created_by ?? null,
      focus_node: args.focus_node ?? null,
    }),
  });
}

export async function getWorkspace(slug: string): Promise<WorkspaceDetail> {
  return fetchJSON<WorkspaceDetail>(
    `${PREFIX}/workspace/${encodeURIComponent(slug)}`,
  );
}

/** List workspaces for the Build sidebar.
 *
 *  All parameters are optional.  ``limit`` is clamped to ``[1, 200]``
 *  server-side; oversized client requests are silently capped rather
 *  than rejected so the sidebar never bombs on a bad config.
 *
 *  PR B adds ``parent_workspace_id`` — when provided, returns only
 *  workspaces forked from that parent (powers VariantStrip). */
export async function listWorkspaces(args?: {
  limit?: number;
  offset?: number;
  filter?: WorkspaceListFilter;
  parent_workspace_id?: string;
}): Promise<WorkspaceListResponse> {
  const params = new URLSearchParams();
  if (args?.limit != null) params.set('limit', String(args.limit));
  if (args?.offset != null) params.set('offset', String(args.offset));
  if (args?.filter) params.set('filter', args.filter);
  if (args?.parent_workspace_id)
    params.set('parent_workspace_id', args.parent_workspace_id);
  const qs = params.toString();
  return fetchJSON<WorkspaceListResponse>(
    `${PREFIX}/workspace${qs ? `?${qs}` : ''}`,
  );
}

// ----------------------------------------------------------------------------
// Fork-with-overrides surface (PR B)
// ----------------------------------------------------------------------------

export interface ForkWorkspaceRequest {
  /** Top-level scalar replacements on the parent's
   *  ``bound_slot_values``.  Most common case: changing a single
   *  scalar slot like ``signal_threshold``. */
  slot_overrides?: Record<string, unknown>;
  /** Per-key merges into nested dict slots.  The dict is shallow-
   *  merged into the parent's slot value, preserving other keys.
   *  Use this when the user changes one field inside a nested-
   *  params dict (e.g. ``signal_params.window_days``). */
  slot_dict_overrides?: Record<string, Record<string, unknown>>;
  name?: string | null;
  created_by?: string | null;
}

export interface ForkWorkspaceResponse {
  workspace_id: string;
  slug: string;
  name: string | null;
  dag_hash: string;
  parent_workspace_id: string;
  url: string;
  override_summary: {
    template_id: string;
    changed: string[];
    new_slot_values: Record<string, unknown>;
    parent_slot_values: Record<string, unknown>;
  };
}

/** Fork a workspace by patching its bound_slot_values and re-running
 *  the same template.  The new workspace links back to the parent
 *  via ``parent_workspace_id``; both stay independently URL-
 *  addressable.
 *
 *  Throws on any 4xx / 5xx with the server's diagnostic message —
 *  the BuildShell catches and surfaces to the UI. */
export async function forkWorkspace(
  slug: string,
  body: ForkWorkspaceRequest,
): Promise<ForkWorkspaceResponse> {
  return fetchJSON<ForkWorkspaceResponse>(
    `${PREFIX}/workspace/${encodeURIComponent(slug)}/fork`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
}

export async function replayWorkspace(
  slug: string,
  mode: 'original' | 'current' = 'original',
): Promise<WorkspaceReplay> {
  return fetchJSON<WorkspaceReplay>(
    `${PREFIX}/workspace/${encodeURIComponent(slug)}/replay?mode=${mode}`,
  );
}

// ---------------------------------------------------------------------------
// Artifact-keyed replay — relocated from PR 9's
// /api/v1/workspace/{hash} URL.  Use this when the caller has an
// artifact hash but no workspace context.
// ---------------------------------------------------------------------------

export async function replayArtifact(
  artifactHash: string,
  mode: 'original' | 'current' = 'original',
): Promise<ArtifactReplay> {
  return fetchJSON<ArtifactReplay>(
    `${PREFIX}/artifacts/${encodeURIComponent(artifactHash)}/replay?mode=${mode}`,
  );
}

// ---------------------------------------------------------------------------
// Artifact payload — PR3
// ---------------------------------------------------------------------------
//
// ``GET /api/v1/artifacts/{artifact_hash}/payload`` returns the full
// deserialised ``StoredArtifact`` for a persisted artifact (the same
// shape ``state.artifact_store.get_artifact_payload_dict`` returns).
// Used by Build widgets to render a workspace node without re-running
// its primitive.  Backend errors:
//
//   - 400 — ``artifact_hash`` is not a 64-char hex SHA-256.
//   - 404 — no metadata row, OR the metadata row points at a missing
//           object-storage blob (orphaned).
//   - 503 — object-storage backend not initialised.
//   - 500 — corrupt row in storage.
//
// ``ArtifactPayloadError`` exposes the ``status`` so widget-side error
// handlers can react differently (e.g. 404 → "this snapshot is gone"
// vs 503 → "storage down, retry later") without parsing the raw
// fetch error string.

/** Status codes the backend uses for the payload endpoint.  Matches
 *  the FastAPI HTTPException statuses in
 *  ``api/routes/artifacts.py::artifact_payload``.  ``-1`` is reserved
 *  for transport-level failures (network, CORS, abort). */
export type ArtifactPayloadErrorStatus = 400 | 404 | 500 | 503 | -1;

/** Error class thrown by ``getArtifactPayload`` for any non-2xx
 *  response.  Carries the HTTP status so callers can branch without
 *  re-fetching or string-matching the network error message. */
export class ArtifactPayloadError extends Error {
  readonly status: ArtifactPayloadErrorStatus;
  readonly artifactHash: string;

  constructor(args: {
    status: ArtifactPayloadErrorStatus;
    artifactHash: string;
    message: string;
  }) {
    super(args.message);
    this.name = 'ArtifactPayloadError';
    this.status = args.status;
    this.artifactHash = args.artifactHash;
  }
}

/** Pre-flight sanity check on the hash format — matches the backend's
 *  ``len == 64 && is_hex`` guard so we fail fast on malformed inputs
 *  without burning a round-trip.  Exposed so the hook can short-
 *  circuit when the caller passes a placeholder. */
export function isLikelyArtifactHash(hash: string): boolean {
  if (typeof hash !== 'string' || hash.length !== 64) return false;
  return /^[0-9a-fA-F]{64}$/.test(hash);
}

/** Fetch a persisted artifact's full deserialised contents by hash.
 *
 *  Throws an ``ArtifactPayloadError`` (with ``status``) on any non-2xx
 *  response; throws a plain ``Error`` for transport failures.  Returns
 *  the typed ``ArtifactPayloadResponse`` envelope on success.
 *
 *  Defensive: validates the JSON body structurally before returning so
 *  a corrupt 200 response doesn't silently break the discriminated-
 *  union narrowing downstream.
 *
 *  Accepts an optional ``AbortSignal`` so hook callers can cancel
 *  in-flight requests on unmount. */
export async function getArtifactPayload(
  artifactHash: string,
  init?: { signal?: AbortSignal },
): Promise<ArtifactPayloadResponse> {
  if (!isLikelyArtifactHash(artifactHash)) {
    throw new ArtifactPayloadError({
      status: 400,
      artifactHash,
      message:
        'artifact_hash must be a 64-char hex SHA-256 digest (client-side check)',
    });
  }

  let res: Response;
  try {
    res = await fetch(
      `${PREFIX}/artifacts/${encodeURIComponent(artifactHash)}/payload`,
      { signal: init?.signal },
    );
  } catch (err) {
    // Transport-level failure (network, CORS, abort).  Re-throw as a
    // typed error with status=-1 so callers don't need to string-
    // match the raw browser fetch error.
    if ((err as { name?: string })?.name === 'AbortError') {
      throw err; // Let the abort propagate untransformed.
    }
    throw new ArtifactPayloadError({
      status: -1,
      artifactHash,
      message:
        err instanceof Error ? err.message : 'Network error fetching artifact',
    });
  }

  if (!res.ok) {
    // Status mapping mirrors the backend's HTTPException codes.
    const body = await res.text().catch(() => '');
    const status = (res.status === 400 ||
    res.status === 404 ||
    res.status === 500 ||
    res.status === 503
      ? res.status
      : -1) as ArtifactPayloadErrorStatus;
    throw new ArtifactPayloadError({
      status,
      artifactHash,
      message: `API ${res.status}: ${res.statusText}${body ? ` — ${body}` : ''}`,
    });
  }

  let parsed: unknown;
  try {
    parsed = await res.json();
  } catch (err) {
    throw new ArtifactPayloadError({
      status: 500,
      artifactHash,
      message: `Artifact payload was not valid JSON: ${
        err instanceof Error ? err.message : String(err)
      }`,
    });
  }

  if (!isArtifactPayloadResponseShape(parsed)) {
    throw new ArtifactPayloadError({
      status: 500,
      artifactHash,
      message:
        'Artifact payload response is structurally invalid (missing artifact_type / metadata / payload).',
    });
  }

  return parsed;
}
