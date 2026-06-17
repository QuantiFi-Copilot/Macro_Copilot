// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_breakeven_butterfly_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is the breakeven butterfly's full canvas.  Mounted by
// VirtualPrimitiveCanvas for single-tool queries OR by the click-to-expand
// modal from a compact card.  Design reference: ./mockups/Extended.png.
//
// Mockup-faithful design choices:
//   - A SINGLE "Country Pair" dropdown (not two) — a breakeven butterfly
//     is a same-country object, so the linker family uniquely picks the
//     pair; the control expands to nominal_curve_family + linker_curve_family.
//   - A SINGLE "Triplet" dropdown for the three tenor points (e.g.
//     2s5s10s) — surfacing three independent dropdowns invites invalid
//     orderings; the structural strict-ordering Pydantic validator
//     rejects those, so the input layer enforces validity instead.
//   - bps-scale KPI strip with the butterfly + period changes + z/percentile
//     + 252d range + observation count.
//   - A separate decomposition row exposing the three endpoint breakevens
//     and the two component wing spreads — the desk can audit the
//     butterfly construction on the same screen.
//   - The "inflation compensation curvature, not pure expected-inflation
//     curvature" caveat surfaces in the methodology card via the backend's
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
  type BuildExtendedProps,
  type ControlDescriptor,
  type TopRightCard,
} from '@/components/shared/build';
import {
  BREAKEVEN_BUTTERFLY_COMPACT_CAVEAT,
  BREAKEVEN_BUTTERFLY_PAIR_OPTIONS,
  BREAKEVEN_BUTTERFLY_TRIPLETS_BY_PAIR,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  decompositionKPIs,
  extendedKPIs,
  pairForLinker,
  sanitiseButterflySeries,
  tripletLabel,
  useBreakevenButterfly,
  zScoreCaptionForButterfly,
} from './breakevenButterflyShared';

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
  const nominalFamily = params.nominal_curve_family ?? 'UST';
  const linkerFamily = params.linker_curve_family ?? 'USD_TIPS';
  const shortTenor = params.short_tenor ?? '5Y';
  const bellyTenor = params.belly_tenor ?? '10Y';
  const longTenor = params.long_tenor ?? '30Y';
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useBreakevenButterfly({
    nominalCurveFamily: nominalFamily,
    linkerCurveFamily: linkerFamily,
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
    BREAKEVEN_BUTTERFLY_TRIPLETS_BY_PAIR[linkerFamily] ??
    BREAKEVEN_BUTTERFLY_TRIPLETS_BY_PAIR.USD_TIPS
  ).map((t) => ({
    value: `${t.short}|${t.belly}|${t.long}`,
    label: t.label,
  }));

  const handleControlChange = (name: string, value: string) => {
    const nextParams = { ...params };
    if (name === 'pair') {
      const meta = pairForLinker(value);
      nextParams.linker_curve_family = value;
      if (meta) nextParams.nominal_curve_family = meta.nominalFamily;
      // Snap to the first registered triplet for the new pair so the
      // (short, belly, long) tuple stays valid against the new family's
      // tenor grid.
      const triplets = BREAKEVEN_BUTTERFLY_TRIPLETS_BY_PAIR[value] ?? [];
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
      nominal_curve_family: nominalFamily,
      linker_curve_family: linkerFamily,
      short_tenor: shortTenor,
      belly_tenor: bellyTenor,
      long_tenor: longTenor,
      ...DEFAULTS,
    });
  };

  // ----- Controls -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'pair',
      label: 'Country Pair',
      kind: 'enum',
      value: linkerFamily,
      options: BREAKEVEN_BUTTERFLY_PAIR_OPTIONS,
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

  // ----- Top-right cards: Z-score / Percentile / Pair -----
  const cm = data?.current_metrics;
  const zCaption = zScoreCaptionForButterfly(cm?.current_z_score);
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
      key: 'pair',
      node: (
        <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
          <span className="kicker text-fg-muted">COUNTRY PAIR</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {pair ? `${pair.country} · ${pair.nominalShort} / ${pair.linkerShort}` : linkerFamily}
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            {BREAKEVEN_BUTTERFLY_COMPACT_CAVEAT}
          </span>
        </div>
      ),
    },
  ];

  const triplet = tripletLabel(shortTenor, bellyTenor, longTenor);
  const identityPrimary = pair
    ? `${pair.country} ${triplet} BREAKEVEN FLY`
    : `${linkerFamily} ${triplet} BREAKEVEN FLY`;
  const identitySubtitle = pair
    ? `${pair.country} bond-implied breakeven butterfly · belly − ½(short + long)`
    : 'Bond-implied breakeven butterfly · belly − ½(short + long)';

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
          short_breakeven_bps: null,
          belly_breakeven_bps: null,
          long_breakeven_bps: null,
          short_years: 0,
          belly_years: 0,
          long_years: 0,
          observation_count: 0,
          methodology_label: '',
        },
        time_series: [],
        // Empty stub TimeSeries — extendedKPIs() reads only
        // current_metrics, so the rendered KPI strip is unaffected by the
        // empty rows.  Required since BreakevenButterflyOutput now
        // mirrors the Pydantic Output exactly (time_series_butterfly +
        // time_series_zscore are non-optional).
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
        name: 'BREAKEVEN BUTTERFLY',
        tags: ['SNAPSHOT', 'DETERMINISTIC', 'CURVATURE'],
      }}
      identity={{
        primary: identityPrimary,
        secondary: 'Belly − ½(wings)',
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
      chartPoints={sanitiseButterflySeries(
        data?.time_series_butterfly?.rows ?? [],
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
        kind: 'Deterministic snapshot (belly − ½(short + long) on bond-implied breakevens)',
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
