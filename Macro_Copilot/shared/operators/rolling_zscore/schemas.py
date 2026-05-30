"""rolling_zscore — parameter schema.

Per OPR8: every consequential method variant is an explicit typed field;
nothing material is hidden in code.  Each field's schema default mirrors
the ``config.yaml`` default (the YAML is the authoritative source — the
operator resolves from it when ``params is None``; the schema default
exists so a partial-override call via the executor's
``params_class(**node.params)`` path stays ergonomic).  A test asserts
the two never diverge (OPR8).

Single-input operator (Series → Series); does NOT carry
``require_matching_*`` flags (those are OPR11 controls for ≥2-input
operators).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RollingZscoreParams(BaseModel):
    """Parameters for the ``rolling_zscore`` operator.

    Variants:
      - ``window``           — trailing-window length, in rows of the
                               input Series (>= 2).
      - ``min_periods``      — minimum non-NaN observations within a
                               window required to emit a non-NaN z; when
                               ``None`` the operator defaults to
                               ``window`` (the strict, no-partial choice).
      - ``ddof``             — delta degrees of freedom for the rolling
                               standard deviation; ``1`` (sample,
                               default) or ``0`` (population).
      - ``look_ahead_safe``  — when ``True`` (default), the rolling
                               mean/std are shifted by one period so
                               the z at time ``t`` uses only data
                               <= ``t-1``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    window: int = Field(default=60, ge=2)
    min_periods: Optional[int] = Field(default=None, ge=1)
    ddof: int = Field(default=1, ge=0, le=1)
    look_ahead_safe: bool = True

    @model_validator(mode="after")
    def _validate(self) -> "RollingZscoreParams":
        if self.min_periods is not None and self.min_periods > self.window:
            raise ValueError(
                f"min_periods={self.min_periods} cannot exceed window="
                f"{self.window} — otherwise the rolling fit can never "
                "emit a non-NaN row."
            )
        return self


__all__ = ["RollingZscoreParams"]
