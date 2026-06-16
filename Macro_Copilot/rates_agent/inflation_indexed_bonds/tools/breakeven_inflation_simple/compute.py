"""
compute.py — Config-driven generic bond-implied breakeven inflation
====================================================================

Second tool in the ``inflation_indexed_bonds`` domain.  Owns the desk
concept of a generic bond-implied breakeven inflation:

    breakeven_pct = nominal_yield_pct - real_yield_pct
    breakeven_bps = breakeven_pct * 100

The math is structurally identical to a sovereign cross-market
spread; the difference is that one leg is a nominal sovereign series
and the other is a linker real-yield series.  That distinction is
the load-bearing reason this primitive exists — see the no-proxy
guard below.

Concept honesty
---------------
This tool returns INFLATION COMPENSATION, NOT pure expected
inflation.  The differential between a nominal and real yield carries
an inflation risk premium and a relative liquidity premium.  The
honesty disclosure lives in the YAML's
``methodology.what_it_does`` and is threaded onto the wire via
``current_metrics.methodology_label``.  That field is sourced from the
YAML at runtime — NOT hardcoded as a Python literal — so a YAML edit
flows through to runtime behaviour.

Phase-1 methodology-exposure surface
------------------------------------
Per ``docs_revamped/03_standards/methodology_exposure.md`` (Phase 1
of the ``revamp`` branch), the same four conventions the sibling
linker real_yield_level tool exposes are exposed here as Pydantic
Input fields with per-call overrides:

    z_score_window_days   z_score_min_periods   z_score_ddof
    field_name (overrides default_field_name)

All four follow the None-sentinel / YAML-fallthrough pattern: when
the caller supplies None (the schema default), ``compute()`` resolves
against the YAML default; when set, the explicit value overrides per
call.  ``_conventions_from_config(config, params)`` is the one place
where the rolling-z-score resolution happens.  The remaining
conventions stay YAML-locked.  See each convention's ``exposure:``
block in ``config.yaml`` for per-decision Criterion-A / Criterion-B
rationale.

Fetch shape
-----------
We reuse ``shared.analytics.rates_fetch.fetch_single_tenor`` twice —
once for the linker leg with
``instrument_type='inflation_linker'`` and once for the nominal leg
with ``instrument_type='sovereign_benchmark'``.  Each leg returns a
long-format ``[trade_date, field_value]`` frame; we tag them with a
``curve_family`` column and feed the concatenated long frame to the
existing ``shared.analytics.spreads.pivot_and_align_tenors`` shape
keyed by ``curve_family``.  This reuses the canonical alignment
primitive every cross_market_spread caller exercises — no new helper
in shared/, no new SQL surface, no risk of drifting from the existing
two-leg alignment behaviour.

No-proxy guard (DESIGN_PRINCIPLES.md §1, §3 / STANDARD_TOOL_AND_YAML_RULES.md §J)
--------------------------------------------------------------------------------
The discriminator on the DB read is the load-bearing guarantee that
this primitive cannot return a proxy:

  - Linker leg: ``instrument_type='inflation_linker'``.  Without this,
    a caller passing a nominal curve_family in the linker slot would
    get nominal yields under a real-yield label.
  - Nominal leg: ``instrument_type='sovereign_benchmark'``.  Without
    this, a caller passing a linker curve_family in the nominal slot
    would get linker real yields under a nominal-yield label and the
    breakeven would silently collapse to ~zero.

Both filters are enforced in code (NOT YAML).  Putting them in YAML
would let a config edit silently switch the tool to a proxy.  Per
DESIGN_PRINCIPLES.md §5, structural identity stays in code.

Either filter producing zero rows yields a controlled
``{"error": "..."}`` envelope — the same shape every other rates tool
uses for recoverable failures.

Same-country invariant (DESIGN_PRINCIPLES.md §5 / STANDARD_TOOL_AND_YAML_RULES.md §H)
-------------------------------------------------------------------------------------
A bond-implied breakeven is by construction a *same-country* object —
the nominal sovereign yield and the linker real yield must come from
the same issuer (so they share inflation regime, sovereign credit,
and tax/legal treatment).  The two legs MUST share both ``country``
and ``currency`` as stored in ``macro_data.instrument_master``.

Examples of valid pairs (each is one row in the universe):
``UST + USD_TIPS`` (US/USD), ``UK_GILT + GBP_LINKER`` (UK/GBP),
``FR_OAT + EUR_FR_LINKER`` (France/EUR),
``CANADA_GOVT + CAD_RRB`` (Canada/CAD).

Cross-country pairs are refused at compute time with a controlled
``{"error": ...}`` envelope.  The load-bearing case is the EUR-zone:
``DE_BUND`` (Germany/EUR) and ``EUR_FR_LINKER`` (France/EUR) share a
currency but NOT a country — the differential of two EUR sovereigns
is NOT a generic breakeven, it is a sovereign-credit / inflation-
regime hybrid we refuse to label as breakeven inflation.

This guard is structural (mathematical truth, not a methodology
knob) so it lives in CODE — there is intentionally no YAML allow-list
of approved country pairs.  A YAML allow-list would let a config edit
silently turn the tool back into a proxy.

The guard fires before either ``fetch_single_tenor`` call: if either
leg has no ``instrument_master`` row at all, or has multiple distinct
``(country, currency)`` rows for that ``(curve_family,
instrument_type)`` pair, OR the two legs disagree on country /
currency, the tool returns ``{"error": ...}`` and never issues the
market-data SELECTs.

Trailing-range wire freeze
--------------------------
``trailing_range_window_days`` is locked at 252 in V1.  The output
schema's field names (``high_252d_bps``, ``low_252d_bps``,
``percentile_252d``) embed the number; changing the convention without
renaming the wire fields would silently lie about what the percentile
is computed against.  ``compute()`` raises ``NotImplementedError`` if
this is set to anything else; see ``methodology.planned_extensions``
in the YAML for the path to making it configurable.

Latest-observation cutoff anchoring
-----------------------------------
The displayed window cutoff is anchored to the data's latest aligned
``trade_date`` (matching OIS rate_level and linker real_yield_level),
NOT ``date.today()``.  Linker daily feeds can lag wall-clock by
several business days; anchoring to today would silently shrink the
displayed window when data is stale.  Sovereign cross_market_spread
anchors to date.today(); the inconsistency is documented in
cross_market_spread's planned_extensions and will be reconciled in a
separate PR.

Test seam
---------
``fetch_single_tenor`` and ``date`` are imported here at module
level; tests patch them via
``patch("rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.X")``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.schemas import (
    BreakevenInflationSimpleCurrentMetrics,
    BreakevenInflationSimpleInput,
    BreakevenInflationSimpleOutput,
    BreakevenInflationSimpleTimeSeriesRow,
)
from shared.analytics.levels import (
    period_changes,
    trailing_high_low_percentile,
)
from shared.analytics.rates_fetch import fetch_single_tenor, latest_trade_date
from shared.analytics.spreads import (
    compute_spread_bps,
    pivot_and_align_tenors,
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


# instrument_type discriminators.  Code-level invariants — NOT YAML
# knobs — per DESIGN_PRINCIPLES.md §5 and the no-proxy guard
# rationale in the module docstring.
_LINKER_INSTRUMENT_TYPE: str = "inflation_linker"
_NOMINAL_INSTRUMENT_TYPE: str = "sovereign_benchmark"


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(
    config: ToolConfig,
    params: Optional[BreakevenInflationSimpleInput] = None,
) -> dict:
    """Pull the methodology kwargs the breakeven compute() needs from a
    ToolConfig, applying any caller overrides from ``params``.

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

    The remaining YAML-locked conventions (period offsets, buffer,
    ffill, trailing range, rounding) are read straight from the config
    regardless of ``params`` — no override path.

    Raises NotImplementedError if ``trailing_range_window_days`` is set
    to anything other than 252 — see the wire-freeze rationale in the
    module docstring.

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
            f"field names (high_252d_bps, low_252d_bps, percentile_252d) "
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
        "bps_round_decimals": config.convention_value("bps_round_decimals"),
        "yield_round_decimals": config.convention_value("yield_round_decimals"),
    }


