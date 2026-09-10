"""FX forward-curve compute path.

For one G10 pair, returns one row per supported tenor (1W / 1M / 3M /
6M / 12M, short → long) with raw forward points, spot-unit forward
points, outright forward, annualised carry, and the rolling 252-day
z-score / percentile / range on the spot-unit forward points.

Architecture
------------
- The JPY-aware divisor and the per-tenor day-count come from
  ``fx_agent/forwards/_shared.py`` (shared with the ``fx_carry`` tool,
  so the two tools cannot drift on conventions).
- The rolling-stat computation (z-score, percentile, range) is
  delegated to ``shared.analytics.levels.compute_level_metrics``, the
  same generic operator the rates ``yield_levels`` tool uses. Tooling
  conventions (window length, min_periods, ddof, ffill limit) come
  from this tool's own ``config.yaml``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from fx_agent.forwards._shared import (
    SUPPORTED_FORWARD_TENORS,
    points_to_spot_units,
    tenor_days_from_config,
)
from fx_agent.forwards.tools.forward_curve.schemas import (
    FXForwardCurveInput,
    FXForwardCurveOutput,
    FXForwardCurveRow,
)
from shared.analytics.levels import clean_single_series, compute_level_metrics
from shared.config import ToolConfig, load_tool_config


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


def _round(value: Optional[float], decimals: int) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), decimals)
    except (TypeError, ValueError):
        return None


def _latest_spot_query() -> Any:
    return text(
        """
        SELECT d.trade_date, d.field_value::float AS spot
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master im
            ON d.instrument_id = im.instrument_id
        WHERE im.instrument_type = 'fx_spot'
          AND im.attributes ->> 'pair' = :pair
          AND d.field_name = :spot_field
        ORDER BY d.trade_date DESC
        LIMIT 1
        """
    )


def _forwards_history_query() -> Any:
    return text(
        """
        SELECT
            im.tenor,
            d.trade_date,
            d.field_value::float AS forward_points
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master im
            ON d.instrument_id = im.instrument_id
        WHERE im.instrument_type = 'fx_forward'
          AND im.attributes ->> 'pair' = :pair
          AND d.field_name = :forward_field
          AND d.trade_date >= :start_date
        ORDER BY im.tenor, d.trade_date
        """
    )


def get_fx_forward_curve(
    engine: Engine,
    params: FXForwardCurveInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Compute a snapshot of the full FX forward curve for one pair."""
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    pair = params.pair.strip().upper()
    if not pair:
        raise ValueError("pair must be a non-empty string")

    spot_field = (
        params.field_name or str(config.convention_value("default_fx_spot_field"))
    )
    forward_field = (
        params.field_name
        or str(config.convention_value("default_fx_forward_field"))
    )
    jpy_divisor = float(config.convention_value("jpy_forward_points_divisor"))
    default_divisor = float(config.convention_value("default_forward_points_divisor"))
    annualization_days = int(config.convention_value("annualization_days"))
    tenor_days_by_tenor = tenor_days_from_config(config)

    z_window = int(config.convention_value("z_score_window_days"))
    z_min_periods = int(config.convention_value("z_score_min_periods"))
    z_ddof = int(config.convention_value("z_score_ddof"))
    trailing_window = int(config.convention_value("trailing_range_window_days"))
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    daily_off = int(config.convention_value("daily_change_offset_rows"))
    weekly_off = int(config.convention_value("weekly_change_offset_rows"))
    monthly_off = int(config.convention_value("monthly_change_offset_rows"))

    spot_dp = int(config.convention_value("spot_round_decimals"))
    fwd_dp = int(config.convention_value("forward_points_round_decimals"))
    carry_bps_dp = int(config.convention_value("carry_bps_round_decimals"))
    carry_pct_dp = int(config.convention_value("carry_pct_round_decimals"))
    z_dp = int(config.convention_value("z_score_round_decimals"))

    lookback_days = int(params.lookback_days)
    start_date = (
        pd.Timestamp.today().normalize() - pd.Timedelta(days=lookback_days)
    ).date()

    # --- 1. Latest spot + forwards history ---------------------------------
    with engine.connect() as conn:
        spot_df = pd.read_sql(
            _latest_spot_query(),
            conn,
            params={"pair": pair, "spot_field": spot_field},
        )
        fwd_df = pd.read_sql(
            _forwards_history_query(),
            conn,
            params={
                "pair": pair,
                "forward_field": forward_field,
                "start_date": start_date,
            },
        )

    if spot_df.empty:
        raise ValueError(
            f"No FX spot rows found for pair {pair!r} in macro_data. "
            f"Query instrument_master with instrument_type='fx_spot' "
            f"for the current set of supported pairs."
        )
    spot_latest = float(spot_df.iloc[0]["spot"])
    spot_date = str(spot_df.iloc[0]["trade_date"])

    if fwd_df.empty:
        raise ValueError(
            f"No FX forward rows found for pair {pair!r} in macro_data "
            f"within the last {lookback_days} days. "
            f"G10 forwards substrate is in fx_agent/playbooks/fx_forwards.yml; "
            f"crosses / EM / NDFs do not have forward points ingested in Wave 1."
        )

    # --- 2. Per-tenor metrics --------------------------------------------
    rows: list[FXForwardCurveRow] = []
    for tenor in SUPPORTED_FORWARD_TENORS:
        tenor_df = fwd_df[fwd_df["tenor"] == tenor].copy()
        if tenor_df.empty:
            # Tenor expected in playbook but missing in DB (shouldn't
            # happen after a clean Wave 1 ingestion, but fail loud if
            # it does so we surface a substrate gap rather than a
            # silently-truncated curve).
            raise ValueError(
                f"No forward rows found for pair {pair!r} at tenor {tenor!r} "
                f"in the last {lookback_days} days. Substrate may be incomplete."
            )

        tenor_df["forward_points_spot_units"] = tenor_df["forward_points"].apply(
            lambda fp: points_to_spot_units(
                pair,
                fp,
                jpy_divisor=jpy_divisor,
                default_divisor=default_divisor,
            )
        )

        # Build the spot-unit forward-points series (date-indexed,
        # ffilled). compute_level_metrics expects a clean Series.
        spot_units_df = clean_single_series(
            tenor_df.rename(
                columns={"forward_points_spot_units": "field_value"}
            )[["trade_date", "field_value"]],
            ffill_limit=ffill_limit,
        )
        spot_units_series = spot_units_df["field_value"]

        metrics = compute_level_metrics(
            spot_units_series,
            z_window=z_window,
            z_min_periods=z_min_periods,
            z_ddof=z_ddof,
            period_offsets={
                "daily": daily_off,
                "weekly": weekly_off,
                "monthly": monthly_off,
            },
            trailing_window=trailing_window,
            yield_round_decimals=spot_dp,
            z_score_round_decimals=z_dp,
            high_low_round_decimals=spot_dp,
        )

        latest_fp = float(tenor_df.iloc[-1]["forward_points"])
        latest_fp_spot_units = float(
            tenor_df.iloc[-1]["forward_points_spot_units"]
        )
        forward_date = str(tenor_df.iloc[-1]["trade_date"])
        outright_forward = spot_latest + latest_fp_spot_units
        tenor_days = tenor_days_by_tenor[tenor]
        carry_bps = (latest_fp_spot_units / spot_latest) * 10000
        carry_pct = (
            (latest_fp_spot_units / spot_latest)
            * (annualization_days / tenor_days)
            * 100
        )

        rows.append(
            FXForwardCurveRow(
                tenor=tenor,
                tenor_days=tenor_days,
                spot=_round(spot_latest, spot_dp),
                spot_date=spot_date,
                forward_date=forward_date,
                forward_points=_round(latest_fp, fwd_dp),
                forward_points_spot_units=_round(latest_fp_spot_units, spot_dp),
                outright_forward=_round(outright_forward, spot_dp),
                carry_bps_spot=_round(carry_bps, carry_bps_dp),
                carry_annualized_pct=_round(carry_pct, carry_pct_dp),
                z_score=metrics["z_score"],
                high_252d=metrics["high"],
                low_252d=metrics["low"],
                percentile_252d=metrics["percentile"],
                observation_count=int(metrics["observation_count"]),
            )
        )

    return FXForwardCurveOutput(
        pair=pair,
        as_of_date=spot_date,
        rows=rows,
    ).model_dump()
