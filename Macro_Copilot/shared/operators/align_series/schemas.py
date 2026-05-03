"""Pydantic parameter schema for align_series.

Per the operator architecture doc: consequential method variants are
exposed as caller-controlled parameters.  ``align_series`` exposes:

  - ``join_policy``        ∈ {inner, outer}     how to combine N indexes
  - ``fill_policy``        ∈ {raw, ffill}        what to do with post-join NaNs
  - ``fill_limit``         Optional[int]         max consecutive NaNs to fill
  - ``require_matching_frequency``    bool       enforce frequency-tag agreement
  - ``require_matching_missingness``  bool       enforce missingness-policy agreement

Phase 1A surface is intentionally minimal (build plan v5 — strip to the
Q1 minimum) plus the structural-metadata compatibility knobs the
operator-architecture doc requires.  Both compatibility knobs default
to ``True`` (strict): silently mixing daily and weekly series, or
silently mixing cleaned and raw series, are exactly the failure modes
the structured metadata exists to catch.  Pass ``False`` to opt into
mixed inputs explicitly; the choice is recorded in lineage either way.

Deferred per the operator's planned_extensions:

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
    require_matching_frequency: bool = True
    require_matching_missingness: bool = True


__all__ = ["AlignSeriesParams"]
