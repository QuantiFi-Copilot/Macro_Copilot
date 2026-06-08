// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_inflation_swap_curve_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is the ZCIS curve spread's.  Mounted as a node body inside
// multi-tool query DAG visualizations.  Design reference: ./mockups/Compact.png.
//
// All layout / tone / chart / methodology-disclosure chrome lives in the
// shared shell at @/components/shared/build — finance-blind.  This file
// is fetch + descriptor-mapping + shell composition only.
// ============================================================================

import { GitCompareArrows } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  ZCIS_CURVE_SPREAD_COMPACT_CAVEAT,
  buildReferenceBands,
  compactKPIs,
  sanitiseSpreadSeries,
  spreadShortLabel,
  useInflationSwapCurveSpread,
  zcisCurveFamilyFor,
} from './inflationSwapCurveSpreadShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const { data, isLoading, errorMessage } = useInflationSwapCurveSpread({
    curveFamily: params.curve_family ?? '',
    shortTenor: params.short_tenor ?? '',
    longTenor: params.long_tenor ?? '',
    lookbackDays:
      params.lookback_days != null && params.lookback_days !== ''
        ? Number(params.lookback_days)
        : undefined,
    fieldName: params.field_name || undefined,
  });

  const cm = data?.current_metrics;
  const curveFamily = cm?.curve_family ?? params.curve_family ?? '';
  const shortTenor = cm?.short_tenor ?? params.short_tenor ?? '';
  const longTenor = cm?.long_tenor ?? params.long_tenor ?? '';
  const meta = zcisCurveFamilyFor(curveFamily);
  const pairLabel =
    shortTenor && longTenor
      ? spreadShortLabel(shortTenor, longTenor)
      : '—';

  return (
    <BuildCompactShell
      toolDisplayName="ZCIS Curve Spread"
      statusPill="SNAPSHOT"
      headerIcon={<GitCompareArrows size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: meta
          ? `${meta.country} ZCIS · ${pairLabel}`
          : `${curveFamily || '—'} ${pairLabel}`,
        secondary: meta
          ? `${meta.marketShort} · ${meta.indexShort}  ${shortTenor} − ${longTenor}`
          : `${shortTenor} − ${longTenor}`,
        flag: meta?.flag,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: `SPREAD (${pairLabel})`, value: '—' },
              { label: '1D CHANGE', value: '—' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitiseSpreadSeries(
        data?.time_series_spread?.rows ?? [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="bp"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={ZCIS_CURVE_SPREAD_COMPACT_CAVEAT}
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
