// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_cross_market_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every primitive ships a compact view;
// this is the same-tenor cross-market sovereign spread's.  Mounted as a
// node body inside multi-tool query DAG visualizations.  Design reference:
// ./mockups/Compact.png.
//
// All layout / tone / chart / methodology-disclosure chrome lives in the
// shared shell at @/components/shared/build — finance-blind.  This file is
// fetch + descriptor-mapping + shell composition only.
// ============================================================================

import { TrendingUp } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  CROSS_MARKET_COMPACT_CAVEAT,
  buildReferenceBands,
  compactKPIs,
  pairShortLabel,
  pairSubtitle,
  sanitiseSpreadSeries,
  sovereignFamilyFor,
  spreadChartRows,
  useCrossMarketSpread,
} from './crossMarketSpreadShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const { data, isLoading, errorMessage } = useCrossMarketSpread({
    curveFamily1: params.curve_family_1 ?? '',
    curveFamily2: params.curve_family_2 ?? '',
    tenor: params.tenor ?? '',
    lookbackDays:
      params.lookback_days != null && params.lookback_days !== ''
        ? Number(params.lookback_days)
        : undefined,
    fieldName: params.field_name || undefined,
  });

  const cm = data?.current_metrics;
  const cf1 = cm?.curve_family_1 ?? params.curve_family_1 ?? '';
  const cf2 = cm?.curve_family_2 ?? params.curve_family_2 ?? '';
  const tenor = cm?.tenor ?? params.tenor ?? '';
  const aMeta = sovereignFamilyFor(cf1);
  const bMeta = sovereignFamilyFor(cf2);
  const pairLabel = pairShortLabel(cf1, cf2);

  // Mockup-faithful identity row:
  //   primary:   "UST-BUND • 10Y"
  //   secondary: "UST_10Y − DE_BUND_10Y"
  //   flags:     🇺🇸 / 🇩🇪
  const primary = tenor ? `${pairLabel} · ${tenor}` : pairLabel || '—';
  const secondary = cf1 && cf2 && tenor ? pairSubtitle(cf1, cf2, tenor) : undefined;
  const flag =
    aMeta && bMeta ? `${aMeta.flag} ${bMeta.flag}` : aMeta?.flag ?? bMeta?.flag;

  return (
    <BuildCompactShell
      toolDisplayName="Cross-Market Spread"
      statusPill="SNAPSHOT"
      headerIcon={<TrendingUp size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary,
        secondary,
        flag,
      }}
      kpis={
        data
          ? compactKPIs(data, tenor)
          : [
              { label: `SPREAD${tenor ? ` (${tenor})` : ''}`, value: '—', unit: 'bp' },
              { label: '1D CHANGE', value: '—', unit: 'bp' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitiseSpreadSeries(
        data ? spreadChartRows(data) : [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="bp"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={CROSS_MARKET_COMPACT_CAVEAT}
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
