// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for get_real_yield_level_tool.
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §2.1 + §5 every new
// primitive ships an extended view; this is real_yield_level's full
// canvas.  Mounted by VirtualPrimitiveCanvas when this tool is opened
// as the sole focus of a single-tool query (Library "Open in Build",
// single-tool Ask handoff, direct ?context= deep-link) OR by the click-
// to-expand modal infrastructure when invoked from a compact card in a
// multi-tool DAG.
//
// Design reference: ./mockups/Extended.png (committed alongside this
// module).
//
// All layout / chart / methodology / lineage chrome lives in the
// shared shell at @/components/shared/build — finance-blind.  This
// wrapper does:
//   1. Read URL params (curve_family / tenor / lookback / field +
//      Phase-1 exposed methodology overrides)
//   2. Fetch via the typed-detail endpoint (useRealYieldLevel)
//   3. Map data → KPIDescriptor[] / ChartPoint[] / MethodologyRow[] /
//      StretchContext / ReferenceBand[]
//   4. Compose <BuildExtendedShell />
//
// Editing controls re-encodes ?context= and re-fetches via the same
// VirtualPrimitiveCanvas URL-update flow used by every other tool.
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  BuildExtendedShell,
  CountryCaveatBadge,
  countryCaveatFor,
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
  buildMethodologyRows,
  buildReferenceBands,
  buildReferenceChips,
  buildStretchContext,
  countOutliersRejected,
  extendedKPIs,
  sanitiseTimeSeries,
  useRealYieldLevel,
} from './realYieldShared';

// ---------------------------------------------------------------------------
// Curve-family + tenor option vocabularies (finance-aware; lives in
// this per-tool wrapper, NOT in the shared shell).
// ---------------------------------------------------------------------------

const CURVE_OPTIONS = [
  { value: 'USD_TIPS', label: 'USD_TIPS' },
  { value: 'GBP_LINKER', label: 'GBP_LINKER' },
  { value: 'EUR_FR_LINKER', label: 'EUR_FR_LINKER' },
  { value: 'CAD_RRB', label: 'CAD_RRB' },
];

