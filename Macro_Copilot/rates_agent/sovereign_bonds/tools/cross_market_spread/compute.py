"""
compute.py — Config-driven sovereign cross-market spread tool
==============================================================

Refactor of the legacy
``rates_agent/sovereign_bonds/tools/cross_market_spread.py`` into the
per-tool-folder pattern.  Every methodology choice (z-score
window/min_periods/ddof, fill limit, period offsets, trailing range
window, bps/z-score rounding, default field) flows from the bundled
``config.yaml`` rather than from module-level constants or hardcoded
literals.

Backward compatibility
----------------------
The default convention values in ``config.yaml`` reproduce the legacy
hardcoded values bit-for-bit:

    z_score_window_days        = 252      (was Z_SCORE_WINDOW)
    z_score_min_periods        = 60       (was Z_SCORE_MIN_PERIODS)
    z_score_ddof               = 1        (was implicit pandas default)
    z_score_buffer_multiplier  = 1.5      (was hardcoded inline)
    daily/weekly/monthly_change_offset_rows = 2/6/22  (period_changes defaults)
    trailing_range_window_days = 252      (was Z_SCORE_WINDOW reused)
    ffill_limit_days           = 5        (was pivot_and_align default)
    bps_round_decimals         = 2        (was hardcoded .round(2))
    z_score_round_decimals     = 4        (was rolling_zscore default)
    default_field_name         = YLD_YTM_MID  (was Pydantic default)

So callers using the bundled config see byte-identical output to the
legacy tool.  The signature gains an optional ``config`` kwarg
(auto-loaded when None); ``field_name`` is now resolved against the
YAML when the caller passes None.

Design decision: trailing-range "252d" clipping bug preserved
-------------------------------------------------------------
Before computing the trailing high/low/percentile, this compute() trims
the series to ``lookback_days``.  That means when ``lookback_days``
corresponds to fewer than 252 trading days, the ``high_252d_bps`` /
``low_252d_bps`` / ``percentile_252d`` fields are computed against a
window smaller than 252.  The SQL parity fixture
(tests/test_cross_market_sql_validation.py) encodes the same
behaviour, so flipping it here would silently re-target the parity
check.  This migration is a *pure refactor*; the bug is documented
under ``methodology.planned_extensions`` in config.yaml and will be
fixed in a separate PR that also regenerates the SQL parity fixture.
Same shape as the butterfly clipping bug.

Design decision: wall-clock lookback cutoff preserved (NOT the OIS
behaviour)
------------------------------------------------------------------
The OIS twin (rates_agent/ois/tools/cross_market_spread.py:164) anchors
the lookback cutoff to the latest data observation
(``wide.index[-1].date()``) rather than ``date.today()``.  The OIS
approach is more correct — the DB can be 1-3 days stale over weekends /
holidays — but adopting it here would be a behaviour change disguised
as a refactor.  Deferred to a separate PR alongside the matching
yield_levels observation_count anchoring fix; both should land
together to keep the 'sovereign vs OIS' parity story coherent.  See
``methodology.planned_extensions`` in config.yaml.

Buffer sizing — defensive
-------------------------
The fetch buffer uses
``max(z_score_window_days, trailing_range_window_days) * z_score_buffer_multiplier``
even though the two windows are equal at 252 today.  When the clipping
bug above is eventually fixed and the trailing window decoupled from
the z-score window, the fetch will already be sized correctly to
populate both stats from the first displayed trading day.

Honest placeholder
------------------
``trailing_range_window_days`` is locked at 252 in V1.  The output
schema's field names embed the number; changing the convention without
renaming the wire fields would silently lie about what the percentile
is computed against.  The compute path raises ``NotImplementedError``
if this is set to anything else; see ``methodology.planned_extensions``
in the YAML for the path to making it configurable.

Boundary rounding
-----------------
``current_z_score`` and ``time_series[].z_score`` pass
``decimals=z_round_decimals`` to ``safe_float`` so a YAML override
above 4 isn't silently truncated by ``safe_float``'s default of 4 —
same boundary-shadowing class as the field_name / delta_bps fixes,
applied day one this time.

``period_changes`` is called with ``decimals=bps_round_decimals`` so
daily / weekly / monthly bps changes also respect the YAML; without
this, the underlying ``delta_bps`` would silently round to 2 even when
the YAML says 4.

Test seam
---------
``fetch_cross_market_pair`` and ``date`` are imported here at module
level; tests patch them via
``patch("rates_agent.sovereign_bonds.tools.cross_market_spread.compute.X")``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.cross_market_spread.schemas import (
    CrossMarketSpreadInput,
    CrossMarketSpreadCurrentMetrics,
    CrossMarketSpreadOutput,
    CrossMarketSpreadTimeSeriesRow,
)
from shared.analytics.levels import (
    period_changes,
    trailing_high_low_percentile,
)
from shared.analytics.rates_fetch import fetch_cross_market_pair
from shared.analytics.spreads import (
    compute_spread_bps,
    pivot_and_align_tenors,
    rolling_zscore,
    safe_float,
)
from shared.config import ToolConfig, load_tool_config


# Bundled config — public symbol so external callers (mcp_server, REST
# routes, the cards batch surface, tests) can build a ToolConfig from
# the same source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Trailing-range window is wire-frozen at 252 in V1; see schemas.py
# and config.yaml's ``planned_extensions``.  This guard fires at the
# convention layer so an editor of config.yaml gets a clear error
# rather than producing percentiles labelled "252d" against a different
# window.
_FROZEN_TRAILING_WINDOW: int = 252


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull the methodology kwargs the cross_market_spread compute()
    needs from a ToolConfig.  Centralised so future callers (e.g. a
    batch surface that loads the same config.yaml) use identical
    resolution.

    Raises NotImplementedError if ``trailing_range_window_days`` is set
    to anything other than 252 — see the wire-freeze rationale in the
    module docstring.
    """
    trailing = config.convention_value("trailing_range_window_days")
    if trailing != _FROZEN_TRAILING_WINDOW:
        raise NotImplementedError(
            f"trailing_range_window_days={trailing!r} is documented in this "
            f"tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented.  "
            f"V1 supports only {_FROZEN_TRAILING_WINDOW} because the output "
            f"field names (high_252d_bps, low_252d_bps, percentile_252d) "
            f"embed that number on the wire.  Either restore the value to "
            f"{_FROZEN_TRAILING_WINDOW} or implement the schema rename + "
            f"frontend update documented in planned_extensions."
        )

    return {
        "z_window": config.convention_value("z_score_window_days"),
        "z_min_periods": config.convention_value("z_score_min_periods"),
        "z_ddof": config.convention_value("z_score_ddof"),
        "z_round_decimals": config.convention_value("z_score_round_decimals"),
        "buffer_multiplier": config.convention_value("z_score_buffer_multiplier"),
        "ffill_limit": config.convention_value("ffill_limit_days"),
        "period_offsets": {
            "daily": config.convention_value("daily_change_offset_rows"),
            "weekly": config.convention_value("weekly_change_offset_rows"),
            "monthly": config.convention_value("monthly_change_offset_rows"),
        },
        "trailing_window": trailing,
        "bps_round_decimals": config.convention_value("bps_round_decimals"),
    }


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_cross_market_spread(
    engine: Engine,
    params: CrossMarketSpreadInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """
    Calculate the cross-market yield differential between two sovereign
    curves at the same tenor point.

    spread = (curve_family_1_yield − curve_family_2_yield) × 100  (bps)

    Convention: for BTP-Bund pass curve_family_1='IT_BTP',
    curve_family_2='DE_BUND'.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : CrossMarketSpreadInput
        Validated input.  ``field_name=None`` resolves against the
        YAML's ``default_field_name`` convention.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialized ``CrossMarketSpreadOutput``, or ``{"error": "..."}``
        on recoverable failure.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # Pull conventions
    # ------------------------------------------------------------------
    conv = _conventions_from_config(config)
    z_window = conv["z_window"]
    z_min_periods = conv["z_min_periods"]
    z_ddof = conv["z_ddof"]
    z_round_decimals = conv["z_round_decimals"]
    buffer_multiplier = conv["buffer_multiplier"]
    ffill_limit = conv["ffill_limit"]
    period_offsets = conv["period_offsets"]
    trailing_window = conv["trailing_window"]
    bps_round_decimals = conv["bps_round_decimals"]
    default_field_name = config.convention_value("default_field_name")

    # Resolve field_name: caller's explicit value wins; None falls
    # through to the YAML default.
    field_name_resolved = (
        params.field_name if params.field_name is not None else default_field_name
    )

    # ------------------------------------------------------------------
    # 1. Date window (defensive: buffer against the *larger* of the
    #    z-window and trailing-window so neither stat starves on the
    #    first displayed row, even if a future config decouples them)
    # ------------------------------------------------------------------
    buffer_calendar_days = int(max(z_window, trailing_window) * buffer_multiplier)
    start_date = date.today() - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 2. Fetch
    # ------------------------------------------------------------------
    raw_df = fetch_cross_market_pair(
        engine=engine,
        curve_family_1=params.curve_family_1,
        curve_family_2=params.curve_family_2,
        tenor=params.tenor,
        field_name=field_name_resolved,
        start_date=start_date,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No data found for curves ['{params.curve_family_1}', "
                f"'{params.curve_family_2}'], tenor='{params.tenor}', "
                f"field='{field_name_resolved}' since {start_date.isoformat()}.  "
                "Please verify the curve families and tenor exist in the database."
            )
        }

    # ------------------------------------------------------------------
    # 3. Validate both curves are present
    # ------------------------------------------------------------------
    available_curves = set(raw_df["curve_family"].unique())
    missing = {params.curve_family_1, params.curve_family_2} - available_curves
    if missing:
        return {
            "error": (
                f"Missing curve data for {missing} at tenor='{params.tenor}'.  "
                f"Available curve families in the query window: "
                f"{sorted(available_curves)}."
            )
        }

    # ------------------------------------------------------------------
    # 4. Pivot → wide format (date × curve_family) and align holiday gaps
    # ------------------------------------------------------------------
    wide = pivot_and_align_tenors(
        raw_df,
        required_tenors=(params.curve_family_1, params.curve_family_2),
        key_col="curve_family",
        ffill_limit=ffill_limit,
    )

    if wide.empty:
        return {
            "error": (
                f"After aligning dates for '{params.curve_family_1}' and "
                f"'{params.curve_family_2}' at {params.tenor}, no "
                "overlapping observations remain."
            )
        }

    # ------------------------------------------------------------------
    # 5. Calculate spread (bps) and rolling z-score
    # ------------------------------------------------------------------
    wide["spread_bps"] = compute_spread_bps(
        wide,
        minuend_col=params.curve_family_1,
        subtrahend_col=params.curve_family_2,
        round_decimals=bps_round_decimals,
    )
    wide["z_score"] = rolling_zscore(
        wide["spread_bps"],
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_round_decimals,
    )

    # ------------------------------------------------------------------
    # 6. Trim to requested display lookback
    # ------------------------------------------------------------------
    # Wall-clock anchored, NOT data-anchored.  The OIS twin uses the
    # latter; aligning them is deferred to a separate PR — see
    # planned_extensions in config.yaml.
    cutoff = pd.Timestamp(date.today() - timedelta(days=params.lookback_days))
    display_df = wide.loc[wide.index >= cutoff].copy()

    if display_df.empty:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} days "
                f"for '{params.curve_family_1}' vs '{params.curve_family_2}' "
                f"at {params.tenor}."
            )
        }

    # ------------------------------------------------------------------
    # 7. Build current_metrics
    # ------------------------------------------------------------------
    latest = display_df.iloc[-1]
    spreads = display_df["spread_bps"]

    current_spread = safe_float(latest["spread_bps"], decimals=bps_round_decimals)

    # Daily / weekly / monthly change in spread (bps).  The spread
    # series is ALREADY in bps, so already_bps=True → plain subtraction.
    # decimals= passes the YAML convention down so daily_change_bps etc.
    # respect bps_round_decimals (not delta_bps's hardcoded default of 2).
    changes = period_changes(
        spreads,
        offsets=period_offsets,
        already_bps=True,
        decimals=bps_round_decimals,
    )

    # Trailing high/low/percentile of the spread (bps scale).  Note: see
    # the docstring's "clipping" note — `spreads` is the trimmed display
    # series.  Preserving legacy parity in this PR.
    high, low, percentile = trailing_high_low_percentile(
        spreads, window=trailing_window, decimals=bps_round_decimals,
    )

    spread_label = f"{params.curve_family_1}-{params.curve_family_2} {params.tenor}"

    metrics = CrossMarketSpreadCurrentMetrics(
        as_of_date=latest.name.strftime("%Y-%m-%d"),
        curve_family_1=params.curve_family_1,
        curve_family_2=params.curve_family_2,
        tenor=params.tenor,
        spread_label=spread_label,
        current_spread_bps=current_spread,
        daily_change_bps=changes["daily"],
        weekly_change_bps=changes["weekly"],
        monthly_change_bps=changes["monthly"],
        # Pass z_round_decimals so a YAML override above 4 isn't
        # silently truncated by safe_float's default of 4 — same
        # boundary-shadowing class as the field_name fix from b2605ee.
        current_z_score=safe_float(latest.get("z_score"), decimals=z_round_decimals),
        rolling_window_days=z_window,
        high_252d_bps=high,
        low_252d_bps=low,
        percentile_252d=percentile,
        curve_family_1_yield=safe_float(latest.get(params.curve_family_1)),
        curve_family_2_yield=safe_float(latest.get(params.curve_family_2)),
    )

    # ------------------------------------------------------------------
    # 8. Build time_series (withheld from LLM, frontend-only)
    # ------------------------------------------------------------------
    ts_rows = [
        CrossMarketSpreadTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            spread_bps=round(row.spread_bps, bps_round_decimals),
            # Same boundary-rounding fix as current_z_score above.
            z_score=safe_float(row.z_score, decimals=z_round_decimals),
        )
        for row in display_df.itertuples()
    ]

    output = CrossMarketSpreadOutput(current_metrics=metrics, time_series=ts_rows)
    return output.model_dump()
