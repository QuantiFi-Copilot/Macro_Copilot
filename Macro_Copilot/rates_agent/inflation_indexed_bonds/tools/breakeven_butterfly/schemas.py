"""Pydantic schemas for the breakeven_butterfly tool.

Owns the desk concept of a *same-country bond-implied breakeven
butterfly* (3-point breakeven-inflation-compensation curvature) — the
3-point curvature on a SINGLE nominal/linker pair (e.g. UST/USD_TIPS
5s10s30s breakeven butterfly, UK_GILT/GBP_LINKER 2s10s30s breakeven
butterfly, FR_OAT/EUR_FR_LINKER 2s5s10s breakeven butterfly,
CANADA_GOVT/CAD_RRB 5s10s30s breakeven butterfly).  Composed from
three ``calculate_breakeven_inflation_simple`` calls (one per endpoint
tenor) into a per-trade-date FIXED simple-butterfly weighting.

Construction rule
-----------------
``butterfly_bps = belly_breakeven_bps
                - 0.5 * (short_breakeven_bps + long_breakeven_bps)``

Equivalent to ``0.5 * (2 * belly - short - long)``.  Sign convention:
POSITIVE = belly is CHEAP versus the half-weighted wings (i.e. belly
breakeven is HIGH relative to the wings); NEGATIVE = belly is RICH.
Matches the sovereign_bonds/butterfly tool's and
inflation_indexed_bonds/real_yield_butterfly tool's sign convention.
All three endpoints arrive from the spot breakeven primitive in BPS;
this tool keeps the butterfly in BPS (same units as the underlying
breakeven series).  Daily / weekly / monthly *changes* of the
butterfly are also reported in BPS (already-bps subtraction).

Same-country invariant
----------------------
All three endpoints share a single nominal/linker pair (e.g. UST +
USD_TIPS).  The two legs of each endpoint MUST come from the same
issuer — i.e. share both ``country`` and ``currency`` as stored in
``macro_data.instrument_master``.  Cross-country pairs (e.g. DE_BUND
vs EUR_FR_LINKER — same currency, different country; or UK_GILT vs
USD_TIPS — different currency) are refused with a controlled error
envelope.  This primitive inherits the spot primitive's
``_enforce_same_country_invariant`` guard transitively: cross-country
pairs are refused BEFORE any market-data SELECT fires.

Validation layering
-------------------
- ``field_name`` defaults to ``None`` — the sentinel that resolves
  against the YAML's ``default_field_name`` convention.  Mirrors the
  empty-string / None-sentinel pattern used by every other rates tool.

- ``lookback_days`` controls the displayed window only (the bespoke
  ``time_series`` row count and the canonical TimeSeries length).
  Does NOT control the rolling z-score window or the trailing range
  window — those are independent conventions in ``config.yaml``.

- ``high_252d_bps`` / ``low_252d_bps`` / ``percentile_252d`` field
  names embed the trailing-range window length and are wire-frozen in
  V1.  See ``config.yaml``'s ``planned_extensions`` for the path to
  making the trailing window configurable.

Cross-field invariants stay here in code:
``_curve_families_must_differ`` is a structural input check (a caller
asking for ``nominal=USD_TIPS, linker=USD_TIPS`` is a structural
error, not a methodology choice), so it is NOT a YAML knob.
``_tenors_must_all_differ`` enforces three distinct tenors.
``_short_belly_long_strictly_ordered`` enforces
``short_years < belly_years < long_years`` computed via the shared
``tenor_to_years`` parser — so e.g. ``short='10Y', belly='5Y',
long='2Y'`` is rejected with a precise error message.  These are
structural inputs, not YAML knobs.

The *same-country / same-currency* invariant is enforced at compute
time by the spot breakeven primitive's helper, NOT here in the
Pydantic validator (it requires a DB lookup against
``macro_data.instrument_master``).  See
``rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute._enforce_same_country_invariant``
for the guard and the controlled ``{"error": ...}`` envelope it emits
on mismatch — both of those flow through this primitive unchanged
because every endpoint composes the spot primitive.

Canonical TimeSeries output
---------------------------
This tool emits TWO canonical
``shared.schemas.time_series.TimeSeries`` payloads alongside the
bespoke ``time_series`` list:

  - ``time_series_butterfly`` (units = BPS) — the breakeven
    butterfly series over the displayed window.  series_name
    pattern:
    ``<nominal_lower>_<linker_lower>_<short_lower>_<belly_lower>_<long_lower>_breakeven_butterfly``.

  - ``time_series_zscore`` (units = Z_SCORE) — the rolling z-score
    of the butterfly series over the displayed window.  series_name
    pattern:
    ``<nominal_lower>_<linker_lower>_<short_lower>_<belly_lower>_<long_lower>_breakeven_butterfly_zscore``.

All three (bespoke + two canonical) share the same display DataFrame
so they cannot drift.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.analytics.curve_bootstrap import tenor_to_years
from shared.schemas import TimeSeries


class BreakevenButterflyInput(BaseModel):
    """Parameters for computing a same-country bond-implied
    breakeven butterfly (3-point curvature) between three breakeven
    tenors of the same nominal/linker pair.

    butterfly_bps = belly_breakeven_bps
                  - 0.5 * (short_breakeven_bps + long_breakeven_bps)

    Same-country invariant
    ----------------------
    The two legs MUST come from the same issuer — i.e. share both
    ``country`` and ``currency`` as stored in
    ``macro_data.instrument_master``.  Valid pairs include
    ``UST + USD_TIPS``, ``UK_GILT + GBP_LINKER``,
    ``FR_OAT + EUR_FR_LINKER``, ``CANADA_GOVT + CAD_RRB``.  Cross-
    country pairs (e.g. ``DE_BUND + EUR_FR_LINKER`` — same currency,
    different country; or ``UK_GILT + USD_TIPS`` — different
    currency) are refused at compute time with a controlled
    ``{"error": "..."}`` envelope.  This invariant is enforced by
    the spot breakeven primitive's
    ``_enforce_same_country_invariant`` helper (which fires before
    either market-data SELECT) and is inherited transitively by
    this primitive's three endpoint calls.
    """

    model_config = ConfigDict(extra="forbid")

    nominal_curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "Nominal sovereign curve family identifier as stored in "
            "instrument_master.  Examples: 'UST', 'UK_GILT', 'FR_OAT', "
            "'CANADA_GOVT'.  See "
            "rates_agent/playbooks/sovereign_bonds.yml for the "
            "ingested universe.  Same-country invariant: this leg "
            "must share country AND currency with "
            "``linker_curve_family``."
        ),
    )
    linker_curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "Sovereign linker curve family identifier as stored in "
            "instrument_master.  Examples: 'USD_TIPS', 'GBP_LINKER', "
            "'EUR_FR_LINKER', 'CAD_RRB'.  See "
            "rates_agent/playbooks/inflation_indexed_bonds.yml for "
            "the ingested universe.  Same-country invariant: this "
            "leg must share country AND currency with "
            "``nominal_curve_family``."
        ),
    )
    short_tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Short wing tenor of the breakeven butterfly (e.g. '2Y' "
            "for 2s5s10s, '5Y' for 5s10s30s).  Must be present on "
            "BOTH the nominal and linker curves at this country; "
            "otherwise the inner endpoint breakeven returns a "
            "controlled error envelope which this tool surfaces.  "
            "Must map to a strictly smaller year fraction than "
            "``belly_tenor`` (validated via "
            "shared.analytics.curve_bootstrap.tenor_to_years)."
        ),
    )
    belly_tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Belly (body) tenor of the breakeven butterfly (e.g. "
            "'5Y' for 2s5s10s, '10Y' for 5s10s30s).  Must be "
            "present on BOTH curves at this country and must map "
            "to a year fraction strictly between ``short_tenor`` "
            "and ``long_tenor``."
        ),
    )
    long_tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Long wing tenor of the breakeven butterfly (e.g. "
            "'10Y' for 2s5s10s, '30Y' for 5s10s30s).  Must be "
            "present on BOTH curves at this country and must map "
            "to a strictly larger year fraction than "
            "``belly_tenor``."
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
            "Bloomberg observation field used for ALL SIX "
            "underlying yield series (nominal + linker at each of "
            "short_tenor, belly_tenor, long_tenor).  When None "
            "(default), the tool falls through to "
            "``default_field_name`` from config.yaml (currently "
            "'YLD_YTM_MID' — same mnemonic for nominal yield-to-"
            "maturity and linker real-yield-to-maturity).  Pass an "
            "explicit field name to override per query.  LLM/HTTP "
            "wrappers MUST translate their wire-level sentinel "
            "(empty string for MCP, missing param for FastAPI) to "
            "None before constructing this input — otherwise the "
            "YAML default is silently shadowed.  See the "
            "curve_move_classifier wrapper-shadowing fix (commit "
            "b2605ee) for the canonical pattern."
        ),
    )

    @model_validator(mode="after")
    def _curve_families_must_differ(
        self,
    ) -> "BreakevenButterflyInput":
        if self.nominal_curve_family == self.linker_curve_family:
            raise ValueError(
                f"nominal_curve_family and linker_curve_family must "
                f"be different, but both are "
                f"'{self.nominal_curve_family}'.  Pass the nominal "
                "sovereign curve as ``nominal_curve_family`` (e.g. "
                "'UST') and the matching linker curve as "
                "``linker_curve_family`` (e.g. 'USD_TIPS')."
            )
        return self

    @model_validator(mode="after")
    def _tenors_must_all_differ(self) -> "BreakevenButterflyInput":
        tenors = [self.short_tenor, self.belly_tenor, self.long_tenor]
        if len(set(tenors)) != 3:
            raise ValueError(
                f"short_tenor, belly_tenor, and long_tenor must all "
                f"be different, but got {tenors}.  A breakeven "
                "butterfly requires three distinct endpoint tenors."
            )
        return self

    @model_validator(mode="after")
    def _short_belly_long_strictly_ordered(
        self,
    ) -> "BreakevenButterflyInput":
        try:
            t_short = tenor_to_years(self.short_tenor)
            t_belly = tenor_to_years(self.belly_tenor)
            t_long = tenor_to_years(self.long_tenor)
        except ValueError as exc:
            raise ValueError(
                f"Could not parse tenor triplet "
                f"short_tenor='{self.short_tenor}', "
                f"belly_tenor='{self.belly_tenor}', "
                f"long_tenor='{self.long_tenor}': {exc}.  Accepted "
                "forms are '<n>W', '<n>M', '<n>Y'."
            ) from exc
        if not (t_short < t_belly < t_long):
            raise ValueError(
                f"Tenor triplet must satisfy "
                f"short_years < belly_years < long_years, but got "
                f"short_tenor='{self.short_tenor}' ({t_short:g}y), "
                f"belly_tenor='{self.belly_tenor}' ({t_belly:g}y), "
                f"long_tenor='{self.long_tenor}' ({t_long:g}y).  A "
                "breakeven butterfly cannot be inverted; pass the "
                "shortest tenor as ``short_tenor``, the middle as "
                "``belly_tenor``, and the longest as ``long_tenor``."
            )
        return self


class BreakevenButterflyCurrentMetrics(BaseModel):
    """Snapshot metrics for a same-country bond-implied breakeven
    butterfly.

    Carries both the butterfly (in BPS) and the three endpoint
    breakevens + year fractions + component wing spreads used to
    form it, so the desk can sanity-check the decomposition end-to-
    end without a second tool call.

    The ``methodology_label`` field carries the wire-honesty
    disclosure sourced from the YAML's ``methodology.what_it_does``
    — NOT a hardcoded Python literal — so a YAML edit to the
    disclosure flows through to runtime.  ``butterfly_label`` is a
    desk-friendly identifier like
    ``'UST/USD_TIPS 5s10s30s breakeven'``.
    """

    as_of_date: str = Field(
        ..., description="Most recent aligned trade date (YYYY-MM-DD).",
    )
    nominal_curve_family: str
    linker_curve_family: str
    short_tenor: str
    belly_tenor: str
    long_tenor: str
    butterfly_label: str = Field(
        ...,
        description=(
            "Human-readable label, e.g. 'UST/USD_TIPS 5s10s30s "
            "breakeven' or 'UK_GILT/GBP_LINKER 2s10s30s breakeven'."
        ),
    )
    current_butterfly_bps: Optional[float] = Field(
        None,
        description=(
            "Current breakeven butterfly in basis points "
            "(belly_breakeven_bps - 0.5*(short_breakeven_bps + "
            "long_breakeven_bps)).  Curvature of the bond-implied "
            "BREAKEVEN curve — distinct from a breakeven curve "
            "spread (2-point difference) and from a nominal "
            "sovereign butterfly (3-point curvature of nominal "
            "yields, in BPS).  Sign convention: POSITIVE = belly "
            "is CHEAP versus the half-weighted wings (i.e. belly "
            "breakeven is HIGH relative to the wings); NEGATIVE = "
            "belly is RICH.  Inflation compensation curvature, "
            "NOT a curvature of pure expected inflation — see "
            "``methodology_label``."
        ),
    )
    daily_change_bps: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in the breakeven butterfly (bps)."
        ),
    )
    weekly_change_bps: Optional[float] = Field(
        None,
        description=(
            "5-trading-day change in the breakeven butterfly (bps)."
        ),
    )
    monthly_change_bps: Optional[float] = Field(
        None,
        description=(
            "22-trading-day (~1 month) change in the breakeven "
            "butterfly (bps)."
        ),
    )
    current_z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the breakeven "
            "butterfly (in bps) vs its own trailing window."
        ),
    )
    rolling_window_days: int = Field(
        ..., description="Window used for the z-score calculation.",
    )
    high_252d_bps: Optional[float] = Field(
        None,
        description=(
            "Highest breakeven butterfly over trailing 252 trading "
            "days (bps)."
        ),
    )
    low_252d_bps: Optional[float] = Field(
        None,
        description=(
            "Lowest breakeven butterfly over trailing 252 trading "
            "days (bps)."
        ),
    )
    percentile_252d: Optional[float] = Field(
        None,
        description=(
            "Percentile rank within trailing 252-day range (0-100)."
        ),
    )
    wing_short_bps: Optional[float] = Field(
        None,
        description=(
            "Component spread: belly_breakeven_bps - "
            "short_breakeven_bps (bps)."
        ),
    )
    wing_long_bps: Optional[float] = Field(
        None,
        description=(
            "Component spread: long_breakeven_bps - "
            "belly_breakeven_bps (bps)."
        ),
    )
    short_breakeven_bps: Optional[float] = Field(
        None,
        description=(
            "Latest spot bond-implied breakeven (bps) at "
            "``short_tenor`` — exposed so the desk can audit the "
            "butterfly decomposition."
        ),
    )
    belly_breakeven_bps: Optional[float] = Field(
        None,
        description=(
            "Latest spot bond-implied breakeven (bps) at "
            "``belly_tenor``."
        ),
    )
    long_breakeven_bps: Optional[float] = Field(
        None,
        description=(
            "Latest spot bond-implied breakeven (bps) at "
            "``long_tenor``."
        ),
    )
    short_years: float = Field(
        ...,
        description=(
            "Year fraction of ``short_tenor`` (e.g. 2.0 for '2Y')."
        ),
    )
    belly_years: float = Field(
        ...,
        description=(
            "Year fraction of ``belly_tenor`` (e.g. 5.0 for '5Y')."
        ),
    )
    long_years: float = Field(
        ...,
        description=(
            "Year fraction of ``long_tenor`` (e.g. 10.0 for '10Y')."
        ),
    )
    observation_count: int = Field(
        ...,
        description=(
            "Number of trading days in the displayed lookback_days "
            "window after inner-join alignment."
        ),
    )
    methodology_label: str = Field(
        ...,
        description=(
            "Wire-honesty disclosure threaded from the YAML's "
            "``methodology.what_it_does``.  Carries the explicit "
            "butterfly formula (belly_breakeven_bps - 0.5*("
            "short_breakeven_bps + long_breakeven_bps)) AND the "
            "'inflation compensation curvature; not pure expected "
            "inflation' caveat so downstream operators and the LLM "
            "cannot misread the output's sign convention or the "
            "compensation-vs-expectations distinction."
        ),
    )


class BreakevenButterflyTimeSeriesRow(BaseModel):
    """Single row in the bespoke breakeven-butterfly time-series.

    ``butterfly_bps`` is the breakeven butterfly in basis points;
    ``z_score`` is the rolling z-score (None for rows in the
    rolling-window warmup).
    """

    date: str
    butterfly_bps: float
    z_score: Optional[float] = None


class BreakevenButterflyOutput(BaseModel):
    """Top-level response for the breakeven_butterfly tool.

    ``time_series`` is the bespoke wire-frozen shape
    (``BreakevenButterflyTimeSeriesRow``) for callers that want
    butterfly + z-score in one row.

    ``time_series_butterfly`` and ``time_series_zscore`` are the
    canonical closed-enum shapes the workflow / operator layer
    consumes.  All three are computed from the same underlying
    display DataFrame — they cannot drift.
    """

    current_metrics: BreakevenButterflyCurrentMetrics
    time_series: List[BreakevenButterflyTimeSeriesRow]
    time_series_butterfly: TimeSeries = Field(
        ...,
        description=(
            "Historical same-country bond-implied breakeven "
            "butterfly (in BPS) over the displayed window.  Closed-"
            "enum ``TimeSeriesUnits.BPS``; series_name pattern: "
            "'<nominal>_<linker>_<short>_<belly>_<long>_breakeven_butterfly'.  "
            "Values match ``time_series[i].butterfly_bps`` 1-to-1 "
            "by construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the breakeven butterfly "
            "(in bps) vs its own trailing window.  Closed-enum "
            "``TimeSeriesUnits.Z_SCORE``; series_name pattern: "
            "'<nominal>_<linker>_<short>_<belly>_<long>_breakeven_butterfly_zscore'.  "
            "Values match ``time_series[i].z_score`` 1-to-1 (None "
            "for rows in the rolling-window warmup)."
        ),
    )


__all__ = [
    "BreakevenButterflyInput",
    "BreakevenButterflyCurrentMetrics",
    "BreakevenButterflyTimeSeriesRow",
    "BreakevenButterflyOutput",
]
