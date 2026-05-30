"""Pydantic parameter schema for rolling_regression.

The operator's V1 surface is intentionally minimal:

  - ``window``       trailing-window length in rows of the aligned panel
  - ``min_periods``  minimum observations within window for a non-NaN fit
  - ``lhs_basis``    raw_value | level_change   how the LHS series is
                     prepared before regression
  - ``rhs_basis``    raw_value | level_change   how the RHS series is
                     prepared before regression
  - ``add_constant`` whether to fit an intercept (default True)

V1 simplification: ``lhs_basis`` and ``rhs_basis`` MUST match —
mixing level/change regressions is a desk-meaningful but easy-to-
misuse variant; we ship the canonical "both raw OR both level_change"
form first and lift the restriction once a real workflow needs it.
The validator enforces parity at construction time.

Solver / condition-number / multi-regressor extensions are deferred
to follow-up operators per the operator-architecture closed-family
discipline.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RollingRegressionParams(BaseModel):
    """Parameters for ``rolling_regression`` (V1 surface)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    window: int = Field(
        ...,
        ge=2,
        description=(
            "Trailing-window length, in rows of the aligned panel.  "
            "Each rolling fit consumes the most-recent ``window`` "
            "observations.  Must be >= ``min_periods``."
        ),
    )
    min_periods: int = Field(
        default=30,
        ge=2,
        description=(
            "Minimum non-NaN observations within a window required to "
            "emit a non-NaN fit.  Default 30 — matches the bundled "
            "rolling_regression primitive's V1 convention.  Smaller "
            "values produce noisier early estimates."
        ),
    )
    lhs_basis: Literal["raw_value", "level_change"] = Field(
        default="level_change",
        description=(
            "How the LHS (target / dependent) series is prepared "
            "before regression.  ``raw_value`` regresses lhs LEVEL on "
            "rhs (the chosen basis).  ``level_change`` regresses "
            "lhs.diff() — the canonical case for change-vs-change "
            "rolling beta (e.g. UST yield CHANGES regressed on OIS "
            "rate CHANGES).  Default ``level_change``."
        ),
    )
    rhs_basis: Literal["raw_value", "level_change"] = Field(
        default="level_change",
        description=(
            "How the RHS (regressor) series is prepared before "
            "regression.  Must match ``lhs_basis`` in V1 — mixed "
            "level/change regressions ship as a separate operator "
            "when a real workflow demands them."
        ),
    )
    add_constant: bool = Field(
        default=True,
        description=(
            "Whether to fit an intercept (alpha).  Default True — "
            "matches the standard with-intercept convention used by "
            "every other rates analytic and by the ``rolling_ols`` "
            "compute primitive's default."
        ),
    )
    require_matching_frequency: bool = Field(
        default=False,
        description=(
            "OPR11 structural-metadata control (mirrors align_series / "
            "series_arithmetic).  When True, refuse to regress two "
            "Series whose frequency tags disagree.  Default False "
            "preserves rolling_regression's inner-join + dropna "
            "behaviour — a regression over the overlapping dates is "
            "well-defined across cadences; flip to True for the strict "
            "event-study discipline."
        ),
    )
    require_matching_missingness: bool = Field(
        default=False,
        description=(
            "OPR11 structural-metadata control.  When True, refuse to "
            "regress two Series whose missingness policies disagree.  "
            "Default False (see require_matching_frequency)."
        ),
    )
    condition_number_warning_threshold: float = Field(
        default=1e10,
        gt=0,
        description=(
            "Condition-number ceiling passed to rolling_ols: a window "
            "whose design matrix exceeds this is refused as near-singular "
            "(OPR7 — no longer a hidden constant).  Default 1e10."
        ),
    )

    @model_validator(mode="after")
    def _validate(self) -> "RollingRegressionParams":
        if self.window < self.min_periods:
            raise ValueError(
                f"window ({self.window}) must be >= min_periods "
                f"({self.min_periods}) — otherwise the rolling fit "
                "can never emit a non-NaN row."
            )
        if self.lhs_basis != self.rhs_basis:
            raise ValueError(
                f"V1 rolling_regression requires lhs_basis == "
                f"rhs_basis (got lhs_basis={self.lhs_basis!r}, "
                f"rhs_basis={self.rhs_basis!r}).  Mixed bases will "
                "ship as a separate operator when a real workflow "
                "demands them."
            )
        return self


__all__ = ["RollingRegressionParams"]
