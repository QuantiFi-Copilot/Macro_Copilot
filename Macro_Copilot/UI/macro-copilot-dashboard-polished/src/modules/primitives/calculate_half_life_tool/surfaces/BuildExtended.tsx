// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// ``calculate_half_life_tool``.  RICH-MODEL shape (OU / AR(1) snapshot).
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every primitive claiming
// ``custom_build_surface`` ships an extended view; this is half-life's
// full canvas, mounted by VirtualPrimitiveCanvas for single-tool queries
// AND by the click-to-expand modal infrastructure when invoked from the
// compact card.
//
// Composition: the rich-model GRAMMAR at @/components/shared/build/model
// (ModelResultLayout zone scaffold → ModelKpiStrip hero → DecompositionBars
// deviation read → MatrixTable β block → diagnostics strip → MethodologyCard),
// with the shared ControlsStrip / FreshnessPill / LineageFooter chrome so
// the surface matches the rest of the Build catalogue.  The shared
// ``BuildExtendedShell`` is chart-centric (chartPoints + reference bands);
// half-life is a PURE SNAPSHOT (no time series on the wire — pinned by the
// backend's test_no_time_series_output), so this module composes the model
// grammar inside its own canvas instead — the same precedent
// scan_extremes_tool established for non-LEVEL-shaped tools.
//
// Controls expose the GET bridge's two input modes (single series / pair
// spread); the tool's third variant (pasted_series) is intentionally NOT
// offered — honest note lives in the methodology card.
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { Timer } from 'lucide-react';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  ControlsStrip,
  FreshnessPill,
  LineageFooter,
  MethodologyCard,
  asOfDateControl,
  type BuildExtendedProps,
  type ControlDescriptor,
} from '@/components/shared/build';
import {
  DecompositionBars,
  MatrixTable,
  ModelKpiStrip,
  ModelResultLayout,
  QualityBadge,
} from '@/components/shared/build/model';
import { CURVE_OPTIONS, TENOR_OPTIONS } from '@/lib/monitorParamOptions';
import {
  BETA_COLUMNS,
  BETA_COLUMN_LABELS,
  HALF_LIFE_DEFAULTS,
  HALF_LIFE_FIELD_OPTIONS,
  HALF_LIFE_LOOKBACK_OPTIONS,
  HALF_LIFE_MODE_OPTIONS,
  PAIR_SECOND_LEG_DEFAULT,
  betaBlockDescription,
  betaMatrixRows,
  buildMethodologyRows,
  buildReferenceChips,
  constructedSeriesLabel,
  deviationDescription,
  deviationEntries,
  diagnosticsKpis,
  heroKpis,
  meanReversionQuality,
  modeForParams,
  unitLabelForSeriesUnits,
  useHalfLifeData,
} from './halfLifeShared';

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params (Record<string,string> parsing) -----
  const curveFamily = params.curve_family ?? HALF_LIFE_DEFAULTS.curve_family;
  const tenor = params.tenor ?? HALF_LIFE_DEFAULTS.tenor;
  const curveFamily2 = params.curve_family_2 || undefined;
  const lookbackDays = params.lookback_days || HALF_LIFE_DEFAULTS.lookback_days;
  const fieldName = params.field_name || HALF_LIFE_DEFAULTS.field_name;
  const mode = modeForParams(params);

  const { data, isLoading, errorMessage } = useHalfLifeData({
    curveFamily,
    tenor,
    curveFamily2,
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
    const nextParams = { ...params };
    if (name === 'mode') {
      // The mode toggle expands/strips ``curve_family_2`` — the wire
      // param that flips the GET bridge into PAIR mode.
      if (value === 'pair') {
        nextParams.curve_family_2 =
          nextParams.curve_family_2 || PAIR_SECOND_LEG_DEFAULT;
      } else {
        delete nextParams.curve_family_2;
      }
    } else {
      nextParams[name] = value;
    }
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({
      curve_family: HALF_LIFE_DEFAULTS.curve_family,
      tenor: HALF_LIFE_DEFAULTS.tenor,
      lookback_days: HALF_LIFE_DEFAULTS.lookback_days,
      field_name: HALF_LIFE_DEFAULTS.field_name,
    });
  };

  // ----- Controls -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'mode',
      label: 'Mode',
      kind: 'enum',
      value: mode,
      options: HALF_LIFE_MODE_OPTIONS,
    },
    {
      name: 'curve_family',
      label: mode === 'pair' ? 'Curve Family (leg 1)' : 'Curve Family',
      kind: 'enum',
      value: curveFamily,
      options: CURVE_OPTIONS,
    },
    ...(mode === 'pair'
      ? ([
          {
            name: 'curve_family_2',
            label: 'Curve Family (leg 2)',
            kind: 'enum',
            value: curveFamily2 ?? PAIR_SECOND_LEG_DEFAULT,
            options: CURVE_OPTIONS,
          },
        ] as const)
      : []),
    {
      name: 'tenor',
      label: 'Tenor',
      kind: 'enum',
      value: tenor,
      options: TENOR_OPTIONS,
    },
    {
      name: 'lookback_days',
      label: 'Lookback',
      kind: 'enum',
      value: lookbackDays,
      options: HALF_LIFE_LOOKBACK_OPTIONS,
    },
    {
      name: 'field_name',
      label: 'Field',
      kind: 'enum',
      value: fieldName,
      options: HALF_LIFE_FIELD_OPTIONS,
      advanced: true,
    },
    asOfDateControl(params.as_of_date),
  ];

  // ----- Identity -----
  const cm = data?.current_metrics;
  const seriesLabel =
    cm?.series_label ?? constructedSeriesLabel(curveFamily, tenor, curveFamily2);
  const unit = unitLabelForSeriesUnits(cm?.series_units);
  const quality = cm ? meanReversionQuality(cm) : null;
  const subtitle =
    mode === 'pair'
      ? `Pair spread (${curveFamily} − ${curveFamily2 ?? PAIR_SECOND_LEG_DEFAULT}) × 100 at ${tenor}, bps — cross_market_spread direction convention`
      : `Single sovereign yield series (${curveFamily} ${tenor}), percent`;

  return (
    <div
      className="flex h-full min-h-0 flex-col overflow-y-auto"
      data-testid="half-life-extended"
    >
      {/* ---------- Title section ---------- */}
      <section className="flex flex-col gap-2 border-b border-line-subtle px-6 pt-5 pb-5">
        <div className="flex items-center gap-2 text-[11px] uppercase tracking-wide text-fg-muted">
          <Timer size={12} strokeWidth={1.75} className="text-ice-300" aria-hidden />
          <span className="font-semibold">MEAN-REVERSION HALF-LIFE</span>
          <span className="text-fg-faint" aria-hidden>•</span>
          <span>SNAPSHOT</span>
          <span className="text-fg-faint" aria-hidden>•</span>
          <span>OU / AR(1) OLS</span>
        </div>
        <div className="flex items-baseline gap-3">
          <h1 className="text-[24px] font-semibold leading-tight text-fg-primary">
            {seriesLabel}
          </h1>
          {quality && (
            <QualityBadge
              level={quality.level}
              label={quality.label}
              note={quality.note}
            />
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[12px] text-fg-secondary">
          <span>{subtitle}</span>
          {cm?.as_of_date && (
            <>
              <span className="text-fg-faint" aria-hidden>·</span>
              <span>As of {cm.as_of_date}</span>
            </>
          )}
          <span className="text-fg-faint" aria-hidden>·</span>
          <span>{lookbackDays}d window · {fieldName}</span>
          <FreshnessPill freshness="fresh" />
        </div>
      </section>

      {/* ---------- Controls strip ---------- */}
      <div className="px-6 pt-4">
        <ControlsStrip
          controls={controls}
          onChange={handleControlChange}
          onReset={handleReset}
        />
      </div>

      {/* ---------- Model result body (grammar scaffold) ---------- */}
      <div className="px-6 pt-4 pb-2">
        {errorMessage ? (
          <ExtendedError message={errorMessage} />
        ) : isLoading || !data ? (
          <ExtendedSkeleton />
        ) : (
          <ModelResultLayout
            hero={
              <section className="card px-5 py-4">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <span className="kicker">OU / AR(1) fit — {seriesLabel}</span>
                  {quality && (
                    <QualityBadge
                      level={quality.level}
                      label={quality.label}
                      note={quality.note}
                    />
                  )}
                </div>
                <ModelKpiStrip items={heroKpis(data)} />
              </section>
            }
            primary={
              <DecompositionBars
                title="Deviation read"
                description={deviationDescription(data)}
                entries={deviationEntries(data)}
                mode="signed"
                unit={unit}
                decimals={2}
              />
            }
            secondary={[
              <MatrixTable
                key="beta"
                title="OU drift coefficient (β)"
                description={betaBlockDescription(data)}
                rowKeyLabel="Coefficient"
                columns={[...BETA_COLUMNS]}
                columnLabels={BETA_COLUMN_LABELS}
                rows={betaMatrixRows(data)}
                decimals={4}
              />,
            ]}
            diagnostics={
              <section className="card px-5 py-4">
                <ModelKpiStrip
                  title="Fit diagnostics"
                  items={diagnosticsKpis(data, lookbackDays)}
                />
              </section>
            }
            methodology={
              <MethodologyCard
                rows={buildMethodologyRows(data, mode, fieldName)}
                references={buildReferenceChips()}
              />
            }
          />
        )}
      </div>

      {/* ---------- Lineage footer ---------- */}
      <LineageFooter
        lineage={{
          toolName,
          version: 'v1',
          kind: 'Deterministic snapshot (OU / AR(1) OLS fit)',
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
    <div className="space-y-5" aria-busy>
      <div className="card h-[120px] animate-pulse bg-line-subtle/20 px-5 py-4" />
      <div className="card h-[96px] animate-pulse bg-line-subtle/20 px-5 py-4" />
      <div className="card h-[88px] animate-pulse bg-line-subtle/20 px-5 py-4" />
      <div className="card h-[88px] animate-pulse bg-line-subtle/20 px-5 py-4" />
    </div>
  );
}

function ExtendedError({ message }: { message: string }) {
  return (
    <div className="card flex min-h-[180px] items-center justify-center px-6 py-6">
      <p className="max-w-[560px] text-center text-[12px] leading-[1.6] text-coral-300">
        {message}
      </p>
    </div>
  );
}
