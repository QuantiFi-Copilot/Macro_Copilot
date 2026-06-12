// ============================================================================
// AuditHeader — "what I understood / checked" strip on the DAG tab.
// ----------------------------------------------------------------------------
// Phase D / D9 (orchestration upgrade): renders the persisted
// ``WorkspaceDetail.run_audit`` sidecar — the open-DAG pipeline's
// intent chain captured at persist time:
//
//   collapsed (default) — ONE slim row: expected-shape chip(s) + gate
//     verdict chip + intent tag + a "what I understood" toggle.  The
//     default view stays clean per the Phase-D ruling; the audit
//     lives behind the toggle.
//   expanded — the L1 decomposition (name + domain hint per economic
//     quantity), the composer's wiring rationale, and the gate's
//     reason — the run's own account of what it understood and
//     checked BEFORE executing.
//
// Renders nothing when the workspace has no audit (template lane /
// direct-fetch persistence / pre-audit rows) — never an error state.
// Finance-blind: every string comes from the persisted chain (the
// LLM-authored / deterministic records), never from this component.
// ============================================================================

import { useState } from 'react';
import { ChevronDown, ChevronRight, ShieldCheck } from 'lucide-react';
import type { WorkspaceRunAudit } from '@/services/workspaceApi';
import { cn } from '@/utils/cn';

type Props = {
  audit: WorkspaceRunAudit | null | undefined;
};

export function AuditHeader({ audit }: Props) {
  const [expanded, setExpanded] = useState(false);

  const chain = audit?.intent_chain;
  if (!audit || !chain) return null;

  const shapes = normaliseShapes(audit.expected_answer_shape);
  const gateStatus = chain.gate?.status ?? null;
  const intentTag = chain.router?.intent_tag ?? null;
  const decomposition = chain.router?.decomposition ?? [];
  const selfCorrected = (audit.recompose_trace?.length ?? 0) > 0;

  return (
    <section className="shrink-0 border-b border-line-subtle px-4 py-2">
      {/* ---------- Collapsed row ---------- */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[11px] font-medium text-fg-secondary transition-colors hover:bg-white/[0.04] hover:text-fg-primary"
          aria-expanded={expanded}
        >
          {expanded ? (
            <ChevronDown size={12} aria-hidden />
          ) : (
            <ChevronRight size={12} aria-hidden />
          )}
          What I understood
        </button>

        {shapes.map((s) => (
          <span
            key={s}
            className="rounded-full border border-ice-400/25 bg-ice-400/[0.06] px-2 py-px text-[9.5px] uppercase tracking-[0.1em] text-ice-300"
            title="The answer shape the router declared BEFORE composing — the deterministic terminal-shape check held the DAG to it."
          >
            expected: {s}
          </span>
        ))}

        {gateStatus && (
          <span
            className={cn(
              'flex items-center gap-1 rounded-full border px-2 py-px text-[9.5px] uppercase tracking-[0.1em]',
              gateStatus === 'PASS'
                ? 'border-mint-400/25 bg-mint-400/[0.06] text-mint-300'
                : 'border-amber-400/25 bg-amber-400/[0.06] text-amber-300',
            )}
            title={chain.gate?.reason ?? undefined}
          >
            <ShieldCheck size={10} aria-hidden />
            gate {gateStatus.toLowerCase()}
          </span>
        )}

        {selfCorrected && (
          <span
            className="rounded-full border border-amber-400/25 bg-amber-400/[0.06] px-2 py-px text-[9.5px] uppercase tracking-[0.1em] text-amber-300"
            title="The first composition failed a deterministic check and was re-composed before executing — full trace on the Notes tab."
          >
            self-corrected
          </span>
        )}

        {intentTag && (
          <span className="mono ml-auto text-[10px] text-fg-faint">
            intent: {intentTag}
          </span>
        )}
      </div>

      {/* ---------- Expanded detail ---------- */}
      {expanded && (
        <div className="mt-2 grid gap-3 rounded-md border border-line-subtle bg-white/[0.012] px-3 py-2.5 md:grid-cols-2">
          <div>
            <p className="kicker mb-1 text-fg-muted">Decomposed</p>
            {decomposition.length === 0 ? (
              <p className="text-[11px] text-fg-faint">
                (No decomposition recorded.)
              </p>
            ) : (
              <ul className="space-y-1">
                {decomposition.map((q, i) => (
                  <li
                    key={q.name ?? i}
                    className="text-[11.5px] leading-snug text-fg-secondary"
                  >
                    <span className="mono text-fg-primary">{q.name}</span>
                    {q.domain_hint && (
                      <span className="text-fg-faint"> · {q.domain_hint}</span>
                    )}
                    {q.nl_description && (
                      <span className="block text-[10.5px] text-fg-muted">
                        {q.nl_description}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="space-y-2">
            {chain.composer?.rationale && (
              <div>
                <p className="kicker mb-1 text-fg-muted">Wired</p>
                <p className="text-[11.5px] leading-snug text-fg-secondary">
                  {chain.composer.rationale}
                </p>
              </div>
            )}
            {chain.gate?.reason && (
              <div>
                <p className="kicker mb-1 text-fg-muted">Checked</p>
                <p className="text-[11.5px] leading-snug text-fg-secondary">
                  {chain.gate.reason}
                </p>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function normaliseShapes(
  raw: string | string[] | null | undefined,
): string[] {
  if (raw == null) return [];
  if (Array.isArray(raw)) return raw.filter((s) => typeof s === 'string');
  return [raw];
}
