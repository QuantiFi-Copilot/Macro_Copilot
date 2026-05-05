"""Pydantic parameter schema for apply_mask.

Two consequential parameters:

  - ``index_policy``  ∈ {intersect, strict_match}
        intersect    : the operator computes the intersection of
                       ``series.payload.index`` and ``mask.mask.index``
                       and applies the mask on that common subset.  Robust
                       to small index mismatches (the canonical case
                       when a mask is built from a regime classifier
                       on one calendar and applied to a target series
                       on a slightly-different calendar).
        strict_match : require ``series.payload.index ==
                       mask.mask.index`` exactly; raise otherwise.
                       Used when the caller wants a hard guarantee
                       (e.g. when both inputs went through the same
                       align_series step and any index drift is a bug).
  - ``preserve_full_index``  bool
        false (default): the output Series's payload index is
                       restricted to the True dates only — i.e. the
                       returned Series is sparse over time, with
                       only mask=True dates present.  Downstream
                       summarization sees only those dates.
        true            : the output retains the full input index but
                       sets values where mask=False to NaN.  Useful
                       when a downstream operator wants to preserve
                       the same date axis (e.g. for index-aligned
                       arithmetic with another series).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ApplyMaskParams(BaseModel):
    """Parameters for ``apply_mask``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    index_policy: Literal["intersect", "strict_match"] = Field(
        default="intersect",
        description=(
            "How to handle a Series + EventSet whose indexes differ. "
            "``intersect`` (default) computes the index intersection "
            "and applies the mask on that common subset.  "
            "``strict_match`` requires identical indexes and raises "
            "otherwise."
        ),
    )
    preserve_full_index: bool = Field(
        default=False,
        description=(
            "When False (default), the output Series's payload "
            "contains only the dates where the mask is True (sparse "
            "over time).  When True, the output keeps the full "
            "(intersected) date axis with mask=False cells set to "
            "NaN."
        ),
    )


__all__ = ["ApplyMaskParams"]
