// ============================================================================
// WirpMeetingPricingWidget — Monitor bento tile for one central bank's
// next-meeting WIRP read.
// ----------------------------------------------------------------------------
// Parameterised on ``central_bank`` only.  Fetches the SAME typed-detail
// endpoint (/api/v1/rates/detail/wirp-meeting-pricing) the Build views
// use — standalone-bridge contract per methodology_exposure.md §5.4.
// Requests ``n_meetings=1`` (next-N mode) because the tile only renders
// the NEXT meeting; the Build views own the full path read.
//
// Per rendering_density.md §8 the Monitor surface is INHERENTLY COMPACT.
// Shows: next meeting date (headline), implied post-meeting policy rate,
// cumulative move probability (verbatim, direction-toned), and the
// 25bp-moves-priced count.  FP9 guardrail honoured: the cumulative move
// probability is NEVER decomposed into hike/cut/hold.
// Mirrors the BreakevenInflationWidget / PolicyFuturesPriceLevelWidget
// shape (WidgetHeader / WidgetBody / WidgetProvenance + local fetcher).
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailWirpMeetingPricing,
  type WirpMeetingPricingDetailParams,
} from '@/services/ratesApi';
import type { WirpMeetingPricingOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import { cn } from '@/utils/cn';
import {
  centralBankMetaFor,
  formatCumMoveProb,
  formatMeetingDate,
  formatNumMoves,
} from '../wirpMeetingPricingShared';

type Props = {
  params: Record<string, unknown>;
};

export function WirpMeetingPricingWidget({ params }: Props) {
  const fetchParams = useMemo<WirpMeetingPricingDetailParams | null>(() => {
    const cb =
      typeof params.central_bank === 'string' && params.central_bank
        ? params.central_bank
        : null;
    if (!cb) return null;
    // The tile shows the NEXT meeting only — next-N mode with n=1.
    return { central_bank: cb, selection_mode: 'next_n_meetings', n_meetings: 1 };
  }, [params]);

  const { data, error, isLoading } = useFetchDetailWirpMeetingPricing(fetchParams);

  if (!fetchParams) {
    return <WidgetError message="Widget params incomplete." />;
  }
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const m = data.current_metrics;
  const meta = centralBankMetaFor(m.central_bank);
  const prob = m.next_cumulative_move_prob_pct;
  const hikeLeaning = (prob ?? 0) > 0;
  const flag = meta?.flag ?? '';

  return (
    <>
      <WidgetHeader
        kicker={`${m.central_bank} · NEXT MEETING ${flag}`.trim()}
        title="WIRP Meeting Pricing"
        meta={
          <span
            className={cn(
              'inline-flex items-center rounded-full px-2 py-[1px] font-mono text-[10px] font-medium tracking-[0.02em] ring-1',
              prob != null && Math.abs(prob) >= 50
                ? 'ring-amber-400/30 bg-amber-400/[0.08] text-amber-300'
                : 'ring-line-soft bg-white/[0.025] text-fg-secondary',
            )}
            title="Cumulative signed 25bp-move probability — Bloomberg verbatim; can exceed ±100."
          >
            cum {formatCumMoveProb(prob)}
          </span>
        }
      />
      <WidgetBody className="flex flex-col gap-2 px-5 pb-3">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-[24px] font-light leading-none tracking-[-0.012em] text-fg-primary">
            {formatMeetingDate(m.next_meeting_date)}
          </span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[10.5px]">
          <span>
            <span className="text-fg-faint">implied </span>
            <span className="font-medium text-fg-primary">
              {m.next_implied_policy_rate_pct != null
                ? `${m.next_implied_policy_rate_pct.toFixed(2)}%`
                : '—'}
            </span>
          </span>
          <span className="text-fg-faint">·</span>
          <span>
            <span className="text-fg-faint">cum prob </span>
            <span
              className={cn(
                'font-medium',
                // Hike-leaning (positive) = tightening → coral; cut-
                // leaning (negative) = easing → mint.  Sign read only.
                prob == null || prob === 0
                  ? 'text-fg-secondary'
                  : hikeLeaning
                    ? 'text-coral-300'
                    : 'text-mint-300',
              )}
            >
              {formatCumMoveProb(prob)}
            </span>
          </span>
          <span className="text-fg-faint">·</span>
          <span>
            <span className="text-fg-faint">moves </span>
            <span className="font-medium text-fg-secondary">
              {formatNumMoves(m.next_num_25bp_moves_priced)}
            </span>
          </span>
        </div>
        <p className="font-mono text-[9.5px] leading-snug text-fg-faint">
          Bloomberg WIRP verbatim · cumulative, no hike/cut/hold split
          {m.next_as_of_date ? ` · as of ${m.next_as_of_date}` : ''}
        </p>
      </WidgetBody>
      <WidgetProvenance
        toolName="calculate_wirp_meeting_pricing_tool"
        asOfDate={m.next_as_of_date ?? undefined}
      />
    </>
  );
}

// ----------------------------------------------------------------------------
// Local fetcher — stable JSON key prevents re-fetch churn on parent
// re-renders (mirrors useFetchDetailBreakeven in the pilot).

function useFetchDetailWirpMeetingPricing(
  params: WirpMeetingPricingDetailParams | null,
) {
  const [state, setState] = useState<{
    data: WirpMeetingPricingOutput | null;
    error: Error | null;
    isLoading: boolean;
  }>({ data: null, error: null, isLoading: !!params });

  const paramsKey = params ? JSON.stringify(params) : null;

  useEffect(() => {
    if (!params) {
      setState({ data: null, error: null, isLoading: false });
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, isLoading: true, error: null }));
    fetchDetailWirpMeetingPricing(params)
      .then((data) => {
        if (!cancelled) setState({ data, error: null, isLoading: false });
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setState({
            data: null,
            error: e instanceof Error ? e : new Error('fetch failed'),
            isLoading: false,
          });
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paramsKey]);

  return state;
}
