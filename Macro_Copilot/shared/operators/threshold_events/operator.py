"""threshold_events — convert a Series into an EventSet via a threshold rule.

Per the operator architecture doc, this module owns the ``masking``
structural method family (Phase 1A — only operator in this family
right now).  Finance-blind; lookahead-safe by default.

Design summary
--------------
The operator runs in two stages:

  1. Build a *threshold series* aligned to the input index:
     - ``raw_value`` basis: threshold series == input series.
     - ``rolling_zscore`` basis: standardised series, computed as
       ``(x - rolling_mean) / rolling_std``.  When
       ``look_ahead_safe=True`` (default), the rolling mean / std
       are shifted by one period so the threshold at time t uses
       only data ≤ t-1 — the canonical event-study hygiene.

  2. Apply the rule (``abs_above`` / ``above`` / ``below``) to the
     threshold series, producing a boolean mask.  Per-event metadata
     records the actual triggering value, the standardised value
     (if applicable), and the rule + threshold combination.

Output ``EventSet`` carries full lineage (input + this operator step).

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
from shared.artifacts.types import EventSet, Series
from shared.config.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    load_operator_config,
)
from shared.operators.threshold_events.schemas import (
    ThresholdBasis,
    ThresholdEventsParams,
    ThresholdRule,
)


_OPERATOR_NAME = "threshold_events"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class ThresholdEventsError(ValueError):
    """Raised by ``threshold_events`` on a recoverable user-facing failure.

    Subclass of ``ValueError`` so existing ``except ValueError`` blocks
    in the rest of ``shared/`` continue to work.  Templates and public
    wrappers convert this into the controlled error envelope at the
    user-facing boundary.
    """


def threshold_events(
    series: Series,
    params: ThresholdEventsParams,
    config: Optional[OperatorConfig] = None,
) -> EventSet:
    """Convert ``series`` into an ``EventSet`` per ``params``.

    Parameters
    ----------
    series :
        Input ``Series`` artifact (one indexed numeric series).
    params :
        ``ThresholdEventsParams`` carrying the rule, threshold,
        basis, rolling args, and ``look_ahead_safe`` flag.  The
        operator does NOT default ``rule`` / ``threshold`` from
        config — those are user-input choices, not v1 defaults.
        ``threshold_basis`` and ``look_ahead_safe`` DO have config
        defaults that fire when the caller omits them; the schema
        already enforces the valid combinations of basis + rolling
        args.
    config :
        Optional ``OperatorConfig``.  When omitted, the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    EventSet
        Boolean mask aligned to ``series.payload.index``, plus
        per-event metadata (date, triggering value, threshold
        context) and lineage.  ``source_series_key`` echoes the
        input's series_key so downstream consumers know which
        series this event set was derived from.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    if not isinstance(config, OperatorConfig):
        raise OperatorConfigError(
            f"threshold_events: 'config' must be OperatorConfig; "
            f"got {type(config).__name__}."
        )
    if config.operator.name != _OPERATOR_NAME:
        raise OperatorConfigError(
            f"threshold_events: config name mismatch — expected "
            f"{_OPERATOR_NAME!r}, got {config.operator.name!r}."
        )

    # ------------------------------------------------------------------
    # 2. Structural-input validation.
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise ThresholdEventsError(
            f"threshold_events: series must be a Series artifact; "
            f"got {type(series).__name__}."
        )

    payload = series.payload
    if len(payload) == 0:
        raise ThresholdEventsError(
            "threshold_events: input series is empty."
        )

    # ------------------------------------------------------------------
    # 3. Build the threshold series per the chosen basis.
    # ------------------------------------------------------------------
    threshold_series, basis_metadata = _build_threshold_series(
        payload, params,
    )

    # ------------------------------------------------------------------
    # 4. Apply the rule → boolean mask.
    # ------------------------------------------------------------------
    mask = _apply_rule(threshold_series, params.rule, params.threshold)
    # Where the threshold series is NaN (e.g., during the rolling
    # warmup or its lookahead-safe shift), the mask must be False —
    # an event cannot fire when the threshold itself is undefined.
    mask = mask.fillna(False).astype(bool)

    # ------------------------------------------------------------------
    # 5. Build per-event metadata.
    # ------------------------------------------------------------------
    event_dates: List[pd.Timestamp] = list(payload.index[mask])
    per_event_metadata: List[Dict[str, Any]] = []
    for ts in event_dates:
        meta: Dict[str, Any] = {
            "rule": params.rule,
            "threshold": params.threshold,
            "threshold_basis": params.threshold_basis,
            "raw_value": float(payload.loc[ts]),
        }
        if params.threshold_basis == "rolling_zscore":
            # ``threshold_series`` already holds the standardised value
            # at this index when basis is rolling_zscore.
            zs = threshold_series.loc[ts]
            meta["zscore_value"] = float(zs) if pd.notna(zs) else None
        per_event_metadata.append(meta)

    # ------------------------------------------------------------------
    # 6. Build the operator step + full lineage.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "rule": params.rule,
        "threshold": params.threshold,
        "threshold_basis": params.threshold_basis,
        "rolling_window": params.rolling_window,
        "min_periods": params.min_periods,
        "look_ahead_safe": params.look_ahead_safe,
        "source_series_key": series.series_key,
        "n_events": int(mask.sum()),
    }
    step_params.update(basis_metadata)

    op_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=step_params,
        input_hashes=(series.lineage.head_hash,),
    )
    lineage = series.lineage.append(op_step)

    return EventSet(
        mask=mask,
        event_dates=event_dates,
        per_event_metadata=per_event_metadata,
        source_series_key=series.series_key,
        lineage=lineage,
    )


