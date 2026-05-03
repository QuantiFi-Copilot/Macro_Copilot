from __future__ import annotations

from pydantic import BaseModel, Field


class FXMacroRiskOverlayInput(BaseModel):
    """Parameters for overlaying one FX pair with macro risk proxies."""

    pair: str = Field(
        ...,
        description="FX pair identifier, e.g. 'EURUSD', 'GBPUSD', 'USDJPY'.",
    )
    lookback_days: int = Field(
        default=365,
        ge=90,
        le=7300,
        description="Calendar days of history used for proxy context.",
    )
    correlation_window_observations: int = Field(
        default=63,
        ge=20,
        le=252,
        description="Observation window for return correlation.",
    )
    field_name: str = Field(
        default="PX_LAST",
        description="Bloomberg field to query. Defaults to PX_LAST.",
    )


class FXMacroRiskProxyRow(BaseModel):
    ticker: str
    label: str
    proxy_family: str
    as_of_date: str
    level: float
    daily_change_pct: float | None
    monthly_change_pct: float | None
    three_month_change_pct: float | None
    z_score: float | None
    correlation_to_pair: float | None


class FXMacroRiskOverlayOutput(BaseModel):
    pair: str
    as_of_date: str
    spot: float
    risk_regime: str
    regime_score: float
    summary: str
    implications: list[str]
    proxy_rows: list[FXMacroRiskProxyRow]
