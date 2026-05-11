"""
compute.py — Config-driven same-country linker real-yield butterfly
====================================================================

Owns the desk concept of a *same-country linker real-yield butterfly*
(curvature) on a single sovereign linker curve (e.g. USD_TIPS 5s10s30s
real-yield butterfly, GBP_LINKER 2s10s30s real-yield butterfly,
EUR_FR_LINKER 2s10s15s real-yield butterfly, CAD_RRB 5s10s30s
real-yield butterfly).  Computed per-trade-date via the FIXED simple-
butterfly weighting:

    butterfly_pct = belly_real_yield_pct
                  - 0.5 * (short_real_yield_pct + long_real_yield_pct)

Equivalent to ``0.5 * (2 * belly - short - long)``.  Sign convention:
POSITIVE = belly is CHEAP versus the half-weighted wings; NEGATIVE =
belly is RICH.  Matches the sovereign_bonds/butterfly sign convention
(which uses the mathematically-equivalent doubled form in BPS).

where ``short_real_yield_pct`` / ``belly_real_yield_pct`` /
``long_real_yield_pct`` are the linker real-yield levels at
``short_tenor`` / ``belly_tenor`` / ``long_tenor`` respectively (each
computed by ``get_real_yield_level``).  T_short / T_belly / T_long are
parsed from the tenor strings via the shared
``shared.analytics.curve_bootstrap.tenor_to_years`` helper purely for
desk-readable display in ``current_metrics.short_years`` /
``belly_years`` / ``long_years`` (the butterfly itself is a fixed-
weighted combination, not a year-weighted forward).

Composition path (deliberately simple)
--------------------------------------
This primitive composes three calls to ``get_real_yield_level`` (one
at ``short_tenor``, one at ``belly_tenor``, one at ``long_tenor``)
into the per-trade-date butterfly.  That gets the load-bearing four-
conjunct SELECT guard "for free":

  - ``instrument_type='inflation_linker'`` AND
    ``curve_family=<user>`` AND ``tenor=<user>`` AND
    ``field_name=<resolved>``.

The level primitive's compute() is invoked with ``config=`` set to
THIS primitive's ToolConfig.  Every shared convention name is present
in both YAMLs with the same numeric value (the cross-config lint
enforces that), so the inner compute reads exactly the same window /
min_periods / ddof / buffer / ffill / rounding values it would read
from its own bundled config.  Tests that override conventions on the
butterfly primitive's ToolConfig automatically see those overrides
flow through to the inner level calls.

Same-curve-family / same-country invariant
------------------------------------------
The three endpoint reads MUST resolve to the same linker
``curve_family``.  By the single-curve_family input shape that is
trivially guaranteed.  We additionally re-assert on the resolved
``macro_data.instrument_master`` rows for that curve_family:

  - rows exist under ``instrument_type='inflation_linker'``,
  - they map to a single ``(country, currency)`` tuple
    (DISTINCT-collapse), and
  - that tuple is non-null.

A failure (no rows, multiple distinct tuples, null fields) surfaces a
controlled error envelope.  This is the post-fetch guard the catalog
requires; it would catch a real instrument_master inconsistency at a
curve family that the silent compose path would otherwise smear
across mismatched conventions.  Mirrors the same guard in
real_yield_curve_spread (its single-curve_family sibling).

Why an extended inner lookback
------------------------------
Each inner level call trims its time_series to the requested
``lookback_days`` worth of display history (anchored to the latest
observation date).  Our rolling z-score / trailing range / period
changes need a longer history so they're fully populated from the
first row of OUR display window.  We therefore pass ``lookback_days =
params.lookback_days + buffer_calendar_days`` to each inner level
call, then align + trim ourselves.  The buffer math matches the level
primitive's own buffer math (``z_window * buffer_multiplier``) so
this primitive does not double-buffer.

Per-trade-date alignment
------------------------
The three endpoint real-yield series are inner-joined on date so we
only emit rows where ALL THREE endpoints had data.  No ffill across
the join: dates where any endpoint has no data are dropped from the
butterfly series, NOT carried forward as synthetic butterfly points.

Trailing-range wire freeze
--------------------------
``trailing_range_window_days`` is locked at 252 in V1.  The output
schema's field names (``high_252d_pct``, ``low_252d_pct``,
``percentile_252d``) embed the number; changing the convention
without renaming the wire fields would silently lie about what the
percentile is computed against.  ``compute()`` raises
``NotImplementedError`` if this is set to anything else; see
``methodology.planned_extensions`` for the path to making it
configurable.

Test seam
---------
``get_real_yield_level`` and ``_fetch_curve_family_country_currency``
are imported here at module level; tests patch them via
``patch("rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly.compute.X")``.
The inner level primitive's own seams
(``rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.fetch_single_tenor``,
``...compute.date``) are also patchable for end-to-end synthetic-
fixture testing.  This module does NOT import ``date`` directly — the
fetch start-date logic lives inside the level primitive, which
exposes its own date seam.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly.schemas import (
    RealYieldButterflyCurrentMetrics,
    RealYieldButterflyInput,
    RealYieldButterflyOutput,
    RealYieldButterflyTimeSeriesRow,
)
from rates_agent.inflation_indexed_bonds.tools.real_yield_level import (
    RealYieldLevelInput,
    get_real_yield_level,
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


# Code-level invariant — the whole point of this primitive owning a
# separate concept from sovereign butterfly is that it operates on
# inflation-linker rows, not nominal sovereign rows.  Putting this in
# YAML would let an edit to config.yaml silently switch the tool over
# to nominal data; per DESIGN_PRINCIPLES.md §5, structural identity
# stays in code.
_LINKER_INSTRUMENT_TYPE: str = "inflation_linker"


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull the methodology kwargs the real_yield_butterfly compute()
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
            f"field names (high_252d_pct, low_252d_pct, percentile_252d) "
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
        "yield_round_decimals": config.convention_value("yield_round_decimals"),
        "bps_round_decimals": config.convention_value("bps_round_decimals"),
        "window_years_round": config.convention_value(
            "window_years_round_decimals",
        ),
    }


# ============================================================================
# SAME-CURVE-FAMILY IDENTITY GUARD (post-fetch instrument_master assertion)
# ============================================================================
#
# Read-only SELECT against the instrument_master table.  Used purely
# to re-assert that the linker curve_family resolves to a single
# (country, currency) tuple under ``instrument_type='inflation_linker'``
# — the load-bearing identity invariant for a same-country / same-
# curve-family object.  DISTINCT collapses a curve_family that spans
# many tenors / instruments down to its (country, currency) identity;
# multiple distinct rows surface as a controlled error.  Mirrors the
# helper in real_yield_curve_spread.compute but local to this tool so
# the post-fetch guard is self-contained.

_INSTRUMENT_COUNTRY_CURRENCY_SQL = text(
    """
    SELECT DISTINCT country, currency
    FROM macro_data.instrument_master
    WHERE curve_family    = :curve_family
      AND instrument_type = :instrument_type
    """
)


def _fetch_curve_family_country_currency(
    engine: Engine,
    curve_family: str,
    instrument_type: str,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return ``(country, currency, error_message)`` for a
    ``(curve_family, instrument_type)`` pair.

    Returns one of three honest shapes:

      - ``(country, currency, None)`` on a single matching row,
      - ``(None, None, "<no rows>")`` when the pair is unknown to
        the instrument master,
      - ``(None, None, "<multiple distinct (country, currency) rows>")``
        when the pair maps to more than one identity (impossible in
        the current universe but treated as a controlled error if it
        ever shows up — never silently picked).
    """
    with engine.connect() as conn:
        rows = conn.execute(
            _INSTRUMENT_COUNTRY_CURRENCY_SQL,
            {
                "curve_family": curve_family,
                "instrument_type": instrument_type,
            },
        ).mappings().all()
    if not rows:
        return (
            None,
            None,
            (
                f"No instrument_master rows found for "
                f"curve_family='{curve_family}', "
                f"instrument_type='{instrument_type}'.  This tool's "
                "same-country / same-curve-family invariant cannot "
                "be evaluated without the (country, currency) "
                "identity for the curve.  Verify the curve family "
                "is spelled correctly and is ingested with "
                f"instrument_type='{instrument_type}' (see "
                "rates_agent/playbooks/inflation_indexed_bonds.yml)."
            ),
        )
    if len(rows) > 1:
        observed = sorted(
            f"({r['country']!r}, {r['currency']!r})" for r in rows
        )
        return (
            None,
            None,
            (
                f"Ambiguous instrument_master metadata for "
                f"curve_family='{curve_family}', "
                f"instrument_type='{instrument_type}': "
                f"multiple distinct (country, currency) rows "
                f"observed: {observed}.  This tool's same-country "
                "/ same-curve-family invariant requires a single "
                "(country, currency) identity per (curve_family, "
                "instrument_type) pair and refuses to pick one "
                "silently."
            ),
        )
    only = rows[0]
    country = only["country"]
    currency = only["currency"]
    if not country or not currency:
        return (
            None,
            None,
            (
                f"Incomplete instrument_master identity for "
                f"curve_family='{curve_family}', "
                f"instrument_type='{instrument_type}': "
                f"country={country!r}, currency={currency!r}.  "
                "Both fields must be populated for the same-country "
                "invariant to be evaluable."
            ),
        )
    return country, currency, None


