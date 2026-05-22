"""Pydantic parameter schema for ``construct_trades``.

Phase 1 PR 12.

The schema's load-bearing pieces:

  - ``LegSpecInput``: user-facing leg row (instrument_key + weight).
    The operator converts this into a frozen
    ``shared.artifacts.trades.LegSpec`` after validating sign
    consistency.  Two layers (user-input vs frozen-artifact-leg)
    so the user-input shape is forgiving while the persisted
    leg is strict.

  - ``ConstructTradesParams``: holding rule + holding window +
    legs + methodology tag.  ``holding_rule`` and
    ``holding_window_days`` default to None so the operator
    resolves them from ``config.yaml`` at runtime — the same
    pattern ``threshold_events`` uses.
"""

from __future__ import annotations

from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator


HoldingRule = Literal["fixed_horizon", "signal_exit", "stop_loss"]
LegConstructionRule = Literal[
    "equal_weight_signed", "gross_unity", "risk_parity",
]


class LegSpecInput(BaseModel):
    """User-facing leg input.  Converted to the frozen
    ``shared.artifacts.trades.LegSpec`` inside the operator.

    Difference from ``LegSpec``: this schema does NOT carry the
    redundant ``side`` field — the operator derives it from the
    sign of ``weight`` and stamps both onto the frozen leg.  The
    artifact-side ``LegSpec`` keeps both so persisted trades are
    self-describing without back-derivation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument_key: str = Field(..., min_length=1)
    weight: float
    units: Optional[str] = None

    @model_validator(mode="after")
    def _check_weight_nonzero(self) -> "LegSpecInput":
        # Zero-weight legs are meaningless and almost always a caller
        # bug.  Reject explicitly so the failure surfaces here.
        if self.weight == 0.0:
            raise ValueError(
                f"LegSpecInput weight cannot be zero "
                f"(instrument_key={self.instrument_key!r})."
            )
        return self


class ConstructTradesParams(BaseModel):
    """Parameters for ``construct_trades`` (Phase 1 V1 surface).

    ``holding_rule`` and ``holding_window_days`` default to ``None``
    so the YAML is authoritative.  When None, the operator resolves
    them from ``config.default_value(...)`` at runtime — same
    pattern as ``threshold_events`` and PR 9's tool-config-resolution.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    legs: Tuple[LegSpecInput, ...] = Field(
        ...,
        min_length=1,
        description=(
            "One or more leg specifications.  Each leg's signed "
            "weight is preserved as-is into the persisted trade "
            "(no portfolio-level normalisation in V1)."
        ),
    )
    holding_rule: Optional[HoldingRule] = Field(
        default=None,
        description=(
            "Trade exit policy.  V1 ships ``fixed_horizon`` only; "
            "``signal_exit`` and ``stop_loss`` raise "
            "NotImplementedError.  None → resolved from config.yaml."
        ),
    )
    holding_window_days: Optional[int] = Field(
        default=None,
        ge=1,
        le=252,
        description=(
            "Business-day holding window for fixed_horizon.  "
            "Required when holding_rule resolves to fixed_horizon; "
            "ignored for other holding rules (which would in any "
            "case raise NotImplementedError in V1)."
        ),
    )
    leg_construction_rule: Optional[LegConstructionRule] = Field(
        default=None,
        description=(
            "How leg weights are interpreted.  V1 ships "
            "``equal_weight_signed`` (pass-through).  Others raise "
            "NotImplementedError."
        ),
    )

    @model_validator(mode="after")
    def _validate_legs(self) -> "ConstructTradesParams":
        # Reject duplicate instrument_keys at the input layer.  The
        # frozen ``LegSpec`` re-validates this at construct time,
        # but failing at the parameter schema surfaces the caller
        # bug earlier in the stack.
        keys = [leg.instrument_key for leg in self.legs]
        if len(set(keys)) != len(keys):
            raise ValueError(
                f"ConstructTradesParams.legs has duplicate "
                f"instrument_keys: {keys}.  Combine duplicates at "
                "the caller (sum weights for the same instrument)."
            )
        return self


__all__ = [
    "HoldingRule",
    "LegConstructionRule",
    "LegSpecInput",
    "ConstructTradesParams",
]
