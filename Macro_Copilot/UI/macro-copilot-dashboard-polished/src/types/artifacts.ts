// ============================================================================
// types/artifacts.ts — wire-shape types for ``GET /api/v1/artifacts/{hash}/payload``
// ----------------------------------------------------------------------------
// PR3 — frontend artifact payload client.  Mirrors the backend's
// ``StoredArtifact`` Pydantic model + the per-artifact-type
// serialisation helpers in ``state/artifact_store.py``.
//
// Source of truth (do NOT diverge):
//   - api/routes/artifacts.py::ArtifactPayloadResponse  (the envelope)
//   - state/schemas.py::ArtifactTypeLiteral             (the closed family)
//   - state/artifact_store.py::_series_to_stored, _series_set_to_stored,
//     _event_set_to_stored, _panel_to_stored,
//     _windowed_panel_to_stored, _trade_set_to_stored
//     (per-type metadata + payload field maps)
//
// Discipline
// ----------
// Every per-type body is typed as ``{...known fields...} & Record<string, unknown>``
// so consumers can read the well-known fields with full inference while
// the catch-all index signature absorbs future additions without a
// TypeScript break.  Consumers SHOULD NOT depend on the index-signature
// path for new fields — extend this file when the backend adds one.
//
// What this file is NOT
// ---------------------
// Not a renderer.  Not a hook.  Not a cache.  Pure shape declarations.
// Bundlers should be able to tree-shake this file to its used types
// without pulling in any runtime code.
// ============================================================================

// ---------------------------------------------------------------------------
// Discriminator — closed set of backend artifact type strings.
// Mirrors ``state.schemas.ArtifactTypeLiteral``.  Adding an entry here
// without adding the matching backend type is a wire mismatch.
// ---------------------------------------------------------------------------

export type ArtifactType =
  | 'Series'
  | 'SeriesSet'
  | 'EventSet'
  | 'Panel'
  | 'WindowedPanel'
  | 'ScalarMetric'   // PR-11B / v2.0 (ADR 0016) — active in backend
  | 'TradeSet';      // KEEP DORMANT — paused backtest surfaces still
                     //                import it; PR-11 explicitly does
                     //                NOT delete dormant TradeSet refs.
                     //                Backend removed it per OPR6 (v2.0);
                     //                pruning lands alongside backtest
                     //                re-admission as a primitive set.

// ---------------------------------------------------------------------------
// Common shapes — repeated across the per-type bodies.
// ---------------------------------------------------------------------------

/** A single lineage step recorded by the substrate at execution time.
 *  Mirrors ``shared.lineage.LineageStep``: a stable structural shape
 *  the wire dumps via ``model_dump(mode="json")``. */
export interface LineageStep {
  name: string;
  /** Step input params — closed dict of JSON-safe scalars by the
   *  substrate's discipline.  Untyped at this layer because each
   *  primitive defines its own step shape. */
  params: Record<string, unknown>;
  /** Closed-set tag enriching the step (e.g. ``primitive``,
   *  ``operator``, ``terminal``).  Optional because some legacy steps
   *  pre-date the field. */
  kind?: string | null;
}

export interface Lineage {
  steps: LineageStep[];
  /** Optional human label set by some operators.  Most steps don't
   *  emit one; consumers should fall back to ``steps[-1].name``. */
  label?: string | null;
}

/** Missingness policy serialised onto the artifact metadata.  Shape
 *  follows ``shared.missingness.MissingnessPolicy.model_dump``. */
export interface MissingnessPolicy {
  fill_method?: string | null;
  fill_value?: number | null;
  // Open-ended — every primitive's policy variant decides its own
  // optional knobs.
  [extra: string]: unknown;
}

/** R5.1 event-offset encoding — promoted onto a Series payload when
 *  the artifact was produced by ``conditional_aggregate``.  Lets the
 *  UI label the index as "Day N" rather than the misleading 1970-
 *  anchored dates the wire format uses for these series. */
export interface IndexEncoding {
  kind: 'event_offset';
  /** ISO-8601 anchor date the offsets are stored against. */
  anchor: string;
  /** Integer offsets (e.g. ``[0, 1, 2, 3, 4, 5]`` for a 5-day forward
   *  window).  Same length as the index slice the UI walks. */
  offsets: number[];
}

