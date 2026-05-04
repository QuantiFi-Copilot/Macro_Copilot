"""
compute.py — Config-driven OIS cross-market spread tool
========================================================

Refactor of the legacy single-file
``rates_agent/ois/tools/cross_market_spread.py`` into the per-tool-
folder pattern.  Every methodology choice (z-score
window/min_periods/ddof, fill limit, period offsets, trailing range
window, bps/z-score rounding, default field) flows from the bundled
``config.yaml`` rather than from module-level constants or hardcoded
literals.

Third OIS tool brought onto the per-tool-folder pattern (after
``rate_level`` and ``curve_spread``).  Two remain (``forward_rate``
and ``scanner``) and will follow.

Backward compatibility
----------------------
The default convention values in ``config.yaml`` reproduce the legacy
behaviour bit-for-bit:

    z_score_window_days        = 252      (was Z_SCORE_WINDOW)
    z_score_min_periods        = 60       (was implicit pandas default)
    z_score_ddof               = 1        (was implicit pandas default)
    z_score_buffer_multiplier  = 1.5      (was hardcoded)
    daily/weekly/monthly_change_offset_rows = 2/6/22  (period_changes defaults)
    trailing_range_window_days = 252      (was Z_SCORE_WINDOW reused)
    ffill_limit_days           = 5        (was pivot_and_align default)
    bps_round_decimals         = 2        (was hardcoded round(.., 2))
    z_score_round_decimals     = 4        (was rolling_zscore default)
    default_swap_rate_field    = PX_LAST  (was Pydantic default)

So callers using the bundled config see byte-identical
``current_metrics`` and bespoke ``time_series`` to the legacy tool.
This migration ADDS two canonical ``TimeSeries`` fields —
``time_series_spread`` (BPS) and ``time_series_zscore`` (Z_SCORE) —
for the upcoming primitive-to-operator bridge.  The signature gains
an optional ``config`` kwarg (auto-loaded when None); ``field_name``
is now resolved against the YAML when the caller passes None.

Boundary rounding — z-score precision honoured throughout
---------------------------------------------------------
``current_z_score`` and every z-score row (bespoke + canonical) pass
``decimals=z_round_decimals`` to ``safe_float`` so a YAML override
above 4 isn't silently truncated by ``safe_float``'s default of 4 —
day-one application of the P2 fix Codex caught on OIS curve_spread
(PR #63).  Same applies to the bps surfaces via
``period_changes(decimals=bps_round_decimals)``.

Honest placeholder
------------------
``trailing_range_window_days`` is locked at 252 in V1.  The output
schema's field names embed the number; changing the convention
without renaming the wire fields would silently lie about what the
percentile is computed against.  The compute path raises
``NotImplementedError`` if this is set to anything else; see
``methodology.planned_extensions`` in the YAML for the path to
making it configurable.

Buffer sizing — defensive
-------------------------
The fetch buffer uses
``max(z_score_window_days, trailing_range_window_days) * z_score_buffer_multiplier``
even though the two windows are equal at 252 today.  When the
clipping bug eventually gets fixed and the trailing window decoupled
from the z-score window, the fetch will already be sized correctly
to populate both stats from the first displayed trading day.

Same primitives as sovereign cross_market_spread
------------------------------------------------
The math (pivot + align + spread + rolling z-score + period changes
+ trailing high/low) is delegated to the same
``shared.analytics.spreads`` and ``shared.analytics.levels`` primitives
the sovereign cross_market_spread tool uses.  Single source of truth.

Cutoff anchoring: data-anchored (NOT wall-clock)
------------------------------------------------
Anchors the lookback cutoff to ``wide.index[-1].date()`` (the latest
data observation), NOT ``date.today()``.  Matches the legacy
single-file OIS tool's behaviour exactly.  Sovereign
cross_market_spread anchors to ``date.today()`` — the divergence is
documented in both tools' planned_extensions and will be reconciled
in a separate PR alongside the matching yield_levels / curve_spread
anchoring fix.

Test seam
---------
``fetch_cross_market_pair`` and ``date`` are imported here at module
level; tests patch them via
``patch("rates_agent.ois.tools.cross_market_spread.compute.X")``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.ois.tools.cross_market_spread.schemas import (
    OISCrossMarketSpreadCurrentMetrics,
    OISCrossMarketSpreadInput,
    OISCrossMarketSpreadOutput,
    OISCrossMarketSpreadTimeSeriesRow,
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
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


# Bundled config — public symbol so external callers (mcp_server,
# tests, future REST routes) can build a ToolConfig from the same
# source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Trailing-range window is wire-frozen at 252 in V1; see schemas.py
# and config.yaml's ``planned_extensions``.  Mirrors the same guard
# in sovereign cross_market_spread.
_FROZEN_TRAILING_WINDOW: int = 252


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull the methodology kwargs the OIS cross_market_spread compute()
    needs from a ToolConfig.  Mirrors sovereign cross_market_spread's
    resolver.

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

def calculate_ois_cross_market_spread(
    engine: Engine,
    params: OISCrossMarketSpreadInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Calculate the OIS cross-market rate differential between two
    OIS curves at the same tenor point.

    spread = (curve_family_1_rate − curve_family_2_rate) × 100  (bps)

    Convention: for SOFR-ESTR pass curve_family_1='USD_SOFR_OIS',
    curve_family_2='EUR_ESTR_OIS'.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : OISCrossMarketSpreadInput
        Validated input.  ``field_name=None`` resolves against the
        YAML's ``default_swap_rate_field`` convention.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``OISCrossMarketSpreadOutput``, or ``{"error": "..."}``
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
    default_field_name = config.convention_value("default_swap_rate_field")

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
    buffer_calendar_days = int(
        max(z_window, trailing_window) * buffer_multiplier
    )
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
                f"No OIS data found for curves ['{params.curve_family_1}', "
                f"'{params.curve_family_2}'], tenor='{params.tenor}', "
                f"field='{field_name_resolved}' since "
                f"{start_date.isoformat()}.  "
                "Please verify both OIS curve families and the tenor "
                "exist in the database."
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
                f"Missing OIS curve data for {missing} at "
                f"tenor='{params.tenor}'.  Available curve families in "
                f"the query window: {sorted(available_curves)}."
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
                f"After aligning dates for OIS '{params.curve_family_1}' "
                f"and '{params.curve_family_2}' at {params.tenor}, no "
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
    # 6. Trim to requested display lookback.  Anchor the cutoff to the
    #    data's latest observation date, NOT date.today() — the DB can
    #    be 1-3 days stale over weekends / holidays and wall-clock
    #    anchoring produces inconsistent history.  This matches the
    #    legacy single-file OIS tool's behaviour exactly.  Sovereign
    #    cross_market_spread anchors to date.today() — the divergence
    #    is documented in both tools' planned_extensions and will be
    #    reconciled in a separate PR.
    # ------------------------------------------------------------------
    as_of_date = wide.index[-1].date()
    cutoff = pd.Timestamp(as_of_date - timedelta(days=params.lookback_days))
    display_df = wide.loc[wide.index >= cutoff].copy()

    if display_df.empty:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} "
                f"days for OIS '{params.curve_family_1}' vs "
                f"'{params.curve_family_2}' at {params.tenor}."
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

    # Trailing high/low/percentile of the spread (bps scale).  See
    # the docstring's "clipping" note — `spreads` is the trimmed
    # display series, matching the legacy OIS tool.  Preserving
    # legacy parity in this PR.
    high, low, percentile = trailing_high_low_percentile(
        spreads, window=trailing_window, decimals=bps_round_decimals,
    )

    spread_label = (
        f"{params.curve_family_1}-{params.curve_family_2} {params.tenor}"
    )

    metrics = OISCrossMarketSpreadCurrentMetrics(
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
        # boundary-shadowing class as the OIS curve_spread P2 fix
        # (PR #63).  Applied day one here.
        current_z_score=safe_float(
            latest.get("z_score"), decimals=z_round_decimals,
        ),
        rolling_window_days=z_window,
        high_252d_bps=high,
        low_252d_bps=low,
        percentile_252d=percentile,
        curve_family_1_rate=safe_float(latest.get(params.curve_family_1)),
        curve_family_2_rate=safe_float(latest.get(params.curve_family_2)),
    )

    # ------------------------------------------------------------------
    # 8. Build time_series — bespoke wire-frozen shape (preserved for
    #    backward-compat) AND two canonical TimeSeries (BPS spread +
    #    Z_SCORE rolling z-score) for the primitive-to-operator
    #    bridge.  All three built from the same display_df rows so
    #    they cannot drift.
    # ------------------------------------------------------------------
    ts_rows = [
        OISCrossMarketSpreadTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            spread_bps=round(row.spread_bps, bps_round_decimals),
            # Same boundary-rounding fix as current_z_score above.
            z_score=safe_float(row.z_score, decimals=z_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    canonical_spread = _build_canonical_cross_spread_series(
        display_df,
        curve_family_1=params.curve_family_1,
        curve_family_2=params.curve_family_2,
        tenor=params.tenor,
        bps_round_decimals=bps_round_decimals,
    )
    canonical_zscore = _build_canonical_cross_zscore_series(
        display_df,
        curve_family_1=params.curve_family_1,
        curve_family_2=params.curve_family_2,
        tenor=params.tenor,
        z_round_decimals=z_round_decimals,
    )

    output = OISCrossMarketSpreadOutput(
        current_metrics=metrics,
        time_series=ts_rows,
        time_series_spread=canonical_spread,
        time_series_zscore=canonical_zscore,
    )
    return output.model_dump()


# ============================================================================
# CANONICAL TIME-SERIES BUILDERS
# ============================================================================

def _build_canonical_cross_spread_series(
    display_df: pd.DataFrame,
    *,
    curve_family_1: str,
    curve_family_2: str,
    tenor: str,
    bps_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's spread column into the canonical
    ``TimeSeries`` shape (closed-enum BPS units).

    Naming convention: ``<cf1_lower>_<cf2_lower>_<tenor_lower>_ois_cross_spread``.
    The ``_ois_cross_spread`` suffix distinguishes from sovereign
    cross-market spreads AND from OIS same-curve spreads
    (``_ois_spread`` suffix on curve_spread) when they all end up in
    the same operator panel downstream.
    """
    series_name = (
        f"{curve_family_1.lower()}_"
        f"{curve_family_2.lower()}_"
        f"{tenor.lower()}_ois_cross_spread"
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=round(float(row.spread_bps), bps_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.BPS,
        description=(
            f"OIS cross-market spread "
            f"({curve_family_1} − {curve_family_2}) at {tenor} over "
            "the displayed window."
        ),
        rows=rows,
    )


def _build_canonical_cross_zscore_series(
    display_df: pd.DataFrame,
    *,
    curve_family_1: str,
    curve_family_2: str,
    tenor: str,
    z_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's z_score column into the canonical
    ``TimeSeries`` shape (closed-enum Z_SCORE units).

    Naming convention: ``<cf1_lower>_<cf2_lower>_<tenor_lower>_ois_cross_zscore``.
    Values match ``time_series[i].z_score`` 1-to-1 — both pass through
    the same ``safe_float(..., decimals=z_round_decimals)`` so the
    canonical series and the bespoke shape cannot drift, AND so a YAML
    override above 4 is honoured (P2 fix from PR #63).
    """
    series_name = (
        f"{curve_family_1.lower()}_"
        f"{curve_family_2.lower()}_"
        f"{tenor.lower()}_ois_cross_zscore"
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=safe_float(row.z_score, decimals=z_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"Rolling z-score of the {curve_family_1}−{curve_family_2} "
            f"OIS spread at {tenor} vs its own trailing window."
        ),
        rows=rows,
    )
