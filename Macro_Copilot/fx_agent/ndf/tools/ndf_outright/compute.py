"""FX NDF outright snapshot compute path.

Mirror of fx_agent/spot/tools/spot_levels/compute.py but for NDFs:

  1. Resolve (ndf_code, tenor) → vendor_ticker (e.g. "CCN+1M Curncy")
     via fx_agent.ndf._shared.ndf_vendor_ticker.
  2. Fetch the outright observation history from market_data_daily over
     the lookback window.
  3. Compute: latest outright, daily / weekly / monthly % changes,
     rolling 252-day z-score, trailing 252-day high / low / percentile,
     observation count.
  4. Return a typed FXNDFOutrightOutput.

Fail-loud: raises ValueError if no data is found (substrate primitive
contract — no silent empty envelope).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from fx_agent.ndf._shared import SUPPORTED_NDF_CODES, ndf_vendor_ticker
from fx_agent.ndf.tools.ndf_outright.schemas import (
    FXNDFOutrightInput,
    FXNDFOutrightMetrics,
    FXNDFOutrightOutput,
)
from shared.config import ToolConfig, load_tool_config


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"
_FROZEN_TRAILING_WINDOW = 252


def _safe_pct_change(series: pd.Series, periods: int) -> Optional[float]:
    if len(series) <= periods:
        return None
    old = series.iloc[-periods - 1]
    new = series.iloc[-1]
    if old is None or pd.isna(old) or old == 0:
        return None
    return float((new / old - 1.0) * 100.0)


def _round_optional(value: Optional[float], decimals: int) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    return round(float(value), decimals)


def _ndf_query() -> Any:
    return text(
        """
        SELECT
            d.trade_date,
            d.field_value::float AS field_value
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master im
            ON d.instrument_id = im.instrument_id
        WHERE im.instrument_type = 'fx_ndf'
          AND im.vendor_ticker = :ticker
          AND d.field_name = :field_name
          AND d.trade_date >= CURRENT_DATE - (:lookback_days || ' days')::interval
        ORDER BY d.trade_date ASC
        """
    )


def get_fx_ndf_outright(
    engine: Engine,
    params: FXNDFOutrightInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    ndf_code = params.ndf_code  # Pydantic Literal already validates
    underlying_pair = SUPPORTED_NDF_CODES[ndf_code]
    tenor = params.tenor
    vendor_ticker = ndf_vendor_ticker(ndf_code, tenor)

    default_field = config.convention_value("default_ndf_field")
    field_name = (params.field_name or default_field).upper().strip()

    z_window = int(config.convention_value("z_score_window_days"))
    z_min_periods = int(config.convention_value("z_score_min_periods"))
    z_ddof = int(config.convention_value("z_score_ddof"))
    trailing_window = int(config.convention_value("trailing_range_window_days"))
    if trailing_window != _FROZEN_TRAILING_WINDOW:
        raise NotImplementedError(
            "trailing_range_window_days is wire-frozen at 252 because "
            "FXNDFOutrightMetrics fields are named high_252d / low_252d / "
            "percentile_252d. See config.yaml methodology.planned_extensions."
        )

    daily_periods = int(config.convention_value("daily_change_periods"))
    weekly_periods = int(config.convention_value("weekly_change_periods"))
    monthly_periods = int(config.convention_value("monthly_change_periods"))
    outright_decimals = int(config.convention_value("outright_round_decimals"))
    pct_decimals = int(config.convention_value("pct_change_round_decimals"))
    z_decimals = int(config.convention_value("z_score_round_decimals"))
    percentile_decimals = int(config.convention_value("percentile_round_decimals"))

    with engine.connect() as conn:
        df = pd.read_sql(
            _ndf_query(),
            conn,
            params={
                "ticker": vendor_ticker,
                "field_name": field_name,
                "lookback_days": params.lookback_days,
            },
        )

    if df.empty:
        raise ValueError(
            f"No FX NDF outright data found for ticker={vendor_ticker!r}, "
            f"field={field_name!r}, lookback_days={params.lookback_days}"
        )

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")
    df = df.dropna(subset=["field_value"]).sort_values("trade_date")

    if df.empty:
        raise ValueError(
            f"FX NDF outright data for ticker={vendor_ticker!r} all dropped "
            "as non-numeric after cleaning — investigate the DB rows."
        )

    values = df["field_value"]
    current = float(values.iloc[-1])
    as_of_date = df["trade_date"].iloc[-1].strftime("%Y-%m-%d")

    trailing = values.tail(z_window)
    mean_252 = trailing.mean()
    std_252 = trailing.std(ddof=z_ddof)
    z_score = None
    if len(trailing) >= z_min_periods and std_252 and not pd.isna(std_252):
        z_score = float((current - mean_252) / std_252)

    range_values = values.tail(trailing_window)
    high_252d = float(range_values.max()) if not range_values.empty else None
    low_252d = float(range_values.min()) if not range_values.empty else None

    percentile_252d = None
    if high_252d is not None and low_252d is not None and high_252d != low_252d:
        percentile_252d = float((current - low_252d) / (high_252d - low_252d) * 100.0)

    metrics = FXNDFOutrightMetrics(
        as_of_date=as_of_date,
        ndf_code=ndf_code,
        underlying_pair=underlying_pair,
        vendor_ticker=vendor_ticker,
        tenor=tenor,
        current_outright=round(current, outright_decimals),
        daily_change_pct=_round_optional(
            _safe_pct_change(values, daily_periods), pct_decimals
        ),
        weekly_change_pct=_round_optional(
            _safe_pct_change(values, weekly_periods), pct_decimals
        ),
        monthly_change_pct=_round_optional(
            _safe_pct_change(values, monthly_periods), pct_decimals
        ),
        z_score=_round_optional(z_score, z_decimals),
        high_252d=_round_optional(high_252d, outright_decimals),
        low_252d=_round_optional(low_252d, outright_decimals),
        percentile_252d=_round_optional(percentile_252d, percentile_decimals),
        observation_count=int(len(df)),
    )

    return FXNDFOutrightOutput(current_metrics=metrics).model_dump()
