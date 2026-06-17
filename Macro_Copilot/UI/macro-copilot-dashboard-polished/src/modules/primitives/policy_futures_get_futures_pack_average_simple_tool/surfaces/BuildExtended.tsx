// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// policy_futures_get_futures_pack_average_simple_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every new primitive ships an extended
// view; this is the same-curve pack-average's full canvas.  Mounted by
// VirtualPrimitiveCanvas for single-tool queries OR by the click-to-expand
// modal from a compact card in a multi-tool DAG.
//
// Design reference: ./mockups/Extended.png.  Mockup-faithful design
// choices:
//   - Controls strip carries Curve / Pack / Lookback / Field; pack is a
//     closed Literal (whites/reds) per the YAML-locked desk convention.
//   - PERCENT-scale headline KPI strip — the backend ships the pack
//     average in PERCENT; the display layer keeps the level in PERCENT
//     (3-dp) and multiplies the 1d change by 100 to render in bps.
//   - Per-leg implied-rate decomposition row exposing the four master
//     stems + current-front underlying contracts + per-leg implied rates.
//   - Top-right cards: Z-score / Percentile / Central-Bank-Context (with
//     RFR / IBOR regime label sourced from the wire).
//   - The full P5 / ADR 0013 disclosure (including the arithmetic-mean
//     weighting + the refusal of duration-weighted / meeting-by-meeting
//     pack variants) surfaces in the methodology card via the backend's
//     ``methodology_disclosure`` field (NOT a hardcoded TS literal).
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
  PACK_OPTIONS,
  POLICY_FUTURES_CURVE_OPTIONS,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  compactCaveatFor,
  curveMetaFor,
  decompositionKPIs,
  extendedKPIs,
  sanitisePackSeries,
  usePolicyFuturesPackAverage,
} from './futuresPackAverageSimpleShared';

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
  pack: 'whites',
  lookback_days: '365',
  field_name: 'PX_LAST',
};

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params -----
  const curveFamily = params.curve_family ?? 'SOFR_FUT';
  const pack = params.pack ?? DEFAULTS.pack;
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;
  const asOfDate = params.as_of_date || undefined;

  const { data, isLoading, errorMessage } = usePolicyFuturesPackAverage({
    curveFamily,
    pack,
    lookbackDays: Number(lookbackDays),
    asOfDate,
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
    const nextParams = { ...params, [name]: value };
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({
      curve_family: curveFamily,
      ...DEFAULTS,
    });
  };

  // ----- Controls -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family',
      label: 'Curve',
      kind: 'enum',
      value: curveFamily,
      options: POLICY_FUTURES_CURVE_OPTIONS,
    },
    {
      name: 'pack',
      label: 'Pack',
      kind: 'enum',
      value: pack,
      options: PACK_OPTIONS,
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

  // ----- Top-right cards: Z-score / Percentile / Central-Bank context -----
  const cm = data?.current_metrics;
  const meta = curveMetaFor(curveFamily);
  const zRegime = regimeForZScore(cm?.z_score_pack_average);
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
            className={`text-[26px] font-medium leading-none ${toneTextClass(toneForZScore(cm?.z_score_pack_average))}`}
          >
            {signedFixed(cm?.z_score_pack_average ?? null, 2)}
          </div>
          <span className={`text-[11.5px] ${toneTextClass(toneForZScore(cm?.z_score_pack_average))}`}>
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
      key: 'central-bank',
      node: (
        <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
          <span className="kicker text-fg-muted">CENTRAL BANK CONTEXT</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {meta ? `${meta.flag} ${meta.cbShort}` : '—'}
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            {meta
              ? `${meta.shortLabel} overnight reference · ${cm?.short_rate_regime ?? meta.regime} regime`
              : 'STIR overnight reference'}
          </span>
        </div>
      ),
    },
  ];

  // ----- Identity row -----
  const packDisplay = pack.toUpperCase();
  const identityPrimary = meta
    ? `${meta.shortLabel} ${packDisplay} PACK AVERAGE`
    : `${curveFamily} ${packDisplay} PACK AVERAGE`;
  const identitySecondary = '4-quarter mean (Implied Rate)';
  const identitySubtitle = meta
    ? `${meta.shortLabel} strip · ${packDisplay.toLowerCase()} = positions ${cm?.strip_positions?.join('-') ?? (pack === 'whites' ? '1-4' : '5-8')} (arithmetic mean)`
    : 'STIR pack-average implied rate (arithmetic mean of four consecutive quarterly contracts)';

  // Combine the headline KPI strip and the decomposition row.  The shell
  // renders them as one continuous strip; the decomposition labels carry
  // their own master-stem markers (SFR1 / SFR2 / SFR3 / SFR4) so the
  // grouping reads naturally.
  const allKpis = data
    ? [...extendedKPIs(data), ...decompositionKPIs(data)]
    : [
        {
          label: 'PACK IMPLIED RATE',
          value: '—',
          unit: '%',
          tone: 'neutral' as const,
        },
        {
          label: '1D CHANGE',
          value: '—',
          unit: 'bp',
          tone: 'neutral' as const,
        },
        {
          label: 'Z-SCORE (252D)',
          value: '—',
          tone: 'neutral' as const,
        },
      ];

  return (
    <BuildExtendedShell
      category={{
        name: 'POLICY FUTURES PACK AVERAGE',
        tags: ['SNAPSHOT', 'DETERMINISTIC'],
      }}
      identity={{
        primary: identityPrimary,
        secondary: identitySecondary,
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
      chartPoints={sanitisePackSeries(data?.time_series ?? []).map((r) => ({
        date: r.date,
        value: r.value ?? NaN,
      }))}
      chartUnit="%"
      chartValueDecimals={3}
      referenceBands={data ? buildReferenceBands(data) : []}
      stretchContext={data ? buildStretchContext(data) : undefined}
      methodology={
        data ? buildMethodologyRows(data, fieldName, Number(lookbackDays)) : []
      }
      methodologyReferences={buildReferenceChips()}
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic snapshot (arithmetic mean of four consecutive quarterly implied rates)',
        providers: [
          'TimescaleDB',
          'macro_data.v_market_data_daily_enriched',
          compactCaveatFor(curveFamily),
        ],
        asOf: cm?.as_of_date,
        freshness: 'fresh',
      }}
      isLoading={isLoading}
      errorMessage={errorMessage ?? undefined}
    />
  );
};

export default BuildExtended;
