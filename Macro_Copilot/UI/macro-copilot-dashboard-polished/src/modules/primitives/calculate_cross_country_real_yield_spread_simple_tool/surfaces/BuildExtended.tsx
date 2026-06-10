// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_cross_country_real_yield_spread_simple_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is cross-country real-yield's full canvas.  Mounted by
// VirtualPrimitiveCanvas for single-tool queries OR by the click-to-expand
// modal from a compact card.  Design reference: ./mockups/Extended.png.
//
// Mockup-faithful design choices:
//   - Title "UK 10Y Real - US 10Y Real" with per-country subtitle.
//   - Two linker-curve dropdowns (First / Second) plus tenor + lookback +
//     field (the cross-country invariant means each leg is a SINGLE
//     linker curve_family — distinct from the breakeven sibling's
//     (nominal, linker) packed-pair convention).
//   - Spread KPIs in PERCENT (real yields are quoted in PERCENT — NOT
//     BPS); period-change KPIs in BPS (desk convention).  Per-curve
//     real-yield endpoint decomposition.
//   - Top-right cards: Z-score / Percentile / Index-family + market-
//     structure caveat.
//   - The index-family + market-structure mismatch caveats surface inline
//     via the wire's ``current_metrics.methodology_label`` (sourced from
//     YAML at runtime, NOT hardcoded).
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  BuildExtendedShell,
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
  CURVE_FAMILY_OPTIONS,
  TENOR_OPTIONS,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  extendedKPIs,
  identitySubtitle,
  linkerCurveFor,
  pairShortLabel,
  sanitiseSpreadSeries,
  shortIndexCaveat,
  useCrossCountryRealYieldSpread,
} from './crossCountryRealYieldSpreadSimpleShared';

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

  // ----- Resolve effective params -----
  const firstCurve = params.first_curve_family ?? 'GBP_LINKER';
  const secondCurve = params.second_curve_family ?? 'USD_TIPS';
  const tenor = params.tenor ?? '10Y';
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useCrossCountryRealYieldSpread({
    firstCurveFamily: firstCurve,
    secondCurveFamily: secondCurve,
    tenor,
    lookbackDays: Number(lookbackDays),
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
    // Cross-country invariant: first_curve_family != second_curve_family.
    // When the user picks a curve that equals the other, snap the other
    // to the first different curve option in the registry order.
    if (name === 'first_curve_family' && value === nextParams.second_curve_family) {
      const fallback = CURVE_FAMILY_OPTIONS.find((o) => o.value !== value);
      if (fallback) nextParams.second_curve_family = fallback.value;
    }
    if (name === 'second_curve_family' && value === nextParams.first_curve_family) {
      const fallback = CURVE_FAMILY_OPTIONS.find((o) => o.value !== value);
      if (fallback) nextParams.first_curve_family = fallback.value;
    }
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({
      first_curve_family: firstCurve,
      second_curve_family: secondCurve,
      tenor,
      ...DEFAULTS,
    });
  };

  // ----- Controls -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'first_curve_family',
      label: 'First curve',
      kind: 'enum',
      value: firstCurve,
      options: CURVE_FAMILY_OPTIONS,
    },
    {
      name: 'second_curve_family',
      label: 'Second curve',
      kind: 'enum',
      value: secondCurve,
      options: CURVE_FAMILY_OPTIONS,
    },
    {
      name: 'tenor',
      label: 'Tenor',
      kind: 'enum',
      value: tenor,
      options: TENOR_OPTIONS,
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

  // ----- Top-right cards: Z-score / Percentile / Index-family caveat -----
  const cm = data?.current_metrics;
  const zRegime = regimeForZScore(cm?.current_z_score);
  const pBucket =
    cm?.percentile_252d != null
      ? cm.percentile_252d >= 80
        ? 'High'
        : cm.percentile_252d <= 20
          ? 'Low'
          : 'Normal'
      : 'Normal';
  const aMeta = linkerCurveFor(firstCurve);
  const bMeta = linkerCurveFor(secondCurve);
  const indexFamilyHeadline =
    aMeta && bMeta
      ? `${aMeta.indexShort} vs ${bMeta.indexShort}`
      : 'Cross-country';
  const indexFamilyCaveat = shortIndexCaveat(firstCurve, secondCurve);

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
            {indexFamilyHeadline}
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            {indexFamilyCaveat}
          </span>
        </div>
      ),
    },
  ];

  const subtitle = identitySubtitle(firstCurve, secondCurve, tenor);
  const pairLabel = pairShortLabel(firstCurve, secondCurve);
  const observationCount = data?.time_series?.length ?? 0;

  return (
    <BuildExtendedShell
      category={{
        name: 'CROSS-COUNTRY LINKER REAL-YIELD SPREAD',
        tags: ['SNAPSHOT', 'DETERMINISTIC', 'CROSS-COUNTRY'],
      }}
      identity={{
        primary: `${pairLabel} ${tenor} Real Yield`,
        secondary: undefined,
        flag: aMeta && bMeta ? `${aMeta.flag} ${bMeta.flag}` : undefined,
        subtitle,
        asOfDate: cm?.as_of_date,
        meta: `${lookbackDays}d window · ${fieldName}`,
      }}
      topRightCards={topRightCards}
      controls={controls}
      onControlChange={handleControlChange}
      onResetControls={handleReset}
      kpis={
        data
          ? extendedKPIs(data, observationCount)
          : extendedKPIs(
              {
                current_metrics: {
                  as_of_date: '',
                  first_curve_family: firstCurve,
                  second_curve_family: secondCurve,
                  tenor,
                  tenor_years: NaN,
                  spread_label: '',
                  current_spread_pct: null,
                  daily_change_bps: null,
                  weekly_change_bps: null,
                  monthly_change_bps: null,
                  current_z_score: null,
                  rolling_window_days: 252,
                  high_252d_pct: null,
                  low_252d_pct: null,
                  percentile_252d: null,
                  first_curve_real_yield_pct: null,
                  second_curve_real_yield_pct: null,
                  first_curve_country: '',
                  first_curve_currency: '',
                  second_curve_country: '',
                  second_curve_currency: '',
                  methodology_label: '',
                },
                time_series: [],
                // Empty stub TimeSeries — the KPI builder reads only
                // current_metrics, so the rendered strip is unaffected
                // by the empty rows.  Required since
                // CrossCountryRealYieldSpreadSimpleOutput mirrors the
                // Pydantic Output exactly (time_series_spread +
                // time_series_zscore are non-optional).
                time_series_spread: {
                  series_name: '',
                  units: 'percent',
                  description: '',
                  rows: [],
                },
                time_series_zscore: {
                  series_name: '',
                  units: 'z_score',
                  description: '',
                  rows: [],
                },
              },
              0,
            )
      }
      chartPoints={sanitiseSpreadSeries(
        data?.time_series_spread?.rows ?? [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="%"
      chartValueDecimals={2}
      referenceBands={data ? buildReferenceBands(data) : []}
      stretchContext={data ? buildStretchContext(data) : undefined}
      methodology={
        data ? buildMethodologyRows(data, fieldName, Number(lookbackDays)) : []
      }
      methodologyReferences={buildReferenceChips()}
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic snapshot (first_real_yield − second_real_yield, inner-join)',
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
