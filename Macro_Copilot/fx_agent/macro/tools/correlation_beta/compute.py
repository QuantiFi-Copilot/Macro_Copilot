from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from database.database import get_db_engine
from fx_agent.macro.tools.correlation_beta.schemas import (
    FXCorrelationBetaInput,
    FXCorrelationBetaOutput,
    FXCorrelationBetaRow,
)
from fx_agent.reference.conventions import normalize_pair


def _pct(values: pd.Series, periods: int) -> float | None:
    if len(values) <= periods:
        return None
    old = values.iloc[-periods - 1]
    new = values.iloc[-1]
    if pd.isna(old) or pd.isna(new) or old == 0:
        return None
    return float((new / old - 1.0) * 100.0)


def _beta_and_corr(fx_returns: pd.Series, proxy_returns: pd.Series) -> tuple[float | None, float | None, float | None, int]:
    joined = pd.concat(
        [fx_returns.rename("fx"), proxy_returns.rename("proxy")],
        axis=1,
    ).dropna()
    observations = int(len(joined))
    if observations < 20:
        return None, None, None, observations

    proxy_var = joined["proxy"].var(ddof=1)
    if not proxy_var or pd.isna(proxy_var):
        return None, None, None, observations

    covariance = joined["fx"].cov(joined["proxy"])
    beta = float(covariance / proxy_var)
    correlation = joined["fx"].corr(joined["proxy"])
    if pd.isna(correlation):
        return beta, None, None, observations
    r_squared = float(correlation * correlation)
    return beta, float(correlation), r_squared, observations


def _sensitivity_label(correlation: float | None, beta: float | None) -> str:
    if correlation is None or beta is None:
        return "insufficient data"
    abs_corr = abs(correlation)
    if abs_corr >= 0.65:
        strength = "high"
    elif abs_corr >= 0.35:
        strength = "medium"
    else:
        strength = "low"
    direction = "positive" if beta > 0 else "negative"
    return f"{strength} {direction} sensitivity"


def _interpret(pair: str, label: str, correlation: float | None, beta: float | None) -> str:
    if correlation is None or beta is None:
        return f"{pair} has insufficient overlapping returns versus {label}."
    direction = "rises" if beta > 0 else "falls"
    return (
        f"{pair} tends to {direction} when {label} rises "
        f"(corr {correlation:+.2f}, beta {beta:+.2f})."
    )


def get_fx_correlation_beta(params: FXCorrelationBetaInput) -> FXCorrelationBetaOutput:
    pair = normalize_pair(params.pair)
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
            params={
                "pair": pair,
                "field_name": field_name,
                "lookback_days": params.lookback_days,
            },
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

    fx_returns = fx_values.pct_change().dropna().tail(params.window_observations)
    rows: list[FXCorrelationBetaRow] = []
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

        proxy_returns = values.pct_change().dropna().tail(params.window_observations)
        beta, correlation, r_squared, observations = _beta_and_corr(fx_returns, proxy_returns)
        label_str = str(label)
        rows.append(
            FXCorrelationBetaRow(
                ticker=str(ticker),
                label=label_str,
                proxy_family=str(family),
                observations=observations,
                correlation=correlation,
                beta=beta,
                r_squared=r_squared,
                proxy_1m_change_pct=_pct(values, 21),
                sensitivity_label=_sensitivity_label(correlation, beta),
                interpretation=_interpret(pair, label_str, correlation, beta),
            )
        )

    rows = sorted(
        rows,
        key=lambda row: abs(row.correlation) if row.correlation is not None else -1,
        reverse=True,
    )
    dominant = rows[0] if rows and rows[0].correlation is not None else None
    dominant_driver = dominant.label if dominant else None
    dominant_corr = dominant.correlation if dominant else None
    as_of_date = fx_values.index[-1].strftime("%Y-%m-%d")

    if dominant:
        summary = (
            f"{pair}'s dominant macro sensitivity is {dominant.label} "
            f"(corr {dominant.correlation:+.2f}, beta {dominant.beta:+.2f}) "
            f"over {params.window_observations} observations."
        )
    else:
        summary = f"{pair} has no reliable macro beta over the requested window."

    risks: list[str] = []
    high_sensitivities = [
        row for row in rows if row.correlation is not None and abs(row.correlation) >= 0.65
    ]
    if high_sensitivities:
        risks.append(
            "High macro beta: "
            + ", ".join(f"{row.label} corr {row.correlation:+.2f}" for row in high_sensitivities[:3])
            + "."
        )
    if len(rows) < 3:
        risks.append("Limited macro proxy coverage in the database.")

    return FXCorrelationBetaOutput(
        pair=pair,
        as_of_date=as_of_date,
        window_observations=params.window_observations,
        dominant_driver=dominant_driver,
        dominant_correlation=dominant_corr,
        rows=rows,
        summary=summary,
        risks=risks,
        follow_ups=[
            f"Show me the {pair} macro risk overlay.",
            f"Classify the FX regime anchored on {pair}.",
            f"Compare {pair} with rates differentials.",
        ],
    )

