// ============================================================================
// Workspace API client
// ----------------------------------------------------------------------------
// Thin fetch wrappers against /api/v1/workspace + /api/v1/artifacts.
// Mirrors the discipline of services/ratesApi.ts + workflowsApi.ts.
//
// Phase 0 PR 10 — workspace persistence + URL routing.  Workspaces are
// identified by their slug in the URL; renames update the display name
// only, the slug (and therefore the URL) is stable.
// ============================================================================

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
 *  than rejected so the sidebar never bombs on a bad config. */
export async function listWorkspaces(args?: {
  limit?: number;
  offset?: number;
  filter?: WorkspaceListFilter;
}): Promise<WorkspaceListResponse> {
  const params = new URLSearchParams();
  if (args?.limit != null) params.set('limit', String(args.limit));
  if (args?.offset != null) params.set('offset', String(args.offset));
  if (args?.filter) params.set('filter', args.filter);
  const qs = params.toString();
  return fetchJSON<WorkspaceListResponse>(
    `${PREFIX}/workspace${qs ? `?${qs}` : ''}`,
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
