// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_beta_adjusted_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every primitive ships an extended
// view; this is the beta-adjusted spread's full canvas, composed from
// the rich-model grammar at @/components/shared/build/model (the
// dual-view replacement for the legacy BuilderCanvas /
// RollingRegressionRenderer route):
//
//   hero        — ModelKpiStrip (residual z / residual bps / β / R²)
//   primary     — ModelSeriesPanel (residual z-score — THE stretch read)
//   secondary   — ModelSeriesPanel (residual, bps)
//                 ModelSeriesPanel (rolling β, ratio)
//   diagnostics — ModelKpiStrip (window / min periods / z window /
//                 intercept / obs) + condition QualityBadge
//   methodology — MethodologyCard threaded from the response's *_used
//                 echo fields + spread_label (P5), incl. the schema's
//                 cheap/rich sign convention verbatim
//
// Controls strip above the layout exposes the GET-bridged Input surface
// (target + regressor legs, regression_window_days — the central knob —
// lookback_days, field_name).  Every other methodology knob is
// YAML-locked per A13 and therefore NOT a control here.
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  ControlsStrip,
  LineageFooter,
  MethodologyCard,
  asOfDateControl,
  type BuildExtendedProps,
  type ControlDescriptor,
} from '@/components/shared/build';
import {
  ModelKpiStrip,
  ModelResultLayout,
  ModelSeriesPanel,
  QualityBadge,
} from '@/components/shared/build/model';
import {
  BETA_ADJUSTED_SPREAD_DEFAULTS,
  buildMethodologyRows,
  conditionQuality,
  diagnosticsMetrics,
  flattenBetaAdjustedSpreadParams,
  heroMetrics,
  resolveBetaAdjustedSpreadParams,
  singleModelSeries,
  useBetaAdjustedSpreadData,
} from './betaAdjustedSpreadShared';

const SOVEREIGN_FAMILY_OPTIONS = [
  { value: 'UST', label: 'UST' },
  { value: 'DE_BUND', label: 'Bund' },
  { value: 'FR_OAT', label: 'OAT' },
  { value: 'IT_BTP', label: 'BTP' },
  { value: 'ES_BONO', label: 'Bono' },
  { value: 'UK_GILT', label: 'Gilt' },
  { value: 'JGB', label: 'JGB' },
];

const TENOR_OPTIONS = ['2Y', '5Y', '10Y', '30Y'].map((t) => ({
  value: t,
  label: t,
}));

const WINDOW_OPTIONS = [
  { value: '60', label: '60d (tactical)' },
  { value: '120', label: '120d' },
  { value: '252', label: '252d (annual)' },
  { value: '504', label: '504d (two-year)' },
];

