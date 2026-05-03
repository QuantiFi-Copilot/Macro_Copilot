"""conditional_aggregate — reduce a WindowedPanel to per-offset stats.

Per the operator architecture doc, this module owns the
``aggregation`` structural method family.  Final operator of
Phase 1A — closes the event-study skeleton end-to-end.

Design summary
--------------
1. Resolve config-defaulted fields when caller omitted them
   (``aggregator`` and ``dispersion`` default to None in the
   schema; YAML is authoritative — same pattern as
   threshold_events post PR #53 and event_windows post PR #55).
2. Validate input is a ``WindowedPanel`` (no MaskedSeries overload
   in v1; that's Q2's trigger).
3. NaN-aware per-column reductions:
   - mean   → np.nanmean
   - median → np.nanmedian
   - std    → np.nanstd(ddof=ddof)
   - count  → np.sum(np.isfinite(...))
4. Per-column dispersion:
   - 'std'  → np.nanstd(ddof=ddof)
   - 'none' → NaN  (dispersion not requested)
5. Per-column n_observations = count of finite cells.
6. Per-column low_n flag = (n_observations < min_n).  The operator
   does NOT suppress values when low_n is True — surfacing the
   flag is enough; the caller / template / UI decides how to
   react to weak evidence.
7. Output is an ``EventResponseSeries``.  Units inherited from the
   panel's units; lineage = panel.lineage + this operator step.

Errors
------
Operators in ``shared.operators.*`` raise typed exceptions on user-
facing failures.  The orchestration / template / public-tool layer
converts to ``{"error": "..."}`` envelopes at the user boundary.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Optional

import numpy as np

from shared.artifacts.lineage import OperatorStep
from shared.artifacts.types import EventResponseSeries, WindowedPanel
from shared.config.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    load_operator_config,
)
from shared.operators.conditional_aggregate.schemas import (
    Aggregator,
    ConditionalAggregateParams,
    DispersionKind,
)


_OPERATOR_NAME = "conditional_aggregate"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class ConditionalAggregateError(ValueError):
    """Raised by ``conditional_aggregate`` on a recoverable user-facing
    failure.

    Subclass of ``ValueError`` so existing ``except ValueError`` blocks
    in the rest of ``shared/`` continue to work.  Templates and public
    wrappers convert this into the controlled error envelope at the
    user-facing boundary.
    """


def conditional_aggregate(
    panel: WindowedPanel,
    params: Optional[ConditionalAggregateParams] = None,
    config: Optional[OperatorConfig] = None,
) -> EventResponseSeries:
    """Reduce ``panel`` per offset (column) using ``params.aggregator``.

    Parameters
    ----------
    panel :
        ``WindowedPanel`` — typically the output of ``event_windows``.
        Must have at least one event row; an empty panel
        (``n_events == 0``) cannot be reduced honestly.
    params :
        Optional ``ConditionalAggregateParams``.  ``aggregator`` and
        ``dispersion`` default to ``None`` in the schema; the operator
        resolves them from ``config.default_value(...)`` when None.
        ``min_n`` and ``ddof`` come from schema defaults (5 and 1
        respectively) — those are non-controversial defaults that
        don't need YAML authority.
    config :
        Optional ``OperatorConfig``.  When omitted, the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    EventResponseSeries
        Per-offset (value, dispersion, n, low_n) tuples.  Units
        inherited from the panel; lineage is panel.lineage with the
        new operator step appended.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    if not isinstance(config, OperatorConfig):
        raise OperatorConfigError(
            f"conditional_aggregate: 'config' must be OperatorConfig; "
            f"got {type(config).__name__}."
        )
    if config.operator.name != _OPERATOR_NAME:
        raise OperatorConfigError(
            f"conditional_aggregate: config name mismatch — expected "
            f"{_OPERATOR_NAME!r}, got {config.operator.name!r}."
        )

    # ------------------------------------------------------------------
    # 2. Resolve config-defaulted fields when caller omitted them.
    # ------------------------------------------------------------------
    if params is None:
        params = ConditionalAggregateParams()

    if params.aggregator is None or params.dispersion is None:
        resolved_aggregator = (
            params.aggregator
            if params.aggregator is not None
            else config.default_value("aggregator")
        )
        resolved_dispersion = (
            params.dispersion
            if params.dispersion is not None
            else config.default_value("dispersion")
        )
        params = ConditionalAggregateParams(
            aggregator=resolved_aggregator,
            dispersion=resolved_dispersion,
            min_n=params.min_n,
            ddof=params.ddof,
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation.
    # ------------------------------------------------------------------
    if not isinstance(panel, WindowedPanel):
        raise ConditionalAggregateError(
            f"conditional_aggregate: panel must be a WindowedPanel "
            f"artifact; got {type(panel).__name__}."
        )

    if panel.n_events == 0:
        raise ConditionalAggregateError(
            "conditional_aggregate: input WindowedPanel has 0 events.  "
            "There is nothing to aggregate; check the upstream "
            "threshold / event_windows steps for unexpectedly empty "
            "outputs."
        )

    # ------------------------------------------------------------------
    # 4. Per-column NaN-aware reductions.
    # ------------------------------------------------------------------
    payload = panel.payload  # shape: [n_events, window_length]
    aggregator: Aggregator = params.aggregator  # type: ignore[assignment]
    dispersion_kind: DispersionKind = params.dispersion  # type: ignore[assignment]

    # n_observations: per-column count of finite cells.  This is the
    # effective sample size at each offset — drives the low_n flag and
    # is what every reduction below operates on after NaN exclusion.
    finite_mask = np.isfinite(payload)
    n_observations = finite_mask.sum(axis=0).astype(int)

    # Aggregator: NaN-aware.
    # ``np.nanmean`` / ``np.nanmedian`` / ``np.nanstd`` raise a
    # RuntimeWarning when all values along a slice are NaN.  We
    # suppress that AND numpy's "Mean of empty slice" /
    # "Degrees of freedom <= 0" warnings — the result is NaN by
    # design and the low_n flag will already be True for those
    # offsets, so the value is honestly "no data" rather than
    # "data is silently bad".
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore", category=RuntimeWarning)
        if aggregator == "mean":
            values = np.nanmean(payload, axis=0)
        elif aggregator == "median":
            values = np.nanmedian(payload, axis=0)
        elif aggregator == "std":
            values = np.nanstd(payload, axis=0, ddof=params.ddof)
        elif aggregator == "count":
            # Count returns the same as n_observations but as float so
            # the output array has consistent dtype.
            values = n_observations.astype(float)
        else:
            raise ConditionalAggregateError(
                f"conditional_aggregate: unsupported aggregator="
                f"{aggregator!r}."
            )

        # Dispersion (always NaN-aware std for v1).
        if dispersion_kind == "std":
            dispersions = np.nanstd(payload, axis=0, ddof=params.ddof)
        elif dispersion_kind == "none":
            dispersions = np.full(payload.shape[1], np.nan, dtype=float)
        else:
            raise ConditionalAggregateError(
                f"conditional_aggregate: unsupported dispersion="
                f"{dispersion_kind!r}."
            )

    # All-NaN columns produce NaN reductions; that's fine, the values
    # column will just have NaN there.
    values = np.asarray(values, dtype=float)
    dispersions = np.asarray(dispersions, dtype=float)

    # low_n flag per offset.
    low_n_flags = (n_observations < params.min_n)

    # ------------------------------------------------------------------
    # 5. Build the operator step + output artifact.
    # ------------------------------------------------------------------
    op_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params={
            "aggregator": aggregator,
            "dispersion": dispersion_kind,
            "min_n": params.min_n,
            "ddof": params.ddof,
            "n_events_in": int(panel.n_events),
            "window_length": int(panel.window_length),
            "n_offsets_low_n": int(low_n_flags.sum()),
            "input_units": panel.units.value,
            "target_series_key": panel.target_series_key,
        },
        input_hashes=(panel.lineage.head_hash,),
    )
    lineage = panel.lineage.append(op_step)

    return EventResponseSeries(
        offsets=list(panel.offsets),
        values=values,
        dispersions=dispersions,
        n_observations=n_observations,
        low_n_flags=low_n_flags,
        aggregator=aggregator,
        dispersion_kind=dispersion_kind,
        units=panel.units,
        target_series_key=panel.target_series_key,
        lineage=lineage,
    )


__all__ = [
    "conditional_aggregate",
    "ConditionalAggregateError",
]
