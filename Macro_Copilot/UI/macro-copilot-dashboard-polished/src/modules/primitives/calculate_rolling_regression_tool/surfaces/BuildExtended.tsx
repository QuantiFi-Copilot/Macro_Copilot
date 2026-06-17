// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_rolling_regression_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every primitive ships an extended
// view; this is rolling regression's full canvas, composed from the
// rich-model grammar at @/components/shared/build/model (consolidation
// target #4 — replaces the legacy BuilderCanvas mount that
// ``modelMetadata`` used to route).  Mounted by VirtualPrimitiveCanvas
// for single-tool queries OR by the click-to-expand modal from a
// compact card.
//
// Zone plan (ModelResultLayout enforces the order):
//   hero        — TARGET / β per regressor / R² (emphasis) / residual
//   primary     — rolling β panel, one line per regressor, zero line,
//                 legend carries the current β per regressor
//   secondary   — [α + residual panel (both yield-percent — one honest
//                 shared axis), R² panel]
//   diagnostics — window / min-periods / intercept / observations echo
//                 strip + the condition QualityBadge
//   methodology — threaded from the response's ``*_used`` echo fields
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
import { CURVE_OPTIONS, TENOR_OPTIONS } from '@/lib/monitorParamOptions';
import { DEFAULT_LOOKBACK_PRESETS, DEFAULT_WINDOW_PRESETS } from '@/lib/modelPresets';
import {
  MAX_REGRESSOR_SLOTS,
  ROLLING_REGRESSION_DEFAULTS,
  betaModelSeries,
  buildMethodologyRows,
  conditionQuality,
  diagnosticsMetrics,
  flattenRollingRegressionParams,
  heroMetrics,
  joinListParam,
  resolveRollingRegressionParams,
  singleModelSeries,
  useRollingRegressionData,
} from './rollingRegressionShared';

