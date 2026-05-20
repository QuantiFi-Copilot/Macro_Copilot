from __future__ import annotations

from typing import Any, Dict

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from fx_agent.spot.tools.schemas import FXScannerInput, FXScannerOutput, FXScannerRow


def _signal_label(z_score: float | None, momentum_1m_pct: float | None) -> str:
    if z_score is None or momentum_1m_pct is None:
        return "Neutral"

    if z_score >= 1.5 and momentum_1m_pct > 0:
        return "Bullish breakout"
    if z_score <= -1.5 and momentum_1m_pct < 0:
        return "Bearish breakdown"
    if z_score >= 1.5 and momentum_1m_pct < 0:
        return "Overbought fade risk"
    if z_score <= -1.5 and momentum_1m_pct > 0:
        return "Oversold rebound"

    return "Neutral"


def run_fx_scanner(engine: Engine, params: FXScannerInput) -> Dict[str, Any]:
    field_name = params.field_name.upper().strip()

    market_filter = ""
    query_params = {"field_name": field_name}

    if params.market_scope:
        market_filter = "AND im.attributes ->> 'market_scope' = :market_scope"
        query_params["market_scope"] = params.market_scope.upper().strip()

    query = text(
        f"""
        WITH latest AS (
            SELECT
                im.vendor_ticker,
                im.attributes ->> 'pair' AS pair,
                d.trade_date,
                d.field_value::float AS field_value,
                ROW_NUMBER() OVER (
                    PARTITION BY im.instrument_id
                    ORDER BY d.trade_date DESC
                ) AS rn
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_spot'
              AND d.field_name = :field_name
              {market_filter}
        )
        SELECT vendor_ticker, pair, trade_date, field_value
        FROM latest
        WHERE rn <= 252
        ORDER BY pair, trade_date ASC
        """
    )

    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params=query_params)

    if df.empty:
        return FXScannerOutput(rows=[]).model_dump()

    rows = []

    for pair, group in df.groupby("pair"):
        group = group.sort_values("trade_date").copy()
        values = pd.to_numeric(group["field_value"], errors="coerce").dropna()

        if values.empty:
            continue

        current = float(values.iloc[-1])
        trailing = values.tail(252)
        mean_252 = trailing.mean()
        std_252 = trailing.std(ddof=1)

        z_score = None
        if len(trailing) >= 20 and std_252 and not pd.isna(std_252):
            z_score = float((current - mean_252) / std_252)

        def pct(periods: int):
            if len(values) <= periods:
                return None
            old = values.iloc[-periods - 1]
            if old == 0 or pd.isna(old):
                return None
            return float((current / old - 1.0) * 100.0)

        momentum_1m_pct = pct(21)
        momentum_3m_pct = pct(63)

        last_row = group.iloc[-1]

        rows.append(
            FXScannerRow(
                pair=str(pair),
                ticker=str(last_row["vendor_ticker"]),
                as_of_date=pd.to_datetime(last_row["trade_date"]).strftime("%Y-%m-%d"),
                current_spot=current,
                daily_change_pct=pct(1),
                weekly_change_pct=pct(5),
                monthly_change_pct=momentum_1m_pct,
                momentum_1m_pct=momentum_1m_pct,
                momentum_3m_pct=momentum_3m_pct,
                z_score=z_score,
                signal=_signal_label(z_score, momentum_1m_pct),
            )
        )

    rows = sorted(
        rows,
        key=lambda row: abs(row.z_score) if row.z_score is not None else -1,
        reverse=True,
    )[: params.top_n]

    return FXScannerOutput(rows=rows).model_dump()
