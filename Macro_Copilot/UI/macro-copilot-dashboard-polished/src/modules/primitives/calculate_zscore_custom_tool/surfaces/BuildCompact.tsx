// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_zscore_custom_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every primitive claiming
// ``custom_build_surface`` ships a compact view; this is the custom-window
// z-score's.  Mounted as a node body inside multi-tool query DAG
// visualizations (e.g. "60d vs 252d z on UST 10Y" → two cards).
//
// All layout / tone / chart / methodology-disclosure chrome lives in the
// shared shell at @/components/shared/build — finance-blind.  This file
// is fetch + descriptor-mapping + shell composition only.
// ============================================================================

import { Activity } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  COMPACT_PLACEHOLDER_KPIS,
  ZSCORE_REFERENCE_BANDS,
  compactCaveat,
  compactKPIs,
  familyForCurve,
  useZscoreCustomData,
  zscoreSeriesPoints,
} from './zscoreCustomShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const { data, isLoading, errorMessage } = useZscoreCustomData({
    curveFamily: params.curve_family ?? '',
    tenor: params.tenor ?? '',
    // The central knob is REQUIRED on the wire — NaN (missing param)
    // short-circuits the hook into the empty state rather than firing
    // a 422 round-trip.
    zScoreWindowDays:
      params.z_score_window_days != null && params.z_score_window_days !== ''
        ? Number(params.z_score_window_days)
        : NaN,
    lookbackDays:
      params.lookback_days != null && params.lookback_days !== ''
        ? Number(params.lookback_days)
        : undefined,
    fieldName: params.field_name || undefined,
  });

  const cm = data?.current_metrics;
  const curveFamily = cm?.curve_family ?? params.curve_family ?? '';
  const tenor = cm?.tenor ?? params.tenor ?? '';
  const windowLabel =
    cm?.z_score_window_days_used ?? params.z_score_window_days ?? '—';
  const meta = familyForCurve(curveFamily);

  return (
    <BuildCompactShell
      toolDisplayName="Z-Score Custom"
      statusPill="ROLLING"
      headerIcon={<Activity size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: meta ? `${meta.country} · ${meta.curveShort}` : (curveFamily || '—'),
        secondary: `${tenor} · ${windowLabel}d window`,
        flag: meta?.flag,
      }}
      kpis={data ? compactKPIs(data) : COMPACT_PLACEHOLDER_KPIS}
      chartPoints={zscoreSeriesPoints(data)}
      chartUnit="σ"
      referenceBands={data ? ZSCORE_REFERENCE_BANDS : []}
      caveatText={compactCaveat(data)}
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
