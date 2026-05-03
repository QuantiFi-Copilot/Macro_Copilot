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

Adding a policy requires adding a new dataclass here so the operator-
layer compatibility checks remain exhaustive.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

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


# Discriminated union — Pydantic uses the ``kind`` field to pick the
# right model on deserialization.  Closed by construction; adding a
# policy requires editing this union and the imports above.
MissingnessPolicy = Annotated[
    Union[CleanSingleSeriesV1, RawNoCleaning],
    Field(discriminator="kind"),
]


__all__ = [
    "MissingnessPolicy",
    "CleanSingleSeriesV1",
    "RawNoCleaning",
]
