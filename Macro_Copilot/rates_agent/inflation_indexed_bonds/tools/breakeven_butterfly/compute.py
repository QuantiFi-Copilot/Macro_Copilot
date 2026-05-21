"""
compute.py — Config-driven same-country breakeven butterfly
============================================================

Owns the desk concept of a *same-country bond-implied breakeven
butterfly* (3-point breakeven-inflation-compensation curvature) on a
single nominal/linker pair (e.g. UST/USD_TIPS 5s10s30s breakeven
butterfly, UK_GILT/GBP_LINKER 2s10s30s breakeven butterfly,
FR_OAT/EUR_FR_LINKER 2s5s10s breakeven butterfly,
CANADA_GOVT/CAD_RRB 5s10s30s breakeven butterfly).  Computed per-
trade-date via the FIXED simple-butterfly weighting:

    butterfly_bps = belly_breakeven_bps
                  - 0.5 * (short_breakeven_bps + long_breakeven_bps)

Equivalent to ``0.5 * (2 * belly - short - long)``.  Sign convention:
POSITIVE = belly is CHEAP versus the half-weighted wings (i.e. belly
breakeven is HIGH relative to the wings); NEGATIVE = belly is RICH.
Matches the sovereign_bonds/butterfly tool's and
inflation_indexed_bonds/real_yield_butterfly tool's sign convention.

where ``short_breakeven_bps`` / ``belly_breakeven_bps`` /
``long_breakeven_bps`` are the spot bond-implied breakevens at
``short_tenor`` / ``belly_tenor`` / ``long_tenor`` respectively (each
computed by ``calculate_breakeven_inflation_simple``).  T_short /
T_belly / T_long are parsed from the tenor strings via the shared
``shared.analytics.curve_bootstrap.tenor_to_years`` helper purely for
desk-readable display in ``current_metrics.short_years`` /
``belly_years`` / ``long_years`` (the butterfly itself is a fixed-
weighted combination, not a year-weighted forward).

Composition path (deliberately simple)
--------------------------------------
This primitive composes three calls to
``calculate_breakeven_inflation_simple`` (one at ``short_tenor``,
one at ``belly_tenor``, one at ``long_tenor``) into the per-trade-
date butterfly.  That gets two load-bearing guards "for free":

  - Linker / nominal instrument_type filter on each leg — i.e. the
    no-proxy guard documented in the spot primitive's compute()
    docstring (DESIGN_PRINCIPLES.md §1, §3 /
    STANDARD_TOOL_AND_YAML_RULES.md §J).
  - Same-country / same-currency invariant — i.e. the
    ``_enforce_same_country_invariant`` helper that fires BEFORE
    any market-data SELECT (DESIGN_PRINCIPLES.md §5 /
    STANDARD_TOOL_AND_YAML_RULES.md §H).

Both guards run on EACH inner endpoint call; if any endpoint's spot
breakeven returns its own controlled error envelope, this tool
surfaces a controlled error envelope of its own that names which
endpoint failed and propagates the inner error context.

The spot primitive's compute() is invoked with ``config=`` set to
THIS primitive's ToolConfig.  Every shared convention name is present
in both YAMLs with the same numeric value (the cross-config lint
enforces that), so the inner compute reads exactly the same window /
min_periods / ddof / buffer / ffill / rounding values it would read
from its own bundled config.  Tests that override conventions on the
butterfly primitive's ToolConfig automatically see those overrides
flow through to the inner spot calls.

Why an extended inner lookback
------------------------------
Each inner spot call trims its time_series_breakeven to
``lookback_days`` worth of display history (anchored to the latest
aligned trade_date).  Our rolling z-score / trailing range / period
changes need a longer history so they're fully populated from the
first row of OUR display window.  We therefore pass ``lookback_days =
params.lookback_days + buffer_calendar_days`` to each inner spot
call, then align + trim ourselves.  The buffer math matches the spot
primitive's own buffer math (``max(z_window, trailing_window) *
buffer_multiplier``) so this primitive does not double-buffer.

Per-trade-date alignment
------------------------
The three endpoint breakeven series are inner-joined on date so we
only emit rows where ALL THREE endpoints had data — matches the
real_yield_butterfly outer alignment step.  No ffill across the
join: dates where any endpoint has no data are dropped from the
butterfly series, NOT carried forward as synthetic butterfly points.

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
``calculate_breakeven_inflation_simple`` is imported here at module
level; tests patch the inner primitive's own seams
(``rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor``,
``...compute._fetch_curve_family_country_currency``,
``...compute.date``) so the inner endpoint calls hit the test
fixtures.  This module does NOT import ``date`` directly — the fetch
start-date logic lives entirely inside the spot primitive, which
already exposes its own date seam.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly.schemas import (
    BreakevenButterflyCurrentMetrics,
    BreakevenButterflyInput,
    BreakevenButterflyOutput,
    BreakevenButterflyTimeSeriesRow,
)
from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple import (
    BreakevenInflationSimpleInput,
    calculate_breakeven_inflation_simple,
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
    """Pull the methodology kwargs the breakeven_butterfly compute()
    needs from a ToolConfig.

    Raises NotImplementedError if ``trailing_range_window_days`` is
    set to a value the V1 wire surface cannot honestly represent.
    See the module docstring for the wire-freeze rationale.
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
# PUBLIC API
# ============================================================================

