// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_cross_market_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every primitive ships an extended view;
// this is the same-tenor cross-market sovereign spread's full canvas.
// Mounted by VirtualPrimitiveCanvas for single-tool queries OR by the click-
// to-expand modal from a compact card.  Design reference: ./mockups/Extended.png.
//
// Mockup-faithful design choices:
//   - Two CURVE dropdowns (Curve A / Curve B) at a SINGLE tenor — the
//     cross-curve invariant means we expose both legs (distinct from
//     calculate_curve_spread_tool which is single-curve / two tenors).
//     A snap helper enforces ``Curve A != Curve B`` at the input layer
//     (the backend re-validates regardless).
//   - bps-scale KPI strip with sign-aware "<cf> Premium" caption — the
//     backend ships the spread + 1d/1w/1m changes in BPS (no unit
//     conversion).  Plus a two-leg PERCENT decomposition row.
//   - Top-right cards: Z-score / Percentile / Sovereign caveat.
//   - The sovereign-divergence caveat (mixes policy + term-premium +
//     credit/supply) surfaces on the methodology card.  TODO(PR10): when
//     the backend ships ``current_metrics.methodology_label`` the
//     "Disclosure" row switches to consume the wire (see shared helper).
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  BuildExtendedShell,
  percentileLabel,
  signedFixed,
  toneForZScore,
  toneTextClass,
  type BuildExtendedProps,
  type ControlDescriptor,
  type TopRightCard,
} from '@/components/shared/build';
import {
  SOVEREIGN_CURVE_OPTIONS,
  SOVEREIGN_TENOR_OPTIONS,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  decompositionKPIs,
  extendedKPIs,
  pairLongSubtitle,
  pairShortLabel,
  premiumCaption,
  sanitiseSpreadSeries,
  sovereignFamilyFor,
  spreadChartRows,
  useCrossMarketSpread,
  zScoreRegimeCaption,
} from './crossMarketSpreadShared';

const LOOKBACK_OPTIONS = [
  { value: '90', label: '90d' },
  { value: '180', label: '180d' },
  { value: '365', label: '365d' },
  { value: '730', label: '2Y' },
  { value: '1825', label: '5Y' },
];

const FIELD_OPTIONS = [
  { value: 'YLD_YTM_MID', label: 'YLD_YTM_MID' },
  { value: 'YLD_YTM_BID', label: 'YLD_YTM_BID' },
  { value: 'YLD_YTM_ASK', label: 'YLD_YTM_ASK' },
  { value: 'PX_LAST', label: 'PX_LAST' },
];

