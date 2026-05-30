"""Pydantic parameter schema for apply_mask.

Two consequential parameters plus the two OPR11 structural-metadata
controls (``require_matching_frequency`` / ``require_matching_missingness``):

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

OPR11 structural-metadata controls
----------------------------------
``apply_mask`` consumes TWO artifacts (a ``Series`` + an ``EventSet``),
so OPR11 requires it to expose **both** ``require_matching_frequency``
and ``require_matching_missingness``, strict-by-default, with the same
naming/semantics as every other multi-artifact operator.  The two axes
are **not symmetric** here, because of how the EventSet artifact is
modelled:

  - ``require_matching_frequency``  bool = True
        REAL check.  An ``EventSet`` carries a ``frequency`` tag
        (propagated from its source Series by ``threshold_events``),
        so the operator can — and does — compare ``series.frequency``
        against ``mask.frequency``.  In strict mode (default) a
        mismatch raises ``ApplyMaskError`` (a mask detected on one
        cadence applied to a differently-tagged target is the
        canonical hazard this check exists to surface — the same
        discipline ``event_windows`` enforces against its
        EventSet + target pair).  Pass ``False`` to opt into
        mixed-frequency masking explicitly; the choice is recorded
        in lineage either way.

  - ``require_matching_missingness``  bool = True
        UNIFORMITY flag.  An ``EventSet`` is a *boolean mask*, not a
        value series — it has no ``missingness_policy`` (a NaN-handling
        regime is meaningless for a True/False indicator), so there is
        no second missingness regime to compare against.  The flag is
        exposed (strict-by-default) and recorded in lineage for OPR11
        uniformity and forward-compatibility, but the comparison is
        vacuous: the output Series inherits the input Series's
        ``missingness_policy`` unchanged regardless of the flag's
        value.  (Threading a missingness regime onto the EventSet was
        considered and rejected — the mask-source and the subsampled
        target are frequently *different* series with legitimately
        different cleaning in the regime/event archetypes, so a strict
        match would reject valid workflows.)
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
    require_matching_frequency: bool = Field(
        default=True,
        description=(
            "OPR11 structural-metadata control (mirrors event_windows / "
            "series_arithmetic / align_series).  When True (default), "
            "refuse to mask a Series with an EventSet whose frequency "
            "tag disagrees — the EventSet carries a real ``frequency`` "
            "propagated from its source Series, so this is an enforced "
            "check.  Pass False to opt into mixed-frequency masking "
            "explicitly; the choice is recorded in lineage either way."
        ),
    )
    require_matching_missingness: bool = Field(
        default=True,
        description=(
            "OPR11 structural-metadata control, exposed for uniformity. "
            "An EventSet is a boolean mask with no missingness regime, "
            "so there is no second policy to compare against — the flag "
            "is strict-by-default and recorded in lineage, but the check "
            "is vacuous and the output inherits the input Series's "
            "missingness_policy unchanged.  See the module docstring for "
            "why the missingness axis is not modelled on the EventSet."
        ),
    )


__all__ = ["ApplyMaskParams"]
