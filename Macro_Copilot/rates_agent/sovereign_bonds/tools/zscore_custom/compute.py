"""
compute.py — Custom-window rolling z-score on a single sovereign yield series
=============================================================================

The first new-pattern tool in the v6 sprint.  Distinct from `yield_levels`
(which fixes the z-score window at YAML's 252-day default) — this tool's
central methodological choice is the user-supplied
`z_score_window_days`, set per request.

Determinism boundary (A13)
--------------------------
The user-facing surface exposes ONLY the central knob
(`z_score_window_days`) plus the basic series identification
(`curve_family`, `tenor`, `lookback_days`, optional `field_name`).
Every other methodology decision (`min_periods`, `ddof`,
`buffer_multiplier`, `ffill_limit_days`, rounding, default field) is
sourced from the bundled `config.yaml` and is NOT user-overridable in
V1.  See docs/architecture/tool_architecture.md.

Output shape
------------
The snapshot follows the established sovereign-tool naming
(`current_yield_pct`, `current_z_score`, etc., with
`*_used` echoes for the rolling-window parameters).  The
time-series payload uses the new shared `TimeSeries` shape
(units=`z_score`) — the first new-pattern tool to do so.  Existing
migrated tools keep their bespoke wire formats; the retro-fit is
deferred to a separate cleanup PR.

Boundary rounding discipline
----------------------------
`safe_float()` calls at metric assembly pass `decimals=` explicitly so
a future YAML override of `z_score_round_decimals` above 4 cannot be
silently truncated by `safe_float`'s default of 4 — the same boundary-
shadowing class as the field_name fix from b2605ee and the
z_score_round_decimals fix from the butterfly migration's Codex review.

Test seam
---------
`fetch_single_tenor` and `date` are imported here at module level;
tests patch them via
`patch("rates_agent.sovereign_bonds.tools.zscore_custom.compute.X")`.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.zscore_custom.schemas import (
    ZscoreCustomInput,
    ZscoreCustomMetrics,
    ZscoreCustomOutput,
)
from shared.analytics.levels import clean_single_series
from shared.analytics.rates_fetch import fetch_single_tenor, latest_trade_date
from shared.analytics.spreads import rolling_zscore, safe_float
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


# Bundled config — public symbol so external callers (mcp_server, REST
# routes, tests) can build a ToolConfig from the same source the tool
# uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull the methodology kwargs zscore_custom needs from a ToolConfig.

    Centralised so the same lookups run identically across compute()
    and any future caller that wants to reuse the resolver.
    """
    return {
        "z_min_periods": config.convention_value("z_score_min_periods"),
        "z_ddof": config.convention_value("z_score_ddof"),
        "buffer_multiplier": config.convention_value(
            "z_score_buffer_multiplier"
        ),
        "ffill_limit": config.convention_value("ffill_limit_days"),
        "yield_round_decimals": config.convention_value("yield_round_decimals"),
        "z_round_decimals": config.convention_value("z_score_round_decimals"),
        "default_field_name": config.convention_value("default_field_name"),
    }


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_zscore_custom(
    engine: Engine,
    params: ZscoreCustomInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Compute a rolling z-score with a user-supplied window.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : ZscoreCustomInput
        Validated input.  ``field_name=None`` resolves against the
        YAML's ``default_field_name`` convention.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``ZscoreCustomOutput``, or ``{"error": "..."}`` on
        recoverable failure.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    conv = _conventions_from_config(config)
    z_window = params.z_score_window_days  # central knob — from user input
    z_min_periods = conv["z_min_periods"]
    z_ddof = conv["z_ddof"]
    buffer_multiplier = conv["buffer_multiplier"]
    ffill_limit = conv["ffill_limit"]
    yield_round_decimals = conv["yield_round_decimals"]
    z_round_decimals = conv["z_round_decimals"]
    default_field_name = conv["default_field_name"]

    # ------------------------------------------------------------------
    # Cross-layer contract: schema accepts z_score_window_days >= 20
    # (an absolute lower bound any reasonable methodology should clear)
    # but the YAML's z_score_min_periods (60 in V1) determines the
    # smallest window pandas will actually accept — pandas raises
    # `min_periods N must be <= window` if window < min_periods.  When
    # those two disagree, return a controlled error envelope rather
    # than letting pandas crash, so the caller sees a clear message
    # naming both values + the YAML knob to look at.
    # ------------------------------------------------------------------
    if z_window < z_min_periods:
        return {
            "error": (
                f"z_score_window_days={z_window} is smaller than the "
                f"YAML's z_score_min_periods={z_min_periods}.  pandas "
                f"requires window >= min_periods.  Either request a "
                f"larger z_score_window_days (>= {z_min_periods}), or "
                f"edit z_score_min_periods in zscore_custom/config.yaml "
                f"if the desk has decided a smaller min_periods is "
                f"acceptable."
            )
        }

    # Resolve field_name: caller's explicit value wins; None falls
    # through to the YAML default.
    field_name_resolved = (
        params.field_name if params.field_name is not None else default_field_name
    )

    # ------------------------------------------------------------------
    # 1. Date window (buffer scaled off the user-supplied z-window)
    # ------------------------------------------------------------------
    buffer_calendar_days = int(z_window * buffer_multiplier)
    # Anchor to the latest available trade_date (not date.today()) so the
    # window resolves to real data when ingestion lags; falls back to today
    # only when the curve has no rows (e.g. mocked engine=None in unit tests).
    anchor = (
        latest_trade_date(engine, curve_family=params.curve_family)
        or date.today()
    )
    start_date = anchor - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 2. Fetch
    # ------------------------------------------------------------------
    raw_df = fetch_single_tenor(
        engine=engine,
        curve_family=params.curve_family,
        tenor=params.tenor,
        field_name=field_name_resolved,
        start_date=start_date,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No data found for curve_family='{params.curve_family}', "
                f"tenor='{params.tenor}', field='{field_name_resolved}' "
                f"since {start_date.isoformat()}.  Please verify the curve "
                "family and tenor exist in the database."
            )
        }

    # ------------------------------------------------------------------
    # 3. Clean + ffill
    # ------------------------------------------------------------------
    clean_df = clean_single_series(raw_df, ffill_limit=ffill_limit)
    if clean_df.empty:
        return {
            "error": (
                f"All values were null after cleaning for "
                f"'{params.curve_family}' {params.tenor}."
            )
        }
    yields = clean_df["field_value"]

    # ------------------------------------------------------------------
    # 4. Rolling z-score using the user's z_window + YAML conventions
    # ------------------------------------------------------------------
    z_series = rolling_zscore(
        yields,
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_round_decimals,
    )

    # ------------------------------------------------------------------
    # 5. Trim to the requested display lookback (wall-clock anchored,
    #    matches the sovereign convention; OIS-anchoring divergence
    #    is documented under planned_extensions in other sovereign
    #    tools' configs and deferred to a separate cross-tool PR)
    # ------------------------------------------------------------------
    cutoff = pd.Timestamp(anchor - timedelta(days=params.lookback_days))
    display_yields = yields.loc[yields.index >= cutoff]
    display_z = z_series.loc[z_series.index >= cutoff]

    if len(display_yields) == 0:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} days "
                f"for '{params.curve_family}' {params.tenor}."
            )
        }

    # ------------------------------------------------------------------
    # 6. Build current_metrics
    # ------------------------------------------------------------------
    # Pass decimals= explicitly at the output boundary so a YAML
    # override above the safe_float default is not silently truncated.
    current_yield = safe_float(
        display_yields.iloc[-1], decimals=yield_round_decimals
    )
    current_z = safe_float(
        display_z.iloc[-1], decimals=z_round_decimals
    )

    metrics = ZscoreCustomMetrics(
        as_of_date=display_yields.index[-1].strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        tenor=params.tenor,
        current_yield_pct=current_yield,
        current_z_score=current_z,
        z_score_window_days_used=z_window,
        z_score_min_periods_used=z_min_periods,
        z_score_ddof_used=z_ddof,
        observation_count=int(len(display_yields)),
    )

    # ------------------------------------------------------------------
    # 7. Build TimeSeries (z-score over the displayed window)
    # ------------------------------------------------------------------
    series_name = (
        f"{params.curve_family.lower()}_{params.tenor.lower()}_"
        f"zscore_{z_window}d"
    )
    ts = TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"{z_window}-day rolling z-score of {field_name_resolved} for "
            f"{params.curve_family} {params.tenor} (min_periods="
            f"{z_min_periods}, ddof={z_ddof})."
        ),
        rows=[
            TimeSeriesRow(
                date=ts_idx.strftime("%Y-%m-%d"),
                value=safe_float(val, decimals=z_round_decimals),
            )
            for ts_idx, val in display_z.items()
        ],
    )

    # Populate both ``time_series`` (legacy) and
    # ``time_series_zscore`` (canonical) with the same TimeSeries
    # instance.  See ZscoreCustomOutput docstring for the naming-
    # convention reconciliation rationale.
    output = ZscoreCustomOutput(
        current_metrics=metrics,
        time_series=ts,
        time_series_zscore=ts,
    )
    return output.model_dump()
