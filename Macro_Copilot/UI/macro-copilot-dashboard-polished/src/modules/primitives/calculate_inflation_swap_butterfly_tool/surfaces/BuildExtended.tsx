// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_inflation_swap_butterfly_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is the same-curve ZCIS butterfly's full canvas.  Mounted by
// VirtualPrimitiveCanvas for single-tool queries OR by the click-to-expand
// modal from a compact card.  Design reference: ./mockups/Extended.png.
//
// Mockup-faithful design choices:
//   - A SINGLE "ZCIS Curve" dropdown (single-curve primitive — distinct
//     from cross_market_inflation_swap_spread, which crosses two families).
//   - A SINGLE "Triplet" dropdown — registered (short, belly, long) presets
//     per curve_family.  The Pydantic schema's
//     ``_short_belly_long_strictly_ordered`` validator rejects inverted
//     orderings; surfacing three independent dropdowns would invite invalid
//     selections.
//   - bps-scale KPI strip — the backend already ships the butterfly +
//     period changes + 252d range + wing spreads in BPS (no unit conversion).
//   - A separate decomposition row exposing the three endpoint ZCIS rates
//     (PERCENT) + two wing spreads (BPS) so the desk can audit
//     ``(belly_zcis − 0.5 × (short + long)) × 100`` on the same screen.
//   - The "CPI-family caveat" (CPI-U / HICPxT / RPI are NOT fungible) surfaces
//     in the methodology card via the backend's index_family + index_lag
//     metadata AND the methodology_label (PR10 / P5 — NOT a hardcoded TS
//     literal).
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  BuildExtendedShell,
  asOfDateControl,
  bucketForPercentile,
  percentileLabel,
  signedFixed,
  toneForZScore,
  toneTextClass,
  type BuildExtendedProps,
  type ControlDescriptor,
  type TopRightCard,
} from '@/components/shared/build';
import {
  INFLATION_SWAP_BUTTERFLY_COMPACT_CAVEAT,
  INFLATION_SWAP_BUTTERFLY_CURVE_OPTIONS,
  INFLATION_SWAP_BUTTERFLY_TRIPLETS_BY_CURVE,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  decompositionKPIs,
  extendedKPIs,
  sanitiseButterflySeries,
  tripletHyphenLabel,
  useInflationSwapButterfly,
  zScoreCaptionForButterfly,
  zcisFamilyFor,
} from './inflationSwapButterflyShared';

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
  const curveFamily = params.curve_family ?? 'USD_ZCIS';
  const shortTenor = params.short_tenor ?? '2Y';
  const bellyTenor = params.belly_tenor ?? '5Y';
  const longTenor = params.long_tenor ?? '10Y';
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useInflationSwapButterfly({
    curveFamily,
    shortTenor,
    bellyTenor,
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

  // Triplet selector encodes the (short, belly, long) tuple as a single
  // string ("2Y|5Y|10Y") to keep the URL state flat.
  const tripletKey = `${shortTenor}|${bellyTenor}|${longTenor}`;
  const tripletOptions = (
    INFLATION_SWAP_BUTTERFLY_TRIPLETS_BY_CURVE[curveFamily]
    ?? INFLATION_SWAP_BUTTERFLY_TRIPLETS_BY_CURVE.USD_ZCIS
  ).map((t) => ({
    value: `${t.short}|${t.belly}|${t.long}`,
    label: t.label,
  }));

  const handleControlChange = (name: string, value: string) => {
    const nextParams = { ...params };
    if (name === 'curve_family') {
      nextParams.curve_family = value;
      // Snap to the first registered triplet for the new curve so the
      // (short, belly, long) tuple stays valid against the new pillar grid.
      const triplets = INFLATION_SWAP_BUTTERFLY_TRIPLETS_BY_CURVE[value] ?? [];
      if (triplets.length > 0) {
        nextParams.short_tenor = triplets[0].short;
        nextParams.belly_tenor = triplets[0].belly;
        nextParams.long_tenor = triplets[0].long;
      }
    } else if (name === 'triplet') {
      const [s, b, l] = value.split('|');
      if (s && b && l) {
        nextParams.short_tenor = s;
        nextParams.belly_tenor = b;
        nextParams.long_tenor = l;
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
      belly_tenor: bellyTenor,
      long_tenor: longTenor,
      ...DEFAULTS,
    });
  };

  // ----- Controls -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family',
      label: 'ZCIS Curve',
      kind: 'enum',
      value: curveFamily,
      options: INFLATION_SWAP_BUTTERFLY_CURVE_OPTIONS,
    },
    {
      name: 'triplet',
      label: 'Triplet',
      kind: 'enum',
      value: tripletKey,
      options: tripletOptions,
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

  // ----- Top-right cards: Z-score / Percentile / Index Family -----
  const cm = data?.current_metrics;
  const zCaption = zScoreCaptionForButterfly(cm?.current_z_score);
  const pBucket = bucketForPercentile(cm?.percentile_252d ?? null);
  const familyMeta = zcisFamilyFor(curveFamily);
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
      key: 'index_family',
      node: (
        <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
          <span className="kicker text-fg-muted">INDEX FAMILY</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {familyMeta
              ? `${familyMeta.marketShort} · ${familyMeta.indexShort}`
              : (cm?.inflation_index_family ?? curveFamily)}
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            {INFLATION_SWAP_BUTTERFLY_COMPACT_CAVEAT}
          </span>
        </div>
      ),
    },
  ];

  const triplet = tripletHyphenLabel(shortTenor, bellyTenor, longTenor);
  const identityPrimary = familyMeta
    ? `${familyMeta.marketShort} ZCIS ${triplet} BUTTERFLY`
    : `${curveFamily} ${triplet} BUTTERFLY`;
  const identitySubtitle = familyMeta
    ? `${familyMeta.marketShort}_ZCIS_${familyMeta.indexShort.replace('-', '')} ${shortTenor} · ${bellyTenor} · ${longTenor} · Zero-Coupon Inflation Swap`
    : `${curveFamily} ${shortTenor} · ${bellyTenor} · ${longTenor} · Zero-Coupon Inflation Swap`;

  // Combine the headline KPI strip and the decomposition row.  The shell
  // renders them as one continuous strip; the decomposition labels carry
  // their own tenor markers so the grouping reads naturally.
  const allKpis = data
    ? [...extendedKPIs(data), ...decompositionKPIs(data)]
    : extendedKPIs({
        current_metrics: {
          as_of_date: '',
          curve_family: curveFamily,
          short_tenor: shortTenor,
          belly_tenor: bellyTenor,
          long_tenor: longTenor,
          butterfly_label: '',
          current_butterfly_bps: null,
          daily_change_bps: null,
          weekly_change_bps: null,
          monthly_change_bps: null,
          current_z_score: null,
          rolling_window_days: 252,
          high_252d_bps: null,
          low_252d_bps: null,
          percentile_252d: null,
          wing_short_bps: null,
          wing_long_bps: null,
          short_zcis_rate_pct: null,
          belly_zcis_rate_pct: null,
          long_zcis_rate_pct: null,
          short_years: 0,
          belly_years: 0,
          long_years: 0,
          observation_count: 0,
          inflation_index_family: '',
          index_lag: '',
          interpolation: '',
          underlying_index: null,
          methodology_label: '',
        },
        time_series: [],
        // Empty stub TimeSeries — extendedKPIs() reads only current_metrics,
        // so the rendered KPI strip is unaffected.  Required since
        // InflationSwapButterflyOutput mirrors the Pydantic Output exactly
        // (time_series_butterfly + time_series_zscore are non-optional).
        time_series_butterfly: {
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
        name: 'INFLATION SWAP BUTTERFLY',
        tags: ['SNAPSHOT', 'DETERMINISTIC', 'CURVATURE'],
      }}
      identity={{
        primary: identityPrimary,
        secondary: familyMeta ? `· ${familyMeta.indexShort}` : undefined,
        flag: familyMeta?.flag,
        subtitle: identitySubtitle,
        asOfDate: cm?.as_of_date,
        meta: `${lookbackDays}d window · ${fieldName}`,
      }}
      topRightCards={topRightCards}
      controls={controls}
      onControlChange={handleControlChange}
      onResetControls={handleReset}
      kpis={allKpis}
      chartPoints={sanitiseButterflySeries(
        data?.time_series_butterfly?.rows ?? [],
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
        kind: 'Deterministic snapshot (belly − ½(short + long) on single ZCIS curve, raw rate space)',
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
