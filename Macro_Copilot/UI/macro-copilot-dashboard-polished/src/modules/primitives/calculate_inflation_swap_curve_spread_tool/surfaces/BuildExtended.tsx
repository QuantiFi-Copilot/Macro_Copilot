// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_inflation_swap_curve_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is the ZCIS curve spread's full canvas.  Mounted by
// VirtualPrimitiveCanvas for single-tool queries OR by the click-to-expand
// modal from a compact card.  Design reference: ./mockups/Extended.png.
//
// Mockup-faithful design choices:
//   - A SINGLE "ZCIS Curve" dropdown (the same-curve invariant means the
//     curve_family alone uniquely identifies the index family / lag /
//     interpolation triple — surfacing those as separate controls would
//     invite invalid combinations the backend would reject anyway).
//   - SHORT TENOR + LONG TENOR as TWO separate dropdowns; the long-tenor
//     options are filtered to strictly-longer tenors so the "long > short"
//     validity rule is enforced at the input layer (the backend re-
//     validates regardless).
//   - bps-scale KPI strip with the spread + period changes + z/percentile
//     + 252d range.
//   - A separate decomposition row exposing the two endpoint ZCIS rates
//     (PERCENT) — the desk can audit the spread construction on the same
//     screen.
//   - The "OTC ZCIS curve; basis to bond BE is a separate primitive"
//     caveat surfaces in the methodology card via the backend's
//     methodology_label (PR10 / P5 — NOT a hardcoded TS literal).
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
  ZScoreRegimeSlider,
  type BuildExtendedProps,
  type ControlDescriptor,
  type TopRightCard,
} from '@/components/shared/build';
import {
  ZCIS_CURVE_SPREAD_CURVE_OPTIONS,
  ZCIS_CURVE_SPREAD_TENORS_BY_CURVE,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  decompositionKPIs,
  extendedKPIs,
  sanitiseSpreadSeries,
  spreadShortLabel,
  tenorToYears,
  useInflationSwapCurveSpread,
  zcisCurveFamilyFor,
  zScoreSlopeCaption,
} from './inflationSwapCurveSpreadShared';

const LOOKBACK_OPTIONS = [
  { value: '90', label: '90d' },
  { value: '180', label: '180d' },
  { value: '365', label: '1Y' },
  { value: '730', label: '2Y' },
  { value: '1825', label: '5Y' },
];

const FIELD_OPTIONS = [
  { value: 'PX_MID', label: 'PX_MID' },
  { value: 'PX_LAST', label: 'PX_LAST' },
  { value: 'PX_BID', label: 'PX_BID' },
  { value: 'PX_ASK', label: 'PX_ASK' },
];

const DEFAULTS = {
  lookback_days: '365',
  field_name: 'PX_MID',
};

/** Long-tenor options for a curve = the curve's tenors strictly longer
 *  than the selected short tenor (enforces the long > short rule). */
