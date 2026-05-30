"""conditional_aggregate — reduce a WindowedPanel to a per-offset Series.

Per the operator architecture doc, this module owns the
``aggregation`` structural method family.  Final operator of
Phase 1A — closes the event-study skeleton end-to-end.

Design summary
--------------
1. Resolve config-defaulted fields when caller omitted them
   (``aggregator`` and ``dispersion`` default to None in the
   schema; YAML is authoritative — same pattern as
   threshold_events post PR #53 and event_windows post PR #55).
   Re-validate after resolution so the cross-field constraint
   (``count + std`` rejection) fires on the resolved combo.
2. Validate input is a ``WindowedPanel`` (no MaskedSeries overload
   in v1; that's Q2's trigger).
3. NaN-aware per-column reductions (ddof=1 = sample std; matches
   the rest of shared.analytics):
   - mean   → np.nanmean
   - median → np.nanmedian
   - std    → np.nanstd(ddof=1)
   - count  → np.sum(np.isfinite(...))
4. Per-column dispersion:
   - 'std'  → np.nanstd(ddof=1)
   - 'none' → omitted (no dispersion column in output)
5. Per-column n_observations = count of finite cells.
6. Per-column low_n flag = (n_observations < min_n).  The operator
   does NOT suppress values when low_n is True — surfacing the
   flag in lineage params is enough; the caller / template / UI
   decides how to react to weak evidence.
7. Output is a typed ``Series`` per the v5 plan.  Indexed by a
   synthetic ``DatetimeIndex`` anchor (``1970-01-01 + offset
   days``) so the artifact contract stays uniform with the rest
   of Phase 1A; the integer offsets, dispersions, n_observations,
   and low_n_flags are recorded in the operator step's lineage
   params so the consumer can recover them without reverse-
   engineering the index encoding.

Output unit policy:
  - ``aggregator='count'``  → ``TimeSeriesUnits.COUNT`` (overrides
                              the panel's units; counts are not in
                              the panel's original measure)
  - other aggregators       → inherit from the panel's units

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
import pandas as pd

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import Series, WindowedPanel
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    _check_config_identity,
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

# Synthetic anchor for the output Series's DatetimeIndex.  Offsets
# are encoded as ``_OFFSET_ANCHOR + Timedelta(days=offset)``.  The
# integer offsets are ALSO recorded in lineage params so consumers
# don't have to reverse-engineer the encoding.
_OFFSET_ANCHOR: pd.Timestamp = pd.Timestamp("1970-01-01")

# Fixed degrees-of-freedom for std (sample std, Bessel's correction).
# Configurable ddof is deferred per planned_extensions.
_FIXED_DDOF: int = 1


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
) -> Series:
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
        ``min_n`` comes from a schema default (5) — non-controversial.
    config :
        Optional ``OperatorConfig``.  When omitted, the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        Length ``window_length``.  Payload is the aggregator value at
        each offset.  Index is a synthetic ``DatetimeIndex`` anchor
        (``1970-01-01 + offset days``); the integer offsets are
        recorded in the operator step's lineage params.  Units come
        from the panel's units (``COUNT`` when aggregator='count').
        Per-offset dispersions, n_observations, and low_n_flags are
        recorded in the operator step's lineage params.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    # Config identity (OPR12) — name AND version.
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

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
        # Re-construct via the schema so the cross-field constraint
        # (count + std rejection) fires on the resolved combo.
        params = ConditionalAggregateParams(
            aggregator=resolved_aggregator,
            dispersion=resolved_dispersion,
            min_n=params.min_n,
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

    finite_mask = np.isfinite(payload)
    n_observations = finite_mask.sum(axis=0).astype(int)

    # Suppress numpy's "Mean of empty slice" / "DOF <= 0" RuntimeWarnings —
    # all-NaN columns are intentional (the low_n flag will already be
    # True there); the result is honestly NaN.
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore", category=RuntimeWarning)

        if aggregator == "mean":
            values = np.nanmean(payload, axis=0)
        elif aggregator == "median":
            values = np.nanmedian(payload, axis=0)
        elif aggregator == "std":
            values = np.nanstd(payload, axis=0, ddof=_FIXED_DDOF)
        elif aggregator == "count":
            values = n_observations.astype(float)
        else:
            raise ConditionalAggregateError(
                f"conditional_aggregate: unsupported aggregator="
                f"{aggregator!r}."
            )

        if dispersion_kind == "std":
            dispersions = np.nanstd(payload, axis=0, ddof=_FIXED_DDOF)
        elif dispersion_kind == "none":
            dispersions = None
        else:
            raise ConditionalAggregateError(
                f"conditional_aggregate: unsupported dispersion="
                f"{dispersion_kind!r}."
            )

    values = np.asarray(values, dtype=float)
    low_n_flags = (n_observations < params.min_n)

    # ------------------------------------------------------------------
    # 5. Resolve output units.  ``count`` overrides to COUNT (counts
    #    are not in the panel's original measure); other aggregators
    #    inherit from the panel.
    # ------------------------------------------------------------------
    if aggregator == "count":
        output_units = TimeSeriesUnits.COUNT
    else:
        output_units = panel.units

    # ------------------------------------------------------------------
    # 6. Build the synthetic-anchor DatetimeIndex.  Offsets ARE the
    #    integer event-relative day indices from the panel; the
    #    encoding scheme is documented in the operator step's params
    #    and in the module docstring.
    # ------------------------------------------------------------------
    offsets = list(panel.offsets)
    index = pd.DatetimeIndex(
        [_OFFSET_ANCHOR + pd.Timedelta(days=int(k)) for k in offsets]
    )
    output_payload = pd.Series(values, index=index, dtype=float)

    # ------------------------------------------------------------------
    # 7. Build the operator step + assemble lineage.
    # ------------------------------------------------------------------
    step_params = {
        "aggregator": aggregator,
        "dispersion": dispersion_kind,
        "min_n": params.min_n,
        "ddof_used": _FIXED_DDOF,
        "n_events_in": int(panel.n_events),
        "window_length": int(panel.window_length),
        "input_units": panel.units.value,
        "output_units": output_units.value,
        "target_series_key": panel.target_series_key,
        # Offset-encoding metadata (so consumers don't have to
        # reverse-engineer the synthetic anchor):
        "offset_anchor": _OFFSET_ANCHOR.strftime("%Y-%m-%d"),
        "event_relative_offsets": offsets,
        # Per-offset metadata that doesn't fit a single Series payload:
        "n_observations_per_offset": [int(x) for x in n_observations],
        "low_n_flags_per_offset": [bool(x) for x in low_n_flags],
        "n_offsets_low_n": int(low_n_flags.sum()),
    }
    if dispersions is not None:
        # NaN dispersions (all-NaN columns) are mapped to None by
        # sanitize_params_for_lineage below — the canonical OPR10 path,
        # replacing the prior hand-rolled finite-check (F-DET-5).
        step_params["dispersions_per_offset"] = [float(x) for x in dispersions]
    else:
        step_params["dispersions_per_offset"] = None

    op_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(panel.lineage.head_hash,),
    )
    lineage = panel.lineage.append(op_step)

    return Series(
        series_key=f"{panel.target_series_key}__{aggregator}_by_offset",
        payload=output_payload,
        units=output_units,
        # Frequency on the synthetic-anchor index isn't a real
        # business-day frequency; leave None so downstream operators
        # don't get misled.  The integer-offset semantic lives in
        # lineage params.
        frequency=None,
        # Output's missingness policy reflects the aggregation:
        # the per-cell aggregation may have skipped NaN cells, so
        # the output values are a fresh derivation — we use the
        # panel's source's policy as the simplest honest answer
        # (the panel itself doesn't currently carry one; future
        # work could thread it).  For v1, RawNoCleaning is the
        # simplest accurate description: the values are computed
        # from the panel directly, no further cleaning applied.
        missingness_policy=_default_output_missingness(),
        lineage=lineage,
    )


def _default_output_missingness():
    """Return the default missingness policy for the output Series.

    The aggregation produced these values fresh; they are not the
    result of any ``clean_single_series`` invocation.  ``RawNoCleaning``
    is the simplest honest description.  Importing here (not at
    module load) avoids a heavyweight import in case
    ``shared.artifacts.missingness`` ever gains side-effects.
    """
    from shared.artifacts.missingness import RawNoCleaning
    return RawNoCleaning()


__all__ = [
    "conditional_aggregate",
    "ConditionalAggregateError",
]
