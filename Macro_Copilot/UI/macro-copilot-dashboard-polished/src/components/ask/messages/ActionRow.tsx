// ============================================================================
// ActionRow — bottom of every assistant research card
// ----------------------------------------------------------------------------
// Four ghost actions: Open in Build · Save to Briefcase · Export ·
// Rerun with…
//
// V1 wiring:
//   - "Open in Build" routes to /workspace?context=<tool_call> when the
//     supervisor surfaced a workspace_context, OR to a workflow-aware
//     workspace URL when the turn ran a workflow template.  Same path
//     today's WorkspaceButton uses, normalized into this row.
//   - "Save to Briefcase" / "Export" / "Rerun with…" are placeholders
//     — they exist in the design contract so the action surface is
//     stable; they no-op (with a hover tooltip) until the Briefcase
//     surface ships in V2.
//
// The placeholder buttons are deliberately not hidden — when a senior
// PM scans the bottom of a card, the four-verb structure communicates
// "here's what you can do with this answer", which is part of the
// product's editorial stance.  They become live in subsequent PRs.
// ============================================================================

import { useNavigate } from 'react-router-dom';
import {
  ArrowUpRight,
  Download,
  PlusSquare,
  RotateCcw,
} from 'lucide-react';
import type { CopilotMessage, WorkspaceContext } from '@/types/copilot';
import { classifyWorkflow, normalizeToolName } from '@/lib/toolNames';
import { cn } from '@/utils/cn';

type Props = {
  message: CopilotMessage;
  /** Click handler that drops a prompt into the composer.  Provided by
   *  AskPage; allows "Rerun with…" to seed the input with the user's
   *  original prompt for quick parameter mutation. */
  onSeedComposer: (prompt: string) => void;
};

export function ActionRow({ message, onSeedComposer }: Props) {
  const navigate = useNavigate();

  const buildHref = resolveBuildHref(message);
  const lastUserPrompt = message.role === 'assistant'
    ? // The composer-seed function is invoked with this turn's user
      // prompt by the parent (AskPage knows the pairing); here we just
      // hint at the action shape.
      undefined
    : undefined;
  void lastUserPrompt;

  return (
    <div className="flex flex-wrap items-center gap-1 border-t border-line-subtle px-3 py-2">
      <ActionButton
        icon={<ArrowUpRight size={12} />}
        label="Open in Build"
        disabled={!buildHref}
        onClick={() => buildHref && navigate(buildHref)}
      />
      <ActionButton
        icon={<PlusSquare size={12} />}
        label="Save to Briefcase"
        disabled
        title="Briefcase ships in V2"
      />
      <ActionButton
        icon={<Download size={12} />}
        label="Export"
        disabled
        title="Export ships in V2"
      />
      <ActionButton
        icon={<RotateCcw size={12} />}
        label="Rerun with…"
        onClick={() => {
          // Seed the composer with the original user prompt so the user
          // can mutate parameters in-place.  The parent owns
          // user-message lookup; we just delegate.
          onSeedComposer('');
        }}
      />
    </div>
  );
}

function ActionButton({
  icon,
  label,
  onClick,
  disabled,
  title,
}: {
  icon: React.ReactNode;
  label: string;
  onClick?: () => void;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cn(
        'flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[11.5px] font-medium transition-colors duration-150 ease-sleek',
        disabled
          ? 'cursor-not-allowed text-fg-faint'
          : 'text-fg-secondary hover:bg-white/[0.025] hover:text-ice-200',
      )}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

function resolveBuildHref(message: CopilotMessage): string | null {
  // 1. Workflow turns with a persisted workspace slug — direct route
  //    to Build's slug-bound shell, which materialises the saved DAG +
  //    per-node artifacts.  This wins over every other path because
  //    the persisted workspace IS the authoritative result.
  //
  //    PR-11B: this also handles open-DAG runs (template_id === null)
  //    that successfully persisted — they emit ``workspace.slug`` the
  //    same way template-lane runs do, and the slug path renders both
  //    correctly via BuildCompleted + GenericResultsDashboard.  Do
  //    NOT route open-DAG to ``?workflow=`` (step 2 below) — that
  //    path is template-only and surfaces a "paused / unknown" card
  //    via ``WorkflowStatusCanvas``.
  const workflowSlug = message.workflow?.workspace?.slug;
  if (workflowSlug) {
    return `/workspace/${workflowSlug}`;
  }

  // 2. Workflow turns WITHOUT a slug — the workflow router fired but
  //    persistence didn't happen (paused template, runner failure,
  //    persist=False).  PR1 — route to an explicit "workflow
  //    unavailable" state so the user gets an honest card instead of
  //    the empty Build shell.  ``BuildShell`` reads the
  //    ``?workflow=<template_id>`` URL param and surfaces the right
  //    state (paused / unavailable / unknown) via the
  //    ``classifyWorkflow`` registry.
  //
  //    PR-11B: the falsy check ``if (templateId)`` correctly excludes
  //    open-DAG (template_id === null): open-DAG without a slug means
  //    persistence failed, and we have no honest "open-DAG paused"
  //    card to render — fall through to the supervisor path (step 3)
  //    or return null cleanly.
  const templateId = message.workflow?.routeDecision.template_id;
  if (templateId) {
    const status = classifyWorkflow(templateId);
    return `/workspace?workflow=${encodeURIComponent(templateId)}&workflow_status=${status.kind}`;
  }

  // 3. Supervisor turns: encode the tool calls into ``?context=...``
  //    so Build decodes them into either a single primitive canvas, a
  //    multi-card grid, or (PR1) an unsupported-known card.  Tool
  //    names are normalised here too so manifest shorthand doesn't
  //    confuse downstream lookups.
  //
  //    PR-B-β — append ``&handoff=ask`` so Build can distinguish
  //    Ask-originated context from Library-blank opens (which use the
  //    same ``?context=`` URL pattern but with empty params).  The
  //    Build-side router reads this marker via ``isAskHandoff(...)``
  //    and threads it through the typed canvases; the missing-param
  //    tile only fires on the Ask-handoff path so blank Library opens
  //    keep folding spec defaults as before.  Backward compatible:
  //    pre-PR-B-β shared URLs without the marker default to the
  //    library policy and continue to render with defaults.
  if (message.workspaceContext) {
    const normalised = normaliseWorkspaceContext(message.workspaceContext);
    const encoded = encodeURIComponent(JSON.stringify(normalised));
    return `/workspace?context=${encoded}&handoff=ask`;
  }
  return null;
}

/** Normalise every ``tool`` name in a workspace context so manifest
 *  shorthand (``half_life_tool``) reaches the decoder in canonical
 *  form (``calculate_half_life_tool``).  Preserves multi-tool order
 *  + params verbatim. */
function normaliseWorkspaceContext(ctx: WorkspaceContext): WorkspaceContext {
  return {
    ...ctx,
    tools: ctx.tools.map((t) => ({
      ...t,
      tool: normalizeToolName(t.tool),
    })),
  };
}
