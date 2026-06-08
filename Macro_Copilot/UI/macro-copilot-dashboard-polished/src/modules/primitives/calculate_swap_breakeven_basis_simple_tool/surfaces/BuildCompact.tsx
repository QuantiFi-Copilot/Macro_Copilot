// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_swap_breakeven_basis_simple_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is swap-breakeven basis's.  Mounted as a node body inside
// multi-tool query DAG visualizations.  Design reference: ./mockups/Compact.png.
//
// All layout / tone / chart / methodology-disclosure chrome lives in the
// shared shell at @/components/shared/build — finance-blind.  This file
// is fetch + descriptor-mapping + shell composition only.
//
// LOAD-BEARING wire-honesty: the inflation-swap basis is NOT a clean
// liquidity-premium read.  The compact view surfaces the wire's
// ``current_metrics.index_family_caveat`` verbatim when the legs reference
// different inflation indices; falls back to a static per-pair caveat
// otherwise.  Methodology disclosure is therefore REACHABLE in the compact
// surface per rendering_density.md §2.2 + §12 — never hidden.
// ============================================================================

import { TrendingUp } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  compactKPIs,
  buildReferenceBands,
  fallbackBasisCaveat,
  pairForZcisFamily,
  sanitiseBasisSeries,
  useSwapBreakevenBasis,
} from './swapBreakevenBasisShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const { data, isLoading, errorMessage } = useSwapBreakevenBasis({
    zcisCurveFamily: params.zcis_curve_family ?? '',
    nominalCurveFamily: params.nominal_curve_family ?? '',
    linkerCurveFamily: params.linker_curve_family ?? '',
    tenor: params.tenor ?? '',
    lookbackDays:
      params.lookback_days != null ? Number(params.lookback_days) : undefined,
    fieldName: params.field_name || undefined,
  });

  const cm = data?.current_metrics;
  const zcisFamily = cm?.zcis_curve_family ?? params.zcis_curve_family ?? '';
  const tenor = cm?.tenor ?? params.tenor ?? '';
  const pair = pairForZcisFamily(zcisFamily);

  // Prefer the wire's structured caveat (it names the resolved per-leg
  // index families verbatim — methodology_exposure §1 single source of
  // truth); fall back to the per-pair static line when the wire has not
  // resolved yet (loading) or the legs match families.
  const caveatLine = cm?.index_family_caveat ?? fallbackBasisCaveat(pair);

  return (
    <BuildCompactShell
      toolDisplayName="Swap-Breakeven Basis"
      statusPill="SNAPSHOT"
      headerIcon={<TrendingUp size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: pair
          ? `${pair.country} · ${pair.zcisIndexShort} SWAP-BE BASIS`
          : (zcisFamily || '—'),
        secondary: tenor || undefined,
        flag: pair?.flag,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'BASIS', value: '—', unit: 'bp' },
              { label: '1D CHANGE', value: '—', unit: 'bp' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitiseBasisSeries(
        data?.time_series_basis?.rows ?? [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="bp"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={caveatLine}
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
