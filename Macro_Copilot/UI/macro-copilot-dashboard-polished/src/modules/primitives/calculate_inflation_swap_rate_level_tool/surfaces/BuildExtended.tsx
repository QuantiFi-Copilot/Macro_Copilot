// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_inflation_swap_rate_level_tool.
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §2.1 + §5 every new
// primitive ships an extended view; this is ZCIS rate_level's full canvas.
// Mounted by VirtualPrimitiveCanvas when this tool is opened as the sole
// focus of a single-tool query OR by the click-to-expand modal
// infrastructure when invoked from a compact card in a multi-tool DAG.
//
// Design reference: ./mockups/Extended.png (committed alongside this module).
//
// Mirrors the sibling get_ois_rate_level_tool's structure file-for-file:
// same shell composition, same descriptor builder pattern, same layout
// slots.  Adapted for the ZCIS rate-level domain:
//
//   - zcis_rate_pct (not current_yield_pct or current_rate_pct) naming
//   - ZCIS curve_family registry (USD_ZCIS / EUR_ZCIS / GBP_ZCIS) with the
//     load-bearing index-family / lag / interpolation triple surfaced
//   - PX_MID default field (not YLD_YTM_MID or PX_LAST)
//   - NO z-score override controls (YAML-locked on this primitive; mirrors
//     the OIS rate_level / sibling level tools)
//   - methodology disclosure flows from the wire's ``methodology_label``
//     (P5 threading; NEVER a hardcoded TS literal)
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
  ZCIS_TENOR_OPTIONS_BY_CURVE,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  extendedKPIs,
  sanitiseTimeSeries,
  useInflationSwapRateLevel,
  zcisFamilyFor,
} from './inflationSwapRateLevelShared';

const LOOKBACK_OPTIONS = [
  { value: '90', label: '90d' },
  { value: '180', label: '180d' },
  { value: '365', label: '365d' },
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

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();

  // Request focused mode: collapse the workspaces sidebar + copilot rail so
  // this extended canvas gets the full viewport width.
  useRequestFocusedMode(true);

  // ----- Resolve effective params (fold defaults for missing ones) -----
  const curveFamily = params.curve_family ?? '';
  const tenor = params.tenor ?? '';
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useInflationSwapRateLevel({
    curveFamily,
    tenor,
    lookbackDays: Number(lookbackDays),
    fieldName,
    asOfDate: params.as_of_date,
  });

  // ----- Param update on control change -----
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
    // When the curve_family changes, the tenor may become invalid for the
    // new family.  Reset to the new family's first available tenor.
    if (name === 'curve_family') {
      const tenors = ZCIS_TENOR_OPTIONS_BY_CURVE[value] ?? [];
      if (tenors.length > 0) nextParams.tenor = tenors[0].value;
    }
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({
      curve_family: curveFamily,
      tenor,
      ...DEFAULTS,
    });
  };

  // ----- Controls list -----
  const tenorOptions =
    ZCIS_TENOR_OPTIONS_BY_CURVE[curveFamily] ?? ZCIS_TENOR_OPTIONS_BY_CURVE.USD_ZCIS;
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family',
      label: 'ZCIS Curve',
      kind: 'enum',
      value: curveFamily || 'EUR_ZCIS',
      options: ZCIS_CURVE_OPTIONS,
    },
    {
      name: 'tenor',
      label: 'Tenor',
      kind: 'enum',
      value: tenor || tenorOptions[0]?.value || '5Y',
      options: tenorOptions,
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
  const meta = zcisFamilyFor(curveFamily);
  const zRegime = regimeForZScore(cm?.z_score);
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
      key: 'index-family',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">INDEX FAMILY · LAG</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {meta ? `${meta.flag} ${meta.indexShort}` : '—'}
          </div>
          <span className="text-[11.5px] text-fg-secondary">
            {cm
              ? `${cm.inflation_index_family} · ${cm.index_lag} lag · ${cm.interpolation}`
              : meta
                ? `${meta.inflationIndexFamily} · ${meta.indexLag} lag · ${meta.interpolation}`
                : 'ZCIS reference index'}
          </span>
        </div>
      ),
    },
  ];

  // ----- Identity row -----
  const identityPrimary = meta
    ? `${meta.marketShort} ZCIS ${tenor || ''} · ${meta.indexShort}`.trim()
    : curveFamily || '—';
  const identitySubtitle = meta?.subtitle ?? 'Zero-Coupon Inflation Swap rate';

  return (
    <BuildExtendedShell
      category={{
        name: 'ZCIS RATE LEVEL',
        tags: ['SNAPSHOT', 'DETERMINISTIC'],
      }}
      identity={{
        primary: identityPrimary,
        secondary: meta ? curveFamily : tenor,
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
                tenor,
                zcis_rate_pct: NaN,
                daily_change_bps: null,
                weekly_change_bps: null,
                monthly_change_bps: null,
                z_score: null,
                high_252d_pct: null,
                low_252d_pct: null,
                percentile_252d: null,
                observation_count: 0,
                inflation_index_family: '',
                index_lag: '',
                interpolation: '',
                underlying_index: null,
                methodology_label: '',
              },
            })
      }
      chartPoints={sanitiseTimeSeries(data?.time_series?.rows ?? []).map((r) => ({
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
