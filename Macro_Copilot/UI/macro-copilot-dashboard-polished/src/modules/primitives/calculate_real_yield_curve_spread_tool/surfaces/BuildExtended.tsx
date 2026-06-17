// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_real_yield_curve_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5.  Mounted by VirtualPrimitiveCanvas for
// single-tool queries OR by the click-to-expand modal from a compact card.
// Design reference: ./mockups/Extended.png.
//
// Mockup-faithful design choices:
//   - SHORT TENOR + LONG TENOR as TWO separate dropdowns; the long-tenor
//     options are filtered to strictly-longer tenors so the "long > short"
//     validity rule is enforced at the input layer (the backend
//     re-validates regardless).
//   - A signed 5-zone z-score regime slider (Extreme Down / Elevated Down /
//     Normal / Elevated Up / Extreme Up) — the ZScoreRegimeSlider shared
//     element — surfaced as a top-right card, because the spread is a
//     SIGNED measure (steepening vs flattening read differently).
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  BuildExtendedShell,
  asOfDateControl,
  countryCaveatFor,
  percentileLabel,
  regimeForZScore,
  signedFixed,
  toneForZScore,
  toneTextClass,
  ZScoreRegimeSlider,
  type BuildExtendedProps,
  type ControlDescriptor,
  type TopRightCard,
} from '@/components/shared/build';
import {
  CURVE_OPTIONS,
  TENOR_OPTIONS_BY_CURVE,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  extendedKPIs,
  sanitiseSpreadSeries,
  spreadShortLabel,
  tenorToYears,
  useRealYieldCurveSpread,
} from './curveSpreadShared';

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
  z_score_window_days: '252',
  z_score_min_periods: '60',
  z_score_ddof: '1',
};

/** Long-tenor options for a curve = the family's tenors strictly longer
 *  than the selected short tenor (enforces the long > short rule). */
