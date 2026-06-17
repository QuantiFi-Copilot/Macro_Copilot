// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_cross_market_inflation_swap_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is cross-market ZCIS's full canvas.  Mounted by
// VirtualPrimitiveCanvas for single-tool queries OR by the click-to-expand
// modal from a compact card.  Design reference: ./mockups/Extended.png.
//
// Mockup-faithful design choices:
//   - Title "USD-EUR 5Y ZCIS SPREAD" with per-leg subtitle.
//   - Two leg dropdowns (Leg A + Leg B) plus tenor + lookback + field
//     (the structural ``cross-curve`` invariant means we expose both legs).
//   - bps-scale KPI strip + per-leg PERCENT decomposition.
//   - Top-right cards: Z-score / Percentile / Index-family caveat.
//   - The index-family caveat (USD_ZCIS / EUR_ZCIS / GBP_ZCIS reference
//     different indices — NOT a clean expected-inflation divergence)
//     surfaces inline via the wire's ``current_metrics.index_family_caveat``
//     + ``methodology_label``.
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  BuildExtendedShell,
  asOfDateControl,
  percentileLabel,
  regimeForZScore,
  signedFixed,
  toneForZScore,
  toneTextClass,
  type BuildExtendedProps,
  type ControlDescriptor,
  type TopRightCard,
} from '@/components/shared/build';
import {
  ZCIS_FAMILY_OPTIONS,
  ZCIS_TENOR_OPTIONS,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  extendedKPIs,
  pairShortLabel,
  pairSubtitle,
  sanitiseSpreadSeries,
  shortIndexCaveat,
  useCrossMarketZcisSpread,
  zcisFamilyFor,
} from './crossMarketZcisShared';

const LOOKBACK_OPTIONS = [
  { value: '90', label: '90d' },
  { value: '180', label: '180d' },
  { value: '365', label: '365d' },
  { value: '730', label: '2Y' },
  { value: '1825', label: '5Y' },
];

const FIELD_OPTIONS = [
  { value: 'PX_MID', label: 'PX_MID' },
  { value: 'PX_BID', label: 'PX_BID' },
  { value: 'PX_ASK', label: 'PX_ASK' },
];

