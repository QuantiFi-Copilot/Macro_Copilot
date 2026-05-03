from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from database.database import get_db_engine
from fx_agent.macro.tools.risk_overlay.schemas import (
    FXMacroRiskOverlayInput,
    FXMacroRiskOverlayOutput,
    FXMacroRiskProxyRow,
)


def _pct(values: pd.Series, periods: int) -> float | None:
    if len(values) <= periods:
        return None
    old = values.iloc[-periods - 1]
    new = values.iloc[-1]
    if old is None or pd.isna(old) or old == 0:
        return None
    return float((new / old - 1.0) * 100.0)


def _z_score(values: pd.Series) -> float | None:
    trailing = values.tail(252)
    if len(trailing) < 20:
        return None
    std = trailing.std(ddof=1)
    if not std or pd.isna(std):
        return None
    return float((trailing.iloc[-1] - trailing.mean()) / std)


def _corr(fx_values: pd.Series, proxy_values: pd.Series, window: int) -> float | None:
    joined = pd.concat(
        [fx_values.rename("fx"), proxy_values.rename("proxy")],
        axis=1,
    ).dropna()
    if len(joined) <= window:
        return None
    returns = joined.pct_change().dropna().tail(window)
    if len(returns) < max(20, window // 2):
        return None
    value = returns["fx"].corr(returns["proxy"])
    return None if pd.isna(value) else float(value)


def _regime(rows: list[FXMacroRiskProxyRow]) -> tuple[str, float, list[str]]:
    by_label = {row.label: row for row in rows}

    dxy = by_label.get("DXY")
    vix = by_label.get("VIX")
    move = by_label.get("MOVE")
    spx = by_label.get("S&P 500")
    gold = by_label.get("Gold Spot")

    score = 0.0
    implications: list[str] = []

    if dxy and dxy.monthly_change_pct is not None:
        if dxy.monthly_change_pct > 1.0:
            score -= 1.0
            implications.append("DXY strength is a headwind for non-USD longs.")
        elif dxy.monthly_change_pct < -1.0:
            score += 1.0
            implications.append("DXY weakness supports non-USD FX versus USD.")

    if vix and vix.z_score is not None:
        if vix.z_score > 1.0:
            score -= 0.8
            implications.append("VIX is elevated, pointing to risk-off pressure.")
        elif vix.z_score < -0.8:
            score += 0.4
            implications.append("VIX is subdued, which supports carry/risk exposure.")

    if move and move.z_score is not None:
        if move.z_score > 1.0:
            score -= 0.7
            implications.append("MOVE is elevated, signalling rates-vol stress.")
        elif move.z_score < -0.8:
            score += 0.3
            implications.append("MOVE is subdued, reducing rates-vol drag on FX.")

    if spx and spx.monthly_change_pct is not None:
        if spx.monthly_change_pct > 2.0:
            score += 0.6
            implications.append("SPX momentum points to risk-on conditions.")
        elif spx.monthly_change_pct < -2.0:
            score -= 0.6
            implications.append("SPX weakness points to risk-off conditions.")

    if gold and gold.monthly_change_pct is not None and gold.monthly_change_pct > 3.0:
        implications.append("Gold strength suggests demand for hedges or real-asset protection.")

    if score >= 1.0:
        return "risk-on / USD softer", round(score, 2), implications
    if score <= -1.0:
        return "risk-off / USD support", round(score, 2), implications
    return "mixed macro risk", round(score, 2), implications or [
        "Macro proxies are mixed; FX pair-specific signals matter more than broad risk beta."
    ]


def get_fx_macro_risk_overlay(
    params: FXMacroRiskOverlayInput,
) -> FXMacroRiskOverlayOutput:
    pair = params.pair.upper().replace("/", "").strip()
    field_name = params.field_name.upper().strip()

    fx_query = text(
        """
        SELECT d.trade_date, d.field_value::float AS field_value
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master im
          ON d.instrument_id = im.instrument_id
        WHERE im.instrument_type = 'fx_spot'
          AND d.field_name = :field_name
          AND im.attributes ->> 'pair' = :pair
          AND d.trade_date >= CURRENT_DATE - (:lookback_days || ' days')::interval
        ORDER BY d.trade_date ASC
        """
    )
    proxy_query = text(
        """
        SELECT
            im.vendor_ticker,
            im.attributes ->> 'label' AS label,
            im.attributes ->> 'proxy_family' AS proxy_family,
            d.trade_date,
            d.field_value::float AS field_value
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master im
          ON d.instrument_id = im.instrument_id
        WHERE im.instrument_type = 'risk_proxy'
          AND d.field_name = :field_name
          AND d.trade_date >= CURRENT_DATE - (:lookback_days || ' days')::interval
        ORDER BY im.vendor_ticker, d.trade_date ASC
        """
    )

    engine = get_db_engine()
    with engine.connect() as conn:
        fx_df = pd.read_sql(
            fx_query,
            conn,
            params={"pair": pair, "field_name": field_name, "lookback_days": params.lookback_days},
        )
        proxy_df = pd.read_sql(
            proxy_query,
            conn,
            params={"field_name": field_name, "lookback_days": params.lookback_days},
        )

    if fx_df.empty:
        raise ValueError(f"No FX spot data found for pair={pair}, field={field_name}")
    if proxy_df.empty:
        raise ValueError("No macro risk proxy data found.")

    fx_df["trade_date"] = pd.to_datetime(fx_df["trade_date"])
    fx_values = (
        fx_df.assign(field_value=pd.to_numeric(fx_df["field_value"], errors="coerce"))
        .dropna(subset=["field_value"])
        .set_index("trade_date")["field_value"]
        .sort_index()
    )
    if fx_values.empty:
        raise ValueError(f"No numeric FX spot data found for pair={pair}")

    rows: list[FXMacroRiskProxyRow] = []
    for (ticker, label, family), group in proxy_df.groupby(["vendor_ticker", "label", "proxy_family"]):
        group = group.sort_values("trade_date").copy()
        group["trade_date"] = pd.to_datetime(group["trade_date"])
        values = (
            group.assign(field_value=pd.to_numeric(group["field_value"], errors="coerce"))
            .dropna(subset=["field_value"])
            .set_index("trade_date")["field_value"]
            .sort_index()
        )
        if values.empty:
            continue

        rows.append(
            FXMacroRiskProxyRow(
                ticker=str(ticker),
                label=str(label),
                proxy_family=str(family),
                as_of_date=values.index[-1].strftime("%Y-%m-%d"),
                level=float(values.iloc[-1]),
                daily_change_pct=_pct(values, 1),
                monthly_change_pct=_pct(values, 21),
                three_month_change_pct=_pct(values, 63),
                z_score=_z_score(values),
                correlation_to_pair=_corr(
                    fx_values,
                    values,
                    params.correlation_window_observations,
                ),
            )
        )

    rows = sorted(rows, key=lambda row: row.label)
    risk_regime, regime_score, implications = _regime(rows)
    as_of_date = fx_values.index[-1].strftime("%Y-%m-%d")
    summary = (
        f"{pair} macro risk overlay is {risk_regime} "
        f"(score {regime_score:+.2f}) as of {as_of_date}."
    )

    return FXMacroRiskOverlayOutput(
        pair=pair,
        as_of_date=as_of_date,
        spot=float(fx_values.iloc[-1]),
        risk_regime=risk_regime,
        regime_score=regime_score,
        summary=summary,
        implications=implications,
        proxy_rows=rows,
    )
