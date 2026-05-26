"""
compute.py — Config-driven same-curve ZCIS tenor-spread primitive
==================================================================

Second tool in the ``inflation_swaps`` domain.  Owns the desk
concept of a same-curve zero-coupon inflation swap (ZCIS) tenor
spread between two pillars of the same ZCIS curve family
(e.g. USD_ZCIS 5s10s, EUR_ZCIS 5s30s, GBP_ZCIS 2s10s).  Computed
per-trade-date via the SPREAD formula:

    spread_bps = (long_zcis_pct - short_zcis_pct) * 100

where ``short_zcis_pct`` and ``long_zcis_pct`` are the ZCIS rate
levels at ``short_tenor`` and ``long_tenor`` respectively (each
computed by ``calculate_inflation_swap_rate_level``).
T_short / T_long are parsed from the tenor strings via the shared
``shared.analytics.curve_bootstrap.tenor_to_years`` helper purely
for desk-readable display in ``current_metrics.short_years`` /
``long_years`` (the spread itself is a difference, not a year-
weighted forward).

Composition path (deliberately simple)
--------------------------------------
This primitive composes two calls to
``calculate_inflation_swap_rate_level`` (one at ``short_tenor``,
one at ``long_tenor``) into the per-trade-date difference.  That
gets the load-bearing four-conjunct SELECT guard "for free":

  - ``instrument_type='inflation_swap'`` AND
    ``pricing_type='zero_coupon_breakeven'`` AND
    ``curve_family=<user>`` AND ``tenor=<user>``.

The level primitive's compute() is invoked with ``config=`` set
to THIS primitive's ToolConfig.  Every shared convention name is
present in both YAMLs with the same numeric value (the cross-
config lint enforces that), so the inner compute reads exactly
the same window / min_periods / ddof / buffer / ffill / rounding
values it would read from its own bundled config.  Tests that
override conventions on the curve-spread primitive's ToolConfig
automatically see those overrides flow through to the inner
level calls.

Same-curve invariant
--------------------
The two endpoint reads MUST share ``inflation_index_family``,
``index_lag``, ``interpolation``, and ``underlying_index`` — by
the same-curve invariant (a single ``curve_family`` field at the
input layer), this is structurally guaranteed.  We re-assert in
code to catch a real instrument_master inconsistency (e.g. one
pillar tagged with an outdated index_lag) rather than silently
average across mismatched conventions.  On mismatch we surface a
controlled error envelope.

Why an extended inner lookback
------------------------------
Each inner level call trims its time_series to the requested
``lookback_days`` worth of display history (anchored to the
latest observation date).  Our rolling z-score / trailing range
/ period changes need a longer history so they're fully
populated from the first row of OUR display window.  We
therefore pass ``lookback_days = params.lookback_days +
buffer_calendar_days`` to each inner level call, then align +
trim ourselves.  The buffer math matches the level primitive's
own buffer math (``max(z_window, trailing_window) *
buffer_multiplier``) so this primitive does not double-buffer.

Per-trade-date alignment
------------------------
The two endpoint ZCIS series are inner-joined on date so we only
emit rows where BOTH endpoints had data.  No ffill across the
join: dates where one endpoint has no data are dropped from the
spread series, NOT carried forward as synthetic spread points.

Trailing-range wire freeze
--------------------------
``trailing_range_window_days`` is locked at 252 in V1.  The
output schema's field names (``high_252d_bps``,
``low_252d_bps``, ``percentile_252d``) embed the number; changing
the convention without renaming the wire fields would silently
lie about what the percentile is computed against.  ``compute()``
raises ``NotImplementedError`` if this is set to anything else;
see ``methodology.planned_extensions`` for the path to making it
configurable.

Test seam
---------
``calculate_inflation_swap_rate_level`` is imported here at
module level; tests patch the inner primitive's own seams
(``rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar``,
``...compute.date``) so the inner endpoint calls hit the test
fixtures.  This module does NOT import ``date`` directly — the
fetch start-date logic lives entirely inside the level primitive,
which already exposes its own date seam.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.inflation_swaps.tools.inflation_swap_curve_spread.schemas import (
    InflationSwapCurveSpreadCurrentMetrics,
    InflationSwapCurveSpreadInput,
    InflationSwapCurveSpreadOutput,
    InflationSwapCurveSpreadTimeSeriesRow,
)
from rates_agent.inflation_swaps.tools.inflation_swap_rate_level import (
    InflationSwapRateLevelInput,
    calculate_inflation_swap_rate_level,
)
from shared.analytics.curve_bootstrap import tenor_to_years
from shared.analytics.levels import (
    period_changes,
    trailing_high_low_percentile,
)
from shared.analytics.spreads import (
    rolling_zscore,
    safe_float,
)
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


# Bundled config — public symbol so external callers (mcp_server,
# REST routes, tests, future batch surfaces) can build a ToolConfig
# from the same source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Trailing-range window is wire-frozen at 252 in V1; see schemas.py
# and config.yaml's ``planned_extensions``.
_FROZEN_TRAILING_WINDOW: int = 252


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull the methodology kwargs the inflation_swap_curve_spread
    compute() needs from a ToolConfig.

    Raises NotImplementedError if ``trailing_range_window_days``
    is set to a value the V1 wire surface cannot honestly
    represent.  See the module docstring for the wire-freeze
    rationale.
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
        "yield_round_decimals": config.convention_value("yield_round_decimals"),
        "window_years_round": config.convention_value(
            "window_years_round_decimals",
        ),
    }


# ============================================================================
# REFERENCE-METADATA EQUALITY GUARD
# ============================================================================

# Fields the same-curve invariant guarantees both legs share.  A
# mismatch surfaces a controlled error envelope — that would
# indicate an instrument_master inconsistency at the underlying
# pillars, not a methodology choice.
_REFERENCE_METADATA_FIELDS = (
    "inflation_index_family",
    "index_lag",
    "interpolation",
    "underlying_index",
)


def _assert_same_curve_reference_metadata(
    *,
    short_metrics: Dict[str, Any],
    long_metrics: Dict[str, Any],
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
) -> Optional[str]:
    """Re-assert that the two endpoint reads share the load-bearing
    reference metadata.  Returns ``None`` on match, an error string
    on mismatch.
    """
    mismatches = []
    for field in _REFERENCE_METADATA_FIELDS:
        s = short_metrics.get(field)
        l = long_metrics.get(field)
        if s != l:
            mismatches.append(
                f"{field}: short_tenor={short_tenor} → {s!r}, "
                f"long_tenor={long_tenor} → {l!r}"
            )
    if not mismatches:
        return None
    return (
        f"Reference-metadata mismatch on curve_family={curve_family!r} "
        f"between {short_tenor} and {long_tenor} legs: "
        + "; ".join(mismatches)
        + ".  The same-curve invariant guarantees these fields are "
        "identical across both pillars; a mismatch indicates a real "
        "instrument_master inconsistency at the underlying pillars.  "
        "Refer to the inflation_swaps playbook to confirm the "
        "intended convention before requesting this curve spread."
    )


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_inflation_swap_curve_spread(
    engine: Engine,
    params: InflationSwapCurveSpreadInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Calculate the same-curve ZCIS tenor spread between two
    pillars of the same ZCIS curve family.

        spread_bps = (long_zcis_pct - short_zcis_pct) * 100

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : InflationSwapCurveSpreadInput
        Validated input.  ``field_name=None`` resolves against
        the YAML's ``default_zcis_rate_field`` convention.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass
        a custom ToolConfig to exercise convention overrides;
        production wrappers must pass ``config=`` explicitly.

    Returns
    -------
    dict
        Serialised ``InflationSwapCurveSpreadOutput``, or
        ``{"error": "..."}`` on recoverable failure.
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
    period_offsets = conv["period_offsets"]
    trailing_window = conv["trailing_window"]
    bps_round_decimals = conv["bps_round_decimals"]
    yield_round_decimals = conv["yield_round_decimals"]
    window_years_round = conv["window_years_round"]

    # Wire-honesty disclosure threaded from YAML — NOT hardcoded.
    methodology_label = config.methodology.what_it_does.strip()

    # ------------------------------------------------------------------
    # Year fractions for display.  The Pydantic validator already
    # enforced parsability and ordering; re-running the parser here
    # is cheap and keeps compute() self-contained.
    # ------------------------------------------------------------------
    short_years = tenor_to_years(params.short_tenor)
    long_years = tenor_to_years(params.long_tenor)

    # ------------------------------------------------------------------
    # Extended inner lookback.  Each inner level call trims its
    # time_series to ``lookback_days`` worth of display history.  We
    # need a longer window so OUR rolling stats are fully populated
    # from the first row of OUR display window; match the buffer
    # math the level primitive uses internally so we don't double-
    # buffer.
    # ------------------------------------------------------------------
    buffer_calendar_days = int(
        max(z_window, trailing_window) * buffer_multiplier
    )
    extended_lookback_days = params.lookback_days + buffer_calendar_days

    # ------------------------------------------------------------------
    # Compose: call the ZCIS rate-level primitive once per endpoint
    # tenor.  This inherits the four-conjunct SELECT guard
    # (instrument_type + pricing_type + curve_family + tenor) for
    # free.  We pass ``config=`` so the inner conventions use the
    # same values this primitive uses; cross-config lint enforces
    # value-agreement on the bundled YAMLs, and any test override
    # on this primitive's ToolConfig flows through to the inner
    # level calls.
    # ------------------------------------------------------------------
    short_inner = calculate_inflation_swap_rate_level(
        engine=engine,
        params=InflationSwapRateLevelInput(
            curve_family=params.curve_family,
            tenor=params.short_tenor,
            lookback_days=extended_lookback_days,
            field_name=params.field_name,
        ),
        config=config,
    )
    if "error" in short_inner:
        return {
            "error": (
                f"ZCIS curve spread "
                f"{params.short_tenor}{params.long_tenor} could not be "
                f"computed for {params.curve_family}: the short-tenor "
                f"endpoint ({params.short_tenor}) failed.  Inner "
                f"error: {short_inner['error']}"
            )
        }

    long_inner = calculate_inflation_swap_rate_level(
        engine=engine,
        params=InflationSwapRateLevelInput(
            curve_family=params.curve_family,
            tenor=params.long_tenor,
            lookback_days=extended_lookback_days,
            field_name=params.field_name,
        ),
        config=config,
    )
    if "error" in long_inner:
        return {
            "error": (
                f"ZCIS curve spread "
                f"{params.short_tenor}{params.long_tenor} could not be "
                f"computed for {params.curve_family}: the long-tenor "
                f"endpoint ({params.long_tenor}) failed.  Inner "
                f"error: {long_inner['error']}"
            )
        }

    # ------------------------------------------------------------------
    # Re-assert the same-curve reference-metadata invariant.  By the
    # single-curve_family input shape this should always hold; a
    # mismatch indicates a real instrument_master inconsistency and
    # we surface a controlled error envelope rather than silently
    # picking one side.
    # ------------------------------------------------------------------
    short_metrics_inner = short_inner.get("current_metrics", {})
    long_metrics_inner = long_inner.get("current_metrics", {})
    ref_err = _assert_same_curve_reference_metadata(
        short_metrics=short_metrics_inner,
        long_metrics=long_metrics_inner,
        curve_family=params.curve_family,
        short_tenor=params.short_tenor,
        long_tenor=params.long_tenor,
    )
    if ref_err is not None:
        return {"error": ref_err}

    # ------------------------------------------------------------------
    # Align endpoint ZCIS series by date.  The level primitive's
    # canonical TimeSeries already has each row pre-rounded to
    # ``yield_round_decimals`` from the inner compute; we use those
    # rows directly so the canonical-shape contract is end-to-end.
    # Strict pandas inner-join on date so we only emit rows where
    # BOTH endpoints had data — no synthetic spread points are
    # produced on dates where one endpoint has no data.
    # ------------------------------------------------------------------
    short_rows = short_inner.get("time_series", {}).get("rows", [])
    long_rows = long_inner.get("time_series", {}).get("rows", [])
    if not short_rows or not long_rows:
        return {
            "error": (
                f"ZCIS curve spread "
                f"{params.short_tenor}{params.long_tenor} could not be "
                f"computed for {params.curve_family}: at least one "
                "endpoint returned no canonical TimeSeries rows.  "
                "This is a bug — the level primitive returned a "
                "snapshot but no history.  Please report with the "
                f"curve_family/short/long {params.curve_family} / "
                f"{params.short_tenor} / {params.long_tenor}."
            )
        }

    short_df = pd.DataFrame(short_rows).rename(
        columns={"value": "short_zcis_rate_pct"},
    )
    long_df = pd.DataFrame(long_rows).rename(
        columns={"value": "long_zcis_rate_pct"},
    )
    short_df["date"] = pd.to_datetime(short_df["date"])
    long_df["date"] = pd.to_datetime(long_df["date"])

    wide = (
        pd.merge(short_df, long_df, on="date", how="inner")
        .sort_values("date")
        .set_index("date")
    )

    # Drop any rows where either endpoint value is NaN — strict
    # inner-join discipline; no synthetic spread on partial data.
    wide = wide.dropna(
        subset=["short_zcis_rate_pct", "long_zcis_rate_pct"],
    )

    if wide.empty:
        return {
            "error": (
                f"After aligning the two endpoint ZCIS series for "
                f"{params.curve_family} ({params.short_tenor} → "
                f"{params.long_tenor}), no overlapping dates "
                "remain.  Verify both pillars have data on this "
                "curve."
            )
        }

    # ------------------------------------------------------------------
    # Spread formula:  spread_bps = (long_pct - short_pct) * 100
    # The endpoint ZCIS series come in already rounded to
    # yield_round_decimals (the level primitive's canonical
    # TimeSeries applies the rounding upstream), so we round the
    # spread once at the end of this arithmetic.  Same boundary-
    # rounding discipline as compute_spread_bps in the sovereign
    # curve_spread primitive.
    # ------------------------------------------------------------------
    wide["spread_bps"] = (
        (wide["long_zcis_rate_pct"] - wide["short_zcis_rate_pct"]) * 100
    ).round(bps_round_decimals)

    # ------------------------------------------------------------------
    # Rolling z-score on the spread bps series.
    # ------------------------------------------------------------------
    wide["z_score"] = rolling_zscore(
        wide["spread_bps"],
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_round_decimals,
    )

    # ------------------------------------------------------------------
    # Trim to requested display lookback — anchored to the data's
    # latest aligned trade_date.  Matches inflation_swap_rate_level /
    # breakeven_curve_spread / OIS rate_level.
    # ------------------------------------------------------------------
    as_of_ts = wide.index[-1]
    cutoff = pd.Timestamp(
        as_of_ts.date() - timedelta(days=params.lookback_days),
    )
    display_df = wide.loc[wide.index >= cutoff].copy()

    if display_df.empty:
        return {
            "error": (
                f"No ZCIS curve spread observations within the last "
                f"{params.lookback_days} days for "
                f"'{params.curve_family}' over "
                f"{params.short_tenor}{params.long_tenor}."
            )
        }

    # ------------------------------------------------------------------
    # Build current_metrics
    # ------------------------------------------------------------------
    latest = display_df.iloc[-1]
    spreads = display_df["spread_bps"]

    current_spread_bps = safe_float(
        latest["spread_bps"], decimals=bps_round_decimals,
    )

    # Daily / weekly / monthly bps changes — series is already in
    # bps so already_bps=True (plain subtraction).  decimals=
    # passes the YAML convention through so the changes respect
    # bps_round_decimals.
    changes = period_changes(
        spreads,
        offsets=period_offsets,
        already_bps=True,
        decimals=bps_round_decimals,
    )

    # Trailing high/low/percentile of the ZCIS curve spread (bps
    # scale).
    high, low, percentile = trailing_high_low_percentile(
        spreads, window=trailing_window, decimals=bps_round_decimals,
    )

    spread_label = (
        f"{params.curve_family} "
        f"{params.short_tenor.replace('Y', '')}s"
        f"{params.long_tenor.replace('Y', '')}s"
    )

    metrics = InflationSwapCurveSpreadCurrentMetrics(
        as_of_date=as_of_ts.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        short_tenor=params.short_tenor,
        long_tenor=params.long_tenor,
        spread_label=spread_label,
        spread_bps=current_spread_bps,
        change_1d_bps=changes["daily"],
        change_1w_bps=changes["weekly"],
        change_1m_bps=changes["monthly"],
        # Pass z_round_decimals so a YAML override above 4 isn't
        # silently truncated by safe_float's default of 4.
        z_score_252d=safe_float(
            latest.get("z_score"), decimals=z_round_decimals,
        ),
        high_252d_bps=high,
        low_252d_bps=low,
        percentile_252d=percentile,
        short_zcis_rate_pct=safe_float(
            latest.get("short_zcis_rate_pct"),
            decimals=yield_round_decimals,
        ),
        long_zcis_rate_pct=safe_float(
            latest.get("long_zcis_rate_pct"),
            decimals=yield_round_decimals,
        ),
        short_years=round(short_years, window_years_round),
        long_years=round(long_years, window_years_round),
        observation_count=len(display_df),
        inflation_index_family=(
            short_metrics_inner.get("inflation_index_family") or ""
        ),
        index_lag=short_metrics_inner.get("index_lag") or "",
        interpolation=short_metrics_inner.get("interpolation") or "",
        underlying_index=short_metrics_inner.get("underlying_index"),
        methodology_label=methodology_label,
    )

    # ------------------------------------------------------------------
    # Build time_series (bespoke shape) AND the two canonical
    # TimeSeries (spread in BPS, z-score in Z_SCORE).  All three
    # share the same display_df rows so they cannot drift.
    # ------------------------------------------------------------------
    ts_rows = [
        InflationSwapCurveSpreadTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            spread_bps=round(row.spread_bps, bps_round_decimals),
            z_score=safe_float(row.z_score, decimals=z_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    canonical_spread = _build_canonical_spread_series(
        display_df,
        curve_family=params.curve_family,
        short_tenor=params.short_tenor,
        long_tenor=params.long_tenor,
        bps_round_decimals=bps_round_decimals,
        inflation_index_family=(
            short_metrics_inner.get("inflation_index_family") or ""
        ),
        index_lag=short_metrics_inner.get("index_lag") or "",
        interpolation=short_metrics_inner.get("interpolation") or "",
    )
    canonical_zscore = _build_canonical_zscore_series(
        display_df,
        curve_family=params.curve_family,
        short_tenor=params.short_tenor,
        long_tenor=params.long_tenor,
        z_round_decimals=z_round_decimals,
    )

    output = InflationSwapCurveSpreadOutput(
        current_metrics=metrics,
        time_series=ts_rows,
        time_series_spread=canonical_spread,
        time_series_zscore=canonical_zscore,
    )
    return output.model_dump()


# ============================================================================
# CANONICAL TIME-SERIES BUILDERS
# ============================================================================

def _series_name_slug(
    *,
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
) -> str:
    """Lower-snake-case slug used as the canonical TimeSeries
    series_name prefix.  Pattern:
    ``<curve_family>_<short_tenor>_<long_tenor>``.
    """
    return (
        f"{curve_family.lower()}_"
        f"{short_tenor.lower()}_"
        f"{long_tenor.lower()}"
    )


def _build_canonical_spread_series(
    display_df: pd.DataFrame,
    *,
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
    bps_round_decimals: int,
    inflation_index_family: str,
    index_lag: str,
    interpolation: str,
) -> TimeSeries:
    """Convert the display DataFrame's spread_bps column into the
    canonical ``TimeSeries`` shape (closed-enum BPS units).

    Naming convention:
    ``<curve_family>_<short_tenor>_<long_tenor>_zcis_curve_spread``.
    """
    slug = _series_name_slug(
        curve_family=curve_family,
        short_tenor=short_tenor,
        long_tenor=long_tenor,
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=round(float(row.spread_bps), bps_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=f"{slug}_zcis_curve_spread",
        units=TimeSeriesUnits.BPS,
        description=(
            f"Same-curve ZCIS tenor spread for {curve_family} "
            f"({short_tenor}/{long_tenor}; (long_zcis_pct - "
            "short_zcis_pct) * 100) over the displayed window.  "
            "Reference: "
            f"inflation_index_family={inflation_index_family!r}, "
            f"index_lag={index_lag!r}, "
            f"interpolation={interpolation!r}.  These conventions "
            "differ across USD_ZCIS / EUR_ZCIS / GBP_ZCIS — same-"
            "curve spreads are interpretable under their own curve's "
            "convention only; cross-curve combinations are a "
            "separate primitive."
        ),
        rows=rows,
    )


def _build_canonical_zscore_series(
    display_df: pd.DataFrame,
    *,
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
    z_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's z_score column into the
    canonical ``TimeSeries`` shape (closed-enum Z_SCORE units).

    Naming convention:
    ``<curve_family>_<short_tenor>_<long_tenor>_zcis_curve_spread_zscore``.
    Values match ``time_series[i].z_score`` 1-to-1 (rounded via the
    same ``z_score_round_decimals`` convention applied to the
    bespoke field) so the two series cannot drift.
    """
    slug = _series_name_slug(
        curve_family=curve_family,
        short_tenor=short_tenor,
        long_tenor=long_tenor,
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=safe_float(row.z_score, decimals=z_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=f"{slug}_zcis_curve_spread_zscore",
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"Rolling z-score of the {curve_family} ZCIS curve "
            f"spread (in bps) over {short_tenor}/{long_tenor} vs "
            "its own trailing window."
        ),
        rows=rows,
    )
