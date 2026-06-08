// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_swap_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is the cross-domain swap-spread's.  Mounted as a node body inside
// multi-tool query DAG visualizations.  Design reference: ./mockups/Compact.png.
//
// Per Option (c) precedent (Batch 1 fdac7d2, Batch 2 cbd5613) — keep
// shell-standard density on Compact (3 KPIs, single identity).  The mockup
// shows a 6-KPI secondary strip; the THESIS Mockup conformance subsection
// documents the accepted deviation.  The full 11-cell strip surfaces on the
// extended view.
//
// All layout / tone / chart / methodology-disclosure chrome lives in the
// shared shell at @/components/shared/build — finance-blind.  This file is
// fetch + descriptor-mapping + shell composition only.
// ============================================================================

import { GitCompareArrows } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  SWAP_SPREAD_COMPACT_CAVEAT,
  buildReferenceBands,
  compactKPIs,
  pairForSovereign,
  sanitiseSpreadSeries,
  useSwapSpread,
} from './swapSpreadShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const { data, isLoading, errorMessage } = useSwapSpread({
    sovereignCurveFamily: params.sovereign_curve_family ?? '',
    oisCurveFamily: params.ois_curve_family ?? '',
    tenor: params.tenor ?? '',
    lookbackDays:
      params.lookback_days != null && params.lookback_days !== ''
        ? Number(params.lookback_days)
        : undefined,
    sovereignFieldName: params.sovereign_field_name || undefined,
    oisFieldName: params.ois_field_name || undefined,
  });

  const cm = data?.current_metrics;
  const sovereignFamily =
    cm?.sovereign_curve_family ?? params.sovereign_curve_family ?? '';
  const oisFamily = cm?.ois_curve_family ?? params.ois_curve_family ?? '';
  const tenor = cm?.tenor ?? params.tenor ?? '';
  const pair = pairForSovereign(sovereignFamily);

  // Mockup-faithful identity row (Compact.png):
  //   primary:   "UST-SOFR · 10Y"
  //   secondary: "UST 10Y yield − SOFR 10Y OIS rate"
  //   flag:      🇺🇸
  const pairLabel = pair
    ? `${pair.sovereignShort}-${pair.oisShort}`
    : sovereignFamily && oisFamily
      ? `${sovereignFamily}-${oisFamily}`
      : '—';
  const primary = `${pairLabel} · ${tenor || '—'}`;
  const secondary = pair
    ? `${pair.sovereignShort} ${tenor} yield − ${pair.oisShort} ${tenor} OIS rate`
    : `${sovereignFamily} yield − ${oisFamily} rate`;

  return (
    <BuildCompactShell
      toolDisplayName="Swap Spread"
      statusPill="SNAPSHOT"
      headerIcon={<GitCompareArrows size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary,
        secondary,
        flag: pair?.flag,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'SPREAD', value: '—', unit: 'bp' },
              { label: '1D CHANGE', value: '—', unit: 'bp' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitiseSpreadSeries(
        data?.time_series_spread?.rows ?? [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="bp"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={SWAP_SPREAD_COMPACT_CAVEAT}
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