# ============================================================================
# THRESHOLD-SERIES CONSTRUCTION
# ============================================================================


def _build_threshold_series(
    payload: pd.Series,
    params: ThresholdEventsParams,
) -> Tuple[pd.Series, Dict[str, Any]]:
    """Build the series the rule will be applied to.

    Returns ``(threshold_series, basis_metadata)``.  ``basis_metadata``
    is merged into the operator step's params so the lineage records
    *exactly* how the threshold series was constructed (e.g., the
    effective min_periods, whether the lookahead shift was applied)."""
    if params.threshold_basis == "raw_value":
        return payload.astype(float), {}

    if params.threshold_basis == "rolling_zscore":
        # rolling_window is enforced non-None by the schema validator.
        window = int(params.rolling_window)  # type: ignore[arg-type]
        # min_periods defaults to rolling_window when omitted — the
        # strict choice (no partial-window stats).
        min_periods = (
            int(params.min_periods)
            if params.min_periods is not None
            else window
        )

        # Rolling mean / std with sample std (ddof=1).  Returns NaN
        # for any window with fewer than min_periods observations.
        rolling_mean = payload.rolling(
            window=window, min_periods=min_periods,
        ).mean()
        rolling_std = payload.rolling(
            window=window, min_periods=min_periods,
        ).std(ddof=1)

        if params.look_ahead_safe:
            # Lookahead-safe: stats at time t use only data ≤ t-1.
            # Implemented as a one-period shift of the rolling stats
            # so the value at index t reflects observations [t-window,
            # t-1] rather than [t-window+1, t].  This is the canonical
            # event-study hygiene.
            rolling_mean = rolling_mean.shift(1)
            rolling_std = rolling_std.shift(1)

        # Standardise.  Where rolling_std is 0 (degenerate window),
        # division produces inf/NaN — convert to NaN so the rule
        # branch correctly emits no event.
        zscore = (payload - rolling_mean) / rolling_std
        zscore = zscore.replace([np.inf, -np.inf], np.nan)

        basis_metadata = {
            "rolling_window_used": window,
            "min_periods_used": min_periods,
            "lookahead_shift_applied": params.look_ahead_safe,
            "ddof_used": 1,
        }
        return zscore.astype(float), basis_metadata

    raise ThresholdEventsError(
        f"threshold_events: unsupported threshold_basis="
        f"{params.threshold_basis!r}."
    )


# ============================================================================
# RULE APPLICATION
# ============================================================================


def _apply_rule(
    threshold_series: pd.Series,
    rule: ThresholdRule,
    threshold: float,
) -> pd.Series:
    """Apply the comparison rule to the threshold series."""
    if rule == "abs_above":
        return (threshold_series.abs() > threshold)
    if rule == "above":
        return (threshold_series > threshold)
    if rule == "below":
        return (threshold_series < threshold)
    raise ThresholdEventsError(
        f"threshold_events: unsupported rule={rule!r}."
    )


__all__ = [
    "threshold_events",
    "ThresholdEventsError",
]
