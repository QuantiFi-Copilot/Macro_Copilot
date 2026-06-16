"""
compute.py — Config-driven cross-domain swap-spread tool
=========================================================

A CROSS-DOMAIN primitive: consumes one sovereign-curve leg + one
OIS-curve leg at the same tenor and emits the asset-swap-spread
style differential ``(sovereign_yield − ois_rate) × 100`` in bps.

Per the per-tool-folder pattern.  Mirrors the OIS
``cross_market_spread`` shape 1:1 for everything except:

  - the fetcher (``fetch_cross_domain_pair``, new in this PR — see
    ``shared/analytics/rates_fetch.py``)
  - the two-leg field-name resolution (sovereign uses
    ``YLD_YTM_MID`` by default; OIS uses ``PX_LAST``; the YAML
    declares both as separately-named conventions to avoid
    collision with sibling tools' single-field defaults)
  - the spread-label format ("UST-USD_SOFR_OIS 10Y" — sovereign
    first per the sign convention)
  - the snapshot field naming (``sovereign_yield_pct`` /
    ``ois_rate_pct`` to make each leg's domain explicit on the
    wire)

Sign convention
---------------
spread = (sovereign_yield − ois_rate) × 100  (bps)

Positive means the sovereign trades CHEAP to OIS — the canonical
"asset-swap spread" sign.  Locked in code; not invertible via
input.

Boundary rounding — z-score precision honoured throughout
---------------------------------------------------------
``current_z_score``, every bespoke ``time_series[i].z_score``, AND
canonical ``time_series_zscore.rows[i].value`` pass
``decimals=z_round_decimals`` to ``safe_float`` so a YAML override
above 4 isn't silently truncated.  Day-one application of the OIS
curve_spread P2 fix (PR #63).  Same ``period_changes(decimals=
bps_round_decimals)`` discipline for the bps surface.

Honest placeholder
------------------
``trailing_range_window_days`` is locked at 252 in V1.  Compute
raises ``NotImplementedError`` if set otherwise — same wire-freeze
guard as every other cross-market / curve-spread tool.

Cutoff anchoring
----------------
Anchored to the data's latest observation date
(``wide.index[-1].date()``), NOT ``date.today()``.  Matches every
other OIS-rooted tool's anchoring convention.

Test seam
---------
``fetch_cross_domain_pair`` and ``date`` are imported here at
module level; tests patch them via
``patch("rates_agent.ois.tools.swap_spread.compute.X")``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.ois.tools.swap_spread.schemas import (
    SwapSpreadCurrentMetrics,
    SwapSpreadInput,
    SwapSpreadOutput,
    SwapSpreadTimeSeriesRow,
)
from shared.analytics.levels import (
    period_changes,
    trailing_high_low_percentile,
)
from shared.analytics.rates_fetch import fetch_cross_domain_pair, latest_trade_date
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


# Trailing-range window is wire-frozen at 252 in V1.  Same guard
# as the other cross-market tools.
_FROZEN_TRAILING_WINDOW: int = 252


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull the methodology kwargs the swap_spread compute() needs
    from a ToolConfig.  Mirrors the resolver shape used by every
    other cross-market tool.

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
            f"embed that number on the wire."
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

def calculate_swap_spread(
    engine: Engine,
    params: SwapSpreadInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Calculate the cross-domain swap spread between a sovereign
    yield curve and an OIS curve at the same tenor.

    spread = (sovereign_yield − ois_rate) × 100  (bps)

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : SwapSpreadInput
        Validated input.  ``sovereign_field_name=None`` /
        ``ois_field_name=None`` resolve against the YAML's
        ``sovereign_leg_default_field`` / ``ois_leg_default_field``
        conventions respectively.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``SwapSpreadOutput``, or ``{"error": "..."}`` on
        recoverable failure.
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

    sovereign_default = config.convention_value("sovereign_leg_default_field")
    ois_default = config.convention_value("ois_leg_default_field")

    # Resolve each leg's field_name independently — caller's explicit
    # value wins; None falls through to the YAML default.
    sovereign_field_resolved = (
        params.sovereign_field_name
        if params.sovereign_field_name is not None
        else sovereign_default
    )
    ois_field_resolved = (
        params.ois_field_name
        if params.ois_field_name is not None
        else ois_default
    )

    # ------------------------------------------------------------------
    # 1. Date window (defensive: buffer against the *larger* of the
    #    z-window and trailing-window so neither stat starves on the
    #    first displayed row)
    # ------------------------------------------------------------------
    buffer_calendar_days = int(
        max(z_window, trailing_window) * buffer_multiplier
    )
    # Anchor to the latest available trade_date (not date.today()) so the
    # window resolves to real data when ingestion lags (weekend / holiday /
    # stale snapshot).  Cross-domain pair: probe each leg's own
    # (curve_family, field_name) and take the EARLIER of the two latest
    # dates so both legs are guaranteed to have data in the window; falls
    # back to today only when neither leg has rows.
    _sov_anchor = latest_trade_date(
        engine,
        curve_family=params.sovereign_curve_family,
        tenor=params.tenor,
        field_name=sovereign_field_resolved,
    )
    _ois_anchor = latest_trade_date(
        engine,
        curve_family=params.ois_curve_family,
        tenor=params.tenor,
        field_name=ois_field_resolved,
    )
    anchor = min(
        [d for d in (_sov_anchor, _ois_anchor) if d], default=date.today()
    )
    start_date = anchor - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 2. Fetch cross-domain pair
    # ------------------------------------------------------------------
    raw_df = fetch_cross_domain_pair(
        engine=engine,
        sovereign_curve_family=params.sovereign_curve_family,
        sovereign_field_name=sovereign_field_resolved,
        ois_curve_family=params.ois_curve_family,
        ois_field_name=ois_field_resolved,
        tenor=params.tenor,
        start_date=start_date,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No data found for sovereign='{params.sovereign_curve_family}' "
                f"(field '{sovereign_field_resolved}') OR ois="
                f"'{params.ois_curve_family}' (field '{ois_field_resolved}') "
                f"at tenor='{params.tenor}' since {start_date.isoformat()}.  "
                "Please verify both curves exist with their respective field "
                "names in the database."
            )
        }

    # ------------------------------------------------------------------
    # 3. Validate both legs are present
    # ------------------------------------------------------------------
    available_curves = set(raw_df["curve_family"].unique())
    missing = (
        {params.sovereign_curve_family, params.ois_curve_family}
        - available_curves
    )
    if missing:
        return {
            "error": (
                f"Missing leg data for {missing} at tenor='{params.tenor}' "
                f"with the resolved field names.  Available curve families "
                f"in the query window: {sorted(available_curves)}.  "
                "Verify the (curve_family, field_name) pair exists for each "
                "leg."
            )
        }

    # ------------------------------------------------------------------
    # 4. Pivot → wide format (date × curve_family) and align gaps
    # ------------------------------------------------------------------
    wide = pivot_and_align_tenors(
        raw_df,
        required_tenors=(
            params.sovereign_curve_family, params.ois_curve_family,
        ),
        key_col="curve_family",
        ffill_limit=ffill_limit,
    )

    if wide.empty:
        return {
            "error": (
                f"After aligning dates for sovereign "
                f"'{params.sovereign_curve_family}' and ois "
                f"'{params.ois_curve_family}' at {params.tenor}, no "
                "overlapping observations remain."
            )
        }

    # ------------------------------------------------------------------
    # 5. Calculate spread (bps) and rolling z-score
    #    Sign convention: sovereign − ois.  Sovereign is the minuend.
    # ------------------------------------------------------------------
    wide["spread_bps"] = compute_spread_bps(
        wide,
        minuend_col=params.sovereign_curve_family,
        subtrahend_col=params.ois_curve_family,
        round_decimals=bps_round_decimals,
    )
    wide["z_score"] = rolling_zscore(
        wide["spread_bps"],
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_round_decimals,
    )
    # Day-over-day change z-score: z-score of spread.diff() vs its own
    # trailing window.  Distinct from ``z_score`` above (which scores
    # the LEVEL).  Powers the canonical event-study Q1 binding ("when
    # the swap spread WIDENS by more than 1.5σ in a single day...").
    # Same window / min_periods / ddof / rounding as the level z-score
    # so a |z|>N threshold has consistent semantics across signals.
    wide["change_z_score"] = rolling_zscore(
        wide["spread_bps"].diff(),
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_round_decimals,
    )

    # ------------------------------------------------------------------
    # 6. Trim to requested display lookback.  Anchor the cutoff to the
    #    data's latest observation date, NOT date.today() — matches
    #    every other OIS-rooted tool.
    # ------------------------------------------------------------------
    as_of_date = wide.index[-1].date()
    cutoff = pd.Timestamp(as_of_date - timedelta(days=params.lookback_days))
    display_df = wide.loc[wide.index >= cutoff].copy()

    if display_df.empty:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} "
                f"days for sovereign '{params.sovereign_curve_family}' vs "
                f"ois '{params.ois_curve_family}' at {params.tenor}."
            )
        }

    # ------------------------------------------------------------------
    # 7. Build current_metrics
    # ------------------------------------------------------------------
    latest = display_df.iloc[-1]
    spreads = display_df["spread_bps"]

    current_spread = safe_float(latest["spread_bps"], decimals=bps_round_decimals)

    # Daily / weekly / monthly change in spread (bps).
    changes = period_changes(
        spreads,
        offsets=period_offsets,
        already_bps=True,
        decimals=bps_round_decimals,
    )

    high, low, percentile = trailing_high_low_percentile(
        spreads, window=trailing_window, decimals=bps_round_decimals,
    )

    spread_label = (
        f"{params.sovereign_curve_family}-{params.ois_curve_family} "
        f"{params.tenor}"
    )

    metrics = SwapSpreadCurrentMetrics(
        as_of_date=latest.name.strftime("%Y-%m-%d"),
        sovereign_curve_family=params.sovereign_curve_family,
        ois_curve_family=params.ois_curve_family,
        tenor=params.tenor,
        spread_label=spread_label,
        current_spread_bps=current_spread,
        daily_change_bps=changes["daily"],
        weekly_change_bps=changes["weekly"],
        monthly_change_bps=changes["monthly"],
        # Pass z_round_decimals so a YAML override above 4 isn't
        # silently truncated by safe_float's default of 4 — same
        # boundary-shadowing class as the OIS curve_spread P2 fix.
        current_z_score=safe_float(
            latest.get("z_score"), decimals=z_round_decimals,
        ),
        rolling_window_days=z_window,
        high_252d_bps=high,
        low_252d_bps=low,
        percentile_252d=percentile,
        sovereign_yield_pct=safe_float(
            latest.get(params.sovereign_curve_family),
        ),
        ois_rate_pct=safe_float(
            latest.get(params.ois_curve_family),
        ),
    )

    # ------------------------------------------------------------------
    # 8. Build time_series — bespoke wire-frozen shape (preserved
    #    for consistency with every other rates primitive) AND two
    #    canonical TimeSeries (BPS spread + Z_SCORE rolling z-score)
    #    for the primitive→operator bridge.  All three from the same
    #    display_df rows so they cannot drift.
    # ------------------------------------------------------------------
    ts_rows = [
        SwapSpreadTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            spread_bps=round(row.spread_bps, bps_round_decimals),
            z_score=safe_float(row.z_score, decimals=z_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    canonical_spread = _build_canonical_swap_spread_series(
        display_df,
        sovereign_curve_family=params.sovereign_curve_family,
        ois_curve_family=params.ois_curve_family,
        tenor=params.tenor,
        bps_round_decimals=bps_round_decimals,
    )
    canonical_zscore = _build_canonical_swap_zscore_series(
        display_df,
        sovereign_curve_family=params.sovereign_curve_family,
        ois_curve_family=params.ois_curve_family,
        tenor=params.tenor,
        z_round_decimals=z_round_decimals,
    )
    canonical_change_zscore = _build_canonical_swap_change_zscore_series(
        display_df,
        sovereign_curve_family=params.sovereign_curve_family,
        ois_curve_family=params.ois_curve_family,
        tenor=params.tenor,
        z_round_decimals=z_round_decimals,
    )

    output = SwapSpreadOutput(
        current_metrics=metrics,
        time_series=ts_rows,
        time_series_spread=canonical_spread,
        time_series_zscore=canonical_zscore,
        time_series_change_zscore=canonical_change_zscore,
    )
    return output.model_dump()


# ============================================================================
# CANONICAL TIME-SERIES BUILDERS
# ============================================================================

def _build_canonical_swap_spread_series(
    display_df: pd.DataFrame,
    *,
    sovereign_curve_family: str,
    ois_curve_family: str,
    tenor: str,
    bps_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's spread column into the canonical
    ``TimeSeries`` shape (closed-enum BPS units).

    Naming convention:
      ``<sov_lower>_<ois_lower>_<tenor_lower>_swap_spread``.

    The ``_swap_spread`` suffix distinguishes from sovereign
    ``_spread`` (cross_market_spread) and OIS ``_ois_spread`` /
    ``_ois_cross_spread`` so that a downstream operator panel
    rendering all three side-by-side can disambiguate them by
    series_name alone.
    """
    series_name = (
        f"{sovereign_curve_family.lower()}_"
        f"{ois_curve_family.lower()}_"
        f"{tenor.lower()}_swap_spread"
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
            f"Cross-domain swap spread "
            f"({sovereign_curve_family} − {ois_curve_family}) at "
            f"{tenor} over the displayed window."
        ),
        rows=rows,
    )


