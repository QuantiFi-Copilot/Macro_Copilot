// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_inflation_swap_forward_tool.
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §2.2 + §5 every new
// primitive ships a compact view; this is ZCIS forward-rate's.  Mounted
// as a node body inside multi-tool query DAG visualizations OR when a
// multi-primitive Ask handoff places this tool in a side-by-side grid.
//
// Design reference: ./mockups/Compact.png (committed alongside this module).
//
// Slot contract: this file is a thin fetch + descriptor-mapping + shell
// composition wrapper.  All layout, tone semantics, methodology
// disclosure, and chart rendering live in the shared shells at
// @/components/shared/build/ — finance-blind, reusable across tools.
//
// Per Option (c) precedent (sibling OIS forward_rate): the compact card
// stays at shell-standard density — 3 headline KPIs (FORWARD ZCIS / 1D
// CHANGE / Z-SCORE) + sparkline + caveat footer.  The mockup shows more
// KPIs (5D / 1M / PERCENTILE / 252D HIGH / 252D LOW / OBSERVATIONS), but
// those belong to the EXTENDED view; surfacing them on the compact card
// would violate the shell-standard 3-KPI ceiling documented in THESIS.
// ============================================================================

import { TrendingUp } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  buildReferenceBands,
  compactCaveatText,
  compactKPIs,
  forwardPairFor,
  forwardShortLabel,
  sanitiseForwardSeries,
  useInflationSwapForward,
  zcisFamilyFor,
} from './inflationSwapForwardShared';

const DEFAULT_FORWARD_PAIR = '5Y5Y';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  // Resolve the (start_tenor, end_tenor) pair from either the canonical
  // forward_pair label or the explicit start/end tenors the URL may
  // carry (the extended view writes both).
  const forwardPairLabel =
    (params.forward_pair as string | undefined) ?? DEFAULT_FORWARD_PAIR;
  const pair = forwardPairFor(forwardPairLabel);
  const startTenor = params.start_tenor || pair?.startTenor || '5Y';
  const endTenor = params.end_tenor || pair?.endTenor || '10Y';

  const { data, isLoading, errorMessage } = useInflationSwapForward({
    curveFamily: params.curve_family ?? '',
    startTenor,
    endTenor,
    lookbackDays:
      params.lookback_days != null ? Number(params.lookback_days) : undefined,
    fieldName: params.field_name || undefined,
  });

  // Pre-data identity (before fetch returns) — pulled from params so the
  // header renders something useful during the loading state.
  const fallbackCurve = params.curve_family ?? '—';
  const cm = data?.current_metrics;
  const curveFamily = cm?.curve_family ?? fallbackCurve;
  const meta = zcisFamilyFor(curveFamily);

  // Identity mirrors the mockup: "EUR ZCIS · 5Y5Y · HICPxT" — the three
  // canonical segments shown in mockups/Compact.png.  Primary line is the
  // market + curve-family stem; secondary line is the forward-pair label
  // followed by the inflation-index short (CPI-U / HICPxT / RPI) so the
  // index-family identity is visible without expanding to the extended
  // view.
  const primary = meta ? `${meta.marketShort} ZCIS` : curveFamily;
  const forwardSeg = cm ? forwardShortLabel(cm) : forwardPairLabel;
  const secondary = meta ? `${forwardSeg} · ${meta.indexShort}` : forwardSeg;

  return (
    <BuildCompactShell
      toolDisplayName="Inflation Swap Forward"
      statusPill="SNAPSHOT"
      headerIcon={<TrendingUp size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary,
        secondary,
        flag: meta?.flag,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'FORWARD ZCIS', value: '—', unit: '%' },
              { label: '1D CHANGE', value: '—', unit: 'bp' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitiseForwardSeries(data?.time_series ?? []).map((r) => ({
        date: r.date,
        value: r.value ?? NaN,
      }))}
      chartUnit="%"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={compactCaveatText(curveFamily)}
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
