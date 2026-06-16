"""
compute.py — Config-driven OIS rate-level tool
================================================

Refactor of the legacy single-file
``rates_agent/ois/tools/rate_level.py`` into the per-tool-folder
pattern.  Every methodology choice (z-score window/min_periods/ddof,
fill limit, period offsets, trailing range window, default field,
rounding) flows from the bundled ``config.yaml`` rather than from
module-level constants or hardcoded literals.

This is the FIRST OIS tool brought onto the per-tool-folder pattern.
The remaining four (curve_spread, forward_rate, cross_market_spread,
scanner) will follow.

Backward compatibility
----------------------
The default convention values in ``config.yaml`` reproduce the legacy
behaviour bit-for-bit:

    z_score_window_days        = 252      (was Z_SCORE_WINDOW)
    z_score_min_periods        = 60       (was implicit pandas default)
    z_score_ddof               = 1        (was implicit pandas default)
    z_score_buffer_multiplier  = 1.5      (was hardcoded)
    daily/weekly/monthly_change_offset_rows = 2/6/22  (was implicit)
    trailing_range_window_days = 252      (was Z_SCORE_WINDOW reused)
    ffill_limit_days           = 5        (was clean_single_series default)
    default_swap_rate_field    = PX_LAST  (was Pydantic default)
    yield_round_decimals       = 4        (was decimals=4 in code)

So callers using the bundled config see byte-identical output to
the legacy tool.  The signature gains an optional ``config`` kwarg
(auto-loaded when None); ``field_name`` is now resolved against the
YAML when the caller passes None.

Honest placeholder
------------------
``trailing_range_window_days`` is locked at 252 in V1.  The output
schema's field names (``high_252d_pct``, ``low_252d_pct``,
``percentile_252d``) embed the number; changing the convention
without renaming the wire fields would silently lie about what the
percentile is computed against.  The compute path raises
``NotImplementedError`` if this is set to anything else; see
``methodology.planned_extensions`` in the YAML for the path to
making it configurable.

Same single primitive used by sovereign yield_levels
----------------------------------------------------
The actual metrics computation (z-score / period changes / trailing
range) is delegated to ``shared.analytics.levels.compute_level_metrics``
— the same primitive the sovereign yield_levels tool uses.  Single
source of truth for the rolling math; the OIS YAML and sovereign YAML
both feed into it via identical kwarg names.

Test seam
---------
``fetch_single_tenor`` and ``date`` are imported here at module
level; tests patch them via
``patch("rates_agent.ois.tools.rate_level.compute.X")``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.ois.tools.rate_level.schemas import (
    OISRateLevelInput,
    OISRateLevelMetrics,
    OISRateLevelOutput,
)
from shared.analytics.levels import (
    clean_single_series,
    compute_level_metrics,
)
from shared.analytics.rates_fetch import fetch_single_tenor, latest_trade_date
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


# Bundled config — public symbol so external callers (mcp_server,
# tests, future REST routes) can build a ToolConfig from the same
# source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Trailing-range window is wire-frozen at 252 in V1; see schemas.py
# and config.yaml's ``planned_extensions``.  This guard fires at the
# convention layer so an editor of config.yaml gets a clear error
# rather than producing percentiles labelled "252d" against a
# different window.  Mirrors the same guard in sovereign yield_levels.
_FROZEN_TRAILING_WINDOW: int = 252


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull the methodology kwargs ``compute_level_metrics`` needs
    from a ToolConfig.

    Raises NotImplementedError if ``trailing_range_window_days`` is
    set to anything other than 252 — see the wire-freeze rationale
    in the module docstring.
    """
    trailing = config.convention_value("trailing_range_window_days")
    if trailing != _FROZEN_TRAILING_WINDOW:
        raise NotImplementedError(
            f"trailing_range_window_days={trailing!r} is documented in this "
            f"tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented.  "
            f"V1 supports only {_FROZEN_TRAILING_WINDOW} because the output "
            f"field names (high_252d_pct, low_252d_pct, percentile_252d) "
            f"embed that number on the wire.  Either restore the value to "
            f"{_FROZEN_TRAILING_WINDOW} or implement the schema rename + "
            f"frontend update documented in planned_extensions."
        )

    return {
        "z_window": config.convention_value("z_score_window_days"),
        "z_min_periods": config.convention_value("z_score_min_periods"),
        "z_ddof": config.convention_value("z_score_ddof"),
        "period_offsets": {
            "daily": config.convention_value("daily_change_offset_rows"),
            "weekly": config.convention_value("weekly_change_offset_rows"),
            "monthly": config.convention_value("monthly_change_offset_rows"),
        },
        "trailing_window": trailing,
        "yield_round_decimals": config.convention_value("yield_round_decimals"),
        "z_score_round_decimals": config.convention_value("z_score_round_decimals"),
        "high_low_round_decimals": config.convention_value("high_low_round_decimals"),
    }


# ============================================================================
# PUBLIC API
# ============================================================================

