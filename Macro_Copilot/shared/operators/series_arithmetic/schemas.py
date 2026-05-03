"""Pydantic parameter schema for series_arithmetic.

Per the operator architecture doc: consequential method variants are
exposed as caller-controlled parameters.  ``series_arithmetic`` exposes:

  - ``op``     ∈ {add, subtract, multiply, divide, diff, pct_change}
  - ``period`` int (only meaningful for diff / pct_change)

Phase 1A surface is intentionally minimal (build plan v5 — strip to
the Q1/Q3 minimum).  Deferred per the operator's planned_extensions:

  - ``log`` and other unary nonlinear ops
  - configurable ``nan_policy`` (propagate vs skip)
  - configurable ``overflow_policy`` (error vs inf)
  - period != 1 is allowed at the schema layer (ge=1) but most
    Phase 1A workflows pin period=1; surfaced as a real knob from
    day one because diff/pct_change of a non-1 lag is a methodology
    choice, not a default
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


__all__ = ["SeriesArithmeticParams", "SeriesArithmeticOp"]
