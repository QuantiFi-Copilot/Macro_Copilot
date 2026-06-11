// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_pca_yield_curve_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every primitive claiming
// custom_build_surface ships a compact view; this is PCA's.  Mounted as
// a node body inside multi-tool query DAG visualizations.
//
// LEVEL-shaped compact: the headline read is three KPIs (variance
// explained / current PC1 level / component count) + the pc1
// factor-score sparkline — the first factor is the fit's dominant
// story, and the full loadings matrix belongs to the extended view.
// All layout / tone / chart / methodology-disclosure chrome lives in
// the shared BuildCompactShell — finance-blind.  This file is fetch +
// descriptor-mapping + shell composition only.
// ============================================================================

import { Boxes } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  PCA_COMPACT_CAVEAT,
  compactKPIs,
  parseOptionalNumber,
  pc1ChartPoints,
  usePcaYieldCurveData,
} from './pcaYieldCurveShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const { data, isLoading, errorMessage } = usePcaYieldCurveData({
    curveFamily: params.curve_family ?? '',
    tenorsCsv: params.tenors,
    lookbackDays: parseOptionalNumber(params.lookback_days),
    nComponents: parseOptionalNumber(params.n_components),
    changeFrequency: params.change_frequency,
    fieldName: params.field_name || undefined,
  });

  const cm = data?.current_metrics;
  const family = cm?.curve_family ?? params.curve_family ?? '—';
  const freq = cm?.change_frequency_used ?? params.change_frequency ?? 'daily';

  return (
    <BuildCompactShell
      toolDisplayName="PCA · Yield Curve"
      statusPill="MODEL FIT"
      headerIcon={<Boxes size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: `${family} PCA`,
        secondary: cm
          ? `${cm.n_components_returned} comps · ${freq}`
          : freq,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'VAR EXPLAINED', value: '—' },
              { label: 'PC1 LEVEL', value: '—' },
              { label: 'COMPONENTS', value: '—' },
            ]
      }
      chartPoints={data ? pc1ChartPoints(data) : []}
      chartUnit=""
      caveatText={PCA_COMPACT_CAVEAT}
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
