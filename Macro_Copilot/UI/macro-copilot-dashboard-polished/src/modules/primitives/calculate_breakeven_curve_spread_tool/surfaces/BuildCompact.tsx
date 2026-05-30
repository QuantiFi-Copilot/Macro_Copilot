// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_breakeven_curve_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is the breakeven curve spread's.  Mounted as a node body
// inside multi-tool query DAG visualizations.  Design reference:
// ./mockups/Compact.png.
//
// All layout / tone / chart / methodology-disclosure chrome lives in the
// shared shell at @/components/shared/build — finance-blind.  This file
// is fetch + descriptor-mapping + shell composition only.
// ============================================================================

import { GitCompareArrows } from 'lucide-react';
import {
  BuildCompactShell,
  countryCaveatFor,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  BREAKEVEN_CURVE_SPREAD_COMPACT_CAVEAT,
  buildReferenceBands,
  compactKPIs,
  pairForLinker,
  sanitiseSpreadSeries,
  spreadShortLabel,
  useBreakevenCurveSpread,
} from './breakevenCurveSpreadShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const { data, isLoading, errorMessage } = useBreakevenCurveSpread({
    nominalCurveFamily: params.nominal_curve_family ?? '',
    linkerCurveFamily: params.linker_curve_family ?? '',
    shortTenor: params.short_tenor ?? '',
    longTenor: params.long_tenor ?? '',
    lookbackDays:
      params.lookback_days != null && params.lookback_days !== ''
        ? Number(params.lookback_days)
        : undefined,
    fieldName: params.field_name || undefined,
  });

  const cm = data?.current_metrics;
  const linkerFamily = cm?.linker_curve_family ?? params.linker_curve_family ?? '';
  const shortTenor = cm?.short_tenor ?? params.short_tenor ?? '';
  const longTenor = cm?.long_tenor ?? params.long_tenor ?? '';
  const pair = pairForLinker(linkerFamily);
  const caveat = countryCaveatFor(linkerFamily);
  const pairLabel =
    shortTenor && longTenor
      ? spreadShortLabel(shortTenor, longTenor)
      : '—';

  return (
    <BuildCompactShell
      toolDisplayName="Breakeven Curve Spread"
      statusPill="SNAPSHOT"
      headerIcon={<GitCompareArrows size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: pair
          ? `${pair.country} ${pairLabel} BE SPREAD`
          : `${linkerFamily || '—'} ${pairLabel} BE SPREAD`,
        secondary: pair
          ? `${pair.nominalShort} / ${pair.linkerShort}  ${shortTenor} − ${longTenor}`
          : `${shortTenor} − ${longTenor}`,
        flag: caveat?.flag,
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
      caveatText={BREAKEVEN_CURVE_SPREAD_COMPACT_CAVEAT}
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
