"""FX NDF implied carry compute path (Phase D).

Mirror of fx_agent/forwards/tools/fx_carry/compute.py but for NDFs.
Key differences:

  - NDF outright is in spot-equivalent units, not forward points → no
    divisor (no /10000 for non-JPY, no /100 for JPY). The formula
    reduces to (outright/spot - 1) * (annualization_days/tenor_days)
    * 100.
  - The substrate join is (im.instrument_type = 'fx_ndf') for the
    outright leg vs (im.instrument_type = 'fx_spot' AND vendor_ticker
    matches the underlying) for the spot leg. The NDF→underlying map
    lives in fx_agent.ndf._shared.SUPPORTED_NDF_CODES so the SQL JOIN
    can resolve the pair via attributes->>'pair'.
  - The spot_convention switch swaps USDCNY → USDCNH for CCN+ only;
    other NDFs always use settlement.

Same rolling-stat operator (compute_level_metrics) and same rank_by
semantics as fx_carry, so cross-asset readers see comparable
extremeness numbers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from fx_agent.ndf._shared import SUPPORTED_NDF_CODES
from fx_agent.ndf.tools.ndf_implied_carry.schemas import (
    FXNDFImpliedCarryInput,
    FXNDFImpliedCarryOutput,
    FXNDFImpliedCarryRow,
)
from shared.analytics.levels import clean_single_series, compute_level_metrics
from shared.config import ToolConfig, load_tool_config


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Offshore-tradable spot overrides per NDF code. Only CCN+ has a clean
# offshore proxy in the current substrate (USDCNH); others fall back to
# settlement when spot_convention='offshore_tradable' is requested.
_OFFSHORE_TRADABLE_OVERRIDE: Dict[str, str] = {
    "CCN+": "USDCNH",
}


def _round(value: Optional[float], decimals: int) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), decimals)
    except (TypeError, ValueError):
        return None


def _sort_key(row: dict, rank_by: str) -> tuple:
    """Same shape as fx_carry's _sort_key — used by sorted(..., reverse=True)."""
    if rank_by == "carry_signed":
        return (1, row["implied_carry_annualized_pct"])
    if rank_by == "abs_carry":
        return (1, abs(row["implied_carry_annualized_pct"]))
    if rank_by == "abs_z_score":
        z = row.get("carry_z_score")
        return (0 if z is None else 1, abs(z) if z is not None else 0)
    raise ValueError(f"Unsupported rank_by={rank_by!r}")


def _tenor_days_from_config(config: ToolConfig) -> Dict[str, int]:
    return {
        "1W": int(config.convention_value("tenor_1w_days")),
        "1M": int(config.convention_value("tenor_1m_days")),
        "3M": int(config.convention_value("tenor_3m_days")),
        "6M": int(config.convention_value("tenor_6m_days")),
        "12M": int(config.convention_value("tenor_12m_days")),
    }


def _build_spot_resolution(spot_convention: str) -> Dict[str, str]:
    """Map ndf_code → spot_pair to use as carry denominator + a 'used'
    label for the output row.

    Returns a dict like:
        {"CCN+": "USDCNH", "IRN+": "USDINR", ...}

    Also returns labels mapping {"CCN+": "offshore_tradable", ...} for
    the output, with "settlement_fallback" for codes where offshore was
    requested but no override exists.
    """
    mapping = {}
    for code, default_pair in SUPPORTED_NDF_CODES.items():
        if spot_convention == "offshore_tradable" and code in _OFFSHORE_TRADABLE_OVERRIDE:
            mapping[code] = _OFFSHORE_TRADABLE_OVERRIDE[code]
        else:
            mapping[code] = default_pair
    return mapping


def _build_label_resolution(spot_convention: str) -> Dict[str, str]:
    labels = {}
    for code in SUPPORTED_NDF_CODES:
        if spot_convention == "offshore_tradable":
            labels[code] = (
                "offshore_tradable"
                if code in _OFFSHORE_TRADABLE_OVERRIDE
                else "settlement_fallback"
            )
        else:
            labels[code] = "settlement"
    return labels


def _history_query_for_pair() -> Any:
    """Per-NDF query: joins a single (ndf_code, spot_pair) pair. We
    call this once per ndf_code in Python so the spot pair varies per
    NDF (settlement vs offshore_tradable for CCN+).
    """
    return text(
        """
        WITH spot_history AS (
            SELECT
                d.trade_date,
                d.field_value::float AS spot
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_spot'
              AND im.attributes ->> 'pair' = :spot_pair
              AND d.field_name = :spot_field
              AND d.trade_date >= :start_date
        ),
        ndf_history AS (
            SELECT
                d.trade_date,
                d.field_value::float AS outright
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_ndf'
              AND im.attributes ->> 'ndf_code' = :ndf_code
              AND im.tenor = :tenor
              AND d.field_name = :ndf_field
              AND d.trade_date >= :start_date
        )
        SELECT
            n.trade_date,
            n.outright,
            s.spot
        FROM ndf_history n
        INNER JOIN spot_history s
          ON s.trade_date = n.trade_date
        ORDER BY n.trade_date
        """
    )


