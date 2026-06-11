// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_yield_change_attribution_pca_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every primitive ships an extended
// view; this is attribution's full canvas, composed from the rich-model
// grammar at @/components/shared/build/model (the dual-view replacement
// for the legacy BuilderCanvas / AttributionRenderer route):
//
//   hero        — ModelKpiStrip (total Δ / top contributor / residual /
//                 resolved window)
//   primary     — DecompositionBars mode="signed" unit="bps"
//                 (per-component contributions + de-emphasised residual)
//   secondary   — MatrixTable, one row: the loading each component
//                 carries at the target tenor (the projection weights)
//   diagnostics — ModelKpiStrip (loadings provenance: source / fit
//                 window / overlap pct / fit observations)
//   methodology — MethodologyCard threaded from the window-provenance +
//                 loadings_* echo fields (P5), incl. the honest note
//                 that pasted_loadings is NOT bridged over GET, + the
//                 retained interpretation cards
//
// PURE SNAPSHOT output — no chart panel.  Controls strip above the
// layout exposes the GET-bridged Input subset; every other methodology
// knob is YAML-locked per A13.
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  ControlsStrip,
  LineageFooter,
  MethodologyCard,
  type BuildExtendedProps,
  type ControlDescriptor,
} from '@/components/shared/build';
import {
  DecompositionBars,
  MatrixTable,
  ModelKpiStrip,
  ModelResultLayout,
} from '@/components/shared/build/model';
import {
  ATTRIBUTION_CHANGE_FREQUENCY_OPTIONS,
  ATTRIBUTION_CURVE_FAMILY_OPTIONS,
  ATTRIBUTION_INTERPRETATION_CARDS,
  ATTRIBUTION_LOOKBACK_OPTIONS,
  ATTRIBUTION_N_COMPONENTS_OPTIONS,
  buildMethodologyRows,
  contributionEntries,
  defaultWindow,
  diagnosticsKpis,
  heroKpis,
  loadingAtTargetMatrixRow,
  parseOptionalNumber,
  useYieldChangeAttributionData,
} from './yieldChangeAttributionShared';

