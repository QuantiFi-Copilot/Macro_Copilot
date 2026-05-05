"""Pydantic parameter schema for align_series.

Per the operator architecture doc: consequential method variants are
exposed as caller-controlled parameters.  ``align_series`` exposes:

  - ``join_policy``        ∈ {inner, outer}     how to combine N indexes
  - ``fill_policy``        ∈ {raw, ffill}        what to do with post-join NaNs
  - ``fill_limit``         Optional[int]         max consecutive NaNs to fill
  - ``require_matching_frequency``    bool       enforce frequency-tag agreement
  - ``require_matching_missingness``  bool       enforce missingness-policy agreement
  - ``output_keys``        Optional[List[str]]   rename SeriesSet members in
                                                 declaration order (closes the
                                                 leaked-series_key gap Codex
                                                 surfaced on PR #87)

Phase 1A surface is intentionally minimal (build plan v5 — strip to the
Q1 minimum) plus the structural-metadata compatibility knobs the
operator-architecture doc requires.  Both compatibility knobs default
to ``True`` (strict): silently mixing daily and weekly series, or
silently mixing cleaned and raw series, are exactly the failure modes
the structured metadata exists to catch.  Pass ``False`` to opt into
mixed inputs explicitly; the choice is recorded in lineage either way.

``output_keys`` rationale
--------------------------
Without ``output_keys``, the SeriesSet's per-key dicts use each input
Series's ``series_key`` (= the bridge's lifted ``time_series.series_name``)
verbatim.  That coupling means downstream ``select_from_series_set``
nodes must know each primitive's wire-naming convention, which leaks
implementation detail into the template surface.  ``output_keys`` lets
the template author specify a stable, template-controlled name for each
input position (e.g. ``["signal", "target"]``) that downstream
``select_from_series_set`` nodes reference as a hard-coded literal —
restoring the workflow-architecture contract's "templates expose only
central analysis knobs, not wiring internals" discipline.

Deferred per the operator's planned_extensions:

  - ``left`` / ``right`` anchored joins
  - ``bfill`` and ``ffill_then_bfill`` policies
  - ``target_frequency`` resampling
  - explicit named-calendar parameter
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AlignSeriesParams(BaseModel):
    """Parameters for ``align_series`` (Phase 1A surface)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    join_policy: Literal["inner", "outer"] = "inner"
    fill_policy: Literal["raw", "ffill"] = "raw"
    fill_limit: Optional[int] = Field(default=None, ge=0)
    require_matching_frequency: bool = True
    require_matching_missingness: bool = True
    output_keys: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional list of output series_keys, one per input "
            "series in declaration order.  When supplied, the "
            "resulting SeriesSet's per-key dicts use these names "
            "instead of each input's ``series_key``.  Length must "
            "equal the number of inputs and entries must be "
            "unique.  When omitted (default), input series_keys "
            "flow through unchanged (backward-compatible)."
        ),
    )

    @model_validator(mode="after")
    def _validate_output_keys_unique(self) -> "AlignSeriesParams":
        if self.output_keys is None:
            return self
        if len(self.output_keys) != len(set(self.output_keys)):
            duplicates = sorted(
                k for k in set(self.output_keys)
                if self.output_keys.count(k) > 1
            )
            raise ValueError(
                f"AlignSeriesParams.output_keys: duplicate name(s) "
                f"{duplicates}; every output key must be unique."
            )
        for i, k in enumerate(self.output_keys):
            if not isinstance(k, str) or not k.strip():
                raise ValueError(
                    f"AlignSeriesParams.output_keys[{i}] must be a "
                    f"non-empty string; got {k!r}."
                )
        return self


__all__ = ["AlignSeriesParams"]