// ---------------------------------------------------------------------------
// Series — single pd.Series serialisation.
//   _series_to_stored: {index, values, name, type:"Series"} + optional
//                       index_encoding for event-relative offsets.
// ---------------------------------------------------------------------------

export interface SeriesMetadata {
  series_key: string;
  units: string;
  frequency?: string | null;
  missingness_policy: MissingnessPolicy;
  lineage: Lineage;
}

export interface SeriesPayloadBody {
  /** ISO-8601 date strings — one per observation. */
  index: string[];
  /** Float values — same length as ``index``.  Wire format may carry
   *  ``null`` for masked / missing observations; consumers should
   *  treat null as NaN at render time. */
  values: Array<number | null>;
  name: string;
  type: 'Series';
  /** Present only for ``conditional_aggregate``-emitted Series. */
  index_encoding?: IndexEncoding;
  // Forward-compat for any payload fields the backend adds later.
  [extra: string]: unknown;
}

export interface SeriesPayloadEnvelope {
  artifact_type: 'Series';
  metadata: SeriesMetadata;
  payload: SeriesPayloadBody;
}

// ---------------------------------------------------------------------------
// SeriesSet — many aligned pd.Series on a common index.
//   _series_set_to_stored: {common_index, series_by_key}
// ---------------------------------------------------------------------------

export interface SeriesSetMetadata {
  units_by_key: Record<string, string>;
  missingness_by_key: Record<string, MissingnessPolicy>;
  upstream_lineage_by_key: Record<string, Lineage>;
  frequency?: string | null;
  lineage: Lineage;
}

export interface SeriesSetPayloadBody {
  /** ISO-8601 date strings — common index for every member series. */
  common_index: string[];
  /** Each value array aligns row-for-row with ``common_index``.
   *  Nulls represent masked / missing observations. */
  series_by_key: Record<string, Array<number | null>>;
  [extra: string]: unknown;
}

export interface SeriesSetPayloadEnvelope {
  artifact_type: 'SeriesSet';
  metadata: SeriesSetMetadata;
  payload: SeriesSetPayloadBody;
}

// ---------------------------------------------------------------------------
// EventSet — a boolean mask over time with event-aware metadata.
//   _event_set_to_stored: {mask_index, mask_values, event_dates,
//                          per_event_metadata}
// ---------------------------------------------------------------------------

export interface EventSetMetadata {
  source_series_key: string;
  frequency?: string | null;
  lineage: Lineage;
}

export interface EventSetPayloadBody {
  /** ISO-8601 date strings — one entry per observation in the mask. */
  mask_index: string[];
  /** Boolean values aligned with ``mask_index``; true = event day. */
  mask_values: boolean[];
  /** ISO-8601 date strings — the subset of ``mask_index`` where
   *  ``mask_values`` is true.  Backend invariant: same length as the
   *  count of true entries in ``mask_values``. */
  event_dates: string[];
  /** Per-event auxiliary data.  Shape varies by primitive; consumers
   *  should treat as opaque pass-through. */
  per_event_metadata: Array<Record<string, unknown>>;
  [extra: string]: unknown;
}

export interface EventSetPayloadEnvelope {
  artifact_type: 'EventSet';
  metadata: EventSetMetadata;
  payload: EventSetPayloadBody;
}

// ---------------------------------------------------------------------------
// Panel — wide DataFrame (index × columns) of floats.
//   _panel_to_stored: {index, columns, data}
// ---------------------------------------------------------------------------

export interface PanelMetadata {
  units_by_column: Record<string, string>;
  missingness_policy: MissingnessPolicy;
  lineage: Lineage;
}

export interface PanelPayloadBody {
  /** ISO-8601 date strings — row index of the panel. */
  index: string[];
  /** Column labels in the order ``data`` row entries are stored. */
  columns: string[];
  /** 2-D matrix — ``data[i][j]`` is the panel cell at
   *  ``(index[i], columns[j])``.  Nulls represent missing cells. */
  data: Array<Array<number | null>>;
  [extra: string]: unknown;
}

