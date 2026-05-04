"""
compute.py — Config-driven OIS curve-spread tool
=================================================

Refactor of the legacy single-file
``rates_agent/ois/tools/curve_spread.py`` into the per-tool-folder
pattern.  Every methodology choice (z-score window/min_periods/ddof,
fill limit, rounding, default field) flows from the bundled
``config.yaml`` rather than from module-level constants or hardcoded
literals.

Second OIS tool brought onto the per-tool-folder pattern (after
``rate_level``).  The remaining three (forward_rate,
cross_market_spread, scanner) will follow.

Backward compatibility
----------------------
The default convention values in ``config.yaml`` reproduce the legacy
behaviour bit-for-bit:

    z_score_window_days        = 252      (was Z_SCORE_WINDOW)
    z_score_min_periods        = 60       (was implicit pandas default)
    z_score_ddof               = 1        (was implicit pandas default)
    z_score_buffer_multiplier  = 1.5      (was hardcoded)
    ffill_limit_days           = 5        (was clean_single_series default)
    spread_bps_round_decimals  = 2        (was hardcoded round(.., 2))
    z_score_round_decimals     = 4        (was rolling_zscore default 4)
    default_swap_rate_field    = PX_LAST  (was Pydantic default)

So callers using the bundled config see byte-identical
``current_metrics`` and ``time_series`` (the wire-frozen bespoke list
shape) to the legacy tool.  This migration ADDS two canonical
``TimeSeries`` fields — ``time_series_spread`` (BPS) and
``time_series_zscore`` (Z_SCORE) — for the upcoming primitive-to-
operator bridge.  The signature gains an optional ``config`` kwarg
(auto-loaded when None); ``field_name`` is now resolved against the
YAML when the caller passes None.

Same primitives as sovereign curve_spread
------------------------------------------
The actual math (pivot + align + spread + rolling z-score) is
delegated to the same ``shared.analytics.spreads`` primitives the
sovereign curve_spread tool uses.  Single source of truth; the OIS
YAML and sovereign YAML both feed into it via identical kwarg names.

Test seam
---------
``fetch_tenor_pair`` and ``date`` are imported here at module level;
tests patch them via
``patch("rates_agent.ois.tools.curve_spread.compute.X")``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.ois.tools.curve_spread.schemas import (
    OISCurveSpreadCurrentMetrics,
    OISCurveSpreadInput,
    OISCurveSpreadOutput,
    OISCurveSpreadTimeSeriesRow,
)
from shared.analytics.rates_fetch import fetch_tenor_pair
from shared.analytics.spreads import (
    compute_spread_bps,
    pivot_and_align_tenors,
    rolling_zscore,
    safe_float,
)
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


# Bundled config — public symbol so external callers (mcp_server,
# tests, future REST routes) can build a ToolConfig from the same
# source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_ois_curve_spread(
    engine: Engine,
    params: OISCurveSpreadInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Calculate the two-point OIS curve spread, daily change, rolling
    z-score, and full time-series for a given OIS curve family and
    tenor pair.

    Parameters
    ----------
    engine : sqlalchemy.engine.Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : OISCurveSpreadInput
        Validated input.  ``field_name=None`` resolves against the
        YAML's ``default_swap_rate_field`` convention.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``OISCurveSpreadOutput``, or ``{"error": "..."}``
        on recoverable failure.
    """

    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # Pull conventions
    # ------------------------------------------------------------------
    z_window = config.convention_value("z_score_window_days")
    z_min_periods = config.convention_value("z_score_min_periods")
    z_ddof = config.convention_value("z_score_ddof")
    buffer_mult = config.convention_value("z_score_buffer_multiplier")
    ffill_limit = config.convention_value("ffill_limit_days")
    spread_round = config.convention_value("spread_bps_round_decimals")
    zscore_round = config.convention_value("z_score_round_decimals")
    default_field_name = config.convention_value("default_swap_rate_field")

    # Resolve field_name: caller's explicit value wins; None falls
    # through to the YAML default.
    field_name_resolved = (
        params.field_name if params.field_name is not None else default_field_name
    )

    # ------------------------------------------------------------------
    # 1. Date window — fetch enough warm-up history so the rolling
    #    z-score is fully populated from the first displayed row.
    # ------------------------------------------------------------------
    buffer_calendar_days = int(z_window * buffer_mult)
    start_date = date.today() - timedelta(
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
        field_name=field_name_resolved,
        start_date=start_date,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No OIS data found for curve_family='{params.curve_family}', "
                f"tenors=['{params.short_tenor}', '{params.long_tenor}'], "
                f"field='{field_name_resolved}' since "
                f"{start_date.isoformat()}.  "
                "Please verify the OIS curve family and tenors exist in the database."
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
                f"Missing tenor data for {missing} in OIS "
                f"curve_family='{params.curve_family}'.  Available tenors "
                f"in the query window: {sorted(available_tenors)}."
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
                f"'{params.long_tenor}' on OIS '{params.curve_family}', no "
                "overlapping observations remain."
            )
        }

    # ------------------------------------------------------------------
    # 5. Calculate spread (bps) and rolling z-score
    # ------------------------------------------------------------------
    # OIS par swap rates are stored as percent (4.25 = 4.25%); spread
    # in bps = (long − short) × 100, rounded per the convention.
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
    # 6. Trim to the requested lookback (discard warm-up rows).
    #    Anchor the cutoff to the data's latest observation date,
    #    NOT date.today() — the DB can be 1-3 days stale over weekends
    #    / holidays and wall-clock anchoring produces inconsistent
    #    history.  This matches the legacy single-file OIS tool's
    #    behaviour exactly.  Sovereign curve_spread anchors to
    #    date.today() — the inconsistency is documented in both tools'
    #    methodology and will be reconciled in a separate PR
    #    (paired with the same yield_levels-vs-rate_level cleanup).
    # ------------------------------------------------------------------
    as_of_date = wide.index[-1].date()
    cutoff = pd.Timestamp(as_of_date - timedelta(days=params.lookback_days))
    display_df = wide.loc[wide.index >= cutoff].copy()

    if display_df.empty:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} days "
                f"for OIS '{params.curve_family}' "
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

    spread_label = _format_ois_spread_label(
        params.short_tenor, params.long_tenor,
    )

    metrics = OISCurveSpreadCurrentMetrics(
        as_of_date=latest.name.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        spread_label=spread_label,
        current_spread_bps=current_spread,
        daily_change_bps=daily_change,
        current_z_score=safe_float(latest.get("z_score")),
        rolling_window_days=z_window,
        short_tenor_rate=safe_float(latest.get(params.short_tenor)),
        long_tenor_rate=safe_float(latest.get(params.long_tenor)),
    )

    # ------------------------------------------------------------------
    # 8. Build time_series — bespoke wire-frozen shape (preserved for
    #    backward-compat) AND two canonical TimeSeries (BPS spread +
    #    Z_SCORE rolling z-score) for the primitive-to-operator
    #    bridge.  All three built from the same display_df rows so
    #    they cannot drift.
    # ------------------------------------------------------------------
    ts_rows = [
        OISCurveSpreadTimeSeriesRow(
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

    output = OISCurveSpreadOutput(
        current_metrics=metrics,
        time_series=ts_rows,
        time_series_spread=canonical_spread,
        time_series_zscore=canonical_zscore,
    )
    return output.model_dump()


# ============================================================================
# LABEL FORMATTING
# ============================================================================

def _format_ois_spread_label(short_tenor: str, long_tenor: str) -> str:
    """Format an OIS spread label.

    Pure-year pairs render in the familiar '2s10s' style; pairs that
    include a sub-year tenor (1W, 1M, 3M, 6M, 9M) render as '3M/2Y'
    etc., because '3Ms2s' is non-standard and would mislead a reader.
    Preserved verbatim from the legacy single-file OIS tool.
    """
    if short_tenor.endswith("Y") and long_tenor.endswith("Y"):
        return f"{short_tenor.replace('Y', '')}s{long_tenor.replace('Y', '')}s"
    return f"{short_tenor}/{long_tenor}"


# ============================================================================
# CANONICAL TIME-SERIES BUILDERS
# ============================================================================

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

    Naming convention: ``<curve_family_lower>_<short>_<long>_ois_spread``.
    The ``_ois_spread`` suffix distinguishes from sovereign spread
    series when both end up in the same operator panel downstream.
    """
    series_name = (
        f"{curve_family.lower()}_"
        f"{short_tenor.lower()}_{long_tenor.lower()}_ois_spread"
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
            f"OIS curve spread {long_tenor} − {short_tenor} on "
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

    Naming convention: ``<curve_family_lower>_<short>_<long>_ois_zscore``.
    Z-score values are pre-rounded by ``rolling_zscore`` upstream
    (its ``round_decimals`` knob is threaded from
    ``z_score_round_decimals``); rows where the rolling window has
    not warmed up are emitted as ``None`` so the canonical shape
    matches the bespoke ``time_series[i].z_score`` 1-to-1.
    """
    series_name = (
        f"{curve_family.lower()}_"
        f"{short_tenor.lower()}_{long_tenor.lower()}_ois_zscore"
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
            f"Rolling z-score of the {long_tenor}−{short_tenor} OIS "
            f"spread on {curve_family} vs its own trailing window."
        ),
        rows=rows,
    )
