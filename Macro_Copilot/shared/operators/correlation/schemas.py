"""correlation — parameter schema.

Per OPR8: every consequential method variant is an explicit typed field;
nothing material is hidden in code. Each field's schema default mirrors
the ``config.yaml`` default (the YAML is the authoritative source — the
operator resolves from it when ``params is None``; the schema default
exists so a partial-override call via the executor's
``params_class(**node.params)`` path stays ergonomic). A test asserts
the two never diverge (OPR8).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# The closed set of correlation methods.  ``kendall`` is declared but
# NOT implemented in v1 — the operator refuses it cleanly (OPR8 honest
# refusal); it is listed so the surface is honest about what is planned.
CorrelationMethod = Literal["pearson", "spearman", "kendall"]


class CorrelationParams(BaseModel):
    """Parameters for the ``correlation`` operator.

    Variants:
      - ``method``      — pearson (linear) | spearman (rank) | kendall
                          (planned, refuses).
      - ``min_periods`` — minimum overlapping non-NaN observations
                          required (a correlation is undefined below 2).

    Structural-metadata flags (OPR11), strict by default:
      - ``require_matching_frequency``
      - ``require_matching_missingness``
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: CorrelationMethod = "pearson"
    min_periods: int = Field(default=2, ge=2)
    require_matching_frequency: bool = True
    require_matching_missingness: bool = True


__all__ = ["CorrelationParams", "CorrelationMethod"]
