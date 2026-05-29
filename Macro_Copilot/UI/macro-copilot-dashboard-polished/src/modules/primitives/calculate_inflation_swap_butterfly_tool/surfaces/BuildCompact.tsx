// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_inflation_swap_butterfly_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is the ZCIS butterfly's.  Mounted as a node body inside multi-
// tool query DAG visualizations.  Design reference: ./mockups/Compact.png.
//
// All layout / tone / chart / methodology-disclosure chrome lives in the
// shared shell at @/components/shared/build — finance-blind.  This file
// is fetch + descriptor-mapping + shell composition only.
// ============================================================================

import { Sparkles } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  INFLATION_SWAP_BUTTERFLY_COMPACT_CAVEAT,
  buildReferenceBands,
  compactKPIs,
  sanitiseButterflySeries,
  tripletHyphenLabel,
  useInflationSwapButterfly,
  zcisFamilyFor,
} from './inflationSwapButterflyShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const { data, isLoading, errorMessage } = useInflationSwapButterfly({
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
  const curveFamily = cm?.curve_family ?? params.curve_family ?? '';
  const shortTenor = cm?.short_tenor ?? params.short_tenor ?? '';
  const bellyTenor = cm?.belly_tenor ?? params.belly_tenor ?? '';
  const longTenor = cm?.long_tenor ?? params.long_tenor ?? '';
  const familyMeta = zcisFamilyFor(curveFamily);
  const triplet =
    shortTenor && bellyTenor && longTenor
      ? tripletHyphenLabel(shortTenor, bellyTenor, longTenor)
      : '—';

  // Mockup-faithful identity row:
  //   primary:   "USD ZCIS · 2-5-10 FLY · CPI-U"
  //   secondary: "USD_ZCIS_CPIU · Zero-Coupon Inflation Swap"
  const marketShort = familyMeta?.marketShort ?? curveFamily;
  const indexShort = familyMeta?.indexShort ?? cm?.inflation_index_family ?? '';
  const primary = familyMeta
    ? `${marketShort} ZCIS · ${triplet} FLY · ${indexShort}`
    : `${curveFamily || '—'} · ${triplet} FLY`;
  const secondary = familyMeta
    ? `${marketShort}_ZCIS_${indexShort.replace('-', '')} · Zero-Coupon Inflation Swap`
    : 'Zero-Coupon Inflation Swap';

  return (
    <BuildCompactShell
      toolDisplayName="Inflation Swap Butterfly"
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
              { label: 'FLY (BPS)', value: '—', unit: 'bp' },
              { label: '1D CHANGE', value: '—', unit: 'bp' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitiseButterflySeries(
        data?.time_series_butterfly?.rows ?? [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="bp"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={INFLATION_SWAP_BUTTERFLY_COMPACT_CAVEAT}
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
