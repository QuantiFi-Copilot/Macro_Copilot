"""transition_events — label changes of a discrete-valued Series.

Finance-blind ``masking``-family operator (the ``threshold_events``
sibling).  Emits an ``EventSet`` with one event at every position
where the input's DISCRETE label differs from the previous row's —
the structural "the state changed here" detector.  The canonical
upstream is any integer-label series (model state labels, sign
buckets, rank buckets); the operator never interprets the labels.

INTEGER-VALUED REQUIREMENT (the Warden's float-label ruling): the
payload is float64 by artifact contract, so dtype cannot carry
discreteness — instead every finite value must be integer-VALUED
(v == round(v)).  A soft/posterior state series (e.g. 0.9) is REFUSED,
never silently rounded: quantising a soft state is a methodology
choice that belongs upstream, in lineage.

EVENT CONVENTION (ART11): the event is stamped at ``t`` — the first
bar where the NEW label is in force — with ``from_value``/``to_value``
in the per-event metadata.  A transition needs two ADJACENT finite
rows: the first row is never an event, and a NaN on either side of a
pair suppresses it (no event across a gap — a change you cannot date
to adjacent observations is not a dated transition).

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind structural method (adjacent-row
    inequality on discrete labels); zero finance vocabulary, no state
    semantics.
  - OPR2      : ONE output artifact type — always an ``EventSet``.
  - OPR8      : ``params: Optional[...] = None`` (zero knobs in v1;
    filtering is a declared planned extension).
  - OPR9      : one ``Series`` in, one ``EventSet`` out.
  - OPR10     : one ``OperatorStep``; the label inventory and event
    count ride in params.
  - OPR13     : typed ``TransitionEventsError`` refusals: non-Series
    input; an all-NaN input; non-integer-valued labels.  ZERO
    transitions is a VALID empty EventSet (the threshold_events
    no-qualifying-rows convention), not a refusal.
  - OPR14     : pure + rerun-deterministic (exact integer equality —
    no float-tolerance ambiguity).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import EventSet, Series
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.transition_events.schemas import TransitionEventsParams


_OPERATOR_NAME = "transition_events"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class TransitionEventsError(ValueError):
    """Raised by ``transition_events`` on a recoverable user-facing
    failure (a ``ValueError`` subclass, OPR13)."""


def transition_events(
    series: Series,
    *,
    params: Optional[TransitionEventsParams] = None,
    config: Optional[OperatorConfig] = None,
) -> EventSet:
    """Label-change events of the discrete-valued ``series``.

    Parameters
    ----------
    series :
        The input ``Series`` whose finite values are integer-valued
        discrete labels (any units tag; the values are labels, not
        quantities).
    params :
        Optional ``TransitionEventsParams`` (no fields in v1).
        ``None`` is equivalent.
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    EventSet
        One event at each position where the label differs from the
        previous ADJACENT finite row, stamped at the first bar of the
        new label, with ``from_value``/``to_value`` metadata; the
        source's frequency tag propagates.  Zero transitions yield a
        valid empty EventSet.

    Raises
    ------
    TransitionEventsError
        The input is not a ``Series``; it has no finite values; or a
        finite value is not integer-valued (soft states refused —
        quantise upstream).
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check — OPR12.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. Resolve params — OPR8 (no fields in v1).
    # ------------------------------------------------------------------
    if params is None:
        params = TransitionEventsParams()

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; discreteness).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise TransitionEventsError(
            "transition_events: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    payload = series.payload
    values = payload.to_numpy(dtype=float)
    finite_mask = np.isfinite(values)
    n_finite = int(finite_mask.sum())
    if n_finite == 0:
        raise TransitionEventsError(
            f"transition_events: the input has no finite values "
            f"({len(values)} rows)."
        )

    finite_vals = values[finite_mask]
    non_integer = finite_vals[finite_vals != np.round(finite_vals)]
    if len(non_integer) > 0:
        raise TransitionEventsError(
            f"transition_events: {len(non_integer)} finite value(s) are "
            "not integer-valued (e.g. "
            f"{float(non_integer[0])!r}) — the input must be a DISCRETE "
            "label series.  Soft/posterior states are refused, never "
            "silently rounded: quantise upstream so the choice is in "
            "lineage."
        )

    # ------------------------------------------------------------------
    # 4. Adjacent-row label changes (exact integer equality; a NaN on
    #    either side of a pair suppresses the event).
    # ------------------------------------------------------------------
    mask_arr = np.zeros(len(values), dtype=bool)
    for i in range(1, len(values)):
        prev_v, cur_v = values[i - 1], values[i]
        if np.isnan(prev_v) or np.isnan(cur_v):
            continue
        if prev_v != cur_v:
            mask_arr[i] = True

    mask = pd.Series(mask_arr, index=payload.index, dtype=bool)
    event_dates: List[pd.Timestamp] = list(payload.index[mask])
    per_event_metadata: List[Dict[str, Any]] = []
    for ts in event_dates:
        pos = payload.index.get_loc(ts)
        per_event_metadata.append({
            "from_value": int(round(values[pos - 1])),
            "to_value": int(round(values[pos])),
        })

    labels = sorted({int(round(v)) for v in finite_vals})

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep (OPR10).
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "event_convention": (
            "stamped at t, the first bar where the NEW label is in "
            "force; adjacent finite rows only (no events across gaps)"
        ),
        "n_events": int(mask_arr.sum()),
        "n_distinct_labels": len(labels),
        "labels": labels[:20],  # bounded inventory for the audit trail
        "n_obs": int(len(values)),
        "n_finite": n_finite,
        "source_series_key": series.series_key,
        "input_units": series.units.value,
    }
    op_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(series.lineage.head_hash,),
    )
    lineage = series.lineage.append(op_step)

    return EventSet(
        mask=mask,
        event_dates=event_dates,
        per_event_metadata=per_event_metadata,
        source_series_key=series.series_key,
        # Propagate the source's frequency tag so event_windows can
        # enforce frequency-tag agreement (the threshold_events
        # convention).
        frequency=series.frequency,
        lineage=lineage,
    )


__all__ = ["transition_events", "TransitionEventsError"]
