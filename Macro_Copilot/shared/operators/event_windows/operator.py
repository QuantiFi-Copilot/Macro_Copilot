"""event_windows — extract a WindowedPanel from an EventSet + target Series.

Per the operator architecture doc, this module owns the ``windowing``
structural method family.  Finance-blind in general, but with one
explicit unit transition: ``units_basis="level_change"`` on a PERCENT
target emits a BPS panel (×100).  The operator owns this transition
by contract because the level-change *is* a methodology-driven unit
change, not arithmetic — it cannot live in strict
``series_arithmetic`` without coupling unit algebra to op semantics.

Design summary
--------------
1. Resolve config-defaulted fields when caller omitted them
   (``incomplete_window_policy`` and ``units_basis`` default to None
   in the schema; YAML is authoritative — same pattern as
   ``threshold_events`` after PR #53).
2. Structural-metadata compatibility:
   - ``events.mask.index == target.payload.index`` (caller aligns
     first; refusal is a controlled error directing them to do so).
   - frequency-tag agreement when ``require_matching_frequency``
     (events sourced from one frequency-tagged series, then matched
     against a differently-tagged target, is the canonical hazard
     this check exists to surface).
3. Build the offsets vector ``[-pre_window, ..., post_window]``,
   skipping offset 0 when ``inclusive_event_day=False``.
4. For each event date, extract the slice; under ``drop`` policy
   skip events whose window doesn't fit; under ``pad_nan`` keep
   them with NaN tails.
5. When ``units_basis="level_change"``, subtract the event-day value
   and convert PERCENT → BPS by multiplying by 100; record the
   transition explicitly in lineage.
6. Compute overlap stats (number of pairs of events whose windows
   overlap on the calendar) and record in lineage params.
7. Build ``WindowedPanel`` with full lineage: events.lineage as the
   primary chain (head_hash flows into the operator step's
   input_hashes), and target.lineage stored on the operator step's
   ``auxiliary_lineages`` so the methodology summary can walk both
   inputs back to fetch.

Errors
------
Operators in ``shared.operators.*`` raise typed exceptions on user-
facing failures.  The orchestration / template / public-tool layer
converts to ``{"error": "..."}`` envelopes at the user boundary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from shared.artifacts.lineage import Lineage, OperatorStep
from shared.artifacts.types import EventSet, Series, WindowedPanel
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    load_operator_config,
)
from shared.operators.event_windows.schemas import (
    EventWindowsParams,
    IncompleteWindowPolicy,
    UnitsBasis,
)


_OPERATOR_NAME = "event_windows"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class EventWindowsError(ValueError):
    """Raised by ``event_windows`` on a recoverable user-facing failure.

    Subclass of ``ValueError`` so existing ``except ValueError`` blocks
    in the rest of ``shared/`` continue to work.  Templates and public
    wrappers convert this into the controlled error envelope at the
    user-facing boundary.
    """


def event_windows(
    events: EventSet,
    target: Series,
    params: Optional[EventWindowsParams] = None,
    config: Optional[OperatorConfig] = None,
) -> WindowedPanel:
    """Extract event-relative windows of ``target`` around each event in
    ``events``.

    Parameters
    ----------
    events :
        ``EventSet`` artifact whose mask is the True/False indicator
        for event days.  ``events.mask.index`` MUST equal
        ``target.payload.index``.  Use ``align_series`` upstream to
        guarantee this.
    target :
        ``Series`` artifact whose response we measure across each
        window.  Typically a different series than the one events
        were detected on (e.g., events from a swap-spread, target =
        10Y UST yield).  May also be the same series — the operator
        does not require otherwise.
    params :
        Optional ``EventWindowsParams``.  Of the schema fields, ONLY
        ``incomplete_window_policy`` and ``units_basis`` are
        YAML-authoritative — when they are ``None`` the operator
        resolves them from ``config.default_value(...)`` at runtime
        (same pattern as ``threshold_events`` post PR #53).  The
        other fields (``pre_window``, ``post_window``,
        ``inclusive_event_day``, ``require_matching_frequency``)
        come from schema defaults.
    config :
        Optional ``OperatorConfig``.  When omitted, the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    WindowedPanel
        Shape ``[n_events, window_length]``; offsets list event-
        relative day indices.  Units come from ``units_basis``
        resolution (raw → target.units; level_change on a PERCENT
        target → BPS).  Lineage chains events.lineage with the new
        operator step; the operator step's ``auxiliary_lineages``
        carries target.lineage.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    if not isinstance(config, OperatorConfig):
        raise OperatorConfigError(
            f"event_windows: 'config' must be OperatorConfig; "
            f"got {type(config).__name__}."
        )
    if config.operator.name != _OPERATOR_NAME:
        raise OperatorConfigError(
            f"event_windows: config name mismatch — expected "
            f"{_OPERATOR_NAME!r}, got {config.operator.name!r}."
        )

    # ------------------------------------------------------------------
    # 2. Resolve config-defaulted fields when caller omitted them
    #    (YAML-authority pattern — same as threshold_events post PR #53).
    # ------------------------------------------------------------------
    if params is None:
        params = EventWindowsParams()

    needs_resolution = (
        params.incomplete_window_policy is None
        or params.units_basis is None
    )
    if needs_resolution:
        resolved_iwp = (
            params.incomplete_window_policy
            if params.incomplete_window_policy is not None
            else config.default_value("incomplete_window_policy")
        )
        resolved_units_basis = (
            params.units_basis
            if params.units_basis is not None
            else config.default_value("units_basis")
        )
        params = EventWindowsParams(
            pre_window=params.pre_window,
            post_window=params.post_window,
            inclusive_event_day=params.inclusive_event_day,
            incomplete_window_policy=resolved_iwp,
            units_basis=resolved_units_basis,
            require_matching_frequency=params.require_matching_frequency,
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation.
    # ------------------------------------------------------------------
    if not isinstance(events, EventSet):
        raise EventWindowsError(
            f"event_windows: events must be an EventSet artifact; "
            f"got {type(events).__name__}."
        )
    if not isinstance(target, Series):
        raise EventWindowsError(
            f"event_windows: target must be a Series artifact; "
            f"got {type(target).__name__}."
        )

    if not events.mask.index.equals(target.payload.index):
        raise EventWindowsError(
            "event_windows: events.mask.index and target.payload.index "
            "must be identical.  Use ``align_series`` upstream so the "
            "events and target share the same trading-day grid.  "
            f"events_len={len(events.mask.index)}, "
            f"target_len={len(target.payload.index)}."
        )

    # Frequency-tag compatibility (Codex P1 follow-up on PR #54).
    # ``EventSet`` carries a ``frequency`` propagated from the source
    # Series in ``threshold_events``; here we enforce it against
    # ``target.frequency`` when the caller wants the strict check.
    # Strict mode (default) raises on mismatch; lenient mode passes
    # and records the choice in lineage params.
    # Cases:
    #   - both None        → strict pass (no information either way)
    #   - both equal       → strict pass
    #   - different values → strict raise; lenient accept
    #   - one None / other concrete (partial tagging) → strict raise;
    #     lenient accept.  Same discipline as align_series — partial
    #     metadata almost always means the caller forgot to tag one
    #     side.
    if params.require_matching_frequency:
        if events.frequency != target.frequency:
            raise EventWindowsError(
                f"event_windows: incompatible frequencies "
                f"events={events.frequency!r} vs "
                f"target={target.frequency!r}.  Pass "
                "require_matching_frequency=False to opt into "
                "mixed-frequency windowing explicitly."
            )

    # ------------------------------------------------------------------
    # 4. Build offsets vector.
    # ------------------------------------------------------------------
    offsets: List[int] = []
    for k in range(-params.pre_window, params.post_window + 1):
        if k == 0 and not params.inclusive_event_day:
            continue
        offsets.append(k)

    # ------------------------------------------------------------------
    # 5. Extract per-event windows.
    # ------------------------------------------------------------------
    panel_index = target.payload.index
    n_target = len(panel_index)
    target_values = target.payload.to_numpy(dtype=float)

    kept_event_dates: List[pd.Timestamp] = []
    kept_event_metadata: List[Dict[str, Any]] = []
    rows: List[np.ndarray] = []
    # Codex P2 follow-up: split drop reasons.  Previously a single
    # ``n_dropped_for_edge`` counter incorrectly attributed
    # NaN-event-day drops to edge effects, making the lineage
    # metadata dishonest.
    n_dropped_for_edge = 0
    n_dropped_for_nan_event_day = 0

    # Map event date → integer position in target index (lookup is
    # O(log n) via searchsorted / Index.get_loc; the index has been
    # validated unique + monotonic at Series construction time).
    for ev_idx, ev_date in enumerate(events.event_dates):
        try:
            t_e = panel_index.get_loc(ev_date)
        except KeyError:
            # Should be unreachable if events.mask.index == panel_index,
            # but defensive:
            raise EventWindowsError(
                f"event_windows: event date {ev_date} not found in "
                "target index — internal invariant violated."
            )

        first = t_e - params.pre_window
        last = t_e + params.post_window
        # last index is INCLUSIVE; full slice end is last+1.

        edge_underflow = first < 0
        edge_overflow = last >= n_target

        if edge_underflow or edge_overflow:
            if params.incomplete_window_policy == "drop":
                n_dropped_for_edge += 1
                continue
            # else: pad_nan — build the row with NaN where the slice
            # spills off the edge.

        # Build the row.  ``raw_window`` contains target values at
        # the offsets we want (NaN for off-edge cells under pad_nan).
        raw_row = np.full(len(offsets), np.nan, dtype=float)
        for col_idx, k in enumerate(offsets):
            t_k = t_e + k
            if 0 <= t_k < n_target:
                raw_row[col_idx] = target_values[t_k]

        # Apply units_basis transition.
        if params.units_basis == "level_change":
            event_day_value = target_values[t_e]
            if not np.isfinite(event_day_value):
                # Cannot subtract from NaN; under drop policy we skip
                # (charged to the NaN-event-day counter, NOT the edge
                # counter), under pad_nan we leave the row all-NaN.
                if params.incomplete_window_policy == "drop":
                    n_dropped_for_nan_event_day += 1
                    continue
                level_change_row = np.full_like(raw_row, np.nan)
            else:
                level_change_row = raw_row - event_day_value
                # PERCENT → BPS: multiply by 100.  Other unit types
                # are *unit-preserving* under level_change (e.g.,
                # BPS - BPS stays BPS, RATIO - RATIO stays RATIO).
                if target.units == TimeSeriesUnits.PERCENT:
                    level_change_row = level_change_row * 100.0
            row = level_change_row
        else:
            # raw basis: cells are target values as-is.
            row = raw_row

        rows.append(row)
        kept_event_dates.append(ev_date)
        # Carry forward the source event's per-event metadata + add
        # window-specific context.
        src_meta = events.per_event_metadata[ev_idx]
        kept_event_metadata.append({
            **src_meta,
            "event_relative_offsets": offsets,
            "event_day_value": float(target_values[t_e])
                if np.isfinite(target_values[t_e]) else None,
        })

    # ------------------------------------------------------------------
    # 6. Resolve output units + assemble payload.
    # ------------------------------------------------------------------
    output_units = _resolve_output_units(target.units, params.units_basis)

    if rows:
        payload = np.vstack(rows)
    else:
        payload = np.empty((0, len(offsets)), dtype=float)

    # ------------------------------------------------------------------
    # 7. Compute overlap stats — # of overlapping pairs of kept events.
    # ------------------------------------------------------------------
    overlap_pairs = _count_overlapping_pairs(
        kept_event_dates, panel_index,
        pre=params.pre_window, post=params.post_window,
    )

    # ------------------------------------------------------------------
    # 8. Build the operator step + assemble lineage.
    # ------------------------------------------------------------------
    op_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params={
            "pre_window": params.pre_window,
            "post_window": params.post_window,
            "inclusive_event_day": params.inclusive_event_day,
            "incomplete_window_policy": params.incomplete_window_policy,
            "units_basis": params.units_basis,
            "require_matching_frequency": params.require_matching_frequency,
            "target_series_key": target.series_key,
            "input_units": target.units.value,
            "output_units": output_units.value,
            "n_events_in": events.n_events,
            "n_events_kept": len(kept_event_dates),
            "n_events_dropped_for_edge": n_dropped_for_edge,
            "n_events_dropped_for_nan_event_day": n_dropped_for_nan_event_day,
            "overlap_pairs_in_kept_events": overlap_pairs,
            "window_length": len(offsets),
        },
        input_hashes=(events.lineage.head_hash, target.lineage.head_hash),
        # Per the binary-provenance contract from PR #51:
        # primary input chain (events) becomes the head's lineage;
        # secondary input chain (target) lives on the step.
        auxiliary_lineages=(target.lineage,),
    )
    lineage = events.lineage.append(op_step)

    return WindowedPanel(
        payload=payload,
        offsets=offsets,
        event_dates=kept_event_dates,
        per_event_metadata=kept_event_metadata,
        target_series_key=target.series_key,
        units=output_units,
        lineage=lineage,
    )


# ============================================================================
# UNIT TRANSITION
# ============================================================================


def _resolve_output_units(
    input_units: TimeSeriesUnits,
    units_basis: UnitsBasis,
) -> TimeSeriesUnits:
    """Resolve the output panel's units per the units_basis contract.

    Phase 1A semantics:

      - ``raw``           → output units = input units.
      - ``level_change``  → output units = BPS when input is PERCENT
                            (multiply by 100, methodology-driven
                            transition); otherwise input units
                            preserved (BPS - BPS = BPS, RATIO - RATIO
                            = RATIO, etc.).

    The transition is owned by this operator (not by series_arithmetic)
    because computing a level-change *is* the methodology choice — it
    is not algebraically equivalent to subtraction at the unit level
    (a `series_arithmetic('subtract')` of PERCENT - PERCENT yields
    PERCENT, not BPS).  See operator architecture doc.
    """
    if units_basis == "raw":
        return input_units
    if units_basis == "level_change":
        if input_units == TimeSeriesUnits.PERCENT:
            return TimeSeriesUnits.BPS
        return input_units
    raise EventWindowsError(
        f"event_windows: unsupported units_basis={units_basis!r}."
    )


# ============================================================================
# OVERLAP STATS
# ============================================================================


def _count_overlapping_pairs(
    event_dates: List[pd.Timestamp],
    panel_index: pd.DatetimeIndex,
    *,
    pre: int,
    post: int,
) -> int:
    """Count pairs of kept events whose windows overlap on the panel
    grid.  Used for lineage-level diagnostics; downstream operators
    (conditional_aggregate) can read this to opt into overlap
    de-weighting in a future v2."""
    if len(event_dates) < 2:
        return 0
    n = len(panel_index)
    positions: List[int] = []
    for d in event_dates:
        try:
            positions.append(panel_index.get_loc(d))
        except KeyError:
            continue
    positions.sort()

    overlaps = 0
    for i, p_i in enumerate(positions):
        # Window of event i covers [p_i - pre, p_i + post] (inclusive).
        # Overlap with event j (j > i) iff p_j - pre <= p_i + post,
        # i.e. p_j <= p_i + post + pre.
        for j in range(i + 1, len(positions)):
            if positions[j] - p_i <= post + pre:
                overlaps += 1
            else:
                break  # positions sorted; no further overlaps possible
    return overlaps


__all__ = [
    "event_windows",
    "EventWindowsError",
]
