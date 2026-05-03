"""Pydantic parameter schema for series_arithmetic.

Per the operator architecture doc: consequential method variants are
exposed as caller-controlled parameters.  ``series_arithmetic`` exposes:

  - ``op``                            ∈ {add, subtract, multiply,
                                          divide, diff, pct_change}
  - ``period``                        int (meaningful for diff/pct_change)
  - ``require_matching_frequency``    bool — for Series-Series ops
  - ``require_matching_missingness``  bool — for Series-Series ops

The two compatibility knobs mirror ``align_series``: when both
operands are Series they MUST share the same structural metadata in
strict mode (default).  This is necessary because pandas-NaN
propagates blindly when two cleaned-differently series are subtracted,
and the output's metadata would silently misrepresent the result.

Phase 1A surface is intentionally minimal (build plan v5 — strip to
the Q1/Q3 minimum).  Deferred per the operator's planned_extensions:

  - ``log`` and other unary nonlinear ops
  - configurable ``nan_policy`` (propagate vs skip)
  - configurable ``overflow_policy`` (error vs inf)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SeriesArithmeticOp = Literal[
    "add",
    "subtract",
    "multiply",
    "divide",
    "diff",
    "pct_change",
]


class SeriesArithmeticParams(BaseModel):
    """Parameters for ``series_arithmetic`` (Phase 1A surface)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    op: SeriesArithmeticOp
    period: int = Field(default=1, ge=1)
    require_matching_frequency: bool = True
    require_matching_missingness: bool = True


__all__ = ["SeriesArithmeticParams", "SeriesArithmeticOp"]
