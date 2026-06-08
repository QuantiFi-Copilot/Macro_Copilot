// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_forward_breakeven_simple_tool.
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §2.2 + §5 every new
// primitive ships a compact view; this is the forward breakeven's.  Mounted
// as a node body inside multi-tool query DAG visualizations (e.g. "compare
// US 5Y5Y BE vs UK 5Y5Y BE vs French 5Y5Y BE") OR when a multi-primitive
// Ask handoff places this tool in a side-by-side grid.
//
// Design reference: ./mockups/Compact.png (committed alongside this module).
//
// Slot contract: this file is a thin fetch + descriptor-mapping + shell
// composition wrapper.  All layout, tone semantics, methodology disclosure,
// and chart rendering live in the shared shells at @/components/shared/build/
// — finance-blind, reusable across tools.
// ============================================================================

import { TrendingUp } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  FORWARD_BREAKEVEN_COMPACT_CAVEAT,
  buildReferenceBands,
  compactKPIs,
  forwardPairFor,
  forwardPairLabel,
  pairForLinker,
  sanitiseForwardBreakevenSeries,
  useForwardBreakeven,
} from './forwardBreakevenShared';

const DEFAULT_FORWARD_PAIR = '5Y5Y';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  // Resolve the (start_tenor, end_tenor) pair from either the canonical
  // forward_pair label or the explicit start/end tenors the URL may carry
  // (the extended view writes both).
  const forwardPairCode =
    (params.forward_pair as string | undefined) ?? DEFAULT_FORWARD_PAIR;
  const pair = forwardPairFor(forwardPairCode);
  const startTenor = params.start_tenor || pair?.startTenor || '5Y';
  const endTenor = params.end_tenor || pair?.endTenor || '10Y';

  const { data, isLoading, errorMessage } = useForwardBreakeven({
    nominalCurveFamily: params.nominal_curve_family ?? '',
    linkerCurveFamily: params.linker_curve_family ?? '',
    startTenor,
    endTenor,
    lookbackDays:
      params.lookback_days != null ? Number(params.lookback_days) : undefined,
    fieldName: params.field_name || undefined,
  });

  const cm = data?.current_metrics;
  const linkerFamily = cm?.linker_curve_family ?? params.linker_curve_family ?? '';
  const pairMeta = pairForLinker(linkerFamily);
  const effectiveStart = cm?.start_tenor ?? startTenor;
  const effectiveEnd = cm?.end_tenor ?? endTenor;
  const effectivePairLabel = forwardPairLabel(effectiveStart, effectiveEnd);

  return (
    <BuildCompactShell
      toolDisplayName="Forward Breakeven Inflation"
      statusPill="SNAPSHOT"
      headerIcon={<TrendingUp size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: pairMeta
          ? `${pairMeta.country} · ${effectivePairLabel} BE`
          : `${linkerFamily} ${effectivePairLabel} BE`,
        secondary: pairMeta ? `${pairMeta.nominalShort}/${pairMeta.linkerShort}` : effectivePairLabel,
        flag: pairMeta?.flag,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: `FORWARD BE (${effectivePairLabel})`, value: '—', unit: 'bp' },
              { label: '1D CHANGE', value: '—', unit: 'bp' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitiseForwardBreakevenSeries(data?.time_series ?? []).map((r) => ({
        date: r.date,
        value: r.value ?? NaN,
      }))}
      chartUnit="bp"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={FORWARD_BREAKEVEN_COMPACT_CAVEAT}
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
