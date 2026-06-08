// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_ois_cross_market_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is the cross-market OIS spread's.  Mounted as a node body inside
// multi-tool query DAG visualizations.  Design reference: ./mockups/Compact.png.
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
  OIS_CROSS_MARKET_COMPACT_CAVEAT,
  buildReferenceBands,
  compactKPIs,
  indexPairShortLabel,
  oisFamilyFor,
  pairCentralBankCaption,
  pairShortLabel,
  sanitiseSpreadSeries,
  useCrossMarketOisSpread,
} from './crossMarketOisShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const { data, isLoading, errorMessage } = useCrossMarketOisSpread({
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
  const legA = cm?.curve_family_1 ?? params.curve_family_1 ?? '';
  const legB = cm?.curve_family_2 ?? params.curve_family_2 ?? '';
  const tenor = cm?.tenor ?? params.tenor ?? '';
  const aMeta = oisFamilyFor(legA);
  const bMeta = oisFamilyFor(legB);
  const indexPair = indexPairShortLabel(legA, legB);
  const pairLabel = pairShortLabel(legA, legB);

  // Mockup-faithful identity row (Compact.png):
  //   primary:   "SOFR-ESTR · 2Y"
  //   secondary: "Fed vs ECB policy-rate differential"
  //   flag:      "🇺🇸 🇪🇺"
  const primary =
    indexPair && tenor ? `${indexPair} · ${tenor}` : `${pairLabel || '—'} · ${tenor || '—'}`;
  const secondary = pairCentralBankCaption(legA, legB);
  const pairFlags = aMeta && bMeta ? `${aMeta.flag} ${bMeta.flag}` : undefined;

  return (
    <BuildCompactShell
      toolDisplayName="Cross-Market OIS Spread"
      statusPill="SNAPSHOT"
      headerIcon={<GitCompareArrows size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary,
        secondary,
        flag: pairFlags,
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
      caveatText={OIS_CROSS_MARKET_COMPACT_CAVEAT}
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
