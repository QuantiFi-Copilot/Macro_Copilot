// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_breakeven_butterfly_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is the breakeven butterfly's.  Mounted as a node body inside
// multi-tool query DAG visualizations.  Design reference: ./mockups/Compact.png.
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
  BREAKEVEN_BUTTERFLY_COMPACT_CAVEAT,
  buildReferenceBands,
  compactKPIs,
  pairForLinker,
  sanitiseButterflySeries,
  tripletLabel,
  useBreakevenButterfly,
} from './breakevenButterflyShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const { data, isLoading, errorMessage } = useBreakevenButterfly({
    nominalCurveFamily: params.nominal_curve_family ?? '',
    linkerCurveFamily: params.linker_curve_family ?? '',
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
  const linkerFamily = cm?.linker_curve_family ?? params.linker_curve_family ?? '';
  const shortTenor = cm?.short_tenor ?? params.short_tenor ?? '';
  const bellyTenor = cm?.belly_tenor ?? params.belly_tenor ?? '';
  const longTenor = cm?.long_tenor ?? params.long_tenor ?? '';
  const pair = pairForLinker(linkerFamily);
  const caveat = countryCaveatFor(linkerFamily);
  const triplet =
    shortTenor && bellyTenor && longTenor
      ? tripletLabel(shortTenor, bellyTenor, longTenor)
      : '—';

  return (
    <BuildCompactShell
      toolDisplayName="Breakeven Butterfly"
      statusPill="SNAPSHOT"
      headerIcon={<Sparkles size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: pair ? `${pair.country} ${triplet} BE FLY` : `${linkerFamily || '—'} ${triplet} BE FLY`,
        secondary: pair ? `${pair.nominalShort} / ${pair.linkerShort}` : undefined,
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
      caveatText={BREAKEVEN_BUTTERFLY_COMPACT_CAVEAT}
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
