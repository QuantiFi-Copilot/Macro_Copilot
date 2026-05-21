"""Pydantic schemas for the cross_country_real_yield_spread_simple tool.

Seventh tool in the ``inflation_indexed_bonds`` domain.  Owns the
desk concept of a *same-tenor cross-country linker real-yield
differential*: the difference between two linker curves' real-yield
levels at the same tenor (e.g. USD_TIPS 10Y real yield minus
GBP_LINKER 10Y real yield).  Explicitly a CROSS-COUNTRY / CROSS-
CURVE object — the two legs MUST come from different linker curve
families.  See ``methodology.what_it_does`` in the tool's
``config.yaml`` and the ``methodology_label`` field on
``CrossCountryRealYieldSpreadSimpleCurrentMetrics``.

Composition path
----------------
This primitive composes two calls to ``get_real_yield_level`` (one
per curve_family).  The level primitive's no-proxy guard
(``instrument_type='inflation_linker'`` filter on each leg's DB
read) is inherited transitively — passing a nominal sovereign
curve_family at either leg returns a controlled error envelope from
the inner level call which this tool surfaces with a leg-attributed
prefix.

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

- ``high_252d_pct`` / ``low_252d_pct`` / ``percentile_252d`` field
  names embed the trailing-range window length and are wire-frozen
  in V1.  See ``config.yaml``'s ``planned_extensions`` for the
  path to making the trailing window configurable.

Cross-field invariants stay here in code:
``_curves_must_differ`` enforces
``first_curve_family != second_curve_family`` at the schema layer
(no DB lookup required).  A caller passing identical curves on both
legs is rejected before any compute work fires; for same-country
curve-shape work, see ``calculate_real_yield_curve_spread_tool``.

The *both-legs-must-be-linker* invariant is enforced at compute
time by a local helper that queries
``macro_data.instrument_master`` for the resolved
``(country, currency)`` tuple under
``instrument_type='inflation_linker'`` on EACH curve.  A non-
linker curve_family on either leg yields a controlled error
envelope.  See
``rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple.compute._enforce_linker_identity_both_legs``
for the guard and the controlled ``{"error": ...}`` envelope it
emits on failure.

Canonical TimeSeries output
---------------------------
This tool emits TWO canonical
``shared.schemas.time_series.TimeSeries`` payloads alongside the
bespoke ``time_series`` list:

  - ``time_series_spread`` (units = PERCENT) — the cross-country
    real-yield spread series over the displayed window.
    series_name pattern:
    ``<first_curve_lower>_<second_curve_lower>_<tenor_lower>_xc_real_yield_spread``.
    Description carries the literal
    ``first_curve_real_yield_pct - second_curve_real_yield_pct``
    sign-convention token AND the index-family / market-structure
    caveats.

  - ``time_series_zscore`` (units = Z_SCORE) — the rolling z-score
    of the spread series (computed on the percent-units spread)
    over the displayed window.  series_name pattern:
    ``<first_curve_lower>_<second_curve_lower>_<tenor_lower>_xc_real_yield_spread_zscore``.

All three (bespoke + two canonical) share the same display
DataFrame so they cannot drift.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.schemas import TimeSeries


class CrossCountryRealYieldSpreadSimpleInput(BaseModel):
    """Parameters for computing a same-tenor cross-country linker
    real-yield differential between two linker curves' real-yield
    levels at the same tenor.

    spread_pct = first_curve_real_yield_pct - second_curve_real_yield_pct

    Cross-country / cross-curve invariant
    -------------------------------------
    first_curve_family and second_curve_family MUST be different
    linker curve families.  The Pydantic validator enforces
    ``first_curve_family != second_curve_family`` at schema time
    (no DB lookup needed).  Same-curve inputs are rejected before
    any compute work fires.

    Both-legs-must-be-linker invariant
    ----------------------------------
    Compute additionally re-asserts at runtime that BOTH curve
    families resolve to ``instrument_type='inflation_linker'``
    under a single ``(country, currency)`` tuple in
    ``macro_data.instrument_master``.  A non-linker curve_family on
    either leg (e.g. nominal sovereign 'UST' or 'DE_BUND') yields a
    controlled error envelope BEFORE any market-data SELECT.
    """

    model_config = ConfigDict(extra="forbid")

    first_curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "First linker curve family identifier exactly as stored "
            "in instrument_master.  Examples: 'USD_TIPS' (US TIPS), "
            "'GBP_LINKER' (UK inflation-linked Gilts), "
            "'EUR_FR_LINKER' (France OATi/OATei), 'CAD_RRB' "
            "(Canadian Real Return Bonds).  See "
            "rates_agent/playbooks/inflation_indexed_bonds.yml for "
            "the ingested universe.  Cross-country invariant: this "
            "field MUST differ from ``second_curve_family``; same-"
            "curve inputs are rejected at the schema layer (for "
            "same-country curve-shape work, use "
            "calculate_real_yield_curve_spread_tool)."
        ),
    )
    second_curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "Second linker curve family identifier exactly as "
            "stored in instrument_master.  Must be a DIFFERENT "
            "linker from ``first_curve_family``.  Sign convention "
            "is first_curve_family minus second_curve_family — "
            "fixed; the tool never silently flips the sign."
        ),
    )
    tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Single tenor in years applied to BOTH curves (e.g. "
            "'5Y', '10Y', '30Y').  The cross-country real-yield "
            "spread is evaluated at the same tenor on each side — "
            "this is the desk-canonical shape (e.g. 'US 10Y real "
            "yield vs UK 10Y real yield').  The tenor must exist "
            "on BOTH curves at the requested country/currency, "
            "otherwise the inner get_real_yield_level call returns "
            "a controlled error envelope that this tool surfaces "
            "with the offending leg named."
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
            "Bloomberg observation field used for BOTH endpoint "
            "real-yield series (first_curve at tenor, "
            "second_curve at tenor).  When None (default), the "
            "tool falls through to ``default_field_name`` from "
            "config.yaml (currently 'YLD_YTM_MID' — the linker "
            "real-yield-to-maturity mnemonic).  Pass an explicit "
            "field name to override per query.  LLM/HTTP wrappers "
            "MUST translate their wire-level sentinel (empty "
            "string for MCP, missing param for FastAPI) to None "
            "before constructing this input — otherwise the YAML "
            "default is silently shadowed.  See the "
            "curve_move_classifier wrapper-shadowing fix (commit "
            "b2605ee) for the canonical pattern."
        ),
    )

    @model_validator(mode="after")
    def _curves_must_differ(
        self,
    ) -> "CrossCountryRealYieldSpreadSimpleInput":
        if self.first_curve_family == self.second_curve_family:
            raise ValueError(
                f"first_curve_family and second_curve_family must "
                f"be different, but both are "
                f"'{self.first_curve_family}'.  This primitive is "
                "a cross-country real-yield differential by "
                "construction; identical curves on both legs "
                "collapse the spread to zero and indicate the "
                "caller meant the same-country curve-shape tool "
                "instead — see "
                "calculate_real_yield_curve_spread_tool."
            )
        return self


class CrossCountryRealYieldSpreadSimpleCurrentMetrics(BaseModel):
    """Snapshot metrics for a same-tenor cross-country linker
    real-yield differential.

    Carries both the cross-country spread (in PERCENT) and the two
    underlying linker real-yield levels + resolved
    (country, currency) identities used to form it, so the desk can
    sanity-check the decomposition end-to-end without a second tool
    call.

    The ``methodology_label`` field carries the wire-honesty
    disclosure sourced from the YAML's
    ``methodology.what_it_does`` — NOT a hardcoded Python literal —
    so a YAML edit to the disclosure flows through to runtime.
    ``spread_label`` is a desk-friendly identifier like
    ``'USD_TIPS - GBP_LINKER 10Y XC real-yield'``.
    """

    as_of_date: str = Field(
        ..., description="Most recent aligned trade date (YYYY-MM-DD).",
    )
    first_curve_family: str
    second_curve_family: str
    tenor: str
    spread_label: str = Field(
        ...,
        description=(
            "Human-readable label, e.g. "
            "'USD_TIPS - GBP_LINKER 10Y XC real-yield' or "
            "'USD_TIPS - EUR_FR_LINKER 5Y XC real-yield'."
        ),
    )
    current_spread_pct: Optional[float] = Field(
        None,
        description=(
            "Current cross-country real-yield spread in PERCENT "
            "(first_curve_real_yield_pct - "
            "second_curve_real_yield_pct).  Cross-country real-"
            "rate differential — distinct from a cross-country "
            "breakeven differential (inflation compensation) and "
            "from a sovereign nominal cross-market spread "
            "(nominal-yield divergence).  Subject to index-family "
            "AND market-structure mismatch caveats — see "
            "``methodology_label``."
        ),
    )
    daily_change_bps: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in the cross-country real-yield "
            "spread (BPS).  Daily change of a percent-units spread "
            "is reported in bps per desk convention."
        ),
    )
    weekly_change_bps: Optional[float] = Field(
        None,
        description=(
            "5-trading-day change in the cross-country real-yield "
            "spread (BPS)."
        ),
    )
    monthly_change_bps: Optional[float] = Field(
        None,
        description=(
            "22-trading-day (~1 month) change in the cross-country "
            "real-yield spread (BPS)."
        ),
    )
    current_z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the cross-country "
            "real-yield spread (in PERCENT) vs its own trailing "
            "window."
        ),
    )
    rolling_window_days: int = Field(
        ..., description="Window used for the z-score calculation.",
    )
    high_252d_pct: Optional[float] = Field(
        None,
        description=(
            "Highest cross-country real-yield spread over trailing "
            "252 trading days (PERCENT)."
        ),
    )
    low_252d_pct: Optional[float] = Field(
        None,
        description=(
            "Lowest cross-country real-yield spread over trailing "
            "252 trading days (PERCENT)."
        ),
    )
    percentile_252d: Optional[float] = Field(
        None,
        description=(
            "Percentile rank within trailing 252-day range (0-100)."
        ),
    )
    first_curve_real_yield_pct: Optional[float] = Field(
        None,
        description=(
            "Latest first_curve real yield (in PERCENT) at "
            "``tenor`` — exposed so the desk can audit the cross-"
            "country spread decomposition."
        ),
    )
    second_curve_real_yield_pct: Optional[float] = Field(
        None,
        description=(
            "Latest second_curve real yield (in PERCENT) at "
            "``tenor``."
        ),
    )
    tenor_years: float = Field(
        ...,
        description=(
            "Year fraction of ``tenor`` (e.g. 10.0 for '10Y').  "
            "Single-tenor display field — both linker legs are "
            "evaluated at this same tenor."
        ),
    )
    first_curve_country: str = Field(
        ...,
        description=(
            "Country identifier resolved from instrument_master "
            "for first_curve_family (e.g. 'US', 'UK', 'France', "
            "'Canada').  Surfaced on the wire so a desk reader "
            "can confirm the resolved linker identity without a "
            "second tool call."
        ),
    )
    first_curve_currency: str = Field(
        ...,
        description=(
            "Currency identifier resolved from instrument_master "
            "for first_curve_family (e.g. 'USD', 'GBP', 'EUR', "
            "'CAD')."
        ),
    )
    second_curve_country: str = Field(
        ...,
        description=(
            "Country identifier resolved from instrument_master "
            "for second_curve_family."
        ),
    )
    second_curve_currency: str = Field(
        ...,
        description=(
            "Currency identifier resolved from instrument_master "
            "for second_curve_family."
        ),
    )
    methodology_label: str = Field(
        ...,
        description=(
            "Wire-honesty disclosure threaded from the YAML's "
            "``methodology.what_it_does``.  Carries the explicit "
            "spread formula (first_curve_real_yield_pct - "
            "second_curve_real_yield_pct), the sign convention "
            "(first minus second), the index-family mismatch "
            "caveat (CPI-U vs HICP vs RPI vs CAN_CPI), AND the "
            "market-structure mismatch caveat (cross-country "
            "linker-liquidity / issuance-size differences) so "
            "downstream operators and the LLM cannot misread the "
            "output."
        ),
    )


class CrossCountryRealYieldSpreadSimpleTimeSeriesRow(BaseModel):
    """Single row in the bespoke cross-country real-yield-spread
    time-series.

    ``spread_pct`` is the cross-country real-yield spread in
    PERCENT; ``z_score`` is the rolling z-score (None for rows in
    the rolling-window warmup).
    """

    date: str
    spread_pct: float
    z_score: Optional[float] = None


class CrossCountryRealYieldSpreadSimpleOutput(BaseModel):
    """Top-level response for the
    cross_country_real_yield_spread_simple tool.

    ``time_series`` is the bespoke wire-frozen shape
    (``CrossCountryRealYieldSpreadSimpleTimeSeriesRow``) for callers
    that want spread + z-score in one row.

    ``time_series_spread`` and ``time_series_zscore`` are the
    canonical closed-enum shapes the workflow / operator layer
    consumes.  All three are computed from the same underlying
    display DataFrame — they cannot drift.
    """

    current_metrics: CrossCountryRealYieldSpreadSimpleCurrentMetrics
    time_series: List[CrossCountryRealYieldSpreadSimpleTimeSeriesRow]
    time_series_spread: TimeSeries = Field(
        ...,
        description=(
            "Historical same-tenor cross-country linker real-yield "
            "spread (in PERCENT) over the displayed window.  "
            "Closed-enum ``TimeSeriesUnits.PERCENT``; series_name "
            "pattern: "
            "'<first>_<second>_<tenor>_xc_real_yield_spread'.  "
            "Values match ``time_series[i].spread_pct`` 1-to-1 by "
            "construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the cross-country "
            "real-yield spread (in PERCENT) vs its own trailing "
            "window.  Closed-enum ``TimeSeriesUnits.Z_SCORE``; "
            "series_name pattern: "
            "'<first>_<second>_<tenor>_xc_real_yield_spread_zscore'. "
            "Values match ``time_series[i].z_score`` 1-to-1 (None "
            "for rows in the rolling-window warmup)."
        ),
    )


__all__ = [
    "CrossCountryRealYieldSpreadSimpleInput",
    "CrossCountryRealYieldSpreadSimpleCurrentMetrics",
    "CrossCountryRealYieldSpreadSimpleTimeSeriesRow",
    "CrossCountryRealYieldSpreadSimpleOutput",
]
