"""FX carry compute path.

For one forward tenor, this primitive:

1. Loads the full per-pair history of (spot, forward_points) over the
   ``lookback_days`` window, joined on the same ``trade_date`` so no
   historical row uses today's spot.
2. Per pair, builds the carry_annualized_pct series and runs the
   shared ``compute_level_metrics`` operator on it to get the rolling
   z-score / percentile / range over the trailing ``trailing_range
   _window_days`` (252 in V1).
3. Picks the LAST common spot+forward date as the current snapshot,
   so the current row is consistent with the z-score series rather
   than being a "latest spot vs latest forward" union that may mix
   dates on a stale tenor.
4. Sorts by ``rank_by`` (signed carry / abs carry / abs z-score) and
   caps to ``top_n`` if set.

Backward compatibility: with the defaults ``rank_by="carry_signed"``
and ``top_n=None`` the output ordering matches the pre-Phase-A-step-6
behaviour. The new ``carry_z_score`` / ``carry_percentile_252d`` /
``carry_high_252d`` / ``carry_low_252d`` / ``carry_observation_count``
/ ``rank`` fields are additive on the row dict.
"""

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
from shared.analytics.levels import clean_single_series, compute_level_metrics
from shared.config import ToolConfig, load_tool_config


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


def _history_query() -> Any:
    """Full per-day join of spot and forward_points over the lookback
    window, for every pair that has both legs at the requested tenor
    AND falls within the requested market_scope (G10 / EM / ALL).

    Codex Phase B BBG batch 2026-05-25: market_scope filter applied on
    the forwards side via fx_family. Spot side stays unfiltered — the
    join naturally excludes spots whose pair has no matching forward
    in the requested scope. NDFs (fx_family='EM_NDF') are explicitly
    NOT included in any market_scope value because they are quoted
    outright (not points); their carry compute path is separate and
    will land in Phase D as calculate_ndf_implied_carry.
    """
    return text(
        """
        WITH spot_history AS (
            SELECT
                im.attributes ->> 'pair' AS pair,
                d.trade_date,
                d.field_value::float AS spot
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_spot'
              AND d.field_name = :spot_field
              AND d.trade_date >= :start_date
        ),
        fwd_history AS (
            SELECT
                im.attributes ->> 'pair' AS pair,
                im.tenor,
                d.trade_date,
                d.field_value::float AS forward_points
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_forward'
              AND im.attributes ->> 'fx_family' = ANY(:fx_families)
              AND im.tenor = :tenor
              AND d.field_name = :forward_field
              AND d.trade_date >= :start_date
        )
        SELECT
            s.pair,
            s.trade_date,
            s.spot,
            f.tenor,
            f.forward_points
        FROM spot_history s
        INNER JOIN fwd_history f
            ON s.pair = f.pair
           AND s.trade_date = f.trade_date
        ORDER BY s.pair, s.trade_date
        """
    )


# Closed map of market_scope → fx_family list. Mirror of the Pydantic
# Literal in schemas.FXCarryMarketScope so callers fail-loud at both
# the schema boundary AND the SQL boundary. NDFs are explicitly
# excluded — they have their own compute path.
_MARKET_SCOPE_TO_FX_FAMILIES = {
    "G10": ["G10_FORWARDS"],
    "EM": ["EM_FORWARDS"],
    "ALL": ["G10_FORWARDS", "EM_FORWARDS"],
}


def _round(value: Optional[float], decimals: int) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), decimals)
    except (TypeError, ValueError):
        return None


