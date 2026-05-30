"""shared.artifacts.missingness — typed family for missingness policy.

Per build plan v5 / R3: every artifact carries a structured
``MissingnessPolicy`` rather than a free-form string.  This lets
operators check compatibility (``align_series`` can refuse to silently
combine a ``RawNoCleaning`` series with a ``CleanSingleSeriesV1``
series unless the caller explicitly opts in).

The closed family for v1:

  - ``CleanSingleSeriesV1``  — the policy applied by
    ``shared.analytics.levels.clean_single_series`` (sort + dedup +
    ffill_limit).  Used when the adapter consumes the output of
    ``fetch_single_tenor → clean_single_series``.
  - ``RawNoCleaning``        — explicit "no cleaning was applied"
    marker.  Adapter requires this when the caller deliberately
    bypasses ``clean_single_series``.
  - ``AlignSeriesFFillV1``   — wraps an upstream policy when
    ``align_series`` materially changed the payload by ffilling
    alignment-introduced gaps.  Without this wrapper the output's
    metadata would still claim ``CleanSingleSeriesV1`` (or
    ``RawNoCleaning``) even though the operator just imputed cells —
    metadata-dishonest.

Adding a policy requires adding a new dataclass here so the operator-
layer compatibility checks remain exhaustive.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class CleanSingleSeriesV1(BaseModel):
    """Missingness policy matching ``clean_single_series``.

    The fields capture the parameters that were actually used so the
    operator layer can detect drift (e.g., aligning two series that
    were cleaned with different ffill_limits).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["clean_single_series_v1"] = "clean_single_series_v1"
    ffill_limit: int = Field(..., ge=0)
    drop_nan: bool = True
    dedup_keep: Literal["first", "last"] = "last"


class RawNoCleaning(BaseModel):
    """Marker policy for series that bypass ``clean_single_series``.

    Operators that take ≥2 inputs MUST explicitly accept this policy
    (or refuse it) — silent mixing of ``Raw`` and ``CleanSingleSeriesV1``
    series violates the cross-cutting structural-metadata contract from
    ``operator_architecture.md``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["raw_no_cleaning"] = "raw_no_cleaning"


class AlignSeriesFFillV1(BaseModel):
    """Missingness policy emitted by ``align_series`` when
    ``fill_policy='ffill'`` materially changed the payload by filling
    alignment-introduced gaps.

    Wraps the upstream policy (what the input declared before alignment)
    so consumers can introspect the full chain.  Without this wrapper
    the output would still claim its upstream policy even though the
    operator just imputed cells.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["align_series_ffill_v1"] = "align_series_ffill_v1"
    upstream: "MissingnessPolicy"
    fill_limit: Optional[int] = None


class CombinedMissingnessV1(BaseModel):
    """Missingness policy emitted when a multi-artifact operator combines
    inputs with DIFFERENT upstream policies under an explicit lenient
    opt-out (``require_matching_missingness=False``).

    Honest record (OPR11) that the output mixes regimes, rather than
    silently adopting one input's policy and discarding the others'.
    ``components`` preserves every input's declared policy in input
    order so a consumer can introspect the mix.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["combined_missingness_v1"] = "combined_missingness_v1"
    components: tuple["MissingnessPolicy", ...] = Field(..., min_length=2)


# Discriminated union — Pydantic uses the ``kind`` field to pick the
# right model on deserialization.  Closed by construction; adding a
# policy requires editing this union and the imports above.
MissingnessPolicy = Annotated[
    Union[
        CleanSingleSeriesV1,
        RawNoCleaning,
        AlignSeriesFFillV1,
        CombinedMissingnessV1,
    ],
    Field(discriminator="kind"),
]

# Resolve the recursive forward references (AlignSeriesFFillV1.upstream,
# CombinedMissingnessV1.components).
AlignSeriesFFillV1.model_rebuild()
CombinedMissingnessV1.model_rebuild()


__all__ = [
    "MissingnessPolicy",
    "CleanSingleSeriesV1",
    "RawNoCleaning",
    "AlignSeriesFFillV1",
    "CombinedMissingnessV1",
]
