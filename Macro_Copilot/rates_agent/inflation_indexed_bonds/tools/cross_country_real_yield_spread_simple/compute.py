"""
compute.py — Config-driven same-tenor cross-country linker real-yield spread
=============================================================================

Seventh tool in the ``inflation_indexed_bonds`` domain.  Owns the
desk concept of a *same-tenor cross-country linker real-yield
differential* between two linker curves' real-yield levels at the
same tenor (e.g. USD_TIPS 10Y real yield minus GBP_LINKER 10Y real
yield).  Computed per-trade-date via the SPREAD formula:

    spread_pct = first_curve_real_yield_pct - second_curve_real_yield_pct

where ``first_curve_real_yield_pct`` and
``second_curve_real_yield_pct`` are the linker real-yield levels at
``tenor`` for first_curve_family and second_curve_family
respectively (each computed by ``get_real_yield_level`` filtered by
``instrument_type='inflation_linker'`` AND ``curve_family=?`` AND
``tenor=?`` AND ``field_name=?`` — the no-proxy guard inherited
transitively from the level primitive's compute path).

Concept honesty
---------------
This tool returns a CROSS-COUNTRY REAL-RATE DIFFERENTIAL.  Two
load-bearing caveats:

  1. INDEX-FAMILY MISMATCH: different countries' linkers reference
     different inflation indices (USD CPI-U non-seasonally adjusted
     vs euro-area HICP ex-tobacco vs UK RPI/CPIH vs Canada CPI).
     These are NOT identical inflation references.

  2. MARKET-STRUCTURE MISMATCH: linker markets differ materially in
     benchmark availability at the same tenor pillar, issuance
     size, liquidity premium, and deflation-floor treatment.  USD
     TIPS, GBP linkers, EUR-area linkers and Canadian RRBs are NOT
     fungible at the same tenor.

Both caveats live in the YAML's ``methodology.what_it_does`` and
in the canonical TimeSeries description, and are threaded onto the
wire via ``current_metrics.methodology_label``.

Sign convention
---------------
Per-trade-date, the displayed cross-country spread is
first_curve_family minus second_curve_family.  This sign
convention is FIXED and surfaced both in YAML and on the wire; the
tool never silently flips the sign.  Flipping the inputs (first ↔
second) flips the displayed sign.  The canonical TimeSeries
description embeds the literal token
``first_curve_real_yield_pct - second_curve_real_yield_pct`` so
downstream consumers cannot reinterpret the sign.

Composition path (deliberately simple)
--------------------------------------
This primitive composes two calls to ``get_real_yield_level``, one
per curve_family at the shared tenor.  That gets four load-bearing
guarantees "for free":

  - ``instrument_type='inflation_linker'`` filter on EACH leg of
    EACH curve — the no-proxy guard documented in the level
    primitive's compute() docstring (DESIGN_PRINCIPLES.md §1, §3 /
    STANDARD_TOOL_AND_YAML_RULES.md §J).
  - The four-conjunct SELECT guard inherited transitively at each
    leg: ``instrument_type='inflation_linker'`` AND
    ``curve_family=<user>`` AND ``tenor=<user>`` AND
    ``field_name=<resolved>``.
  - Boundary rounding parity at the latest aligned trade_date
    (``current_spread_pct`` matches the bespoke and canonical
    time_series last-row values bit-for-bit).
  - Convention threading inherited from the level primitive (this
    primitive's ToolConfig is passed unchanged into BOTH inner
    calls).

Both-legs-must-be-linker invariant
----------------------------------
BOTH curve_families MUST resolve under
``instrument_type='inflation_linker'`` in
``macro_data.instrument_master``.  We additionally re-assert on the
resolved rows for each curve_family:

  - rows exist under ``instrument_type='inflation_linker'``,
  - they map to a single ``(country, currency)`` tuple
    (DISTINCT-collapse), and
  - that tuple is non-null.

A failure on EITHER leg (no rows, multiple distinct tuples, null
fields) surfaces a controlled error envelope with a leg-attributed
prefix.  This guard mirrors
``real_yield_curve_spread._enforce_same_curve_family_identity`` but
runs on BOTH curve_families (since this primitive is cross-curve).
A non-linker curve_family on either side (e.g. a nominal sovereign
'UST' or 'DE_BUND') is therefore refused BEFORE any market-data
SELECT.

Why an extended inner lookback
------------------------------
Each inner level call trims its time_series to the requested
``lookback_days`` worth of display history (anchored to the latest
observation date).  Our rolling z-score / trailing range / period
changes need a longer history so they're fully populated from the
first row of OUR display window.  We therefore pass
``lookback_days = params.lookback_days + buffer_calendar_days`` to
each inner level call, then align + trim ourselves.  The buffer
math matches the level primitive's own buffer math
(``z_window * buffer_multiplier``) so this primitive does not
double-buffer.

Per-trade-date alignment
------------------------
The two endpoint real-yield series are inner-joined on date so we
only emit rows where BOTH curves had data.  No ffill across the
inner join: dates where one curve has no data are dropped from the
spread series, NOT carried forward as synthetic spread points.

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
``get_real_yield_level`` and
``_fetch_curve_family_country_currency`` are imported here at
module level; tests patch them via
``patch("rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple.compute.X")``.
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

from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple.schemas import (
    CrossCountryRealYieldSpreadSimpleCurrentMetrics,
    CrossCountryRealYieldSpreadSimpleInput,
    CrossCountryRealYieldSpreadSimpleOutput,
    CrossCountryRealYieldSpreadSimpleTimeSeriesRow,
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
# separate concept from sovereign cross_market_spread is that it
# operates on inflation-linker rows on BOTH legs, not nominal
# sovereign rows.  Putting this in YAML would let an edit to
# config.yaml silently switch the tool over to nominal data; per
# DESIGN_PRINCIPLES.md §5, structural identity stays in code.
_LINKER_INSTRUMENT_TYPE: str = "inflation_linker"


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull the methodology kwargs the
    cross_country_real_yield_spread_simple compute() needs from a
    ToolConfig.

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
# LINKER-IDENTITY GUARD (post-fetch instrument_master assertion on BOTH legs)
# ============================================================================
#
# Read-only SELECT against the instrument_master table.  Used to
# re-assert that EACH linker curve_family resolves to a single
# (country, currency) tuple under ``instrument_type='inflation_linker'``
# — the load-bearing identity invariant for a same-tenor cross-
# country object.  DISTINCT collapses a curve_family that spans
# many tenors / instruments down to its (country, currency)
# identity; multiple distinct rows surface as a controlled error.
# Mirrors the helper in real_yield_curve_spread.compute but runs on
# BOTH curve_families (since this primitive is cross-curve).

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
        the current universe but treated as a controlled error if
        it ever shows up — never silently picked).
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
                "cross-country / both-legs-must-be-linker invariant "
                "cannot be evaluated without the (country, "
                "currency) identity for the curve.  Verify the "
                "curve family is spelled correctly and is ingested "
                f"with instrument_type='{instrument_type}' (see "
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
                f"observed: {observed}.  This tool's cross-country "
                "/ both-legs-must-be-linker invariant requires a "
                "single (country, currency) identity per "
                "(curve_family, instrument_type) pair and refuses "
                "to pick one silently."
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
                "Both fields must be populated for the cross-"
                "country invariant to be evaluable."
            ),
        )
    return country, currency, None


def _enforce_linker_identity_both_legs(
    engine: Engine,
    *,
    first_curve_family: str,
    second_curve_family: str,
) -> tuple[
    Optional[str], Optional[str],
    Optional[str], Optional[str],
    Optional[str],
]:
    """Look up the ``(country, currency)`` identities of BOTH
    requested linker curve_families and refuse the request if
    either lookup fails or the curve_family does not resolve under
    ``instrument_type='inflation_linker'``.

    Returns ``(first_country, first_currency, second_country,
    second_currency, None)`` on a clean resolution (the caller
    proceeds to the compose path).  Returns five-tuple with the
    last element set to an error string otherwise — the caller
    wraps the error in the standard ``{"error": "..."}`` envelope
    with the offending leg named.
    """
    f_country, f_currency, f_err = _fetch_curve_family_country_currency(
        engine,
        first_curve_family,
        _LINKER_INSTRUMENT_TYPE,
    )
    if f_err is not None:
        return None, None, None, None, (
            "Could not resolve country/currency for the "
            f"first_curve_family='{first_curve_family}' "
            f"(instrument_type='{_LINKER_INSTRUMENT_TYPE}').  "
            f"{f_err}  If you passed a nominal sovereign "
            "curve_family (e.g. 'UST', 'DE_BUND'), use the "
            "sovereign_bonds calculate_cross_market_spread_tool "
            "instead — this tool only operates on inflation-"
            "linker rows on BOTH legs."
        )
    s_country, s_currency, s_err = _fetch_curve_family_country_currency(
        engine,
        second_curve_family,
        _LINKER_INSTRUMENT_TYPE,
    )
    if s_err is not None:
        return None, None, None, None, (
            "Could not resolve country/currency for the "
            f"second_curve_family='{second_curve_family}' "
            f"(instrument_type='{_LINKER_INSTRUMENT_TYPE}').  "
            f"{s_err}  If you passed a nominal sovereign "
            "curve_family (e.g. 'UST', 'DE_BUND'), use the "
            "sovereign_bonds calculate_cross_market_spread_tool "
            "instead — this tool only operates on inflation-"
            "linker rows on BOTH legs."
        )
    return f_country, f_currency, s_country, s_currency, None


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_cross_country_real_yield_spread_simple(
    engine: Engine,
    params: CrossCountryRealYieldSpreadSimpleInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Calculate the same-tenor cross-country linker real-yield
    differential between two linker curves' real-yield levels at
    the same tenor.

        spread_pct = first_curve_real_yield_pct - second_curve_real_yield_pct

    Sign convention is first_curve_family minus
    second_curve_family — fixed.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : CrossCountryRealYieldSpreadSimpleInput
        Validated input.  ``field_name=None`` resolves against the
        YAML's ``default_field_name`` convention.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass
        a custom ToolConfig to exercise convention overrides;
        production wrappers must pass ``config=`` explicitly.

    Returns
    -------
    dict
        Serialised ``CrossCountryRealYieldSpreadSimpleOutput``, or
        ``{"error": "..."}`` on recoverable failure (with a leg-
        attributed prefix for inner-call failures).
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
    # Both-legs-must-be-linker identity guard (post-fetch
    # instrument_master re-assertion).  Mirrors the sibling
    # real_yield_curve_spread guard but runs on BOTH curve_families.
    # Fires BEFORE any market-data SELECT — a non-linker
    # curve_family (e.g. 'UST') on either leg returns its
    # controlled error envelope here without firing any of the
    # level primitive's fetches.
    # ------------------------------------------------------------------
    (
        first_country, first_currency,
        second_country, second_currency,
        identity_err,
    ) = _enforce_linker_identity_both_legs(
        engine,
        first_curve_family=params.first_curve_family,
        second_curve_family=params.second_curve_family,
    )
    if identity_err is not None:
        return {"error": identity_err}

    # ------------------------------------------------------------------
    # Year fraction for display.  The Pydantic validator already
    # enforced cross-curve invariance; tenor parsing is delegated
    # to the inner level calls (which use the same shared parser).
    # If the tenor is unparsable, the inner level calls will
    # surface a controlled error envelope from their own fetch
    # path; we still compute it here for the display field.
    # ------------------------------------------------------------------
    try:
        tenor_years = tenor_to_years(params.tenor)
    except ValueError as exc:
        return {
            "error": (
                f"Cross-country real-yield spread could not be "
                f"computed for {params.first_curve_family} vs "
                f"{params.second_curve_family} @ {params.tenor}: "
                f"tenor could not be parsed ({exc}).  Accepted "
                "forms are '<n>W', '<n>M', '<n>Y'."
            )
        }

    # ------------------------------------------------------------------
    # Extended inner lookback.  Each inner level call trims its
    # time_series to ``lookback_days`` worth of display history.
    # We need a longer window so OUR rolling stats are fully
    # populated from the first row of OUR display window; match
    # the buffer math the level primitive uses internally so we
    # don't double-buffer.
    # ------------------------------------------------------------------
    buffer_calendar_days = int(
        max(z_window, trailing_window) * buffer_multiplier
    )
    extended_lookback_days = params.lookback_days + buffer_calendar_days

    # ------------------------------------------------------------------
    # Compose: call the linker real-yield-level primitive once per
    # curve at the shared tenor.  This inherits the no-proxy guard
    # (instrument_type='inflation_linker' filter on the DB read)
    # for free.  We pass ``config=`` so the inner conventions use
    # the same values this primitive uses; cross-config lint
    # enforces value-agreement on the bundled YAMLs, and any test
    # override on this primitive's ToolConfig flows through to the
    # inner level calls.
    # ------------------------------------------------------------------
    first_inner = get_real_yield_level(
        engine=engine,
        params=RealYieldLevelInput(
            curve_family=params.first_curve_family,
            tenor=params.tenor,
            lookback_days=extended_lookback_days,
            field_name=params.field_name,
            as_of_date=params.as_of_date,
        ),
        config=config,
    )
    if "error" in first_inner:
        return {
            "error": (
                f"Cross-country real-yield spread "
                f"({params.first_curve_family} vs "
                f"{params.second_curve_family} @ {params.tenor}) "
                f"could not be computed: first_curve leg failed: "
                f"{first_inner['error']}"
            )
        }

    second_inner = get_real_yield_level(
        engine=engine,
        params=RealYieldLevelInput(
            curve_family=params.second_curve_family,
            tenor=params.tenor,
            lookback_days=extended_lookback_days,
            field_name=params.field_name,
            as_of_date=params.as_of_date,
        ),
        config=config,
    )
    if "error" in second_inner:
        return {
            "error": (
                f"Cross-country real-yield spread "
                f"({params.first_curve_family} vs "
                f"{params.second_curve_family} @ {params.tenor}) "
                f"could not be computed: second_curve leg failed: "
                f"{second_inner['error']}"
            )
        }

    # ------------------------------------------------------------------
    # Align endpoint real-yield series by date.  The level
    # primitive's canonical TimeSeries already has each row pre-
    # rounded to ``yield_round_decimals`` from the inner compute;
    # we use those rows directly so the canonical-shape contract
    # is end-to-end.  Strict pandas inner-join on date so we only
    # emit rows where BOTH curves had data — no synthetic spread
    # points are produced on dates where one curve has no data.
    # ------------------------------------------------------------------
    first_rows = first_inner.get("time_series", {}).get("rows", [])
    second_rows = second_inner.get("time_series", {}).get("rows", [])
    if not first_rows or not second_rows:
        return {
            "error": (
                f"Cross-country real-yield spread "
                f"({params.first_curve_family} vs "
                f"{params.second_curve_family} @ {params.tenor}) "
                f"could not be computed: at least one curve "
                "returned no canonical TimeSeries rows.  This is "
                "a bug — the level primitive returned a snapshot "
                "but no history.  Please report with the curve "
                "pair and tenor."
            )
        }

    first_df = pd.DataFrame(first_rows).rename(
        columns={"value": "first_curve_real_yield_pct"},
    )
    second_df = pd.DataFrame(second_rows).rename(
        columns={"value": "second_curve_real_yield_pct"},
    )
    first_df["date"] = pd.to_datetime(first_df["date"])
    second_df["date"] = pd.to_datetime(second_df["date"])

    wide = (
        pd.merge(first_df, second_df, on="date", how="inner")
        .sort_values("date")
        .set_index("date")
    )

    # Drop any rows where either leg's value is NaN — strict
    # inner-join discipline; no synthetic spread on partial data.
    wide = wide.dropna(
        subset=[
            "first_curve_real_yield_pct",
            "second_curve_real_yield_pct",
        ],
    )

    if wide.empty:
        return {
            "error": (
                f"After aligning the two endpoint real-yield "
                f"series ({params.first_curve_family} vs "
                f"{params.second_curve_family} @ {params.tenor}), "
                "no overlapping dates remain.  The two linker "
                "curves may not have a common date history at "
                "this tenor — verify both curves publish at the "
                "requested tenor."
            )
        }

    # ------------------------------------------------------------------
    # Spread formula:  spread_pct = first_pct - second_pct
    # The endpoint real-yield series come in already rounded to
    # yield_round_decimals (the level primitive's canonical
    # TimeSeries applies the rounding upstream), so we round the
    # spread once at the end of this arithmetic.  Same boundary-
    # rounding discipline as the sibling curve-spread primitives.
    # ------------------------------------------------------------------
    wide["spread_pct"] = (
        wide["first_curve_real_yield_pct"]
        - wide["second_curve_real_yield_pct"]
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
    # real_yield_curve_spread / cross_country_breakeven_spread_simple /
    # OIS rate_level.
    # ------------------------------------------------------------------
    as_of_ts = wide.index[-1]
    cutoff = pd.Timestamp(
        as_of_ts.date() - timedelta(days=params.lookback_days),
    )
    display_df = wide.loc[wide.index >= cutoff].copy()

    if display_df.empty:
        return {
            "error": (
                f"No cross-country real-yield spread observations "
                f"within the last {params.lookback_days} days for "
                f"{params.first_curve_family} vs "
                f"{params.second_curve_family} @ {params.tenor}."
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

    # Trailing high/low/percentile of the cross-country real-yield
    # spread (percent scale).
    high, low, percentile = trailing_high_low_percentile(
        spreads, window=trailing_window, decimals=yield_round_decimals,
    )

    spread_label = (
        f"{params.first_curve_family} - "
        f"{params.second_curve_family} "
        f"{params.tenor} XC real-yield"
    )

    metrics = CrossCountryRealYieldSpreadSimpleCurrentMetrics(
        as_of_date=as_of_ts.strftime("%Y-%m-%d"),
        first_curve_family=params.first_curve_family,
        second_curve_family=params.second_curve_family,
        tenor=params.tenor,
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
        first_curve_real_yield_pct=safe_float(
            latest.get("first_curve_real_yield_pct"),
            decimals=yield_round_decimals,
        ),
        second_curve_real_yield_pct=safe_float(
            latest.get("second_curve_real_yield_pct"),
            decimals=yield_round_decimals,
        ),
        tenor_years=round(tenor_years, window_years_round),
        first_curve_country=first_country,
        first_curve_currency=first_currency,
        second_curve_country=second_country,
        second_curve_currency=second_currency,
        methodology_label=methodology_label,
    )

    # ------------------------------------------------------------------
    # Build time_series (bespoke shape) AND the two canonical
    # TimeSeries (spread in PERCENT, z-score in Z_SCORE).  All
    # three share the same display_df rows so they cannot drift.
    # ------------------------------------------------------------------
    ts_rows = [
        CrossCountryRealYieldSpreadSimpleTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            spread_pct=round(row.spread_pct, yield_round_decimals),
            z_score=safe_float(row.z_score, decimals=z_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    canonical_spread = _build_canonical_spread_series(
        display_df,
        first_curve_family=params.first_curve_family,
        second_curve_family=params.second_curve_family,
        tenor=params.tenor,
        yield_round_decimals=yield_round_decimals,
        first_country=first_country,
        first_currency=first_currency,
        second_country=second_country,
        second_currency=second_currency,
    )
    canonical_zscore = _build_canonical_zscore_series(
        display_df,
        first_curve_family=params.first_curve_family,
        second_curve_family=params.second_curve_family,
        tenor=params.tenor,
        z_round_decimals=z_round_decimals,
    )

    output = CrossCountryRealYieldSpreadSimpleOutput(
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
    first_curve_family: str,
    second_curve_family: str,
    tenor: str,
) -> str:
    """Lower-snake-case slug used as the canonical TimeSeries
    series_name prefix.  Pattern:
    ``<first>_<second>_<tenor>``.
    """
    return (
        f"{first_curve_family.lower()}_"
        f"{second_curve_family.lower()}_"
        f"{tenor.lower()}"
    )


def _build_canonical_spread_series(
    display_df: pd.DataFrame,
    *,
    first_curve_family: str,
    second_curve_family: str,
    tenor: str,
    yield_round_decimals: int,
    first_country: str,
    first_currency: str,
    second_country: str,
    second_currency: str,
) -> TimeSeries:
    """Convert the display DataFrame's spread_pct column into the
    canonical ``TimeSeries`` shape (closed-enum PERCENT units).

    Naming convention:
    ``<first>_<second>_<tenor>_xc_real_yield_spread``.

    The description embeds the literal
    ``first_curve_real_yield_pct - second_curve_real_yield_pct``
    sign-convention token AND the index-family / market-structure
    caveats so downstream consumers cannot reinterpret the sign or
    forget the caveats.
    """
    slug = _series_name_slug(
        first_curve_family=first_curve_family,
        second_curve_family=second_curve_family,
        tenor=tenor,
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=round(float(row.spread_pct), yield_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=f"{slug}_xc_real_yield_spread",
        units=TimeSeriesUnits.PERCENT,
        description=(
            f"Cross-country linker real-yield spread "
            f"({first_curve_family} - {second_curve_family}) at "
            f"{tenor} (first_curve_real_yield_pct - "
            f"second_curve_real_yield_pct) over the displayed "
            f"window.  Cross-country real-rate differential for "
            f"({first_country}, {first_currency}) vs "
            f"({second_country}, {second_currency}) linkers; "
            "distinct from a cross-country breakeven differential "
            "(inflation compensation) and from a sovereign nominal "
            "cross-market spread.  Quoted in PERCENT (same units "
            "as the underlying real yields).  Subject to index-"
            "family mismatch caveat (CPI-U vs HICP vs RPI vs "
            "CAN_CPI) AND market-structure mismatch caveat (cross-"
            "country linker-liquidity / issuance-size differences) "
            "— see the tool's methodology disclosure."
        ),
        rows=rows,
    )


def _build_canonical_zscore_series(
    display_df: pd.DataFrame,
    *,
    first_curve_family: str,
    second_curve_family: str,
    tenor: str,
    z_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's z_score column into the
    canonical ``TimeSeries`` shape (closed-enum Z_SCORE units).

    Naming convention:
    ``<first>_<second>_<tenor>_xc_real_yield_spread_zscore``.
    Values match ``time_series[i].z_score`` 1-to-1 (rounded via
    the same ``z_score_round_decimals`` convention applied to the
    bespoke field) so the two series cannot drift.
    """
    slug = _series_name_slug(
        first_curve_family=first_curve_family,
        second_curve_family=second_curve_family,
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
        series_name=f"{slug}_xc_real_yield_spread_zscore",
        units=TimeSeriesUnits.Z_SCORE,
        description=(
            f"Rolling z-score of the cross-country linker real-"
            f"yield spread ({first_curve_family} - "
            f"{second_curve_family}) at {tenor} vs its own "
            "trailing window."
        ),
        rows=rows,
    )