def _sort_key(row: dict, rank_by: str) -> tuple:
    """Descending sort key with None-at-end behaviour for z-score.

    Returned tuple is ordered so ``sorted(..., key=..., reverse=True)``
    produces the desired ranking; the first element of the tuple is
    1 for "has value" and 0 for "missing value" so missing rows end
    up last regardless of the second element's sign.
    """
    if rank_by == "carry_signed":
        return (1, row["carry_annualized_pct"])
    if rank_by == "abs_carry":
        return (1, abs(row["carry_annualized_pct"]))
    if rank_by == "abs_z_score":
        z = row.get("carry_z_score")
        return (0 if z is None else 1, abs(z) if z is not None else 0)
    raise ValueError(f"Unsupported rank_by={rank_by!r}")


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
        raise ValueError(
            f"Unsupported FX forward tenor: {tenor!r}. "
            f"Supported tenors are: {sorted(tenor_days_by_tenor)}."
        )
    tenor_days = tenor_days_by_tenor[tenor]
    annualization_days = int(config.convention_value("annualization_days"))
    spot_field = (
        params.field_name or str(config.convention_value("default_fx_spot_field"))
    )
    forward_field = (
        params.field_name or str(config.convention_value("default_fx_forward_field"))
    )
    jpy_divisor = float(config.convention_value("jpy_forward_points_divisor"))
    default_divisor = float(config.convention_value("default_forward_points_divisor"))

    z_window = int(config.convention_value("z_score_window_days"))
    z_min_periods = int(config.convention_value("z_score_min_periods"))
    z_ddof = int(config.convention_value("z_score_ddof"))
    trailing_window = int(config.convention_value("trailing_range_window_days"))
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    daily_off = int(config.convention_value("daily_change_offset_rows"))
    weekly_off = int(config.convention_value("weekly_change_offset_rows"))
    monthly_off = int(config.convention_value("monthly_change_offset_rows"))

    spot_decimals = int(config.convention_value("spot_round_decimals"))
    fwd_decimals = int(config.convention_value("forward_points_round_decimals"))
    carry_bps_decimals = int(config.convention_value("carry_bps_round_decimals"))
    carry_pct_decimals = int(config.convention_value("carry_pct_round_decimals"))
    z_decimals = int(config.convention_value("z_score_round_decimals"))

    lookback_days = int(params.lookback_days)
    start_date = (
        pd.Timestamp.today().normalize() - pd.Timedelta(days=lookback_days)
    ).date()

    # Resolve market_scope → list of fx_family values to filter forwards.
    # Closed enum — fail-loud if somehow a value sneaks past the Pydantic
    # Literal upstream (defensive).
    try:
        fx_families = _MARKET_SCOPE_TO_FX_FAMILIES[params.market_scope]
    except KeyError as e:
        raise ValueError(
            f"calculate_fx_carry: market_scope={params.market_scope!r} not in "
            f"closed set {sorted(_MARKET_SCOPE_TO_FX_FAMILIES.keys())}."
        ) from e

    # --- 1. Per-day spot+forward history for every pair at this tenor ----
    with engine.connect() as conn:
        df = pd.read_sql(
            _history_query(),
            conn,
            params={
                "tenor": tenor,
                "spot_field": spot_field,
                "forward_field": forward_field,
                "start_date": start_date,
                "fx_families": fx_families,
            },
        )

    if df.empty:
        return FXCarryOutput(tenor=tenor, rows=[]).model_dump()

    # Per-row spot-unit conversion and carry — done on the full
    # history, NOT just the latest, so the z-score series is built
    # from per-date carry rather than (per-date forward × today's spot).
    df["forward_points_spot_units"] = df.apply(
        lambda row: points_to_spot_units(
            row["pair"],
            row["forward_points"],
            jpy_divisor=jpy_divisor,
            default_divisor=default_divisor,
        ),
        axis=1,
    )
    df["carry_annualized_pct"] = (
        (df["forward_points_spot_units"] / df["spot"])
        * (annualization_days / tenor_days)
        * 100
    )

    # --- 2. Per-pair snapshot + rolling stats ----------------------------
    snapshots: list[dict] = []
    for pair, group in df.groupby("pair", sort=False):
        group_sorted = group.sort_values("trade_date").copy()

        # Clean the carry series for the rolling-stat operator (sorted,
        # date-indexed, ffilled).
        clean = clean_single_series(
            group_sorted.rename(columns={"carry_annualized_pct": "field_value"})[
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
            period_offsets={
                "daily": daily_off,
                "weekly": weekly_off,
                "monthly": monthly_off,
            },
            trailing_window=trailing_window,
            yield_round_decimals=carry_pct_decimals,
            z_score_round_decimals=z_decimals,
            high_low_round_decimals=carry_pct_decimals,
        )

        # Current snapshot = LAST common spot+forward date for this
        # pair (Codex garde-fou #2). Aligned with the z-score series'
        # last point, so the snapshot's carry and the series' last
        # value are the same number.
        latest_row = group_sorted.iloc[-1]
        latest_spot = float(latest_row["spot"])
        latest_fp = float(latest_row["forward_points"])
        latest_fp_spot_units = float(latest_row["forward_points_spot_units"])
        outright = latest_spot + latest_fp_spot_units
        carry_bps = (latest_fp_spot_units / latest_spot) * 10000
        carry_pct = float(latest_row["carry_annualized_pct"])
        snapshot_date = str(latest_row["trade_date"])

        snapshots.append(
            {
                "pair": str(pair),
                "tenor": tenor,
                "spot_date": snapshot_date,
                "forward_date": snapshot_date,
                "spot": _round(latest_spot, spot_decimals),
                "forward_points": _round(latest_fp, fwd_decimals),
                "forward_points_spot_units": _round(
                    latest_fp_spot_units, spot_decimals
                ),
                "outright_forward": _round(outright, spot_decimals),
                "carry_bps_spot": _round(carry_bps, carry_bps_decimals),
                "carry_annualized_pct": _round(carry_pct, carry_pct_decimals),
                "carry_signal": "High carry" if carry_pct > 0 else "Low carry",
                "carry_z_score": metrics["z_score"],
                "carry_percentile_252d": metrics["percentile"],
                "carry_high_252d": metrics["high"],
                "carry_low_252d": metrics["low"],
                "carry_observation_count": int(metrics["observation_count"]),
            }
        )

    # --- 3. Rank + top_n -------------------------------------------------
    snapshots.sort(key=lambda r: _sort_key(r, params.rank_by), reverse=True)
    if params.top_n is not None:
        snapshots = snapshots[: int(params.top_n)]
    for idx, snap in enumerate(snapshots, start=1):
        snap["rank"] = idx

    rows = [FXCarryRow(**snap) for snap in snapshots]
    return FXCarryOutput(tenor=tenor, rows=rows).model_dump()
