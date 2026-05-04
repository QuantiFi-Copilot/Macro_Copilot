from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from database.database import get_db_engine
from fx_agent.forwards.tools.fx_carry.schemas import (
    FXCarryInput,
    FXCarryOutput,
    FXCarryRow,
)
from fx_agent.reference.conventions import (
    DAYS_IN_YEAR,
    normalize_tenor,
    points_to_spot_units,
    tenor_days,
)


def get_fx_carry(params: FXCarryInput) -> FXCarryOutput:
    tenor = normalize_tenor(params.tenor)
    tenor_days_value = tenor_days(tenor)

    query = text(
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
              AND d.field_name = 'PX_LAST'
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
              AND d.field_name = 'PX_LAST'
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

    engine = get_db_engine()
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"tenor": tenor})

    if df.empty:
        return FXCarryOutput(tenor=tenor, rows=[])

    df["forward_points_spot_units"] = df.apply(
        lambda row: points_to_spot_units(row["pair"], row["forward_points"]),
        axis=1,
    )

    df["outright_forward"] = df["spot"] + df["forward_points_spot_units"]

    df["carry_bps_spot"] = (
        df["forward_points_spot_units"] / df["spot"]
    ) * 10000

    df["carry_annualized_pct"] = (
        (df["forward_points_spot_units"] / df["spot"])
        * (DAYS_IN_YEAR / tenor_days_value)
        * 100
    )

    df["carry_signal"] = df["carry_annualized_pct"].apply(
        lambda value: "High carry" if value > 0 else "Low carry"
    )

    df = df.sort_values("carry_annualized_pct", ascending=False)

    rows = [
        FXCarryRow(
            pair=str(row["pair"]),
            spot_date=str(row["spot_date"]),
            forward_date=str(row["forward_date"]),
            spot=float(row["spot"]),
            tenor=str(row["tenor"]),
            forward_points=float(row["forward_points"]),
            forward_points_spot_units=float(row["forward_points_spot_units"]),
            outright_forward=float(row["outright_forward"]),
            carry_bps_spot=float(row["carry_bps_spot"]),
            carry_annualized_pct=float(row["carry_annualized_pct"]),
            carry_signal=str(row["carry_signal"]),
        )
        for row in df.to_dict(orient="records")
    ]

    return FXCarryOutput(tenor=tenor, rows=rows)