const LOOKBACK_OPTIONS = [
  { value: '365', label: '1Y' },
  { value: '730', label: '2Y' },
  { value: '1825', label: '5Y' },
];

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  decoded,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  const resolved = resolveBetaAdjustedSpreadParams(
    params,
    decoded?.paramsStructured,
  );
  const { data, isLoading, errorMessage } = useBetaAdjustedSpreadData(resolved);

  // ----- URL update on control change (pilot pushParams pattern) -----
  const pushParams = (nextParams: Record<string, string>) => {
    // Stage D — inside the multi-tool DAG expand-to-modal, edits stay
    // local (onParamsChange) instead of navigating the global URL.
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
    pushParams({ ...flattenBetaAdjustedSpreadParams(resolved), [name]: value });
  };

  const handleReset = () => {
    pushParams({ ...BETA_ADJUSTED_SPREAD_DEFAULTS });
  };

  // ----- Controls -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'target_curve_family',
      label: 'Target (cheap/rich subject)',
      kind: 'enum',
      value: resolved.targetCurveFamily,
      options: SOVEREIGN_FAMILY_OPTIONS,
    },
    {
      name: 'target_tenor',
      label: 'Target Tenor',
      kind: 'enum',
      value: resolved.targetTenor,
      options: TENOR_OPTIONS,
    },
    {
      name: 'regressor_curve_family',
      label: 'Hedge (regressor)',
      kind: 'enum',
      value: resolved.regressorCurveFamily,
      options: SOVEREIGN_FAMILY_OPTIONS,
    },
    {
      name: 'regressor_tenor',
      label: 'Hedge Tenor',
      kind: 'enum',
      value: resolved.regressorTenor,
      options: TENOR_OPTIONS,
    },
    {
      name: 'regression_window_days',
      label: 'Fit Window',
      kind: 'enum',
      value: resolved.regressionWindowDays,
      options: WINDOW_OPTIONS,
    },
    {
      name: 'lookback_days',
      label: 'Lookback',
      kind: 'enum',
      value: resolved.lookbackDays,
      options: LOOKBACK_OPTIONS,
    },
    {
      name: 'field_name',
      label: 'field_name (both legs)',
      kind: 'text',
      value: resolved.fieldName,
      advanced: true,
    },
    asOfDateControl(params.as_of_date),
  ];

  const cm = data?.current_metrics ?? null;
  const condition = conditionQuality(data);

  return (
    // h-full + min-h-0 + overflow-y-auto = the rich-model scroll chassis;
    // without it a tall result is clipped inside BuildShell's fixed-height
    // overflow-hidden <main> (matches half_life / rolling_regression).
    <div className="flex h-full min-h-0 flex-col gap-4 overflow-y-auto">
      {/* ---------- Identity header ---------- */}
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className="kicker text-fg-muted">
            BETA-ADJUSTED SPREAD — ROLLING HEDGE FIT · DETERMINISTIC
          </div>
          <h2 className="mt-0.5 text-[19px] font-semibold text-fg-primary">
            {cm?.spread_label ??
              `${resolved.targetCurveFamily} ${resolved.targetTenor} vs ${resolved.regressorCurveFamily} ${resolved.regressorTenor}`}
            <span className="ml-2 text-[12.5px] font-normal text-fg-secondary">
              {resolved.regressionWindowDays}d window
            </span>
          </h2>
        </div>
        {cm && (
          <span className="text-[11.5px] text-fg-muted">
            As of {cm.as_of_date}
          </span>
        )}
      </header>

      {/* ---------- Controls strip (above the layout) ---------- */}
      <ControlsStrip
        controls={controls}
        onChange={handleControlChange}
        onReset={handleReset}
      />

      {/* ---------- Result body ---------- */}
      {errorMessage ? (
        <ExtendedError message={errorMessage} />
      ) : isLoading || !data || !cm ? (
        <ExtendedSkeleton />
      ) : (
        <ModelResultLayout
          hero={<ModelKpiStrip items={heroMetrics(data)} title="HEDGE FIT" />}
          primary={
            <ModelSeriesPanel
              title="Residual z-score · over time"
              description={`Stretch of today's residual vs its own ${cm.z_score_window_days_used}d history — the desk signal.`}
              series={singleModelSeries(
                data.time_series_residual_z_score,
                'residual_z',
                'residual z',
                0,
              )}
              unit="σ"
              decimals={2}
              zeroLine
            />
          }
          secondary={[
            <ModelSeriesPanel
              title="Residual · over time"
              description="target − β·regressor − α, in bps.  Positive = target cheap vs the hedge line; negative = rich."
              series={singleModelSeries(
                data.time_series_residual,
                'residual',
                'residual',
                1,
              )}
              unit="bp"
              decimals={1}
              zeroLine
            />,
            <ModelSeriesPanel
              title="Rolling β · over time"
              description={`Hedge ratio from each ${cm.regression_window_days_used}d trailing fit.`}
              series={singleModelSeries(
                data.time_series_beta,
                'beta',
                'β',
                2,
              )}
              unit="ratio"
              decimals={3}
            />,
          ]}
          diagnostics={
            <section className="card flex flex-col gap-3 px-5 py-4">
              <ModelKpiStrip
                items={diagnosticsMetrics(data)}
                title="DIAGNOSTICS"
                desktopCols={5}
              />
              <div className="flex flex-wrap items-center gap-2 border-t border-line-subtle pt-3">
                <span className="kicker text-fg-faint">LATEST FIT</span>
                <QualityBadge
                  level={condition.level}
                  label={condition.label}
                  note={condition.note}
                />
              </div>
            </section>
          }
          methodology={
            <MethodologyCard
              rows={buildMethodologyRows(data, resolved.fieldName)}
            />
          }
        />
      )}

      {/* ---------- Lineage footer ---------- */}
      <LineageFooter
        lineage={{
          toolName,
          version: 'v1',
          kind: 'Deterministic model fit (rolling OLS hedge + residual z)',
          providers: ['TimescaleDB', 'macro_data.v_market_data_daily_enriched'],
          asOf: cm?.as_of_date,
          freshness: 'fresh',
        }}
      />
    </div>
  );
};

export default BuildExtended;

// ---------------------------------------------------------------------------
// Skeleton + error states
// ---------------------------------------------------------------------------

function ExtendedSkeleton() {
  return (
    <div className="space-y-5">
      <div className="card grid grid-cols-2 gap-3 px-5 py-4 md:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="flex flex-col gap-1.5">
            <div className="h-2 w-20 animate-pulse rounded bg-line-subtle" />
            <div className="h-6 w-16 animate-pulse rounded bg-line-subtle" />
          </div>
        ))}
      </div>
      <div className="card h-[300px] animate-pulse bg-line-subtle/20" />
      <div className="card h-[260px] animate-pulse bg-line-subtle/20" />
      <div className="card h-[140px] animate-pulse bg-line-subtle/20" />
    </div>
  );
}

function ExtendedError({ message }: { message: string }) {
  return (
    <section className="card flex min-h-[200px] items-center justify-center px-6 py-8">
      <p className="max-w-[560px] text-center text-[12px] leading-[1.6] text-coral-300">
        {message}
      </p>
    </section>
  );
}