const STATIC_DEFAULTS = {
  curve_family: 'UST',
  target_tenor: '10Y',
  pca_lookback_days: '1825',
  n_components: '3',
  change_frequency: 'daily',
  tenors: '',
  field_name: '',
};

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params -----
  // The default window (~trailing quarter) is computed at render time
  // (FM7: module.ts stays a pure value, unlike the legacy spec's
  // module-eval IIFE).
  const fallbackWindow = defaultWindow();
  const curveFamily = params.curve_family || STATIC_DEFAULTS.curve_family;
  const targetTenor = params.target_tenor || STATIC_DEFAULTS.target_tenor;
  const startDate = params.start_date || fallbackWindow.start;
  const endDate = params.end_date || fallbackWindow.end;
  const pcaLookbackDays =
    params.pca_lookback_days || STATIC_DEFAULTS.pca_lookback_days;
  const nComponents = params.n_components || STATIC_DEFAULTS.n_components;
  const changeFrequency =
    params.change_frequency || STATIC_DEFAULTS.change_frequency;
  const tenorsCsv = params.tenors ?? STATIC_DEFAULTS.tenors;
  const fieldName = params.field_name || STATIC_DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = useYieldChangeAttributionData({
    curveFamily,
    targetTenor,
    startDate,
    endDate,
    pcaLookbackDays: parseOptionalNumber(pcaLookbackDays),
    nComponents: parseOptionalNumber(nComponents),
    changeFrequency,
    tenorsCsv,
    fieldName: fieldName || undefined,
  });

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
    pushParams({ ...params, [name]: value });
  };

  const handleReset = () => {
    pushParams({
      ...STATIC_DEFAULTS,
      start_date: fallbackWindow.start,
      end_date: fallbackWindow.end,
    });
  };

  // ----- Controls -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family',
      label: 'Curve Family',
      kind: 'enum',
      value: curveFamily,
      options: ATTRIBUTION_CURVE_FAMILY_OPTIONS,
    },
    {
      name: 'target_tenor',
      label: 'Target Tenor',
      kind: 'text',
      value: targetTenor,
    },
    {
      name: 'start_date',
      label: 'Window Start (YYYY-MM-DD)',
      kind: 'text',
      value: startDate,
    },
    {
      name: 'end_date',
      label: 'Window End (YYYY-MM-DD)',
      kind: 'text',
      value: endDate,
    },
    {
      name: 'n_components',
      label: 'Components',
      kind: 'enum',
      value: nComponents,
      options: ATTRIBUTION_N_COMPONENTS_OPTIONS,
    },
    {
      name: 'pca_lookback_days',
      label: 'PCA Lookback',
      kind: 'enum',
      value: pcaLookbackDays,
      options: ATTRIBUTION_LOOKBACK_OPTIONS,
      advanced: true,
    },
    {
      name: 'change_frequency',
      label: 'Change Frequency',
      kind: 'enum',
      value: changeFrequency,
      options: ATTRIBUTION_CHANGE_FREQUENCY_OPTIONS,
      advanced: true,
    },
    {
      name: 'tenors',
      label: 'Fit tenors (subset, comma-joined)',
      kind: 'text',
      value: tenorsCsv,
      advanced: true,
    },
    {
      name: 'field_name',
      label: 'field_name (override)',
      kind: 'text',
      value: fieldName,
      advanced: true,
    },
  ];

  const cm = data?.current_metrics ?? null;
  const loadingRow = cm ? loadingAtTargetMatrixRow(cm) : null;

  return (
    <div className="flex flex-col gap-4">
      {/* ---------- Identity header ---------- */}
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className="kicker text-fg-muted">
            YIELD-CHANGE ATTRIBUTION · PCA — SNAPSHOT · DETERMINISTIC
          </div>
          <h2 className="mt-0.5 text-[19px] font-semibold text-fg-primary">
            {curveFamily} {targetTenor} attribution
            <span className="ml-2 text-[12.5px] font-normal text-fg-secondary">
              {startDate} → {endDate}
            </span>
          </h2>
        </div>
        {cm && (
          <span className="text-[11.5px] text-fg-muted">
            Resolved {cm.start_date_resolved} → {cm.end_date_resolved}
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
      ) : isLoading || !data || !cm || !loadingRow ? (
        <ExtendedSkeleton />
      ) : (
        <ModelResultLayout
          hero={<ModelKpiStrip items={heroKpis(cm)} title="ATTRIBUTION" />}
          primary={
            <DecompositionBars
              title="Contribution · per component"
              description="target Δ = Σ contributions + residual; bars extend from the center axis — mint positive, coral negative; the residual row is de-emphasised."
              entries={contributionEntries(cm)}
              mode="signed"
              unit="bps"
              decimals={2}
            />
          }
          secondary={[
            <MatrixTable
              title="Loading at target tenor"
              description={`Each component's eigenvector entry at ${cm.target_tenor} — the unitless projection weight behind its contribution.`}
              rowKeyLabel="Tenor"
              columns={loadingRow.columns}
              columnBadges={loadingRow.columnBadges}
              rows={loadingRow.rows}
              decimals={3}
            />,
          ]}
          diagnostics={
            <section className="card px-5 py-4">
              <ModelKpiStrip
                items={diagnosticsKpis(cm)}
                title="LOADINGS PROVENANCE"
              />
            </section>
          }
          methodology={
            <div className="flex flex-col gap-4">
              <MethodologyCard rows={buildMethodologyRows(cm)} />
              <section className="card px-5 py-4">
                <div className="kicker mb-2 text-fg-muted">
                  CANONICAL INTERPRETATION (NOT RESPONSE-THREADED)
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  {ATTRIBUTION_INTERPRETATION_CARDS.map((card) => (
                    <div key={card.headline}>
                      <p className="text-[12px] font-semibold text-fg-primary">
                        {card.headline}
                      </p>
                      <p className="mt-0.5 text-[11.5px] leading-relaxed text-fg-secondary">
                        {card.body}
                      </p>
                    </div>
                  ))}
                </div>
              </section>
            </div>
          }
        />
      )}

      {/* ---------- Lineage footer ---------- */}
      <LineageFooter
        lineage={{
          toolName,
          version: 'v1',
          kind: 'Deterministic snapshot (PCA-projected yield-change decomposition)',
          providers: ['TimescaleDB', 'macro_data.v_market_data_daily_enriched'],
          asOf: cm?.end_date_resolved,
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
      <div className="card h-[180px] animate-pulse bg-line-subtle/20" />
      <div className="card h-[100px] animate-pulse bg-line-subtle/20" />
      <div className="card h-[120px] animate-pulse bg-line-subtle/20" />
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
