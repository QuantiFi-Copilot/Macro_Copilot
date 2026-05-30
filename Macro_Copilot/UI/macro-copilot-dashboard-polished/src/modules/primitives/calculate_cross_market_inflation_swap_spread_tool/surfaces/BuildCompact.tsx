// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_cross_market_inflation_swap_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is cross-market ZCIS's.  Mounted as a node body inside multi-
// tool query DAG visualizations.  Design reference: ./mockups/Compact.png.
//
// All layout / tone / chart / methodology-disclosure chrome lives in the
// shared shell at @/components/shared/build — finance-blind.  This file
// is fetch + descriptor-mapping + shell composition only.
// ============================================================================

import { TrendingUp } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  compactKPIs,
  buildReferenceBands,
  pairShortLabel,
  sanitiseSpreadSeries,
  shortIndexCaveat,
  useCrossMarketZcisSpread,
  zcisFamilyFor,
} from './crossMarketZcisShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const { data, isLoading, errorMessage } = useCrossMarketZcisSpread({
    legACurveFamily: params.leg_a_curve_family ?? '',
    legBCurveFamily: params.leg_b_curve_family ?? '',
    tenor: params.tenor ?? '',
    lookbackDays:
      params.lookback_days != null ? Number(params.lookback_days) : undefined,
    fieldName: params.field_name || undefined,
  });

  const cm = data?.current_metrics;
  const legA = cm?.leg_a_curve_family ?? params.leg_a_curve_family ?? '';
  const legB = cm?.leg_b_curve_family ?? params.leg_b_curve_family ?? '';
  const tenor = cm?.tenor ?? params.tenor ?? '';
  const aMeta = zcisFamilyFor(legA);
  const bMeta = zcisFamilyFor(legB);
  const pairLabel = pairShortLabel(legA, legB);

  // Prefer the wire's structured caveat (it names the resolved per-leg
  // index families verbatim — methodology_exposure §1 single source of
  // truth); fall back to a static computed string when the wire has not
  // resolved yet (loading) or the legs happen to share a family (rare
  // in V1 — every USD/EUR/GBP pair has distinct families).
  const caveatLine = cm?.index_family_caveat ?? shortIndexCaveat(legA, legB);

  // Pair-chip footer combines flags + the short pair label
  // (mockup Compact.png footer-right: flag · USD-EUR).
  const pairFlags = aMeta && bMeta ? `${aMeta.flag} ${bMeta.flag}` : '';

  return (
    <BuildCompactShell
      toolDisplayName="Cross-Market Inflation Swap Spread"
      statusPill="SNAPSHOT"
      headerIcon={<TrendingUp size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: `${pairLabel} ${tenor} ZCIS`.trim() || '—',
        secondary: aMeta && bMeta ? `${aMeta.indexShort} vs ${bMeta.indexShort}` : undefined,
        flag: pairFlags || undefined,
      }}
      kpis={
        data
          ? compactKPIs(data)
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