def _build_canonical_swap_zscore_series(
    display_df: pd.DataFrame,
    *,
    sovereign_curve_family: str,
    ois_curve_family: str,
    tenor: str,
    z_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's z_score column into the canonical
    ``TimeSeries`` shape (closed-enum Z_SCORE units).

    Naming convention:
      ``<sov_lower>_<ois_lower>_<tenor_lower>_swap_spread_zscore``.

    Values match ``time_series[i].z_score`` 1-to-1 — both pass
    through ``safe_float(..., decimals=z_round_decimals)`` so the
    canonical and bespoke series cannot drift.
    """
    series_name = (
        f"{sovereign_curve_family.lower()}_"
        f"{ois_curve_family.lower()}_"
        f"{tenor.lower()}_swap_spread_zscore"
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
            f"Rolling z-score of the {sovereign_curve_family}−"
            f"{ois_curve_family} swap spread at {tenor} vs its own "
            "trailing window."
        ),
        rows=rows,
    )


def _build_canonical_swap_change_zscore_series(
    display_df: pd.DataFrame,
    *,
    sovereign_curve_family: str,
    ois_curve_family: str,
    tenor: str,
    z_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's ``change_z_score`` column into
    the canonical ``TimeSeries`` shape (closed-enum Z_SCORE units).

    Naming convention:
      ``<sov_lower>_<ois_lower>_<tenor_lower>_swap_spread_change_zscore``.

    This series is the canonical signal for "spread WIDENED a lot
    today" event-study questions (the canonical event-study proof Q1
    in the rates-agent workflow architecture).  Distinguished from
    ``time_series_zscore`` (which z-scores the spread LEVEL); the two
    co-exist so binding callers can pick level-stretch vs change-
    stretch event semantics explicitly.
    """
    series_name = (
        f"{sovereign_curve_family.lower()}_"
        f"{ois_curve_family.lower()}_"
        f"{tenor.lower()}_swap_spread_change_zscore"
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=safe_float(row.change_z_score, decimals=z_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"Rolling z-score of the day-over-day CHANGE in the "
            f"{sovereign_curve_family}−{ois_curve_family} swap spread "
            f"at {tenor} (z-score of spread.diff() vs its own trailing "
            "window).  Canonical signal for single-day-widening "
            "event-study questions."
        ),
        rows=rows,
    )
