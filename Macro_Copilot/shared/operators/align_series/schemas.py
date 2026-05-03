"""Pydantic parameter schema for align_series.

Per the operator architecture doc: consequential method variants are
exposed as caller-controlled parameters.  ``align_series`` exposes:

  - ``join_policy``  ∈ {inner, outer}    how to combine N indexes
  - ``fill_policy``  ∈ {raw, ffill}      what to do with post-join NaNs
  - ``fill_limit``   Optional[int]       max consecutive NaNs to fill

Phase 1A surface is intentionally minimal (build plan v5 — strip to the
Q1 minimum).  Deferred per the operator's planned_extensions:

  - ``left`` / ``right`` anchored joins
  - ``bfill`` and ``ffill_then_bfill`` policies
  - ``target_frequency`` resampling
  - explicit named-calendar parameter
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AlignSeriesParams(BaseModel):
    """Parameters for ``align_series`` (Phase 1A surface)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    join_policy: Literal["inner", "outer"] = "inner"
    fill_policy: Literal["raw", "ffill"] = "raw"
    fill_limit: Optional[int] = Field(default=None, ge=0)


__all__ = ["AlignSeriesParams"]
