"""Pydantic parameter schema for fit_kalman.

The operator's V1 surface is intentionally minimal:

  - ``target_key``             which SeriesSet member is the dependent y
  - ``signal_to_noise_ratio``  delta = Q / R — how fast the coefficients
                               are allowed to move (THE knob; no default)
  - ``basis``                  raw_value | level_change — how EVERY
                               member is prepared before the regression
  - ``add_constant``           whether to fit a time-varying intercept

``target_key`` and ``signal_to_noise_ratio`` are REQUIRED-no-default
(OPR8): which member is the target is an intent choice, and the SNR is
the load-bearing methodology knob — neither has an honest default, so
``params is None`` refuses outright at the operator.

V1 simplification (mirrors rolling_regression): ONE ``basis`` is applied
uniformly to the target AND every regressor — mixed level/change
regressions ship as a separate operator once a real workflow needs them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FitKalmanParams(BaseModel):
    """Parameters for ``fit_kalman`` (V1 surface)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_key: str = Field(
        ...,
        min_length=1,
        description=(
            "The SeriesSet member key that is the DEPENDENT series y "
            "(y = sum_j beta_j x_j + alpha + eps).  Every OTHER member is "
            "a regressor.  REQUIRED — which member is the target is an "
            "intent choice with no honest default; designating it wrong "
            "silently inverts the regression."
        ),
    )
    signal_to_noise_ratio: float = Field(
        ...,
        gt=0.0,
        description=(
            "delta = Q / R, the process-to-observation variance ratio — "
            "THE knob.  Larger ⇒ the coefficients are allowed to move "
            "faster (a more adaptive, noisier time-varying path); smaller "
            "⇒ a stiffer path that hugs the static OLS fit.  REQUIRED — "
            "the adaptivity is the core methodology choice and has no "
            "honest default (typical exploratory values: 1e-3 stiff … "
            "1e-1 fast)."
        ),
    )
    basis: Literal["raw_value", "level_change"] = Field(
        default="level_change",
        description=(
            "How EVERY member (target + all regressors) is prepared "
            "before the regression.  ``level_change`` regresses the "
            "first differences (the canonical change-vs-change case); "
            "``raw_value`` regresses levels.  Applied UNIFORMLY in V1 — "
            "mixed bases ship as a separate operator.  Default "
            "``level_change`` (matches rolling_regression)."
        ),
    )
    add_constant: bool = Field(
        default=True,
        description=(
            "Whether to fit a time-varying intercept (the ``alpha`` "
            "member of the output SeriesSet).  Default True — the "
            "standard with-intercept convention."
        ),
    )


__all__ = ["FitKalmanParams"]
