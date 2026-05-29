// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_real_yield_butterfly_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is the linker real-yield butterfly's.  Mounted as a node body
// inside multi-tool query DAG visualizations.  Design reference:
// ./mockups/Compact.png.
//
// All layout / tone / chart / methodology-disclosure chrome lives in the
// shared shell at @/components/shared/build — finance-blind.  This file
// is fetch + descriptor-mapping + shell composition only.
// ============================================================================

import { Sparkles } from 'lucide-react';
import {
  BuildCompactShell,
  countryCaveatFor,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  REAL_YIELD_BUTTERFLY_COMPACT_CAVEAT,
  buildReferenceBands,
  compactKPIs,
  curveForFamily,
  sanitiseButterflySeries,
  tripletHyphenLabel,
  useRealYieldButterfly,
} from './realYieldButterflyShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const { data, isLoading, errorMessage } = useRealYieldButterfly({
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
  const curve = curveForFamily(curveFamily);
  const caveat = countryCaveatFor(curveFamily);
  const triplet =
    shortTenor && bellyTenor && longTenor
      ? tripletHyphenLabel(shortTenor, bellyTenor, longTenor)
      : '—';

  return (
    <BuildCompactShell
      toolDisplayName="Real Yield Butterfly"
      statusPill="SNAPSHOT"
      headerIcon={<Sparkles size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: `${curveFamily || '—'} ${triplet} RY FLY`,
        secondary: curve ? `${curve.shortLabel} (single linker curve)` : 'single linker curve',
        flag: caveat?.flag,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'FLY', value: '—' },
              { label: '1D CHANGE', value: '—' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitiseButterflySeries(
        data?.time_series_butterfly?.rows ?? [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="bp"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={REAL_YIELD_BUTTERFLY_COMPACT_CAVEAT}
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