def _enforce_same_curve_family_identity(
    engine: Engine,
    *,
    curve_family: str,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Look up the ``(country, currency)`` of the requested linker
    curve_family and refuse the request if the lookup fails or the
    curve_family does not resolve under
    ``instrument_type='inflation_linker'``.

    Returns ``(country, currency, None)`` on a clean resolution (the
    caller proceeds to the compose path).  Returns ``(None, None,
    error_message)`` otherwise — the caller wraps the error in the
    standard ``{"error": "..."}`` envelope.
    """
    country, currency, err = _fetch_curve_family_country_currency(
        engine,
        curve_family,
        _LINKER_INSTRUMENT_TYPE,
    )
    if err is not None:
        return None, None, (
            "Could not resolve country/currency for the linker "
            f"curve_family='{curve_family}' "
            f"(instrument_type='{_LINKER_INSTRUMENT_TYPE}').  "
            f"{err}  If you passed a nominal sovereign "
            "curve_family (e.g. 'UST', 'DE_BUND'), use the "
            "sovereign_bonds calculate_butterfly_tool instead — "
            "this tool only operates on inflation-linker rows."
        )
    return country, currency, None


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_real_yield_butterfly(
    engine: Engine,
    params: RealYieldButterflyInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Calculate the same-country linker real-yield butterfly
    (curvature) on a single sovereign linker curve.

        butterfly_pct = belly_real_yield_pct
                      - 0.5 * (short_real_yield_pct
                               + long_real_yield_pct)

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : RealYieldButterflyInput
        Validated input.  ``field_name=None`` resolves against the
        YAML's ``default_field_name`` convention.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides; production
        wrappers must pass ``config=`` explicitly.

    Returns
    -------
    dict
        Serialised ``RealYieldButterflyOutput``, or ``{"error": "..."}``
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
    period_offsets = conv["period_offsets"]
    trailing_window = conv["trailing_window"]
    yield_round_decimals = conv["yield_round_decimals"]
    bps_round_decimals = conv["bps_round_decimals"]
    window_years_round = conv["window_years_round"]

    # Wire-honesty disclosure threaded from YAML — NOT hardcoded.
    methodology_label = config.methodology.what_it_does.strip()

    # ------------------------------------------------------------------
    # Same-curve-family identity guard (post-fetch instrument_master
    # re-assertion).  Mirrors real_yield_curve_spread's identity guard
    # but scoped to all three endpoints of the same linker
    # curve_family.  Fires BEFORE any market-data SELECT — a non-
    # linker curve_family (e.g. 'UST') returns its controlled error
    # envelope here without firing any of the level primitive's
    # fetches.
    # ------------------------------------------------------------------
    country, currency, identity_err = _enforce_same_curve_family_identity(
        engine,
        curve_family=params.curve_family,
    )
    if identity_err is not None:
        return {"error": identity_err}

    # ------------------------------------------------------------------
    # Year fractions for display.  The Pydantic validator already
    # enforced parsability and ordering; re-running the parser here is
    # cheap and keeps compute() self-contained.
    # ------------------------------------------------------------------
    short_years = tenor_to_years(params.short_tenor)
    belly_years = tenor_to_years(params.belly_tenor)
    long_years = tenor_to_years(params.long_tenor)

    # ------------------------------------------------------------------
    # Extended inner lookback.  Each inner level call trims its
    # time_series to ``lookback_days`` worth of display history.  We
    # need a longer window so OUR rolling stats are fully populated
    # from the first row of OUR display window; match the buffer math
    # the level primitive uses internally so we don't double-buffer.
    # ------------------------------------------------------------------
    buffer_calendar_days = int(
        max(z_window, trailing_window) * buffer_multiplier
    )
    extended_lookback_days = params.lookback_days + buffer_calendar_days

    # ------------------------------------------------------------------
    # Compose: call the linker real-yield-level primitive once per
    # endpoint tenor.  This inherits the no-proxy guard
    # (instrument_type='inflation_linker' filter on the DB read) for
    # free.  We pass ``config=`` so the inner conventions use the same
    # values this primitive uses; cross-config lint enforces value-
    # agreement on the bundled YAMLs, and any test override on this
    # primitive's ToolConfig flows through to the inner level calls.
    # ------------------------------------------------------------------
    endpoint_specs = (
        ("short", params.short_tenor),
        ("belly", params.belly_tenor),
        ("long", params.long_tenor),
    )
    inner_results: Dict[str, Dict[str, Any]] = {}
    for endpoint_label, tenor in endpoint_specs:
        result = get_real_yield_level(
            engine=engine,
            params=RealYieldLevelInput(
                curve_family=params.curve_family,
                tenor=tenor,
                lookback_days=extended_lookback_days,
                field_name=params.field_name,
            ),
            config=config,
        )
        if "error" in result:
            return {
                "error": (
                    f"Real-yield butterfly "
                    f"{params.short_tenor}{params.belly_tenor}"
                    f"{params.long_tenor} could not be computed for "
                    f"{params.curve_family}: the {endpoint_label}-"
                    f"tenor endpoint ({tenor}) failed.  Inner "
                    f"error: {result['error']}"
                )
            }
        inner_results[endpoint_label] = result

    # ------------------------------------------------------------------
    # Align endpoint real-yield series by date.  The level primitive's
    # canonical TimeSeries already has each row pre-rounded to
    # ``yield_round_decimals`` from the inner compute; we use those
    # rows directly so the canonical-shape contract is end-to-end.
    # Strict pandas inner-join on date so we only emit rows where ALL
    # THREE endpoints had data — no synthetic butterfly points are
    # produced on dates where any endpoint has no data.
    # ------------------------------------------------------------------
    endpoint_frames: Dict[str, pd.DataFrame] = {}
    for endpoint_label in ("short", "belly", "long"):
        rows = inner_results[endpoint_label].get("time_series", {}).get(
            "rows", [],
        )
        if not rows:
            return {
                "error": (
                    f"Real-yield butterfly "
                    f"{params.short_tenor}{params.belly_tenor}"
                    f"{params.long_tenor} could not be computed for "
                    f"{params.curve_family}: the {endpoint_label}-"
                    "tenor endpoint returned no canonical TimeSeries "
                    "rows.  This is a bug — the level primitive "
                    "returned a snapshot but no history.  Please "
                    f"report with the curve_family/short/belly/long "
                    f"{params.curve_family} / {params.short_tenor} / "
                    f"{params.belly_tenor} / {params.long_tenor}."
                )
            }
        col_name = f"{endpoint_label}_real_yield_pct"
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
            "short_real_yield_pct",
            "belly_real_yield_pct",
            "long_real_yield_pct",
        ],
    )

    if wide.empty:
        return {
            "error": (
                f"After aligning the three endpoint real-yield "
                f"series for {params.curve_family} ("
                f"{params.short_tenor} → {params.belly_tenor} → "
                f"{params.long_tenor}), no overlapping dates remain.  "
                "Verify all three pillars have data on this linker "
                "curve."
            )
        }

    # ------------------------------------------------------------------
    # Butterfly formula:
    #   butterfly_pct = belly - 0.5 * (short + long)
    # The endpoint real-yield series come in already rounded to
    # yield_round_decimals (the level primitive's canonical TimeSeries
    # applies the rounding upstream), so we round the butterfly once
    # at the end of this arithmetic.  Same boundary-rounding
    # discipline as the sibling curve-spread primitives.  Wing spreads
    # use the same boundary-rounding so the snapshot's wing_*_pct
    # fields agree with the desk's manual decomposition.
    # ------------------------------------------------------------------
    short_series = wide["short_real_yield_pct"]
    belly_series = wide["belly_real_yield_pct"]
    long_series = wide["long_real_yield_pct"]
    wide["butterfly_pct"] = (
        belly_series - 0.5 * (short_series + long_series)
    ).round(yield_round_decimals)
    wide["wing_short_pct"] = (belly_series - short_series).round(
        yield_round_decimals,
    )
    wide["wing_long_pct"] = (long_series - belly_series).round(
        yield_round_decimals,
    )

    # ------------------------------------------------------------------
    # Rolling z-score on the butterfly series (in PERCENT units).
    # ------------------------------------------------------------------
    wide["z_score"] = rolling_zscore(
        wide["butterfly_pct"],
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_round_decimals,
    )

    # ------------------------------------------------------------------
    # Trim to requested display lookback — anchored to the data's
    # latest aligned trade_date.  Matches real_yield_level /
    # real_yield_curve_spread.
    # ------------------------------------------------------------------
    as_of_ts = wide.index[-1]
    cutoff = pd.Timestamp(
        as_of_ts.date() - timedelta(days=params.lookback_days),
    )
    display_df = wide.loc[wide.index >= cutoff].copy()

    if display_df.empty:
        return {
            "error": (
                f"No real-yield butterfly observations within the "
                f"last {params.lookback_days} days for "
                f"'{params.curve_family}' over "
                f"{params.short_tenor}{params.belly_tenor}"
                f"{params.long_tenor}."
            )
        }

    # ------------------------------------------------------------------
    # Build current_metrics
    # ------------------------------------------------------------------
    latest = display_df.iloc[-1]
    bflys = display_df["butterfly_pct"]

    current_butterfly_pct = safe_float(
        latest["butterfly_pct"], decimals=yield_round_decimals,
    )

    # Daily / weekly / monthly BPS changes — series is in PERCENT so
    # already_bps=False (multiplies by 100 to produce bps).  decimals=
    # passes the YAML convention through so the changes respect
    # bps_round_decimals.
    changes = period_changes(
        bflys,
        offsets=period_offsets,
        already_bps=False,
        decimals=bps_round_decimals,
    )

    # Trailing high/low/percentile of the real-yield butterfly
    # (percent scale).
    high, low, percentile = trailing_high_low_percentile(
        bflys, window=trailing_window, decimals=yield_round_decimals,
    )

    butterfly_label = (
        f"{params.curve_family} "
        f"{params.short_tenor.replace('Y', '')}s"
        f"{params.belly_tenor.replace('Y', '')}s"
        f"{params.long_tenor.replace('Y', '')}s real-yield"
    )

    metrics = RealYieldButterflyCurrentMetrics(
        as_of_date=as_of_ts.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        short_tenor=params.short_tenor,
        belly_tenor=params.belly_tenor,
        long_tenor=params.long_tenor,
        butterfly_label=butterfly_label,
        current_butterfly_pct=current_butterfly_pct,
        daily_change_bps=changes["daily"],
        weekly_change_bps=changes["weekly"],
        monthly_change_bps=changes["monthly"],
        # Pass z_round_decimals so a YAML override above 4 isn't
        # silently truncated by safe_float's default of 4.
        current_z_score=safe_float(
            latest.get("z_score"), decimals=z_round_decimals,
        ),
        rolling_window_days=z_window,
        high_252d_pct=high,
        low_252d_pct=low,
        percentile_252d=percentile,
        wing_short_pct=safe_float(
            latest.get("wing_short_pct"), decimals=yield_round_decimals,
        ),
        wing_long_pct=safe_float(
            latest.get("wing_long_pct"), decimals=yield_round_decimals,
        ),
        short_real_yield_pct=safe_float(
            latest.get("short_real_yield_pct"),
            decimals=yield_round_decimals,
        ),
        belly_real_yield_pct=safe_float(
            latest.get("belly_real_yield_pct"),
            decimals=yield_round_decimals,
        ),
        long_real_yield_pct=safe_float(
            latest.get("long_real_yield_pct"),
            decimals=yield_round_decimals,
        ),
        short_years=round(short_years, window_years_round),
        belly_years=round(belly_years, window_years_round),
        long_years=round(long_years, window_years_round),
        observation_count=len(display_df),
        country=country,
        currency=currency,
        methodology_label=methodology_label,
    )

    # ------------------------------------------------------------------
    # Build time_series (bespoke shape) AND the two canonical
    # TimeSeries (butterfly in PERCENT, z-score in Z_SCORE).  All
    # three share the same display_df rows so they cannot drift.
    # ------------------------------------------------------------------
    ts_rows = [
        RealYieldButterflyTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            butterfly_pct=round(row.butterfly_pct, yield_round_decimals),
            z_score=safe_float(row.z_score, decimals=z_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    canonical_butterfly = _build_canonical_butterfly_series(
        display_df,
        curve_family=params.curve_family,
        short_tenor=params.short_tenor,
        belly_tenor=params.belly_tenor,
        long_tenor=params.long_tenor,
        yield_round_decimals=yield_round_decimals,
        country=country,
        currency=currency,
    )
    canonical_zscore = _build_canonical_zscore_series(
        display_df,
        curve_family=params.curve_family,
        short_tenor=params.short_tenor,
        belly_tenor=params.belly_tenor,
        long_tenor=params.long_tenor,
        z_round_decimals=z_round_decimals,
    )

    output = RealYieldButterflyOutput(
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
    curve_family: str,
    short_tenor: str,
    belly_tenor: str,
    long_tenor: str,
) -> str:
    """Lower-snake-case slug used as the canonical TimeSeries
    series_name prefix.  Pattern:
    ``<curve_family>_<short_tenor>_<belly_tenor>_<long_tenor>``.
    """
    return (
        f"{curve_family.lower()}_"
        f"{short_tenor.lower()}_"
        f"{belly_tenor.lower()}_"
        f"{long_tenor.lower()}"
    )


def _build_canonical_butterfly_series(
    display_df: pd.DataFrame,
    *,
    curve_family: str,
    short_tenor: str,
    belly_tenor: str,
    long_tenor: str,
    yield_round_decimals: int,
    country: str,
    currency: str,
) -> TimeSeries:
    """Convert the display DataFrame's butterfly_pct column into the
    canonical ``TimeSeries`` shape (closed-enum PERCENT units).

    Naming convention:
    ``<curve_family>_<short>_<belly>_<long>_real_yield_butterfly``.
    """
    slug = _series_name_slug(
        curve_family=curve_family,
        short_tenor=short_tenor,
        belly_tenor=belly_tenor,
        long_tenor=long_tenor,
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=round(float(row.butterfly_pct), yield_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=f"{slug}_real_yield_butterfly",
        units=TimeSeriesUnits.PERCENT,
        description=(
            f"Same-country linker real-yield butterfly (curvature) "
            f"for {curve_family} ({short_tenor}/{belly_tenor}/"
            f"{long_tenor}; belly - 0.5*(short+long)) over the "
            "displayed window.  Curvature of REAL YIELDS for "
            f"({country}, {currency}) linkers — distinct from a "
            "real-yield curve spread (2-point difference) and from "
            "a nominal sovereign butterfly (3-point curvature of "
            "nominal yields, in BPS).  Quoted in PERCENT (same "
            "units as the underlying real yields)."
        ),
        rows=rows,
    )


def _build_canonical_zscore_series(
    display_df: pd.DataFrame,
    *,
    curve_family: str,
    short_tenor: str,
    belly_tenor: str,
    long_tenor: str,
    z_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's z_score column into the
    canonical ``TimeSeries`` shape (closed-enum Z_SCORE units).

    Naming convention:
    ``<curve_family>_<short>_<belly>_<long>_real_yield_butterfly_zscore``.
    Values match ``time_series[i].z_score`` 1-to-1 (rounded via the
    same ``z_score_round_decimals`` convention applied to the
    bespoke field) so the two series cannot drift.
    """
    slug = _series_name_slug(
        curve_family=curve_family,
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
        series_name=f"{slug}_real_yield_butterfly_zscore",
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"Rolling z-score of the {curve_family} real-yield "
            f"butterfly (in PERCENT) over {short_tenor}/"
            f"{belly_tenor}/{long_tenor} vs its own trailing window."
        ),
    rows=rows,
    )
