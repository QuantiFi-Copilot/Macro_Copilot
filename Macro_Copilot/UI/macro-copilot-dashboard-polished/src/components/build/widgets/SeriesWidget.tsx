// ============================================================================
// SeriesWidget — payload-backed default renderer for Series artifacts.
// ----------------------------------------------------------------------------
// PR4 — loaded the persisted Series body via the PR3 hook and rendered
// the actual observations rather than the ``ArtifactSummary.preview_values``
// proxy.
//
// PR2 (new plan) — applies the central date-policy classifier from
// ``artifactFormat.ts`` so the two LEGITIMATE-but-meaningless sentinel
// dates the backend emits don't leak into the user-facing card:
//
//   - ``summarize_series`` emits a one-row Series at the hard-coded
//     ``SUMMARY_SENTINEL_DATE`` (``1900-01-01``).  We detect that
//     shape and render a ``SeriesSummaryScalar`` body: big-number
//     value + units, no sparkline, no "as-of" date line.
//
//   - ``conditional_aggregate`` emits a Series whose index is
//     ``1970-01-01 + offset_days`` for an N-day forward window, with
//     the actual offsets stamped onto ``payload.index_encoding``.
//     We detect that and render an ``OffsetLabeledSeries`` body:
//     same sparkline + stats strip shape, but row labels switch from
//     calendar dates to ``t-N`` / ``t0`` / ``t+N`` offset ticks.
//
// Every other Series renders unchanged (the pre-PR2 sparkline + stats
// strip with real ``formatDate``-formatted dates).
// ============================================================================

import { useMemo } from 'react';
import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerArtifactRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { Sparkline } from '@/components/ui/Sparkline';
import type { ChartTone } from '@/lib/chart';
import { PayloadShell } from './shared/PayloadShell';
import {
  classifyArtifactDate,
  firstSeriesObservation,
  formatDate,
  formatNumberWithUnits,
  getEventOffsetEncoding,
  isSentinelOneRowSeries,
  lastSeriesObservation,
  MISSING_VALUE_DASH,
  offsetLabel,
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
  // Dispatch on payload shape FIRST.  The summary-sentinel + event-
  // offset branches both produce a different visual register than the
  // canonical date-indexed Series.
  if (isSentinelOneRowSeries(payload)) {
    return <SeriesSummaryScalar payload={payload} />;
  }
  const offsetEncoding = getEventOffsetEncoding(payload);
  if (offsetEncoding !== null) {
    return (
      <OffsetLabeledSeries
        payload={payload}
        offsets={offsetEncoding.offsets}
        category={category}
        size={size}
      />
    );
  }
  return <CalendarSeries payload={payload} category={category} size={size} />;
}

// ----------------------------------------------------------------------------
// Default render — calendar-indexed Series (the common case)
// ----------------------------------------------------------------------------

