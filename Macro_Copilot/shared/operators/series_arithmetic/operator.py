"""series_arithmetic — strict elementwise arithmetic on typed Series.

Per the operator architecture doc, this module owns the ``arithmetic``
structural method family.  Finance-blind; refuses unit-incoherent
operations rather than silently coercing.

Strict unit algebra (Phase 1A)
------------------------------

  ``add`` / ``subtract`` :
    require ``left.units == right.units``.  Output units = same.

  ``multiply`` :
    Series * scalar only.  Output units = left.units.
    Series * Series is REJECTED in v1 (no unit composition rules
    until ``convert_units`` lands; see planned_extensions).

  ``divide`` :
    Series / Series of SAME units → ``ratio`` (unitless).
    Series / scalar → left.units (preserved).
    Different-unit Series / Series is REJECTED.

  ``diff`` :
    Unary; output units = left.units, lineage records "differenced".

  ``pct_change`` :
    Unary; output units = ``ratio`` regardless of input.

Anything outside the table raises ``SeriesArithmeticError``.
There is NO silent percent → bps multiplication; that requires a
deferred ``convert_units`` operator.

Composition contract
--------------------
When both operands are Series, they MUST already share a
``DatetimeIndex``.  ``series_arithmetic`` does not align — that's
``align_series``'s job.  If indexes differ, the operator raises a
controlled error directing the caller to align first.

Errors
------
Operators in ``shared.operators.*`` raise typed exceptions on user-
facing failures.  The orchestration / template / public-tool layer
converts to ``{"error": "..."}`` envelopes at the user boundary.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Tuple, Union

import numpy as np
import pandas as pd

from shared.artifacts.lineage import Lineage, OperatorStep
from shared.artifacts.missingness import MissingnessPolicy
from shared.artifacts.types import Series
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    load_operator_config,
)
from shared.operators.series_arithmetic.schemas import (
    SeriesArithmeticOp,
    SeriesArithmeticParams,
)


_OPERATOR_NAME = "series_arithmetic"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Ops that take a unary Series input (right is omitted).
_UNARY_OPS: Tuple[SeriesArithmeticOp, ...] = ("diff", "pct_change")

# Ops that produce a unitless (``ratio``) output regardless of inputs
# (when their other compatibility checks are satisfied).
_RATIO_OUTPUT_OPS: Tuple[SeriesArithmeticOp, ...] = ("pct_change",)


class SeriesArithmeticError(ValueError):
    """Raised by ``series_arithmetic`` on a recoverable user-facing failure.

    Subclass of ``ValueError`` so existing ``except ValueError`` blocks
    in the rest of ``shared/`` continue to work.  Templates and public
    wrappers convert this into the controlled error envelope at the
    user-facing boundary.
    """


def series_arithmetic(
    left: Series,
    op: SeriesArithmeticOp,
    right: Optional[Union[Series, int, float]] = None,
    *,
    params: Optional[SeriesArithmeticParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Apply an elementwise arithmetic op to ``left`` and ``right``.

    Parameters
    ----------
    left :
        The left-hand ``Series`` artifact.
    op :
        One of ``add``, ``subtract``, ``multiply``, ``divide``,
        ``diff``, ``pct_change``.  Surfaced as a positional argument
        rather than only on ``params`` so the call site reads
        naturally (``series_arithmetic(a, "subtract", b)``).
    right :
        For binary ops, a ``Series`` (must share index with ``left``)
        or a Python scalar.  For unary ops (``diff``, ``pct_change``)
        ``right`` MUST be ``None`` — passing one is a usage error.
    params :
        Optional ``SeriesArithmeticParams``.  Currently used only for
        ``period`` (defaults to 1; meaningful for ``diff``/``pct_change``).
        ``op`` from ``params`` is ignored — the positional ``op``
        argument is authoritative; if both are supplied they must agree.
    config :
        Optional ``OperatorConfig``.  When omitted, the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        Output payload + units per the strict unit-algebra table in
        the module docstring.  Lineage is the union of input lineages
        plus this operator step.
    """
    # ------------------------------------------------------------------
    # 1. Load config + resolve params.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    if not isinstance(config, OperatorConfig):
        raise OperatorConfigError(
            f"series_arithmetic: 'config' must be OperatorConfig; "
            f"got {type(config).__name__}."
        )
    if config.operator.name != _OPERATOR_NAME:
        raise OperatorConfigError(
            f"series_arithmetic: config name mismatch — expected "
            f"{_OPERATOR_NAME!r}, got {config.operator.name!r}."
        )

    if params is None:
        params = SeriesArithmeticParams(
            op=op,
            period=int(config.default_value("period")),
        )
    elif params.op != op:
        # Tolerate but require explicit agreement when both are passed.
        raise SeriesArithmeticError(
            f"series_arithmetic: positional op={op!r} disagrees with "
            f"params.op={params.op!r}.  Pass only one or make them match."
        )

    require_matching_frequency = params.require_matching_frequency
    require_matching_missingness = params.require_matching_missingness

    # ------------------------------------------------------------------
    # 2. Arity + structural-input validation.
    # ------------------------------------------------------------------
    is_unary = op in _UNARY_OPS

    if is_unary and right is not None:
        raise SeriesArithmeticError(
            f"series_arithmetic: op={op!r} is unary; pass right=None."
        )
    if not is_unary and right is None:
        raise SeriesArithmeticError(
            f"series_arithmetic: op={op!r} requires a right operand "
            "(Series or scalar)."
        )

    if not isinstance(left, Series):
        raise SeriesArithmeticError(
            f"series_arithmetic: left must be a Series artifact; "
            f"got {type(left).__name__}."
        )

    if (not is_unary) and isinstance(right, Series):
        if not left.payload.index.equals(right.payload.index):
            raise SeriesArithmeticError(
                f"series_arithmetic: left and right Series must share "
                "an identical DatetimeIndex.  Use ``align_series`` "
                f"first.  left.len={len(left.payload)}, "
                f"right.len={len(right.payload)}."
            )
        # Frequency compatibility — same discipline as align_series.
        # Strict mode (default) requires the two operands' frequency
        # tags to agree (or both be None).  Lenient mode accepts
        # mismatch and records that on the output (frequency=None).
        if require_matching_frequency and left.frequency != right.frequency:
            raise SeriesArithmeticError(
                f"series_arithmetic op={op!r}: incompatible frequencies "
                f"left={left.frequency!r} vs right={right.frequency!r}.  "
                "Pass require_matching_frequency=False to opt into "
                "mixed-frequency arithmetic explicitly."
            )
        # Missingness compatibility — JSON-string canonicalisation
        # handles flat AND nested policies (matches the fix in
        # align_series after the AlignSeriesFFillV1 nesting).
        if require_matching_missingness:
            left_sig = json.dumps(
                left.missingness_policy.model_dump(mode="json"),
                sort_keys=True, separators=(",", ":"),
            )
            right_sig = json.dumps(
                right.missingness_policy.model_dump(mode="json"),
                sort_keys=True, separators=(",", ":"),
            )
            if left_sig != right_sig:
                raise SeriesArithmeticError(
                    f"series_arithmetic op={op!r}: incompatible "
                    f"missingness policies left vs right.  Pass "
                    "require_matching_missingness=False to opt into "
                    "mixed policies explicitly."
                )

    # ------------------------------------------------------------------
    # 3. Strict unit algebra → resolve output units.
    # ------------------------------------------------------------------
    output_units = _resolve_output_units(op, left, right)

    # ------------------------------------------------------------------
    # 4. Compute payload.
    # ------------------------------------------------------------------
    payload = _compute_payload(op, left, right, period=params.period)

    # ------------------------------------------------------------------
    # 5. Build lineage step.
    # ------------------------------------------------------------------
    auxiliary_lineages: Tuple[Lineage, ...] = ()
    if is_unary:
        input_hashes = (left.lineage.head_hash,)
        right_kind = "none"
        right_units = None
    elif isinstance(right, Series):
        input_hashes = (left.lineage.head_hash, right.lineage.head_hash)
        right_kind = "series"
        right_units = right.units.value
        # Persist the right operand's full lineage chain on the
        # operator step so downstream methodology summaries can walk
        # back into it without an external lineage cache (Codex P1
        # follow-up — head.lineage alone otherwise loses the right
        # operand's fetch/clean path).
        auxiliary_lineages = (right.lineage,)
    else:
        input_hashes = (left.lineage.head_hash,)
        right_kind = "scalar"
        right_units = None

    op_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params={
            "op": op,
            "period": params.period,
            "right_kind": right_kind,
            "right_scalar": (
                float(right) if (right_kind == "scalar") else None
            ),
            "left_units": left.units.value,
            "right_units": right_units,
            "output_units": output_units.value,
            "require_matching_frequency": require_matching_frequency,
            "require_matching_missingness": require_matching_missingness,
        },
        input_hashes=input_hashes,
        auxiliary_lineages=auxiliary_lineages,
    )

    # Output lineage = left's chain + this op step.  The right's chain
    # is reachable via op_step.auxiliary_lineages.
    out_lineage = left.lineage.append(op_step)

    # ------------------------------------------------------------------
    # 6. Determine output frequency + missingness.  Arithmetic does
    #    not change index semantics; we propagate the agreed-on
    #    metadata when both operands match (or when the right is a
    #    scalar / unary op).  Under lenient mode with disagreement,
    #    we drop frequency to None (cannot honestly emit a single
    #    tag) and preserve left's missingness policy with the
    #    lenient choice recorded in lineage params.
    # ------------------------------------------------------------------
    output_frequency = left.frequency
    output_missingness: MissingnessPolicy = left.missingness_policy
    if isinstance(right, Series):
        if left.frequency == right.frequency:
            output_frequency = left.frequency
        else:
            # We only get here when require_matching_frequency=False
            # (strict path raised already).  Drop the tag to None.
            output_frequency = None
        # When require_matching_missingness=False let strict mismatch
        # through, output policy is "left's policy" — a deliberate
        # simplification for v1.  The lenient choice is logged in
        # the step's params; introducing a structured combined-policy
        # wrapper is deferred until a real workflow demands it.
        output_missingness = left.missingness_policy

    return Series(
        series_key=_compose_series_key(left, op, right),
        payload=payload,
        units=output_units,
        frequency=output_frequency,
        missingness_policy=output_missingness,
        lineage=out_lineage,
    )