# ============================================================================
# SAME-COUNTRY INVARIANT GUARD
# ============================================================================

# Read-only SELECT against the instrument_master table.  Used purely
# to enforce the same-country / same-currency invariant before the
# market-data fetch fires.  DISTINCT collapses a curve_family that
# spans many tenors / instruments down to its (country, currency)
# identity; multiple distinct rows surface as a controlled error.
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
      - ``(None, None, "<no rows>")`` when the pair is unknown to the
        instrument master,
      - ``(None, None, "<multiple distinct (country, currency) rows>")``
        when the pair maps to more than one identity (impossible in
        the current universe but treated as a controlled error if it
        ever shows up — never silently picked).

    Same-country alignment uses BOTH ``country`` and ``currency``.
    They are redundant for most families today, but the EUR-zone case
    is the load-bearing distinction: DE_BUND (Germany/EUR) and
    EUR_FR_LINKER (France/EUR) share a currency but are different
    countries, and that pair is NOT a generic breakeven.
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
                "same-country invariant cannot be evaluated without "
                "the (country, currency) identity for this leg.  "
                "Verify the curve family is spelled correctly and is "
                "ingested with the expected instrument_type (see "
                "rates_agent/playbooks/sovereign_bonds.yml and "
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
                "invariant requires a single (country, currency) "
                "identity per (curve_family, instrument_type) pair "
                "and refuses to pick one silently."
            ),
        )
    only = rows[0]
    return only["country"], only["currency"], None


