// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// policy_futures_get_futures_cross_market_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is the cross-market STIR spread's full canvas.  Mounted by
// VirtualPrimitiveCanvas for single-tool queries OR by the click-to-expand
// modal from a compact card in a multi-tool DAG.
//
// Design reference: ./mockups/Extended.png.  Mockup-faithful design
// choices:
//   - TWO "Curve Family" dropdowns (Leg A + Leg B) — cross-market primitive
//     keyed on two different policy-futures families.  Snap-different
//     handler ensures the schema's ``curve_family_a != curve_family_b``
//     invariant cannot be violated from the controls strip.
//   - A SINGLE strip-position dropdown — matched-strip cross-market read.
//   - bps-scale headline KPI strip — the backend ships the spread in
//     PERCENT POINTS in the A − B convention; the display layer
//     multiplies by 100 (NO sign flip — A − B IS the desk-canonical
//     cross-CB divergence direction).
//   - Per-leg implied-rate decomposition row exposing the master stems +
//     current-front underlying contracts inline.
//   - Top-right cards: Z-score / Percentile / Cross-CB Context (with
//     explicit RFR-vs-IBOR labelling when the pair is mixed-regime).
//   - The full P5 / ADR 0013 disclosure (including the mixed-regime call-
//     out + the RAW-differential guardrail) surfaces in the methodology
//     card via the backend's ``methodology_disclosure`` field (NOT a
//     hardcoded TS literal).
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  BuildExtendedShell,
  asOfDateControl,
  percentileLabel,
  signedFixed,
  toneForZScore,
  toneTextClass,
  type BuildExtendedProps,
  type ControlDescriptor,
  type TopRightCard,
} from '@/components/shared/build';
import {
  POLICY_FUTURES_CURVE_OPTIONS,
  STRIP_POSITION_OPTIONS,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  compactCaveatFor,
  crossCBLabel,
  curveMetaFor,
  decompositionKPIs,
  extendedKPIs,
  isMixedRegime,
  pairStripLabel,
  sanitiseSpreadSeries,
  usePolicyFuturesCrossMarket,
} from './futuresCrossMarketSpreadShared';

const LOOKBACK_OPTIONS = [
  { value: '90', label: '90d' },
  { value: '180', label: '180d' },
  { value: '365', label: '365d' },
  { value: '730', label: '2Y' },
  { value: '1825', label: '5Y' },
];

const FIELD_OPTIONS = [
  { value: 'PX_LAST', label: 'PX_LAST' },
  { value: 'PX_MID', label: 'PX_MID' },
  { value: 'PX_BID', label: 'PX_BID' },
  { value: 'PX_ASK', label: 'PX_ASK' },
];

