// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_wirp_meeting_pricing_tool.  MEETING-LIST shape.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every primitive claiming
// ``custom_build_surface`` ships an extended view.  The WIRP payload is a
// LIST of per-meeting snapshots, not a time series — like the SCANNER-
// shape siblings (scan_extremes_tool) this component owns its own layout
// (identity row → next-meeting callout cards → controls strip → meeting
// STRIP visual → per-meeting table → methodology card → lineage footer)
// and reuses the shared ``ControlsStrip`` / ``KPIStrip`` /
// ``MethodologyCard`` / ``LineageFooter`` / ``FreshnessPill`` elements so
// the chrome matches the rest of the Build catalogue.
//
// The meeting strip is the desk-canonical WIRP read: per-meeting implied
// policy rate as a column reading across meeting dates (the "path" the
// market prices).  Composed as a flex column strip with tabular-nums —
// the shared MainChart is time-series oriented (date x-axis, range chips)
// and would misrepresent ≤24 discrete meeting snapshots as a series.
//
// FP9 guardrail honoured throughout: cumulative_move_prob_pct is rendered
// VERBATIM with direction tone only — no hike/cut/hold decomposition.
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { CalendarClock, Info } from 'lucide-react';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  ControlsStrip,
  FreshnessPill,
  KPIStrip,
  LineageFooter,
  MethodologyCard,
  asOfDateControl,
  toneTextClass,
  type BuildExtendedProps,
  type ControlDescriptor,
} from '@/components/shared/build';
import type { WirpMeetingSnapshot } from '@/types/rates';
import {
  WIRP_CENTRAL_BANK_OPTIONS,
  WIRP_COMPACT_CAVEAT,
  WIRP_N_MEETINGS_OPTIONS,
  WIRP_SELECTION_MODE_OPTIONS,
  buildMeetingStrip,
  buildMethodologyRows,
  buildReferenceChips,
  centralBankMetaFor,
  extendedKPIs,
  formatCumMoveProb,
  formatMeetingDate,
  formatNumMoves,
  formatRateChangeNative,
  formatRatePct,
  moveDirectionCaption,
  moveDirectionTone,
  useWirpMeetingPricing,
} from './wirpMeetingPricingShared';

