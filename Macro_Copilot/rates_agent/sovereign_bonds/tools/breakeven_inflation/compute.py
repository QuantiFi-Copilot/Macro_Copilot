"""compute.py — Deterministic breakeven_inflation math.

Phase 1 PR 19.

The TIPS-vs-Nominal analog to ``calculate_swap_spread`` for the
swap_spread tool.  Reuses ``shared.analytics.spreads`` helpers for
the spread math + rolling z-score, exactly the way the existing
spread primitives do.

Test seam
---------
``fetch_tenor_group`` and ``date`` imported at module level for
test patchability via
``...breakeven_inflation.compute.fetch_tenor_group``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.breakeven_inflation.schemas import (
    BreakevenInflationCurrentMetrics,
    BreakevenInflationInput,
    BreakevenInflationOutput,
    BreakevenInflationTimeSeriesRow,
)
from shared.analytics.rates_fetch import fetch_tenor_group
from shared.analytics.spreads import (
    compute_spread_bps,
    pivot_and_align_tenors,
    rolling_zscore,
    safe_float,
)
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

_TOOL_NAME = "calculate_breakeven_inflation_tool"
_TOOL_VERSION = "1.0.0"


# ============================================================================
# PUBLIC API
# ============================================================================


def calculate_breakeven_inflation(
    engine: Engine,
    params: BreakevenInflationInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Compute the matched-tenor breakeven inflation series + z-score
    + current snapshot.

    Same envelope as ``calculate_swap_spread``: returns
    ``output.model_dump()`` or ``{"error": "..."}``.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    convention = params.convention or config.convention_value(
        "default_convention",
    )
    if convention == "inflation_swap_breakeven":
        return {
            "error": (
                "convention=inflation_swap_breakeven is declared in the "
                "closed-enum but V1 raises NotImplementedError — "
                "inflation-swap data + seasonal CPI adjustments are "
                "not yet ingested.  See config.yaml methodology."
                "planned_extensions."
            )
        }
    if convention != "nominal_breakeven":
        return {
            "error": (
                f"Unknown convention {convention!r}; expected one of "
                "['nominal_breakeven', 'inflation_swap_breakeven']."
            )
        }

    z_window = int(config.convention_value("z_score_window_days"))
    z_min_periods = int(config.convention_value("z_score_min_periods"))
    z_ddof = int(config.convention_value("z_score_ddof"))
    buffer_mult = float(config.convention_value("z_score_buffer_multiplier"))
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    bps_round = int(config.convention_value("breakeven_bps_round_decimals"))
    z_round = int(config.convention_value("z_score_round_decimals"))

    # ------------------------------------------------------------------
    # Date window with warm-up buffer (same recipe as curve_spread).
    # ------------------------------------------------------------------
    buffer_calendar_days = int(z_window * buffer_mult)
    start_date = date.today() - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # Two independent fetches (one per leg) — the nominal and real
    # curves have different curve_family values + potentially
    # different field_name values.  We use the generic
    # ``fetch_tenor_group`` helper twice (curve_family-scoped) rather
    # than ``fetch_cross_market_pair`` (which only handles two curves
    # at the SAME field_name).
    # ------------------------------------------------------------------
    nominal_df = fetch_tenor_group(
        engine=engine,
        curve_family=params.nominal_curve_family,
        tenors=[params.tenor],
        field_name=params.nominal_field_name,
        start_date=start_date,
    )
    real_df = fetch_tenor_group(
        engine=engine,
        curve_family=params.real_curve_family,
        tenors=[params.tenor],
        field_name=params.real_field_name,
        start_date=start_date,
    )

    if nominal_df.empty:
        return {
            "error": (
                f"No data found for nominal curve "
                f"{params.nominal_curve_family} tenor={params.tenor} "
                f"field={params.nominal_field_name} since "
                f"{start_date.isoformat()}."
            )
        }
    if real_df.empty:
        return {
            "error": (
                f"No data found for real curve "
                f"{params.real_curve_family} tenor={params.tenor} "
                f"field={params.real_field_name} since "
                f"{start_date.isoformat()}."
            )
        }

    # Pivot each leg + align onto a common date index.  We tag the
    # leg's column with a stable name (``nominal`` / ``real``) so the
    # spread compute is unambiguous.
    nominal_df = nominal_df.rename(columns={"tenor": "leg"})
    nominal_df["leg"] = "nominal"
    real_df = real_df.rename(columns={"tenor": "leg"})
    real_df["leg"] = "real"
    combined = pd.concat([nominal_df, real_df], axis=0, ignore_index=True)

    wide = (
        combined.pivot_table(
            index="trade_date",
            columns="leg",
            values="field_value",
            aggfunc="first",
        )
        .sort_index()
    )
    wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index))
    if ffill_limit > 0:
        wide = wide.ffill(limit=ffill_limit)

    # Drop dates where either leg is still NaN after ffill.
    wide = wide.dropna(subset=["nominal", "real"], how="any")
    if wide.empty:
        return {
            "error": (
                "After aligning nominal + real legs, no overlapping "
                "observations remain.  Check that both curves have "
                "data over the requested window."
            )
        }

    # ------------------------------------------------------------------
    # Compute breakeven (bps) + rolling z-score.
    # ------------------------------------------------------------------
    wide["breakeven_bps"] = compute_spread_bps(
        wide,
        minuend_col="nominal",
        subtrahend_col="real",
        round_decimals=bps_round,
    )
    wide["z_score"] = rolling_zscore(
        wide["breakeven_bps"],
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_round,
    )

    # Trim to the displayed lookback (discard warm-up rows).
    cutoff = pd.Timestamp(date.today() - timedelta(days=params.lookback_days))
    display_df = wide.loc[wide.index >= cutoff].copy()
    if display_df.empty:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} "
                f"days for {params.nominal_curve_family}/"
                f"{params.real_curve_family} {params.tenor}."
            )
        }

    # ------------------------------------------------------------------
    # Build current_metrics + time-series + canonical TimeSeries.
    # ------------------------------------------------------------------
    latest = display_df.iloc[-1]
    previous = display_df.iloc[-2] if len(display_df) >= 2 else None
    current_breakeven = safe_float(latest["breakeven_bps"])
    daily_change = (
        safe_float(round(
            latest["breakeven_bps"] - previous["breakeven_bps"], bps_round
        ))
        if previous is not None
        else None
    )

    metrics = BreakevenInflationCurrentMetrics(
        as_of_date=latest.name.strftime("%Y-%m-%d"),
        nominal_curve_family=params.nominal_curve_family,
        real_curve_family=params.real_curve_family,
        tenor=params.tenor,
        current_breakeven_bps=current_breakeven,
        daily_change_bps=daily_change,
        current_z_score=safe_float(latest.get("z_score")),
        rolling_window_days=z_window,
        nominal_yield_pct=safe_float(latest.get("nominal")),
        real_yield_pct=safe_float(latest.get("real")),
    )

    ts_rows = [
        BreakevenInflationTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            breakeven_bps=round(row.breakeven_bps, bps_round),
            z_score=safe_float(row.z_score),
        )
        for row in display_df.itertuples()
    ]

    canonical_breakeven = _build_canonical_breakeven_series(
        display_df,
        nominal_curve_family=params.nominal_curve_family,
        real_curve_family=params.real_curve_family,
        tenor=params.tenor,
        round_decimals=bps_round,
    )
    canonical_zscore = _build_canonical_zscore_series(
        display_df,
        nominal_curve_family=params.nominal_curve_family,
        real_curve_family=params.real_curve_family,
        tenor=params.tenor,
    )

    disclosures = [
        f"convention={convention} (nominal_yield - real_yield × 100, in bps).",
        (
            "V1 does NOT include CPI seasonal adjustments — inflation-"
            "swap breakeven (with seasonal) requires data not yet "
            "ingested."
        ),
        f"Matched-tenor breakeven at {params.tenor} (same tenor on both legs).",
    ]

    output = BreakevenInflationOutput(
        current_metrics=metrics,
        time_series=ts_rows,
        time_series_breakeven=canonical_breakeven,
        time_series_zscore=canonical_zscore,
        methodology_disclosures=disclosures,
    )
    return output.model_dump()


# ============================================================================
# Helpers
# ============================================================================


def _build_canonical_breakeven_series(
    display_df: pd.DataFrame,
    *,
    nominal_curve_family: str,
    real_curve_family: str,
    tenor: str,
    round_decimals: int,
) -> TimeSeries:
    series_name = (
        f"{nominal_curve_family.lower()}_{real_curve_family.lower()}_"
        f"{tenor.lower()}_breakeven"
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=round(float(row.breakeven_bps), round_decimals),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.BPS,
        description=(
            f"Breakeven inflation = {nominal_curve_family} yield − "
            f"{real_curve_family} real yield at matched tenor {tenor}."
        ),
        rows=rows,
    )


def _build_canonical_zscore_series(
    display_df: pd.DataFrame,
    *,
    nominal_curve_family: str,
    real_curve_family: str,
    tenor: str,
) -> TimeSeries:
    series_name = (
        f"{nominal_curve_family.lower()}_{real_curve_family.lower()}_"
        f"{tenor.lower()}_breakeven_zscore"
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=safe_float(row.z_score),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"Rolling z-score of the {nominal_curve_family}/"
            f"{real_curve_family} {tenor} breakeven vs its trailing "
            "window."
        ),
        rows=rows,
    )


__all__ = [
    "CONFIG_PATH",
    "calculate_breakeven_inflation",
]
