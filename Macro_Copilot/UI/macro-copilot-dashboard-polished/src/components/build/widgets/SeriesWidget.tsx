// ============================================================================
// SeriesWidget — payload-backed default renderer for Series artifacts.
// ----------------------------------------------------------------------------
// PR4 — loads the persisted Series body via the PR3 hook and renders
// the actual observations rather than the pre-PR4
// ``ArtifactSummary.preview_values`` proxy (which was bounded to 16
// points and missed the tail of long series).
//
// Layout
// ------
//   - Series-key kicker (units in line)
//   - Latest observation: date + value (defensively formatted)
//   - Sparkline of every non-null observation (toned by stage category)
//   - Stats strip: first / last dates, total + finite obs counts
//
// Empty-state contract
// --------------------
// A Series with zero finite observations renders an explicit
// "no observations" state — not a 1970 fallback, not "0 rows", not
// a flatlined chart at y=0.
//
// What this widget DOES NOT do
// ----------------------------
//   - Never re-runs the underlying primitive.
//   - Never inflates the obs count to ``artifact.row_count`` when the
//     payload's index says otherwise.
//   - Never renders a sparkline of fewer than 2 points (one point
//     isn't a line).
// ============================================================================

import { useMemo } from 'react';
import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerArtifactRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { Sparkline } from '@/components/ui/Sparkline';
import type { ChartTone } from '@/lib/chart';
import { PayloadShell } from './shared/PayloadShell';
import {
  firstSeriesObservation,
  formatDate,
  formatNumberWithUnits,
  lastSeriesObservation,
  seriesFiniteCount,
  seriesObservationCount,
  type SeriesPayloadEnvelope,
} from './shared/artifactFormat';
import type { StageCategory } from '@/components/build/lib/buildTypes';

const TONE_BY_CATEGORY: Record<StageCategory, ChartTone> = {
  input: 'blue',
  transform: 'rates',
  output: 'amber',
};

const SeriesWidget: NodeRenderer = ({ node, artifact, category, size }) => {
  return (
    <PayloadShell<SeriesPayloadEnvelope>
      artifactHash={node.artifact_hash ?? artifact.hash}
      expectedType="Series"
      displayName="Series"
      isEmpty={(p) => seriesObservationCount(p) === 0}
      emptyMessage="The series persisted with zero observations."
    >
      {(payload) => (
        <SeriesBody payload={payload} category={category} size={size} />
      )}
    </PayloadShell>
  );
};

function SeriesBody({
  payload,
  category,
  size,
}: {
  payload: SeriesPayloadEnvelope;
  category: StageCategory;
  size: 'small' | 'medium' | 'wide' | 'tall';
}) {
  const units = payload.metadata.units;
  const last = useMemo(() => lastSeriesObservation(payload), [payload]);
  const first = useMemo(() => firstSeriesObservation(payload), [payload]);
  const totalObs = useMemo(() => seriesObservationCount(payload), [payload]);
  const finiteObs = useMemo(() => seriesFiniteCount(payload), [payload]);

  const sparklineData = useMemo(() => {
    const idx = payload.payload.index ?? [];
    const vs = payload.payload.values ?? [];
    const out: { value: number; index: string }[] = [];
    for (let i = 0; i < vs.length; i++) {
      const v = vs[i];
      if (v === null || v === undefined || !Number.isFinite(v)) continue;
      out.push({ value: v, index: idx[i] ?? String(i) });
    }
    return out;
  }, [payload]);

  if (finiteObs === 0) {
    return (
      <div className="flex min-h-0 flex-1 flex-col px-5 pt-3 pb-4">
        <div className="kicker text-fg-muted">
          {payload.metadata.series_key}
          {units && (
            <span className="ml-2 normal-case tracking-normal text-fg-faint">
              {units}
            </span>
          )}
        </div>
        <p className="mt-3 text-[11px] leading-[1.5] text-fg-secondary">
          The series ran but every observation is null.  Common cause:
          the rolling window was shorter than the primitive&apos;s
          <code className="mx-1 font-mono text-[10.5px] text-ice-300">
            min_periods
          </code>
          threshold.
        </p>
      </div>
    );
  }

  const tone = TONE_BY_CATEGORY[category];
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="px-5 pt-3 pb-1">
        <div className="kicker text-fg-muted">
          {payload.metadata.series_key}
          {units && (
            <span className="ml-2 normal-case tracking-normal text-fg-faint">
              {units}
            </span>
          )}
        </div>
        {last && (
          <div className="mt-1.5 flex items-baseline gap-2">
            <span className="font-mono text-[18px] tabular-nums text-fg-primary">
              {formatNumberWithUnits(last.value, units)}
            </span>
            <span className="font-mono text-[10px] text-fg-faint">
              as-of {formatDate(last.date)}
            </span>
          </div>
        )}
      </div>

      {sparklineData.length >= 2 && (
        <Sparkline
          data={sparklineData}
          tone={tone}
          mode="area"
          height={size === 'small' ? 56 : 96}
        />
      )}

      <div className="grid gap-x-4 gap-y-1.5 px-5 pt-3 pb-3 sm:grid-cols-4 grid-cols-2">
        <Cell
          label="First date"
          value={first ? formatDate(first.date) : '—'}
        />
        <Cell label="Last date" value={last ? formatDate(last.date) : '—'} />
        <Cell label="Obs" value={totalObs.toLocaleString()} />
        <Cell label="Finite" value={finiteObs.toLocaleString()} />
      </div>
    </div>
  );
}

function Cell({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="text-[9.5px] font-medium uppercase tracking-[0.16em] text-fg-faint">
        {label}
      </span>
      <span className="truncate font-mono text-[12px] tabular-nums text-fg-secondary">
        {value}
      </span>
    </div>
  );
}

registerArtifactRenderer('Series', SeriesWidget);
export { SeriesWidget };
