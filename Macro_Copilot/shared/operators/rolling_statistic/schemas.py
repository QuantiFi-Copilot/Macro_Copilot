"""rolling_statistic — parameter schema.

Per OPR8: every consequential method variant is an explicit typed field;
the chosen statistic is a closed Literal so the validator + caller see
the full set up front.  Each field's schema default mirrors the
``config.yaml`` default; a test asserts the two never diverge.

Single-input operator (Series → Series); does NOT carry
``require_matching_*`` flags (those are OPR11 controls for ≥2-input
operators).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# Closed set of statistic variants.  All five are IMPLEMENTED in v1 —
# unlike correlation's ``kendall``, this operator does not currently
# declare any honest-refusal placeholders.  New statistics ship as
# additions to BOTH this Literal AND the operator's implemented set
# in lock-step (OPR8).
RollingStatisticName = Literal["mean", "std", "min", "max", "sum"]


class RollingStatisticParams(BaseModel):
    """Parameters for the ``rolling_statistic`` operator.

    Variants:
      - ``statistic``       — closed set ``{mean, std, min, max, sum}``.
      - ``window``          — trailing-window length, in rows of the
                              input Series (>= 1).
      - ``min_periods``     — minimum non-NaN observations within a
                              window required to emit a non-NaN value;
                              when ``None`` the operator defaults to
                              ``window`` (the strict choice).
      - ``ddof``            — delta degrees of freedom used ONLY when
                              ``statistic='std'``.
      - ``look_ahead_safe`` — when ``True``, the rolling output is
                              shifted by one period so the value at
                              ``t`` reflects data <= ``t-1``.  Default
                              ``False`` because a rolling reducer is a
                              smoothing transform, not a forward signal.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    statistic: RollingStatisticName = "mean"
    window: int = Field(default=20, ge=1)
    min_periods: Optional[int] = Field(default=None, ge=1)
    ddof: int = Field(default=1, ge=0, le=1)
    look_ahead_safe: bool = False

    @model_validator(mode="after")
    def _validate(self) -> "RollingStatisticParams":
        if self.min_periods is not None and self.min_periods > self.window:
            raise ValueError(
                f"min_periods={self.min_periods} cannot exceed window="
                f"{self.window} — otherwise the rolling reducer can "
                "never emit a non-NaN row."
            )
        # statistic='std' is undefined on a single observation —
        # window of 1 with std is incoherent.  Other statistics work
        # fine at window=1 (rolling mean/min/max/sum of one point IS
        # that point), so the floor is statistic-specific.
        if self.statistic == "std" and self.window < 2:
            raise ValueError(
                f"statistic='std' requires window >= 2 (got "
                f"{self.window}); a single-observation std is undefined."
            )
        return self


__all__ = ["RollingStatisticParams", "RollingStatisticName"]