# ============================================================================
# UNIT ALGEBRA
# ============================================================================


def _resolve_output_units(
    op: SeriesArithmeticOp,
    left: Series,
    right: Optional[Union[Series, int, float]],
) -> TimeSeriesUnits:
    """Implement the strict unit-algebra table.  Raises on incoherent
    combinations rather than silently coercing."""
    if op == "add" or op == "subtract":
        # Both must be Series with matching units.
        if not isinstance(right, Series):
            raise SeriesArithmeticError(
                f"series_arithmetic op={op!r} requires a right Series; "
                "scalar arithmetic is meaningless under strict unit "
                "algebra (a scalar has no unit).  For unit-preserving "
                "scalar shifts, defer to a future ``shift_series`` "
                "operator."
            )
        if left.units != right.units:
            raise SeriesArithmeticError(
                f"series_arithmetic op={op!r}: incompatible units "
                f"left={left.units.value!r} vs right={right.units.value!r}.  "
                "Strict unit algebra refuses cross-unit arithmetic; "
                "convert one side explicitly via a future "
                "``convert_units`` operator before subtracting."
            )
        return left.units

    if op == "multiply":
        # Series × scalar only in v1.
        if isinstance(right, Series):
            raise SeriesArithmeticError(
                "series_arithmetic op='multiply' with two Series is "
                "not supported in v1 (no unit-composition rules yet).  "
                "Multiply by a scalar instead, or wait for the "
                "deferred ``convert_units`` operator."
            )
        if not isinstance(right, (int, float)):
            raise SeriesArithmeticError(
                f"series_arithmetic op='multiply' right must be a "
                f"Series or numeric scalar; got {type(right).__name__}."
            )
        return left.units

    if op == "divide":
        if isinstance(right, Series):
            if left.units != right.units:
                raise SeriesArithmeticError(
                    f"series_arithmetic op='divide' with two Series "
                    f"requires matching units; got "
                    f"left={left.units.value!r}, "
                    f"right={right.units.value!r}.  Strict unit "
                    "algebra rejects cross-unit division."
                )
            # Same-unit / same-unit → unitless ratio.
            return TimeSeriesUnits.RATIO
        if not isinstance(right, (int, float)):
            raise SeriesArithmeticError(
                f"series_arithmetic op='divide' right must be a "
                f"Series or numeric scalar; got {type(right).__name__}."
            )
        # Series / scalar preserves left units.
        return left.units

    if op == "diff":
        # Unary, units preserved.
        return left.units

    if op == "pct_change":
        # Unary, output is unitless ratio regardless of input.
        return TimeSeriesUnits.RATIO

    raise SeriesArithmeticError(
        f"series_arithmetic: unsupported op={op!r}."
    )


