"""
compute.py — Config-driven same-curve ZCIS forward-rate primitive
==================================================================

Third tool in the ``inflation_swaps`` domain.  Owns the desk
concept of a same-curve forward zero-coupon inflation swap (ZCIS)
rate between two pillars on the same ZCIS curve family
(e.g. USD_ZCIS 5Y5Y, EUR_ZCIS 5Y5Y, GBP_ZCIS 2Y3Y).  Computed
per-trade-date via the DUAL-COMPOUNDING GEOMETRIC formula:

    (1 + r_short)^T_short * (1 + f)^(T_long - T_short) =
        (1 + r_long)^T_long

    f = ((1 + r_long)^T_long / (1 + r_short)^T_short)
            ^ (1 / (T_long - T_short)) - 1

where ``r_short`` and ``r_long`` are the ZCIS rate levels at
``start_tenor`` and ``end_tenor`` respectively (each computed by
``calculate_inflation_swap_rate_level``).  ``T_short`` / ``T_long``
are parsed from the tenor strings via the shared
``shared.analytics.curve_bootstrap.tenor_to_years`` helper (NOT a
bespoke parser).

Concept honesty
---------------
This tool returns FORWARD INFLATION COMPENSATION, NOT pure forward
expected inflation.  ZCIS still carries an inflation risk premium
and a (smaller, but non-zero) liquidity premium, and the geometric
forward inherits both.  The honesty disclosure lives in the YAML's
``methodology.what_it_does`` and is threaded onto the wire via
``current_metrics.methodology_label``.  That field is sourced from
the YAML at runtime — NOT hardcoded as a Python literal — so a
YAML edit flows through to runtime behaviour.

Composition path (deliberately simple)
--------------------------------------
This primitive composes two calls to
``calculate_inflation_swap_rate_level`` (one at ``start_tenor``,
one at ``end_tenor``) into the geometric forward.  That gets the
load-bearing four-conjunct SELECT guard "for free":

  - ``instrument_type='inflation_swap'`` AND
    ``pricing_type='zero_coupon_breakeven'`` AND
    ``curve_family=<user>`` AND ``tenor=<user>``.

The level primitive's compute() is invoked with ``config=`` set to
THIS primitive's ToolConfig.  Every shared convention name is
present in both YAMLs with the same numeric value (the cross-
config lint enforces that), so the inner compute reads exactly the
same window / min_periods / ddof / buffer / ffill / rounding values
it would read from its own bundled config.  Tests that override
conventions on the forward primitive's ToolConfig automatically see
those overrides flow through to the inner level calls.

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
latest observation date).  Our rolling z-score / trailing range /
period changes need a longer history so they're fully populated
from the first row of OUR display window.  We therefore pass
``lookback_days = params.lookback_days + buffer_calendar_days`` to
each inner level call, then align + trim ourselves.  The buffer
math matches the level primitive's own buffer math
(``max(z_window, trailing_window) * buffer_multiplier``) so this
primitive does not double-buffer.

Per-trade-date alignment
------------------------
The two endpoint ZCIS series are inner-joined on date so we only
emit rows where BOTH endpoints had data.  No ffill across the
join: dates where one endpoint has no data are dropped from the
forward series, NOT carried forward as synthetic forward points.

Forward formula (dual-compounding geometric)
--------------------------------------------
``forward_compounding_mode`` is locked at ``dual_compounding`` in
V1.  See ``methodology.what_it_does`` for the literal formula and
``methodology.planned_extensions`` for the year_weighted_linear
alternative (the desk shorthand and the default in
forward_breakeven_simple, where breakeven is an inflation-
compensation differential rather than a true zero-coupon rate).
``compute()`` raises ``NotImplementedError`` if this convention is
set to anything else; the wire-disclosed ``methodology_label`` and
the SQL validator must move in lockstep with this convention so
the desk user can never see a label that disagrees with the math.

Trailing-range wire freeze
--------------------------
``trailing_range_window_days`` is locked at 252 in V1.  The output
schema's field names (``high_252d_bps``, ``low_252d_bps``,
``percentile_252d``) embed the number; changing the convention
without renaming the wire fields would silently lie about what the
percentile is computed against.  ``compute()`` raises
``NotImplementedError`` if this is set to anything else; see
``methodology.planned_extensions`` for the path to making it
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

from rates_agent.inflation_swaps.tools.inflation_swap_forward.schemas import (
    InflationSwapForwardCurrentMetrics,
    InflationSwapForwardInput,
    InflationSwapForwardOutput,
    InflationSwapForwardTimeSeriesRow,
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


# Forward-formula compounding mode is wire-frozen at
# ``dual_compounding`` in V1.  See config.yaml's
# ``methodology.planned_extensions`` for the year_weighted_linear
# alternative path.
_SUPPORTED_FORWARD_FORMULA: str = "dual_compounding"


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull the methodology kwargs the inflation_swap_forward
    compute() needs from a ToolConfig.

    Raises NotImplementedError if either ``trailing_range_window_days``
    or ``forward_compounding_mode`` is set to a value the V1 wire
    surface cannot honestly represent.  See the module docstring for
    the wire-freeze rationale on each.
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

    formula = config.convention_value("zcis_forward_compounding_mode")
    if formula != _SUPPORTED_FORWARD_FORMULA:
        raise NotImplementedError(
            f"zcis_forward_compounding_mode={formula!r} is documented in "
            f"this tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions for the year_weighted_linear "
            f"alternative) but is not yet implemented.  V1 supports only "
            f"{_SUPPORTED_FORWARD_FORMULA!r} because the wire-disclosed "
            f"methodology_label and the SQL validator must move in "
            f"lockstep with this convention; supporting a second value "
            f"requires updating both at the same time so a desk user "
            f"can never see a label that disagrees with the math."
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
        "forward_formula": formula,
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
    start_tenor: str,
    end_tenor: str,
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
                f"{field}: start_tenor={start_tenor} → {s!r}, "
                f"end_tenor={end_tenor} → {l!r}"
            )
    if not mismatches:
        return None
    return (
        f"Reference-metadata mismatch on curve_family={curve_family!r} "
        f"between {start_tenor} and {end_tenor} legs: "
        + "; ".join(mismatches)
        + ".  The same-curve invariant guarantees these fields are "
        "identical across both pillars; a mismatch indicates a real "
        "instrument_master inconsistency at the underlying pillars.  "
        "Refer to the inflation_swaps playbook to confirm the "
        "intended convention before requesting this forward."
    )


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_inflation_swap_forward(
    engine: Engine,
    params: InflationSwapForwardInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Calculate the same-curve ZCIS forward rate between two
    pillars on the same ZCIS curve family.

        f = ((1 + r_long)^T_long / (1 + r_short)^T_short)
                ^ (1 / (T_long - T_short)) - 1

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : InflationSwapForwardInput
        Validated input.  ``field_name=None`` resolves against the
        YAML's ``default_zcis_rate_field`` convention.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass
        a custom ToolConfig to exercise convention overrides;
        production wrappers must pass ``config=`` explicitly.

    Returns
    -------
    dict
        Serialised ``InflationSwapForwardOutput``, or
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
    # Year fractions for the forward window.  The Pydantic validator
    # already enforced parsability and ordering; re-running the parser
    # here is cheap and keeps compute() self-contained.
    # ------------------------------------------------------------------
    start_years = tenor_to_years(params.start_tenor)
    end_years = tenor_to_years(params.end_tenor)
    dt_years = end_years - start_years

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
            tenor=params.start_tenor,
            lookback_days=extended_lookback_days,
            field_name=params.field_name,
        ),
        config=config,
    )
    if "error" in short_inner:
        return {
            "error": (
                f"ZCIS forward "
                f"{params.start_tenor}{params.end_tenor} could not be "
                f"computed for {params.curve_family}: the start-tenor "
                f"endpoint ({params.start_tenor}) failed.  Inner "
                f"error: {short_inner['error']}"
            )
        }

    long_inner = calculate_inflation_swap_rate_level(
        engine=engine,
        params=InflationSwapRateLevelInput(
            curve_family=params.curve_family,
            tenor=params.end_tenor,
            lookback_days=extended_lookback_days,
            field_name=params.field_name,
        ),
        config=config,
    )
    if "error" in long_inner:
        return {
            "error": (
                f"ZCIS forward "
                f"{params.start_tenor}{params.end_tenor} could not be "
                f"computed for {params.curve_family}: the end-tenor "
                f"endpoint ({params.end_tenor}) failed.  Inner "
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
        start_tenor=params.start_tenor,
        end_tenor=params.end_tenor,
    )
    if ref_err is not None:
        return {"error": ref_err}

    # ------------------------------------------------------------------
    # Align endpoint ZCIS series by date.  The level primitive's
    # canonical TimeSeries already has each row pre-rounded to
    # ``yield_round_decimals`` from the inner compute; we use those
    # rows directly so the canonical-shape contract is end-to-end.
    # Strict pandas inner-join on date so we only emit rows where
    # BOTH endpoints had data — no synthetic forward points are
    # produced on dates where one endpoint has no data.
    # ------------------------------------------------------------------
    short_rows = short_inner.get("time_series", {}).get("rows", [])
    long_rows = long_inner.get("time_series", {}).get("rows", [])
    if not short_rows or not long_rows:
        return {
            "error": (
                f"ZCIS forward "
                f"{params.start_tenor}{params.end_tenor} could not be "
                f"computed for {params.curve_family}: at least one "
                "endpoint returned no canonical TimeSeries rows.  "
                "This is a bug — the level primitive returned a "
                "snapshot but no history.  Please report with the "
                f"curve_family/start/end {params.curve_family} / "
                f"{params.start_tenor} / {params.end_tenor}."
            )
        }

    short_df = pd.DataFrame(short_rows).rename(
        columns={"value": "start_zcis_pct"},
    )
    long_df = pd.DataFrame(long_rows).rename(
        columns={"value": "end_zcis_pct"},
    )
    short_df["date"] = pd.to_datetime(short_df["date"])
    long_df["date"] = pd.to_datetime(long_df["date"])

    wide = (
        pd.merge(short_df, long_df, on="date", how="inner")
        .sort_values("date")
        .set_index("date")
    )

    # Drop any rows where either endpoint value is NaN — strict
    # inner-join discipline; no synthetic forward on partial data.
    wide = wide.dropna(
        subset=["start_zcis_pct", "end_zcis_pct"],
    )

    if wide.empty:
        return {
            "error": (
                f"After aligning the two endpoint ZCIS series for "
                f"{params.curve_family} ({params.start_tenor} → "
                f"{params.end_tenor}), no overlapping dates remain.  "
                "Verify both pillars have data on this curve."
            )
        }

    # ------------------------------------------------------------------
    # Dual-compounding geometric forward formula:
    #   r_short_dec = r_short_pct / 100
    #   r_long_dec  = r_long_pct  / 100
    #   f_dec = ((1 + r_long_dec)^T_long / (1 + r_short_dec)^T_short)
    #               ^ (1 / (T_long - T_short)) - 1
    #   forward_pct = f_dec * 100
    #
    # The endpoint ZCIS series come in already rounded to
    # yield_round_decimals (the level primitive's canonical
    # TimeSeries applies the rounding upstream).  We round the
    # forward percent to yield_round_decimals once and derive bps
    # from it so the boundary parity holds:
    #   forward_zcis_bps == round(forward_zcis_pct * 100, bps_round_decimals).
    # Same boundary-rounding discipline as forward_breakeven_simple
    # (modulo unit choice — its forward TimeSeries ships in BPS, ours
    # in PERCENT because ZCIS is a true zero-coupon rate).
    # ------------------------------------------------------------------
    short_dec = wide["start_zcis_pct"] / 100.0
    long_dec = wide["end_zcis_pct"] / 100.0
    forward_dec = (
        ((1.0 + long_dec) ** end_years)
        / ((1.0 + short_dec) ** start_years)
    ) ** (1.0 / dt_years) - 1.0
    wide["forward_zcis_pct"] = (forward_dec * 100.0).round(
        yield_round_decimals,
    )
    wide["forward_zcis_bps"] = (wide["forward_zcis_pct"] * 100.0).round(
        bps_round_decimals,
    )

    # ------------------------------------------------------------------
    # Rolling z-score on the forward bps series (BPS scale, mirroring
    # forward_breakeven_simple / inflation_swap_curve_spread).
    # ------------------------------------------------------------------
    wide["z_score"] = rolling_zscore(
        wide["forward_zcis_bps"],
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_round_decimals,
    )

    # ------------------------------------------------------------------
    # Trim to requested display lookback — anchored to the data's
    # latest aligned trade_date.  Matches inflation_swap_rate_level /
    # inflation_swap_curve_spread / breakeven_curve_spread / OIS
    # rate_level.
    # ------------------------------------------------------------------
    as_of_ts = wide.index[-1]
    cutoff = pd.Timestamp(
        as_of_ts.date() - timedelta(days=params.lookback_days),
    )
    display_df = wide.loc[wide.index >= cutoff].copy()

    if display_df.empty:
        return {
            "error": (
                f"No ZCIS forward observations within the last "
                f"{params.lookback_days} days for "
                f"'{params.curve_family}' over "
                f"{params.start_tenor}{params.end_tenor}."
            )
        }

    # ------------------------------------------------------------------
    # Build current_metrics
    # ------------------------------------------------------------------
    latest = display_df.iloc[-1]
    forwards = display_df["forward_zcis_bps"]

    current_forward_pct = safe_float(
        latest["forward_zcis_pct"], decimals=yield_round_decimals,
    )
    current_forward_bps = safe_float(
        latest["forward_zcis_bps"], decimals=bps_round_decimals,
    )

    # Daily / weekly / monthly bps changes — series is already in
    # bps so already_bps=True (plain subtraction).  decimals=
    # passes the YAML convention through so the changes respect
    # bps_round_decimals.
    changes = period_changes(
        forwards,
        offsets=period_offsets,
        already_bps=True,
        decimals=bps_round_decimals,
    )

    # Trailing high/low/percentile of the forward (bps scale).
    high, low, percentile = trailing_high_low_percentile(
        forwards, window=trailing_window, decimals=bps_round_decimals,
    )

    forward_window_label = (
        f"{params.curve_family} "
        f"{params.start_tenor}{params.end_tenor}"
    )

    metrics = InflationSwapForwardCurrentMetrics(
        as_of_date=as_of_ts.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        start_tenor=params.start_tenor,
        end_tenor=params.end_tenor,
        forward_window_label=forward_window_label,
        forward_zcis_pct=current_forward_pct,
        forward_zcis_bps=current_forward_bps,
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
        start_zcis_pct=safe_float(
            latest.get("start_zcis_pct"),
            decimals=yield_round_decimals,
        ),
        end_zcis_pct=safe_float(
            latest.get("end_zcis_pct"),
            decimals=yield_round_decimals,
        ),
        start_years=round(start_years, window_years_round),
        end_years=round(end_years, window_years_round),
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
    # TimeSeries (forward in PERCENT, z-score in Z_SCORE).  All three
    # share the same display_df rows so they cannot drift.
    # ------------------------------------------------------------------
    ts_rows = [
        InflationSwapForwardTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            forward_zcis_pct=round(
                row.forward_zcis_pct, yield_round_decimals,
            ),
            forward_zcis_bps=round(
                row.forward_zcis_bps, bps_round_decimals,
            ),
            z_score=safe_float(row.z_score, decimals=z_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    canonical_forward = _build_canonical_forward_series(
        display_df,
        curve_family=params.curve_family,
        start_tenor=params.start_tenor,
        end_tenor=params.end_tenor,
        yield_round_decimals=yield_round_decimals,
        inflation_index_family=(
            short_metrics_inner.get("inflation_index_family") or ""
        ),
        index_lag=short_metrics_inner.get("index_lag") or "",
        interpolation=short_metrics_inner.get("interpolation") or "",
    )
    canonical_zscore = _build_canonical_zscore_series(
        display_df,
        curve_family=params.curve_family,
        start_tenor=params.start_tenor,
        end_tenor=params.end_tenor,
        z_round_decimals=z_round_decimals,
    )

    output = InflationSwapForwardOutput(
        current_metrics=metrics,
        time_series=ts_rows,
        time_series_forward=canonical_forward,
        time_series_zscore=canonical_zscore,
    )
    return output.model_dump()


# ============================================================================
# CANONICAL TIME-SERIES BUILDERS
# ============================================================================

def _series_name_slug(
    *,
    curve_family: str,
    start_tenor: str,
    end_tenor: str,
) -> str:
    """Lower-snake-case slug used as the canonical TimeSeries
    series_name prefix.  Pattern:
    ``<curve_family>_<start_tenor>_<end_tenor>``.
    """
    return (
        f"{curve_family.lower()}_"
        f"{start_tenor.lower()}_"
        f"{end_tenor.lower()}"
    )


def _build_canonical_forward_series(
    display_df: pd.DataFrame,
    *,
    curve_family: str,
    start_tenor: str,
    end_tenor: str,
    yield_round_decimals: int,
    inflation_index_family: str,
    index_lag: str,
    interpolation: str,
) -> TimeSeries:
    """Convert the display DataFrame's forward_zcis_pct column into
    the canonical ``TimeSeries`` shape (closed-enum PERCENT units).

    Forward ZCIS is a *level*, not a spread, so the canonical
    TimeSeries ships in PERCENT (mirrors OIS forward_rate; do NOT
    confuse with curve-spread tools that ship in BPS).

    Naming convention:
    ``<curve_family>_<start_tenor>_<end_tenor>_zcis_forward``.
    """
    slug = _series_name_slug(
        curve_family=curve_family,
        start_tenor=start_tenor,
        end_tenor=end_tenor,
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=round(float(row.forward_zcis_pct), yield_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=f"{slug}_zcis_forward",
        units=TimeSeriesUnits.PERCENT,
        description=(
            f"Same-curve forward zero-coupon inflation swap (ZCIS) "
            f"rate for {curve_family} {start_tenor}{end_tenor} via the "
            "dual-compounding geometric formula on the two endpoint "
            "ZCIS rates.  Forward inflation compensation; not a clean "
            "forward expected-inflation read (ZCIS still carries "
            "inflation risk premium and a smaller liquidity premium).  "
            "Reference: "
            f"inflation_index_family={inflation_index_family!r}, "
            f"index_lag={index_lag!r}, "
            f"interpolation={interpolation!r}.  These conventions "
            "differ across USD_ZCIS / EUR_ZCIS / GBP_ZCIS — same-"
            "curve forwards are interpretable under their own curve's "
            "convention only; cross-curve forwards are out of scope "
            "for V1."
        ),
        rows=rows,
    )


def _build_canonical_zscore_series(
    display_df: pd.DataFrame,
    *,
    curve_family: str,
    start_tenor: str,
    end_tenor: str,
    z_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's z_score column into the
    canonical ``TimeSeries`` shape (closed-enum Z_SCORE units).

    Naming convention:
    ``<curve_family>_<start_tenor>_<end_tenor>_zcis_forward_zscore``.
    Values match ``time_series[i].z_score`` 1-to-1 (rounded via the
    same ``z_score_round_decimals`` convention applied to the
    bespoke field) so the two series cannot drift.
    """
    slug = _series_name_slug(
        curve_family=curve_family,
        start_tenor=start_tenor,
        end_tenor=end_tenor,
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=safe_float(row.z_score, decimals=z_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=f"{slug}_zcis_forward_zscore",
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"Rolling z-score of the {curve_family} ZCIS forward "
            f"rate (in bps) over {start_tenor}{end_tenor} vs its own "
            "trailing window."
        ),
        rows=rows,
    )
