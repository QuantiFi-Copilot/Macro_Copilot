// ============================================================================
// workspaceScopedContext.ts — augment user messages with workspace context.
// ----------------------------------------------------------------------------
// The chat WebSocket protocol takes a plain string (``user_message
// .content``).  PR B's workspace-scoped composer prepends a small
// natural-language context preamble so the LLM understands which
// workspace the user is talking about:
//
//   "[Workspace: <name> · template <template_id> · stages <comma-
//    joined stage titles> · 4 nodes]\n\n<the user's prompt>"
//
// We use prose rather than structured metadata because the existing
// orchestrator (workflow_router + supervisor) understands natural
// language exclusively — adding a structured-metadata channel would
// require a protocol change.  PR C may upgrade to a structured
// envelope; this prose preamble works today.
//
// The preamble is cheap (~30-60 tokens) and PR-A-onwards prompt
// caching makes its cost negligible.
// ============================================================================

import type { WorkspaceDetail } from '@/services/workspaceApi';
import { topologicalOrder } from '@/components/build/lib/topologicalOrder';
import { prettyStageTitle } from '@/components/build/lib/stageDisplay';

/** Build the preamble line.  Returns empty string when the workspace
 *  carries no useful identity (legacy rows with no template_id). */
export function buildWorkspacePreamble(
  detail: WorkspaceDetail | null,
): string {
  if (!detail) return '';
  const title = detail.name?.trim() || `/${detail.slug}`;
  const ordered = topologicalOrder(detail.nodes, detail.edges);
  const stageNames = ordered
    .map((n) => prettyStageTitle(n.name ?? n.node_id))
    .slice(0, 6);
  const stageList = stageNames.length === 0 ? '' : `stages ${stageNames.join(', ')}`;
  const tmpl = detail.template_id ? `template ${detail.template_id}` : '';
  const nodeCount = `${detail.nodes.length} nodes`;

  const parts = [
    `Workspace: ${title}`,
    tmpl,
    stageList,
    nodeCount,
  ].filter(Boolean);

  return `[${parts.join(' · ')}]`;
}

/** Compose the final outgoing message: preamble + blank line + user
 *  prompt.  When the preamble is empty (no detail), returns the
 *  raw prompt unchanged. */
export function composeScopedMessage(
  detail: WorkspaceDetail | null,
  prompt: string,
): string {
  const preamble = buildWorkspacePreamble(detail);
  if (!preamble) return prompt;
  return `${preamble}\n\n${prompt}`;
}
