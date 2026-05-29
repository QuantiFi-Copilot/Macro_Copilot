"""
compute.py — Config-driven same-country linker real-yield curve spread
========================================================================

Sixth tool in the ``inflation_indexed_bonds`` domain.  Owns the
desk concept of a *same-country linker real-yield curve spread*
between two real-yield tenors of the same sovereign linker curve
(e.g. USD_TIPS 5s10s real-yield, GBP_LINKER 2s10s real-yield,
EUR_FR_LINKER 5s10s real-yield, CAD_RRB 5s30s real-yield).
Computed per-trade-date via the SPREAD formula:

    spread_pct = long_real_yield_pct - short_real_yield_pct

where ``short_real_yield_pct`` and ``long_real_yield_pct`` are the
linker real-yield levels at ``short_tenor`` and ``long_tenor``
respectively (each computed by ``get_real_yield_level``).
T_short / T_long are parsed from the tenor strings via the shared
``shared.analytics.curve_bootstrap.tenor_to_years`` helper purely
for desk-readable display in ``current_metrics.short_years`` /
``long_years`` (the spread itself is a difference, not a year-
weighted forward).

Composition path (deliberately simple)
--------------------------------------
This primitive composes two calls to ``get_real_yield_level`` (one
at ``short_tenor``, one at ``long_tenor``) into the per-trade-date
difference.  That gets the load-bearing four-conjunct SELECT guard
"for free":

  - ``instrument_type='inflation_linker'`` AND
    ``curve_family=<user>`` AND ``tenor=<user>`` AND
    ``field_name=<resolved>``.

The level primitive's compute() is invoked with ``config=`` set
to THIS primitive's ToolConfig.  Every shared convention name is
present in both YAMLs with the same numeric value (the cross-
config lint enforces that), so the inner compute reads exactly
the same window / min_periods / ddof / buffer / ffill / rounding
values it would read from its own bundled config.  Tests that
override conventions on the curve-spread primitive's ToolConfig
automatically see those overrides flow through to the inner
level calls.

Same-curve-family / same-country invariant
------------------------------------------
The two endpoint reads MUST resolve to the same linker
``curve_family``.  By the single-curve_family input shape that is
trivially guaranteed.  We additionally re-assert on the resolved
``macro_data.instrument_master`` rows for that curve_family:

  - rows exist under ``instrument_type='inflation_linker'``,
  - they map to a single ``(country, currency)`` tuple
    (DISTINCT-collapse), and
  - that tuple is non-null.

A failure (no rows, multiple distinct tuples, null fields) surfaces
a controlled error envelope.  This is the post-fetch guard the
catalog requires; it would catch a real instrument_master
inconsistency at a curve family that the silent compose path would
otherwise smear across mismatched conventions.

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
own buffer math (``z_window * buffer_multiplier``) so this
primitive does not double-buffer.

Per-trade-date alignment
------------------------
The two endpoint real-yield series are inner-joined on date so
we only emit rows where BOTH endpoints had data.  No ffill across
the join: dates where one endpoint has no data are dropped from
the spread series, NOT carried forward as synthetic spread points.

Trailing-range wire freeze
--------------------------
``trailing_range_window_days`` is locked at 252 in V1.  The
output schema's field names (``high_252d_pct``,
``low_252d_pct``, ``percentile_252d``) embed the number; changing
the convention without renaming the wire fields would silently
lie about what the percentile is computed against.  ``compute()``
raises ``NotImplementedError`` if this is set to anything else;
see ``methodology.planned_extensions`` for the path to making it
configurable.

Test seam
---------
``get_real_yield_level`` and ``_fetch_curve_family_country_currency``
are imported here at module level; tests patch them via
``patch("rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread.compute.X")``.
The inner level primitive's own seams
(``rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.fetch_single_tenor``,
``...compute.date``) are also patchable for end-to-end synthetic-
fixture testing.  This module does NOT import ``date`` directly —
the fetch start-date logic lives inside the level primitive,
which exposes its own date seam.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread.schemas import (
    RealYieldCurveSpreadCurrentMetrics,
    RealYieldCurveSpreadInput,
    RealYieldCurveSpreadOutput,
    RealYieldCurveSpreadTimeSeriesRow,
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


# Bundled config — public symbol so external callers (mcp_server,
# REST routes, tests, future batch surfaces) can build a ToolConfig
# from the same source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Trailing-range window is wire-frozen at 252 in V1; see schemas.py
# and config.yaml's ``planned_extensions``.
_FROZEN_TRAILING_WINDOW: int = 252


# Code-level invariant — the whole point of this primitive owning a
# separate concept from sovereign curve_spread is that it operates
# on inflation-linker rows, not nominal sovereign rows.  Putting
# this in YAML would let an edit to config.yaml silently switch the
# tool over to nominal data; per DESIGN_PRINCIPLES.md §5, structural
# identity stays in code.
_LINKER_INSTRUMENT_TYPE: str = "inflation_linker"


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(
    config: ToolConfig,
    params: Optional[RealYieldCurveSpreadInput] = None,
) -> dict:
    """Pull the methodology kwargs the real_yield_curve_spread
    compute() needs from a ToolConfig, applying any caller overrides
    from ``params``.

    Override semantics
    ------------------
    For each Pydantic Input field whose corresponding YAML convention
    has ``exposure.expose: true`` (see
    ``docs_revamped/03_standards/methodology_exposure.md``):

      - When the Input field is ``None`` (the schema default) the YAML
        value is used.
      - When the Input field carries an explicit value, that value
        overrides the YAML for this call.

    The three rolling-z-score conventions are the currently-exposed
    methodology surface (mirroring the sibling linker real_yield_level
    tool):
      - ``z_score_window_days``
      - ``z_score_min_periods``
      - ``z_score_ddof``

    These overrides apply to the SPREAD's own rolling z-score AND the
    fetch-window buffer math (``extended_lookback_days`` is derived
    from ``z_window``).  The inner endpoint ``get_real_yield_level``
    calls intentionally receive the bundled config WITHOUT these Input
    overrides because their z-scores are NOT consumed — only their
    real-yield level ``time_series`` rows feed the spread.

    The remaining YAML-locked conventions (period offsets, buffer,
    ffill, trailing range, rounding, window-year display rounding) are
    read straight from the config regardless of ``params``.

    Raises NotImplementedError if ``trailing_range_window_days``
    is set to a value the V1 wire surface cannot honestly
    represent.  See the module docstring for the wire-freeze
    rationale.

    Backward compatibility
    ----------------------
    ``params`` is optional (default None) so legacy callers that
    invoked this helper without an Input continue to work — the
    no-params path returns the pure-YAML resolution that pre-dated
    the Phase-1 exposure work.
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

    def _override(convention_name: str) -> Any:
        """Return the caller's Input value when non-None, else the YAML value.

        Mirrors the field_name sentinel pattern: caller's explicit
        value wins; None falls through to YAML.  Defensive against
        future schema changes via ``getattr(..., default=None)``.
        """
        if params is not None:
            input_value = getattr(params, convention_name, None)
            if input_value is not None:
                return input_value
        return config.convention_value(convention_name)

    return {
        # Exposed methodology surface — Input overrides accepted.
        "z_window": _override("z_score_window_days"),
        "z_min_periods": _override("z_score_min_periods"),
        "z_ddof": _override("z_score_ddof"),
        # YAML-locked conventions — read straight from config.
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
# helper in breakeven_inflation_simple.compute but local to this tool
# so the post-fetch guard is self-contained.

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
    curve_family and refuse the request if the lookup fails or
    the curve_family does not resolve under
    ``instrument_type='inflation_linker'``.

    Returns ``(country, currency, None)`` on a clean resolution
    (the caller proceeds to the compose path).  Returns
    ``(None, None, error_message)`` otherwise — the caller wraps
    the error in the standard ``{"error": "..."}`` envelope.
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
            "sovereign_bonds calculate_curve_spread_tool instead — "
            "this tool only operates on inflation-linker rows."
        )
    return country, currency, None


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_real_yield_curve_spread(
    engine: Engine,
    params: RealYieldCurveSpreadInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Calculate the same-country linker real-yield curve spread
    between two real-yield tenors of the same sovereign linker
    curve.

        spread_pct = long_real_yield_pct - short_real_yield_pct

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : RealYieldCurveSpreadInput
        Validated input.  ``field_name=None`` resolves against
        the YAML's ``default_field_name`` convention.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass
        a custom ToolConfig to exercise convention overrides;
        production wrappers must pass ``config=`` explicitly.

    Returns
    -------
    dict
        Serialised ``RealYieldCurveSpreadOutput``, or
        ``{"error": "..."}`` on recoverable failure.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # Pull conventions.  Applies any per-call Input overrides for the
    # exposed rolling-z-score surface (z_score_window_days /
    # z_score_min_periods / z_score_ddof) — these flow into BOTH the
    # spread's own rolling z-score AND the fetch-window buffer math
    # below.  The inner endpoint level calls intentionally use the
    # bundled YAML default (their z-score is not consumed).  See
    # _conventions_from_config + config.yaml exposure blocks.
    # ------------------------------------------------------------------
    conv = _conventions_from_config(config, params)
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
    # re-assertion).  Mirrors breakeven_inflation_simple's same-
    # country invariant guard but scoped to a single linker
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
    # Compose: call the linker real-yield-level primitive once per
    # endpoint tenor.  This inherits the no-proxy guard
    # (instrument_type='inflation_linker' filter on the DB read) for
    # free.  We pass ``config=`` so the inner conventions use the
    # same values this primitive uses; cross-config lint enforces
    # value-agreement on the bundled YAMLs, and any test override
    # on this primitive's ToolConfig flows through to the inner
    # level calls.
    # ------------------------------------------------------------------
    short_inner = get_real_yield_level(
        engine=engine,
        params=RealYieldLevelInput(
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
                f"Real-yield curve spread "
                f"{params.short_tenor}{params.long_tenor} could not "
                f"be computed for {params.curve_family}: the short-"
                f"tenor endpoint ({params.short_tenor}) failed.  "
                f"Inner error: {short_inner['error']}"
            )
        }

    long_inner = get_real_yield_level(
        engine=engine,
        params=RealYieldLevelInput(
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
                f"Real-yield curve spread "
                f"{params.short_tenor}{params.long_tenor} could not "
                f"be computed for {params.curve_family}: the long-"
                f"tenor endpoint ({params.long_tenor}) failed.  "
                f"Inner error: {long_inner['error']}"
            )
        }

    # ------------------------------------------------------------------
    # Align endpoint real-yield series by date.  The level
    # primitive's canonical TimeSeries already has each row pre-
    # rounded to ``yield_round_decimals`` from the inner compute;
    # we use those rows directly so the canonical-shape contract is
    # end-to-end.  Strict pandas inner-join on date so we only emit
    # rows where BOTH endpoints had data — no synthetic spread
    # points are produced on dates where one endpoint has no data.
    # ------------------------------------------------------------------
    short_rows = short_inner.get("time_series", {}).get("rows", [])
    long_rows = long_inner.get("time_series", {}).get("rows", [])
    if not short_rows or not long_rows:
        return {
            "error": (
                f"Real-yield curve spread "
                f"{params.short_tenor}{params.long_tenor} could not "
                f"be computed for {params.curve_family}: at least "
                "one endpoint returned no canonical TimeSeries "
                "rows.  This is a bug — the level primitive "
                "returned a snapshot but no history.  Please report "
                f"with the curve_family/short/long {params.curve_family} "
                f"/ {params.short_tenor} / {params.long_tenor}."
            )
        }

    short_df = pd.DataFrame(short_rows).rename(
        columns={"value": "short_real_yield_pct"},
    )
    long_df = pd.DataFrame(long_rows).rename(
        columns={"value": "long_real_yield_pct"},
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
        subset=["short_real_yield_pct", "long_real_yield_pct"],
    )

    if wide.empty:
        return {
            "error": (
                f"After aligning the two endpoint real-yield series "
                f"for {params.curve_family} ({params.short_tenor} → "
                f"{params.long_tenor}), no overlapping dates "
                "remain.  Verify both pillars have data on this "
                "linker curve."
            )
        }

    # ------------------------------------------------------------------
    # Spread formula:  spread_pct = long_pct - short_pct
    # The endpoint real-yield series come in already rounded to
    # yield_round_decimals (the level primitive's canonical
    # TimeSeries applies the rounding upstream), so we round the
    # spread once at the end of this arithmetic.  Same boundary-
    # rounding discipline as the sibling curve-spread primitives.
    # ------------------------------------------------------------------
    wide["spread_pct"] = (
        wide["long_real_yield_pct"] - wide["short_real_yield_pct"]
    ).round(yield_round_decimals)

    # ------------------------------------------------------------------
    # Rolling z-score on the spread series (in PERCENT units).
    # ------------------------------------------------------------------
    wide["z_score"] = rolling_zscore(
        wide["spread_pct"],
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_round_decimals,
    )

    # ------------------------------------------------------------------
    # Trim to requested display lookback — anchored to the data's
    # latest aligned trade_date.  Matches real_yield_level /
    # breakeven_curve_spread / inflation_swap_curve_spread / OIS
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
                f"No real-yield curve spread observations within "
                f"the last {params.lookback_days} days for "
                f"'{params.curve_family}' over "
                f"{params.short_tenor}{params.long_tenor}."
            )
        }

    # ------------------------------------------------------------------
    # Build current_metrics
    # ------------------------------------------------------------------
    latest = display_df.iloc[-1]
    spreads = display_df["spread_pct"]

    current_spread_pct = safe_float(
        latest["spread_pct"], decimals=yield_round_decimals,
    )

    # Daily / weekly / monthly BPS changes — series is in PERCENT
    # so already_bps=False (multiplies by 100 to produce bps).
    # decimals= passes the YAML convention through so the changes
    # respect bps_round_decimals.
    changes = period_changes(
        spreads,
        offsets=period_offsets,
        already_bps=False,
        decimals=bps_round_decimals,
    )

    # Trailing high/low/percentile of the real-yield curve spread
    # (percent scale).
    high, low, percentile = trailing_high_low_percentile(
        spreads, window=trailing_window, decimals=yield_round_decimals,
    )

    spread_label = (
        f"{params.curve_family} "
        f"{params.short_tenor.replace('Y', '')}s"
        f"{params.long_tenor.replace('Y', '')}s real-yield"
    )

    metrics = RealYieldCurveSpreadCurrentMetrics(
        as_of_date=as_of_ts.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        short_tenor=params.short_tenor,
        long_tenor=params.long_tenor,
        spread_label=spread_label,
        current_spread_pct=current_spread_pct,
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
        short_real_yield_pct=safe_float(
            latest.get("short_real_yield_pct"),
            decimals=yield_round_decimals,
        ),
        long_real_yield_pct=safe_float(
            latest.get("long_real_yield_pct"),
            decimals=yield_round_decimals,
        ),
        short_years=round(short_years, window_years_round),
        long_years=round(long_years, window_years_round),
        observation_count=len(display_df),
        country=country,
        currency=currency,
        methodology_label=methodology_label,
    )

    # ------------------------------------------------------------------
    # Build time_series (bespoke shape) AND the two canonical
    # TimeSeries (spread in PERCENT, z-score in Z_SCORE).  All three
    # share the same display_df rows so they cannot drift.
    # ------------------------------------------------------------------
    ts_rows = [
        RealYieldCurveSpreadTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            spread_pct=round(row.spread_pct, yield_round_decimals),
            z_score=safe_float(row.z_score, decimals=z_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    canonical_spread = _build_canonical_spread_series(
        display_df,
        curve_family=params.curve_family,
        short_tenor=params.short_tenor,
        long_tenor=params.long_tenor,
        yield_round_decimals=yield_round_decimals,
        country=country,
        currency=currency,
    )
    canonical_zscore = _build_canonical_zscore_series(
        display_df,
        curve_family=params.curve_family,
        short_tenor=params.short_tenor,
        long_tenor=params.long_tenor,
        z_round_decimals=z_round_decimals,
    )

    output = RealYieldCurveSpreadOutput(
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
    yield_round_decimals: int,
    country: str,
    currency: str,
) -> TimeSeries:
    """Convert the display DataFrame's spread_pct column into the
    canonical ``TimeSeries`` shape (closed-enum PERCENT units).

    Naming convention:
    ``<curve_family>_<shortten>_<longten>_real_yield_curve_spread``.
    """
    slug = _series_name_slug(
        curve_family=curve_family,
        short_tenor=short_tenor,
        long_tenor=long_tenor,
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=round(float(row.spread_pct), yield_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=f"{slug}_real_yield_curve_spread",
        units=TimeSeriesUnits.PERCENT,
        description=(
            f"Same-country linker real-yield curve spread for "
            f"{curve_family} ({short_tenor}/{long_tenor}; "
            "long_real_yield_pct - short_real_yield_pct) over the "
            "displayed window.  Term structure of REAL YIELDS for "
            f"({country}, {currency}) linkers — distinct from a "
            "breakeven curve spread (inflation compensation) and "
            "from a nominal sovereign curve spread.  Quoted in "
            "PERCENT (same units as the underlying real yields)."
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
    ``<curve_family>_<shortten>_<longten>_real_yield_curve_spread_zscore``.
    Values match ``time_series[i].z_score`` 1-to-1 (rounded via
    the same ``z_score_round_decimals`` convention applied to the
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
        series_name=f"{slug}_real_yield_curve_spread_zscore",
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"Rolling z-score of the {curve_family} real-yield "
            f"curve spread (in PERCENT) over {short_tenor}/"
            f"{long_tenor} vs its own trailing window."
        ),
        rows=rows,
    )
