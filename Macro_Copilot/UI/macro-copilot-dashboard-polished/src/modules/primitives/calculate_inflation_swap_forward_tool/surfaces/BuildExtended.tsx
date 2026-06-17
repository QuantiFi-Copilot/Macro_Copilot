// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_inflation_swap_forward_tool.
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §2.1 + §5 every new
// primitive ships an extended view; this is ZCIS forward-rate's full canvas.
// Mounted by VirtualPrimitiveCanvas when this tool is opened as the sole
// focus of a single-tool query OR by the click-to-expand modal
// infrastructure when invoked from a compact card in a multi-tool DAG.
//
// Design reference: ./mockups/Extended.png (committed alongside this module).
//
// Mirrors the calculate_ois_forward_rate_tool sibling's structure file-for-
// file: same shell composition, same descriptor builder pattern, same
// layout slots.  Adapted for the ZCIS forward-rate domain:
//
//   - forward (start, end) tenor-pair selection on the same ZCIS curve
//   - PX_MID default field (mirrors the inflation_swaps family default)
//   - NO z-score override controls (YAML-locked on this primitive)
//   - per-curve inflation-index methodology disclosure (CPI-U / HICPxT /
//     RPI with distinct lag + interpolation)
//   - wire-sourced ``methodology_label`` (PR10 wire-honesty pattern) on
//     the methodology card — NOT a hardcoded TS literal
//   - KPI strip uses 5D / 1M change cells directly (the ZCIS schema
//     exposes change_1w_bps + change_1m_bps; OIS sibling substitutes
//     START / END SPOT here because OIS lacks those fields)
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
  ZCIS_CURVE_OPTIONS,
  ZCIS_FORWARD_PAIR_OPTIONS,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  extendedKPIs,
  forwardPairFor,
  forwardShortLabel,
  sanitiseForwardSeries,
  useInflationSwapForward,
  zcisFamilyFor,
} from './inflationSwapForwardShared';

const LOOKBACK_OPTIONS = [
  { value: '90', label: '90d' },
  { value: '180', label: '180d' },
  { value: '365', label: '1Y' },
  { value: '1825', label: '5Y' },
  { value: '3650', label: '10Y' },
];

const FIELD_OPTIONS = [
  { value: 'PX_MID', label: 'PX_MID' },
  { value: 'PX_LAST', label: 'PX_LAST' },
  { value: 'PX_BID', label: 'PX_BID' },
  { value: 'PX_ASK', label: 'PX_ASK' },
];