function longTenorOptions(curveFamily: string, shortTenor: string) {
  const all = TENOR_OPTIONS_BY_CURVE[curveFamily] ?? TENOR_OPTIONS_BY_CURVE.USD_TIPS;
  const shortYears = tenorToYears(shortTenor);
  if (Number.isNaN(shortYears)) return all;
  return all.filter((o) => tenorToYears(o.value) > shortYears);
}

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  const curveFamily = params.curve_family ?? 'USD_TIPS';
  const shortTenor = params.short_tenor ?? '5Y';
  const longTenor = params.long_tenor ?? '10Y';
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;
  const zWindow = params.z_score_window_days || DEFAULTS.z_score_window_days;
  const zMinPeriods = params.z_score_min_periods || DEFAULTS.z_score_min_periods;
  const zDdof = params.z_score_ddof ?? DEFAULTS.z_score_ddof;

  const { data, isLoading, errorMessage } = useRealYieldCurveSpread({
    curveFamily,
    shortTenor,
    longTenor,
    lookbackDays: Number(lookbackDays),
    fieldName,
    zScoreWindowDays: Number(zWindow),
    zScoreMinPeriods: Number(zMinPeriods),
    zScoreDdof: Number(zDdof),
    asOfDate: params.as_of_date,
  });

  const pushParams = (nextParams: Record<string, string>) => {
    // Stage D — when mounted inside the multi-tool DAG expand-to-modal,
    // edits stay local (onParamsChange) instead of navigating the global
    // URL, which would replace the multi-tool context behind the modal.
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
    const nextParams = { ...params, [name]: value };
    if (name === 'curve_family') {
      // Reset to the new family's first valid (short, long) pair.
      const tenors = TENOR_OPTIONS_BY_CURVE[value] ?? [];
      if (tenors.length >= 2) {
        nextParams.short_tenor = tenors[0].value;
        nextParams.long_tenor = tenors[1].value;
      }
    } else if (name === 'short_tenor') {
      // If the new short tenor is >= the current long tenor, bump the
      // long tenor to the next valid (strictly-longer) option.
      const opts = longTenorOptions(curveFamily, value);
      if (!opts.some((o) => o.value === longTenor) && opts.length > 0) {
        nextParams.long_tenor = opts[0].value;
      }
    }
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({
      curve_family: curveFamily,
      short_tenor: shortTenor,
      long_tenor: longTenor,
      ...DEFAULTS,
    });
  };

  // ----- Controls -----
  const shortOptions = TENOR_OPTIONS_BY_CURVE[curveFamily] ?? TENOR_OPTIONS_BY_CURVE.USD_TIPS;
  const longOptions = longTenorOptions(curveFamily, shortTenor);
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family',
      label: 'Curve Family',
      kind: 'enum',
      value: curveFamily,
      options: CURVE_OPTIONS,
    },
    {
      name: 'short_tenor',
      label: 'Short Tenor',
      kind: 'enum',
      value: shortTenor,
      options: shortOptions,
    },
    {
      name: 'long_tenor',
      label: 'Long Tenor',
      kind: 'enum',
      value: longTenor,
      options: longOptions,
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
    {
      name: 'z_score_window_days',
      label: 'z_score_window_days',
      kind: 'number',
      value: zWindow,
      min: 60,
      max: 1260,
      advanced: true,
    },
    {
      name: 'z_score_min_periods',
      label: 'z_score_min_periods',
      kind: 'number',
      value: zMinPeriods,
      min: 20,
      max: 252,
      advanced: true,
    },
    {
      name: 'z_score_ddof',
      label: 'z_score_ddof',
      kind: 'enum',
      value: zDdof,
      options: [
        { value: '1', label: '1 (sample)' },
        { value: '0', label: '0 (population)' },
      ],
      advanced: true,
    },
    asOfDateControl(params.as_of_date),
  ];

  // ----- Top-right cards: Z-score / Percentile / signed 5-zone regime -----
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
      key: 'regime',
      node: (
        <div className="card flex h-full flex-col justify-center gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">CURVE REGIME (SIGNED)</span>
          <ZScoreRegimeSlider value={cm?.current_z_score} />
        </div>
      ),
    },
  ];

  const caveat = countryCaveatFor(curveFamily);
  const pairLabel = spreadShortLabel(shortTenor, longTenor);
  const identitySubtitle =
    curveFamily === 'USD_TIPS'
      ? 'US TIPS · Real-yield curve shape (real-rate term structure)'
      : curveFamily === 'GBP_LINKER'
        ? 'UK Index-Linked Gilts · Real-yield curve shape'
        : curveFamily === 'EUR_FR_LINKER'
          ? 'French OATei · Real-yield curve shape'
          : curveFamily === 'CAD_RRB'
            ? 'Canadian RRB · Real-yield curve shape'
            : 'Sovereign linker · Real-yield curve shape';

  return (
    <BuildExtendedShell
      category={{
        name: 'REAL-YIELD CURVE SPREAD',
        tags: ['CURVE SHAPE', 'DETERMINISTIC'],
      }}
      identity={{
        primary: `${curveFamily} ${pairLabel}`,
        secondary: 'real-yield',
        flag: caveat?.flag,
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
                curve_family: curveFamily,
                short_tenor: shortTenor,
                long_tenor: longTenor,
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
                short_real_yield_pct: null,
                long_real_yield_pct: null,
                short_years: 0,
                long_years: 0,
                observation_count: 0,
                country: '',
                currency: '',
                methodology_label: '',
              },
              time_series: [],
            })
      }
      chartPoints={sanitiseSpreadSeries(
        data?.time_series_spread?.rows ?? [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="%"
      chartValueDecimals={3}
      referenceBands={data ? buildReferenceBands(data) : []}
      stretchContext={data ? buildStretchContext(data) : undefined}
      methodology={
        data
          ? buildMethodologyRows(
              data,
              fieldName,
              Number(zWindow),
              Number(zMinPeriods),
              Number(zDdof),
            )
          : []
      }
      methodologyReferences={buildReferenceChips()}
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic snapshot (composed from two real-yield levels)',
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
