// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_zscore_custom_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every primitive claiming
// ``custom_build_surface`` ships an extended view; this is the custom-
// window z-score's full canvas.  Mounted by VirtualPrimitiveCanvas for
// single-tool queries OR by the click-to-expand modal from a compact card.
//
// Design choices:
//   - ``z_score_window_days`` is the PRIMARY control (NOT advanced) —
//     it is this tool's central methodological knob per config.yaml A13.
//     min_periods / ddof are YAML-locked on the backend and intentionally
//     have NO controls here; their effective values are echoed on the
//     wire and surfaced in the KPI strip + methodology card.
//   - The main chart plots the z-score series itself (Z_SCORE units), so
//     the ±1.5σ / ±2σ reference bands are FIXED constants in y-units —
//     mirrors how every other z-emitting sibling overlays its envelope.
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  BuildExtendedShell,
  asOfDateControl,
  regimeForZScore,
  signedFixed,
  toneForZScore,
  toneTextClass,
  unsignedFixed,
  type BuildExtendedProps,
  type ControlDescriptor,
  type TopRightCard,
} from '@/components/shared/build';
import { CURVE_OPTIONS } from '@/lib/monitorParamOptions';
import {
  ZSCORE_CAVEAT_FALLBACK,
  ZSCORE_DEFAULTS,
  ZSCORE_FIELD_OPTIONS,
  ZSCORE_LOOKBACK_OPTIONS,
  ZSCORE_REFERENCE_BANDS,
  ZSCORE_TENOR_OPTIONS,
  ZSCORE_WINDOW_OPTIONS,
  buildMethodologyRows,
  buildStretchContext,
  extendedKPIs,
  familyForCurve,
  useZscoreCustomData,
  zscoreSeriesPoints,
} from './zscoreCustomShared';

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params -----
  const curveFamily = params.curve_family ?? ZSCORE_DEFAULTS.curve_family;
  const tenor = params.tenor ?? ZSCORE_DEFAULTS.tenor;
  const zWindow = params.z_score_window_days || ZSCORE_DEFAULTS.z_score_window_days;
  const lookbackDays = params.lookback_days ?? ZSCORE_DEFAULTS.lookback_days;
  const fieldName = params.field_name || ZSCORE_DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useZscoreCustomData({
    curveFamily,
    tenor,
    zScoreWindowDays: Number(zWindow),
    lookbackDays: Number(lookbackDays),
    fieldName,
    asOfDate: params.as_of_date,
  });

  // ----- URL update on control change -----
  const pushParams = (nextParams: Record<string, string>) => {
    // Stage D — when mounted inside the multi-tool DAG expand-to-modal,
    // edits stay local (onParamsChange) instead of navigating the global
    // URL, which would replace the multi-tool context behind the modal.
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
    pushParams({
      curve_family: curveFamily,
      tenor,
      ...{
        z_score_window_days: ZSCORE_DEFAULTS.z_score_window_days,
        lookback_days: ZSCORE_DEFAULTS.lookback_days,
        field_name: ZSCORE_DEFAULTS.field_name,
      },
    });
  };

  // ----- Controls -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family',
      label: 'Curve Family',
      kind: 'enum',
      value: curveFamily,
      options: CURVE_OPTIONS,
    },
    {
      name: 'tenor',
      label: 'Tenor',
      kind: 'enum',
      value: tenor,
      options: ZSCORE_TENOR_OPTIONS,
    },
    {
      // The central knob — primary placement, never advanced-tier.
      name: 'z_score_window_days',
      label: 'Z-Score Window',
      kind: 'enum',
      value: zWindow,
      options: ZSCORE_WINDOW_OPTIONS,
    },
    {
      name: 'lookback_days',
      label: 'Lookback (display)',
      kind: 'enum',
      value: lookbackDays,
      options: ZSCORE_LOOKBACK_OPTIONS,
    },
    {
      name: 'field_name',
      label: 'Field',
      kind: 'enum',
      value: fieldName,
      options: ZSCORE_FIELD_OPTIONS,
    },
    asOfDateControl(params.as_of_date),
  ];

  // ----- Top-right cards: Z-score / Window / Instrument -----
  const cm = data?.current_metrics;
  const zRegime = regimeForZScore(cm?.current_z_score);
  const meta = familyForCurve(curveFamily);
  const topRightCards: ReadonlyArray<TopRightCard> = [
    {
      key: 'zscore',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">
            Z-SCORE ({cm?.z_score_window_days_used ?? zWindow}D)
          </span>
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
      key: 'window',
      node: (
        <div className="card flex h-full flex-col gap-2 px-4 py-3">
          <span className="kicker text-fg-muted">ROLLING WINDOW</span>
          <div className="text-[26px] font-medium leading-none text-fg-primary">
            {cm?.z_score_window_days_used ?? zWindow}
            <span className="ml-1 text-[13px] text-fg-secondary">d</span>
          </div>
          <span className="text-[11.5px] text-fg-secondary">
            min {cm?.z_score_min_periods_used ?? '—'} · ddof {cm?.z_score_ddof_used ?? '—'}
          </span>
        </div>
      ),
    },
    {
      key: 'instrument',
      node: (
        <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
          <span className="kicker text-fg-muted">INSTRUMENT</span>
          <div className="text-[15px] font-medium leading-tight text-fg-primary">
            {meta ? `${meta.flag} ${meta.country} · ${meta.curveShort} ${tenor}` : `${curveFamily} ${tenor}`}
          </div>
          <span className="text-[11px] leading-snug text-fg-muted">
            {ZSCORE_CAVEAT_FALLBACK}
          </span>
        </div>
      ),
    },
  ];

  const identitySubtitle = meta
    ? `${meta.country} sovereign yield · ${zWindow}d rolling z-score (window set per request)`
    : `Sovereign yield · ${zWindow}d rolling z-score (window set per request)`;

  return (
    <BuildExtendedShell
      category={{
        name: 'CUSTOM-WINDOW Z-SCORE',
        tags: ['ROLLING', 'DETERMINISTIC', 'STRETCH'],
      }}
      identity={{
        primary: meta ? `${meta.curveShort} ${tenor}` : `${curveFamily} ${tenor}`,
        secondary: 'Z-Score',
        flag: meta?.flag,
        subtitle: identitySubtitle,
        asOfDate: cm?.as_of_date,
        meta: `${lookbackDays}d display window · ${fieldName} · yield ${unsignedFixed(cm?.current_yield_pct, 2)}%`,
      }}
      topRightCards={topRightCards}
      controls={controls}
      onControlChange={handleControlChange}
      onResetControls={handleReset}
      kpis={
        data
          ? extendedKPIs(data)
          : [
              { label: 'CURRENT YIELD', value: '—' },
              { label: `Z-SCORE (${zWindow}D)`, value: '—' },
              { label: 'WINDOW USED', value: '—' },
              { label: 'MIN PERIODS', value: '—' },
              { label: 'DDOF', value: '—' },
              { label: 'OBSERVATIONS', value: '—' },
            ]
      }
      chartPoints={zscoreSeriesPoints(data)}
      chartUnit="σ"
      chartValueDecimals={2}
      referenceBands={data ? ZSCORE_REFERENCE_BANDS : []}
      stretchContext={data ? buildStretchContext(data) : undefined}
      methodology={
        data
          ? buildMethodologyRows(data, fieldName, Number(lookbackDays))
          : []
      }
      lineage={{
        toolName,
        version: 'v1',
        kind: 'Deterministic rolling statistic (custom window)',
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
