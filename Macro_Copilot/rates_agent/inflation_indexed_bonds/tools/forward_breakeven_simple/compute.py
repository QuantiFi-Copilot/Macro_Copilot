"""
compute.py — Config-driven forward bond-implied breakeven inflation
====================================================================

Third tool in the ``inflation_indexed_bonds`` domain.  Owns the desk
concept of a forward bond-implied breakeven inflation between two
same-country curve points (e.g. 5Y5Y, 5Y10Y, 2Y3Y):

    forward_breakeven_bps =
        (BE_long_bps * T_long - BE_short_bps * T_short)
        / (T_long - T_short)

where ``BE_short_bps`` and ``BE_long_bps`` are the spot bond-implied
breakevens at ``start_tenor`` and ``end_tenor`` respectively (each
computed by ``calculate_breakeven_inflation_simple``), and
``T_short`` / ``T_long`` are the calendar year fractions of the two
endpoint tenors (parsed via the shared
``shared.analytics.curve_bootstrap.tenor_to_years`` helper, NOT a
bespoke parser).

Concept honesty
---------------
This tool returns FORWARD INFLATION COMPENSATION, NOT pure forward
expected inflation.  Each endpoint breakeven carries an inflation
risk premium and a relative liquidity premium between the nominal
sovereign and the linker; the year-weighted forward inherits both.
The honesty disclosure lives in the YAML's
``methodology.what_it_does`` and is threaded onto the wire via
``current_metrics.methodology_label``.  That field is sourced from
the YAML at runtime — NOT hardcoded as a Python literal — so a YAML
edit flows through to runtime behaviour.

Composition path (deliberately simple)
--------------------------------------
This primitive composes two calls to
``calculate_breakeven_inflation_simple`` (one at ``start_tenor``,
one at ``end_tenor``) into the year-weighted forward.  That gets two
load-bearing guards "for free":

  - Linker / nominal instrument_type filter on each leg — i.e. the
    no-proxy guard documented in the spot primitive's compute()
    docstring (DESIGN_PRINCIPLES.md §1, §3 / STANDARD_TOOL_AND_YAML_RULES.md §J).
  - Same-country / same-currency invariant — i.e. the
    ``_enforce_same_country_invariant`` helper that fires BEFORE any
    market-data SELECT (DESIGN_PRINCIPLES.md §5 / STANDARD_TOOL_AND_YAML_RULES.md §H).

Both guards run on EACH inner endpoint call; if either endpoint's
spot breakeven returns its own controlled error envelope, this tool
surfaces a controlled error envelope of its own that names which
endpoint failed and propagates the inner error context.

The spot primitive's compute() is invoked with ``config=`` set to
THIS primitive's ToolConfig.  Every shared convention name is
present in both YAMLs with the same numeric value (the cross-config
lint enforces that), so the inner compute reads exactly the same
window / min_periods / ddof / buffer / ffill / rounding values it
would read from its own bundled config.  Tests that override
conventions on the forward primitive's ToolConfig automatically see
those overrides flow through to the inner spot calls.

Why an extended inner lookback
------------------------------
Each inner spot call trims its time_series_breakeven to
``lookback_days`` worth of display history (anchored to the latest
aligned trade_date).  Our rolling z-score / trailing range / period
changes need a longer history so they're fully populated from the
first row of OUR display window.  We therefore pass
``lookback_days = params.lookback_days + buffer_calendar_days`` to
each inner spot call, then align + trim ourselves.  The buffer math
matches the spot primitive's own buffer math
(``max(z_window, trailing_window) * buffer_multiplier``) so this
primitive does not double-buffer.

Forward formula (year-weighted linear)
--------------------------------------
``forward_compounding_mode`` is locked at ``year_weighted_linear`` in
V1.  See ``methodology.what_it_does`` for the literal formula and
``methodology.planned_extensions`` for the dual-compounding
alternative (mirrors OIS forward_rate's bootstrap).  ``compute()``
raises ``NotImplementedError`` if this convention is set to anything
else; the wire-disclosed ``methodology_label`` and the SQL validator
must move in lockstep with this convention so the desk user can
never see a label that disagrees with the math.

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
fixtures.  This module does NOT import ``date`` directly — the
fetch start-date logic lives entirely inside the spot primitive,
which already exposes its own date seam.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple import (
    BreakevenInflationSimpleInput,
    calculate_breakeven_inflation_simple,
)
from rates_agent.inflation_indexed_bonds.tools.forward_breakeven_simple.schemas import (
    ForwardBreakevenSimpleCurrentMetrics,
    ForwardBreakevenSimpleInput,
    ForwardBreakevenSimpleOutput,
    ForwardBreakevenSimpleTimeSeriesRow,
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


# Bundled config — public symbol so external callers (mcp_server, REST
# routes, tests, future batch surfaces) can build a ToolConfig from
# the same source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Trailing-range window is wire-frozen at 252 in V1; see schemas.py
# and config.yaml's ``planned_extensions``.
_FROZEN_TRAILING_WINDOW: int = 252


# Forward-formula compounding mode is wire-frozen at
# ``year_weighted_linear`` in V1.  See config.yaml's
# ``methodology.planned_extensions`` for the dual-compounding
# alternative path.
_SUPPORTED_FORWARD_FORMULA: str = "year_weighted_linear"


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull the methodology kwargs the forward_breakeven compute()
    needs from a ToolConfig.

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

    formula = config.convention_value("forward_compounding_mode")
    if formula != _SUPPORTED_FORWARD_FORMULA:
        raise NotImplementedError(
            f"forward_compounding_mode={formula!r} is documented in this "
            f"tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions for the dual_compounding "
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
        "window_years_round": config.convention_value("window_years_round_decimals"),
        "forward_formula": formula,
    }


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_forward_breakeven_simple(
    engine: Engine,
    params: ForwardBreakevenSimpleInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Calculate the forward bond-implied breakeven inflation between
    two same-country curve points.

        forward_breakeven_bps =
            (BE_long_bps * T_long - BE_short_bps * T_short)
            / (T_long - T_short)

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : ForwardBreakevenSimpleInput
        Validated input.  ``field_name=None`` resolves against the
        YAML's ``default_field_name`` convention.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides; production
        wrappers must pass ``config=`` explicitly.

    Returns
    -------
    dict
        Serialised ``ForwardBreakevenSimpleOutput``, or
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
    # Extended inner lookback.  Each inner spot call trims its
    # time_series_breakeven to its own ``lookback_days`` of display
    # history.  We need a longer window so OUR rolling stats are
    # fully populated from the first row of OUR display window;
    # match the buffer math the spot primitive uses internally so we
    # don't double-buffer.
    # ------------------------------------------------------------------
    buffer_calendar_days = int(
        max(z_window, trailing_window) * buffer_multiplier
    )
    extended_lookback_days = params.lookback_days + buffer_calendar_days

    # ------------------------------------------------------------------
    # Compose: call the spot bond-implied breakeven primitive once per
    # endpoint tenor.  This inherits the no-proxy guard
    # (instrument_type filter on each leg) and the same-country
    # invariant (instrument_master (country, currency) check before
    # any market-data SELECT) for free.  We pass ``config=`` so the
    # inner conventions use the same values this primitive uses;
    # cross-config lint enforces value-agreement on the bundled
    # YAMLs, and any test override on this primitive's ToolConfig
    # flows through to the inner spot calls.
    # ------------------------------------------------------------------
    short_inner = calculate_breakeven_inflation_simple(
        engine=engine,
        params=BreakevenInflationSimpleInput(
            nominal_curve_family=params.nominal_curve_family,
            linker_curve_family=params.linker_curve_family,
            tenor=params.start_tenor,
            lookback_days=extended_lookback_days,
            field_name=params.field_name,
            as_of_date=params.as_of_date,
        ),
        config=config,
    )
    if "error" in short_inner:
        return {
            "error": (
                f"Forward breakeven {params.start_tenor}{params.end_tenor} "
                f"could not be computed for "
                f"{params.nominal_curve_family} vs "
                f"{params.linker_curve_family}: the start-tenor "
                f"endpoint ({params.start_tenor}) failed.  Inner "
                f"error: {short_inner['error']}"
            )
        }

    long_inner = calculate_breakeven_inflation_simple(
        engine=engine,
        params=BreakevenInflationSimpleInput(
            nominal_curve_family=params.nominal_curve_family,
            linker_curve_family=params.linker_curve_family,
            tenor=params.end_tenor,
            lookback_days=extended_lookback_days,
            field_name=params.field_name,
            as_of_date=params.as_of_date,
        ),
        config=config,
    )
    if "error" in long_inner:
        return {
            "error": (
                f"Forward breakeven {params.start_tenor}{params.end_tenor} "
                f"could not be computed for "
                f"{params.nominal_curve_family} vs "
                f"{params.linker_curve_family}: the end-tenor "
                f"endpoint ({params.end_tenor}) failed.  Inner "
                f"error: {long_inner['error']}"
            )
        }

    # ------------------------------------------------------------------
    # Align endpoint breakevens by date.  The spot primitive's
    # canonical TimeSeries already has each row pre-rounded to
    # ``bps_round_decimals`` from the inner compute; we use those
    # rows directly (NOT the bespoke ``time_series`` field) so the
    # canonical-shape contract is end-to-end.  Inner-join on date so
    # we only emit rows where BOTH endpoints had data — matches the
    # spot primitive's own pivot_and_align dropna behaviour.
    # ------------------------------------------------------------------
    short_rows = short_inner.get("time_series_breakeven", {}).get("rows", [])
    long_rows = long_inner.get("time_series_breakeven", {}).get("rows", [])
    if not short_rows or not long_rows:
        return {
            "error": (
                f"Forward breakeven {params.start_tenor}{params.end_tenor} "
                f"could not be computed: at least one endpoint "
                "returned no canonical TimeSeries rows.  This is a "
                "bug — the spot primitive returned a snapshot but no "
                "history.  Please report with the curve_family pair "
                f"{params.nominal_curve_family} / "
                f"{params.linker_curve_family}."
            )
        }

    short_df = pd.DataFrame(short_rows).rename(
        columns={"value": "start_breakeven_bps"},
    )
    long_df = pd.DataFrame(long_rows).rename(
        columns={"value": "end_breakeven_bps"},
    )
    short_df["date"] = pd.to_datetime(short_df["date"])
    long_df["date"] = pd.to_datetime(long_df["date"])

    wide = (
        pd.merge(short_df, long_df, on="date", how="inner")
        .sort_values("date")
        .set_index("date")
    )

    if wide.empty:
        return {
            "error": (
                f"After aligning the two endpoint breakeven series for "
                f"{params.nominal_curve_family} vs "
                f"{params.linker_curve_family} "
                f"({params.start_tenor} → {params.end_tenor}), no "
                "overlapping dates remain.  The two endpoint tenors "
                "may not have a common date history at this country — "
                "verify both curves have data at both tenors."
            )
        }

    # ------------------------------------------------------------------
    # Year-weighted linear forward formula:
    #   forward = (BE_long * T_long - BE_short * T_short) / (T_long - T_short)
    # The endpoint breakeven series come in already rounded to
    # bps_round_decimals (both the spot primitive and the spot's
    # canonical TimeSeries apply the rounding upstream), so we round
    # the forward bps once at the end of this arithmetic.  Same
    # boundary-rounding discipline as the spot primitive's
    # ``compute_spread_bps`` rounding step.
    # ------------------------------------------------------------------
    wide["forward_breakeven_bps"] = (
        (
            wide["end_breakeven_bps"] * end_years
            - wide["start_breakeven_bps"] * start_years
        )
        / dt_years
    ).round(bps_round_decimals)

    # ------------------------------------------------------------------
    # Rolling z-score on the forward bps series.
    # ------------------------------------------------------------------
    wide["z_score"] = rolling_zscore(
        wide["forward_breakeven_bps"],
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_round_decimals,
    )

    # ------------------------------------------------------------------
    # Trim to requested display lookback — anchored to the data's
    # latest aligned trade_date.  Matches breakeven_inflation_simple
    # / real_yield_level / OIS rate_level.
    # ------------------------------------------------------------------
    as_of_ts = wide.index[-1]
    cutoff = pd.Timestamp(
        as_of_ts.date() - timedelta(days=params.lookback_days),
    )
    display_df = wide.loc[wide.index >= cutoff].copy()

    if display_df.empty:
        return {
            "error": (
                f"No forward breakeven observations within the last "
                f"{params.lookback_days} days for "
                f"'{params.nominal_curve_family}' vs "
                f"'{params.linker_curve_family}' over "
                f"{params.start_tenor}{params.end_tenor}."
            )
        }

    # ------------------------------------------------------------------
    # Build current_metrics
    # ------------------------------------------------------------------
    latest = display_df.iloc[-1]
    forwards = display_df["forward_breakeven_bps"]

    current_forward_bps = safe_float(
        latest["forward_breakeven_bps"], decimals=bps_round_decimals,
    )
    current_forward_pct = (
        round(
            float(latest["forward_breakeven_bps"]) / 100.0,
            yield_round_decimals,
        )
        if pd.notna(latest["forward_breakeven_bps"]) else None
    )

    # Daily / weekly / monthly bps changes — series is already in bps
    # so already_bps=True (plain subtraction).  decimals= passes the
    # YAML convention through so the changes respect bps_round_decimals.
    changes = period_changes(
        forwards,
        offsets=period_offsets,
        already_bps=True,
        decimals=bps_round_decimals,
    )

    # Trailing high/low/percentile of the forward breakeven (bps scale).
    high, low, percentile = trailing_high_low_percentile(
        forwards, window=trailing_window, decimals=bps_round_decimals,
    )

    forward_window_label = (
        f"{params.nominal_curve_family}/"
        f"{params.linker_curve_family} "
        f"{params.start_tenor}{params.end_tenor}"
    )

    metrics = ForwardBreakevenSimpleCurrentMetrics(
        as_of_date=as_of_ts.strftime("%Y-%m-%d"),
        nominal_curve_family=params.nominal_curve_family,
        linker_curve_family=params.linker_curve_family,
        start_tenor=params.start_tenor,
        end_tenor=params.end_tenor,
        forward_window_label=forward_window_label,
        forward_breakeven_pct=current_forward_pct,
        forward_breakeven_bps=current_forward_bps,
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
        start_breakeven_bps=safe_float(
            latest.get("start_breakeven_bps"),
            decimals=bps_round_decimals,
        ),
        end_breakeven_bps=safe_float(
            latest.get("end_breakeven_bps"),
            decimals=bps_round_decimals,
        ),
        start_years=round(start_years, window_years_round),
        end_years=round(end_years, window_years_round),
        methodology_label=methodology_label,
    )

    # ------------------------------------------------------------------
    # Build time_series (bespoke shape) AND the two canonical
    # TimeSeries (forward in BPS, z-score in Z_SCORE).  All three
    # share the same display_df rows so they cannot drift.
    # ------------------------------------------------------------------
    ts_rows = [
        ForwardBreakevenSimpleTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            forward_breakeven_bps=round(
                row.forward_breakeven_bps, bps_round_decimals,
            ),
            z_score=safe_float(row.z_score, decimals=z_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    canonical_forward = _build_canonical_forward_series(
        display_df,
        nominal_curve_family=params.nominal_curve_family,
        linker_curve_family=params.linker_curve_family,
        start_tenor=params.start_tenor,
        end_tenor=params.end_tenor,
        bps_round_decimals=bps_round_decimals,
    )
    canonical_zscore = _build_canonical_forward_zscore_series(
        display_df,
        nominal_curve_family=params.nominal_curve_family,
        linker_curve_family=params.linker_curve_family,
        start_tenor=params.start_tenor,
        end_tenor=params.end_tenor,
        z_round_decimals=z_round_decimals,
    )

    output = ForwardBreakevenSimpleOutput(
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
    nominal_curve_family: str,
    linker_curve_family: str,
    start_tenor: str,
    end_tenor: str,
) -> str:
    """Lower-snake-case slug used as the canonical TimeSeries
    series_name prefix.  Pattern:
    ``<nominal>_<linker>_<startten>_<endten>``.
    """
    return (
        f"{nominal_curve_family.lower()}_"
        f"{linker_curve_family.lower()}_"
        f"{start_tenor.lower()}_"
        f"{end_tenor.lower()}"
    )


def _build_canonical_forward_series(
    display_df: pd.DataFrame,
    *,
    nominal_curve_family: str,
    linker_curve_family: str,
    start_tenor: str,
    end_tenor: str,
    bps_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's forward_breakeven_bps column
    into the canonical ``TimeSeries`` shape (closed-enum BPS units).

    Naming convention:
    ``<nominal>_<linker>_<startten>_<endten>_forward_breakeven``.
    """
    slug = _series_name_slug(
        nominal_curve_family=nominal_curve_family,
        linker_curve_family=linker_curve_family,
        start_tenor=start_tenor,
        end_tenor=end_tenor,
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=round(
                float(row.forward_breakeven_bps), bps_round_decimals,
            ),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=f"{slug}_forward_breakeven",
        units=TimeSeriesUnits.BPS,
        description=(
            f"Forward bond-implied breakeven inflation "
            f"({nominal_curve_family} − {linker_curve_family}) over "
            f"{start_tenor}{end_tenor} via the year-weighted linear "
            "formula on the two endpoint spot breakevens.  Forward "
            "inflation compensation; not a clean forward "
            "expected-inflation read (carries inflation risk premium "
            "and liquidity premium)."
        ),
        rows=rows,
    )


def _build_canonical_forward_zscore_series(
    display_df: pd.DataFrame,
    *,
    nominal_curve_family: str,
    linker_curve_family: str,
    start_tenor: str,
    end_tenor: str,
    z_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's z_score column into the
    canonical ``TimeSeries`` shape (closed-enum Z_SCORE units).

    Naming convention:
    ``<nominal>_<linker>_<startten>_<endten>_forward_breakeven_zscore``.
    Values match ``time_series[i].z_score`` 1-to-1 (rounded via the
    same ``z_score_round_decimals`` convention applied to the
    bespoke field) so the two series cannot drift.
    """
    slug = _series_name_slug(
        nominal_curve_family=nominal_curve_family,
        linker_curve_family=linker_curve_family,
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
        series_name=f"{slug}_forward_breakeven_zscore",
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"Rolling z-score of the {nominal_curve_family}−"
            f"{linker_curve_family} forward bond-implied breakeven "
            f"(in bps) over {start_tenor}{end_tenor} vs its own "
            "trailing window."
        ),
        rows=rows,
    )
