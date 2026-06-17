// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_ois_curve_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is the same-curve OIS curve-spread's full canvas.  Mounted by
// VirtualPrimitiveCanvas for single-tool queries OR by the click-to-expand
// modal from a compact card.  Design reference: ./mockups/Extended.png.
//
// Mockup-faithful design choices:
//   - A SINGLE "OIS Curve" dropdown (single-curve primitive — distinct from
//     calculate_ois_cross_market_spread, which crosses two families).
//   - SHORT TENOR + LONG TENOR as TWO separate dropdowns; the long-tenor
//     options are filtered to strictly-longer tenors so the "long > short"
//     validity rule is enforced at the input layer (the backend re-validates
//     ``short_tenor != long_tenor`` regardless).
//   - bps-scale KPI strip — the backend already ships the spread + 1d change
//     in BPS (no unit conversion).  Percentile / 252d high / 252d low /
//     observation_count / 3m / 12m changes are computed CLIENT-SIDE from
//     ``time_series_spread.rows`` because the OIS curve-spread Output is
//     leaner than the linker / sovereign siblings — surfacing only the
//     fields the wire carries OR the desk can derive without inventing.
//   - A separate decomposition row exposing the two endpoint OIS rates
//     (PERCENT) so the desk can audit ``long − short`` on the same screen.
//   - The "risk-neutral OIS curve shape" caveat surfaces in the methodology
//     card.  TODO(PR10): when the backend ships
//     ``current_metrics.methodology_label`` the "Disclosure" row switches to
//     consume the wire — see ./oisCurveSpreadShared.ts for the marker.
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
  OIS_CURVE_OPTIONS,
  OIS_CURVE_SPREAD_COMPACT_CAVEAT,
  TENOR_OPTIONS_BY_CURVE,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  decompositionKPIs,
  extendedKPIs,
  oisFamilyFor,
  sanitiseSpreadSeries,
  spreadShortLabel,
  tenorToYears,
  trailingPercentile252d,
  useOisCurveSpread,
  zScoreCaptionForSpread,
} from './oisCurveSpreadShared';

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

/** Long-tenor options for a curve = the family's tenors strictly longer
 *  than the selected short tenor (enforces the long > short rule). */
function longTenorOptions(curveFamily: string, shortTenor: string) {
  const all =
    TENOR_OPTIONS_BY_CURVE[curveFamily] ?? TENOR_OPTIONS_BY_CURVE.USD_SOFR_OIS;
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
  const curveFamily = params.curve_family ?? 'USD_SOFR_OIS';
  const shortTenor = params.short_tenor ?? '2Y';
  const longTenor = params.long_tenor ?? '10Y';
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useOisCurveSpread({
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
    const nextParams = { ...params, [name]: value };
    if (name === 'curve_family') {
      // Reset to the new family's first valid (short, long) pair.
      const tenors = TENOR_OPTIONS_BY_CURVE[value] ?? [];
      if (tenors.length >= 2) {
        nextParams.short_tenor = tenors[0].value;
        nextParams.long_tenor = tenors[tenors.length - 1].value;
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
  const shortOptions =
    TENOR_OPTIONS_BY_CURVE[curveFamily] ?? TENOR_OPTIONS_BY_CURVE.USD_SOFR_OIS;
  const longOptions = longTenorOptions(curveFamily, shortTenor);
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family',
      label: 'OIS Curve',
      kind: 'enum',
      value: curveFamily,
      options: OIS_CURVE_OPTIONS,
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

  // ----- Top-right cards: Z-score / Percentile / Overnight Index -----
  const cm = data?.current_metrics;
  const zCaption = zScoreCaptionForSpread(cm?.current_z_score);
  const percentile = data ? trailingPercentile252d(data) : null;
  const pBucket = bucketForPercentile(percentile);
  const familyMeta = oisFamilyFor(curveFamily);
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
            {percentileLabel(percentile)}
          </div>
          <span className="text-[11.5px] text-fg-secondary">{pBucket}</span>
        </div>
      ),
    },
    {
      key: 'overnight_index',
      node: (
        <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
          <span className="kicker text-fg-muted">OVERNIGHT INDEX</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {familyMeta
              ? `${familyMeta.marketShort} · ${familyMeta.indexShort}`
              : curveFamily}
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            {OIS_CURVE_SPREAD_COMPACT_CAVEAT}
          </span>
        </div>
      ),
    },
  ];

  const pairLabel = spreadShortLabel(shortTenor, longTenor);
  const identityPrimary = familyMeta
    ? `${familyMeta.indexShort} ${pairLabel} OIS SPREAD`
    : `${curveFamily} ${pairLabel} OIS SPREAD`;
  const identitySubtitle = familyMeta
    ? `${familyMeta.marketShort} ${familyMeta.indexShort} Overnight Index Swap Curve (${pairLabel} Curve Spread)`
    : `${curveFamily} ${shortTenor} → ${longTenor} OIS Spread`;

  // Combine the headline KPI strip and the decomposition row.  The shell
  // renders them as one continuous strip; the decomposition labels carry
  // their own tenor markers so the grouping reads naturally.
  const allKpis = data
    ? [
        ...extendedKPIs(data),
        ...decompositionKPIs(data, shortTenor, longTenor),
      ]
    : [
        { label: 'SPREAD', value: '—', unit: 'bp', tone: 'neutral' as const },
        { label: '1D CHANGE', value: '—', unit: 'bp', tone: 'neutral' as const },
        { label: 'Z-SCORE (252D)', value: '—', tone: 'neutral' as const },
      ];

  return (
    <BuildExtendedShell
      category={{
        name: 'OIS CURVE SPREAD',
        tags: ['SNAPSHOT', 'DETERMINISTIC', 'CURVE SHAPE'],
      }}
      identity={{
        primary: identityPrimary,
        secondary: familyMeta ? `· ${familyMeta.marketShort}` : undefined,
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
      chartPoints={sanitiseSpreadSeries(
        data?.time_series_spread?.rows ?? [],
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
              Number(lookbackDays),
              shortTenor,
              longTenor,
            )
          : []
      }
      methodologyReferences={buildReferenceChips()}
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic snapshot (long − short on single OIS curve, raw rate space, in bps)',
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