function CalendarSeries({
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
    return <AllNullsBody payload={payload} />;
  }

  const tone = TONE_BY_CATEGORY[category];
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="px-5 pt-3 pb-1">
        <SeriesKeyLine payload={payload} />
        {last && (
          <div className="mt-1.5 flex items-baseline gap-2">
            <span className="font-mono text-[18px] tabular-nums text-fg-primary">
              {formatNumberWithUnits(last.value, units)}
            </span>
            <span className="font-mono text-[10px] text-fg-faint">
              {asOfLabel(last.date)}
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
          value={first ? formatRealDateCell(first.date) : MISSING_VALUE_DASH}
        />
        <Cell
          label="Last date"
          value={last ? formatRealDateCell(last.date) : MISSING_VALUE_DASH}
        />
        <Cell label="Obs" value={totalObs.toLocaleString()} />
        <Cell label="Finite" value={finiteObs.toLocaleString()} />
      </div>
    </div>
  );
}

/** "as-of {date}" preamble — when the date classifies as the summary
 *  sentinel, suppress the line entirely; when unknown, suppress too;
 *  otherwise return the formatted date. */
function asOfLabel(value: unknown): string {
  const cls = classifyArtifactDate(value);
  if (cls.kind === 'summary_sentinel') return '';
  if (cls.kind === 'unknown') return '';
  return `as-of ${formatDate(value)}`;
}

/** Format a real-date cell, suppressing sentinels.  Returns the
 *  formatted date for real-date input, or the missing-value dash for
 *  sentinels / unknown / unparseable values. */
function formatRealDateCell(value: unknown): string {
  const cls = classifyArtifactDate(value);
  if (cls.kind !== 'real_date') return MISSING_VALUE_DASH;
  return formatDate(value);
}

// ----------------------------------------------------------------------------
// Summary-scalar render — one-row Series at SUMMARY_SENTINEL_DATE
// ----------------------------------------------------------------------------

function SeriesSummaryScalar({ payload }: { payload: SeriesPayloadEnvelope }) {
  const units = payload.metadata.units;
  const value = payload.payload.values?.[0];
  return (
    <div className="flex min-h-0 flex-1 flex-col px-5 pt-3 pb-4">
      <div className="kicker text-fg-muted">
        Summary · {payload.metadata.series_key}
        {units && (
          <span className="ml-2 normal-case tracking-normal text-fg-faint">
            {units}
          </span>
        )}
      </div>
      <div className="mt-3 flex items-baseline gap-2">
        <span className="font-serif-display text-[24px] font-light leading-none text-fg-primary">
          {Number.isFinite(value)
            ? formatNumberWithUnits(value, units)
            : MISSING_VALUE_DASH}
        </span>
      </div>
      <p className="mt-3 text-[10.5px] leading-[1.5] text-fg-faint">
        Scalar summary — the persisted Series is one row at the
        backend&apos;s sentinel-date marker.  No time axis to chart.
      </p>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Offset-labeled render — Series with event-relative offsets
// (conditional_aggregate output)
// ----------------------------------------------------------------------------

function OffsetLabeledSeries({
  payload,
  offsets,
  category,
  size,
}: {
  payload: SeriesPayloadEnvelope;
  offsets: number[];
  category: StageCategory;
  size: 'small' | 'medium' | 'wide' | 'tall';
}) {
  const units = payload.metadata.units;
  const totalObs = useMemo(() => seriesObservationCount(payload), [payload]);
  const finiteObs = useMemo(() => seriesFiniteCount(payload), [payload]);

  // Build a labelled point list keyed on offset rather than ISO date.
  const sparklineData = useMemo(() => {
    const vs = payload.payload.values ?? [];
    const out: { value: number; index: string; offset: number }[] = [];
    for (let i = 0; i < vs.length; i++) {
      const v = vs[i];
      if (v === null || v === undefined || !Number.isFinite(v)) continue;
      const offset = offsets[i];
      const label =
        typeof offset === 'number' ? offsetLabel(offset) : String(i);
      out.push({ value: v, index: label, offset: offset ?? 0 });
    }
    return out;
  }, [payload, offsets]);

  if (finiteObs === 0) {
    return <AllNullsBody payload={payload} />;
  }

  // First / last offsets surfaced explicitly.
  const firstOffset = offsets[0];
  const lastOffset = offsets[offsets.length - 1];
  const tone = TONE_BY_CATEGORY[category];

  // Pull the last finite value for the headline.
  let lastValue: number | null = null;
  let lastOffsetForHeadline: number | null = null;
  const vs = payload.payload.values ?? [];
  for (let i = vs.length - 1; i >= 0; i--) {
    const v = vs[i];
    if (v !== null && v !== undefined && Number.isFinite(v)) {
      lastValue = v;
      lastOffsetForHeadline = offsets[i] ?? null;
      break;
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="px-5 pt-3 pb-1">
        <div className="kicker text-fg-muted">
          {payload.metadata.series_key}
          <span className="ml-2 rounded-sm border border-violet-400/30 bg-violet-500/10 px-1.5 py-0.5 normal-case tracking-normal text-violet-200">
            event-relative
          </span>
          {units && (
            <span className="ml-2 normal-case tracking-normal text-fg-faint">
              {units}
            </span>
          )}
        </div>
        {lastValue !== null && (
          <div className="mt-1.5 flex items-baseline gap-2">
            <span className="font-mono text-[18px] tabular-nums text-fg-primary">
              {formatNumberWithUnits(lastValue, units)}
            </span>
            {lastOffsetForHeadline !== null && (
              <span className="font-mono text-[10px] text-fg-faint">
                at {offsetLabel(lastOffsetForHeadline)}
              </span>
            )}
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
          label="First offset"
          value={
            typeof firstOffset === 'number'
              ? offsetLabel(firstOffset)
              : MISSING_VALUE_DASH
          }
        />
        <Cell
          label="Last offset"
          value={
            typeof lastOffset === 'number'
              ? offsetLabel(lastOffset)
              : MISSING_VALUE_DASH
          }
        />
        <Cell label="Obs" value={totalObs.toLocaleString()} />
        <Cell label="Finite" value={finiteObs.toLocaleString()} />
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Shared sub-components
// ----------------------------------------------------------------------------

function AllNullsBody({ payload }: { payload: SeriesPayloadEnvelope }) {
  const units = payload.metadata.units;
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

function SeriesKeyLine({ payload }: { payload: SeriesPayloadEnvelope }) {
  const units = payload.metadata.units;
  return (
    <div className="kicker text-fg-muted">
      {payload.metadata.series_key}
      {units && (
        <span className="ml-2 normal-case tracking-normal text-fg-faint">
          {units}
        </span>
      )}
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
