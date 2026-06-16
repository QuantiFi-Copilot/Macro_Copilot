"""
compute.py — Deterministic curve-spread math (config-driven)
=============================================================

Replaces the legacy ``rates_agent/sovereign_bonds/tools/curve_spread.py``
with a config-driven implementation.  Every methodology choice
(z-score window, fill limit, sample-vs-population std, output rounding)
now flows from the bundled ``config.yaml`` rather than module-level
constants.

Backward compatibility
----------------------
The default convention values in ``config.yaml`` reproduce the legacy
hardcoded values bit-for-bit:

    z_score_window_days       = 252      (was Z_SCORE_WINDOW)
    z_score_min_periods       = 60       (was Z_SCORE_MIN_PERIODS)
    z_score_ddof              = 1        (was implicit pandas default)
    z_score_buffer_multiplier = 1.5      (was hardcoded 1.5)
    ffill_limit_days          = 5        (was hardcoded 5)
    spread_bps_round_decimals = 2        (was hardcoded round(2))
    z_score_round_decimals    = 4        (was rolling_zscore default 4)

So the only observable change for callers using the bundled config is
that the function signature gains an optional ``config`` parameter
that defaults to None (auto-load).  Existing callers that don't pass
config see unchanged behaviour.

Test seam
---------
The helper imports ``fetch_tenor_pair`` and ``date`` here at module
level so unit tests can mock both via
``patch("rates_agent.sovereign_bonds.tools.curve_spread.compute.X")``.
The package ``__init__.py`` re-exports ``calculate_curve_spread`` for
convenience but does NOT re-export those test seams; tests must
target this module's namespace directly.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.curve_spread.schemas import (
    CurveSpreadCurrentMetrics,
    CurveSpreadInput,
    CurveSpreadOutput,
    CurveSpreadTimeSeriesRow,
)
from shared.analytics.rates_fetch import fetch_tenor_pair, latest_trade_date
from shared.analytics.spreads import (
    compute_spread_bps,
    pivot_and_align_tenors,
    rolling_zscore,
    safe_float,
)
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


# Bundled config — relative to this file.  Loaded lazily on first call.
# Public symbol so external callers (mcp_server, REST routes, tests)
# can build their own ToolConfig from the same source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_curve_spread(
    engine: Engine,
    params: CurveSpreadInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """
    Calculate the two-point curve spread, daily change, rolling z-score,
    and full time-series for a given curve family and tenor pair.

    Parameters
    ----------
    engine : sqlalchemy.engine.Engine
        A live SQLAlchemy engine connected to the macro TimescaleDB.
    params : CurveSpreadInput
        Validated Pydantic input with curve_family, short_tenor,
        long_tenor, lookback_days, and field_name.
    config : ToolConfig, optional
        Tool configuration object.  When None (default), loads the
        bundled ``config.yaml``; tests pass a custom ToolConfig to
        exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``CurveSpreadOutput`` with ``current_metrics`` and
        ``time_series`` keys.  On insufficient data or other recoverable
        failures, returns ``{"error": "..."}`` with a human-readable
        message the LLM can relay to the user.
    """

    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # Pull conventions from config.  Fail fast (KeyError) if the YAML
    # is missing a key we depend on — better than silently using a
    # different default than the bundled YAML expects.
    # ------------------------------------------------------------------
    z_window = config.convention_value("z_score_window_days")
    z_min_periods = config.convention_value("z_score_min_periods")
    z_ddof = config.convention_value("z_score_ddof")
    buffer_mult = config.convention_value("z_score_buffer_multiplier")
    ffill_limit = config.convention_value("ffill_limit_days")
    spread_round = config.convention_value("spread_bps_round_decimals")
    zscore_round = config.convention_value("z_score_round_decimals")

    # ------------------------------------------------------------------
    # 1. Determine the date window
    # ------------------------------------------------------------------
    # Two independent concepts:
    #   - lookback_days:   how much *displayed* history the user wants
    #   - z_window:        fixed (typically 252) trading-day rolling
    #                      window for mean/std
    #
    # We fetch extra history (the warm-up buffer) so the z-score is
    # already populated from the first displayed row.  The buffer is
    # ``buffer_mult`` × the z-score window in calendar days, which
    # accounts for weekends and holidays.
    buffer_calendar_days = int(z_window * buffer_mult)
    # Anchor to the latest available trade_date (not date.today()) so the
    # window resolves to real data when ingestion lags (weekend / holiday /
    # stale snapshot); falls back to today only when the curve has no rows.
    anchor = (
        params.as_of_date
        or latest_trade_date(engine, curve_family=params.curve_family)
        or date.today()
    )
    start_date = anchor - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 2. Fetch
    # ------------------------------------------------------------------
    raw_df = fetch_tenor_pair(
        engine=engine,
        curve_family=params.curve_family,
        short_tenor=params.short_tenor,
        long_tenor=params.long_tenor,
        field_name=params.field_name,
        start_date=start_date,
        end_date=anchor,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No data found for curve_family='{params.curve_family}', "
                f"tenors=['{params.short_tenor}', '{params.long_tenor}'], "
                f"field='{params.field_name}' since {start_date.isoformat()}.  "
                "Please verify the curve family and tenors exist in the database."
            )
        }

    # ------------------------------------------------------------------
    # 3. Validate that both tenors are present in the fetched data
    # ------------------------------------------------------------------
    available_tenors = set(raw_df["tenor"].unique())
    missing = {params.short_tenor, params.long_tenor} - available_tenors
    if missing:
        return {
            "error": (
                f"Missing tenor data for {missing} in "
                f"curve_family='{params.curve_family}'.  Available tenors in "
                f"the query window: {sorted(available_tenors)}."
            )
        }

    # ------------------------------------------------------------------
    # 4. Pivot → wide format and align across holiday gaps
    # ------------------------------------------------------------------
    wide = pivot_and_align_tenors(
        raw_df,
        required_tenors=(params.short_tenor, params.long_tenor),
        ffill_limit=ffill_limit,
    )

    if wide.empty:
        return {
            "error": (
                f"After aligning dates for '{params.short_tenor}' and "
                f"'{params.long_tenor}' on '{params.curve_family}', no "
                "overlapping observations remain."
            )
        }

    # ------------------------------------------------------------------
    # 5. Calculate spread (bps) and rolling z-score
    # ------------------------------------------------------------------
    # Yields are stored as percentages (e.g. 4.25 = 4.25%); spread in
    # bps = (long − short) × 100, rounded per the convention.
    wide["spread_bps"] = compute_spread_bps(
        wide,
        minuend_col=params.long_tenor,
        subtrahend_col=params.short_tenor,
        round_decimals=spread_round,
    )
    wide["z_score"] = rolling_zscore(
        wide["spread_bps"],
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=zscore_round,
    )

    # ------------------------------------------------------------------
    # 6. Trim to the requested lookback (discard warm-up rows)
    # ------------------------------------------------------------------
    cutoff = pd.Timestamp(anchor - timedelta(days=params.lookback_days))
    display_df = wide.loc[wide.index >= cutoff].copy()

    if display_df.empty:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} days "
                f"for '{params.curve_family}' "
                f"{params.short_tenor}/{params.long_tenor}."
            )
        }

    # ------------------------------------------------------------------
    # 7. Build current_metrics
    # ------------------------------------------------------------------
    latest = display_df.iloc[-1]
    previous = display_df.iloc[-2] if len(display_df) >= 2 else None

    current_spread = safe_float(latest["spread_bps"])
    daily_change = (
        safe_float(round(latest["spread_bps"] - previous["spread_bps"], spread_round))
        if previous is not None
        else None
    )

    spread_label = (
        f"{params.short_tenor.replace('Y', '')}s"
        f"{params.long_tenor.replace('Y', '')}s"
    )

    metrics = CurveSpreadCurrentMetrics(
        as_of_date=latest.name.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        spread_label=spread_label,
        current_spread_bps=current_spread,
        daily_change_bps=daily_change,
        current_z_score=safe_float(latest.get("z_score")),
        rolling_window_days=z_window,
        short_tenor_yield=safe_float(latest.get(params.short_tenor)),
        long_tenor_yield=safe_float(latest.get(params.long_tenor)),
    )

    # ------------------------------------------------------------------
    # 8. Build time_series (legacy bespoke shape — wire-frozen for
    #    frontend backward-compat) AND the two canonical TimeSeries
    #    (time_series_spread for the BPS spread history,
    #    time_series_zscore for the Z_SCORE rolling-z-score history)
    #    for the upcoming primitive-to-operator bridge.  All three
    #    are built from the same display_df rows so they cannot drift.
    # ------------------------------------------------------------------
    ts_rows = [
        CurveSpreadTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            spread_bps=round(row.spread_bps, spread_round),
            z_score=safe_float(row.z_score),
        )
        for row in display_df.itertuples()
    ]
    canonical_spread = _build_canonical_spread_series(
        display_df,
        curve_family=params.curve_family,
        short_tenor=params.short_tenor,
        long_tenor=params.long_tenor,
        spread_round=spread_round,
    )
    canonical_zscore = _build_canonical_zscore_series(
        display_df,
        curve_family=params.curve_family,
        short_tenor=params.short_tenor,
        long_tenor=params.long_tenor,
    )

    output = CurveSpreadOutput(
        current_metrics=metrics,
        time_series=ts_rows,
        time_series_spread=canonical_spread,
        time_series_zscore=canonical_zscore,
    )
    return output.model_dump()


def _build_canonical_spread_series(
    display_df: pd.DataFrame,
    *,
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
    spread_round: int,
) -> TimeSeries:
    """Convert the display DataFrame's spread column into the canonical
    ``TimeSeries`` shape (closed-enum BPS units).

    Naming convention: ``<curve_family_lower>_<short>_<long>_spread``.
    """
    series_name = (
        f"{curve_family.lower()}_"
        f"{short_tenor.lower()}_{long_tenor.lower()}_spread"
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=round(float(row.spread_bps), spread_round),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.BPS,
        description=(
            f"Curve spread {long_tenor} − {short_tenor} on "
            f"{curve_family} over the displayed window."
        ),
        rows=rows,
    )


def _build_canonical_zscore_series(
    display_df: pd.DataFrame,
    *,
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
) -> TimeSeries:
    """Convert the display DataFrame's z_score column into the canonical
    ``TimeSeries`` shape (closed-enum Z_SCORE units).

    Naming convention: ``<curve_family_lower>_<short>_<long>_zscore``.
    Z-score values are pre-rounded by ``rolling_zscore`` upstream
    (its ``round_decimals`` knob is threaded from
    ``z_score_round_decimals``); rows where the rolling window has
    not warmed up are emitted as ``None`` so the canonical shape
    matches the bespoke ``time_series[i].z_score`` 1-to-1.
    """
    series_name = (
        f"{curve_family.lower()}_"
        f"{short_tenor.lower()}_{long_tenor.lower()}_zscore"
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
            f"Rolling z-score of the {long_tenor}−{short_tenor} spread "
            f"on {curve_family} vs its own trailing window."
        ),
        rows=rows,
    )
