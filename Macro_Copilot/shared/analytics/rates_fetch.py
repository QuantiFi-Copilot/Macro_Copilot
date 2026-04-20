"""
rates_fetch.py — Database helpers for rates analytics
======================================================

Thin SQL wrappers used by rates domain tools (sovereign_bonds, ois, and
any future sub-domain).  These functions query the shared enriched view
``macro_data.v_market_data_daily_enriched`` and return long-format
DataFrames that callers pivot / align as needed.

These helpers are instrument-type agnostic: sovereign bonds and OIS
swaps both live in the same base view, and the caller supplies the
``curve_family`` + ``field_name`` pair that uniquely identifies the
series of interest (e.g. UST + YLD_YTM_MID for sovereigns, USD_SOFR_OIS
+ PX_LAST for OIS).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine


_FETCH_TENOR_PAIR_SQL = text("""
    SELECT
        trade_date,
        tenor,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE curve_family  = :curve_family
      AND tenor         IN (:short_tenor, :long_tenor)
      AND field_name    = :field_name
      AND trade_date   >= :start_date
    ORDER BY trade_date
""")


def fetch_tenor_pair(
    engine: Engine,
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
    field_name: str,
    start_date: date,
) -> pd.DataFrame:
    """
    Fetch two tenors on one curve from the enriched view.

    Returns a long-format DataFrame with columns
    ``['trade_date', 'tenor', 'field_value']``.  Empty DataFrame if no
    rows match the filter.
    """
    with engine.connect() as conn:
        result = conn.execute(
            _FETCH_TENOR_PAIR_SQL,
            {
                "curve_family": curve_family,
                "short_tenor": short_tenor,
                "long_tenor": long_tenor,
                "field_name": field_name,
                "start_date": start_date.isoformat(),
            },
        )
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)