# ============================================================================
# PAYLOAD COMPUTATION
# ============================================================================


def _compute_payload(
    op: SeriesArithmeticOp,
    left: Series,
    right: Optional[Union[Series, int, float]],
    *,
    period: int,
) -> pd.Series:
    """Run the actual arithmetic.  Index validation and unit checks
    have already passed by the time we reach here."""
    lp = left.payload
    if op == "add":
        return (lp + right.payload).astype(float)  # type: ignore[union-attr]
    if op == "subtract":
        return (lp - right.payload).astype(float)  # type: ignore[union-attr]
    if op == "multiply":
        return (lp * float(right)).astype(float)  # type: ignore[arg-type]
    if op == "divide":
        if isinstance(right, Series):
            return (lp / right.payload).astype(float)
        # Scalar: explicit zero-divisor guard so the user gets a
        # controlled error rather than pandas' silent inf.
        scalar = float(right)  # type: ignore[arg-type]
        if scalar == 0.0:
            raise SeriesArithmeticError(
                "series_arithmetic op='divide' by scalar 0 is "
                "undefined; pass a non-zero divisor or use a future "
                "``overflow_policy='inf'`` knob."
            )
        return (lp / scalar).astype(float)
    if op == "diff":
        return lp.diff(periods=period).astype(float)
    if op == "pct_change":
        # fill_method=None to avoid pandas' deprecated default; NaN
        # propagation handled at the orchestration layer for v1.
        return lp.pct_change(periods=period, fill_method=None).astype(float)
    raise SeriesArithmeticError(
        f"series_arithmetic: unsupported op={op!r}."
    )


# ============================================================================
# SERIES KEY COMPOSITION
# ============================================================================


def _compose_series_key(
    left: Series,
    op: SeriesArithmeticOp,
    right: Optional[Union[Series, int, float]],
) -> str:
    """Build a readable series_key for the output.  Not load-bearing
    for correctness (lineage carries identity); useful for debugging
    and for downstream operators that key on series_key."""
    if isinstance(right, Series):
        return f"{left.series_key}__{op}__{right.series_key}"
    if isinstance(right, (int, float)):
        return f"{left.series_key}__{op}__{right}"
    # Unary
    return f"{left.series_key}__{op}"


__all__ = [
    "series_arithmetic",
    "SeriesArithmeticError",
]