const DEFAULTS = {
  lookback_days: '365',
  field_name: 'PX_MID',
};

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params -----
  const legA = params.leg_a_curve_family ?? 'USD_ZCIS';
  const legB = params.leg_b_curve_family ?? 'EUR_ZCIS';
  const tenor = params.tenor ?? '5Y';
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useCrossMarketZcisSpread({
    legACurveFamily: legA,
    legBCurveFamily: legB,
    tenor,
    lookbackDays: Number(lookbackDays),
    fieldName,
    asOfDate: params.as_of_date,
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
    // that equals the other, snap the other to the first different family.
    if (name === 'leg_a_curve_family' && value === nextParams.leg_b_curve_family) {
      const fallback = ZCIS_FAMILY_OPTIONS.find((o) => o.value !== value);
      if (fallback) nextParams.leg_b_curve_family = fallback.value;
    }
    if (name === 'leg_b_curve_family' && value === nextParams.leg_a_curve_family) {
      const fallback = ZCIS_FAMILY_OPTIONS.find((o) => o.value !== value);
      if (fallback) nextParams.leg_a_curve_family = fallback.value;
    }
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({
      leg_a_curve_family: legA,
      leg_b_curve_family: legB,
      tenor,
      ...DEFAULTS,
    });
  };

  // ----- Controls -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'leg_a_curve_family',
      label: 'Leg A',
      kind: 'enum',
      value: legA,
      options: ZCIS_FAMILY_OPTIONS,
    },
    {
      name: 'leg_b_curve_family',
      label: 'Leg B',
      kind: 'enum',
      value: legB,
      options: ZCIS_FAMILY_OPTIONS,
    },
    {
      name: 'tenor',
      label: 'Tenor',
      kind: 'enum',
      value: tenor,
      options: ZCIS_TENOR_OPTIONS,
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

  // ----- Top-right cards: Z-score / Percentile / Index-family caveat -----
  const cm = data?.current_metrics;
  const zRegime = regimeForZScore(cm?.z_score_252d);
  const pBucket =
    cm?.percentile_252d != null
      ? cm.percentile_252d >= 80
        ? 'High'
        : cm.percentile_252d <= 20
          ? 'Low'
          : 'Normal'
      : 'Normal';
  const aMeta = zcisFamilyFor(legA);
  const bMeta = zcisFamilyFor(legB);
  const topRightCards: ReadonlyArray<TopRightCard> = [
    {
      key: 'zscore',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">Z-SCORE (252D)</span>
          <div
            className={`text-[26px] font-medium leading-none ${toneTextClass(toneForZScore(cm?.z_score_252d))}`}
          >
            {signedFixed(cm?.z_score_252d ?? null, 2)}
          </div>
          <span className={`text-[11.5px] ${toneTextClass(toneForZScore(cm?.z_score_252d))}`}>
            {zRegime}
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
      key: 'index_family',
      node: (
        <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
          <span className="kicker text-fg-muted">INDEX FAMILIES</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {aMeta && bMeta
              ? `${aMeta.indexShort} vs ${bMeta.indexShort}`
              : `${legA} vs ${legB}`}
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            {cm?.index_family_caveat ?? shortIndexCaveat(legA, legB)}
          </span>
        </div>
      ),
    },
  ];

  const identitySubtitle = pairSubtitle(legA, legB, tenor);
  const pairLabel = pairShortLabel(legA, legB);

  return (
    <BuildExtendedShell
      category={{
        name: 'CROSS-MARKET INFLATION SWAP SPREAD',
        tags: ['SNAPSHOT', 'DETERMINISTIC', 'CROSS-MARKET'],
      }}
      identity={{
        primary: `${pairLabel} ${tenor} ZCIS SPREAD`,
        secondary: undefined,
        flag: aMeta && bMeta ? `${aMeta.flag} ${bMeta.flag}` : undefined,
        subtitle: identitySubtitle,
        asOfDate: cm?.as_of_date,
        meta: `${lookbackDays}d window · ${fieldName}`,
      }}
      topRightCards={topRightCards}
      controls={controls}
      onControlChange={handleControlChange}
      onResetControls={handleReset}
      kpis={
        data
          ? extendedKPIs(data)
          : extendedKPIs({
              current_metrics: {
                as_of_date: '',
                leg_a_curve_family: legA,
                leg_b_curve_family: legB,
                tenor,
                tenor_years: NaN,
                spread_label: '',
                spread_pct: NaN,
                spread_bps: NaN,
                change_1d_bps: null,
                change_1w_bps: null,
                change_1m_bps: null,
                z_score_252d: null,
                high_252d_bps: null,
                low_252d_bps: null,
                percentile_252d: null,
                leg_a_pct: null,
                leg_b_pct: null,
                observation_count: 0,
                leg_a_inflation_index_family: '',
                leg_b_inflation_index_family: '',
                index_families_match: false,
                index_family_caveat: null,
                leg_a_index_lag: '',
                leg_b_index_lag: '',
                leg_a_interpolation: '',
                leg_b_interpolation: '',
                leg_a_underlying_index: null,
                leg_b_underlying_index: null,
                methodology_label: '',
              },
              time_series: [],
              // Empty stub TimeSeries — extendedKPIs() reads only
              // current_metrics, so the rendered KPI strip is unaffected
              // by the empty rows.  Required since
              // CrossMarketInflationSwapSpreadOutput mirrors the Pydantic
              // Output exactly (time_series_spread + time_series_zscore
              // are non-optional).
              time_series_spread: {
                series_name: '',
                units: 'bps',
                description: '',
                rows: [],
              },
              time_series_zscore: {
                series_name: '',
                units: 'z_score',
                description: '',
                rows: [],
              },
            })
      }
      chartPoints={sanitiseSpreadSeries(
        data?.time_series_spread?.rows ?? [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="bp"
      chartValueDecimals={1}
      referenceBands={data ? buildReferenceBands(data) : []}
      stretchContext={data ? buildStretchContext(data) : undefined}
      methodology={
        data ? buildMethodologyRows(data, fieldName, Number(lookbackDays)) : []
      }
      methodologyReferences={buildReferenceChips()}
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic snapshot (leg_a − leg_b, inner-join)',
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
