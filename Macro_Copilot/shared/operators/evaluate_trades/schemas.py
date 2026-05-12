"""Pydantic parameter schema for ``evaluate_trades``.

Phase 1 PR 12.

V1 keeps the user-facing surface small.  ``pricing_convention``,
``frictionless``, and ``financing_assumption`` default to None and
resolve from ``config.yaml`` — same convention as
``threshold_events`` and ``construct_trades``.

The brief calls V1 "frictionless V1 with methodology disclosure."
The disclosure piece is structural — the YAML's defaults pin the
disclosures, the operator raises ``NotImplementedError`` if the
caller tries to override them, and the workspace methodology card
renders the values from the persisted lineage step.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


PricingConvention = Literal["mid", "bid", "ask"]
FinancingAssumption = Literal["none", "constant_rate", "overnight_repo_curve"]


class EvaluateTradesParams(BaseModel):
    """Parameters for ``evaluate_trades`` (Phase 1 V1 surface)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

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
            "V1 ships ``none`` (no financing carry).  Other values "
            "raise NotImplementedError.  None → resolved from "
            "config.yaml."
        ),
    )


__all__ = [
    "PricingConvention",
    "FinancingAssumption",
    "EvaluateTradesParams",
]