export interface PanelPayloadEnvelope {
  artifact_type: 'Panel';
  metadata: PanelMetadata;
  payload: PanelPayloadBody;
}

// ---------------------------------------------------------------------------
// WindowedPanel — per-event 2-D matrix (offsets × events).
//   _windowed_panel_to_stored: {data, event_dates, per_event_metadata}
//   with metadata.offsets carrying the column axis.
// ---------------------------------------------------------------------------

export interface WindowedPanelMetadata {
  /** Integer offsets relative to each event date (e.g. ``[-5, -4, ...,
   *  +4, +5]`` for a symmetric +/- 5-day window). */
  offsets: number[];
  target_series_key: string;
  units: string;
  lineage: Lineage;
}

export interface WindowedPanelPayloadBody {
  /** 2-D float matrix; ``data[event_idx][offset_idx]`` is the value at
   *  ``event_dates[event_idx] + offsets[offset_idx]``.  Nulls are
   *  acceptable for windows extending past the underlying series. */
  data: Array<Array<number | null>>;
  /** ISO-8601 date strings — same length as ``data.length``. */
  event_dates: string[];
  /** Per-event auxiliary metadata; shape varies by primitive. */
  per_event_metadata: Array<Record<string, unknown>>;
  [extra: string]: unknown;
}

export interface WindowedPanelPayloadEnvelope {
  artifact_type: 'WindowedPanel';
  metadata: WindowedPanelMetadata;
  payload: WindowedPanelPayloadBody;
}

// ---------------------------------------------------------------------------
// ScalarMetric — a single finite scalar statistic (PR-11B / v2.0 ART4/ART5).
//   Backend Pydantic shape (shared/artifacts/types.py:420):
//     metric_key: str
//     value: float (finite — ±Inf/NaN rejected at construction per ART11)
//     units: TimeSeriesUnits (e.g. "RATIO" for correlation)
//     lineage: Lineage
// ---------------------------------------------------------------------------

export interface ScalarMetricMetadata {
  /** Operator-supplied identifier (e.g. "correlation_coefficient").
   *  Load-bearing — frontend cards key chip labels off this field. */
  metric_key: string;
  /** TimeSeriesUnits — RATIO for correlation, BPS / PERCENT / Z_SCORE etc.
   *  for other statistical operators.  String here because the closed
   *  family lives on the backend (shared/schemas/time_series.py); the
   *  frontend treats it as an opaque short label and formats by it. */
  units: string;
  lineage: Lineage;
}

export interface ScalarMetricPayloadBody {
  /** Single finite number.  Backend invariant: ART11 forbids ±Inf and NaN
   *  on the wire — the operator raises a typed error rather than emitting
   *  a non-finite ScalarMetric. */
  value: number;
  /** Forward-compat catch-all for any payload fields the backend adds. */
  [extra: string]: unknown;
}

export interface ScalarMetricPayloadEnvelope {
  artifact_type: 'ScalarMetric';
  metadata: ScalarMetricMetadata;
  payload: ScalarMetricPayloadBody;
}

// ---------------------------------------------------------------------------
// TradeSet — flattened trade-record list.
//   _trade_set_to_stored: {trades: TradeSet.to_records()}
// ---------------------------------------------------------------------------

/** One row of a TradeSet payload.  Mirrors ``TradeSet.to_records()``
 *  output: ISO timestamps + JSON-safe scalars.  Field set varies
 *  slightly by backtest variant; consumers should treat extra keys
 *  as additive. */
export interface TradeRecord {
  entry_date: string;
  exit_date?: string | null;
  entry_value?: number | null;
  exit_value?: number | null;
  pnl?: number | null;
  return_pct?: number | null;
  // Open-ended — each backtest archetype may add legs, weights,
  // signal_value, etc.  Untyped here because the shape isn't closed.
  [extra: string]: unknown;
}

export interface TradeSetMetadata {
  source_event_key?: string | null;
  /** Backend's ``methodology_policy`` — the closed-vocabulary tag
   *  declaring how trades were assembled (e.g. ``conservative``,
   *  ``permissive``).  String for now; tighten if/when the substrate
   *  publishes a Literal type. */
  methodology_policy: string;
  lineage: Lineage;
}

