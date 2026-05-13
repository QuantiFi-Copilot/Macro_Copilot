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
      {/* "Show in Build" is the primary CTA on every assistant card
          when a workflow / workspace context is available — PR C
          promotes it from a ghost button to an outlined primary so the
          handoff from Ask → Build is unmistakable. */}
      <ActionButton
        icon={<ArrowUpRight size={12} />}
        label="Show in Build"
        disabled={!buildHref}
        primary={!!buildHref}
        onClick={() => buildHref && navigate(buildHref)}
        title={
          buildHref
            ? 'Open this analysis in the Build workspace'
            : 'No workspace context for this turn'
        }
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
  primary,
}: {
  icon: React.ReactNode;
  label: string;
  onClick?: () => void;
  disabled?: boolean;
  title?: string;
  /** Render with a visible outline + accent fill so the action reads
   *  as the primary CTA in the row.  Used for "Show in Build" when a
   *  workspace context is available. */
  primary?: boolean;
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
          : primary
            ? 'border border-ice-400/35 bg-ice-500/[0.06] text-ice-100 shadow-[0_0_0_1px_rgba(146,178,232,0.04)] hover:border-ice-400/55 hover:bg-ice-500/[0.10] hover:text-ice-50'
            : 'text-fg-secondary hover:bg-white/[0.025] hover:text-ice-200',
      )}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

function resolveBuildHref(message: CopilotMessage): string | null {
  // Workflow turns: route to the workspace's workflow context with the
  // template_id and the bound slot_values so Build opens with the same
  // graph the user just saw.  For V1 we degrade to /workspace because
  // the workflow-aware workspace surface ships in a later PR.
  if (message.workflow?.routeDecision.template_id) {
    return '/workspace';
  }
  // Supervisor turns: same path the legacy WorkspaceButton uses —
  // ?context=<encoded JSON>.
  if (message.workspaceContext) {
    const encoded = encodeURIComponent(JSON.stringify(message.workspaceContext));
    return `/workspace?context=${encoded}`;
  }
  return null;
}