def _enforce_same_country_invariant(
    engine: Engine,
    *,
    nominal_curve_family: str,
    linker_curve_family: str,
) -> Optional[str]:
    """Look up the ``(country, currency)`` of each leg and refuse the
    request if either lookup fails or the two legs disagree.

    Returns ``None`` on a clean same-country pair (the caller proceeds
    to the fetch).  Returns a precise human error string otherwise —
    the caller wraps it in the standard ``{"error": "..."}`` envelope.
    """
    nominal_country, nominal_currency, nominal_err = (
        _fetch_curve_family_country_currency(
            engine,
            nominal_curve_family,
            _NOMINAL_INSTRUMENT_TYPE,
        )
    )
    if nominal_err is not None:
        return (
            "Could not resolve country/currency for the nominal leg "
            f"(curve_family='{nominal_curve_family}', "
            f"instrument_type='{_NOMINAL_INSTRUMENT_TYPE}').  "
            f"{nominal_err}"
        )

    linker_country, linker_currency, linker_err = (
        _fetch_curve_family_country_currency(
            engine,
            linker_curve_family,
            _LINKER_INSTRUMENT_TYPE,
        )
    )
    if linker_err is not None:
        return (
            "Could not resolve country/currency for the linker leg "
            f"(curve_family='{linker_curve_family}', "
            f"instrument_type='{_LINKER_INSTRUMENT_TYPE}').  "
            f"{linker_err}"
        )

    if (
        nominal_country != linker_country
        or nominal_currency != linker_currency
    ):
        return (
            "Cross-country pair refused: nominal "
            f"'{nominal_curve_family}' is "
            f"({nominal_country!r}, {nominal_currency!r}) while "
            f"linker '{linker_curve_family}' is "
            f"({linker_country!r}, {linker_currency!r}).  This "
            "primitive is a *same-country* generic bond-implied "
            "breakeven inflation by construction (the nominal "
            "sovereign and linker legs must share both country and "
            "currency, e.g. UST + USD_TIPS, UK_GILT + GBP_LINKER, "
            "FR_OAT + EUR_FR_LINKER, CANADA_GOVT + CAD_RRB).  A "
            "cross-country differential is a sovereign-credit / "
            "inflation-regime hybrid, not a generic breakeven, and "
            "is refused at compute time by design.  For cross-"
            "country sovereign comparisons use the existing "
            "cross_market_spread tool (or a future cross-country "
            "breakeven primitive when one ships) — this tool will "
            "not silently fall through."
        )
    return None


