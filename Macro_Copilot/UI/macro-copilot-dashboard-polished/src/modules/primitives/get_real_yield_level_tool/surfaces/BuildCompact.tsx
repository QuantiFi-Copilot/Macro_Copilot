// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for get_real_yield_level_tool.
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §2.2 + §5 every new
// primitive ships a compact view; this is real_yield_level's.  Mounted as
// a node body inside multi-tool query DAG visualizations OR when a multi-
// primitive Ask handoff places this tool in a side-by-side grid.
//
// Design reference: ./mockups/Compact.png (committed alongside this
// module per the user's mockup-first workflow).
//
// Slot contract: this file is ~80 lines of fetch + descriptor-mapping +
// shell composition.  All layout, tone semantics, methodology
// disclosure, and chart rendering live in the shared shells at
// @/components/shared/build/ — finance-blind, reusable across tools.
// ============================================================================

import { Activity } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  compactCaveatText,
  compactKPIs,
  buildReferenceBands,
  sanitiseTimeSeries,
  useRealYieldLevel,
} from './realYieldShared';
import { countryCaveatFor } from '@/components/shared/build';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const { data, isLoading, errorMessage } = useRealYieldLevel({
    curveFamily: params.curve_family ?? '',
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

  // Pre-data identity (before fetch returns) — pulled from params so the
  // header renders something useful during the loading state.
  const fallbackCurve = params.curve_family ?? '—';
  const fallbackTenor = params.tenor ?? '';
  const cm = data?.current_metrics;
  const curveFamily = cm?.curve_family ?? fallbackCurve;
  const tenor = cm?.tenor ?? fallbackTenor;
  const caveat = countryCaveatFor(curveFamily);

  return (
    <BuildCompactShell
      toolDisplayName="Real Yield Level"
      statusPill="SNAPSHOT"
      headerIcon={<Activity size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: curveFamily,
        secondary: tenor,
        flag: caveat?.flag,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'REAL YIELD', value: '—' },
              { label: '1D CHANGE', value: '—' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitiseTimeSeries(data?.time_series?.rows ?? []).map((r) => ({
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
