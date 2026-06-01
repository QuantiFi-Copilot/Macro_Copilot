// ============================================================================
// artifactFormat — defensive formatters for payload-backed widgets.
// ----------------------------------------------------------------------------
// PR4 — small, deterministic, side-effect-free helpers shared across
// every payload-backed widget (Series / SeriesSet / Panel /
// WindowedPanel / EventSet / TradeSet).
//
// Discipline
// ----------
// 1. NEVER coerce invalid input to a misleading default.  Bad date
//    strings render as ``"—"`` rather than ``"1970-01-01"`` (the
//    Unix-epoch trap that ``new Date(undefined)`` falls into when the
//    caller isn't careful).
// 2. NEVER swallow type-discriminating null/undefined.  Each helper
//    has a clear ``null``-vs-fallback contract documented inline.
// 3. NEVER do business logic.  These are pure presentation transforms;
//    higher-level "what does this payload look like" decisions belong
//    in the widgets themselves.
// 4. Keep them deterministic.  No locale-dependent date parsing, no
//    runtime config lookup.  ISO-8601 in → human string out, full stop.
//
// What this file is NOT
// ---------------------
// Not a widget.  Not a hook.  Not a UI component.  Pure functions
// only; bundlers should tree-shake unused exports cleanly.
// ============================================================================

import type {
  ArtifactPayloadResponse,
  EventSetPayloadEnvelope,
  PanelPayloadEnvelope,
  ScalarMetricPayloadEnvelope,
  SeriesPayloadEnvelope,
  SeriesSetPayloadEnvelope,
  TradeSetPayloadEnvelope,
  WindowedPanelPayloadEnvelope,
} from '@/types/artifacts';

// ---------------------------------------------------------------------------
// Date formatting — the load-bearing helper.  The widgets used to (and
// some libraries still do) call ``new Date(value).toISOString().slice(0,10)``
// which silently produces ``"1970-01-01"`` when ``value`` is null /
// undefined / unparseable.  We never do that.
// ---------------------------------------------------------------------------

/** A user-visible placeholder for missing / unparseable values.  One
 *  shared constant so we don't sprinkle em-dashes through every
 *  widget — search-and-replace stays cheap if the visual register
 *  ever changes. */
export const MISSING_VALUE_DASH = '—';

/** Returns a normalised ISO-8601 date string (``YYYY-MM-DD``) when
 *  the input parses cleanly, OR ``MISSING_VALUE_DASH`` when it
 *  doesn't.  Never returns ``"1970-01-01"`` for invalid input — that
 *  was the bug pattern PR4 set out to eliminate.
 *
 *  Accepts:
 *    - ISO-8601 strings (the wire format every artifact uses):
 *      ``"2025-01-03"``, ``"2025-01-03T00:00:00Z"``
 *    - numeric epoch ms via the explicit ``epochMs`` flag (off by
 *      default because most artifact wires send strings).
 *
 *  Rejects:
 *    - null / undefined / empty string → ``MISSING_VALUE_DASH``.
 *    - any string that ``Date.parse`` returns NaN for → ``MISSING_VALUE_DASH``.
 *    - the all-zeros sentinel ``"1970-01-01"`` IF + ONLY IF the
 *      caller passes ``{rejectEpochSentinel: true}`` (Series and
 *      EventSet wires don't emit it, so the default is to leave it
 *      alone).
 */
export interface FormatDateOptions {
  epochMs?: boolean;
  rejectEpochSentinel?: boolean;
}

export function formatDate(
  value: unknown,
  options: FormatDateOptions = {},
): string {
  if (value === null || value === undefined) return MISSING_VALUE_DASH;
  if (typeof value === 'string') {
    if (value === '') return MISSING_VALUE_DASH;
    // The Unix-epoch sentinel — explicit opt-in because some payloads
    // legitimately reference 1970-01-01.
    if (options.rejectEpochSentinel && value.startsWith('1970-01-01')) {
      return MISSING_VALUE_DASH;
    }
    const parsed = Date.parse(value);
    if (Number.isNaN(parsed)) return MISSING_VALUE_DASH;
    return value.slice(0, 10);
  }
  if (typeof value === 'number') {
    if (!options.epochMs) return MISSING_VALUE_DASH;
    if (!Number.isFinite(value)) return MISSING_VALUE_DASH;
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return MISSING_VALUE_DASH;
    return d.toISOString().slice(0, 10);
  }
  return MISSING_VALUE_DASH;
}

