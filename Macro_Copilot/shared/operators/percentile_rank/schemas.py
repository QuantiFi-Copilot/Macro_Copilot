"""percentile_rank — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field; the closed set of tie-handling ``method`` values is a Literal
mirroring ``scipy.stats.percentileofscore``'s supported kinds.  Each
field's schema default mirrors the ``config.yaml`` default; a test
asserts the two never diverge.

Single-input operator (Series → Series); does NOT carry
``require_matching_*`` flags (those are OPR11 controls for ≥2-input
operators).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# Closed set of tie-handling methods.  Mirrors
# ``scipy.stats.percentileofscore``'s ``kind`` parameter.  All four
# are IMPLEMENTED in v1 — the operator passes ``method`` through to
# scipy directly.
PercentileRankMethod = Literal["mean", "weak", "strict", "rank"]


class PercentileRankParams(BaseModel):
    """Parameters for the ``percentile_rank`` operator.

    Variants:
      - ``window``           — trailing-window length, in rows of the
                               input Series (>= 2).  ``None`` ⇒
                               expanding window (rank vs ALL history).
      - ``min_periods``      — minimum non-NaN observations in the
                               history slice required to emit a
                               non-NaN rank (>= 2 — a percentile rank
                               needs at least two points to be
                               distinguishable).
      - ``method``           — tie-handling method
                               (``mean`` | ``weak`` | ``strict`` |
                               ``rank``).  See
                               ``scipy.stats.percentileofscore`` for
                               exact semantics.
      - ``look_ahead_safe``  — when ``True`` (default), the history
                               STRICTLY excludes the current
                               observation (rank at ``t`` uses
                               ``[t-window, t-1]``).  When ``False``,
                               the current observation IS included
                               (``[t-window+1, t]``, the in-sample
                               rank).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    window: Optional[int] = Field(default=252, ge=2)
    min_periods: int = Field(default=20, ge=2)
    method: PercentileRankMethod = "mean"
    look_ahead_safe: bool = True

    @model_validator(mode="after")
    def _validate(self) -> "PercentileRankParams":
        if self.window is not None and self.min_periods > self.window:
            raise ValueError(
                f"min_periods={self.min_periods} cannot exceed window="
                f"{self.window} — otherwise the rank can never emit a "
                "non-NaN row."
            )
        return self


__all__ = ["PercentileRankParams", "PercentileRankMethod"]
