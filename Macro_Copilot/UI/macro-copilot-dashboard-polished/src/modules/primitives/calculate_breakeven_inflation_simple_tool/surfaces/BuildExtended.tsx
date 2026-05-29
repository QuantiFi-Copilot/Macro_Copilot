// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_breakeven_inflation_simple_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is breakeven's full canvas.  Mounted by VirtualPrimitiveCanvas
// for single-tool queries OR by the click-to-expand modal from a compact
// card.  Design reference: ./mockups/Extended.png.
//
// Mockup-faithful design choices:
//   - A SINGLE "Country Pair" dropdown (not two) — a breakeven is a
//     same-country object, so the linker family uniquely picks the pair;
//     the control expands to nominal_curve_family + linker_curve_family.
//   - bps-scale KPI strip + the two underlying yields for the decomposition.
//   - The inflation-compensation honesty caveat surfaces in the methodology
//     card + a top-right PAIR card.
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
  BREAKEVEN_COMPACT_CAVEAT,
  BREAKEVEN_PAIR_OPTIONS,
  BREAKEVEN_TENOR_OPTIONS_BY_PAIR,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  extendedKPIs,
  pairForLinker,
  sanitiseBreakevenSeries,
  useBreakevenInflation,
} from './breakevenShared';

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

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params -----
  const nominalFamily = params.nominal_curve_family ?? 'UST';
  const linkerFamily = params.linker_curve_family ?? 'USD_TIPS';
  const tenor = params.tenor ?? '10Y';
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;
  const zWindow = params.z_score_window_days || DEFAULTS.z_score_window_days;
  const zMinPeriods = params.z_score_min_periods || DEFAULTS.z_score_min_periods;
  const zDdof = params.z_score_ddof ?? DEFAULTS.z_score_ddof;

  const { data, isLoading, errorMessage } = useBreakevenInflation({
    nominalCurveFamily: nominalFamily,
    linkerCurveFamily: linkerFamily,
    tenor,
    lookbackDays: Number(lookbackDays),
    fieldName,
    zScoreWindowDays: Number(zWindow),
    zScoreMinPeriods: Number(zMinPeriods),
    zScoreDdof: Number(zDdof),
  });

  // ----- URL update on control change -----
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
    const nextParams = { ...params };
    if (name === 'pair') {
      // The single Country-Pair control expands to BOTH legs.  The
      // linker family uniquely determines the nominal counterparty.
      const meta = pairForLinker(value);
      nextParams.linker_curve_family = value;
      if (meta) nextParams.nominal_curve_family = meta.nominalFamily;
      const tenors = BREAKEVEN_TENOR_OPTIONS_BY_PAIR[value] ?? [];
      if (tenors.length > 0) nextParams.tenor = tenors[0].value;
    } else {
      nextParams[name] = value;
    }
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({
      nominal_curve_family: nominalFamily,
      linker_curve_family: linkerFamily,
      tenor,
      ...DEFAULTS,
    });
  };

  // ----- Controls -----
  const tenorOptions =
    BREAKEVEN_TENOR_OPTIONS_BY_PAIR[linkerFamily] ??
    BREAKEVEN_TENOR_OPTIONS_BY_PAIR.USD_TIPS;
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'pair',
      label: 'Country Pair',
      kind: 'enum',
      value: linkerFamily,
      options: BREAKEVEN_PAIR_OPTIONS,
    },
    {
      name: 'tenor',
      label: 'Tenor',
      kind: 'enum',
      value: tenor,
      options: tenorOptions,
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
  ];

  // ----- Top-right cards: Z-score / Percentile / Pair -----
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
  const pair = pairForLinker(linkerFamily);
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
      key: 'pair',
      node: (
        <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
          <span className="kicker text-fg-muted">COUNTRY PAIR</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {pair ? `${pair.country} · ${pair.nominalShort} / ${pair.linkerShort}` : linkerFamily}
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            {BREAKEVEN_COMPACT_CAVEAT}
          </span>
        </div>
      ),
    },
  ];

  const identitySubtitle = pair
    ? `${pair.country} bond-implied breakeven · ${pair.nominalShort} nominal − ${pair.linkerShort} real yield`
    : 'Bond-implied breakeven · nominal − linker real yield';

  return (
    <BuildExtendedShell
      category={{
        name: 'BREAKEVEN INFLATION',
        tags: ['SNAPSHOT', 'DETERMINISTIC', 'COMPENSATION'],
      }}
      identity={{
        primary: pair ? `${pair.country} ${tenor}` : `${linkerFamily} ${tenor}`,
        secondary: 'Breakeven',
        flag: undefined,
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
                nominal_curve_family: nominalFamily,
                linker_curve_family: linkerFamily,
                tenor,
                breakeven_label: '',
                breakeven_pct: NaN,
                breakeven_bps: NaN,
                daily_change_bps: null,
                weekly_change_bps: null,
                monthly_change_bps: null,
                current_z_score: null,
                rolling_window_days: 252,
                high_252d_bps: null,
                low_252d_bps: null,
                percentile_252d: null,
                nominal_yield_pct: null,
                real_yield_pct: null,
                methodology_label: '',
              },
              time_series: [],
            })
      }
      chartPoints={sanitiseBreakevenSeries(
        data?.time_series_breakeven?.rows ?? [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="bp"
      chartValueDecimals={1}
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
        kind: 'Deterministic snapshot (nominal − linker real)',
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