// ---------------------------------------------------------------------------
// PR2 — central date policy.
// ---------------------------------------------------------------------------
//
// The backend operators emit two LEGITIMATE sentinel dates that look
// like real market dates to a naive renderer:
//
//   - ``summarize_series`` (``shared/operators/summarize_series/operator.py``)
//     emits a one-row Series whose single index entry is the
//     hard-coded ``SUMMARY_SENTINEL_DATE = 1900-01-01``.  The
//     surrounding workflow uses this row as a "scalar summary"
//     marker — the date is semantically meaningless.
//
//   - ``conditional_aggregate``
//     (``shared/operators/conditional_aggregate/operator.py``) emits
//     a Series whose index is ``_OFFSET_ANCHOR + Timedelta(days=offset)``
//     where ``_OFFSET_ANCHOR = 1970-01-01`` and ``offset`` is
//     ``[0, 1, ..., N]`` for an N-day forward window.  The artifact
//     store auto-promotes ``payload.index_encoding =
//     {kind: "event_offset", anchor, offsets}`` so downstream
//     consumers can translate the synthetic dates back to
//     event-relative offsets ("t+5", "t-2", "t0").
//
// Pre-PR2 the widgets called ``formatDate`` on these values blindly
// → users saw "as-of 1900-01-01" / "as-of 1970-01-06" on event-study
// aggregate cards and regime summary cards.  Both look like garbage
// data.  PR2 introduces a single classification helper that every
// widget consults before rendering a date.

/** ISO-8601 string for the ``summarize_series`` operator's sentinel
 *  date.  Hard-coded on both backend + frontend; changing it requires
 *  a coordinated schema bump.  See operator source. */
export const SUMMARY_SENTINEL_DATE = '1900-01-01';

/** ISO-8601 string for the ``conditional_aggregate`` operator's
 *  event-offset anchor.  Same source-of-truth discipline. */
export const EVENT_OFFSET_ANCHOR = '1970-01-01';

/** Closed-vocabulary classification of an artifact-side date value.
 *  Drives per-widget rendering policy ("show as a date" vs "hide" vs
 *  "translate to offset label" vs "fallback").  Adding a new
 *  classification is a closed-family extension — one entry on the
 *  union + one branch in the classifier. */
export type ArtifactDateClass =
  | 'real_date' // a real calendar date the user should see
  | 'summary_sentinel' // 1900-01-01 — semantically a "scalar marker"
  | 'event_offset_anchor' // 1970-01-01 or +N days — event-relative offset
  | 'unknown'; // null / unparseable / empty

export interface ClassifiedDate {
  kind: ArtifactDateClass;
  /** Original raw string echoed back (when classification yields a
   *  parseable string).  Useful for debugging + tooltip detail. */
  raw?: string;
}

/** Classify an artifact-side date value into the closed family.
 *  Pure — no React, no I/O.  Pass ``hasEventOffsetEncoding: true``
 *  when the surrounding Series carries an ``index_encoding`` blob;
 *  that's the only signal that lets us treat ``1970-01-NN`` as an
 *  event-relative offset (and NOT as a real Unix-epoch date).
 *
 *  Without that context, ``1970-01-01`` could be a real (if obscure)
 *  market date, so we leave it alone — Codex's audit specifically
 *  warned against suppressing sentinels universally. */
export function classifyArtifactDate(
  value: unknown,
  context: { hasEventOffsetEncoding?: boolean } = {},
): ClassifiedDate {
  if (value === null || value === undefined) return { kind: 'unknown' };
  if (typeof value !== 'string') return { kind: 'unknown' };
  if (value === '') return { kind: 'unknown' };

  // The 1900-01-01 sentinel is documented by ``summarize_series`` as
  // semantically meaningless.  No legitimate market data lives there.
  if (value.startsWith(SUMMARY_SENTINEL_DATE)) {
    return { kind: 'summary_sentinel', raw: value };
  }

  // 1970-01-NN classification is context-dependent:
  //   - WITH ``hasEventOffsetEncoding: true`` (the surrounding Series
  //     payload carries an event_offset blob), every 1970-* date is
  //     synthetic.
  //   - WITHOUT that signal, we leave it as a regular date — the
  //     widget renders ``1970-01-01`` literally because we can't
  //     prove it's synthetic.  Defensive: that case shouldn't fire
  //     for any real workspace today.
  if (context.hasEventOffsetEncoding && value.startsWith('1970-')) {
    return { kind: 'event_offset_anchor', raw: value };
  }

  // Parseable real date — return as ``real_date``.
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return { kind: 'unknown', raw: value };
  return { kind: 'real_date', raw: value };
}