// Tenor sets vary per curve_family per the institutional doc + manifest.
const TENOR_OPTIONS_BY_CURVE: Record<string, ReadonlyArray<{ value: string; label: string }>> = {
  USD_TIPS: ['5Y', '10Y', '20Y', '30Y'].map((t) => ({ value: t, label: t })),
  GBP_LINKER: ['1Y', '2Y', '3Y', '5Y', '10Y', '15Y', '20Y', '30Y', '50Y'].map((t) => ({
    value: t,
    label: t,
  })),
  EUR_FR_LINKER: ['2Y', '5Y', '7Y', '10Y', '15Y'].map((t) => ({ value: t, label: t })),
  CAD_RRB: ['5Y', '10Y', '15Y', '20Y', '25Y', '30Y'].map((t) => ({ value: t, label: t })),
};

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
  z_score_window_days: '252',
  z_score_min_periods: '60',
  z_score_ddof: '1',
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  decoded,
  askHandoff,
  onParamsChange,
}) => {
  const navigate = useNavigate();

  // Request focused mode: collapse the workspaces sidebar + copilot
  // rail so this extended canvas gets the full viewport width.
  // Per docs_revamped/03_standards/rendering_density.md the extended
  // view's spacious layout assumes full width.  User can re-open
  // either panel via the edge toggle buttons the shell exposes.
  useRequestFocusedMode(true);

  // ----- Resolve effective params (fold defaults for missing ones) -----
  const curveFamily = params.curve_family ?? '';
  const tenor = params.tenor ?? '';
  const lookbackDays = params.lookback_days ?? DEFAULTS.lookback_days;
  const fieldName = params.field_name || DEFAULTS.field_name;
  const zWindow = params.z_score_window_days || DEFAULTS.z_score_window_days;
  const zMinPeriods = params.z_score_min_periods || DEFAULTS.z_score_min_periods;
  const zDdof = params.z_score_ddof ?? DEFAULTS.z_score_ddof;

  const { data, isLoading, errorMessage } = useRealYieldLevel({
    curveFamily,
    tenor,
    lookbackDays: Number(lookbackDays),
    fieldName,
    zScoreWindowDays: Number(zWindow),
    zScoreMinPeriods: Number(zMinPeriods),
    zScoreDdof: Number(zDdof),
  });

  // ----- Param update on control change -----
  // Stage D — when mounted inside the multi-tool DAG expand-to-modal,
  // edits stay local (onParamsChange) instead of navigating the global
  // URL, which would replace the multi-tool context behind the modal.
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
    // When the curve_family changes, the tenor may become invalid for
    // the new family.  Reset the tenor to the new family's first
    // available tenor for safety.
    if (name === 'curve_family') {
      const tenors = TENOR_OPTIONS_BY_CURVE[value] ?? [];
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
  const tenorOptions = TENOR_OPTIONS_BY_CURVE[curveFamily] ?? TENOR_OPTIONS_BY_CURVE.USD_TIPS;
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family',
      label: 'Curve Family',
      kind: 'enum',
      value: curveFamily || 'USD_TIPS',
      options: CURVE_OPTIONS,
    },
    {
      name: 'tenor',
      label: 'Tenor',
      kind: 'enum',
      value: tenor || tenorOptions[0]?.value || '10Y',
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
    // Phase-1 exposed methodology overrides — hidden behind "Advanced".
    {
      name: 'z_score_window_days',
      label: 'z_score_window_days',
      kind: 'number',
      value: zWindow,
      min: 60,
      max: 1260,
      advanced: true,
    },
    {
      name: 'z_score_min_periods',
      label: 'z_score_min_periods',
      kind: 'number',
      value: zMinPeriods,
      min: 20,
      max: 252,
      advanced: true,
    },
    {
      name: 'z_score_ddof',
      label: 'z_score_ddof',
      kind: 'enum',
      value: zDdof,
      options: [
        { value: '1', label: '1 (sample)' },
        { value: '0', label: '0 (population)' },
      ],
      advanced: true,
    },
  ];

  // ----- Top-right cards: Z-score / Percentile / Country caveat -----
  const cm = data?.current_metrics;
  const zRegime = regimeForZScore(cm?.z_score);
  const pBucket = cm?.percentile_252d != null
    ? cm.percentile_252d >= 80 ? 'High' : cm.percentile_252d <= 20 ? 'Low' : 'Normal'
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
      key: 'country-caveat',
      node: <CountryCaveatBadge curveFamily={curveFamily} variant="card" />,
    },
  ];

  // ----- Identity row -----
  const caveat = countryCaveatFor(curveFamily);
  const identitySubtitle =
    curveFamily === 'USD_TIPS'
      ? 'US Treasury Inflation-Protected Securities · Generic benchmark real yield'
      : curveFamily === 'GBP_LINKER'
        ? 'UK Index-Linked Gilts · Generic benchmark real yield'
        : curveFamily === 'EUR_FR_LINKER'
          ? 'French OATei · Generic benchmark real yield'
          : curveFamily === 'CAD_RRB'
            ? 'Canadian Real Return Bonds · Generic benchmark real yield'
            : 'Sovereign linker · Generic benchmark real yield';

  return (
    <BuildExtendedShell
      category={{
        name: 'REAL YIELD LEVEL',
        tags: ['SNAPSHOT', 'DETERMINISTIC'],
      }}
      identity={{
        primary: curveFamily || '—',
        secondary: tenor,
        flag: caveat?.flag,
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
                real_yield_pct: NaN,
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
      chartValueDecimals={3}
      referenceBands={data ? buildReferenceBands(data) : []}
      stretchContext={data ? buildStretchContext(data) : undefined}
      methodology={
        data
          ? buildMethodologyRows(
              data,
              fieldName,
              Number(zWindow),
              Number(zMinPeriods),
              Number(zDdof),
            )
          : []
      }
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
