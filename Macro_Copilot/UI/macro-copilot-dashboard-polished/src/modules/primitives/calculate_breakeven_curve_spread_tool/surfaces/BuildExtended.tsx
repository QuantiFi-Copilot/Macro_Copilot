// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_breakeven_curve_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is the breakeven curve spread's full canvas.  Mounted by
// VirtualPrimitiveCanvas for single-tool queries OR by the click-to-expand
// modal from a compact card.  Design reference: ./mockups/Extended.png.
//
// Mockup-faithful design choices:
//   - A SINGLE "Country Pair" dropdown (not two) — a breakeven curve spread
//     is a same-country object, so the linker family uniquely picks the
//     pair; the control expands to nominal_curve_family + linker_curve_family.
//   - SHORT TENOR + LONG TENOR as TWO separate dropdowns; the long-tenor
//     options are filtered to strictly-longer tenors so the "long > short"
//     validity rule is enforced at the input layer (the backend re-validates
//     regardless).
//   - bps-scale KPI strip with the spread + period changes + z/percentile
//     + 252d range.
//   - A separate decomposition row exposing the two endpoint breakevens —
//     the desk can audit the spread construction on the same screen.
//   - The "inflation compensation term structure, not pure expected
//     inflation" caveat surfaces in the methodology card via the backend's
//     methodology_label (PR10 / P5 — NOT a hardcoded TS literal).
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  BuildExtendedShell,
  percentileLabel,
  signedFixed,
  toneForZScore,
  toneTextClass,
  ZScoreRegimeSlider,
  type BuildExtendedProps,
  type ControlDescriptor,
  type TopRightCard,
} from '@/components/shared/build';
import {
  BREAKEVEN_CURVE_SPREAD_PAIR_OPTIONS,
  BREAKEVEN_CURVE_SPREAD_TENORS_BY_PAIR,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  decompositionKPIs,
  extendedKPIs,
  pairForLinker,
  sanitiseSpreadSeries,
  spreadShortLabel,
  tenorToYears,
  useBreakevenCurveSpread,
  zScoreSlopeCaption,
} from './breakevenCurveSpreadShared';