/** Read the optional ``index_encoding`` blob off a persisted Series
 *  payload.  Returns ``null`` when the payload doesn't carry one
 *  (the common case — only ``conditional_aggregate`` Series emit
 *  this today).  Pure. */
export function getEventOffsetEncoding(
  payload: SeriesPayloadEnvelope,
): { anchor: string; offsets: number[] } | null {
  const enc = payload.payload.index_encoding;
  if (!enc) return null;
  if (enc.kind !== 'event_offset') return null;
  if (!Array.isArray(enc.offsets)) return null;
  return { anchor: enc.anchor, offsets: enc.offsets };
}

/** Render an integer offset as ``t-N`` / ``t0`` / ``t+N``.  Used by
 *  the SeriesWidget when the payload's ``index_encoding`` decodes the
 *  date axis as event-relative offsets.  Mirrors the same vocabulary
 *  ``WindowedPanelWidget`` already uses. */
export function offsetLabel(offset: number): string {
  if (!Number.isFinite(offset)) return MISSING_VALUE_DASH;
  if (offset === 0) return 't0';
  if (offset > 0) return `t+${offset}`;
  return `t${offset}`; // negative already carries its sign
}

/** True when the Series payload is the one-row summary shape
 *  ``summarize_series`` emits: exactly one observation index at the
 *  ``SUMMARY_SENTINEL_DATE`` sentinel.  Drives SeriesWidget's
 *  "scalar summary" rendering mode (no sparkline, no as-of date,
 *  big-number layout).  Pure. */
export function isSentinelOneRowSeries(
  payload: SeriesPayloadEnvelope,
): boolean {
  const idx = payload.payload.index;
  if (!Array.isArray(idx) || idx.length !== 1) return false;
  return classifyArtifactDate(idx[0]).kind === 'summary_sentinel';
}

/** Pick the right label for the ``i``-th observation of a Series
 *  body.  Encapsulates the date-policy dispatch:
 *
 *    - When the payload carries an event-offset encoding, return
 *      ``offsetLabel(offsets[i])``.
 *    - When the date classifies as the summary sentinel, return
 *      ``""`` (the renderer skips the row label in that case —
 *      typically pairs with a scalar-summary rendering mode).
 *    - When the date classifies as a real market date, return the
 *      raw ISO string the renderer formats as needed.
 *    - Otherwise return ``MISSING_VALUE_DASH``.
 *
 *  Pure.  Centralising this here means a widget that needs per-row
 *  labels never has to re-implement the policy. */
export function rowLabelForSeries(
  payload: SeriesPayloadEnvelope,
  rowIndex: number,
): string {
  const enc = getEventOffsetEncoding(payload);
  if (enc !== null) {
    const offset = enc.offsets[rowIndex];
    if (typeof offset === 'number') return offsetLabel(offset);
    return MISSING_VALUE_DASH;
  }
  const raw = payload.payload.index?.[rowIndex];
  const cls = classifyArtifactDate(raw);
  if (cls.kind === 'summary_sentinel') return '';
  if (cls.kind === 'real_date') return cls.raw ?? MISSING_VALUE_DASH;
  return MISSING_VALUE_DASH;
}

// ---------------------------------------------------------------------------
// Number formatting — every numeric cell in the widgets routes here so
// the visual register stays consistent.
// ---------------------------------------------------------------------------

/** Formatting hint that drives unit suffix + decimals.  Inferred from
 *  the artifact metadata's ``units`` field (or per-column units map
 *  for Panel).  Adding a new unit family is one branch here. */
export type NumericUnitKind =
  | 'percent' // 4.32 → "4.32%"
  | 'bps' // 12.5 → "12.5 bps"
  | 'z_score' // 1.23 → "1.23σ"
  | 'count' // 1281 → "1,281"
  | 'currency' // 1234.5 → "$1,234.50"
  | 'decimal'; // 3.1415 → "3.14"

/** Coerce a backend unit string into a ``NumericUnitKind`` for
 *  formatting purposes.  Unknown units route through ``'decimal'``
 *  rather than throwing — keeps the widget body resilient to new
 *  unit strings the backend may add later. */
export function unitKind(units: string | null | undefined): NumericUnitKind {
  if (!units) return 'decimal';
  const u = units.toLowerCase();
  if (u === 'percent' || u === '%' || u === 'pct' || u === 'yield_pct')
    return 'percent';
  if (u === 'bps' || u === 'basis_points') return 'bps';
  if (u === 'z_score' || u === 'zscore') return 'z_score';
  if (u === 'count' || u === 'integer' || u === 'int') return 'count';
  if (u === 'usd' || u === 'currency') return 'currency';
  return 'decimal';
}