const DEFAULTS = {
  forward_pair: '5Y5Y',
  lookback_days: '365',
  field_name: 'PX_MID',
};

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();

  // Request focused mode: collapse the workspaces sidebar + copilot rail
  // so this extended canvas gets the full viewport width.
  useRequestFocusedMode(true);

  // ----- Resolve effective params (fold defaults for missing ones) -----
  const curveFamily = params.curve_family ?? 'EUR_ZCIS';
  const forwardPairLabel =
    (params.forward_pair as string | undefined) ?? DEFAULTS.forward_pair;
  const pair =
    forwardPairFor(forwardPairLabel) ?? forwardPairFor(DEFAULTS.forward_pair)!;
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useInflationSwapForward({
    curveFamily,
    startTenor: pair.startTenor,
    endTenor: pair.endTenor,
    lookbackDays: Number(lookbackDays),
    fieldName,
    asOfDate: params.as_of_date,
  });

  // ----- Param update on control change -----
  // Stage D — when mounted inside the multi-tool DAG expand-to-modal,
  // edits stay local (onParamsChange) instead of navigating the global URL.
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
    const nextParams: Record<string, string> = { ...params, [name]: value };
    // When forward_pair changes, fold the (start_tenor, end_tenor) pair
    // into the backend-recognised params too so the URL state captures
    // the exact tenor-pair input the typed-detail endpoint expects.
    if (name === 'forward_pair') {
      const nextPair = forwardPairFor(value);
      if (nextPair) {
        nextParams.start_tenor = nextPair.startTenor;
        nextParams.end_tenor = nextPair.endTenor;
      }
    }
    pushParams(nextParams);
  };

  const handleReset = () => {
    const resetPair = forwardPairFor(DEFAULTS.forward_pair)!;
    pushParams({
      curve_family: curveFamily,
      forward_pair: DEFAULTS.forward_pair,
      start_tenor: resetPair.startTenor,
      end_tenor: resetPair.endTenor,
      lookback_days: DEFAULTS.lookback_days,
      field_name: DEFAULTS.field_name,
    });
  };

  // ----- Controls list -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family',
      label: 'ZCIS Curve',
      kind: 'enum',
      value: curveFamily,
      options: ZCIS_CURVE_OPTIONS,
    },
    {
      name: 'forward_pair',
      label: 'Forward',
      kind: 'enum',
      value: forwardPairLabel,
      options: ZCIS_FORWARD_PAIR_OPTIONS,
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

  // ----- Top-right cards: Z-score / Percentile / Central-bank context -----
  const cm = data?.current_metrics;
  const meta = zcisFamilyFor(curveFamily);
  const zRegime = regimeForZScore(cm?.z_score_252d);
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
      key: 'inflation-anchor',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">INFLATION ANCHOR</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {meta ? `${meta.flag} ${meta.centralBank}` : '—'}
          </div>
          <span className="text-[11.5px] text-fg-secondary">
            {cm
              ? `${cm.inflation_index_family} · lag ${cm.index_lag} · ${cm.interpolation}`
              : meta
                ? `${meta.indexShort} · lag ${meta.defaultIndexLag} · ${meta.defaultInterpolation}`
                : 'Inflation index reference'}
          </span>
        </div>
      ),
    },
  ];

  // ----- Identity row -----
  const shortLabel = cm
    ? forwardShortLabel(cm)
    : forwardPairLabel;
  const identityPrimary = meta
    ? `${meta.marketShort} ${shortLabel} ZCIS Forward · ${meta.indexShort}`
    : `${curveFamily} ${shortLabel} ZCIS Forward`;
  const identitySubtitle = `Zero-Coupon Inflation Swap Forward (${pair.startTenor} start, ${pair.endTenor} horizon)`;

  return (
    <BuildExtendedShell
      category={{
        name: 'INFLATION SWAPS · FORWARD ZCIS',
        tags: ['SNAPSHOT', 'DUAL COMPOUNDING'],
      }}
      identity={{
        primary: identityPrimary,
        secondary: meta ? curveFamily : forwardPairLabel,
        flag: meta?.flag,
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
                curve_family: curveFamily,
                start_tenor: pair.startTenor,
                end_tenor: pair.endTenor,
                forward_window_label: `${curveFamily} ${forwardPairLabel}`,
                forward_zcis_pct: null as unknown as number,
                forward_zcis_bps: null as unknown as number,
                change_1d_bps: null,
                change_1w_bps: null,
                change_1m_bps: null,
                z_score_252d: null,
                high_252d_bps: null,
                low_252d_bps: null,
                percentile_252d: null,
                start_zcis_pct: null,
                end_zcis_pct: null,
                start_years: 0,
                end_years: 0,
                observation_count: 0,
                inflation_index_family:
                  meta?.indexShort ?? '—',
                index_lag: meta?.defaultIndexLag ?? '—',
                interpolation: meta?.defaultInterpolation ?? '—',
                underlying_index: null,
                methodology_label: '',
              },
              time_series: [],
            })
      }
      chartPoints={sanitiseForwardSeries(data?.time_series ?? []).map((r) => ({
        date: r.date,
        value: r.value ?? NaN,
      }))}
      chartUnit="%"
      chartValueDecimals={3}
      referenceBands={data ? buildReferenceBands(data) : []}
      stretchContext={data ? buildStretchContext(data) : undefined}
      methodology={data ? buildMethodologyRows(data, fieldName) : []}
      methodologyReferences={buildReferenceChips()}
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic snapshot',
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
