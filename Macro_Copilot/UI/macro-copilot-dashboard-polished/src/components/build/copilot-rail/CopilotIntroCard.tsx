// ============================================================================
// CopilotIntroCard — the static intro shown in the workspace copilot rail.
// ----------------------------------------------------------------------------
// Two modes:
//   - ``empty``     — Build canvas has no workspace open.  Tells the
//                     user what kinds of prompts the copilot accepts
//                     and surfaces a few starter chips.
//   - ``completed`` — A workspace is open.  Tells the user how to
//                     interrogate this specific workspace (PR B
//                     enables this for real; PR A leaves the
//                     starter chips visible but disabled).
//
// The card uses the same ``research-card`` surface treatment as
// Monitor's widgets so the right rail reads consistently with the
// main canvas.
// ============================================================================

import { Sparkles } from 'lucide-react';

type Mode = 'empty' | 'completed';

type Props = {
  mode: Mode;
  /** The workspace title — only used in ``completed`` mode. */
  workspaceTitle?: string;
};

export function CopilotIntroCard({ mode, workspaceTitle }: Props) {
  const headline =
    mode === 'empty'
      ? 'Welcome to Build.'
      : workspaceTitle
        ? `Workspace: ${workspaceTitle}`
        : 'Workspace open.';

  return (
    <article className="research-card relative flex flex-col gap-3 px-4 py-4">
      <span aria-hidden className="research-card-rail" />

      <div className="flex items-center gap-2">
        <Sparkles size={12} strokeWidth={1.75} className="text-ice-300" />
        <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-fg-muted">
          Workspace copilot
        </span>
      </div>

      <h3 className="font-serif-display text-[20px] font-light leading-[1.15] tracking-[-0.012em] text-fg-primary">
        {headline}
      </h3>

      {mode === 'empty' ? <EmptyBody /> : <CompletedBody />}
    </article>
  );
}

function EmptyBody() {
  return (
    <>
      <p className="text-[11.5px] leading-[1.55] text-fg-secondary">
        I'll help you construct, extend, and interrogate analyses.
        Drop a prompt into the composer in the centre column or pick
        one of the category tiles to start.
      </p>
      <ul className="mt-1 flex flex-col gap-1 text-[11px] leading-[1.45] text-fg-muted">
        <li>· Every analysis is a DAG of primitives + operators.</li>
        <li>· All data and results are versioned and traceable.</li>
        <li>· Save workspaces to revisit, share, or build on later.</li>
      </ul>
    </>
  );
}

function CompletedBody() {
  return (
    <>
      <p className="text-[11.5px] leading-[1.55] text-fg-secondary">
        This workspace's DAG and per-node results are on the canvas.
        In PR B you'll be able to chat with this analysis — override
        a stage's parameters, fork a variant, or ask for plain-
        English explanation of any node.
      </p>
      <p className="text-[10.5px] leading-[1.45] text-fg-faint">
        Composer below is intentionally read-only in PR A so the
        interactive surface ships as a single reviewable change.
      </p>
    </>
  );
}