export interface FormatNumberOptions {
  /** Override the inferred unit kind.  When omitted, falls through to
   *  ``unitKind(units)`` then ``'decimal'``. */
  unit?: NumericUnitKind;
  /** Decimal places.  Default depends on the unit kind:
   *   percent / decimal → 2
   *   bps               → 1 (single-bp precision is more than enough)
   *   z_score           → 2
   *   count             → 0
   *   currency          → 2
   */
  decimals?: number;
  /** Whether to render the unit suffix.  Default true; the suffix is
   *  the most visible cue that the renderer didn't fall through to a
   *  generic decimal formatter. */
  withUnit?: boolean;
}

const _DEFAULT_DECIMALS: Record<NumericUnitKind, number> = {
  percent: 2,
  bps: 1,
  z_score: 2,
  count: 0,
  currency: 2,
  decimal: 2,
};

/** Format a numeric scalar with the right unit suffix.  Returns
 *  ``MISSING_VALUE_DASH`` for null / undefined / non-finite input. */
export function formatNumber(
  value: unknown,
  options: FormatNumberOptions = {},
): string {
  if (value === null || value === undefined) return MISSING_VALUE_DASH;
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return MISSING_VALUE_DASH;
  }
  const unit = options.unit ?? 'decimal';
  const decimals = options.decimals ?? _DEFAULT_DECIMALS[unit];
  const withUnit = options.withUnit ?? true;
  const formatted = value.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  if (!withUnit) return formatted;
  switch (unit) {
    case 'percent':
      return `${formatted}%`;
    case 'bps':
      return `${formatted} bps`;
    case 'z_score':
      return `${formatted}σ`;
    case 'currency':
      return `$${formatted}`;
    case 'count':
      return formatted;
    case 'decimal':
    default:
      return formatted;
  }
}

/** Convenience wrapper that picks the unit kind from the artifact's
 *  ``units`` metadata string and forwards to ``formatNumber``. */
export function formatNumberWithUnits(
  value: unknown,
  units: string | null | undefined,
  options: Omit<FormatNumberOptions, 'unit'> = {},
): string {
  return formatNumber(value, { ...options, unit: unitKind(units) });
}

// ---------------------------------------------------------------------------
// Payload-shape pickers — read-only inspections of the typed
// envelopes.  Each helper returns a small JSON-safe value the widgets
// drop straight into the DOM; widgets stay focused on layout.
// ---------------------------------------------------------------------------

/** Count of events in an EventSet payload.  Replaces the PR1-era
 *  bug where widgets used ``artifact.row_count`` (which is the mask
 *  LENGTH, not the count of true entries). */
export function eventCountFromPayload(
  payload: EventSetPayloadEnvelope,
): number {
  const dates = payload.payload.event_dates;
  return Array.isArray(dates) ? dates.length : 0;
}

/** Total observations in a Series — defensive against missing /
 *  malformed payload bodies. */
export function seriesObservationCount(
  payload: SeriesPayloadEnvelope,
): number {
  const idx = payload.payload.index;
  return Array.isArray(idx) ? idx.length : 0;
}

/** Count of non-null observations in a Series — useful for "N
 *  finite observations" sub-labels. */
export function seriesFiniteCount(payload: SeriesPayloadEnvelope): number {
  const vs = payload.payload.values;
  if (!Array.isArray(vs)) return 0;
  let n = 0;
  for (const v of vs) {
    if (v !== null && v !== undefined && Number.isFinite(v)) n += 1;
  }
  return n;
}

/** Most recent non-null observation in a Series, returned as
 *  ``{date, value}`` for ergonomic destructuring.  Returns ``null``
 *  when the series is empty or all nulls.  Walks from the end so the
 *  common case (series ends with a real value) is O(1). */
export function lastSeriesObservation(
  payload: SeriesPayloadEnvelope,
): { date: string; value: number } | null {
  const idx = payload.payload.index ?? [];
  const vs = payload.payload.values ?? [];
  for (let i = vs.length - 1; i >= 0; i--) {
    const v = vs[i];
    if (v !== null && v !== undefined && Number.isFinite(v)) {
      return { date: idx[i] ?? '', value: v };
    }
  }
  return null;
}

/** Earliest non-null observation in a Series — mirror of
 *  ``lastSeriesObservation``.  Returns ``null`` for empty / all-null. */
