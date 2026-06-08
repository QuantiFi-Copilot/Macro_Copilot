// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// policy_futures_get_futures_calendar_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is the same-curve STIR calendar spread's.  Mounted as a node
// body inside multi-tool query DAG visualizations (e.g. *"compare SFR 1-3
// calendar spread vs ER 1-3 calendar spread"*).
//
// Design reference: ./mockups/Compact.png.
//
// Per the Option-(c) precedent (Batch 1 fdac7d2 +
// policy_futures_get_futures_price_level_tool d8e8233 + tool 19 fcc5381)
// this file uses the SHELL-STANDARD density (3 KPIs + single identity
// line + sparkline + caveat + expand).  The mockup's denser layout — the
// extra Whites tag + flag chip in the identity row plus the dual-band
// sparkline annotations — is preserved visually; promoting additional KPI
// cells into the compact view would require extending BuildCompactShell
// with a ``secondaryKpis`` slot, touching 10+ shipped compact views.  See
// THESIS.md → Mockup conformance for the deferral rationale.
// ============================================================================

import { GitCompareArrows } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  POLICY_FUTURES_CALENDAR_COMPACT_CAVEAT,
  buildReferenceBands,
  calendarPairLabel,
  compactKPIs,
  curveMetaFor,
  sanitiseSpreadSeries,
  stripSegmentLabel,
  usePolicyFuturesCalendar,
} from './futuresCalendarSpreadShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const curveFamilyParam = params.curve_family ?? '';
  const shortNum =
    params.strip_position_short && Number(params.strip_position_short)
      ? Number(params.strip_position_short)
      : 0;
  const longNum =
    params.strip_position_long && Number(params.strip_position_long)
      ? Number(params.strip_position_long)
      : 0;

  const { data, isLoading, errorMessage } = usePolicyFuturesCalendar({
    curveFamily: curveFamilyParam,
    stripPositionShort: shortNum,
    stripPositionLong: longNum,
    lookbackDays:
      params.lookback_days != null && params.lookback_days !== ''
        ? Number(params.lookback_days)
        : undefined,
    asOfDate: params.as_of_date || undefined,
    fieldName: params.field_name || undefined,
  });

  const cm = data?.current_metrics;
  const curveFamily = cm?.curve_family ?? curveFamilyParam;
  const shortPos = cm?.strip_position_short ?? shortNum;
  const longPos = cm?.strip_position_long ?? longNum;
  const meta = curveMetaFor(curveFamily);
  const pairDisplay =
    shortPos > 0 && longPos > 0 ? calendarPairLabel(shortPos, longPos) : '—';
  // Segment chip — single segment when both legs share Whites/Reds;
  // mixed otherwise.  Mockup shows "WHITES" for SFR 1-3.
  const segments = new Set<string>();
  if (shortPos > 0) segments.add(stripSegmentLabel(shortPos));
  if (longPos > 0) segments.add(stripSegmentLabel(longPos));
  const segmentTag =
    segments.size === 1 ? [...segments][0] : segments.size > 1 ? 'Mixed' : '';

  // Mockup-faithful identity row:
  //   primary:   "SFR · 1-3 · WHITES"   (master short + pair + segment)
  //   secondary: "SOFR Calendar Spread (Implied Rate)"
  const primary = meta
    ? `${meta.shortLabel} · ${pairDisplay}${segmentTag ? ` · ${segmentTag}` : ''}`
    : `${curveFamily || '—'} · ${pairDisplay}`;
  const secondary = meta
    ? `${meta.shortLabel} Calendar Spread (Implied Rate)`
    : 'STIR Calendar Spread (Implied Rate)';

  return (
    <BuildCompactShell
      toolDisplayName="Policy Futures Calendar Spread"
      statusPill="STIR"
      headerIcon={<GitCompareArrows size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary,
        secondary,
        flag: meta?.flag,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'SPREAD (BPS)', value: '—', unit: 'bp' },
              { label: '1D CHANGE (BPS)', value: '—', unit: 'bp' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitiseSpreadSeries(data?.time_series ?? []).map(
        (r) => ({ date: r.date, value: r.value ?? NaN }),
      )}
      chartUnit="bp"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={POLICY_FUTURES_CALENDAR_COMPACT_CAVEAT}
      asOf={cm?.as_of_date}
      freshness="fresh"
      onExpand={onExpand}
      size={size}
      isLoading={isLoading}
      errorMessage={errorMessage ?? undefined}
      callMeta={callMeta}
    />
  );
};

export default BuildCompact;
