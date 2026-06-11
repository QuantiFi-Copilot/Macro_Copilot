// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_rolling_regression_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every primitive ships a compact
// view; this is rolling regression's grid card, mounted as a node body
// inside multi-tool query DAG visualizations.
//
// All layout / tone / chart / methodology-disclosure chrome lives in
// the shared shell at @/components/shared/build — finance-blind.  This
// file is fetch + descriptor-mapping + shell composition only.
//
// Curated read (NOT a shrink-to-fit of the extended view):
//   identity  — "{target_label} ~ N regressors"
//   3 KPIs    — β (first regressor) / R² / residual
//   sparkline — the FIRST beta series (the hedge-ratio drift is the
//               compact-card signal; the full per-regressor set lives
//               in the extended view)
//   caveat    — the rolling-window honesty one-liner
// ============================================================================

import { Activity } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  ROLLING_REGRESSION_COMPACT_CAVEAT,
  compactKPIs,
  firstBetaChartPoints,
  resolveRollingRegressionParams,
  useRollingRegressionData,
} from './rollingRegressionShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const resolved = resolveRollingRegressionParams(params);
  const { data, isLoading, errorMessage } = useRollingRegressionData(resolved);

  const cm = data?.current_metrics;
  const regressorCount =
    cm?.regressor_labels.length ?? resolved.regressorCurveFamilies.length;
  const identityPrimary =
    cm?.target_label
    ?? `${resolved.targetCurveFamily}_${resolved.targetTenor}`;

  return (
    <BuildCompactShell
      toolDisplayName="Rolling Regression"
      statusPill="ROLLING OLS"
      headerIcon={
        <Activity size={12} strokeWidth={1.75} className="text-ice-300" />
      }
      identity={{
        primary: identityPrimary,
        secondary: `~ ${regressorCount} regressor${regressorCount === 1 ? '' : 's'}`,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'β', value: '—' },
              { label: 'R²', value: '—' },
              { label: 'RESIDUAL', value: '—' },
            ]
      }
      chartPoints={firstBetaChartPoints(data)}
      chartUnit={data?.time_series_betas?.[0]?.units ?? 'ratio'}
      caveatText={ROLLING_REGRESSION_COMPACT_CAVEAT}
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
