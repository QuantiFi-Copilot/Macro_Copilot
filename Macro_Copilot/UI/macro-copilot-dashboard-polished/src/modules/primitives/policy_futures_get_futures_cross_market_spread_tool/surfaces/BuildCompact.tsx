// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// policy_futures_get_futures_cross_market_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is the cross-market STIR spread's.  Mounted as a node body
// inside multi-tool query DAG visualizations (e.g. *"compare SFR-SFI 1
// vs SFR-ER 1 vs SFI-ER 1 front-quarter cross-CB divergence"*).
//
// Design reference: ./mockups/Compact.png.  Mockup-faithful identity row:
//   primary:   "SFR-ER · pos1 · WHITES"      (master stems + strip + segment)
//   secondary: "Front-Quarter Cross-CB Spread (Implied Rate)"
//
// Per the Option-(c) precedent (Batch 1 fdac7d2 +
// policy_futures_get_futures_price_level_tool d8e8233 + tool 19 fcc5381 +
// tool 20 be2595b) this file uses the SHELL-STANDARD density (3 KPIs +
// single identity line + sparkline + caveat + expand).  The mockup's
// denser layout — the dual flag chip + WHITES segment tag in the
// identity row plus the dual-band sparkline annotations — is preserved
// visually; promoting additional KPI cells into the compact view would
// require extending BuildCompactShell with a ``secondaryKpis`` slot,
// touching 10+ shipped compact views.  See THESIS.md → Mockup
// conformance for the deferral rationale.
// ============================================================================

import { ArrowLeftRight } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  buildReferenceBands,
  compactCaveatFor,
  compactKPIs,
  curveMetaFor,
  pairStripLabel,
  sanitiseSpreadSeries,
  stripSegmentLabel,
  usePolicyFuturesCrossMarket,
} from './futuresCrossMarketSpreadShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const curveFamilyAParam = params.curve_family_a ?? '';
  const curveFamilyBParam = params.curve_family_b ?? '';
  const stripPositionParam =
    params.strip_position && Number(params.strip_position)
      ? Number(params.strip_position)
      : 0;

  const { data, isLoading, errorMessage } = usePolicyFuturesCrossMarket({
    curveFamilyA: curveFamilyAParam,
    curveFamilyB: curveFamilyBParam,
    stripPosition: stripPositionParam,
    lookbackDays:
      params.lookback_days != null && params.lookback_days !== ''
        ? Number(params.lookback_days)
        : undefined,
    asOfDate: params.as_of_date || undefined,
    fieldName: params.field_name || undefined,
  });

  const cm = data?.current_metrics;
  const curveFamilyA = cm?.curve_family_a ?? curveFamilyAParam;
  const curveFamilyB = cm?.curve_family_b ?? curveFamilyBParam;
  const stripPosition = cm?.strip_position ?? stripPositionParam;
  const aMeta = curveMetaFor(curveFamilyA);
  const bMeta = curveMetaFor(curveFamilyB);

  const pairLabel = stripPosition > 0
    ? pairStripLabel(curveFamilyA, curveFamilyB, stripPosition)
    : '—';
  const segmentTag = stripPosition > 0 ? stripSegmentLabel(stripPosition) : '';

  // Mockup-faithful identity row.
  const primary = aMeta && bMeta
    ? `${aMeta.stripStemPrefix}-${bMeta.stripStemPrefix} · pos${stripPosition || '—'}${segmentTag ? ` · ${segmentTag}` : ''}`
    : `${pairLabel}${segmentTag ? ` · ${segmentTag}` : ''}`;
  const secondary = 'Front-Quarter Cross-CB Spread (Implied Rate)';
  const flagPair = aMeta && bMeta ? `${aMeta.flag} ${bMeta.flag}` : undefined;

  return (
    <BuildCompactShell
      toolDisplayName="Policy Futures Cross-Market Spread"
      statusPill="Cross-CB STIR"
      headerIcon={<ArrowLeftRight size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary,
        secondary,
        flag: flagPair,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'SPREAD (BPS)', value: '—', unit: 'bp' },
              { label: '1D CHANGE (BPS)', value: '—', unit: 'bp' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitiseSpreadSeries(data?.time_series ?? []).map(
        (r) => ({ date: r.date, value: r.value ?? NaN }),
      )}
      chartUnit="bp"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={compactCaveatFor(curveFamilyA, curveFamilyB)}
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
