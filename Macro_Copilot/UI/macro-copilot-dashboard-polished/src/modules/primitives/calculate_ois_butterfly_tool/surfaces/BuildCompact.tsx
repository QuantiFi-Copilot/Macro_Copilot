// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_ois_butterfly_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is the OIS butterfly's.  Mounted as a node body inside multi-
// tool query DAG visualizations.  Design reference: ./mockups/Compact.png.
//
// All layout / tone / chart / methodology-disclosure chrome lives in the
// shared shell at @/components/shared/build — finance-blind.  This file is
// fetch + descriptor-mapping + shell composition only.
// ============================================================================

import { Sparkles } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  OIS_BUTTERFLY_COMPACT_CAVEAT,
  buildReferenceBands,
  compactKPIs,
  oisFamilyFor,
  sanitiseButterflySeries,
  tripletHyphenLabel,
  useOisButterfly,
} from './oisButterflyShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const { data, isLoading, errorMessage } = useOisButterfly({
    curveFamily: params.curve_family ?? '',
    shortTenor: params.short_tenor ?? '',
    bellyTenor: params.belly_tenor ?? '',
    longTenor: params.long_tenor ?? '',
    lookbackDays:
      params.lookback_days != null && params.lookback_days !== ''
        ? Number(params.lookback_days)
        : undefined,
    fieldName: params.field_name || undefined,
  });

  const cm = data?.current_metrics;
  // The OIS butterfly Output does NOT carry per-tenor strings (only the
  // ``butterfly_label``), so the per-tenor identity in the identity row is
  // reconstructed from the request params, not the wire response.  Distinct
  // from the inflation_swap_butterfly compact view where the wire ships
  // ``cm.short_tenor`` / ``cm.belly_tenor`` / ``cm.long_tenor`` directly.
  const curveFamily = cm?.curve_family ?? params.curve_family ?? '';
  const shortTenor = params.short_tenor ?? '';
  const bellyTenor = params.belly_tenor ?? '';
  const longTenor = params.long_tenor ?? '';
  const familyMeta = oisFamilyFor(curveFamily);
  const triplet =
    shortTenor && bellyTenor && longTenor
      ? tripletHyphenLabel(shortTenor, bellyTenor, longTenor)
      : '—';

  // Mockup-faithful identity row:
  //   primary:   "SOFR · 2-5-10 OIS FLY"
  //   secondary: "USD SOFR OIS Curve Curvature"
  const indexShort = familyMeta?.indexShort ?? curveFamily;
  const marketShort = familyMeta?.marketShort ?? '';
  const primary = familyMeta
    ? `${indexShort} · ${triplet} OIS FLY`
    : `${curveFamily || '—'} · ${triplet} FLY`;
  const secondary = familyMeta
    ? `${marketShort} ${indexShort} OIS Curve Curvature`
    : 'Overnight Index Swap Curve Curvature';

  return (
    <BuildCompactShell
      toolDisplayName="OIS Butterfly"
      statusPill="SNAPSHOT"
      headerIcon={<Sparkles size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary,
        secondary,
        flag: familyMeta?.flag,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'FLY (bps)', value: '—', unit: 'bps' },
              { label: '1D CHANGE', value: '—', unit: 'bps' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitiseButterflySeries(
        data?.time_series_butterfly?.rows ?? [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="bps"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={OIS_BUTTERFLY_COMPACT_CAVEAT}
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
