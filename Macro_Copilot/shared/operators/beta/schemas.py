"""beta — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field; each field's schema default mirrors the ``config.yaml`` default
(the YAML is the authoritative source — the operator resolves from it
when ``params is None``).  A test asserts the two never diverge (OPR8).

Multi-input operator (2 Series); CARRIES the OPR11
``require_matching_*`` controls for frequency + missingness (strict by
default).  Deliberately NO ``require_matching_units`` flag: a
regression slope absorbs the regressor's units (β is in
lhs_units / rhs_units), so the operator is unit-INVARIANT across its
inputs — both units are recorded in lineage and the output is a
``RATIO``-tagged ScalarMetric.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BetaParams(BaseModel):
    """Parameters for the ``beta`` operator.

    Variants:
      - ``add_constant`` — fit an intercept α (default).  When False
        the slope is fit through the origin.
      - ``min_periods`` — minimum overlapping non-NaN observations
        required to fit.  Floor 3: with an intercept, n = 2 fits the
        line exactly and the slope carries no information beyond the
        two points.
      - ``condition_number_threshold`` — refuse the fit when the
        design matrix's condition number exceeds this (a numerically
        meaningless slope must not ship as a ScalarMetric).

    Structural-metadata flags (OPR11), strict by default:
      - ``require_matching_frequency``
      - ``require_matching_missingness``
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    add_constant: bool = True
    min_periods: int = Field(default=3, ge=3)
    condition_number_threshold: float = Field(default=1e10, gt=0)
    require_matching_frequency: bool = True
    require_matching_missingness: bool = True


__all__ = ["BetaParams"]
