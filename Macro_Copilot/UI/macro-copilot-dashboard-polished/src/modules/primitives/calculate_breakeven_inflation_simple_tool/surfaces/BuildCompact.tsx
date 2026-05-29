// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_breakeven_inflation_simple_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is breakeven's.  Mounted as a node body inside multi-tool
// query DAG visualizations.  Design reference: ./mockups/Compact.png.
//
// All layout / tone / chart / methodology-disclosure chrome lives in the
// shared shell at @/components/shared/build — finance-blind.  This file
// is fetch + descriptor-mapping + shell composition only.
// ============================================================================

import { TrendingUp } from 'lucide-react';
import {
  BuildCompactShell,
  countryCaveatFor,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  BREAKEVEN_COMPACT_CAVEAT,
  compactKPIs,
  buildReferenceBands,
  pairForLinker,
  sanitiseBreakevenSeries,
  useBreakevenInflation,
} from './breakevenShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const { data, isLoading, errorMessage } = useBreakevenInflation({
    nominalCurveFamily: params.nominal_curve_family ?? '',
    linkerCurveFamily: params.linker_curve_family ?? '',
    tenor: params.tenor ?? '',
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
  const linkerFamily = cm?.linker_curve_family ?? params.linker_curve_family ?? '';
  const tenor = cm?.tenor ?? params.tenor ?? '';
  const pair = pairForLinker(linkerFamily);
  const caveat = countryCaveatFor(linkerFamily);

  return (
    <BuildCompactShell
      toolDisplayName="Breakeven Inflation"
      statusPill="SNAPSHOT"
      headerIcon={<TrendingUp size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: pair ? `${pair.country} · ${pair.nominalShort}/${pair.linkerShort}` : (linkerFamily || '—'),
        secondary: tenor,
        flag: caveat?.flag,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'BREAKEVEN', value: '—' },
              { label: '1D CHANGE', value: '—' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitiseBreakevenSeries(
        data?.time_series_breakeven?.rows ?? [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="bp"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={BREAKEVEN_COMPACT_CAVEAT}
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
