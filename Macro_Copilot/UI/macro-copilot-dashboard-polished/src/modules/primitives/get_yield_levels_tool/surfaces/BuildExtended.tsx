// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for get_yield_levels_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every primitive ships an extended view;
// this is the sovereign yield-level full canvas.  Mounted by
// VirtualPrimitiveCanvas for single-tool queries OR by the click-to-expand
// modal from a compact card in a multi-tool DAG.  Design reference:
// ./mockups/Extended.png.
//
// Mockup-faithful design choices:
//   - A CURVE + TENOR + LOOKBACK + FIELD controls strip — the four
//     instrument-selection knobs the backend Pydantic Input exposes.
//   - No advanced z-score override controls (the sovereign yield_levels
//     Pydantic Input does NOT accept z_score_window_days / min_periods /
//     ddof — those are real_yield_level-only Phase-1 exposures).  The
//     z-score model is documented in the methodology card.
//   - KPI strip with the full 9-cell read off the wire (YIELD / 1D / 5D /
//     1M / Z-SCORE / PERCENTILE / 252D HIGH / 252D LOW / OBSERVATIONS).
//   - Z-Score and Percentile cards on the top-right; third card carries
//     a SHAPE / REGIME caption (curve family + tenor short summary).
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
  SOVEREIGN_CURVE_OPTIONS,
  SOVEREIGN_TENOR_OPTIONS,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  extendedKPIs,
  sanitiseTimeSeries,
  sovereignFamilyFor,
  useYieldLevel,
} from './yieldLevelShared';

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
  curve_family: 'UST',
  tenor: '10Y',
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

  const curveFamily = params.curve_family ?? DEFAULTS.curve_family;
  const tenor = params.tenor ?? DEFAULTS.tenor;
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useYieldLevel({
    curveFamily,
    tenor,
    lookbackDays: Number(lookbackDays),
    fieldName,
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
    pushParams({ ...params, [name]: value });
  };

  const handleReset = () => {
    pushParams({ ...DEFAULTS });
  };

  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family',
      label: 'Curve',
      kind: 'enum',
      value: curveFamily,
      options: SOVEREIGN_CURVE_OPTIONS,
    },
    {
      name: 'tenor',
      label: 'Tenor',
      kind: 'enum',
      value: tenor,
      options: SOVEREIGN_TENOR_OPTIONS,
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

  const cm = data?.current_metrics;
  const zRegime = regimeForZScore(cm?.z_score);
  const pBucket = cm?.percentile_252d != null
    ? cm.percentile_252d >= 80 ? 'High' : cm.percentile_252d <= 20 ? 'Low' : 'Normal'
    : 'Normal';
  const familyMeta = sovereignFamilyFor(curveFamily);
  const topRightCards: ReadonlyArray<TopRightCard> = [
    {
      key: 'zscore',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">Z-SCORE (252D)</span>
          <div
            className={`text-[26px] font-medium leading-none ${toneTextClass(toneForZScore(cm?.z_score))}`}
          >
            {signedFixed(cm?.z_score ?? null, 2)}
          </div>
          <span className={`text-[11.5px] ${toneTextClass(toneForZScore(cm?.z_score))}`}>
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
      key: 'market',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">MARKET</span>
          <div className="text-[26px] font-medium leading-none text-fg-primary">
            {familyMeta?.flag ?? ''} {familyMeta?.shortLabel ?? curveFamily}
          </div>
          <span className="text-[11.5px] text-fg-secondary">
            {familyMeta?.longLabel ?? 'Sovereign benchmark'}
          </span>
        </div>
      ),
    },
  ];

  const identityPrimary = familyMeta
    ? `${familyMeta.shortLabel} ${tenor} Yield`
    : `${curveFamily} ${tenor} Yield`;
  const identitySubtitle = familyMeta
    ? `${familyMeta.longLabel} · Generic benchmark yield (${fieldName})`
    : `${curveFamily} ${tenor} · Generic benchmark yield`;

  return (
    <BuildExtendedShell
      category={{
        name: 'SOVEREIGN YIELD LEVEL',
        tags: ['SNAPSHOT', 'DETERMINISTIC'],
      }}
      identity={{
        primary: identityPrimary,
        secondary: undefined,
        flag: familyMeta?.flag,
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
          : [
              { label: 'YIELD', value: '—', unit: '%', tone: 'neutral' as const },
              { label: '1D CHANGE', value: '—', unit: 'bp', tone: 'neutral' as const },
              { label: 'Z-SCORE (252D)', value: '—', tone: 'neutral' as const },
            ]
      }
      chartPoints={sanitiseTimeSeries(data?.time_series?.rows ?? []).map((r) => ({
        date: r.date,
        value: r.value ?? NaN,
      }))}
      chartUnit="%"
      chartValueDecimals={3}
      referenceBands={data ? buildReferenceBands(data) : []}
      stretchContext={data ? buildStretchContext(data) : undefined}
      methodology={
        data
          ? buildMethodologyRows(data, fieldName, Number(lookbackDays))
          : []
      }
      methodologyReferences={buildReferenceChips()}
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic snapshot (single yield point, in percent)',
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
