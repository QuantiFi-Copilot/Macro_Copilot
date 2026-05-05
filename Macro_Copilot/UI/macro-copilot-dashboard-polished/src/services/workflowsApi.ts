// ============================================================================
// Workflows + Tools API client
// ----------------------------------------------------------------------------
// Thin fetch wrappers against the /api/v1/workflows + /api/v1/tools FastAPI
// surface (api/routes/workflows/*).  Mirrors the discipline of
// `services/ratesApi.ts` so callers consume one cohesive API style.
// ============================================================================

import type {
  ToolCard,
  ToolCardEnvelope,
  ToolCatalogueResponse,
  WorkflowCardEnvelope,
  WorkflowCatalogueResponse,
  WorkflowExecuteEnvelope,
  WorkflowTemplateCard,
} from '@/types/workflows';

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
// Workflows
// ---------------------------------------------------------------------------

export async function fetchWorkflows(): Promise<WorkflowTemplateCard[]> {
  const res = await fetchJSON<WorkflowCatalogueResponse>(`${PREFIX}/workflows`);
  return res.workflows;
}

export async function fetchWorkflowCard(
  templateId: string,
): Promise<WorkflowTemplateCard | null> {
  const res = await fetchJSON<WorkflowCardEnvelope>(
    `${PREFIX}/workflows/${encodeURIComponent(templateId)}`,
  );
  return res.ok ? res.card : null;
}

export async function runWorkflow(
  templateId: string,
  slotValues: Record<string, unknown>,
): Promise<WorkflowExecuteEnvelope> {
  return fetchJSON<WorkflowExecuteEnvelope>(
    `${PREFIX}/workflows/${encodeURIComponent(templateId)}/run`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slot_values: slotValues }),
    },
  );
}

// ---------------------------------------------------------------------------
// Tools (primitive catalogue)
// ---------------------------------------------------------------------------

export async function fetchTools(): Promise<ToolCard[]> {
  const res = await fetchJSON<ToolCatalogueResponse>(`${PREFIX}/tools`);
  return res.tools;
}

export async function fetchToolCard(toolName: string): Promise<ToolCard | null> {
  const res = await fetchJSON<ToolCardEnvelope>(
    `${PREFIX}/tools/${encodeURIComponent(toolName)}`,
  );
  return res.ok ? res.card : null;
}
