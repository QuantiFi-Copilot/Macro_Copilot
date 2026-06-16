"""Pydantic schemas for the cross_country_breakeven_spread_simple tool.

Fifth tool in the ``inflation_indexed_bonds`` domain.  Owns the desk
concept of a *same-tenor cross-country breakeven inflation
differential*: the difference between two countries' generic bond-
implied breakevens at the same tenor (e.g. US 10Y breakeven minus
EUR-FR 10Y breakeven).  Explicitly a CROSS-COUNTRY object — the
two country pairs MUST come from different sovereign issuers.  See
``methodology.what_it_does`` in the tool's ``config.yaml`` and the
``methodology_label`` field on
``CrossCountryBreakevenSpreadSimpleCurrentMetrics``.

Composition path
----------------
This primitive composes two calls to
``calculate_breakeven_inflation_simple`` (one per country), each
constrained to its own (nominal, linker) pair.  The spot
primitive's no-proxy guard (instrument_type filter on each leg) AND
its same-country / same-currency invariant (per leg) are inherited
transitively — both fire BEFORE any market-data SELECT on each leg.

Validation layering
-------------------
- ``field_name`` defaults to ``None`` — the sentinel that resolves
  against the YAML's ``default_field_name`` convention.  Mirrors
  the empty-string / None-sentinel pattern used by every other
  rates tool.

- ``lookback_days`` controls the displayed window only (the
  bespoke ``time_series`` row count and the canonical TimeSeries
  length).  Does NOT control the rolling z-score window or the
  trailing range window — those are independent conventions in
  ``config.yaml``.

- ``high_252d_bps`` / ``low_252d_bps`` / ``percentile_252d`` field
  names embed the trailing-range window length and are wire-frozen
  in V1.  See ``config.yaml``'s ``planned_extensions`` for the
  path to making the trailing window configurable.

Cross-field invariants stay here in code:
``_nominal_pairs_must_differ`` AND ``_linker_pairs_must_differ``
enforce the cross-country requirement at the schema layer (no DB
lookup required for these guards).  A caller passing identical
country_a / country_b pairs is rejected before any compute work
fires; for same-country breakeven curve / spot work, use the
dedicated same-country tools.

The *per-leg same-country / same-currency* invariant — i.e. that
the (nominal, linker) pair INSIDE each country leg must come from
the same sovereign issuer (e.g. country_a_nominal=UST +
country_a_linker=USD_TIPS, never UST + EUR_FR_LINKER) — is enforced
at compute time by the underlying
``breakeven_inflation_simple._enforce_same_country_invariant``
helper (it requires a DB lookup against
``macro_data.instrument_master``), NOT here in the Pydantic
validator.  See
``rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute._enforce_same_country_invariant``
for the guard.  Each composed inner call carries the same guard;
this primitive surfaces the inner controlled error envelope with a
leg-attributed prefix when violated.

Canonical TimeSeries output
---------------------------
This tool emits TWO canonical
``shared.schemas.time_series.TimeSeries`` payloads alongside the
bespoke ``time_series`` list:

  - ``time_series_spread`` (units = BPS) — the cross-country
    breakeven spread series over the displayed window.  series_name
    pattern: ``<an_lower>_<al_lower>_<bn_lower>_<bl_lower>_<tenor_lower>_xc_breakeven_spread``.

  - ``time_series_zscore`` (units = Z_SCORE) — the rolling z-score
    of the spread series over the displayed window.  series_name
    pattern: ``<an_lower>_<al_lower>_<bn_lower>_<bl_lower>_<tenor_lower>_xc_breakeven_spread_zscore``.

All three (bespoke + two canonical) share the same display
DataFrame so they cannot drift.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.schemas import TimeSeries


class CrossCountryBreakevenSpreadSimpleInput(BaseModel):
    """Parameters for computing a same-tenor cross-country breakeven
    inflation differential between two countries' generic bond-implied
    breakevens.

    spread_bps = breakeven_a_bps - breakeven_b_bps

    Cross-country invariant
    -----------------------
    country_a and country_b MUST be different sovereign issuers.  The
    Pydantic validators enforce
    ``country_a_nominal_pair != country_b_nominal_pair`` AND
    ``country_a_linker_pair != country_b_linker_pair`` at schema
    time (no DB lookup needed).  Same-country pairs are rejected
    before any compute work fires.

    Per-leg same-country invariant
    ------------------------------
    Inside each country leg, the (nominal, linker) pair MUST share
    country AND currency in ``macro_data.instrument_master`` (e.g.
    UST + USD_TIPS, FR_OAT + EUR_FR_LINKER).  This invariant is NOT
    re-implemented here — it is enforced by the underlying
    ``breakeven_inflation_simple`` primitive's
    ``_enforce_same_country_invariant`` helper which fires BEFORE
    any market-data SELECT on each leg.  Leg-internal cross-country
    pollution (e.g. country_a_nominal=UST,
    country_a_linker=EUR_FR_LINKER) yields a controlled error
    envelope that this tool surfaces with a leg-attributed prefix.
    """

    model_config = ConfigDict(extra="forbid")

    country_a_nominal_pair: str = Field(
        ...,
        min_length=1,
        description=(
            "Country A's nominal sovereign curve family identifier as "
            "stored in instrument_master.  Examples: 'UST' (US), "
            "'UK_GILT' (UK), 'FR_OAT' (France), 'DE_BUND' (Germany), "
            "'CANADA_GOVT' (Canada).  Must pair with "
            "``country_a_linker_pair`` under the same-country "
            "invariant enforced inside breakeven_inflation_simple."
        ),
    )
    country_a_linker_pair: str = Field(
        ...,
        min_length=1,
        description=(
            "Country A's sovereign linker curve family identifier as "
            "stored in instrument_master.  Examples: 'USD_TIPS' (US), "
            "'GBP_LINKER' (UK), 'EUR_FR_LINKER' (France), 'CAD_RRB' "
            "(Canada).  Must share country AND currency with "
            "``country_a_nominal_pair`` (the spot primitive's "
            "same-country invariant — enforced per leg)."
        ),
    )
    country_b_nominal_pair: str = Field(
        ...,
        min_length=1,
        description=(
            "Country B's nominal sovereign curve family identifier as "
            "stored in instrument_master.  Must be a DIFFERENT "
            "sovereign issuer from ``country_a_nominal_pair`` (this "
            "primitive is a cross-country object by construction)."
        ),
    )
    country_b_linker_pair: str = Field(
        ...,
        min_length=1,
        description=(
            "Country B's sovereign linker curve family identifier as "
            "stored in instrument_master.  Must pair with "
            "``country_b_nominal_pair`` under the same-country "
            "invariant enforced inside breakeven_inflation_simple, "
            "and must be a DIFFERENT linker from "
            "``country_a_linker_pair``."
        ),
    )
    tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Single tenor in years applied to BOTH country legs (e.g. "
            "'5Y', '10Y', '30Y').  The cross-country spread is "
            "evaluated at the same tenor on each side — this is the "
            "desk-canonical shape (e.g. 'US 10Y breakeven vs FR 10Y "
            "breakeven').  The tenor must exist on BOTH country pairs "
            "at the requested country/currency, otherwise the inner "
            "breakeven_inflation_simple call returns a controlled "
            "error envelope that this tool surfaces."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of *displayed* history.  Does NOT "
            "control the rolling z-score window (config: "
            "z_score_window_days, currently 252) or the trailing "
            "range window (config: trailing_range_window_days, "
            "locked at 252 in V1)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field used for ALL FOUR underlying "
            "yield series (country_a_nominal, country_a_linker, "
            "country_b_nominal, country_b_linker, all at the same "
            "tenor).  When None (default), the tool falls through to "
            "``default_field_name`` from config.yaml (currently "
            "'YLD_YTM_MID' — same mnemonic for nominal yield-to-"
            "maturity and linker real-yield-to-maturity).  Pass an "
            "explicit field name to override per query.  LLM/HTTP "
            "wrappers MUST translate their wire-level sentinel "
            "(empty string for MCP, missing param for FastAPI) to "
            "None before constructing this input — otherwise the "
            "YAML default is silently shadowed."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=(
            "Optional as-of date (YYYY-MM-DD): compute as of this trade "
            "date instead of the latest available data.  None → latest "
            "(live snapshot).  Supply a date for a historical, replayable "
            "view.  Threaded into BOTH inner breakeven_inflation_simple "
            "legs so each country leg is anchored to the same as-of trade "
            "date; the inner primitive caps every fetch at this upper bound."
        ),
    )

    @model_validator(mode="after")
    def _nominal_pairs_must_differ(
        self,
    ) -> "CrossCountryBreakevenSpreadSimpleInput":
        if self.country_a_nominal_pair == self.country_b_nominal_pair:
            raise ValueError(
                f"country_a_nominal_pair and country_b_nominal_pair "
                f"must be different, but both are "
                f"'{self.country_a_nominal_pair}'.  This primitive is "
                "a cross-country breakeven differential by "
                "construction — for same-country breakeven curve / "
                "spot work, use calculate_breakeven_curve_spread_tool "
                "or calculate_breakeven_inflation_simple_tool."
            )
        return self

    @model_validator(mode="after")
    def _linker_pairs_must_differ(
        self,
    ) -> "CrossCountryBreakevenSpreadSimpleInput":
        if self.country_a_linker_pair == self.country_b_linker_pair:
            raise ValueError(
                f"country_a_linker_pair and country_b_linker_pair "
                f"must be different, but both are "
                f"'{self.country_a_linker_pair}'.  This primitive is "
                "a cross-country breakeven differential by "
                "construction; identical linkers on both legs "
                "collapse the spread to ~zero and indicate the "
                "caller meant a same-country tool instead."
            )
        return self


class CrossCountryBreakevenSpreadSimpleCurrentMetrics(BaseModel):
    """Snapshot metrics for a same-tenor cross-country breakeven
    differential.

    Carries both the cross-country spread (in bps) and the two
    underlying country breakevens used to form it, so the desk can
    sanity-check the decomposition end-to-end without a second tool
    call.

    The ``methodology_label`` field carries the wire-honesty
    disclosure sourced from the YAML's
    ``methodology.what_it_does`` — NOT a hardcoded Python literal —
    so a YAML edit to the disclosure flows through to runtime.
    ``spread_label`` is a desk-friendly identifier like
    ``'UST/USD_TIPS - FR_OAT/EUR_FR_LINKER 10Y XC breakeven'``.
    """

    as_of_date: str = Field(
        ..., description="Most recent trade date (YYYY-MM-DD).",
    )
    country_a_nominal_pair: str
    country_a_linker_pair: str
    country_b_nominal_pair: str
    country_b_linker_pair: str
    tenor: str
    spread_label: str = Field(
        ...,
        description=(
            "Human-readable label, e.g. "
            "'UST/USD_TIPS - FR_OAT/EUR_FR_LINKER 10Y XC breakeven' "
            "or 'UST/USD_TIPS - UK_GILT/GBP_LINKER 5Y XC breakeven'."
        ),
    )
    current_spread_bps: Optional[float] = Field(
        None,
        description=(
            "Current cross-country breakeven spread in basis points "
            "(breakeven_a_bps - breakeven_b_bps).  Cross-country "
            "inflation-compensation differential; not a pure cross-"
            "country expected-inflation differential — see "
            "``methodology_label`` (each leg carries inflation risk "
            "premium and liquidity premium, AND the two legs may "
            "reference different inflation indices)."
        ),
    )
    daily_change_bps: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in the cross-country breakeven "
            "spread (bps)."
        ),
    )
    weekly_change_bps: Optional[float] = Field(
        None,
        description=(
            "5-trading-day change in the cross-country breakeven "
            "spread (bps)."
        ),
    )
    monthly_change_bps: Optional[float] = Field(
        None,
        description=(
            "22-trading-day (~1 month) change in the cross-country "
            "breakeven spread (bps)."
        ),
    )
    current_z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the cross-country "
            "breakeven spread (in bps) vs its own trailing window."
        ),
    )
    rolling_window_days: int = Field(
        ..., description="Window used for the z-score calculation.",
    )
    high_252d_bps: Optional[float] = Field(
        None,
        description=(
            "Highest cross-country breakeven spread over trailing 252 "
            "trading days (bps)."
        ),
    )
    low_252d_bps: Optional[float] = Field(
        None,
        description=(
            "Lowest cross-country breakeven spread over trailing 252 "
            "trading days (bps)."
        ),
    )
    percentile_252d: Optional[float] = Field(
        None,
        description=(
            "Percentile rank within trailing 252-day range (0-100)."
        ),
    )
    breakeven_a_bps: Optional[float] = Field(
        None,
        description=(
            "Latest country_a spot bond-implied breakeven (bps) at "
            "``tenor`` — exposed so the desk can audit the cross-"
            "country spread decomposition."
        ),
    )
    breakeven_b_bps: Optional[float] = Field(
        None,
        description=(
            "Latest country_b spot bond-implied breakeven (bps) at "
            "``tenor``."
        ),
    )
    tenor_years: float = Field(
        ...,
        description=(
            "Year fraction of ``tenor`` (e.g. 10.0 for '10Y').  "
            "Single-tenor display field — both country legs are "
            "evaluated at this same tenor."
        ),
    )
    methodology_label: str = Field(
        ...,
        description=(
            "Wire-honesty disclosure threaded from the YAML's "
            "``methodology.what_it_does``.  Carries the explicit "
            "spread formula (breakeven_a_bps - breakeven_b_bps), "
            "the sign convention (country_a minus country_b), the "
            "'inflation compensation, not pure expected inflation' "
            "caveat AND the index-family mismatch caveat (CPI-U vs "
            "HICP, etc.) so downstream operators and the LLM cannot "
            "misread the output."
        ),
    )


class CrossCountryBreakevenSpreadSimpleTimeSeriesRow(BaseModel):
    """Single row in the bespoke cross-country breakeven-spread
    time-series.

    ``spread_bps`` is the cross-country breakeven spread in basis
    points; ``z_score`` is the rolling z-score (None for rows in the
    rolling-window warmup).
    """

    date: str
    spread_bps: float
    z_score: Optional[float] = None


class CrossCountryBreakevenSpreadSimpleOutput(BaseModel):
    """Top-level response for the cross_country_breakeven_spread_simple
    tool.

    ``time_series`` is the bespoke wire-frozen shape
    (``CrossCountryBreakevenSpreadSimpleTimeSeriesRow``) for callers
    that want spread + z-score in one row.

    ``time_series_spread`` and ``time_series_zscore`` are the
    canonical closed-enum shapes the workflow / operator layer
    consumes.  All three are computed from the same underlying
    display DataFrame — they cannot drift.
    """

    current_metrics: CrossCountryBreakevenSpreadSimpleCurrentMetrics
    time_series: List[CrossCountryBreakevenSpreadSimpleTimeSeriesRow]
    time_series_spread: TimeSeries = Field(
        ...,
        description=(
            "Historical same-tenor cross-country breakeven spread "
            "(in BPS) over the displayed window.  Closed-enum "
            "``TimeSeriesUnits.BPS``; series_name pattern: "
            "'<an>_<al>_<bn>_<bl>_<tenor>_xc_breakeven_spread'.  "
            "Values match ``time_series[i].spread_bps`` 1-to-1 by "
            "construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the cross-country "
            "breakeven spread (in bps) vs its own trailing window.  "
            "Closed-enum ``TimeSeriesUnits.Z_SCORE``; series_name "
            "pattern: "
            "'<an>_<al>_<bn>_<bl>_<tenor>_xc_breakeven_spread_zscore'.  "
            "Values match ``time_series[i].z_score`` 1-to-1 (None "
            "for rows in the rolling-window warmup)."
        ),
    )


__all__ = [
    "CrossCountryBreakevenSpreadSimpleInput",
    "CrossCountryBreakevenSpreadSimpleCurrentMetrics",
    "CrossCountryBreakevenSpreadSimpleTimeSeriesRow",
    "CrossCountryBreakevenSpreadSimpleOutput",
]
