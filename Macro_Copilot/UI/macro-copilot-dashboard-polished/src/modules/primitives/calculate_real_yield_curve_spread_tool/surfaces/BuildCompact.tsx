// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_real_yield_curve_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5.  Mounted as a node body inside multi-
// tool query DAG visualizations.  Design reference: ./mockups/Compact.png.
// Fetch + descriptor-mapping + shell composition only; all chrome lives in
// the finance-blind shell at @/components/shared/build.
// ============================================================================

import { GitCompareArrows } from 'lucide-react';
import {
  BuildCompactShell,
  countryCaveatFor,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  CURVE_SPREAD_COMPACT_CAVEAT,
  compactKPIs,
  buildReferenceBands,
  sanitiseSpreadSeries,
  spreadShortLabel,
  useRealYieldCurveSpread,
} from './curveSpreadShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const { data, isLoading, errorMessage } = useRealYieldCurveSpread({
    curveFamily: params.curve_family ?? '',
    shortTenor: params.short_tenor ?? '',
    longTenor: params.long_tenor ?? '',
    lookbackDays:
      params.lookback_days != null ? Number(params.lookback_days) : undefined,
    fieldName: params.field_name || undefined,
    zScoreWindowDays:
      params.z_score_window_days != null && params.z_score_window_days !== ''
        ? Number(params.z_score_window_days)
        : undefined,
    zScoreMinPeriods:
      params.z_score_min_periods != null && params.z_score_min_periods !== ''
        ? Number(params.z_score_min_periods)
        : undefined,
    zScoreDdof:
      params.z_score_ddof != null && params.z_score_ddof !== ''
        ? Number(params.z_score_ddof)
        : undefined,
  });

  const cm = data?.current_metrics;
  const curveFamily = cm?.curve_family ?? params.curve_family ?? '—';
  const shortTenor = cm?.short_tenor ?? params.short_tenor ?? '';
  const longTenor = cm?.long_tenor ?? params.long_tenor ?? '';
  const caveat = countryCaveatFor(curveFamily);
  const pairLabel =
    shortTenor && longTenor ? spreadShortLabel(shortTenor, longTenor) : '';

  return (
    <BuildCompactShell
      toolDisplayName="Real-Yield Curve Spread"
      statusPill="CURVE"
      headerIcon={<GitCompareArrows size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: curveFamily,
        secondary: pairLabel,
        flag: caveat?.flag,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'SPREAD', value: '—' },
              { label: '1D CHANGE', value: '—' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitiseSpreadSeries(
        data?.time_series_spread?.rows ?? [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="%"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={CURVE_SPREAD_COMPACT_CAVEAT}
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