# ============================================================================
# TWO-LEG FETCH (long-format frame keyed by curve_family)
# ============================================================================

def _fetch_two_legs(
    engine: Engine,
    *,
    nominal_curve_family: str,
    linker_curve_family: str,
    tenor: str,
    field_name: str,
    start_date: date,
    end_date: Optional[date] = None,
) -> tuple[pd.DataFrame, str | None]:
    """Fetch the nominal-leg and linker-leg single-tenor series, each
    with its own instrument_type filter, and return a single
    long-format ``[trade_date, curve_family, field_value]`` frame.

    Returns ``(combined_df, error_message)``.  ``error_message`` is
    None on success; on failure, the empty/sentinel DataFrame is
    returned with a controlled error string the caller wraps in a
    ``{"error": "..."}`` envelope.

    Either leg returning zero rows is a controlled failure — see the
    no-proxy guard rationale in the module docstring.

    ``end_date`` (optional, default ``None``): historical as-of UPPER
    bound, threaded into both legs' ``fetch_single_tenor`` calls.
    ``None`` returns each leg's frame unchanged byte-for-byte (the
    pre-as-of default).  The compute path passes ``end_date=anchor``;
    when the caller does not supply ``as_of_date`` the anchor is the
    latest available ``trade_date``, so the cap drops zero rows.
    """
    linker_df = fetch_single_tenor(
        engine=engine,
        curve_family=linker_curve_family,
        tenor=tenor,
        field_name=field_name,
        start_date=start_date,
        instrument_type=_LINKER_INSTRUMENT_TYPE,
        end_date=end_date,
    )
    if linker_df.empty:
        msg = (
            f"No linker (instrument_type='{_LINKER_INSTRUMENT_TYPE}') "
            f"rows found for curve_family='{linker_curve_family}', "
            f"tenor='{tenor}', field='{field_name}' since "
            f"{start_date.isoformat()}.  If you passed a nominal "
            "sovereign curve_family in the linker slot (e.g. 'UST', "
            "'DE_BUND'), correct the argument order — this tool's "
            "linker leg is filtered honestly and will not silently "
            "fall through to nominal data.  Otherwise verify the "
            "linker curve family and tenor exist in the database "
            "(see rates_agent/playbooks/inflation_indexed_bonds.yml "
            "for the ingested universe)."
        )
        return pd.DataFrame(columns=["trade_date", "curve_family", "field_value"]), msg

    nominal_df = fetch_single_tenor(
        engine=engine,
        curve_family=nominal_curve_family,
        tenor=tenor,
        field_name=field_name,
        start_date=start_date,
        instrument_type=_NOMINAL_INSTRUMENT_TYPE,
        end_date=end_date,
    )
    if nominal_df.empty:
        msg = (
            f"No nominal sovereign "
            f"(instrument_type='{_NOMINAL_INSTRUMENT_TYPE}') rows "
            f"found for curve_family='{nominal_curve_family}', "
            f"tenor='{tenor}', field='{field_name}' since "
            f"{start_date.isoformat()}.  If you passed a linker "
            "curve_family in the nominal slot (e.g. 'USD_TIPS', "
            "'GBP_LINKER'), correct the argument order — this tool's "
            "nominal leg is filtered honestly with "
            f"instrument_type='{_NOMINAL_INSTRUMENT_TYPE}' and will "
            "not silently fall through to linker data (which would "
            "collapse the breakeven to ~zero).  Otherwise verify the "
            "nominal curve family and tenor exist in the database "
            "(see rates_agent/playbooks/sovereign_bonds.yml for the "
            "ingested universe)."
        )
        return pd.DataFrame(columns=["trade_date", "curve_family", "field_value"]), msg

    linker_df = linker_df.assign(curve_family=linker_curve_family)
    nominal_df = nominal_df.assign(curve_family=nominal_curve_family)
    combined = pd.concat([nominal_df, linker_df], ignore_index=True)
    combined = combined[["trade_date", "curve_family", "field_value"]]
    return combined, None


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_breakeven_inflation_simple(
    engine: Engine,
    params: BreakevenInflationSimpleInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Calculate the generic bond-implied breakeven inflation between a
    nominal sovereign curve and the corresponding linker curve at the
    same tenor.

        breakeven_pct = nominal_yield_pct - real_yield_pct
        breakeven_bps = breakeven_pct * 100

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : BreakevenInflationSimpleInput
        Validated input.  ``field_name=None`` resolves against the
        YAML's ``default_field_name`` convention.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides; production
        wrappers must pass ``config=`` explicitly.

    Returns
    -------
    dict
        Serialised ``BreakevenInflationSimpleOutput``, or
        ``{"error": "..."}`` on recoverable failure.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # Pull conventions.  Applies any per-call Input overrides for the
    # exposed rolling-z-score surface (z_score_window_days /
    # z_score_min_periods / z_score_ddof); see
    # _conventions_from_config + config.yaml exposure blocks.
    # ------------------------------------------------------------------
    conv = _conventions_from_config(config, params)
    z_window = conv["z_window"]
    z_min_periods = conv["z_min_periods"]
    z_ddof = conv["z_ddof"]
    z_round_decimals = conv["z_round_decimals"]
    buffer_multiplier = conv["buffer_multiplier"]
    ffill_limit = conv["ffill_limit"]
    period_offsets = conv["period_offsets"]
    trailing_window = conv["trailing_window"]
    bps_round_decimals = conv["bps_round_decimals"]
    yield_round_decimals = conv["yield_round_decimals"]
    default_field_name = config.convention_value("default_field_name")

    # Resolve field_name: caller's explicit value wins; None falls
    # through to the YAML default.
    field_name_resolved = (
        params.field_name if params.field_name is not None else default_field_name
    )

    # Wire-honesty disclosure threaded from YAML — NOT hardcoded.
    methodology_label = config.methodology.what_it_does.strip()

    # ------------------------------------------------------------------
    # 1. Date window (defensive: buffer against the *larger* of the
    #    z-window and trailing-window so neither stat starves on the
    #    first displayed row, even if a future config decouples them)
    # ------------------------------------------------------------------
    buffer_calendar_days = int(max(z_window, trailing_window) * buffer_multiplier)
    # Anchor the fetch window to the latest available trade_date (not
    # date.today()) so a short lookback still resolves to real data when
    # the linker / nominal feeds lag wall-clock; falls back to today only
    # when the filtered universe is empty.  Filter on the shared tenor +
    # field both legs fetch at — the most-recent of the two legs' last
    # trade_date is the right floor (the pivot/align step intersects
    # them anyway).  The display cutoff below stays anchored to the
    # data's last aligned trade_date (see step 5).
    anchor = (
        params.as_of_date
        or latest_trade_date(
            engine,
            tenor=params.tenor,
            field_name=field_name_resolved,
        )
        or date.today()
    )
    start_date = anchor - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 1b. Same-country / same-currency invariant.  A bond-implied
    #     breakeven is by construction a same-country object — refuse
    #     cross-country pairs (e.g. DE_BUND vs EUR_FR_LINKER, or
    #     UK_GILT vs USD_TIPS) BEFORE any market-data SELECT fires.
    #     Structural / mathematical-truth invariant — lives in code,
    #     not YAML, per DESIGN_PRINCIPLES.md §5 and
    #     STANDARD_TOOL_AND_YAML_RULES.md §H.
    # ------------------------------------------------------------------
    same_country_error = _enforce_same_country_invariant(
        engine,
        nominal_curve_family=params.nominal_curve_family,
        linker_curve_family=params.linker_curve_family,
    )
    if same_country_error is not None:
        return {"error": same_country_error}

    # ------------------------------------------------------------------
    # 2. Fetch both legs honestly — each with its own instrument_type
    #    discriminator.  Either leg empty → controlled error envelope.
    # ------------------------------------------------------------------
    raw_df, fetch_error = _fetch_two_legs(
        engine=engine,
        nominal_curve_family=params.nominal_curve_family,
        linker_curve_family=params.linker_curve_family,
        tenor=params.tenor,
        field_name=field_name_resolved,
        start_date=start_date,
        end_date=anchor,
    )
    if fetch_error is not None:
        return {"error": fetch_error}

    # ------------------------------------------------------------------
    # 3. Pivot → wide format (date × curve_family) and align holiday
    # gaps using the same canonical alignment primitive
    # cross_market_spread uses.
    # ------------------------------------------------------------------
    wide = pivot_and_align_tenors(
        raw_df,
        required_tenors=(
            params.nominal_curve_family,
            params.linker_curve_family,
        ),
        key_col="curve_family",
        ffill_limit=ffill_limit,
    )

    if wide.empty:
        return {
            "error": (
                f"After aligning dates for nominal "
                f"'{params.nominal_curve_family}' and linker "
                f"'{params.linker_curve_family}' at "
                f"{params.tenor}, no overlapping observations "
                "remain.  The two legs may not have a common date "
                "history at this tenor — verify both series exist in "
                "the database for the requested window."
            )
        }

    # ------------------------------------------------------------------
    # 4. Compute breakeven (bps) and rolling z-score.  The breakeven
    #    is structurally a cross-market spread: nominal − linker, *100.
    # ------------------------------------------------------------------
    wide["breakeven_bps"] = compute_spread_bps(
        wide,
        minuend_col=params.nominal_curve_family,
        subtrahend_col=params.linker_curve_family,
        round_decimals=bps_round_decimals,
    )
    wide["z_score"] = rolling_zscore(
        wide["breakeven_bps"],
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_round_decimals,
    )

    # ------------------------------------------------------------------
    # 5. Trim to requested display lookback — anchored to the data's
    #    latest aligned trade_date (matching real_yield_level and OIS
    #    rate_level).  Sovereign cross_market_spread uses date.today();
    #    inconsistency documented in cross_market_spread's
    #    planned_extensions.
    # ------------------------------------------------------------------
    as_of_ts = wide.index[-1]
    cutoff = pd.Timestamp(as_of_ts.date() - timedelta(days=params.lookback_days))
    display_df = wide.loc[wide.index >= cutoff].copy()

    if display_df.empty:
        return {
            "error": (
                f"No observations within the last "
                f"{params.lookback_days} days for "
                f"'{params.nominal_curve_family}' vs "
                f"'{params.linker_curve_family}' at {params.tenor}."
            )
        }

    # ------------------------------------------------------------------
    # 6. Build current_metrics
    # ------------------------------------------------------------------
    latest = display_df.iloc[-1]
    breakevens = display_df["breakeven_bps"]

    current_breakeven_bps = safe_float(
        latest["breakeven_bps"], decimals=bps_round_decimals,
    )
    # breakeven_pct is the same number divided by 100; round at
    # yield-precision so it lines up with the underlying yield fields
    # on the wire.
    current_breakeven_pct = (
        round(float(latest["breakeven_bps"]) / 100.0, yield_round_decimals)
        if pd.notna(latest["breakeven_bps"]) else None
    )

    # Daily / weekly / monthly change in breakeven (bps).  The
    # breakeven series is ALREADY in bps, so already_bps=True → plain
    # subtraction.  decimals= passes the YAML convention down so
    # daily_change_bps etc. respect bps_round_decimals (not delta_bps's
    # hardcoded default of 2).
    changes = period_changes(
        breakevens,
        offsets=period_offsets,
        already_bps=True,
        decimals=bps_round_decimals,
    )

    # Trailing high/low/percentile of the breakeven (bps scale).
    high, low, percentile = trailing_high_low_percentile(
        breakevens, window=trailing_window, decimals=bps_round_decimals,
    )

    breakeven_label = (
        f"{params.nominal_curve_family}-"
        f"{params.linker_curve_family} {params.tenor} breakeven"
    )

    metrics = BreakevenInflationSimpleCurrentMetrics(
        as_of_date=as_of_ts.strftime("%Y-%m-%d"),
        nominal_curve_family=params.nominal_curve_family,
        linker_curve_family=params.linker_curve_family,
        tenor=params.tenor,
        breakeven_label=breakeven_label,
        breakeven_pct=current_breakeven_pct,
        breakeven_bps=current_breakeven_bps,
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
        nominal_yield_pct=safe_float(
            latest.get(params.nominal_curve_family),
            decimals=yield_round_decimals,
        ),
        real_yield_pct=safe_float(
            latest.get(params.linker_curve_family),
            decimals=yield_round_decimals,
        ),
        methodology_label=methodology_label,
    )

    # ------------------------------------------------------------------
    # 7. Build time_series (bespoke shape) AND the two canonical
    #    TimeSeries (time_series_breakeven for the BPS history,
    #    time_series_zscore for the Z_SCORE history).  All three
    #    share the same display_df rows so they cannot drift.
    # ------------------------------------------------------------------
    ts_rows = [
        BreakevenInflationSimpleTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            breakeven_bps=round(row.breakeven_bps, bps_round_decimals),
            z_score=safe_float(row.z_score, decimals=z_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    canonical_breakeven = _build_canonical_breakeven_series(
        display_df,
        nominal_curve_family=params.nominal_curve_family,
        linker_curve_family=params.linker_curve_family,
        tenor=params.tenor,
        bps_round_decimals=bps_round_decimals,
    )
    canonical_zscore = _build_canonical_breakeven_zscore_series(
        display_df,
        nominal_curve_family=params.nominal_curve_family,
        linker_curve_family=params.linker_curve_family,
        tenor=params.tenor,
        z_round_decimals=z_round_decimals,
    )

    output = BreakevenInflationSimpleOutput(
        current_metrics=metrics,
        time_series=ts_rows,
        time_series_breakeven=canonical_breakeven,
        time_series_zscore=canonical_zscore,
    )
    return output.model_dump()


# ============================================================================
# CANONICAL TIME-SERIES BUILDERS
# ============================================================================

def _build_canonical_breakeven_series(
    display_df: pd.DataFrame,
    *,
    nominal_curve_family: str,
    linker_curve_family: str,
    tenor: str,
    bps_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's breakeven_bps column into the
    canonical ``TimeSeries`` shape (closed-enum BPS units).

    Naming convention:
    ``<nominal_lower>_<linker_lower>_<tenor_lower>_breakeven``.
    """
    series_name = (
        f"{nominal_curve_family.lower()}_"
        f"{linker_curve_family.lower()}_"
        f"{tenor.lower()}_breakeven"
    )
    rows = [
        TimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            value=round(float(row.breakeven_bps), bps_round_decimals),
        )
        for row in display_df.itertuples()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.BPS,
        description=(
            f"Generic bond-implied breakeven inflation "
            f"({nominal_curve_family} − {linker_curve_family}) at "
            f"{tenor} over the displayed window.  Inflation "
            "compensation; not a clean expected-inflation read "
            "(carries inflation risk premium and liquidity premium)."
        ),
        rows=rows,
    )


def _build_canonical_breakeven_zscore_series(
    display_df: pd.DataFrame,
    *,
    nominal_curve_family: str,
    linker_curve_family: str,
    tenor: str,
    z_round_decimals: int,
) -> TimeSeries:
    """Convert the display DataFrame's z_score column into the canonical
    ``TimeSeries`` shape (closed-enum Z_SCORE units).

    Naming convention:
    ``<nominal_lower>_<linker_lower>_<tenor_lower>_zscore``.  Values
    match ``time_series[i].z_score`` 1-to-1 (rounded via the same
    ``z_score_round_decimals`` convention applied to the bespoke
    field) so the two series cannot drift.
    """
    series_name = (
        f"{nominal_curve_family.lower()}_"
        f"{linker_curve_family.lower()}_"
        f"{tenor.lower()}_zscore"
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
            f"Rolling z-score of the {nominal_curve_family}−"
            f"{linker_curve_family} bond-implied breakeven (in bps) "
            f"at {tenor} vs its own trailing window."
        ),
        rows=rows,
    )
