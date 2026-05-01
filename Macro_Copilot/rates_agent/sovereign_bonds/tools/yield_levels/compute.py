"""
compute.py — Config-driven sovereign yield-levels tool
========================================================

Refactor of the legacy ``rates_agent/sovereign_bonds/tools/yield_levels.py``
into the per-tool-folder pattern.  Every methodology choice
(z-score window/min_periods/ddof, fill limit, period offsets,
trailing range window, default field) flows from the bundled
``config.yaml`` rather than from module-level constants or hardcoded
literals.

Backward compatibility
----------------------
The default convention values in ``config.yaml`` reproduce the legacy
hardcoded values bit-for-bit:

    z_score_window_days        = 252      (was Z_SCORE_WINDOW)
    z_score_min_periods        = 60       (was Z_SCORE_MIN_PERIODS)
    z_score_ddof               = 1        (was implicit pandas default)
    z_score_buffer_multiplier  = 1.5      (was hardcoded)
    daily/weekly/monthly_change_offset_rows = 2/6/22  (was DEFAULT_PERIOD_OFFSETS)
    trailing_range_window_days = 252      (was Z_SCORE_WINDOW reused)
    ffill_limit_days           = 5        (was clean_single_series default)
    default_field_name         = YLD_YTM_MID  (was Pydantic default)

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

Same single primitive used by the batch yield-snapshot card
-----------------------------------------------------------
The actual metrics computation (z-score / period changes / trailing
range) is delegated to ``shared.analytics.levels.compute_level_metrics``.
That primitive is also called by ``api/routes/rates/cards.py``'s
``_compute_yield_snapshot`` batch surface, so editing the YAML here
also drives the rates-page yield snapshot.  Single source of truth.

Test seam
---------
``fetch_single_tenor`` and ``date`` are imported here at module
level; tests patch them via
``patch("rates_agent.sovereign_bonds.tools.yield_levels.compute.X")``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.yield_levels.schemas import (
    YieldLevelInput,
    YieldLevelMetrics,
    YieldLevelOutput,
)
from shared.analytics.levels import (
    clean_single_series,
    compute_level_metrics,
)
from shared.analytics.rates_fetch import fetch_single_tenor
from shared.config import ToolConfig, load_tool_config


# Bundled config — public symbol so external callers (mcp_server,
# REST routes, the cards batch path, tests) can build a ToolConfig
# from the same source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Legacy (un-prefixed-but-still-private) alias for the rates page's
# batch surface to import alongside the public name.  Removed once
# every caller migrates to CONFIG_PATH.
_CONFIG_PATH: Path = CONFIG_PATH


# Trailing-range window is wire-frozen at 252 in V1; see schemas.py
# and config.yaml's ``planned_extensions``.  This guard fires at the
# convention layer so an editor of config.yaml gets a clear error
# rather than producing percentiles labelled "252d" against a
# different window.
_FROZEN_TRAILING_WINDOW: int = 252


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull the methodology kwargs ``compute_level_metrics`` needs
    from a ToolConfig.  Centralised here so the batch card path
    (which loads the same config.yaml) uses identical resolution.

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
    }


# ============================================================================
# PUBLIC API
# ============================================================================

def get_yield_levels(
    engine: Engine,
    params: YieldLevelInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Return current yield level + period changes + z-score + trailing
    high/low/percentile + observation_count for a single curve point.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    params : YieldLevelInput
        Validated input.  ``field_name=None`` resolves against the
        YAML's ``default_field_name`` convention.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``YieldLevelOutput``, or ``{"error": "..."}`` on
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
    default_field_name = config.convention_value("default_field_name")
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
    start_date = date.today() - timedelta(
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
    # 3. Clean
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
    # 4. Metrics — single canonical primitive used by the batch path too
    # ------------------------------------------------------------------
    metrics_dict = compute_level_metrics(yields, **metrics_kwargs)

    # ------------------------------------------------------------------
    # 5. Observation count is the LOOKBACK_DAYS-window count, NOT the
    #    full series length.  This matches the legacy tool's behaviour
    #    exactly so the parity-test of the migration is meaningful.
    #    The OIS rate_level tool anchors the cutoff to the latest
    #    observation; sovereign here anchors to date.today().  That
    #    inconsistency is documented in config.yaml's
    #    planned_extensions and will be reconciled in a follow-up PR.
    # ------------------------------------------------------------------
    cutoff = pd.Timestamp(date.today() - timedelta(days=params.lookback_days))
    display_yields = yields.loc[yields.index >= cutoff]
    obs_count = len(display_yields)

    if obs_count == 0:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} days "
                f"for '{params.curve_family}' {params.tenor}."
            )
        }

    # ------------------------------------------------------------------
    # 6. Build output
    # ------------------------------------------------------------------
    metrics = YieldLevelMetrics(
        as_of_date=yields.index[-1].strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        tenor=params.tenor,
        current_yield_pct=metrics_dict["current_value"],
        daily_change_bps=metrics_dict["period_changes"]["daily"],
        weekly_change_bps=metrics_dict["period_changes"]["weekly"],
        monthly_change_bps=metrics_dict["period_changes"]["monthly"],
        z_score=metrics_dict["z_score"],
        high_252d_pct=metrics_dict["high"],
        low_252d_pct=metrics_dict["low"],
        percentile_252d=metrics_dict["percentile"],
        observation_count=obs_count,
    )

    output = YieldLevelOutput(current_metrics=metrics)
    return output.model_dump()
