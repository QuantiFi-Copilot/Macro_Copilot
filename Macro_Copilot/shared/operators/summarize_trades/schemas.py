"""Pydantic parameter schema for ``summarize_trades``.

Phase 1 PR 12.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


PnLAggregationMethod = Literal["final_pnl", "cumulative_max", "time_weighted"]
EmptyTradesetPolicy = Literal["nan_metrics", "raise"]


class SummarizeTradesParams(BaseModel):
    """Parameters for ``summarize_trades``.

    All fields default to None → resolved from ``config.yaml`` at
    runtime.  The annualisation factor ``trading_days_per_year``
    + the holding-window read from the upstream TradeSet's lineage
    drive Sharpe annualisation; the operator doesn't expose
    ``holding_window`` directly because it's redundant with what
    the lineage already records.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    trading_days_per_year: Optional[int] = Field(
        default=None, ge=1,
        description=(
            "Trading-days-per-year factor for Sharpe annualisation.  "
            "None → config default (252)."
        ),
    )
    pnl_aggregation_method: Optional[PnLAggregationMethod] = Field(
        default=None,
        description=(
            "How per-trade P&L is summarised.  V1 ships ``final_pnl`` "
            "(last non-NaN value per trade).  Others raise "
            "NotImplementedError."
        ),
    )
    empty_tradeset_policy: Optional[EmptyTradesetPolicy] = Field(
        default=None,
        description=(
            "Behaviour on an empty input.  ``nan_metrics`` emits a "
            "single-row Panel with NaN values; ``raise`` aborts."
        ),
    )


__all__ = [
    "PnLAggregationMethod",
    "EmptyTradesetPolicy",
    "SummarizeTradesParams",
]