const DEFAULTS = {
  lookback_days: '365',
  field_name: 'PX_LAST',
};

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params -----
  const curveFamilyA = params.curve_family_a ?? 'SOFR_FUT';
  const curveFamilyB = params.curve_family_b ?? 'EUR_SHORT_RATE_FUT';
  const stripStr = params.strip_position ?? '1';
  const stripPosition = Number(stripStr) || 1;
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;
  const asOfDate = params.as_of_date || undefined;

  const { data, isLoading, errorMessage } = usePolicyFuturesCrossMarket({
    curveFamilyA,
    curveFamilyB,
    stripPosition,
    lookbackDays: Number(lookbackDays),
    asOfDate,
    fieldName,
  });

  // ----- URL update on control change -----
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
    // Cross-market invariant: leg_a != leg_b.  When the user picks a leg
    // that equals the other, snap the other to the first different family
    // so the dispatch stays valid against the schema layer.
    if (name === 'curve_family_a' && value === nextParams.curve_family_b) {
      const fallback = POLICY_FUTURES_CURVE_OPTIONS.find((o) => o.value !== value);
      if (fallback) nextParams.curve_family_b = fallback.value;
    }
    if (name === 'curve_family_b' && value === nextParams.curve_family_a) {
      const fallback = POLICY_FUTURES_CURVE_OPTIONS.find((o) => o.value !== value);
      if (fallback) nextParams.curve_family_a = fallback.value;
    }
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({
      curve_family_a: curveFamilyA,
      curve_family_b: curveFamilyB,
      strip_position: stripStr,
      ...DEFAULTS,
    });
  };

  // ----- Controls -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family_a',
      label: 'Leg A',
      kind: 'enum',
      value: curveFamilyA,
      options: POLICY_FUTURES_CURVE_OPTIONS,
    },
    {
      name: 'curve_family_b',
      label: 'Leg B',
      kind: 'enum',
      value: curveFamilyB,
      options: POLICY_FUTURES_CURVE_OPTIONS,
    },
    {
      name: 'strip_position',
      label: 'Strip Position',
      kind: 'enum',
      value: stripStr,
      options: STRIP_POSITION_OPTIONS,
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
    asOfDateControl(params.as_of_date),
  ];

  // ----- Top-right cards: Z-score / Percentile / Cross-CB context -----
  const cm = data?.current_metrics;
  const aMeta = curveMetaFor(curveFamilyA);
  const bMeta = curveMetaFor(curveFamilyB);
  const mixedRegime = isMixedRegime(
    cm?.short_rate_regime_a,
    cm?.short_rate_regime_b,
  );
  const topRightCards: ReadonlyArray<TopRightCard> = [
    {
      key: 'zscore',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">Z-SCORE (252D)</span>
          <div
            className={`text-[26px] font-medium leading-none ${toneTextClass(toneForZScore(cm?.z_score_spread))}`}
          >
            {signedFixed(cm?.z_score_spread ?? null, 2)}
          </div>
          <span className={`text-[11.5px] ${toneTextClass(toneForZScore(cm?.z_score_spread))}`}>
            {cm?.z_score_spread != null
              ? Math.abs(cm.z_score_spread) >= 2
                ? 'Extreme'
                : Math.abs(cm.z_score_spread) >= 1.5
                  ? 'Elevated'
                  : 'Normal'
              : '—'}
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
          <span className="text-[11.5px] text-fg-secondary">
            {cm?.percentile_252d != null
              ? cm.percentile_252d >= 80
                ? 'High'
                : cm.percentile_252d <= 20
                  ? 'Low'
                  : 'Normal'
              : 'Normal'}
          </span>
        </div>
      ),
    },
    {
      key: 'cross-cb-context',
      node: (
        <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
          <span className="kicker text-fg-muted">CROSS-CB CONTEXT</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {crossCBLabel(curveFamilyA, curveFamilyB)}
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            {mixedRegime
              ? `Mixed regime (${cm?.short_rate_regime_a ?? aMeta?.regime} vs ${cm?.short_rate_regime_b ?? bMeta?.regime}) — read as RAW divergence, NOT basis-adjusted.`
              : `${cm?.short_rate_regime_a ?? aMeta?.regime ?? '—'} both legs · same short-rate convention.`}
          </span>
        </div>
      ),
    },
  ];

  // ----- Identity row -----
  const pairLabel = pairStripLabel(curveFamilyA, curveFamilyB, stripPosition);
  const identityPrimary = aMeta && bMeta
    ? `${aMeta.stripStemPrefix}-${bMeta.stripStemPrefix} Pos${stripPosition} Cross-CB Spread`
    : `${pairLabel} Cross-CB Spread`;
  const identitySecondary = 'Strip (Implied Rate)';
  const identitySubtitle = `${crossCBLabel(curveFamilyA, curveFamilyB)} — strip position ${stripPosition} matched-strip implied-rate differential (bps display)`;
  const flagPair = aMeta && bMeta ? `${aMeta.flag} ${bMeta.flag}` : undefined;

  // Combine the headline KPI strip and the decomposition row.  The shell
  // renders them as one continuous strip; the decomposition labels carry
  // their own master-stem markers so the grouping reads naturally.
  const allKpis = data
    ? [
        ...extendedKPIs(data),
        ...decompositionKPIs(data),
      ]
    : [
        {
          label: 'SPREAD',
          value: '—',
          unit: 'bp',
          tone: 'neutral' as const,
        },
        {
          label: '1D CHANGE',
          value: '—',
          unit: 'bp',
          tone: 'neutral' as const,
        },
        {
          label: 'Z-SCORE (252D)',
          value: '—',
          tone: 'neutral' as const,
        },
      ];

  return (
    <BuildExtendedShell
      category={{
        name: 'POLICY FUTURES CROSS-MARKET SPREAD',
        tags: ['SNAPSHOT', 'DETERMINISTIC', 'CROSS-MARKET RV'],
      }}
      identity={{
        primary: identityPrimary,
        secondary: identitySecondary,
        flag: flagPair,
        subtitle: identitySubtitle,
        asOfDate: cm?.as_of_date,
        meta: `${lookbackDays}d window · ${fieldName}`,
      }}
      topRightCards={topRightCards}
      controls={controls}
      onControlChange={handleControlChange}
      onResetControls={handleReset}
      kpis={allKpis}
      chartPoints={sanitiseSpreadSeries(data?.time_series ?? []).map(
        (r) => ({ date: r.date, value: r.value ?? NaN }),
      )}
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
        kind: 'Deterministic snapshot (rate_A − rate_B at matched strip slot, implied-rate axis in bps)',
        providers: [
          'TimescaleDB',
          'macro_data.v_market_data_daily_enriched',
          compactCaveatFor(curveFamilyA, curveFamilyB),
        ],
        asOf: cm?.as_of_date,
        freshness: 'fresh',
      }}
      isLoading={isLoading}
      errorMessage={errorMessage ?? undefined}
    />
  );
};

export default BuildExtended;
