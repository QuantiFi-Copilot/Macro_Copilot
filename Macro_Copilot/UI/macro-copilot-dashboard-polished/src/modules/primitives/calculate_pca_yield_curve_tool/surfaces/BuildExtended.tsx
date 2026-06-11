// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// calculate_pca_yield_curve_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every primitive ships an extended
// view; this is PCA's full canvas, composed from the rich-model grammar
// at @/components/shared/build/model (the dual-view replacement for the
// legacy BuilderCanvas / PcaLoadingsRenderer route):
//
//   hero        — ModelKpiStrip (components / variance / PC1 / obs)
//   primary     — MatrixTable   (loadings: tenors × pcN, quality chips)
//   secondary   — DecompositionBars (variance shares + Σ cumulative)
//                 ModelSeriesPanel  (factor scores over time)
//   diagnostics — ModelKpiStrip (fit window / frequency / sign anchor)
//                 + per-component QualityBadge row
//   methodology — MethodologyCard threaded from the response's *_used
//                 echo fields (P5) + the retained PC1/PC2/PC3
//                 interpretation cards (moved off modelMetadata)
//
// Controls strip above the layout exposes the GET-bridged Input surface
// (curve_family / tenors / lookback_days / n_components /
// change_frequency / field_name) — every other methodology knob is
// YAML-locked per A13 and therefore NOT a control here.
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
  ModelSeriesPanel,
  QualityBadge,
  qualityLevelForFlag,
} from '@/components/shared/build/model';
import {
  PCA_CHANGE_FREQUENCY_OPTIONS,
  PCA_COMPACT_CAVEAT,
  PCA_CURVE_FAMILY_OPTIONS,
  PCA_INTERPRETATION_CARDS,
  PCA_LOOKBACK_OPTIONS,
  PCA_N_COMPONENTS_OPTIONS,
  buildMethodologyRows,
  componentNames,
  diagnosticsKpis,
  factorModelSeries,
  heroKpis,
  loadingsColumnBadges,
  loadingsMatrixRows,
  parseOptionalNumber,
  usePcaYieldCurveData,
  varianceEntries,
} from './pcaYieldCurveShared';

const DEFAULTS = {
  curve_family: 'UST',
  tenors: '',
  lookback_days: '1825',
  n_components: '3',
  change_frequency: 'daily',
  field_name: '',
};

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params (URL strings; comma-joined tenors) -----
  const curveFamily = params.curve_family || DEFAULTS.curve_family;
  const tenorsCsv = params.tenors ?? DEFAULTS.tenors;
  const lookbackDays = params.lookback_days || DEFAULTS.lookback_days;
  const nComponents = params.n_components || DEFAULTS.n_components;
  const changeFrequency = params.change_frequency || DEFAULTS.change_frequency;
  const fieldName = params.field_name || DEFAULTS.field_name;

  const { data, isLoading, errorMessage } = usePcaYieldCurveData({
    curveFamily,
    tenorsCsv,
    lookbackDays: parseOptionalNumber(lookbackDays),
    nComponents: parseOptionalNumber(nComponents),
    changeFrequency,
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
    pushParams({ ...DEFAULTS });
  };

  // ----- Controls -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family',
      label: 'Curve Family',
      kind: 'enum',
      value: curveFamily,
      options: PCA_CURVE_FAMILY_OPTIONS,
    },
    {
      name: 'tenors',
      label: 'Tenors (subset, comma-joined)',
      kind: 'text',
      value: tenorsCsv,
    },
    {
      name: 'lookback_days',
      label: 'Lookback',
      kind: 'enum',
      value: lookbackDays,
      options: PCA_LOOKBACK_OPTIONS,
    },
    {
      name: 'n_components',
      label: 'Components',
      kind: 'enum',
      value: nComponents,
      options: PCA_N_COMPONENTS_OPTIONS,
    },
    {
      name: 'change_frequency',
      label: 'Change Frequency',
      kind: 'enum',
      value: changeFrequency,
      options: PCA_CHANGE_FREQUENCY_OPTIONS,
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
  const cols = cm ? componentNames(cm) : [];

  return (
    <div className="flex flex-col gap-4">
      {/* ---------- Identity header ---------- */}
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className="kicker text-fg-muted">
            PCA · YIELD CURVE — MODEL FIT · DETERMINISTIC
          </div>
          <h2 className="mt-0.5 text-[19px] font-semibold text-fg-primary">
            {curveFamily} PCA
            <span className="ml-2 text-[12.5px] font-normal text-fg-secondary">
              {nComponents} components · {changeFrequency} changes
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
          hero={<ModelKpiStrip items={heroKpis(cm)} title="PCA FIT" />}
          primary={
            <MatrixTable
              title="Loadings"
              description="One row per tenor; one column per component. Bar shading shows magnitude; mint = positive, coral = negative."
              rowKeyLabel="Tenor"
              columns={cols}
              columnBadges={loadingsColumnBadges(cm)}
              rows={loadingsMatrixRows(cm)}
              decimals={3}
            />
          }
          secondary={[
            <DecompositionBars
              title="Variance explained"
              description="Per-component share of yield-change variance in the fit window; Σ is the running cumulative."
              entries={varianceEntries(cm)}
              mode="share"
            />,
            <ModelSeriesPanel
              title="Factor scores · over time"
              description={`Each component's ${cm.change_frequency_used} score on the centered yield-changes panel.`}
              series={factorModelSeries(data)}
              currentLevels={cm.current_factor_levels}
              decimals={2}
              zeroLine
            />,
          ]}
          diagnostics={
            <section className="card flex flex-col gap-3 px-5 py-4">
              <ModelKpiStrip items={diagnosticsKpis(cm)} title="DIAGNOSTICS" />
              <div className="flex flex-wrap items-center gap-2 border-t border-line-subtle pt-3">
                <span className="kicker text-fg-faint">COMPONENT QUALITY</span>
                {cm.component_metadata.map((meta) => (
                  <span key={meta.component_name} className="flex items-center gap-1">
                    <span className="mono text-[10.5px] text-fg-secondary">
                      {meta.component_name}
                    </span>
                    <QualityBadge
                      level={qualityLevelForFlag(meta.quality_flag)}
                      label={
                        meta.quality_flag === 'ok'
                          ? 'ok'
                          : meta.quality_flag.replace(/_/g, ' ')
                      }
                      note={meta.quality_note ?? undefined}
                    />
                  </span>
                ))}
              </div>
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
                  {PCA_INTERPRETATION_CARDS.map((card) => (
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
          kind: 'Deterministic model fit (PCA on yield changes)',
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
      <div className="card h-[260px] animate-pulse bg-line-subtle/20" />
      <div className="card h-[140px] animate-pulse bg-line-subtle/20" />
      <div className="card h-[300px] animate-pulse bg-line-subtle/20" />
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