export function firstSeriesObservation(
  payload: SeriesPayloadEnvelope,
): { date: string; value: number } | null {
  const idx = payload.payload.index ?? [];
  const vs = payload.payload.values ?? [];
  for (let i = 0; i < vs.length; i++) {
    const v = vs[i];
    if (v !== null && v !== undefined && Number.isFinite(v)) {
      return { date: idx[i] ?? '', value: v };
    }
  }
  return null;
}

/** Member-series keys of a SeriesSet, in insertion order.  Returns
 *  an empty array for malformed payloads so callers can rely on
 *  ``.length``. */
export function seriesSetMembers(
  payload: SeriesSetPayloadEnvelope,
): string[] {
  const src = payload.payload.series_by_key ?? {};
  if (!src || typeof src !== 'object') return [];
  return Object.keys(src);
}

/** Convenience: extract one member of a SeriesSet as a synthetic
 *  ``SeriesPayloadEnvelope`` so widgets can reuse the same Series-
 *  rendering primitives for SeriesSet member previews.  Returns
 *  ``null`` when the requested key isn't in the payload. */
export function seriesSetMemberAsSeries(
  payload: SeriesSetPayloadEnvelope,
  memberKey: string,
): SeriesPayloadEnvelope | null {
  const values = payload.payload.series_by_key?.[memberKey];
  if (!Array.isArray(values)) return null;
  const units = payload.metadata.units_by_key?.[memberKey] ?? '';
  return {
    artifact_type: 'Series',
    metadata: {
      series_key: memberKey,
      units,
      frequency: payload.metadata.frequency ?? null,
      // The substrate's SeriesSet wire format doesn't include a
      // per-member missingness policy on the lookup path; reuse the
      // set-wide policy if it exposed one (it doesn't in V1) or fall
      // back to an empty object.  Either way the renderer doesn't
      // depend on it for first-pass display.
      missingness_policy: payload.metadata.missingness_by_key?.[memberKey] ?? {},
      lineage:
        payload.metadata.upstream_lineage_by_key?.[memberKey] ??
        payload.metadata.lineage,
    },
    payload: {
      index: payload.payload.common_index ?? [],
      values,
      name: memberKey,
      type: 'Series',
    },
  };
}

/** Column labels of a Panel payload, in insertion order. */
export function panelColumns(payload: PanelPayloadEnvelope): string[] {
  return Array.isArray(payload.payload.columns)
    ? payload.payload.columns
    : [];
}

/** Row index of a Panel payload, in insertion order. */
export function panelRowIndex(payload: PanelPayloadEnvelope): string[] {
  return Array.isArray(payload.payload.index) ? payload.payload.index : [];
}

/** Cell value at ``(rowIdx, columnIdx)``.  Returns ``null`` for any
 *  out-of-bounds access OR missing payload structure — callers don't
 *  need to do bounds-checks themselves. */
export function panelCell(
  payload: PanelPayloadEnvelope,
  rowIdx: number,
  columnIdx: number,
): number | null {
  const row = payload.payload.data?.[rowIdx];
  if (!Array.isArray(row)) return null;
  const cell = row[columnIdx];
  if (cell === undefined) return null;
  return cell;
}

/** Number of events in a WindowedPanel.  ``event_dates`` is the
 *  authoritative count (one event per row in the 2-D matrix). */
export function windowedEventCount(
  payload: WindowedPanelPayloadEnvelope,
): number {
  return Array.isArray(payload.payload.event_dates)
    ? payload.payload.event_dates.length
    : 0;
}

/** Offset axis of a WindowedPanel — integer offsets relative to each
 *  event date (e.g. ``[-5, -4, …, +4, +5]``). */
export function windowedOffsets(
  payload: WindowedPanelPayloadEnvelope,
): number[] {
  return Array.isArray(payload.metadata.offsets)
    ? payload.metadata.offsets
    : [];
}

/** Tradesheet row count.  Returns 0 for malformed payloads so the
 *  widget's "no trades" branch fires cleanly. */
export function tradeRowCount(payload: TradeSetPayloadEnvelope): number {
  const trades = payload.payload.trades;
  return Array.isArray(trades) ? trades.length : 0;
}

// ---------------------------------------------------------------------------
// Discriminator type guards — re-exported here for widget ergonomics.
// (The types module exports them as well; importing from this file
// avoids two distinct import statements in widget code.)
// ---------------------------------------------------------------------------

export type {
  ArtifactPayloadResponse,
  EventSetPayloadEnvelope,
  PanelPayloadEnvelope,
  ScalarMetricPayloadEnvelope,
  SeriesPayloadEnvelope,
  SeriesSetPayloadEnvelope,
  TradeSetPayloadEnvelope,
  WindowedPanelPayloadEnvelope,
};
