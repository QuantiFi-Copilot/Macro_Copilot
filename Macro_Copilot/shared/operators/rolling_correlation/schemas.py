"""rolling_correlation — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field; the closed set of correlation methods mirrors ``correlation``
so the two operators are directly substitutable.  Each field's schema
default mirrors the ``config.yaml`` default.

Multi-input operator (≥2 Series); CARRIES the two ``require_matching_*``
OPR11 controls (strict by default — same discipline as ``correlation``).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# Same closed set as ``correlation``: pearson + spearman implemented,
# kendall declared-but-unbuilt → NotImplementedError (OPR8 honest
# refusal).  Mirrors the sibling operator so callers can switch
# between the full-sample (``correlation``) and rolling
# (``rolling_correlation``) forms without surprise.
RollingCorrelationMethod = Literal["pearson", "spearman", "kendall"]


class RollingCorrelationParams(BaseModel):
    """Parameters for the ``rolling_correlation`` operator.

    Variants:
      - ``method``      — pearson (linear) | spearman (rank) | kendall
                          (planned, refuses cleanly).
      - ``window``      — trailing-window length, in rows of the
                          (shared) input index.
      - ``min_periods`` — minimum overlapping non-NaN pairs within
                          a window required to emit a non-NaN
                          coefficient; when ``None`` defaults to
                          ``window``.

    Structural-metadata flags (OPR11), strict by default:
      - ``require_matching_frequency``
      - ``require_matching_missingness``
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: RollingCorrelationMethod = "pearson"
    window: int = Field(default=60, ge=2)
    min_periods: Optional[int] = Field(default=None, ge=2)
    require_matching_frequency: bool = True
    require_matching_missingness: bool = True

    @model_validator(mode="after")
    def _validate(self) -> "RollingCorrelationParams":
        if self.min_periods is not None and self.min_periods > self.window:
            raise ValueError(
                f"min_periods={self.min_periods} cannot exceed window="
                f"{self.window} — otherwise the rolling correlation can "
                "never emit a non-NaN row."
            )
        return self


__all__ = ["RollingCorrelationParams", "RollingCorrelationMethod"]
