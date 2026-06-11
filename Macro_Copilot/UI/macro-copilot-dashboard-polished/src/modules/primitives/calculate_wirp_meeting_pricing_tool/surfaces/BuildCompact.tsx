// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_wirp_meeting_pricing_tool.  MEETING-LIST shape.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 every primitive claiming
// ``custom_build_surface`` ships a compact view.  The WIRP payload's
// canonical headline data is a MEETING LIST, not a single time series —
// per the SCANNER-shape guardrail precedent (scan_extremes_tool's
// BuildCompact) this component honours the SEMANTIC contract of §2.2
// (identity / headline data / methodology / expand / tone cues) with a
// table-shaped layout: a 3-cell KPI row (NEXT MEETING / IMPLIED RATE /
// CUM MOVE PROB) above a compact row list of the next meetings, reusing
// the shared ``FreshnessPill`` + ``toneTextClass`` helpers so the visual
// treatment matches the rest of the compact catalogue.
//
// FP9 guardrail: cumulative_move_prob_pct renders VERBATIM with a
// direction tone — never decomposed into hike/cut/hold probabilities.
// ============================================================================

import { ArrowUpRight, CalendarClock, Info } from 'lucide-react';
import {
  FreshnessPill,
  toneTextClass,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  WIRP_COMPACT_CAVEAT,
  centralBankMetaFor,
  compactKPIs,
  formatCumMoveProb,
  formatMeetingDate,
  formatNumMoves,
  formatRatePct,
  moveDirectionTone,
  useWirpMeetingPricing,
} from './wirpMeetingPricingShared';

