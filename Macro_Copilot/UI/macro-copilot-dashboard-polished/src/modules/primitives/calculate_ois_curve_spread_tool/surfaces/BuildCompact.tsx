// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_ois_curve_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is the OIS curve-spread's.  Mounted as a node body inside multi-
// tool query DAG visualizations.  Design reference: ./mockups/Compact.png.
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
  OIS_CURVE_SPREAD_COMPACT_CAVEAT,
  buildReferenceBands,
  compactKPIs,
  oisFamilyFor,
  sanitiseSpreadSeries,
  spreadShortLabel,
  useOisCurveSpread,
} from './oisCurveSpreadShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const { data, isLoading, errorMessage } = useOisCurveSpread({
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
  // The OIS curve-spread Output does NOT carry per-tenor strings (only the
  // combined ``spread_label``), so the per-tenor identity in the identity row
  // is reconstructed from the request params, not the wire response.
  // Distinct from the real-yield curve-spread compact view where the wire
  // ships ``cm.short_tenor`` / ``cm.long_tenor`` directly.
  const curveFamily = cm?.curve_family ?? params.curve_family ?? '';
  const shortTenor = params.short_tenor ?? '';
  const longTenor = params.long_tenor ?? '';
  const familyMeta = oisFamilyFor(curveFamily);
  const pairLabel =
    shortTenor && longTenor ? spreadShortLabel(shortTenor, longTenor) : '—';

  // Mockup-faithful identity row:
  //   primary:   "SOFR · 2s10s"  (overnight-index · pair)
  //   secondary: "SOFR 2Y → SOFR 10Y OIS Spread"
  const indexShort = familyMeta?.indexShort ?? curveFamily;
  const primary = familyMeta
    ? `${indexShort} · ${pairLabel}`
    : `${curveFamily || '—'} · ${pairLabel}`;
  const secondary = familyMeta && shortTenor && longTenor
    ? `${indexShort} ${shortTenor} → ${indexShort} ${longTenor} OIS Spread`
    : 'Overnight Index Swap curve spread';

  return (
    <BuildCompactShell
      toolDisplayName="OIS Curve Spread"
      statusPill="SNAPSHOT"
      headerIcon={<GitCompareArrows size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary,
        secondary,
        flag: familyMeta?.flag,
      }}
      kpis={
        data
          ? compactKPIs(data, shortTenor, longTenor)
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
      caveatText={OIS_CURVE_SPREAD_COMPACT_CAVEAT}
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