const DEFAULTS = {
  selection_mode: 'next_n_meetings',
  n_meetings: '6',
};

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params (Record<string,string> boundary) -----
  const centralBank = params.central_bank ?? 'FOMC';
  // URL params arrive as bare strings — narrow to the closed mode
  // pair (anything unrecognised falls back to the default mode).
  const selectionMode =
    params.selection_mode === 'specific_meeting_date'
      ? ('specific_meeting_date' as const)
      : ('next_n_meetings' as const);
  const nMeetingsStr = params.n_meetings || DEFAULTS.n_meetings;
  const meetingDate = params.meeting_date || undefined;
  const isSpecificMode = selectionMode === 'specific_meeting_date';

  const { data, isLoading, errorMessage } = useWirpMeetingPricing({
    centralBank,
    selectionMode,
    nMeetings: isSpecificMode ? undefined : Number(nMeetingsStr),
    meetingDate: isSpecificMode ? meetingDate : undefined,
    asOfDate: params.as_of_date,
  });

  const cm = data?.current_metrics;
  const meta = centralBankMetaFor(centralBank);
  const meetings = data?.meetings ?? [];
  const stripCells = buildMeetingStrip(meetings);

  // ----- Param update on control change -----
  const pushParams = (nextParams: Record<string, string>) => {
    if (onParamsChange) {
      onParamsChange(nextParams);
      return;
    }
    const nextCtx = encodeURIComponent(
      JSON.stringify({
        tools: [{ tool: toolName, params: nextParams }],
        tool_count: 1,
      }),
    );
    navigate(`/workspace?context=${nextCtx}`, { replace: true });
  };

  const handleControlChange = (name: string, value: string) => {
    const nextParams = { ...params, [name]: value };
    if (name === 'selection_mode') {
      // Mode switch invalidates the dependent field of the OTHER mode —
      // strip it so the backend's mode → required-field invariant never
      // fires from a stale param combination.
      if (value === 'next_n_meetings') {
        delete nextParams.meeting_date;
        if (!nextParams.n_meetings) nextParams.n_meetings = DEFAULTS.n_meetings;
      } else {
        delete nextParams.n_meetings;
      }
    }
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({ central_bank: centralBank, ...DEFAULTS });
  };

  // ----- Controls (mode-dependent third control) -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'central_bank',
      label: 'Central Bank',
      kind: 'enum',
      value: centralBank,
      options: WIRP_CENTRAL_BANK_OPTIONS,
    },
    {
      name: 'selection_mode',
      label: 'Selection Mode',
      kind: 'enum',
      value: selectionMode,
      options: WIRP_SELECTION_MODE_OPTIONS,
    },
    ...(isSpecificMode
      ? [
          {
            name: 'meeting_date',
            label: 'Meeting Date (YYYY-MM-DD)',
            kind: 'text',
            value: meetingDate ?? '',
          } as ControlDescriptor,
        ]
      : [
          {
            name: 'n_meetings',
            label: 'Meetings',
            kind: 'enum',
            value: nMeetingsStr,
            options: WIRP_N_MEETINGS_OPTIONS,
          } as ControlDescriptor,
        ]),
    asOfDateControl(params.as_of_date),
  ];

  const asOfDate = cm?.next_as_of_date ?? meetings[0]?.as_of_date ?? null;

  return (
    <div
      className="flex h-full min-h-0 flex-col overflow-y-auto"
      data-testid="wirp-meeting-pricing-extended"
    >
      {/* ---------- Title section ---------- */}
      <section className="grid grid-cols-1 gap-4 border-b border-line-subtle px-6 pt-5 pb-5 lg:grid-cols-[1fr_auto]">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-wide text-fg-muted">
            <CalendarClock size={12} strokeWidth={1.75} className="text-ice-300" aria-hidden />
            <span className="font-semibold">WIRP MEETING PRICING</span>
            <span className="text-fg-faint" aria-hidden>•</span>
            <span>SNAPSHOT</span>
            <span className="text-fg-faint" aria-hidden>•</span>
            <span>BLOOMBERG INGEST</span>
          </div>
          <div className="flex items-baseline gap-3">
            <h1 className="flex items-baseline gap-3 text-[26px] font-medium leading-tight tracking-[-0.012em] text-fg-primary">
              <span>{centralBank}</span>
              <span className="text-fg-faint" aria-hidden>·</span>
              <span>Meeting Pricing</span>
              {meta && <span className="text-[22px] leading-none">{meta.flag}</span>}
            </h1>
          </div>
          <p className="text-[12.5px] leading-[1.55] text-fg-secondary">
            {meta
              ? `${meta.longLabel} · implied post-meeting ${meta.policyRateLabel}`
              : 'Per-meeting implied policy rate path'}
          </p>
          <p className="flex flex-wrap items-center gap-2 text-[11.5px] text-fg-muted">
            {asOfDate && <span>As of {asOfDate}</span>}
            <span className="text-fg-faint" aria-hidden>·</span>
            <span>
              {isSpecificMode
                ? `Specific meeting ${meetingDate ?? '—'}`
                : `Next ${nMeetingsStr} meetings`}
            </span>
            <FreshnessPill freshness="fresh" />
          </p>
        </div>
        <div className="grid grid-flow-col auto-cols-[minmax(190px,1fr)] gap-3">
          <NextMeetingCard
            date={cm?.next_meeting_date ?? null}
            asOf={cm?.next_as_of_date ?? null}
          />
          <ImpliedRateCard rate={cm?.next_implied_policy_rate_pct ?? null} meta={meta?.policyRateLabel} />
          <CumMoveProbCard prob={cm?.next_cumulative_move_prob_pct ?? null} />
        </div>
      </section>

      {/* ---------- Controls strip ---------- */}
      <div className="px-6 pt-4">
        <ControlsStrip
          controls={controls}
          onChange={handleControlChange}
          onReset={handleReset}
        />
      </div>

      {/* ---------- KPI strip ---------- */}
      <div className="px-6 pt-3">
        <KPIStrip
          kpis={
            data
              ? extendedKPIs(data)
              : [
                  { label: 'NEXT MEETING', value: '—' },
                  { label: 'IMPLIED RATE', value: '—' },
                  { label: 'CUM MOVE PROB', value: '—' },
                  { label: '25BP MOVES PRICED', value: '—' },
                  { label: 'Δ VS EFFECTIVE', value: '—' },
                  { label: 'MEETINGS RETURNED', value: '—' },
                ]
          }
        />
      </div>

      {/* ---------- Meeting strip visual ---------- */}
      <div className="px-6 pt-4">
        <div className="card flex flex-col gap-3 px-4 py-4">
          <div className="flex items-baseline justify-between gap-3">
            <span className="kicker text-fg-muted">
              IMPLIED POLICY RATE BY MEETING
            </span>
            <span className="text-[11px] text-fg-faint">
              Bloomberg WIRP_IMPLIED_RATE, verbatim — the per-meeting path,
              not an interpolated curve.
            </span>
          </div>
          {errorMessage ? (
            <StripError message={errorMessage} />
          ) : isLoading ? (
            <StripSkeleton />
          ) : (
            <MeetingStrip cells={stripCells} />
          )}
        </div>
      </div>

      {/* ---------- Per-meeting table ---------- */}
      <div className="px-6 pt-4">
        <MeetingsTable
          meetings={meetings}
          isLoading={isLoading}
          errorMessage={errorMessage ?? undefined}
        />
      </div>

      {/* ---------- Methodology ---------- */}
      <div className="grid grid-cols-1 gap-3 px-6 pt-4 pb-6 lg:grid-cols-[2fr_3fr]">
        <CumulativeProbKeyCard />
        <MethodologyCard
          rows={data ? buildMethodologyRows(data) : []}
          references={buildReferenceChips()}
        />
      </div>

      <LineageFooter
        lineage={{
          toolName,
          version: 'v1',
          kind: 'Bloomberg WIRP ingest snapshot (verbatim, list-shaped)',
          providers: ['TimescaleDB', 'Bloomberg WIRP (ADR 0009)'],
          asOf: asOfDate ?? undefined,
          freshness: 'fresh',
        }}
      />
    </div>
  );
};

