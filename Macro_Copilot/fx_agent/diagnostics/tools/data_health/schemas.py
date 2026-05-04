from __future__ import annotations

from pydantic import BaseModel, Field


class FXDataHealthInput(BaseModel):
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description="Calendar days used when counting recent observations.",
    )
    stale_after_days: int = Field(
        default=5,
        ge=1,
        le=365,
        description="Series is stale if it lags the dataset as-of date by more than this.",
    )
    field_name: str = Field(
        default="PX_LAST",
        description="Market-data field used for coverage checks.",
    )


class FXDataFamilyCoverage(BaseModel):
    family: str
    instrument_type: str
    instruments: int
    series_with_data: int
    latest_date: str | None = None
    stale_series: int = 0


class FXDataHealthPairCoverage(BaseModel):
    pair: str
    has_spot: bool
    forward_tenors: list[str] = Field(default_factory=list)
    vol_tenors: list[str] = Field(default_factory=list)
    latest_spot_date: str | None = None
    latest_forward_date: str | None = None
    latest_vol_date: str | None = None
    observation_count: int = 0
    status: str
    missing: list[str] = Field(default_factory=list)


class FXDataHealthRiskProxyCoverage(BaseModel):
    ticker: str
    latest_date: str | None = None
    observation_count: int = 0
    status: str


class FXDataHealthOutput(BaseModel):
    as_of_date: str | None = None
    field_name: str
    status: str
    summary: dict[str, int]
    families: list[FXDataFamilyCoverage]
    pairs: list[FXDataHealthPairCoverage]
    risk_proxies: list[FXDataHealthRiskProxyCoverage]
    missing_pairs: list[str]
    stale_series: list[str]