// Preset-derived select options.  The current value is spliced in when
// it isn't a preset (deep links may carry any [10, 2520] window).
function presetOptions(
  presets: ReadonlyArray<number>,
  current: string,
  suffix: string,
): Array<{ value: string; label: string }> {
  const opts = presets.map((p) => ({ value: String(p), label: `${p}${suffix}` }));
  if (current && !opts.some((o) => o.value === current)) {
    opts.push({ value: current, label: `${current}${suffix}` });
  }
  return opts;
}

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  decoded,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params (flat wire > Ask structured > defaults) ---
  const resolved = resolveRollingRegressionParams(
    params,
    decoded?.paramsStructured,
  );
  const { data, isLoading, errorMessage } = useRollingRegressionData(resolved);
  const cm = data?.current_metrics;

  // ----- URL update on control change -----
  const pushParams = (nextParams: Record<string, string>) => {
    // Stage D — inside the multi-tool DAG expand-to-modal, edits stay
    // local (onParamsChange) instead of navigating the global URL,
    // which would replace the multi-tool context behind the modal.
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
    const next = { ...resolved };
    if (name === 'regressor_count') {
      const count = Math.max(1, Math.min(MAX_REGRESSOR_SLOTS, Number(value)));
      const families = [...next.regressorCurveFamilies];
      const tenors = [...next.regressorTenors];
      while (families.length < count) {
        // New slots seed from the last slot — the desk's common case is
        // "same curve, neighbouring tenor"; any value is editable next.
        families.push(families[families.length - 1] ?? ROLLING_REGRESSION_DEFAULTS.regressor_curve_families);
        tenors.push(tenors[tenors.length - 1] ?? ROLLING_REGRESSION_DEFAULTS.regressor_tenors);
      }
      next.regressorCurveFamilies = families.slice(0, count);
      next.regressorTenors = tenors.slice(0, count);
    } else if (name.startsWith('regressor_') && name.endsWith('_curve_family')) {
      const i = Number(name.split('_')[1]) - 1;
      next.regressorCurveFamilies = next.regressorCurveFamilies.map(
        (f, j) => (j === i ? value : f),
      );
    } else if (name.startsWith('regressor_') && name.endsWith('_tenor')) {
      const i = Number(name.split('_')[1]) - 1;
      next.regressorTenors = next.regressorTenors.map(
        (t, j) => (j === i ? value : t),
      );
    } else if (name === 'target_curve_family') {
      next.targetCurveFamily = value;
    } else if (name === 'target_tenor') {
      next.targetTenor = value;
    } else if (name === 'regression_window_days') {
      next.regressionWindowDays = value;
    } else if (name === 'lookback_days') {
      next.lookbackDays = value;
    } else if (name === 'field_name') {
      next.fieldName = value;
    } else if (name === 'as_of_date') {
      next.asOfDate = value || undefined;
    }
    pushParams(flattenRollingRegressionParams(next));
  };

  const handleReset = () => {
    pushParams({ ...ROLLING_REGRESSION_DEFAULTS });
  };

  // ----- Controls (the param wire is the paired comma-joined lists; the
  // UI decomposes them into per-slot selects) -----
  const regressorControls: ControlDescriptor[] =
    resolved.regressorCurveFamilies.flatMap((family, i) => [
      {
        name: `regressor_${i + 1}_curve_family`,
        label: `Regressor ${i + 1}`,
        kind: 'enum' as const,
        value: family,
        options: CURVE_OPTIONS,
      },
      {
        name: `regressor_${i + 1}_tenor`,
        label: `R${i + 1} tenor`,
        kind: 'enum' as const,
        value: resolved.regressorTenors[i] ?? '',
        options: TENOR_OPTIONS,
      },
    ]);

  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'target_curve_family',
      label: 'Target',
      kind: 'enum',
      value: resolved.targetCurveFamily,
      options: CURVE_OPTIONS,
    },
    {
      name: 'target_tenor',
      label: 'Target tenor',
      kind: 'enum',
      value: resolved.targetTenor,
      options: TENOR_OPTIONS,
    },
    {
      name: 'regressor_count',
      label: 'Regressors',
      kind: 'enum',
      value: String(resolved.regressorCurveFamilies.length),
      options: Array.from({ length: MAX_REGRESSOR_SLOTS }, (_, i) => ({
        value: String(i + 1),
        label: String(i + 1),
      })),
    },
    ...regressorControls,
    {
      name: 'regression_window_days',
      label: 'Window',
      kind: 'enum',
      value: resolved.regressionWindowDays,
      options: presetOptions(
        DEFAULT_WINDOW_PRESETS,
        resolved.regressionWindowDays,
        'd',
      ),
    },
    {
      name: 'lookback_days',
      label: 'Lookback',
      kind: 'enum',
      value: resolved.lookbackDays,
      options: presetOptions(
        DEFAULT_LOOKBACK_PRESETS,
        resolved.lookbackDays,
        'd',
      ),
      advanced: true,
    },
    {
      name: 'field_name',
      label: 'Field',
      kind: 'enum',
      value: resolved.fieldName,
      options: [
        { value: 'YLD_YTM_MID', label: 'YLD_YTM_MID' },
        { value: 'YLD_YTM_BID', label: 'YLD_YTM_BID' },
        { value: 'YLD_YTM_ASK', label: 'YLD_YTM_ASK' },
      ],
      advanced: true,
    },
    asOfDateControl(params.as_of_date),
  ];

  // ----- Identity copy -----
  const regressorPhrase = resolved.regressorCurveFamilies
    .map((f, i) => `${f}_${resolved.regressorTenors[i] ?? ''}`)
    .join(' + ');
  const identityPrimary =
    cm?.target_label ?? `${resolved.targetCurveFamily}_${resolved.targetTenor}`;
  const identityFormula = cm
    ? `${cm.target_label} ~ ${cm.regressor_labels.join(' + ')}`
    : `${identityPrimary} ~ ${regressorPhrase}`;
  const condition = conditionQuality(data);

  // ----- Grammar zones -----
  const betaSeries = data ? betaModelSeries(data) : [];
  const residualAlphaSeries = data
    ? [
        ...singleModelSeries(data.time_series_residual, 'residual', 'Residual', 4),
        ...singleModelSeries(data.time_series_alpha, 'alpha', 'Alpha', 1),
      ]
    : [];
  const rSquaredSeries = data
    ? singleModelSeries(data.time_series_r_squared, 'r_squared', 'R²', 3)
    : [];

  return (
    <div
      className="flex h-full min-h-0 flex-col overflow-y-auto"
      data-testid="rolling-regression-build-extended"
    >
      {/* ---------- Title section ---------- */}
      <section className="flex flex-col gap-2 border-b border-line-subtle px-6 pt-5 pb-5">
        <div className="flex flex-wrap items-center gap-1.5 text-[11px] uppercase tracking-[0.05em]">
          <span className="font-semibold text-fg-secondary">ROLLING REGRESSION</span>
          <span className="text-fg-faint" aria-hidden>·</span>
          <span className="text-fg-muted">ROLLING OLS</span>
          <span className="text-fg-faint" aria-hidden>·</span>
          <span className="text-fg-muted">TIME-SERIES MODEL</span>
        </div>
        <h1 className="flex flex-wrap items-baseline gap-3 text-[26px] font-medium leading-tight tracking-[-0.012em] text-fg-primary">
          <span>{identityFormula}</span>
          <QualityBadge
            level={condition.level}
            label={condition.label}
            note={condition.note}
          />
        </h1>
        <p className="text-[12.5px] leading-[1.55] text-fg-secondary">
          Trailing-window OLS of one sovereign yield series on{' '}
          {resolved.regressorCurveFamilies.length === 1 ? 'one regressor' : `${resolved.regressorCurveFamilies.length} regressors`}
          {' '}— per-regressor β, α, residual, in-window R², condition flag.
        </p>
        {(cm?.as_of_date || resolved.regressionWindowDays) && (
          <p className="flex flex-wrap items-center gap-2 text-[11.5px] text-fg-muted">
            {cm?.as_of_date && <span>As of {cm.as_of_date}</span>}
            <span className="text-fg-faint" aria-hidden>·</span>
            <span>
              {resolved.regressionWindowDays}d window · {resolved.lookbackDays}d lookback · {resolved.fieldName}
            </span>
          </p>
        )}
      </section>

      {/* ---------- Controls strip ---------- */}
      <div className="px-6 pt-4">
        <ControlsStrip
          controls={controls}
          onChange={handleControlChange}
          onReset={handleReset}
        />
      </div>

      {/* ---------- Result body (grammar composition) ---------- */}
      <div className="px-6 py-4">
        {errorMessage ? (
          <ResultError message={errorMessage} />
        ) : !data ? (
          <ResultSkeleton />
        ) : (
          <ModelResultLayout
            hero={
              <section className="card px-5 py-4">
                <ModelKpiStrip
                  items={heroMetrics(data)}
                  title="LATEST FIT"
                  desktopCols={5}
                />
              </section>
            }
            primary={
              <ModelSeriesPanel
                title="Rolling β · per regressor"
                description="Trailing-window OLS coefficient per regressor at each window-end date — the partial elasticity of the target with the other regressors held flat."
                series={betaSeries}
                unit={data.time_series_betas[0]?.units ?? 'ratio'}
                zeroLine
                currentLevels={data.current_metrics.current_betas}
                decimals={3}
                height={320}
              />
            }
            secondary={[
              <ModelSeriesPanel
                key="residual-alpha"
                title="Residual + α · yield-percent"
                description="Per-row fit residual (y − α − Xβ) and the rolling intercept — both in yield-percent, one honest shared axis."
                series={residualAlphaSeries}
                unit={data.time_series_residual?.units ?? 'percent'}
                zeroLine
                decimals={3}
                height={220}
              />,
              <ModelSeriesPanel
                key="r-squared"
                title="Rolling R² · in-window fit quality"
                description="A sharp drop is a structural-break tell — the linear hedge ratio is losing predictive power for that horizon."
                series={rSquaredSeries}
                unit={data.time_series_r_squared?.units ?? 'ratio'}
                decimals={3}
                height={180}
              />,
            ]}
            diagnostics={
              <section className="card flex flex-col gap-3 px-5 py-4">
                <div className="flex items-center gap-2">
                  <span className="kicker text-fg-muted">FIT DIAGNOSTICS</span>
                  <QualityBadge
                    level={condition.level}
                    label={condition.label}
                    note={condition.note}
                  />
                </div>
                <ModelKpiStrip items={diagnosticsMetrics(data)} desktopCols={4} />
              </section>
            }
            methodology={
              <MethodologyCard
                rows={buildMethodologyRows(data, resolved.fieldName)}
              />
            }
          />
        )}
        {isLoading && data && <RefreshHint />}
      </div>

      {/* ---------- Lineage footer ---------- */}
      <div className="mt-auto pt-3">
        <LineageFooter
          lineage={{
            toolName,
            version: 'v1',
            kind: 'Rolling-window OLS (numpy.linalg.lstsq per window)',
            providers: ['TimescaleDB', 'macro_data.v_market_data_daily_enriched'],
            asOf: cm?.as_of_date,
            freshness: 'fresh',
          }}
        />
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Loading / error bodies (mirror the shared shells' treatments)
// ---------------------------------------------------------------------------

function ResultSkeleton() {
  return (
    <div className="space-y-5" aria-busy="true">
      <div className="card grid grid-cols-2 gap-4 px-5 py-4 sm:grid-cols-5">
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} className="flex flex-col gap-1.5">
            <div className="h-2 w-16 animate-pulse rounded bg-line-subtle" />
            <div className="h-6 w-20 animate-pulse rounded bg-line-subtle" />
          </div>
        ))}
      </div>
      <div className="card h-[320px] animate-pulse bg-line-subtle/30" />
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <div className="card h-[200px] animate-pulse bg-line-subtle/30" />
        <div className="card h-[200px] animate-pulse bg-line-subtle/30" />
      </div>
    </div>
  );
}

function ResultError({ message }: { message: string }) {
  return (
    <section className="card flex min-h-[280px] flex-col items-center justify-center gap-3 px-6">
      <p className="max-w-[480px] text-center text-[12px] leading-[1.5] text-coral-300">
        {message}
      </p>
      <p className="max-w-[480px] text-center text-[11px] leading-[1.5] text-fg-muted">
        A degenerate selection (target equal to a regressor) or a window
        shorter than the YAML min-periods returns a controlled error —
        adjust the controls above.
      </p>
    </section>
  );
}

function RefreshHint() {
  return (
    <p className="mt-3 text-center text-[10.5px] text-fg-faint">
      Refreshing with the latest controls…
    </p>
  );
}

export default BuildExtended;