def calculate_breakeven_butterfly(
    engine: Engine,
    params: BreakevenButterflyInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Calculate the same-country bond-implied breakeven butterfly
    (3-point curvature) on a single nominal/linker pair.

        butterfly_bps = belly_breakeven_bps
                      - 0.5 * (short_breakeven_bps
                               + long_breakeven_bps)

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : BreakevenButterflyInput
        Validated input.  ``field_name=None`` resolves against the
        YAML's ``default_field_name`` convention.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides; production
        wrappers must pass ``config=`` explicitly.

    Returns
    -------
    dict
        Serialised ``BreakevenButterflyOutput``, or
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
    window_years_round = conv["window_years_round"]

    # Wire-honesty disclosure threaded from YAML — NOT hardcoded.
    methodology_label = config.methodology.what_it_does.strip()

    # ------------------------------------------------------------------
    # Year fractions for display.  The Pydantic validator already
    # enforced parsability and ordering; re-running the parser here
    # is cheap and keeps compute() self-contained.
    # ------------------------------------------------------------------
    short_years = tenor_to_years(params.short_tenor)
    belly_years = tenor_to_years(params.belly_tenor)
    long_years = tenor_to_years(params.long_tenor)

    # ------------------------------------------------------------------
    # Extended inner lookback.  Each inner spot call trims its
    # time_series_breakeven to its own ``lookback_days`` of display
    # history.  We need a longer window so OUR rolling stats are
    # fully populated from the first row of OUR display window;
    # match the buffer math the spot primitive uses internally so
    # we don't double-buffer.
    # ------------------------------------------------------------------
    buffer_calendar_days = int(
        max(z_window, trailing_window) * buffer_multiplier
    )
    extended_lookback_days = params.lookback_days + buffer_calendar_days

    # ------------------------------------------------------------------
    # Compose: call the spot bond-implied breakeven primitive once
    # per endpoint tenor.  This inherits the no-proxy guard
    # (instrument_type filter on each leg) and the same-country
    # invariant (instrument_master (country, currency) check before
    # any market-data SELECT) for free.  We pass ``config=`` so the
    # inner conventions use the same values this primitive uses;
    # cross-config lint enforces value-agreement on the bundled
    # YAMLs, and any test override on this primitive's ToolConfig
    # flows through to the inner spot calls.
    # ------------------------------------------------------------------
    endpoint_specs = (
        ("short", params.short_tenor),
        ("belly", params.belly_tenor),
        ("long", params.long_tenor),
    )
    inner_results: Dict[str, Dict[str, Any]] = {}
    for endpoint_label, tenor in endpoint_specs:
        result = calculate_breakeven_inflation_simple(
            engine=engine,
            params=BreakevenInflationSimpleInput(
                nominal_curve_family=params.nominal_curve_family,
                linker_curve_family=params.linker_curve_family,
                tenor=tenor,
                lookback_days=extended_lookback_days,
                field_name=params.field_name,
            ),
            config=config,
        )
        if "error" in result:
            return {
                "error": (
                    f"Breakeven butterfly "
                    f"{params.short_tenor}{params.belly_tenor}"
                    f"{params.long_tenor} could not be computed for "
                    f"{params.nominal_curve_family} vs "
                    f"{params.linker_curve_family}: the "
                    f"{endpoint_label}-tenor endpoint ({tenor}) "
                    f"failed.  Inner error: {result['error']}"
                )
            }
        inner_results[endpoint_label] = result

    # ------------------------------------------------------------------
    # Align endpoint breakevens by date.  The spot primitive's
    # canonical TimeSeries already has each row pre-rounded to
    # ``bps_round_decimals`` from the inner compute; we use those
    # rows directly (NOT the bespoke ``time_series`` field) so the
    # canonical-shape contract is end-to-end.  Strict pandas inner-
    # join on date so we only emit rows where ALL THREE endpoints
    # had data — no synthetic butterfly points are produced on
    # dates where any endpoint has no data.
    # ------------------------------------------------------------------
    endpoint_frames: Dict[str, pd.DataFrame] = {}
    for endpoint_label in ("short", "belly", "long"):
        rows = inner_results[endpoint_label].get(
            "time_series_breakeven", {},
        ).get("rows", [])
        if not rows:
            return {
                "error": (
                    f"Breakeven butterfly "
                    f"{params.short_tenor}{params.belly_tenor}"
                    f"{params.long_tenor} could not be computed for "
                    f"{params.nominal_curve_family} vs "
                    f"{params.linker_curve_family}: the "
                    f"{endpoint_label}-tenor endpoint returned no "
                    "canonical TimeSeries rows.  This is a bug — "
                    "the spot primitive returned a snapshot but no "
                    "history.  Please report with the curve_family "
                    f"pair {params.nominal_curve_family} / "
                    f"{params.linker_curve_family} and tenors "
                    f"{params.short_tenor} / {params.belly_tenor} / "
                    f"{params.long_tenor}."
                )
            }
        col_name = f"{endpoint_label}_breakeven_bps"
        df = pd.DataFrame(rows).rename(columns={"value": col_name})
        df["date"] = pd.to_datetime(df["date"])
        endpoint_frames[endpoint_label] = df

    wide = (
        endpoint_frames["short"]
        .merge(endpoint_frames["belly"], on="date", how="inner")
        .merge(endpoint_frames["long"], on="date", how="inner")
        .sort_values("date")
        .set_index("date")
    )

    # Drop any rows where any endpoint value is NaN — strict inner-
    # join discipline; no synthetic butterfly on partial data.
    wide = wide.dropna(
        subset=[
            "short_breakeven_bps",
            "belly_breakeven_bps",
            "long_breakeven_bps",
        ],
    )

    if wide.empty:
        return {
            "error": (
                f"After aligning the three endpoint breakeven "
                f"series for {params.nominal_curve_family} vs "
                f"{params.linker_curve_family} ("
                f"{params.short_tenor} → {params.belly_tenor} → "
                f"{params.long_tenor}), no overlapping dates "
                "remain.  Verify all three pillars have data on "
                "both the nominal and linker curves at this "
                "country."
            )
        }

    # ------------------------------------------------------------------
    # Butterfly formula:
    #   butterfly_bps = belly - 0.5 * (short + long)
    # The endpoint breakeven series come in already rounded to
    # bps_round_decimals (the spot primitive's canonical TimeSeries
    # applies the rounding upstream), so we round the butterfly
    # once at the end of this arithmetic.  Same boundary-rounding
    # discipline as the sibling butterfly primitives.  Wing spreads
    # use the same boundary-rounding so the snapshot's wing_*_bps
    # fields agree with the desk's manual decomposition.
    # ------------------------------------------------------------------
    short_series = wide["short_breakeven_bps"]
    belly_series = wide["belly_breakeven_bps"]
    long_series = wide["long_breakeven_bps"]
    wide["butterfly_bps"] = (
        belly_series - 0.5 * (short_series + long_series)
    ).round(bps_round_decimals)
    wide["wing_short_bps"] = (belly_series - short_series).round(
        bps_round_decimals,
    )
    wide["wing_long_bps"] = (long_series - belly_series).round(
        bps_round_decimals,
    )

    # ------------------------------------------------------------------
    # Rolling z-score on the butterfly series (in BPS units).
    # ------------------------------------------------------------------
    wide["z_score"] = rolling_zscore(
        wide["butterfly_bps"],
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_round_decimals,
    )

    # ------------------------------------------------------------------
    # Trim to requested display lookback — anchored to the data's
    # latest aligned trade_date.  Matches breakeven_inflation_simple
    # / breakeven_curve_spread / real_yield_butterfly / OIS
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
                f"No breakeven butterfly observations within the "
                f"last {params.lookback_days} days for "
                f"'{params.nominal_curve_family}' vs "
                f"'{params.linker_curve_family}' over "
                f"{params.short_tenor}{params.belly_tenor}"
                f"{params.long_tenor}."
            )
        }

    # ------------------------------------------------------------------
    # Build current_metrics
    # ------------------------------------------------------------------
    latest = display_df.iloc[-1]
    bflys = display_df["butterfly_bps"]

    current_butterfly_bps = safe_float(
        latest["butterfly_bps"], decimals=bps_round_decimals,
    )

    # Daily / weekly / monthly bps changes — series is already in
    # bps so already_bps=True (plain subtraction).  decimals=
    # passes the YAML convention through so the changes respect
    # bps_round_decimals.
    changes = period_changes(
        bflys,
        offsets=period_offsets,
        already_bps=True,
        decimals=bps_round_decimals,
    )

    # Trailing high/low/percentile of the breakeven butterfly (bps
    # scale).
    high, low, percentile = trailing_high_low_percentile(
        bflys, window=trailing_window, decimals=bps_round_decimals,
    )

    butterfly_label = (
        f"{params.nominal_curve_family}/"
        f"{params.linker_curve_family} "
        f"{params.short_tenor.replace('Y', '')}s"
        f"{params.belly_tenor.replace('Y', '')}s"
        f"{params.long_tenor.replace('Y', '')}s breakeven"
    )

    metrics = BreakevenButterflyCurrentMetrics(
        as_of_date=as_of_ts.strftime("%Y-%m-%d"),
        nominal_curve_family=params.nominal_curve_family,
        linker_curve_family=params.linker_curve_family,
        short_tenor=params.short_tenor,
        belly_tenor=params.belly_tenor,
        long_tenor=params.long_tenor,
        butterfly_label=butterfly_label,
        current_butterfly_bps=current_butterfly_bps,
        daily_change_bps=changes["daily"],
        weekly_change_bps=changes["weekly"],
        monthly_change_bps=changes["monthly"],
        # Pass z_round_decimals so a YAML override above 4 isn't
        # silently truncated by safe_float's default of 4.
        current_z_score=safe_float(
            latest.get("z_score"), decimals=z_round_decimals,
        ),
        rolling_window_days=z_window,
        high_252d_bps=high,
        low_252d_bps=low,
        percentile_252d=percentile,
        wing_short_bps=safe_float(
            latest.get("wing_short_bps"), decimals=bps_round_decimals,
        ),
        wing_long_bps=safe_float(
            latest.get("wing_long_bps"), decimals=bps_round_decimals,
        ),
        short_breakeven_bps=safe_float(
            latest.get("short_breakeven_bps"),
            decimals=bps_round_decimals,
        ),
        belly_breakeven_bps=safe_float(
            latest.get("belly_breakeven_bps"),
            decimals=bps_round_decimals,
        ),
        long_breakeven_bps=safe_float(
            latest.get("long_breakeven_bps"),
            decimals=bps_round_decimals,
        ),
        short_years=round(short_years, window_years_round),
        belly_years=round(belly_years, window_years_round),
        long_years=round(long_years, window_years_round),
        observation_count=len(display_df),
        methodology_label=methodology_label,
    )

    # ------------------------------------------------------------------
    # Build time_series (bespoke shape) AND the two canonical
    # TimeSeries (butterfly in BPS, z-score in Z_SCORE).  All three
    # share the same display_df rows so they cannot drift.
    # ------------------------------------------------------------------
    ts_rows = [
        BreakevenButterflyTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            butterfly_bps=round(row.butterfly_bps, bps_round_decimals),
            z_score=safe_float(row.z_score, decimals=z_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    canonical_butterfly = _build_canonical_butterfly_series(
        display_df,
        nominal_curve_family=params.nominal_curve_family,
        linker_curve_family=params.linker_curve_family,
        short_tenor=params.short_tenor,
        belly_tenor=params.belly_tenor,
        long_tenor=params.long_tenor,
        bps_round_decimals=bps_round_decimals,
    )
    canonical_zscore = _build_canonical_zscore_series(
        display_df,
        nominal_curve_family=params.nominal_curve_family,
        linker_curve_family=params.linker_curve_family,
        short_tenor=params.short_tenor,
        belly_tenor=params.belly_tenor,
        long_tenor=params.long_tenor,
        z_round_decimals=z_round_decimals,
    )

    output = BreakevenButterflyOutput(
        current_metrics=metrics,
        time_series=ts_rows,
        time_series_butterfly=canonical_butterfly,
        time_series_zscore=canonical_zscore,
    )
    return output.model_dump()


# ============================================================================
# CANONICAL TIME-SERIES BUILDERS
# ============================================================================

def _series_name_slug(
    *,
    nominal_curve_family: str,
    linker_curve_family: str,
    short_tenor: str,
    belly_tenor: str,
    long_tenor: str,
) -> str:
    """Lower-snake-case slug used as the canonical TimeSeries
    series_name prefix.  Pattern:
    ``<nominal>_<linker>_<short>_<belly>_<long>``.
    """
    return (
        f"{nominal_curve_family.lower()}_"
        f"{linker_curve_family.lower()}_"
        f"{short_tenor.lower()}_"
        f"{belly_tenor.lower()}_"
        f"{long_tenor.lower()}"
    )


def _build_canonical_butterfly_series(
    display_df: pd.DataFrame,
    *,
    nominal_curve_family: str,
    linker_curve_family: str,
    short_tenor: str,
    belly_tenor: str,
    long_tenor: str,
    bps_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's butterfly_bps column into the
    canonical ``TimeSeries`` shape (closed-enum BPS units).

    Naming convention:
    ``<nominal>_<linker>_<short>_<belly>_<long>_breakeven_butterfly``.
    """
    slug = _series_name_slug(
        nominal_curve_family=nominal_curve_family,
        linker_curve_family=linker_curve_family,
        short_tenor=short_tenor,
        belly_tenor=belly_tenor,
        long_tenor=long_tenor,
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=round(float(row.butterfly_bps), bps_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=f"{slug}_breakeven_butterfly",
        units=TimeSeriesUnits.BPS,
        description=(
            f"Same-country bond-implied breakeven butterfly "
            f"({nominal_curve_family}/{linker_curve_family}) "
            f"{short_tenor}/{belly_tenor}/{long_tenor} (belly - "
            "0.5*(short + long)) over the displayed window.  "
            "Curvature of bond-implied INFLATION COMPENSATION; "
            "NOT a curvature of pure expected inflation (each "
            "endpoint breakeven carries an inflation risk premium "
            "and a relative liquidity premium between the nominal "
            "sovereign and the linker, and the butterfly inherits "
            "all three at each endpoint).  Quoted in BPS (same "
            "units as the underlying breakeven series)."
        ),
        rows=rows,
    )


def _build_canonical_zscore_series(
    display_df: pd.DataFrame,
    *,
    nominal_curve_family: str,
    linker_curve_family: str,
    short_tenor: str,
    belly_tenor: str,
    long_tenor: str,
    z_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's z_score column into the
    canonical ``TimeSeries`` shape (closed-enum Z_SCORE units).

    Naming convention:
    ``<nominal>_<linker>_<short>_<belly>_<long>_breakeven_butterfly_zscore``.
    Values match ``time_series[i].z_score`` 1-to-1 (rounded via the
    same ``z_score_round_decimals`` convention applied to the
    bespoke field) so the two series cannot drift.
    """
    slug = _series_name_slug(
        nominal_curve_family=nominal_curve_family,
        linker_curve_family=linker_curve_family,
        short_tenor=short_tenor,
        belly_tenor=belly_tenor,
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
        series_name=f"{slug}_breakeven_butterfly_zscore",
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"Rolling z-score of the {nominal_curve_family}/"
            f"{linker_curve_family} breakeven butterfly (in bps) "
            f"over {short_tenor}/{belly_tenor}/{long_tenor} vs its "
            "own trailing window."
        ),
        rows=rows,
    )
