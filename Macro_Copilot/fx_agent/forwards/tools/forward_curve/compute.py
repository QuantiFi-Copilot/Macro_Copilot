from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from database.database import get_db_engine
from fx_agent.forwards.tools.forward_curve.schemas import (
    FXForwardCurveInput,
    FXForwardCurveOutput,
    FXForwardCurveRow,
)
from fx_agent.reference.conventions import (
    DAYS_IN_YEAR,
    TENOR_DAYS,
    normalize_pair,
    points_to_spot_units,
)


def get_fx_forward_curve(params: FXForwardCurveInput) -> FXForwardCurveOutput:
    pair = normalize_pair(params.pair)

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
              AND im.attributes ->> 'pair' = :pair
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
              AND d.field_name = 'PX_LAST'
              AND im.attributes ->> 'pair' = :pair
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
        df = pd.read_sql(query, conn, params={"pair": pair})

    if df.empty:
        return FXForwardCurveOutput(pair=pair, rows=[])

    df["tenor_days"] = df["tenor"].map(TENOR_DAYS)
    df = df.dropna(subset=["tenor_days"]).copy()
    df["tenor_days"] = df["tenor_days"].astype(int)

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
        * (DAYS_IN_YEAR / df["tenor_days"])
        * 100
    )

    df = df.sort_values("tenor_days")

    rows = [
        FXForwardCurveRow(
            pair=str(row["pair"]),
            spot_date=str(row["spot_date"]),
            forward_date=str(row["forward_date"]),
            spot=float(row["spot"]),
            tenor=str(row["tenor"]),
            tenor_days=int(row["tenor_days"]),
            forward_points=float(row["forward_points"]),
            forward_points_spot_units=float(row["forward_points_spot_units"]),
            outright_forward=float(row["outright_forward"]),
            carry_bps_spot=float(row["carry_bps_spot"]),
            carry_annualized_pct=float(row["carry_annualized_pct"]),
        )
        for row in df.to_dict(orient="records")
    ]

    return FXForwardCurveOutput(pair=pair, rows=rows)
