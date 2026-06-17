// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_cross_country_breakeven_spread_simple_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is cross-country breakeven's full canvas.  Mounted by
// VirtualPrimitiveCanvas for single-tool queries OR by the click-to-expand
// modal from a compact card.  Design reference: ./mockups/Extended.png.
//
// Mockup-faithful design choices:
//   - Title "UK 10Y BE — US 10Y BE" with per-country subtitle.
//   - Two country pair dropdowns (Country A + Country B) plus tenor +
//     lookback + field (the structural cross-country invariant means we
//     expose both country pairs as packaged (nominal, linker) tuples).
//   - bps-scale KPI strip + per-country BREAKEVEN BPS decomposition.
//   - Top-right cards: Z-score / Percentile / Index-family caveat.
//   - The index-family mismatch caveat (USD TIPS → CPI-U / UK GILT
//     LINKERS → RPI / FR OAT LINKERS → HICPxT / CAD RRBs → Canada CPI
//     reference different indices — NOT a clean expected-inflation
//     divergence) surfaces inline via the wire's
//     ``current_metrics.methodology_label`` and the per-country pair
//     metadata.
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
  COUNTRY_PAIR_OPTIONS,
  TENOR_OPTIONS,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  countryPairFor,
  extendedKPIs,
  identitySubtitle,
  pairShortLabel,
  sanitiseSpreadSeries,
  shortIndexCaveat,
  useCrossCountryBreakevenSpread,
} from './crossCountryBreakevenSpreadSimpleShared';

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

/** Split a packaged ``country_x_pair`` URL param of the form
 *  ``"UST/USD_TIPS"`` into its two sub-fields.  The controls strip
 *  exposes ONE select per country (packaged pair) for cleanliness;
 *  the data hook receives the four nominal/linker strings the wire
 *  expects.  Falls back to defaults when the URL param is missing. */
function splitPair(packed: string | undefined, defaultPacked: string): { nominal: string; linker: string } {
  const value = packed && packed.includes('/') ? packed : defaultPacked;
  const [nominal, linker] = value.split('/');
  return { nominal, linker };
}

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params -----
  const pairAPacked = params.country_a_pair ?? 'UK_GILT/GBP_LINKER';
  const pairBPacked = params.country_b_pair ?? 'UST/USD_TIPS';
  const { nominal: aNominal, linker: aLinker } = splitPair(pairAPacked, 'UK_GILT/GBP_LINKER');
  const { nominal: bNominal, linker: bLinker } = splitPair(pairBPacked, 'UST/USD_TIPS');
  const tenor = params.tenor ?? '10Y';
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useCrossCountryBreakevenSpread({
    countryANominalPair: aNominal,
    countryALinkerPair: aLinker,
    countryBNominalPair: bNominal,
    countryBLinkerPair: bLinker,
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
    // Cross-country invariant: country_a_pair != country_b_pair.  When
    // the user picks a pair that equals the other, snap the other to
    // the first different pair option in the registry order.
    if (name === 'country_a_pair' && value === nextParams.country_b_pair) {
      const fallback = COUNTRY_PAIR_OPTIONS.find((o) => o.value !== value);
      if (fallback) nextParams.country_b_pair = fallback.value;
    }
    if (name === 'country_b_pair' && value === nextParams.country_a_pair) {
      const fallback = COUNTRY_PAIR_OPTIONS.find((o) => o.value !== value);
      if (fallback) nextParams.country_a_pair = fallback.value;
    }
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({
      country_a_pair: pairAPacked,
      country_b_pair: pairBPacked,
      tenor,
      ...DEFAULTS,
    });
  };

  // ----- Controls -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'country_a_pair',
      label: 'Country A',
      kind: 'enum',
      value: pairAPacked,
      options: COUNTRY_PAIR_OPTIONS,
    },
    {
      name: 'country_b_pair',
      label: 'Country B',
      kind: 'enum',
      value: pairBPacked,
      options: COUNTRY_PAIR_OPTIONS,
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
    asOfDateControl(params.as_of_date),
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
  const aMeta = countryPairFor(aNominal, aLinker);
  const bMeta = countryPairFor(bNominal, bLinker);
  const indexFamilyHeadline =
    aMeta && bMeta
      ? `${aMeta.indexShort} vs ${bMeta.indexShort}`
      : 'Cross-country';
  const indexFamilyCaveat = shortIndexCaveat(aNominal, aLinker, bNominal, bLinker);

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

  const subtitle = identitySubtitle(aNominal, aLinker, bNominal, bLinker, tenor);
  const pairLabel = pairShortLabel(aNominal, aLinker, bNominal, bLinker);
  const observationCount = data?.time_series?.length ?? 0;

  return (
    <BuildExtendedShell
      category={{
        name: 'CROSS-COUNTRY BOND BREAKEVEN SPREAD',
        tags: ['SNAPSHOT', 'DETERMINISTIC', 'CROSS-COUNTRY'],
      }}
      identity={{
        primary: `${pairLabel} ${tenor} BE`,
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
                  country_a_nominal_pair: aNominal,
                  country_a_linker_pair: aLinker,
                  country_b_nominal_pair: bNominal,
                  country_b_linker_pair: bLinker,
                  tenor,
                  tenor_years: NaN,
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
                  breakeven_a_bps: null,
                  breakeven_b_bps: null,
                  methodology_label: '',
                },
                time_series: [],
                // Empty stub TimeSeries — the KPI builder reads only
                // current_metrics, so the rendered strip is unaffected
                // by the empty rows.  Required since
                // CrossCountryBreakevenSpreadSimpleOutput mirrors the
                // Pydantic Output exactly (time_series_spread +
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
              },
              0,
            )
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
        kind: 'Deterministic snapshot (breakeven_a − breakeven_b, inner-join)',
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
