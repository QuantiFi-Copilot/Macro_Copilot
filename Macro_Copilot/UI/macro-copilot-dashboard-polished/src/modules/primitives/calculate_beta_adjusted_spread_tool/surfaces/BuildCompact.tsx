// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_beta_adjusted_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every primitive ships a compact
// view; this is the beta-adjusted spread's grid card.  Mounted as a node
// body inside multi-tool query DAG visualizations.
//
// All layout / tone / chart / methodology-disclosure chrome lives in the
// shared shell at @/components/shared/build — finance-blind.  This file
// is fetch + descriptor-mapping + shell composition only.  The sparkline
// is the RESIDUAL Z series (the stretch read a PM acts on), not the raw
// spread — THESIS Q3 documents the choice.
// ============================================================================

import { Sigma } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  BETA_ADJUSTED_COMPACT_CAVEAT,
  compactKPIs,
  residualZChartPoints,
  resolveBetaAdjustedSpreadParams,
  useBetaAdjustedSpreadData,
} from './betaAdjustedSpreadShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const resolved = resolveBetaAdjustedSpreadParams(params);
  const { data, isLoading, errorMessage } = useBetaAdjustedSpreadData(resolved);

  const cm = data?.current_metrics;

  return (
    <BuildCompactShell
      toolDisplayName="Beta-Adjusted Spread"
      statusPill="MODEL FIT"
      headerIcon={<Sigma size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary:
          cm?.spread_label ??
          `${resolved.targetCurveFamily} ${resolved.targetTenor} vs ${resolved.regressorCurveFamily} ${resolved.regressorTenor}`,
        secondary: `${resolved.regressionWindowDays}d fit`,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'RESIDUAL Z', value: '—' },
              { label: 'RESIDUAL', value: '—' },
              { label: 'β', value: '—' },
            ]
      }
      chartPoints={residualZChartPoints(data)}
      chartUnit="σ"
      caveatText={BETA_ADJUSTED_COMPACT_CAVEAT}
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