const LOOKBACK_OPTIONS = [
  { value: '90', label: '90d' },
  { value: '180', label: '180d' },
  { value: '365', label: '1Y' },
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

/** Long-tenor options for a pair = the pair's tenors strictly longer
 *  than the selected short tenor (enforces the long > short rule). */
function longTenorOptions(linkerFamily: string, shortTenor: string) {
  const all =
    BREAKEVEN_CURVE_SPREAD_TENORS_BY_PAIR[linkerFamily]
    ?? BREAKEVEN_CURVE_SPREAD_TENORS_BY_PAIR.USD_TIPS;
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

  // ----- Resolve effective params -----
  const nominalFamily = params.nominal_curve_family ?? 'UST';
  const linkerFamily = params.linker_curve_family ?? 'USD_TIPS';
  const shortTenor = params.short_tenor ?? '2Y';
  const longTenor = params.long_tenor ?? '10Y';
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useBreakevenCurveSpread({
    nominalCurveFamily: nominalFamily,
    linkerCurveFamily: linkerFamily,
    shortTenor,
    longTenor,
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
    if (name === 'pair') {
      const meta = pairForLinker(value);
      nextParams.linker_curve_family = value;
      if (meta) nextParams.nominal_curve_family = meta.nominalFamily;
      // Snap to the first valid (short, long) pair on the new family's
      // tenor grid so the spread stays valid.
      const tenors = BREAKEVEN_CURVE_SPREAD_TENORS_BY_PAIR[value] ?? [];
      if (tenors.length >= 2) {
        nextParams.short_tenor = tenors[0].value;
        nextParams.long_tenor = tenors[1].value;
      }
    } else if (name === 'short_tenor') {
      nextParams.short_tenor = value;
      // If the new short tenor is >= the current long tenor, bump the
      // long tenor to the next valid (strictly-longer) option.
      const opts = longTenorOptions(linkerFamily, value);
      if (!opts.some((o) => o.value === longTenor) && opts.length > 0) {
        nextParams.long_tenor = opts[0].value;
      }
    } else {
      nextParams[name] = value;
    }
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({
      nominal_curve_family: nominalFamily,
      linker_curve_family: linkerFamily,
      short_tenor: shortTenor,
      long_tenor: longTenor,
      ...DEFAULTS,
    });
  };

  // ----- Controls -----
  const shortOptions =
    BREAKEVEN_CURVE_SPREAD_TENORS_BY_PAIR[linkerFamily]
    ?? BREAKEVEN_CURVE_SPREAD_TENORS_BY_PAIR.USD_TIPS;
  const longOptions = longTenorOptions(linkerFamily, shortTenor);
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'pair',
      label: 'Country Pair',
      kind: 'enum',
      value: linkerFamily,
      options: BREAKEVEN_CURVE_SPREAD_PAIR_OPTIONS,
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
  ];

  // ----- Top-right cards: Z-score / Percentile / signed 5-zone regime -----
  // The breakeven curve spread is a SIGNED measure (steepening vs
  // flattening read differently), so we surface the signed 5-zone
  // ZScoreRegimeSlider here — mirroring the real_yield_curve_spread
  // shape-twin's top-right strip.
  const cm = data?.current_metrics;
  const zCaption = zScoreSlopeCaption(cm?.current_z_score);
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
      key: 'regime',
      node: (
        <div className="card flex h-full flex-col justify-center gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">CURVE REGIME (SIGNED)</span>
          <ZScoreRegimeSlider value={cm?.current_z_score} />
        </div>
      ),
    },
  ];

  const pair = pairForLinker(linkerFamily);
  const pairLabel = spreadShortLabel(shortTenor, longTenor);
  const identityPrimary = pair
    ? `${pair.country} ${pairLabel} BREAKEVEN SPREAD`
    : `${linkerFamily} ${pairLabel} BREAKEVEN SPREAD`;
  const identitySubtitle = pair
    ? `${pair.country} bond-implied breakeven curve spread · long − short`
    : 'Bond-implied breakeven curve spread · long − short';

  // Combine the headline KPI strip and the decomposition row.  The shell
  // renders them as one continuous strip; the decomposition labels carry
  // their own tenor markers so the grouping reads naturally even though
  // the visual frame is shared.
  const allKpis = data
    ? [...extendedKPIs(data), ...decompositionKPIs(data)]
    : extendedKPIs({
        current_metrics: {
          as_of_date: '',
          nominal_curve_family: nominalFamily,
          linker_curve_family: linkerFamily,
          short_tenor: shortTenor,
          long_tenor: longTenor,
          spread_label: '',
          current_spread_bps: null,
          daily_change_bps: null,
          weekly_change_bps: null,
          monthly_change_bps: null,
          current_z_score: null,
          rolling_window_days: 252,
          high_252d_bps: null,
          low_252d_bps: null,
          percentile_252d: null,
          short_breakeven_bps: null,
          long_breakeven_bps: null,
          short_years: 0,
          long_years: 0,
          methodology_label: '',
        },
        time_series: [],
        // Empty stub TimeSeries — extendedKPIs() reads only
        // current_metrics, so the rendered KPI strip is unaffected by the
        // empty rows.  Required since BreakevenCurveSpreadOutput mirrors
        // the Pydantic Output exactly (time_series_spread +
        // time_series_zscore are non-optional).
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
      });

  return (
    <BuildExtendedShell
      category={{
        name: 'BREAKEVEN CURVE SPREAD',
        tags: ['CURVE SHAPE', 'DETERMINISTIC'],
      }}
      identity={{
        primary: identityPrimary,
        secondary: `${pair ? `${pair.nominalShort} / ${pair.linkerShort}` : linkerFamily} · ${shortTenor} − ${longTenor}`,
        flag: undefined,
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
        data?.time_series_spread?.rows ?? [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="bp"
      chartValueDecimals={1}
      referenceBands={data ? buildReferenceBands(data) : []}
      stretchContext={data ? buildStretchContext(data) : undefined}
      methodology={data ? buildMethodologyRows(data, fieldName) : []}
      methodologyReferences={buildReferenceChips()}
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic snapshot (long − short on bond-implied breakevens)',
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