export interface TradeSetPayloadBody {
  trades: TradeRecord[];
  [extra: string]: unknown;
}

export interface TradeSetPayloadEnvelope {
  artifact_type: 'TradeSet';
  metadata: TradeSetMetadata;
  payload: TradeSetPayloadBody;
}

// ---------------------------------------------------------------------------
// Discriminated union — the actual response shape.
// ---------------------------------------------------------------------------

/** Discriminated union over ``artifact_type``.  Consumers MUST switch
 *  on ``response.artifact_type`` (or use a helper like
 *  ``isSeriesPayload``) before reading typed metadata / payload fields.
 *
 *  PR-11B: ``ScalarMetricPayloadEnvelope`` added (v2.0 ART4/ART5 — the
 *  backend admitted ``ScalarMetric`` for statistical operators like
 *  correlation/covariance/cointegration).  ``TradeSetPayloadEnvelope``
 *  kept DORMANT — paused backtest surfaces still import it and PR-11
 *  explicitly does not delete dormant TradeSet references.  Pruning
 *  lands alongside backtest re-admission. */
export type ArtifactPayloadResponse =
  | SeriesPayloadEnvelope
  | SeriesSetPayloadEnvelope
  | EventSetPayloadEnvelope
  | PanelPayloadEnvelope
  | WindowedPanelPayloadEnvelope
  | ScalarMetricPayloadEnvelope
  | TradeSetPayloadEnvelope;

// ---------------------------------------------------------------------------
// Type guards — small, exhaustive helpers so consumers don't sprinkle
// raw equality checks across the codebase.
// ---------------------------------------------------------------------------

export function isSeriesPayload(
  r: ArtifactPayloadResponse,
): r is SeriesPayloadEnvelope {
  return r.artifact_type === 'Series';
}

export function isSeriesSetPayload(
  r: ArtifactPayloadResponse,
): r is SeriesSetPayloadEnvelope {
  return r.artifact_type === 'SeriesSet';
}

export function isEventSetPayload(
  r: ArtifactPayloadResponse,
): r is EventSetPayloadEnvelope {
  return r.artifact_type === 'EventSet';
}

export function isPanelPayload(
  r: ArtifactPayloadResponse,
): r is PanelPayloadEnvelope {
  return r.artifact_type === 'Panel';
}

export function isWindowedPanelPayload(
  r: ArtifactPayloadResponse,
): r is WindowedPanelPayloadEnvelope {
  return r.artifact_type === 'WindowedPanel';
}

export function isTradeSetPayload(
  r: ArtifactPayloadResponse,
): r is TradeSetPayloadEnvelope {
  return r.artifact_type === 'TradeSet';
}

export function isScalarMetricPayload(
  r: ArtifactPayloadResponse,
): r is ScalarMetricPayloadEnvelope {
  return r.artifact_type === 'ScalarMetric';
}

/** Validate a JSON-decoded object looks like an ArtifactPayloadResponse.
 *  Pure structural check — does NOT verify the per-type body fields.
 *  Used by the API client / hook to fail fast on a malformed response
 *  rather than letting the discriminated-union narrowing silently
 *  fall through to undefined behaviour. */
export function isArtifactPayloadResponseShape(
  v: unknown,
): v is ArtifactPayloadResponse {
  if (!v || typeof v !== 'object') return false;
  const r = v as Record<string, unknown>;
  if (typeof r.artifact_type !== 'string') return false;
  if (!isKnownArtifactType(r.artifact_type)) return false;
  if (!r.metadata || typeof r.metadata !== 'object') return false;
  if (!r.payload || typeof r.payload !== 'object') return false;
  return true;
}

export function isKnownArtifactType(s: string): s is ArtifactType {
  return (
    s === 'Series' ||
    s === 'SeriesSet' ||
    s === 'EventSet' ||
    s === 'Panel' ||
    s === 'WindowedPanel' ||
    s === 'ScalarMetric' || // PR-11B / v2.0 ART4/ART5
    s === 'TradeSet'        // KEEP DORMANT — see ArtifactType comment
  );
}
