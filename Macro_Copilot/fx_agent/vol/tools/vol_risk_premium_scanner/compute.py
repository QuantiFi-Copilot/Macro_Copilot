from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from database.database import get_db_engine
from fx_agent.vol.tools.vol_risk_premium import (
    FXVolRiskPremiumInput,
    get_fx_vol_risk_premium,
)
from fx_agent.vol.tools.vol_risk_premium_scanner.schemas import (
    FXVolRiskPremiumScannerInput,
    FXVolRiskPremiumScannerOutput,
    FXVolRiskPremiumScannerRow,
)


def _available_pairs(tenor: str) -> list[str]:
    query = text(
        """
        SELECT DISTINCT im.attributes ->> 'pair' AS pair
        FROM macro_data.instrument_master im
        WHERE im.instrument_type = 'fx_vol'
          AND im.tenor = :tenor
          AND im.attributes ->> 'pair' IS NOT NULL
        ORDER BY pair
        """
    )
    engine = get_db_engine()
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"tenor": tenor})
    return [str(pair) for pair in df["pair"].dropna().tolist()]


def scan_fx_vol_risk_premium(
    params: FXVolRiskPremiumScannerInput,
) -> FXVolRiskPremiumScannerOutput:
    tenor = params.tenor.upper().strip()
    rows: list[FXVolRiskPremiumScannerRow] = []

    for pair in _available_pairs(tenor):
        try:
            result = get_fx_vol_risk_premium(
                FXVolRiskPremiumInput(
                    pair=pair,
                    tenor=tenor,
                    realized_window_observations=params.realized_window_observations,
                    lookback_days=params.lookback_days,
                )
            )
        except Exception:
            continue

        metrics = result.current_metrics
        rows.append(
            FXVolRiskPremiumScannerRow(
                pair=metrics.pair,
                as_of_date=metrics.as_of_date,
                implied_vol_pct=metrics.implied_vol_pct,
                realized_vol_annualized_pct=metrics.realized_vol_annualized_pct,
                vol_risk_premium_pct=metrics.vol_risk_premium_pct,
                premium_z_score=metrics.premium_z_score,
                signal=metrics.signal,
                suggested_expression=metrics.suggested_expression,
            )
        )

    rows = sorted(
        rows,
        key=lambda row: (
            abs(row.premium_z_score)
            if row.premium_z_score is not None
            else abs(row.vol_risk_premium_pct or 0.0)
        ),
        reverse=True,
    )[: params.top_n]

    return FXVolRiskPremiumScannerOutput(tenor=tenor, rows=rows)
