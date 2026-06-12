// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// classify_curve_move_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every primitive ships an extended
// view; this is the curve classifier's full canvas.  PURE SNAPSHOT
// tool — the wire is one classified observation (no time series), so
// the canvas is a REGIME READ, not a chart:
//
//   hero      — the regime label (emphasized, toned) + spread move +
//               display-only driver + spread now-vs-prior
//   quadrant  — the four per-leg numbers behind the call (front/back
//               levels + signed changes)
//   methodology — wire fields + the config.yaml-locked thresholds
//   lineage   — the standard footer
//
// This migration retired the legacy 'regime' typed-view route — the
// LAST legacy view claim in the codebase; the routing substrate
// itself was deleted in consolidation target #5 (G-3.5).
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
import { PrimitiveMetrics } from '@/components/build/primitive/PrimitiveMetrics';
import {
  CLASSIFY_DEFAULTS,
  CURVE_FAMILY_OPTIONS,
  LOOKBACK_PERIOD_OPTIONS,
  TENOR_OPTIONS,
  buildMethodologyRows,
  heroMetrics,
  quadrantMetrics,
  useRegimeData,
} from './classifyCurveMoveShared';

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params -----
  const curveFamily = params.curve_family || CLASSIFY_DEFAULTS.curve_family;
  const frontTenor = params.front_tenor || CLASSIFY_DEFAULTS.front_tenor;
  const backTenor = params.back_tenor || CLASSIFY_DEFAULTS.back_tenor;
  const lookbackPeriod =
    params.lookback_period || CLASSIFY_DEFAULTS.lookback_period;
  const fieldName = params.field_name || '';

  const { data, isLoading, errorMessage } = useRegimeData({
    curveFamily,
    frontTenor,
    backTenor,
    lookbackPeriod,
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
    pushParams({ ...CLASSIFY_DEFAULTS });
  };

  // ----- Controls -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family',
      label: 'Curve Family',
      kind: 'enum',
      value: curveFamily,
      options: CURVE_FAMILY_OPTIONS,
    },
    {
      name: 'front_tenor',
      label: 'Front Tenor',
      kind: 'enum',
      value: frontTenor,
      options: TENOR_OPTIONS,
    },
    {
      name: 'back_tenor',
      label: 'Back Tenor',
      kind: 'enum',
      value: backTenor,
      options: TENOR_OPTIONS,
    },
    {
      name: 'lookback_period',
      label: 'Lookback',
      kind: 'enum',
      value: lookbackPeriod,
      options: LOOKBACK_PERIOD_OPTIONS,
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

  return (
    <div className="flex flex-col gap-4">
      {/* ---------- Identity header ---------- */}
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className="kicker text-fg-muted">
            CURVE MOVE CLASSIFIER — DETERMINISTIC SNAPSHOT
          </div>
          <h2 className="mt-0.5 text-[19px] font-semibold text-fg-primary">
            {curveFamily} {frontTenor}/{backTenor}
            <span className="ml-2 text-[12.5px] font-normal text-fg-secondary">
              {lookbackPeriod} move
            </span>
          </h2>
        </div>
        {cm && (
          <span className="text-[11.5px] text-fg-muted">
            As of {cm.as_of_date}
          </span>
        )}
      </header>

      {/* ---------- Controls strip ---------- */}
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
        <div className="space-y-5">
          <section className="card px-5 py-4">
            <PrimitiveMetrics
              items={heroMetrics(data)}
              title="REGIME READ"
              desktopCols={4}
            />
          </section>
          <section className="card px-5 py-4">
            <PrimitiveMetrics
              items={quadrantMetrics(data)}
              title="THE FOUR NUMBERS BEHIND THE CALL"
              desktopCols={4}
            />
          </section>
          <MethodologyCard rows={buildMethodologyRows(data)} />
        </div>
      )}

      {/* ---------- Lineage footer ---------- */}
      <LineageFooter
        lineage={{
          toolName,
          version: 'v1',
          kind: 'Deterministic snapshot (six-label curve-move classification)',
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
      <div className="card h-[140px] animate-pulse bg-line-subtle/20" />
      <div className="card h-[160px] animate-pulse bg-line-subtle/20" />
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