export default BuildExtended;

// ---------------------------------------------------------------------------
// Top-right callout cards
// ---------------------------------------------------------------------------

function NextMeetingCard({
  date,
  asOf,
}: {
  date: string | null;
  asOf: string | null;
}) {
  return (
    <div className="card flex h-full flex-col gap-2 px-4 py-3">
      <span className="kicker text-fg-muted">NEXT MEETING</span>
      <div className="text-[22px] font-medium leading-none text-fg-primary">
        {formatMeetingDate(date)}
      </div>
      <span className="text-[11.5px] text-fg-secondary">
        {asOf ? `WIRP as of ${asOf}` : '—'}
      </span>
    </div>
  );
}

function ImpliedRateCard({
  rate,
  meta,
}: {
  rate: number | null;
  meta?: string;
}) {
  return (
    <div className="card flex h-full flex-col gap-2 px-4 py-3">
      <span className="kicker text-fg-muted">IMPLIED RATE</span>
      <div className="text-[26px] font-medium leading-none text-fg-primary">
        {formatRatePct(rate)}
      </div>
      <span className="text-[11px] leading-snug text-fg-secondary">
        {meta ?? 'Post-meeting implied policy rate'}
      </span>
    </div>
  );
}

function CumMoveProbCard({ prob }: { prob: number | null }) {
  const tone = moveDirectionTone(prob);
  return (
    <div className="card flex h-full flex-col gap-2 px-4 py-3">
      <span className="kicker text-fg-muted">CUM MOVE PROB</span>
      <div className={`text-[26px] font-medium leading-none ${toneTextClass(tone)}`}>
        {formatCumMoveProb(prob)}
      </div>
      <span className={`text-[11.5px] ${toneTextClass(tone)}`}>
        {moveDirectionCaption(prob)} · cumulative
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Meeting strip — per-meeting implied rate as a column strip across the
// meeting dates.  Flex columns with tabular-nums; height scaled within
// the strip's [min, max] band (display scaling only).
// ---------------------------------------------------------------------------

function MeetingStrip({
  cells,
}: {
  cells: ReadonlyArray<{
    meeting: WirpMeetingSnapshot;
    heightFrac: number | null;
  }>;
}) {
  if (cells.length === 0) {
    return (
      <div className="flex h-[160px] items-center justify-center text-[12px] text-fg-secondary">
        No meetings inside the WIRP horizon for this selection.
      </div>
    );
  }
  return (
    <div className="flex h-[180px] items-stretch gap-2">
      {cells.map(({ meeting, heightFrac }) => {
        const tone = moveDirectionTone(meeting.cumulative_move_prob_pct);
        return (
          <div
            key={meeting.vendor_ticker}
            className="flex min-w-0 flex-1 flex-col items-center gap-1"
            title={`${meeting.meeting_date} · implied ${formatRatePct(meeting.implied_policy_rate_pct)} · cum move prob ${formatCumMoveProb(meeting.cumulative_move_prob_pct)}`}
          >
            <span className="font-mono text-[11px] tabular-nums text-fg-primary">
              {meeting.implied_policy_rate_pct != null
                ? meeting.implied_policy_rate_pct.toFixed(2)
                : '—'}
            </span>
            <div className="flex w-full flex-1 items-end">
              {heightFrac != null ? (
                <div
                  className="w-full rounded-t bg-ice-300/60 ring-1 ring-ice-300/30"
                  style={{ height: `${heightFrac * 100}%` }}
                  aria-label={`${meeting.meeting_date}: implied ${formatRatePct(meeting.implied_policy_rate_pct)}`}
                />
              ) : (
                <div className="w-full rounded-t border border-dashed border-line-subtle" style={{ height: '12%' }} aria-label={`${meeting.meeting_date}: no implied rate`} />
              )}
            </div>
            <span className={`font-mono text-[10px] tabular-nums ${toneTextClass(tone)}`}>
              {formatCumMoveProb(meeting.cumulative_move_prob_pct)}
            </span>
            <span className="truncate font-mono text-[9.5px] text-fg-muted">
              {formatMeetingDate(meeting.meeting_date)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function StripSkeleton() {
  return (
    <div className="flex h-[180px] items-end gap-2">
      {[0.45, 0.6, 0.7, 0.55, 0.8, 0.65].map((h, i) => (
        <div key={i} className="flex flex-1 items-end">
          <div
            className="w-full animate-pulse rounded-t bg-line-subtle"
            style={{ height: `${h * 100}%` }}
          />
        </div>
      ))}
    </div>
  );
}

function StripError({ message }: { message: string }) {
  return (
    <div className="flex h-[160px] items-center justify-center px-4">
      <p className="max-w-[480px] text-center text-[12px] leading-[1.5] text-coral-300">
        {message}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-meeting table
// ---------------------------------------------------------------------------

function MeetingsTable({
  meetings,
  isLoading,
  errorMessage,
}: {
  meetings: ReadonlyArray<WirpMeetingSnapshot>;
  isLoading?: boolean;
  errorMessage?: string;
}) {
  return (
    <div className="card overflow-hidden">
      <div className="border-b border-line-subtle px-4 py-3">
        <span className="kicker text-fg-muted">Per-meeting detail</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-[12.5px]">
          <thead>
            <tr className="border-b border-line-subtle text-left text-[10.5px] uppercase tracking-wide text-fg-muted">
              <th className="px-4 py-2 font-normal">Meeting</th>
              <th className="px-3 py-2 font-normal">Token</th>
              <th className="px-3 py-2 text-right font-normal">Implied Rate</th>
              <th className="px-3 py-2 text-right font-normal">Cum Move Prob</th>
              <th className="px-3 py-2 text-right font-normal">25bp Moves</th>
              <th className="px-3 py-2 text-right font-normal">Δ Native (pp)</th>
              <th className="px-4 py-2 text-right font-normal">As Of</th>
            </tr>
          </thead>
          <tbody>
            {errorMessage ? (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-coral-300">
                  {errorMessage}
                </td>
              </tr>
            ) : isLoading ? (
              [0, 1, 2, 3].map((i) => (
                <tr key={`sk-${i}`} className="border-b border-line-subtle">
                  {[24, 16, 12, 12, 10, 12, 16].map((w, j) => (
                    <td key={j} className="px-3 py-2">
                      <div
                        className={`h-3 animate-pulse rounded bg-line-subtle ${j >= 2 ? 'ml-auto' : ''}`}
                        style={{ width: `${w * 4}px` }}
                      />
                    </td>
                  ))}
                </tr>
              ))
            ) : meetings.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-fg-secondary">
                  No meetings inside the WIRP horizon for this selection
                  (honest absence — the horizon carries fewer ingested
                  meetings than requested).
                </td>
              </tr>
            ) : (
              meetings.map((m) => {
                const tone = moveDirectionTone(m.cumulative_move_prob_pct);
                return (
                  <tr
                    key={m.vendor_ticker}
                    className="border-b border-line-subtle hover:bg-surface-overlay/60"
                  >
                    <td className="px-4 py-2 font-mono tabular-nums text-fg-primary">
                      {m.meeting_date}
                    </td>
                    <td className="px-3 py-2 font-mono text-[11.5px] text-fg-secondary">
                      {m.meeting_token ?? '—'}
                    </td>
                    <td className="px-3 py-2 text-right font-mono tabular-nums text-fg-primary">
                      {formatRatePct(m.implied_policy_rate_pct)}
                    </td>
                    <td className={`px-3 py-2 text-right font-mono tabular-nums ${toneTextClass(tone)}`}>
                      {formatCumMoveProb(m.cumulative_move_prob_pct)}
                    </td>
                    <td className={`px-3 py-2 text-right font-mono tabular-nums ${toneTextClass(moveDirectionTone(m.num_25bp_moves_priced))}`}>
                      {formatNumMoves(m.num_25bp_moves_priced)}
                    </td>
                    <td className={`px-3 py-2 text-right font-mono tabular-nums ${toneTextClass(moveDirectionTone(m.rate_change_native))}`}>
                      {formatRateChangeNative(m.rate_change_native)}
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-[11.5px] text-fg-muted">
                      {m.as_of_date}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cumulative-probability key card — the FP9 guardrail rendered as a desk
// reading key (structural framing; the substantive P5 disclosure is the
// wire's methodology_note on the MethodologyCard).
// ---------------------------------------------------------------------------

function CumulativeProbKeyCard() {
  return (
    <div className="card flex flex-col gap-3 px-4 py-4">
      <span className="kicker text-fg-muted">Reading key</span>
      <ul className="flex flex-col gap-2 text-[12px] text-fg-secondary">
        <li className="flex items-center gap-2">
          <span className={`font-mono text-[11px] ${toneTextClass('negative')}`}>+%</span>
          <span>Positive cum move prob · hike-leaning (coral)</span>
        </li>
        <li className="flex items-center gap-2">
          <span className={`font-mono text-[11px] ${toneTextClass('positive')}`}>−%</span>
          <span>Negative cum move prob · cut-leaning (mint)</span>
        </li>
        <li className="flex items-center gap-2">
          <span className="font-mono text-[11px] text-fg-muted">±100+</span>
          <span>Beyond ±100 · more than one 25bp move priced (normal)</span>
        </li>
      </ul>
      <div className="flex items-start gap-2 rounded-md border border-line-subtle bg-surface-overlay px-3 py-2 text-[11px] leading-snug text-fg-secondary">
        <Info size={12} strokeWidth={1.5} className="mt-0.5 shrink-0 text-fg-muted" aria-hidden />
        <span>{WIRP_COMPACT_CAVEAT}</span>
      </div>
    </div>
  );
}
