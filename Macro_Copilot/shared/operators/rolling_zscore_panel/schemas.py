"""Pydantic parameter schema for rolling_zscore_panel.

The operator's V1 surface is intentionally minimal and z-score-only:

  - ``window``       trailing-window length in rows of the Panel
  - ``min_periods``  minimum observations within window for a non-NaN
                     z-score
  - ``ddof``         delta-degrees-of-freedom for the rolling std
                     (1 = sample std, 0 = population std)

Scope discipline (operator-architecture review 2026-05-28): this
operator emits z-scores ONLY. Cross-sectional rank and percentile
are DISTINCT structural families (ranking, not normalization) and
ship as separate operators (``cross_sectional_rank`` is already a
Pass example in docs/architecture/operator_architecture.md). Folding
them in here would be a wastebasket pattern the architecture doc
explicitly warns against.

``window`` is REQUIRED (no default) — there is no universal right
lookback, and the choice is methodologically consequential, so the
caller must declare it (same discipline ``rolling_regression`` uses
for its ``window``). ``min_periods`` and ``ddof`` carry config
defaults.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RollingZscorePanelParams(BaseModel):
    """Parameters for ``rolling_zscore_panel`` (V1 surface)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    window: int = Field(
        ...,
        ge=2,
        description=(
            "Trailing-window length, in rows of the Panel. Each "
            "column's z-score at row t uses the most-recent "
            "``window`` observations up to and including t. Must be "
            ">= ``min_periods``. REQUIRED — no universal default; "
            "the FX scanners pass window=252 (1y daily) but that "
            "lives in the FX-side config, not here."
        ),
    )
    min_periods: int = Field(
        default=60,
        ge=2,
        description=(
            "Minimum non-NaN observations within a window required "
            "to emit a non-NaN z-score. Default 60 — matches the FX "
            "scanner convention (a 1y window needs ~a quarter of "
            "data before the z-score is trustworthy). Below this, "
            "the row's z-score is NaN."
        ),
    )
    ddof: int = Field(
        default=1,
        ge=0,
        description=(
            "Delta-degrees-of-freedom for the rolling std. Default 1 "
            "(sample std) — matches the FX scanner / vol_z_score "
            "convention. 0 = population std."
        ),
    )

    @model_validator(mode="after")
    def _window_ge_min_periods(self) -> "RollingZscorePanelParams":
        if self.window < self.min_periods:
            raise ValueError(
                f"window ({self.window}) must be >= min_periods "
                f"({self.min_periods})."
            )
        if self.ddof >= self.window:
            raise ValueError(
                f"ddof ({self.ddof}) must be < window ({self.window}) "
                "or every rolling std is undefined."
            )
        return self


__all__ = ["RollingZscorePanelParams"]
