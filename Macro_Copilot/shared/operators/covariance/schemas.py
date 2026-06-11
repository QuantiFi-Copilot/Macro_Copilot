"""covariance — parameter schema.

Per OPR8: every consequential method variant is an explicit typed field;
nothing material is hidden in code.  Each field's schema default mirrors
the ``config.yaml`` default (the YAML is the authoritative source — the
operator resolves from it when ``params is None``; the schema default
exists so a partial-override call via the executor's
``params_class(**node.params)`` path stays ergonomic).  A test asserts
the two never diverge (OPR8).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CovarianceParams(BaseModel):
    """Parameters for the ``covariance`` operator.

    Variants:
      - ``ddof``        — delta degrees of freedom for the denominator
                          ``n_obs - ddof``: 1 (sample covariance,
                          Bessel-corrected — the default) or 0
                          (population covariance).
      - ``min_periods`` — minimum overlapping non-NaN observations
                          required (a sample covariance is undefined
                          below 2).

    Structural-metadata flags (OPR11), strict by default:
      - ``require_matching_units``       — a covariance is
        unit-BEARING (its semantic unit is the product of the two
        inputs' units), so mixed-unit inputs are refused unless the
        caller opts out explicitly; the opt-out is recorded in lineage.
      - ``require_matching_frequency``
      - ``require_matching_missingness``
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ddof: int = Field(default=1, ge=0, le=1)
    min_periods: int = Field(default=2, ge=2)
    require_matching_units: bool = True
    require_matching_frequency: bool = True
    require_matching_missingness: bool = True


__all__ = ["CovarianceParams"]