// Number of meeting rows the compact card surfaces.  The card shows the
// next 4; the rest are reached via the expand affordance.
const COMPACT_MEETING_ROWS = 4;

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const centralBank = params.central_bank ?? '';
  const selectionMode = params.selection_mode || 'next_n_meetings';
  const isSpecificMode = selectionMode === 'specific_meeting_date';
  const nMeetings =
    params.n_meetings != null && params.n_meetings !== ''
      ? Number(params.n_meetings)
      : undefined;

  const { data, isLoading, errorMessage } = useWirpMeetingPricing({
    centralBank,
    selectionMode,
    nMeetings: isSpecificMode ? undefined : nMeetings,
    meetingDate: isSpecificMode ? params.meeting_date || undefined : undefined,
  });

  const cm = data?.current_metrics;
  const meta = centralBankMetaFor(cm?.central_bank ?? centralBank);
  const meetings = data?.meetings ?? [];
  const visibleMeetings = meetings.slice(0, COMPACT_MEETING_ROWS);
  const asOfDate = cm?.next_as_of_date ?? meetings[0]?.as_of_date ?? null;
  const kpis = data ? compactKPIs(data) : null;

  const minHeight = size === 'medium' ? 420 : 340;

  return (
    <article
      className="card flex flex-col gap-3 overflow-hidden bg-bg-elevated px-5 py-4"
      style={{ minHeight }}
      data-testid="wirp-meeting-pricing-compact"
    >
      {/* ---------- Header ---------- */}
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-line-subtle bg-surface-overlay">
            <CalendarClock size={12} strokeWidth={1.75} className="text-ice-300" aria-hidden />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[13px] font-semibold text-fg-primary">
              <span className="truncate">WIRP MEETING PRICING</span>
              <span className="text-fg-faint" aria-hidden>·</span>
              <span className="kicker text-fg-muted">SNAPSHOT</span>
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[12px] text-fg-secondary">
              <span className="font-mono">
                {centralBank || '—'} {meta?.flag ?? ''}
              </span>
              {cm && (
                <>
                  <span className="text-fg-faint" aria-hidden>·</span>
                  <span>{cm.n_meetings_returned} meetings</span>
                </>
              )}
              {callMeta && (
                <span className="ml-1 kicker text-fg-faint">
                  · call {callMeta.n} of {callMeta.m}
                </span>
              )}
            </div>
          </div>
        </div>
        {onExpand && (
          <button
            type="button"
            onClick={onExpand}
            aria-label="Open extended view of WIRP meeting pricing"
            title="Open extended view"
            className="rounded-md border border-line-subtle bg-surface-overlay p-1.5 text-fg-muted transition-colors hover:border-line-strong hover:text-fg-primary"
          >
            <ArrowUpRight size={14} strokeWidth={1.75} />
          </button>
        )}
      </header>

      <hr className="border-t border-line-subtle" />

      {/* ---------- Body ---------- */}
      {errorMessage ? (
        <CompactError message={errorMessage} />
      ) : isLoading ? (
        <CompactSkeleton />
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-3">
          {/* 3-cell headline KPI row */}
          <div className="grid grid-cols-3 gap-2">
            {(kpis ?? [
              { label: 'NEXT MEETING', value: '—' },
              { label: 'IMPLIED RATE', value: '—' },
              { label: 'CUM MOVE PROB', value: '—' },
            ]).map((k) => (
              <div
                key={k.label}
                className="flex flex-col gap-1 rounded-md border border-line-subtle bg-surface-overlay px-2.5 py-2"
              >
                <span className="kicker text-[9.5px] text-fg-muted">{k.label}</span>
                <span
                  className={`font-mono text-[15px] leading-none tabular-nums ${toneTextClass(k.tone ?? 'neutral')}`}
                >
                  {k.value}
                  {k.unit && (
                    <span className="ml-0.5 text-[10px] text-fg-muted">{k.unit}</span>
                  )}
                </span>
                {k.caption && (
                  <span className={`text-[9.5px] ${toneTextClass(k.tone ?? 'neutral')}`}>
                    {k.caption}
                  </span>
                )}
              </div>
            ))}
          </div>

          {/* Compact meeting rows (table-shaped guardrail) */}
          <div className="flex flex-col">
            <div className="grid grid-cols-[1.2fr_auto_auto_auto] items-center gap-2 px-1 text-[10.5px] uppercase tracking-wide text-fg-muted">
              <span>Meeting</span>
              <span className="text-right">Implied</span>
              <span className="text-right">Cum Prob</span>
              <span className="text-right">Moves</span>
            </div>
            {visibleMeetings.length === 0 ? (
              <div className="px-1 py-3 text-[12px] text-fg-secondary">
                No meetings inside the WIRP horizon for this selection.
              </div>
            ) : (
              visibleMeetings.map((m) => {
                const tone = moveDirectionTone(m.cumulative_move_prob_pct);
                return (
                  <div
                    key={m.vendor_ticker}
                    className="grid grid-cols-[1.2fr_auto_auto_auto] items-center gap-2 border-t border-line-subtle px-1 py-1.5 text-[12.5px]"
                  >
                    <span className="truncate font-mono text-[12px] text-fg-primary">
                      {formatMeetingDate(m.meeting_date)}
                    </span>
                    <span className="text-right font-mono tabular-nums text-fg-primary">
                      {formatRatePct(m.implied_policy_rate_pct)}
                    </span>
                    <span className={`text-right font-mono tabular-nums ${toneTextClass(tone)}`}>
                      {formatCumMoveProb(m.cumulative_move_prob_pct)}
                    </span>
                    <span className={`text-right font-mono tabular-nums ${toneTextClass(moveDirectionTone(m.num_25bp_moves_priced))}`}>
                      {formatNumMoves(m.num_25bp_moves_priced)}
                    </span>
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}

      {/* ---------- Footer ---------- */}
      <hr className="border-t border-line-subtle" />
      <footer className="flex items-center justify-between gap-3 text-[11px]">
        <div
          className="flex min-w-0 flex-1 items-center gap-1.5 text-fg-secondary"
          title={WIRP_COMPACT_CAVEAT}
        >
          <Info size={12} strokeWidth={1.5} className="shrink-0 text-fg-muted" aria-hidden />
          <span className="truncate">{WIRP_COMPACT_CAVEAT}</span>
        </div>
        <div className="flex shrink-0 items-center gap-3 text-fg-muted">
          {onExpand && meetings.length > visibleMeetings.length && (
            <button
              type="button"
              onClick={onExpand}
              className="text-ice-300 underline-offset-2 hover:underline"
            >
              View all {meetings.length} →
            </button>
          )}
          {asOfDate && <span>As of {asOfDate}</span>}
          <FreshnessPill freshness="fresh" />
        </div>
      </footer>
    </article>
  );
};

export default BuildCompact;

// ---------------------------------------------------------------------------
// Skeleton + error states
// ---------------------------------------------------------------------------

function CompactSkeleton() {
  return (
    <div className="flex min-h-[180px] flex-1 flex-col gap-3">
      <div className="grid grid-cols-3 gap-2">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="h-14 animate-pulse rounded-md border border-line-subtle bg-line-subtle/40"
          />
        ))}
      </div>
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          className="grid grid-cols-[1.2fr_auto_auto_auto] items-center gap-2 border-t border-line-subtle px-1 py-1.5"
        >
          <div className="h-3 w-20 animate-pulse rounded bg-line-subtle" />
          <div className="h-3 w-12 animate-pulse rounded bg-line-subtle justify-self-end" />
          <div className="h-3 w-12 animate-pulse rounded bg-line-subtle justify-self-end" />
          <div className="h-3 w-10 animate-pulse rounded bg-line-subtle justify-self-end" />
        </div>
      ))}
    </div>
  );
}

function CompactError({ message }: { message: string }) {
  return (
    <div className="flex min-h-[180px] flex-1 items-center justify-center px-2">
      <p className="text-center text-[11.5px] leading-[1.5] text-coral-300">
        {message}
      </p>
    </div>
  );
}
