// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for get_ois_rate_level_tool.
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §2.1 + §5 every new
// primitive ships an extended view; this is OIS rate_level's full canvas.
// Mounted by VirtualPrimitiveCanvas when this tool is opened as the sole
// focus of a single-tool query OR by the click-to-expand modal
// infrastructure when invoked from a compact card in a multi-tool DAG.
//
// Design reference: ./mockups/Extended.png (committed alongside this module).
//
// Mirrors the get_real_yield_level_tool reference module's structure
// file-for-file: same shell composition, same descriptor builder pattern,
// same layout slots.  Adapted for the OIS rate-level domain:
//
//   - rate (not yield) naming
//   - OIS curve_family registry (USD_SOFR_OIS / EUR_ESTR_OIS / GBP_SONIA_OIS
//     / JPY_OIS / AUD_OIS / CAD_OIS)
//   - PX_LAST default field (not YLD_YTM_MID)
//   - NO z-score override controls (YAML-locked on this primitive; mirrors
//     the OIS curve_spread / butterfly siblings)
//   - per-family methodology disclosure ("risk-neutral implied policy path")
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
  OIS_CURVE_OPTIONS,
  OIS_TENOR_OPTIONS_BY_CURVE,
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  extendedKPIs,
  oisFamilyFor,
  sanitiseTimeSeries,
  useOisRateLevel,
} from './oisRateLevelShared';

const LOOKBACK_OPTIONS = [
  { value: '90', label: '90d' },
  { value: '180', label: '180d' },
  { value: '365', label: '365d' },
  { value: '730', label: '2Y' },
  { value: '1825', label: '5Y' },
];

const FIELD_OPTIONS = [
  { value: 'PX_LAST', label: 'PX_LAST' },
  { value: 'PX_BID', label: 'PX_BID' },
  { value: 'PX_ASK', label: 'PX_ASK' },
  { value: 'PX_MID', label: 'PX_MID' },
];

const DEFAULTS = {
  lookback_days: '365',
  field_name: 'PX_LAST',
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

  const { data, isLoading, errorMessage } = useOisRateLevel({
    curveFamily,
    tenor,
    lookbackDays: Number(lookbackDays),
    fieldName,
  });

  // ----- Param update on control change -----
  // Stage D — when mounted inside the multi-tool DAG expand-to-modal, edits
  // stay local (onParamsChange) instead of navigating the global URL.
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
    // new family.  Reset the tenor to the new family's first available
    // tenor for safety.
    if (name === 'curve_family') {
      const tenors = OIS_TENOR_OPTIONS_BY_CURVE[value] ?? [];
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
    OIS_TENOR_OPTIONS_BY_CURVE[curveFamily] ?? OIS_TENOR_OPTIONS_BY_CURVE.USD_SOFR_OIS;
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family',
      label: 'OIS Curve',
      kind: 'enum',
      value: curveFamily || 'USD_SOFR_OIS',
      options: OIS_CURVE_OPTIONS,
    },
    {
      name: 'tenor',
      label: 'Tenor',
      kind: 'enum',
      value: tenor || tenorOptions[0]?.value || '2Y',
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
  ];

  // ----- Top-right cards: Z-score / Percentile / Central-bank caveat -----
  const cm = data?.current_metrics;
  const meta = oisFamilyFor(curveFamily);
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
      key: 'central-bank',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">CENTRAL BANK CONTEXT</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {meta ? `${meta.flag} ${meta.centralBank}` : '—'}
          </div>
          <span className="text-[11.5px] text-fg-secondary">
            {meta ? `${meta.indexShort} overnight reference` : 'OIS overnight reference'}
          </span>
        </div>
      ),
    },
  ];

  // ----- Identity row -----
  const identityPrimary = meta
    ? `${meta.indexShort} ${tenor || ''} RATE`.trim()
    : curveFamily || '—';
  const identitySubtitle = meta?.subtitle ?? 'Sovereign OIS · Par swap rate';

  return (
    <BuildExtendedShell
      category={{
        name: 'OIS RATE LEVEL',
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
                current_rate_pct: NaN,
                daily_change_bps: null,
                weekly_change_bps: null,
                monthly_change_bps: null,
                z_score: null,
                high_252d_pct: null,
                low_252d_pct: null,
                percentile_252d: null,
                observation_count: 0,
              },
            })
      }
      chartPoints={sanitiseTimeSeries(data?.time_series?.rows ?? []).map((r) => ({
        date: r.date,
        value: r.value ?? NaN,
      }))}
      chartUnit="%"
      chartValueDecimals={4}
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
