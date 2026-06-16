"""
compute.py — Config-driven same-tenor cross-country breakeven spread
=====================================================================

Fifth tool in the ``inflation_indexed_bonds`` domain.  Owns the
desk concept of a same-tenor cross-country breakeven inflation
differential between two countries' generic bond-implied breakevens
at the same tenor (e.g. US 10Y breakeven minus EUR-FR 10Y
breakeven).  Computed per-trade-date via the SPREAD formula:

    spread_bps = breakeven_a_bps - breakeven_b_bps

where ``breakeven_a_bps`` and ``breakeven_b_bps`` are the spot
bond-implied breakevens at ``tenor`` for country_a and country_b
respectively (each computed by
``calculate_breakeven_inflation_simple`` constrained to that
country's own (nominal, linker) pair).

Concept honesty
---------------
This tool returns a CROSS-COUNTRY INFLATION-COMPENSATION
DIFFERENTIAL, NOT a pure cross-country expected-inflation
differential.  Two load-bearing caveats inherited / introduced
here:

  1. Each leg is a generic linker-implied breakeven and inherits
     the spot primitive's "inflation compensation, not pure
     expected inflation" caveat (each leg carries an inflation
     risk premium and a relative liquidity premium between its
     nominal sovereign and its linker).

  2. INDEX-FAMILY MISMATCH: different countries' linkers reference
     different inflation indices (CPI-U non-seasonally adjusted vs
     HICP ex-tobacco vs RPI/CPIH vs Canada CPI).  These are NOT
     identical inflation references — a US 10Y breakeven minus
     EUR-FR 10Y breakeven is a CPI-U-vs-HICP differential, not a
     pure expected-inflation differential.

Both caveats live in the YAML's ``methodology.what_it_does`` and
are threaded onto the wire via
``current_metrics.methodology_label``.  The wire field is sourced
from the YAML at runtime — NOT hardcoded as a Python literal — so
a YAML edit flows through to runtime behaviour.

Sign convention
---------------
Per-trade-date, the displayed cross-country spread is country_a
minus country_b.  This sign convention is FIXED and surfaced both
in YAML and on the wire; the tool never silently flips the sign.
Flipping the inputs (a↔b) flips the displayed sign.

Composition path (deliberately simple)
--------------------------------------
This primitive composes two calls to
``calculate_breakeven_inflation_simple`` (one per country), each
constrained to its own (nominal, linker) pair.  That gets four
load-bearing guarantees "for free":

  - Linker / nominal instrument_type filter on each leg of each
    country — the no-proxy guard documented in the spot primitive's
    compute() docstring (DESIGN_PRINCIPLES.md §1, §3 /
    STANDARD_TOOL_AND_YAML_RULES.md §J).
  - Same-country / same-currency invariant PER LEG — the
    ``_enforce_same_country_invariant`` helper that fires BEFORE
    any market-data SELECT on each country leg.  This means
    leg-internal cross-country pollution (e.g.
    country_a_nominal=UST + country_a_linker=EUR_FR_LINKER) is
    refused before any market-data SELECT — this primitive does
    NOT re-implement that guard, it composes it.
  - Boundary rounding parity / wire-frozen 252d trailing window /
    convention threading inherited from the spot primitive.

Cross-country guard layering
----------------------------
The cross-country requirement (country_a vs country_b) is
enforced in this primitive's INPUT VALIDATOR (no DB lookup
needed).  The same-country requirement WITHIN each leg
(country_a_nominal must share country with country_a_linker) is
enforced inside ``breakeven_inflation_simple`` per leg.  Neither
guard is re-implemented in the other primitive; they compose
honestly.

Either inner call returning a controlled error envelope is
surfaced by this tool with a leg-attributed prefix
(``country_a leg failed: ...`` / ``country_b leg failed: ...``)
and the inner error context is propagated.

The spot primitive's compute() is invoked with ``config=`` set to
THIS primitive's ToolConfig.  Every shared convention name is
present in both YAMLs with the same numeric value (the cross-
config lint enforces that), so the inner compute reads exactly
the same window / min_periods / ddof / buffer / ffill / rounding
values it would read from its own bundled config.  Tests that
override conventions on this primitive's ToolConfig automatically
see those overrides flow through to the inner spot calls.

Why an extended inner lookback
------------------------------
Each inner spot call trims its time_series_breakeven to its own
``lookback_days`` of display history.  Our rolling z-score /
trailing range / period changes need a longer history so they're
fully populated from the first row of OUR display window.  We
therefore pass ``lookback_days = params.lookback_days +
buffer_calendar_days`` to each inner spot call, then align +
trim ourselves.  Same buffer math the spot primitive uses
internally (``max(z_window, trailing_window) *
buffer_multiplier``) so this primitive does not double-buffer.

Per-trade-date alignment
------------------------
The two country breakeven series are inner-joined on date so we
only emit rows where BOTH country legs had data — matches the
breakeven_curve_spread / forward_breakeven_simple alignment step.
No ffill across the join: dates where one country has no data are
dropped from the spread series, NOT carried forward as synthetic
spread points.

Trailing-range wire freeze
--------------------------
``trailing_range_window_days`` is locked at 252 in V1.  The output
schema's field names (``high_252d_bps``, ``low_252d_bps``,
``percentile_252d``) embed the number; changing the convention
without renaming the wire fields would silently lie about what
the percentile is computed against.  ``compute()`` raises
``NotImplementedError`` if this is set to anything else; see
``methodology.planned_extensions`` for the path to making it
configurable.

Test seam
---------
``calculate_breakeven_inflation_simple`` is imported here at
module level; tests patch the inner primitive's own seams
(``...breakeven_inflation_simple.compute.fetch_single_tenor``,
``...compute._fetch_curve_family_country_currency``,
``...compute.date``) so the inner per-country calls hit the test
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
from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple.schemas import (
    CrossCountryBreakevenSpreadSimpleCurrentMetrics,
    CrossCountryBreakevenSpreadSimpleInput,
    CrossCountryBreakevenSpreadSimpleOutput,
    CrossCountryBreakevenSpreadSimpleTimeSeriesRow,
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
    """Pull the methodology kwargs the cross-country breakeven
    spread compute() needs from a ToolConfig.

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
        "window_years_round": config.convention_value("window_years_round_decimals"),
    }


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_cross_country_breakeven_spread_simple(
    engine: Engine,
    params: CrossCountryBreakevenSpreadSimpleInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Calculate the same-tenor cross-country breakeven inflation
    differential between two countries' generic bond-implied
    breakevens.

        spread_bps = breakeven_a_bps - breakeven_b_bps

    Sign convention is country_a minus country_b — fixed.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : CrossCountryBreakevenSpreadSimpleInput
        Validated input.  ``field_name=None`` resolves against the
        YAML's ``default_field_name`` convention.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass
        a custom ToolConfig to exercise convention overrides;
        production wrappers must pass ``config=`` explicitly.

    Returns
    -------
    dict
        Serialised
        ``CrossCountryBreakevenSpreadSimpleOutput``, or
        ``{"error": "..."}`` on recoverable failure (with a
        leg-attributed prefix for inner-call failures).
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
    # Year fraction for display.  The Pydantic validators have
    # already ensured the cross-country / cross-linker requirements;
    # tenor parsing is delegated to the inner spot calls (which use
    # the same shared parser).  We still compute it here for the
    # display field; if the tenor is unparsable, the inner spot
    # calls will surface a controlled error envelope from their own
    # fetch path.
    # ------------------------------------------------------------------
    try:
        tenor_years = tenor_to_years(params.tenor)
    except ValueError as exc:
        return {
            "error": (
                f"Cross-country breakeven spread could not be "
                f"computed for {params.country_a_nominal_pair}/"
                f"{params.country_a_linker_pair} vs "
                f"{params.country_b_nominal_pair}/"
                f"{params.country_b_linker_pair} @ "
                f"{params.tenor}: tenor could not be parsed "
                f"({exc}).  Accepted forms are '<n>W', '<n>M', "
                "'<n>Y'."
            )
        }

    # ------------------------------------------------------------------
    # Extended inner lookback.  Each inner spot call trims its
    # time_series_breakeven to ``lookback_days`` worth of display
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
    # per country, each constrained to that country's own
    # (nominal, linker) pair.  This composition is the load-bearing
    # mechanism for the per-leg same-country invariant — the spot
    # primitive's ``_enforce_same_country_invariant`` fires per leg
    # before any market-data SELECT.  Leg-internal cross-country
    # pollution (e.g. country_a_nominal=UST + country_a_linker=
    # EUR_FR_LINKER) is refused at compute time by the spot
    # primitive itself; this primitive surfaces that controlled
    # error envelope with a leg-attributed prefix.
    # ------------------------------------------------------------------
    a_inner = calculate_breakeven_inflation_simple(
        engine=engine,
        params=BreakevenInflationSimpleInput(
            nominal_curve_family=params.country_a_nominal_pair,
            linker_curve_family=params.country_a_linker_pair,
            tenor=params.tenor,
            lookback_days=extended_lookback_days,
            field_name=params.field_name,
            as_of_date=params.as_of_date,
        ),
        config=config,
    )
    if "error" in a_inner:
        return {
            "error": (
                f"Cross-country breakeven spread "
                f"({params.country_a_nominal_pair}/"
                f"{params.country_a_linker_pair} vs "
                f"{params.country_b_nominal_pair}/"
                f"{params.country_b_linker_pair} @ "
                f"{params.tenor}) could not be computed: "
                f"country_a leg failed: {a_inner['error']}"
            )
        }

    b_inner = calculate_breakeven_inflation_simple(
        engine=engine,
        params=BreakevenInflationSimpleInput(
            nominal_curve_family=params.country_b_nominal_pair,
            linker_curve_family=params.country_b_linker_pair,
            tenor=params.tenor,
            lookback_days=extended_lookback_days,
            field_name=params.field_name,
            as_of_date=params.as_of_date,
        ),
        config=config,
    )
    if "error" in b_inner:
        return {
            "error": (
                f"Cross-country breakeven spread "
                f"({params.country_a_nominal_pair}/"
                f"{params.country_a_linker_pair} vs "
                f"{params.country_b_nominal_pair}/"
                f"{params.country_b_linker_pair} @ "
                f"{params.tenor}) could not be computed: "
                f"country_b leg failed: {b_inner['error']}"
            )
        }

    # ------------------------------------------------------------------
    # Align country breakevens by date.  The spot primitive's
    # canonical TimeSeries already has each row pre-rounded to
    # ``bps_round_decimals`` from the inner compute; we use those
    # rows directly (NOT the bespoke ``time_series`` field) so the
    # canonical-shape contract is end-to-end.  Inner-join on date
    # so we only emit rows where BOTH country legs had data — no
    # synthetic spread points are produced on dates where one
    # country has no data.
    # ------------------------------------------------------------------
    a_rows = a_inner.get("time_series_breakeven", {}).get("rows", [])
    b_rows = b_inner.get("time_series_breakeven", {}).get("rows", [])
    if not a_rows or not b_rows:
        return {
            "error": (
                f"Cross-country breakeven spread "
                f"({params.country_a_nominal_pair}/"
                f"{params.country_a_linker_pair} vs "
                f"{params.country_b_nominal_pair}/"
                f"{params.country_b_linker_pair} @ "
                f"{params.tenor}) could not be computed: at least "
                "one country leg returned no canonical TimeSeries "
                "rows.  This is a bug — the spot primitive returned "
                "a snapshot but no history.  Please report with the "
                "country pair."
            )
        }

    a_df = pd.DataFrame(a_rows).rename(
        columns={"value": "breakeven_a_bps"},
    )
    b_df = pd.DataFrame(b_rows).rename(
        columns={"value": "breakeven_b_bps"},
    )
    a_df["date"] = pd.to_datetime(a_df["date"])
    b_df["date"] = pd.to_datetime(b_df["date"])

    wide = (
        pd.merge(a_df, b_df, on="date", how="inner")
        .sort_values("date")
        .set_index("date")
    )

    if wide.empty:
        return {
            "error": (
                f"After aligning the two country breakeven series "
                f"({params.country_a_nominal_pair}/"
                f"{params.country_a_linker_pair} vs "
                f"{params.country_b_nominal_pair}/"
                f"{params.country_b_linker_pair} @ "
                f"{params.tenor}), no overlapping dates remain.  "
                "The two country pairs may not have a common date "
                "history at this tenor — verify both pairs have "
                "data for the requested window."
            )
        }

    # ------------------------------------------------------------------
    # Spread formula:  spread_bps = a - b  (country_a minus country_b).
    # The country breakeven series come in already rounded to
    # bps_round_decimals (the spot primitive's canonical TimeSeries
    # applies the rounding upstream), so we round the spread once
    # at the end of this arithmetic.  Same boundary-rounding
    # discipline as the breakeven_curve_spread primitive.
    # ------------------------------------------------------------------
    wide["spread_bps"] = (
        wide["breakeven_a_bps"] - wide["breakeven_b_bps"]
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
    # latest aligned trade_date.  Matches breakeven_curve_spread /
    # breakeven_inflation_simple / forward_breakeven_simple /
    # real_yield_level / OIS rate_level.
    # ------------------------------------------------------------------
    as_of_ts = wide.index[-1]
    cutoff = pd.Timestamp(
        as_of_ts.date() - timedelta(days=params.lookback_days),
    )
    display_df = wide.loc[wide.index >= cutoff].copy()

    if display_df.empty:
        return {
            "error": (
                f"No cross-country breakeven spread observations "
                f"within the last {params.lookback_days} days for "
                f"{params.country_a_nominal_pair}/"
                f"{params.country_a_linker_pair} vs "
                f"{params.country_b_nominal_pair}/"
                f"{params.country_b_linker_pair} @ {params.tenor}."
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

    # Trailing high/low/percentile of the cross-country spread
    # (bps scale).
    high, low, percentile = trailing_high_low_percentile(
        spreads, window=trailing_window, decimals=bps_round_decimals,
    )

    spread_label = (
        f"{params.country_a_nominal_pair}/"
        f"{params.country_a_linker_pair} - "
        f"{params.country_b_nominal_pair}/"
        f"{params.country_b_linker_pair} "
        f"{params.tenor} XC breakeven"
    )

    metrics = CrossCountryBreakevenSpreadSimpleCurrentMetrics(
        as_of_date=as_of_ts.strftime("%Y-%m-%d"),
        country_a_nominal_pair=params.country_a_nominal_pair,
        country_a_linker_pair=params.country_a_linker_pair,
        country_b_nominal_pair=params.country_b_nominal_pair,
        country_b_linker_pair=params.country_b_linker_pair,
        tenor=params.tenor,
        spread_label=spread_label,
        current_spread_bps=current_spread_bps,
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
        breakeven_a_bps=safe_float(
            latest.get("breakeven_a_bps"),
            decimals=bps_round_decimals,
        ),
        breakeven_b_bps=safe_float(
            latest.get("breakeven_b_bps"),
            decimals=bps_round_decimals,
        ),
        tenor_years=round(tenor_years, window_years_round),
        methodology_label=methodology_label,
    )

    # ------------------------------------------------------------------
    # Build time_series (bespoke shape) AND the two canonical
    # TimeSeries (spread in BPS, z-score in Z_SCORE).  All three
    # share the same display_df rows so they cannot drift.
    # ------------------------------------------------------------------
    ts_rows = [
        CrossCountryBreakevenSpreadSimpleTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            spread_bps=round(row.spread_bps, bps_round_decimals),
            z_score=safe_float(row.z_score, decimals=z_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    canonical_spread = _build_canonical_spread_series(
        display_df,
        country_a_nominal_pair=params.country_a_nominal_pair,
        country_a_linker_pair=params.country_a_linker_pair,
        country_b_nominal_pair=params.country_b_nominal_pair,
        country_b_linker_pair=params.country_b_linker_pair,
        tenor=params.tenor,
        bps_round_decimals=bps_round_decimals,
    )
    canonical_zscore = _build_canonical_zscore_series(
        display_df,
        country_a_nominal_pair=params.country_a_nominal_pair,
        country_a_linker_pair=params.country_a_linker_pair,
        country_b_nominal_pair=params.country_b_nominal_pair,
        country_b_linker_pair=params.country_b_linker_pair,
        tenor=params.tenor,
        z_round_decimals=z_round_decimals,
    )

    output = CrossCountryBreakevenSpreadSimpleOutput(
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
    country_a_nominal_pair: str,
    country_a_linker_pair: str,
    country_b_nominal_pair: str,
    country_b_linker_pair: str,
    tenor: str,
) -> str:
    """Lower-snake-case slug used as the canonical TimeSeries
    series_name prefix.  Pattern:
    ``<an>_<al>_<bn>_<bl>_<tenor>``.
    """
    return (
        f"{country_a_nominal_pair.lower()}_"
        f"{country_a_linker_pair.lower()}_"
        f"{country_b_nominal_pair.lower()}_"
        f"{country_b_linker_pair.lower()}_"
        f"{tenor.lower()}"
    )


def _build_canonical_spread_series(
    display_df: pd.DataFrame,
    *,
    country_a_nominal_pair: str,
    country_a_linker_pair: str,
    country_b_nominal_pair: str,
    country_b_linker_pair: str,
    tenor: str,
    bps_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's spread_bps column into the
    canonical ``TimeSeries`` shape (closed-enum BPS units).

    Naming convention:
    ``<an>_<al>_<bn>_<bl>_<tenor>_xc_breakeven_spread``.
    """
    slug = _series_name_slug(
        country_a_nominal_pair=country_a_nominal_pair,
        country_a_linker_pair=country_a_linker_pair,
        country_b_nominal_pair=country_b_nominal_pair,
        country_b_linker_pair=country_b_linker_pair,
        tenor=tenor,
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=round(float(row.spread_bps), bps_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=f"{slug}_xc_breakeven_spread",
        units=TimeSeriesUnits.BPS,
        description=(
            f"Cross-country breakeven spread "
            f"({country_a_nominal_pair}/{country_a_linker_pair} - "
            f"{country_b_nominal_pair}/{country_b_linker_pair}) "
            f"at {tenor} (breakeven_a_bps - breakeven_b_bps) over "
            "the displayed window.  Cross-country inflation-"
            "compensation differential; not a pure cross-country "
            "expected-inflation differential (carries inflation "
            "risk premium and liquidity premium at each leg, AND "
            "subject to index-family mismatch caveat — see the "
            "tool's methodology disclosure)."
        ),
        rows=rows,
    )


def _build_canonical_zscore_series(
    display_df: pd.DataFrame,
    *,
    country_a_nominal_pair: str,
    country_a_linker_pair: str,
    country_b_nominal_pair: str,
    country_b_linker_pair: str,
    tenor: str,
    z_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's z_score column into the
    canonical ``TimeSeries`` shape (closed-enum Z_SCORE units).

    Naming convention:
    ``<an>_<al>_<bn>_<bl>_<tenor>_xc_breakeven_spread_zscore``.
    Values match ``time_series[i].z_score`` 1-to-1 (rounded via
    the same ``z_score_round_decimals`` convention applied to the
    bespoke field) so the two series cannot drift.
    """
    slug = _series_name_slug(
        country_a_nominal_pair=country_a_nominal_pair,
        country_a_linker_pair=country_a_linker_pair,
        country_b_nominal_pair=country_b_nominal_pair,
        country_b_linker_pair=country_b_linker_pair,
        tenor=tenor,
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=safe_float(row.z_score, decimals=z_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=f"{slug}_xc_breakeven_spread_zscore",
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"Rolling z-score of the cross-country breakeven "
            f"spread ({country_a_nominal_pair}/"
            f"{country_a_linker_pair} - "
            f"{country_b_nominal_pair}/{country_b_linker_pair}) "
            f"at {tenor} vs its own trailing window."
        ),
        rows=rows,
    )
