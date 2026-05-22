from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from fx_agent.forwards._shared import (
    points_to_spot_units,
    tenor_days_from_config,
)
from fx_agent.forwards.tools.fx_carry.schemas import (
    FXCarryInput,
    FXCarryOutput,
    FXCarryRow,
)
from shared.config import ToolConfig, load_tool_config


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


def _carry_query() -> Any:
    return text(
        """
        WITH latest_spot AS (
            SELECT
                im.attributes ->> 'pair' AS pair,
                d.trade_date,
                d.field_value::float AS spot,
                ROW_NUMBER() OVER (
                    PARTITION BY im.instrument_id
                    ORDER BY d.trade_date DESC
                ) AS rn
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_spot'
              AND d.field_name = :spot_field
        ),
        latest_fwd AS (
            SELECT
                im.attributes ->> 'pair' AS pair,
                im.tenor,
                d.trade_date,
                d.field_value::float AS forward_points,
                ROW_NUMBER() OVER (
                    PARTITION BY im.instrument_id
                    ORDER BY d.trade_date DESC
                ) AS rn
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_forward'
              AND im.tenor = :tenor
              AND d.field_name = :forward_field
        )
        SELECT
            s.pair,
            s.trade_date AS spot_date,
            f.trade_date AS forward_date,
            s.spot,
            f.tenor,
            f.forward_points
        FROM latest_spot s
        JOIN latest_fwd f
            ON s.pair = f.pair
        WHERE s.rn = 1
          AND f.rn = 1
        """
    )


def _round(value: float, decimals: int) -> float:
    return round(float(value), decimals)


def get_fx_carry(
    engine: Engine,
    params: FXCarryInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    tenor = (params.tenor or config.convention_value("default_tenor")).upper().strip()
    tenor_days_by_tenor = tenor_days_from_config(config)
    if tenor not in tenor_days_by_tenor:
        # Fail loud. Silently falling back to a default tenor (the v1
        # behaviour) produced massively inflated annualised carry when
        # callers passed unsupported tenors, since the (annualisation /
        # days_per_tenor) ratio compounded the mismatch. The Pydantic
        # schema already restricts ``tenor`` to the supported set, so
        # this branch only fires when a caller bypasses validation
        # (e.g. ``FXCarryInput.model_construct``) — and we want that
        # to surface immediately, not pretend everything is fine.
        raise ValueError(
            f"Unsupported FX forward tenor: {tenor!r}. "
            f"Supported tenors are: {sorted(tenor_days_by_tenor)}."
        )
    tenor_days = tenor_days_by_tenor[tenor]
    annualization_days = int(config.convention_value("annualization_days"))
    spot_field = str(config.convention_value("default_fx_spot_field"))
    forward_field = str(config.convention_value("default_fx_forward_field"))
    jpy_divisor = float(config.convention_value("jpy_forward_points_divisor"))
    default_divisor = float(config.convention_value("default_forward_points_divisor"))

    with engine.connect() as conn:
        df = pd.read_sql(
            _carry_query(),
            conn,
            params={
                "tenor": tenor,
                "spot_field": spot_field,
                "forward_field": forward_field,
            },
        )

    if df.empty:
        return FXCarryOutput(tenor=tenor, rows=[]).model_dump()

    df["forward_points_spot_units"] = df.apply(
        lambda row: points_to_spot_units(
            row["pair"],
            row["forward_points"],
            jpy_divisor=jpy_divisor,
            default_divisor=default_divisor,
        ),
        axis=1,
    )

    df["outright_forward"] = df["spot"] + df["forward_points_spot_units"]

    df["carry_bps_spot"] = (
        df["forward_points_spot_units"] / df["spot"]
    ) * 10000

    df["carry_annualized_pct"] = (
        (df["forward_points_spot_units"] / df["spot"])
        * (annualization_days / tenor_days)
        * 100
    )

    df["carry_signal"] = df["carry_annualized_pct"].apply(
        lambda value: "High carry" if value > 0 else "Low carry"
    )

    df = df.sort_values("carry_annualized_pct", ascending=False)

    spot_decimals = int(config.convention_value("spot_round_decimals"))
    fwd_decimals = int(config.convention_value("forward_points_round_decimals"))
    carry_bps_decimals = int(config.convention_value("carry_bps_round_decimals"))
    carry_pct_decimals = int(config.convention_value("carry_pct_round_decimals"))

    rows = [
        FXCarryRow(
            pair=str(row["pair"]),
            spot_date=str(row["spot_date"]),
            forward_date=str(row["forward_date"]),
            spot=_round(row["spot"], spot_decimals),
            tenor=str(row["tenor"]),
            forward_points=_round(row["forward_points"], fwd_decimals),
            forward_points_spot_units=_round(
                row["forward_points_spot_units"],
                spot_decimals,
            ),
            outright_forward=_round(row["outright_forward"], spot_decimals),
            carry_bps_spot=_round(row["carry_bps_spot"], carry_bps_decimals),
            carry_annualized_pct=_round(
                row["carry_annualized_pct"],
                carry_pct_decimals,
            ),
            carry_signal=str(row["carry_signal"]),
        )
        for row in df.to_dict(orient="records")
    ]

    return FXCarryOutput(tenor=tenor, rows=rows).model_dump()