def calculate_fx_ndf_implied_carry(
    engine: Engine,
    params: FXNDFImpliedCarryInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    tenor = params.tenor
    tenor_days_by_tenor = _tenor_days_from_config(config)
    if tenor not in tenor_days_by_tenor:
        raise ValueError(
            f"Unsupported NDF tenor: {tenor!r}. "
            f"Supported: {sorted(tenor_days_by_tenor)}."
        )
    tenor_days = tenor_days_by_tenor[tenor]
    annualization_days = int(config.convention_value("annualization_days"))

    default_ndf_field = str(config.convention_value("default_ndf_field"))
    default_spot_field = str(config.convention_value("default_spot_field"))
    ndf_field = (params.field_name or default_ndf_field).upper().strip()
    spot_field = (params.field_name or default_spot_field).upper().strip()

    z_window = int(config.convention_value("z_score_window_days"))
    z_min_periods = int(config.convention_value("z_score_min_periods"))
    z_ddof = int(config.convention_value("z_score_ddof"))
    trailing_window = int(config.convention_value("trailing_range_window_days"))
    ffill_limit = int(config.convention_value("ffill_limit_days"))

    spot_decimals = int(config.convention_value("spot_round_decimals"))
    outright_decimals = int(config.convention_value("outright_round_decimals"))
    carry_pct_decimals = int(config.convention_value("carry_pct_round_decimals"))
    z_decimals = int(config.convention_value("z_score_round_decimals"))

    lookback_days = int(params.lookback_days)
    start_date = (
        pd.Timestamp.today().normalize() - pd.Timedelta(days=lookback_days)
    ).date()

    spot_resolution = _build_spot_resolution(params.spot_convention)
    label_resolution = _build_label_resolution(params.spot_convention)

    snapshots: list[dict] = []
    sql = _history_query_for_pair()

    for ndf_code, underlying_pair in SUPPORTED_NDF_CODES.items():
        spot_pair = spot_resolution[ndf_code]
        spot_label = label_resolution[ndf_code]

        with engine.connect() as conn:
            df = pd.read_sql(
                sql,
                conn,
                params={
                    "ndf_code": ndf_code,
                    "spot_pair": spot_pair,
                    "tenor": tenor,
                    "ndf_field": ndf_field,
                    "spot_field": spot_field,
                    "start_date": start_date,
                },
            )

        if df.empty:
            # No data — skip this NDF rather than failing the entire
            # cross-section. Cross-asset scanner shouldn't crash on a
            # single sparse pair.
            continue

        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df["spot"] = pd.to_numeric(df["spot"], errors="coerce")
        df["outright"] = pd.to_numeric(df["outright"], errors="coerce")
        df = df.dropna(subset=["spot", "outright"]).sort_values("trade_date")
        if df.empty:
            continue

        # Per-date implied carry (no lookahead — built per-date BEFORE
        # selecting the latest snapshot).
        df["implied_carry_annualized_pct"] = (
            (df["outright"] / df["spot"] - 1.0)
            * (annualization_days / tenor_days)
            * 100.0
        )

        clean = clean_single_series(
            df.rename(columns={"implied_carry_annualized_pct": "field_value"})[
                ["trade_date", "field_value"]
            ],
            ffill_limit=ffill_limit,
        )
        carry_series = clean["field_value"]

        metrics = compute_level_metrics(
            carry_series,
            z_window=z_window,
            z_min_periods=z_min_periods,
            z_ddof=z_ddof,
            period_offsets={"daily": 2, "weekly": 6, "monthly": 22},
            trailing_window=trailing_window,
            yield_round_decimals=carry_pct_decimals,
            z_score_round_decimals=z_decimals,
            high_low_round_decimals=carry_pct_decimals,
        )

        latest = df.iloc[-1]
        latest_spot = float(latest["spot"])
        latest_outright = float(latest["outright"])
        latest_carry = float(latest["implied_carry_annualized_pct"])
        snapshot_date = str(latest["trade_date"].date() if hasattr(latest["trade_date"], "date") else latest["trade_date"])

        snapshots.append(
            {
                "ndf_code": ndf_code,
                "underlying_pair": underlying_pair,
                "tenor": tenor,
                "spot_date": snapshot_date,
                "forward_date": snapshot_date,
                "spot_convention_used": spot_label,
                "spot": _round(latest_spot, spot_decimals),
                "outright": _round(latest_outright, outright_decimals),
                "implied_carry_annualized_pct": _round(latest_carry, carry_pct_decimals),
                "carry_signal": "High carry" if latest_carry > 0 else "Low carry",
                "carry_z_score": metrics["z_score"],
                "carry_percentile_252d": metrics["percentile"],
                "carry_high_252d": metrics["high"],
                "carry_low_252d": metrics["low"],
                "carry_observation_count": int(metrics["observation_count"]),
            }
        )

    snapshots.sort(key=lambda r: _sort_key(r, params.rank_by), reverse=True)
    if params.top_n is not None:
        snapshots = snapshots[: int(params.top_n)]
    for idx, snap in enumerate(snapshots, start=1):
        snap["rank"] = idx

    rows = [FXNDFImpliedCarryRow(**snap) for snap in snapshots]
    return FXNDFImpliedCarryOutput(
        tenor=tenor, spot_convention=params.spot_convention, rows=rows
    ).model_dump()
