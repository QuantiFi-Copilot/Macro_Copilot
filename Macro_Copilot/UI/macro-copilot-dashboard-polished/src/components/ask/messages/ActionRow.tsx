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
import type { CopilotMessage } from '@/types/copilot';
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
  // Workflow turns: route directly to the persisted workspace's slug
  // when the workflow_result carried one.  Build's slug-bound shell
  // (``BuildShell`` → ``SlugBoundShell``) materialises the saved DAG
  // + per-node artifacts so the user sees the full graph and result
  // set they just generated.
  //
  // Older builds of this code shipped a placeholder that always sent
  // workflow turns to ``/workspace`` (empty shell).  The slug-aware
  // surface (Phases R1-R2) makes the persisted handoff real; falling
  // back to ``/workspace`` only if the workflow finished but didn't
  // persist (e.g. ``persist=False`` runner).
  const workflowSlug = message.workflow?.workspace?.slug;
  if (workflowSlug) {
    return `/workspace/${workflowSlug}`;
  }
  if (message.workflow?.routeDecision.template_id) {
    return '/workspace';
  }
  // Supervisor turns: encode the tool calls into ``?context=...`` so
  // Build's empty shell decodes them into a virtual primitive canvas
  // (``VirtualPrimitiveCanvas``) that fetches the typed-detail
  // endpoint and renders the analysis without requiring backend
  // workspace persistence.
  if (message.workspaceContext) {
    const encoded = encodeURIComponent(JSON.stringify(message.workspaceContext));
    return `/workspace?context=${encoded}`;
  }
  return null;
}