def get_ois_rate_level(
    engine: Engine,
    params: OISRateLevelInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Return current OIS par-swap-rate level + period changes +
    z-score + trailing high/low/percentile + observation_count for a
    single curve point.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    params : OISRateLevelInput
        Validated input.  ``field_name=None`` resolves against the
        YAML's ``default_swap_rate_field`` convention.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``OISRateLevelOutput``, or ``{"error": "..."}`` on
        recoverable failure.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # Pull conventions
    # ------------------------------------------------------------------
    z_window = config.convention_value("z_score_window_days")
    buffer_mult = config.convention_value("z_score_buffer_multiplier")
    ffill_limit = config.convention_value("ffill_limit_days")
    default_field_name = config.convention_value("default_swap_rate_field")
    metrics_kwargs = _conventions_from_config(config)

    # Resolve field_name: caller's explicit value wins; None falls
    # through to the YAML default.
    field_name_resolved = (
        params.field_name if params.field_name is not None else default_field_name
    )

    # ------------------------------------------------------------------
    # 1. Date window
    # ------------------------------------------------------------------
    buffer_calendar_days = int(z_window * buffer_mult)
    # Anchor to the latest available trade_date (not date.today()) so the
    # window resolves to real data when ingestion lags (weekend / holiday /
    # stale snapshot); falls back to today only when the series has no rows.
    anchor = (
        latest_trade_date(
            engine,
            curve_family=params.curve_family,
            tenor=params.tenor,
            field_name=field_name_resolved,
        )
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
                f"No OIS data found for curve_family='{params.curve_family}', "
                f"tenor='{params.tenor}', field='{field_name_resolved}' "
                f"since {start_date.isoformat()}.  "
                "Please verify the OIS curve family and tenor exist in the database."
            )
        }

    # ------------------------------------------------------------------
    # 3. Clean
    # ------------------------------------------------------------------
    clean_df = clean_single_series(raw_df, ffill_limit=ffill_limit)
    if clean_df.empty:
        return {
            "error": (
                f"All values were null after cleaning for OIS "
                f"'{params.curve_family}' {params.tenor}."
            )
        }
    rates = clean_df["field_value"]

    # ------------------------------------------------------------------
    # 4. Metrics — single canonical primitive shared with sovereign
    # ------------------------------------------------------------------
    metrics_dict = compute_level_metrics(rates, **metrics_kwargs)

    # ------------------------------------------------------------------
    # 5. Observation count is the LOOKBACK_DAYS-window count, NOT the
    #    full series length.  Anchor the cutoff to the data's latest
    #    observation date, NOT date.today() — market data can be 1-3
    #    days stale over weekends / holidays; anchoring to wall-clock
    #    would include inconsistent history depending on when the tool
    #    runs.  This matches the legacy single-file OIS tool's
    #    behaviour exactly.  Sovereign yield_levels currently anchors
    #    to date.today() — that inconsistency is documented in both
    #    tools' planned_extensions and will be reconciled in a
    #    follow-up PR.
    # ------------------------------------------------------------------
    as_of_date = rates.index[-1].date()
    cutoff = pd.Timestamp(as_of_date - timedelta(days=params.lookback_days))
    display_rates = rates.loc[rates.index >= cutoff]
    obs_count = len(display_rates)

    if obs_count == 0:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} days "
                f"for OIS '{params.curve_family}' {params.tenor}."
            )
        }

    # ------------------------------------------------------------------
    # 6. Build output (snapshot)
    # ------------------------------------------------------------------
    metrics = OISRateLevelMetrics(
        as_of_date=as_of_date.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        tenor=params.tenor,
        current_rate_pct=metrics_dict["current_value"],
        daily_change_bps=metrics_dict["period_changes"]["daily"],
        weekly_change_bps=metrics_dict["period_changes"]["weekly"],
        monthly_change_bps=metrics_dict["period_changes"]["monthly"],
        z_score=metrics_dict["z_score"],
        high_252d_pct=metrics_dict["high"],
        low_252d_pct=metrics_dict["low"],
        percentile_252d=metrics_dict["percentile"],
        observation_count=obs_count,
    )

    # ------------------------------------------------------------------
    # 7. Canonical TimeSeries.  Built from the SAME ``display_rates``
    #    slice the snapshot was computed against, rounded with the
    #    SAME ``yield_round_decimals`` convention the snapshot used,
    #    so the snapshot's ``current_rate_pct`` equals
    #    ``time_series.rows[-1].value`` STRICTLY (not just within
    #    tolerance).  Same pattern as sovereign yield_levels.
    # ------------------------------------------------------------------
    yield_round_decimals = metrics_kwargs["yield_round_decimals"]
    canonical_series = _build_canonical_rate_time_series(
        display_rates,
        curve_family=params.curve_family,
        tenor=params.tenor,
        yield_round_decimals=yield_round_decimals,
    )

    output = OISRateLevelOutput(
        current_metrics=metrics,
        time_series=canonical_series,
    )
    return output.model_dump()


# ============================================================================
# CANONICAL TIME-SERIES BUILDER
# ============================================================================

def _build_canonical_rate_time_series(
    display_rates: pd.Series,
    *,
    curve_family: str,
    tenor: str,
    yield_round_decimals: int,
) -> TimeSeries:
    """Convert the cleaned, in-window OIS rate series into the canonical
    ``TimeSeries`` shape with closed-enum units (PERCENT).

    Each row is rounded with ``yield_round_decimals`` so the canonical
    series and the snapshot's ``current_rate_pct`` agree byte-for-byte
    at the latest row (the snapshot uses the SAME convention via
    ``compute_level_metrics(yield_round_decimals=...)``).

    Naming convention: ``<curve_family_lower>_<tenor_lower>_ois_rate``
    (the ``_ois_rate`` suffix distinguishes from sovereign yield series
    when both end up in the same operator panel downstream).
    """
    series_name = f"{curve_family.lower()}_{tenor.lower()}_ois_rate"
    rows = [
        TimeSeriesRow(
            date=ts.strftime("%Y-%m-%d"),
            value=(
                round(float(v), yield_round_decimals)
                if pd.notna(v) else None
            ),
        )
        for ts, v in display_rates.items()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.PERCENT,
        description=(
            f"Historical OIS par-swap-rate levels for {curve_family} "
            f"{tenor} over the display window (cleaned, ffill'd; "
            f"rounded to {yield_round_decimals} decimals to match "
            "current_metrics.current_rate_pct exactly at the latest "
            "row)."
        ),
        rows=rows,
    )
