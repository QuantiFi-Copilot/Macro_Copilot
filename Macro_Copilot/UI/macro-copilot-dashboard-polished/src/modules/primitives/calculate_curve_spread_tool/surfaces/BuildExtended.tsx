// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_curve_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every primitive ships an extended view;
// this is the same-curve sovereign curve-spread's full canvas.  Mounted by
// VirtualPrimitiveCanvas for single-tool queries OR by the click-to-expand
// modal from a compact card.  Design reference: ./mockups/Extended.png.
//
// Mockup-faithful design choices:
//   - A SINGLE "Curve" dropdown (single-curve primitive — distinct from
//     calculate_cross_market_spread_tool, which crosses two sovereigns).
//   - SHORT TENOR + LONG TENOR as TWO separate dropdowns; the long-tenor
//     options are filtered to strictly-longer tenors so the "long > short"
//     validity rule is enforced at the input layer (the backend re-validates
//     ``short_tenor != long_tenor`` regardless via Pydantic).
//   - bps-scale KPI strip — the backend ships the spread + 1d change in BPS
//     (no unit conversion).  5d / 1m changes, percentile / 252d high / 252d
//     low / observation count are computed CLIENT-SIDE from the
//     ``time_series`` rows because the sovereign curve_spread Output is leaner
//     than the linker / cross_market siblings (no wire fields for these).
//   - A separate decomposition row exposing the two endpoint sovereign
//     yields (PERCENT) so the desk can audit ``long − short`` on the same
//     screen.
//   - The "sovereign-vs-OIS cross-check" caveat surfaces in the methodology
//     card.  TODO(PR10): when the backend ships
//     ``current_metrics.methodology_label`` the "Disclosure" row switches to
//     consume the wire — see ./curveSpreadShared.ts for the marker.
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
  SOVEREIGN_CURVE_OPTIONS,
  SOVEREIGN_TENOR_OPTIONS,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  decompositionKPIs,
  extendedKPIs,
  sanitiseSpreadSeries,
  shapeRegimeCaption,
  sovereignFamilyFor,
  spreadChartRows,
  spreadShortLabel,
  tenorToYears,
  trailingPercentile252d,
  useCurveSpread,
  zScoreCaptionForSpread,
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
  { value: 'PX_LAST', label: 'PX_LAST' },
];

const DEFAULTS = {
  lookback_days: '365',
  field_name: 'YLD_YTM_MID',
};

/** Long-tenor options strictly longer than the selected short tenor.  The
 *  backend re-validates ``short_tenor != long_tenor`` regardless. */
function longTenorOptions(shortTenor: string) {
  const shortYears = tenorToYears(shortTenor);
  if (Number.isNaN(shortYears)) return SOVEREIGN_TENOR_OPTIONS;
  return SOVEREIGN_TENOR_OPTIONS.filter(
    (o) => tenorToYears(o.value) > shortYears,
  );
}

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  const curveFamily = params.curve_family ?? 'UST';
  const shortTenor = params.short_tenor ?? '2Y';
  const longTenor = params.long_tenor ?? '10Y';
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useCurveSpread({
    curveFamily,
    shortTenor,
    longTenor,
    lookbackDays: Number(lookbackDays),
    fieldName,
    asOfDate: params.as_of_date,
  });

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
    if (name === 'short_tenor') {
      const opts = longTenorOptions(value);
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

  const longOptions = longTenorOptions(shortTenor);
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family',
      label: 'Curve',
      kind: 'enum',
      value: curveFamily,
      options: SOVEREIGN_CURVE_OPTIONS,
    },
    {
      name: 'short_tenor',
      label: 'Short Tenor',
      kind: 'enum',
      value: shortTenor,
      options: SOVEREIGN_TENOR_OPTIONS,
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

  const cm = data?.current_metrics;
  const zCaption = zScoreCaptionForSpread(cm?.current_z_score);
  const percentile = data ? trailingPercentile252d(data) : null;
  const pBucket = bucketForPercentile(percentile);
  const familyMeta = sovereignFamilyFor(curveFamily);
  const shapeCaption = shapeRegimeCaption(cm?.current_spread_bps);
  const regimeKicker =
    shapeCaption === '—' && zCaption === '—'
      ? 'REGIME'
      : `${shapeCaption} · ${zCaption}`.toUpperCase();
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
      key: 'regime',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">{regimeKicker}</span>
          <div className="text-[26px] font-medium leading-none text-fg-primary">
            {signedFixed(cm?.current_spread_bps ?? null, 1)}
            <span className="ml-1 text-[13px] text-fg-secondary">bp</span>
          </div>
          <span className="text-[11.5px] text-fg-secondary">
            Curve-shape regime
          </span>
        </div>
      ),
    },
  ];

  const pairLabel = spreadShortLabel(shortTenor, longTenor);
  const identityPrimary = familyMeta
    ? `${familyMeta.shortLabel} ${pairLabel}`
    : `${curveFamily} ${pairLabel}`;
  const identitySubtitle = familyMeta
    ? `${longTenor} ${familyMeta.shortLabel} yield minus ${shortTenor} ${familyMeta.shortLabel} yield · Sovereign yield curve slope`
    : `${curveFamily} ${shortTenor} → ${longTenor} curve spread`;

  // Combine the headline KPI strip and the decomposition row.  The shell
  // renders them as one continuous strip; the decomposition labels carry
  // their own tenor + sovereign markers so the grouping reads naturally.
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
        name: 'SOVEREIGN CURVE SPREAD',
        tags: ['SNAPSHOT', 'DETERMINISTIC', 'CURVE SHAPE'],
      }}
      identity={{
        primary: identityPrimary,
        secondary: familyMeta ? `· ${familyMeta.longLabel}` : undefined,
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
        data ? spreadChartRows(data) : [],
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
        kind: 'Deterministic snapshot (long − short on single sovereign curve, raw yield space, in bps)',
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
