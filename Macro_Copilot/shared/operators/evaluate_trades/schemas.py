"""Pydantic parameter schema for ``evaluate_trades``.

Phase 1 PR 12 (operator) + PR 19 (financing extension).

PR 19 adds:
  - ``financing_assumption="external_series"`` — caller passes a
    ``financing_rate_panel`` from the ``compute_financing_rate_tool``
    primitive; per-leg daily carry is added to the price-change P&L.
  - ``financing_basis`` — day-count basis for the accrual fraction.

Backwards-compatibility invariants (PR 12 contract):
  - ``financing_assumption=None`` → resolves to config default
    (``none`` unless overridden).
  - ``financing_assumption="none"`` → produces the IDENTICAL
    bit-for-bit Panel as PR 12 did (no financing carry).
  - All existing PR 12 callers / tests keep their behaviour.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from shared.artifacts.types import Panel


PricingConvention = Literal["mid", "bid", "ask"]
# PR 19: added ``external_series`` for the new financing path.  The
# pre-existing values stay so the closed enum remains backward-
# compatible.
FinancingAssumption = Literal[
    "none",
    "constant_rate",
    "overnight_repo_curve",
    "external_series",
]
DayCountBasis = Literal["act_360", "act_365", "act_act_isda"]


class EvaluateTradesParams(BaseModel):
    """Parameters for ``evaluate_trades``."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    pricing_convention: Optional[PricingConvention] = Field(
        default=None,
        description=(
            "How leg prices are pulled from the price Panel.  V1 ships "
            "``mid`` only; ``bid``/``ask`` raise NotImplementedError.  "
            "None → resolved from config.yaml."
        ),
    )
    frictionless: Optional[bool] = Field(
        default=None,
        description=(
            "V1 is hard-coded frictionless.  Passing False raises "
            "NotImplementedError.  None → resolved from config.yaml."
        ),
    )
    financing_assumption: Optional[FinancingAssumption] = Field(
        default=None,
        description=(
            "How financing carry is included in per-trade P&L.  None → "
            "resolved from config.yaml (default ``none`` preserves the "
            "PR 12 frictionless contract).  ``external_series`` requires "
            "``financing_rate_panel`` to be supplied.  ``constant_rate`` "
            "/ ``overnight_repo_curve`` raise NotImplementedError — the "
            "caller should build a financing-rate Series via the "
            "compute_financing_rate_tool primitive and pass it through "
            "``financing_rate_panel`` with assumption=``external_series``."
        ),
    )
    financing_rate_panel: Optional[Panel] = Field(
        default=None,
        description=(
            "REQUIRED when financing_assumption=``external_series``.  A "
            "Panel produced by the compute_financing_rate_tool primitive "
            "whose single column carries the per-date rate in PERCENT.  "
            "Index must overlap the price Panel's index; non-overlapping "
            "dates contribute zero financing (NO silent fabrication)."
        ),
    )
    financing_basis: Optional[DayCountBasis] = Field(
        default=None,
        description=(
            "Day-count basis for the per-day accrual fraction.  None → "
            "resolved from config.yaml (``act_360`` — sovereign repo "
            "convention).  Ignored when financing_assumption=``none``."
        ),
    )


__all__ = [
    "PricingConvention",
    "FinancingAssumption",
    "DayCountBasis",
    "EvaluateTradesParams",
]
