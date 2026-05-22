// ============================================================================
// WorkflowStatusCanvas — explicit state for workflow handoffs without a slug.
// ----------------------------------------------------------------------------
// PR1 — Build Routing Coverage.  Mounts when ``BuildShell`` reads
// ``/workspace?workflow=<template_id>&workflow_status=<kind>``.  This
// URL is emitted by ``ActionRow.resolveBuildHref`` when an Ask turn
// triggered a recognised workflow template but the persisted
// workspace slug never landed (paused template, persistence failed,
// runner timeout, etc.).  Instead of dropping the user on the empty
// Build shell, we surface an honest status card.
//
// Statuses (matches ``classifyWorkflow`` in ``lib/toolNames.ts``):
//   - ``known``     — recognised template; the workspace slug should
//     normally appear.  When we land here with this status, the most
//     likely cause is a transient persistence failure on the run.
//   - ``paused``    — declared in code but gated off the LLM surface
//     (backtest, today).  Always lands here.
//   - ``unknown``   — template_id we don't recognise; render the same
//     decode-style card so the user knows the link is stale.
//
// The card is intentionally short — it names the status, explains why
// the user landed here, and offers two CTAs (back to Ask + open the
// workspaces sidebar).  No retry button because the supervisor owns
// the run lifecycle; the user re-runs through Ask.
// ============================================================================

import { Construction, AlertCircle, ArrowLeft } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { classifyWorkflow } from '@/lib/toolNames';

type Props = {
  templateId: string;
  /** Optional status hint forwarded by ``ActionRow.resolveBuildHref``.
   *  When absent, we re-classify from ``templateId`` so direct URL
   *  pastes / bookmarks still render the right state. */
  status: string | null;
};

interface CardCopy {
  kind: 'known_no_slug' | 'paused' | 'unknown';
  badge: string;
  title: string;
  reason: string;
  whatNext: string;
  tone: 'amber' | 'coral';
}

function classify(templateId: string, hint: string | null): CardCopy {
  // Hint can be ``known | paused | unknown`` from the URL param OR
  // empty/null on direct paste.  Re-classify regardless so a stale
  // hint never overrides the real registry state.
  const status = classifyWorkflow(templateId);
  if (status.kind === 'paused') {
    return {
      kind: 'paused',
      badge: 'workflow · paused',
      title: 'This workflow is paused',
      reason:
        "The backend declares this template but it's intentionally gated off the LLM surface (data prerequisites or design rework in progress).",
      whatNext:
        'Back-test rendering is the example today — it ships when DV01, OTR, true O/N OIS, and TIPS carry are wired in.',
      tone: 'amber',
    };
  }
  if (status.kind === 'unknown') {
    return {
      kind: 'unknown',
      badge: 'workflow · unknown',
      title: 'Workflow template not recognised',
      reason: `The handoff carries template_id="${templateId}" which Build doesn't know about.`,
      whatNext:
        'The link may be stale (template renamed) or for a workflow that isn\'t shipped yet.  Re-run the original Ask query.',
      tone: 'coral',
    };
  }
  // status.kind === 'known' but we still landed here because the slug
  // was missing.  Most likely transient persistence failure.  Hint
  // is informational only.
  void hint;
  return {
    kind: 'known_no_slug',
    badge: 'workflow · result unavailable',
    title: 'Workflow ran, but no workspace was persisted',
    reason:
      "The supervisor matched the template and ran it, but the persisted workspace slug didn't land on this turn — usually a transient runner / persistence error.",
    whatNext:
      'Re-run the original Ask query.  When persistence succeeds the workspace will appear in the left sidebar.',
    tone: 'amber',
  };
}

export function WorkflowStatusCanvas({ templateId, status }: Props) {
  const navigate = useNavigate();
  const copy = classify(templateId, status);
  const Icon = copy.tone === 'coral' ? AlertCircle : Construction;

  return (
    <div className="flex h-full min-h-0 items-center justify-center px-6 py-10">
      <div className="card flex max-w-[560px] flex-col gap-5 px-6 py-5">
        <header className="flex items-start gap-3">
          <span
            aria-hidden
            className={
              copy.tone === 'coral'
                ? 'mt-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-coral-400/30 bg-coral-500/10 text-coral-300'
                : 'mt-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-amber-400/30 bg-amber-500/10 text-amber-300'
            }
          >
            <Icon size={16} strokeWidth={1.75} />
          </span>
          <div className="min-w-0">
            <span
              className={
                copy.tone === 'coral'
                  ? 'rounded-sm border border-coral-400/35 bg-coral-500/10 px-1.5 py-0.5 font-mono text-[9.5px] font-semibold uppercase tracking-[0.18em] text-coral-200'
                  : 'rounded-sm border border-amber-400/35 bg-amber-500/10 px-1.5 py-0.5 font-mono text-[9.5px] font-semibold uppercase tracking-[0.18em] text-amber-200'
              }
            >
              {copy.badge}
            </span>
            <h2 className="mt-1.5 text-[15.5px] font-semibold tracking-[-0.012em] text-fg-primary">
              {copy.title}
            </h2>
            <div className="mt-0.5 font-mono text-[10.5px] text-fg-muted">
              {templateId}
            </div>
          </div>
        </header>

        <section className="flex flex-col gap-2">
          <p className="kicker text-fg-muted">Why this card</p>
          <p className="text-[12.5px] leading-[1.55] text-fg-secondary">
            {copy.reason}
          </p>
        </section>

        <section className="flex flex-col gap-2">
          <p className="kicker text-fg-muted">What to do next</p>
          <p className="text-[12.5px] leading-[1.55] text-fg-secondary">
            {copy.whatNext}
          </p>
        </section>

        <div className="mt-1 flex flex-wrap items-center gap-2 border-t border-line-subtle pt-4">
          <button
            type="button"
            onClick={() => navigate('/ask')}
            className="flex items-center gap-1.5 rounded-md border border-line-soft bg-white/[0.025] px-3 py-1.5 text-[11.5px] font-medium text-fg-secondary transition-colors hover:border-ice-400/35 hover:text-ice-200"
          >
            <ArrowLeft size={11} strokeWidth={1.75} />
            <span>Back to Ask</span>
          </button>
        </div>
      </div>
    </div>
  );
}