function longTenorOptions(curveFamily: string, shortTenor: string) {
  const all =
    ZCIS_CURVE_SPREAD_TENORS_BY_CURVE[curveFamily]
    ?? ZCIS_CURVE_SPREAD_TENORS_BY_CURVE.USD_ZCIS;
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
  const curveFamily = params.curve_family ?? 'EUR_ZCIS';
  const shortTenor = params.short_tenor ?? '2Y';
  const longTenor = params.long_tenor ?? '10Y';
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useInflationSwapCurveSpread({
    curveFamily,
    shortTenor,
    longTenor,
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
    if (name === 'curve_family') {
      nextParams.curve_family = value;
      // Snap to the first valid (short, long) pair on the new family's
      // tenor grid so the spread stays valid.
      const tenors = ZCIS_CURVE_SPREAD_TENORS_BY_CURVE[value] ?? [];
      if (tenors.length >= 2) {
        nextParams.short_tenor = tenors[0].value;
        nextParams.long_tenor = tenors[1].value;
      }
    } else if (name === 'short_tenor') {
      nextParams.short_tenor = value;
      // If the new short tenor is >= the current long tenor, bump the
      // long tenor to the next valid (strictly-longer) option.
      const opts = longTenorOptions(curveFamily, value);
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
      curve_family: curveFamily,
      short_tenor: shortTenor,
      long_tenor: longTenor,
      ...DEFAULTS,
    });
  };

  // ----- Controls -----
  const shortOptions =
    ZCIS_CURVE_SPREAD_TENORS_BY_CURVE[curveFamily]
    ?? ZCIS_CURVE_SPREAD_TENORS_BY_CURVE.USD_ZCIS;
  const longOptions = longTenorOptions(curveFamily, shortTenor);
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family',
      label: 'ZCIS Curve',
      kind: 'enum',
      value: curveFamily,
      options: ZCIS_CURVE_SPREAD_CURVE_OPTIONS,
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
    asOfDateControl(params.as_of_date),
  ];

  // ----- Top-right cards: Z-score / Percentile / signed 5-zone regime -----
  // The ZCIS curve spread is a SIGNED measure (steepening vs flattening
  // read differently), so we surface the signed 5-zone ZScoreRegimeSlider
  // — mirroring the breakeven-curve-spread shape-twin's strip.
  const cm = data?.current_metrics;
  const zCaption = zScoreSlopeCaption(cm?.z_score_252d);
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
            className={`text-[26px] font-medium leading-none ${toneTextClass(toneForZScore(cm?.z_score_252d))}`}
          >
            {signedFixed(cm?.z_score_252d ?? null, 2)}
          </div>
          <span className={`text-[11.5px] ${toneTextClass(toneForZScore(cm?.z_score_252d))}`}>
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
          <ZScoreRegimeSlider value={cm?.z_score_252d} />
        </div>
      ),
    },
  ];

  const meta = zcisCurveFamilyFor(curveFamily);
  const pairLabel = spreadShortLabel(shortTenor, longTenor);
  const identityPrimary = meta
    ? `${meta.country} ${pairLabel} ZCIS SPREAD`
    : `${curveFamily} ${pairLabel} ZCIS SPREAD`;
  const identitySubtitle = meta
    ? `${meta.marketShort} ZCIS curve · long − short`
    : 'Zero-Coupon Inflation Swap curve spread · long − short';

  // Combine the headline KPI strip and the decomposition row.  The shell
  // renders them as one continuous strip; the decomposition labels carry
  // their own tenor markers so the grouping reads naturally even though
  // the visual frame is shared.
  const allKpis = data
    ? [...extendedKPIs(data), ...decompositionKPIs(data)]
    : extendedKPIs({
        current_metrics: {
          as_of_date: '',
          curve_family: curveFamily,
          short_tenor: shortTenor,
          long_tenor: longTenor,
          spread_label: '',
          spread_bps: NaN,
          change_1d_bps: null,
          change_1w_bps: null,
          change_1m_bps: null,
          z_score_252d: null,
          high_252d_bps: null,
          low_252d_bps: null,
          percentile_252d: null,
          short_zcis_rate_pct: null,
          long_zcis_rate_pct: null,
          short_years: 0,
          long_years: 0,
          observation_count: 0,
          inflation_index_family: '',
          index_lag: '',
          interpolation: '',
          underlying_index: null,
          methodology_label: '',
        },
        time_series: [],
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
        name: 'ZCIS CURVE SPREAD',
        tags: ['CURVE SHAPE', 'DETERMINISTIC'],
      }}
      identity={{
        primary: identityPrimary,
        secondary: `${meta ? `${meta.marketShort} · ${meta.indexShort}` : curveFamily} · ${shortTenor} − ${longTenor}`,
        flag: meta?.flag,
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
        kind: 'Deterministic snapshot (long − short on ZCIS rates × 100)',
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