const DEFAULTS = {
  lookback_days: '365',
  field_name: 'YLD_YTM_MID',
};

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  const cf1 = params.curve_family_1 ?? 'UST';
  const cf2 = params.curve_family_2 ?? 'DE_BUND';
  const tenor = params.tenor ?? '10Y';
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useCrossMarketSpread({
    curveFamily1: cf1,
    curveFamily2: cf2,
    tenor,
    lookbackDays: Number(lookbackDays),
    fieldName,
  });

  const pushParams = (nextParams: Record<string, string>) => {
    if (onParamsChange) {
      onParamsChange(nextParams);
      return;
    }
    const nextCtx = encodeURIComponent(
      JSON.stringify({
        tools: [{ tool: toolName, params: nextParams }],
        tool_count: 1,
      }),
    );
    navigate(`/workspace?context=${nextCtx}`, { replace: true });
  };

  const handleControlChange = (name: string, value: string) => {
    const nextParams = { ...params };
    nextParams[name] = value;
    // Cross-curve invariant: cf1 != cf2.  When the user picks a curve that
    // equals the other, snap the other to the first different family.
    if (name === 'curve_family_1' && value === nextParams.curve_family_2) {
      const fallback = SOVEREIGN_CURVE_OPTIONS.find((o) => o.value !== value);
      if (fallback) nextParams.curve_family_2 = fallback.value;
    }
    if (name === 'curve_family_2' && value === nextParams.curve_family_1) {
      const fallback = SOVEREIGN_CURVE_OPTIONS.find((o) => o.value !== value);
      if (fallback) nextParams.curve_family_1 = fallback.value;
    }
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({
      curve_family_1: cf1,
      curve_family_2: cf2,
      tenor,
      ...DEFAULTS,
    });
  };

  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family_1',
      label: 'Curve A',
      kind: 'enum',
      value: cf1,
      options: SOVEREIGN_CURVE_OPTIONS,
    },
    {
      name: 'curve_family_2',
      label: 'Curve B',
      kind: 'enum',
      value: cf2,
      options: SOVEREIGN_CURVE_OPTIONS,
    },
    {
      name: 'tenor',
      label: 'Tenor',
      kind: 'enum',
      value: tenor,
      options: SOVEREIGN_TENOR_OPTIONS,
    },
    {
      name: 'lookback_days',
      label: 'Lookback',
      kind: 'enum',
      value: lookbackDays,
      options: LOOKBACK_OPTIONS,
    },
    {
      name: 'field_name',
      label: 'Field',
      kind: 'enum',
      value: fieldName,
      options: FIELD_OPTIONS,
    },
  ];

  const cm = data?.current_metrics;
  const zCaption = zScoreRegimeCaption(cm?.current_z_score);
  const aMeta = sovereignFamilyFor(cf1);
  const bMeta = sovereignFamilyFor(cf2);
  const pBucket =
    cm?.percentile_252d != null
      ? cm.percentile_252d >= 80
        ? 'High'
        : cm.percentile_252d <= 20
          ? 'Low'
          : 'Normal'
      : 'Normal';
  const premium = premiumCaption(cm?.current_spread_bps, cf1, cf2);

  const topRightCards: ReadonlyArray<TopRightCard> = [
    {
      key: 'zscore',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">Z-SCORE (252D)</span>
          <div
            className={`text-[26px] font-medium leading-none ${toneTextClass(toneForZScore(cm?.current_z_score))}`}
          >
            {signedFixed(cm?.current_z_score ?? null, 2)}
          </div>
          <span className={`text-[11.5px] ${toneTextClass(toneForZScore(cm?.current_z_score))}`}>
            {zCaption}
          </span>
        </div>
      ),
    },
    {
      key: 'percentile',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">PERCENTILE (252D)</span>
          <div className="text-[26px] font-medium leading-none text-fg-primary">
            {percentileLabel(cm?.percentile_252d ?? null)}
          </div>
          <span className="text-[11.5px] text-fg-secondary">{pBucket}</span>
        </div>
      ),
    },
    {
      key: 'premium',
      node: (
        <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
          <span className="kicker text-fg-muted">{premium === '—' ? 'PREMIUM' : premium.toUpperCase()}</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            Sovereign divergence
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            policy + term premium + credit/supply.
          </span>
        </div>
      ),
    },
  ];

  const pairLabel = pairShortLabel(cf1, cf2);
  const identityPrimary = `${pairLabel} ${tenor} Spread`;
  const identitySubtitle = pairLongSubtitle(cf1, cf2, tenor);
  const flag =
    aMeta && bMeta ? `${aMeta.flag} ${bMeta.flag}` : aMeta?.flag ?? bMeta?.flag;

  // Combine the headline KPI strip and the two-leg decomposition row.  The
  // shell renders them as one continuous strip; the decomposition labels
  // carry their own tenor + sovereign markers so the grouping reads
  // naturally.
  const allKpis = data
    ? [...extendedKPIs(data), ...decompositionKPIs(data, tenor)]
    : [
        { label: 'SPREAD', value: '—', unit: 'bp', tone: 'neutral' as const },
        { label: '1D CHANGE', value: '—', unit: 'bp', tone: 'neutral' as const },
        { label: 'Z-SCORE (252D)', value: '—', tone: 'neutral' as const },
      ];

  return (
    <BuildExtendedShell
      category={{
        name: 'SOVEREIGN CROSS-MARKET SPREAD',
        tags: ['SNAPSHOT', 'DETERMINISTIC', 'CROSS-MARKET'],
      }}
      identity={{
        primary: identityPrimary,
        secondary: aMeta && bMeta ? `· ${aMeta.longLabel} vs ${bMeta.longLabel}` : undefined,
        flag,
        subtitle: identitySubtitle,
        asOfDate: cm?.as_of_date,
        meta: `${lookbackDays}d window · ${fieldName}`,
      }}
      topRightCards={topRightCards}
      controls={controls}
      onControlChange={handleControlChange}
      onResetControls={handleReset}
      kpis={allKpis}
      chartPoints={sanitiseSpreadSeries(
        data ? spreadChartRows(data) : [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="bp"
      chartValueDecimals={1}
      referenceBands={data ? buildReferenceBands(data) : []}
      stretchContext={data ? buildStretchContext(data) : undefined}
      methodology={
        data
          ? buildMethodologyRows(data, fieldName, Number(lookbackDays))
          : []
      }
      methodologyReferences={buildReferenceChips()}
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic snapshot (cf1 − cf2 at matched tenor, inner-join, in bps)',
        providers: ['TimescaleDB', 'macro_data.v_market_data_daily_enriched'],
        asOf: cm?.as_of_date,
        freshness: 'fresh',
      }}
      isLoading={isLoading}
      errorMessage={errorMessage ?? undefined}
    />
  );
};

export default BuildExtended;
