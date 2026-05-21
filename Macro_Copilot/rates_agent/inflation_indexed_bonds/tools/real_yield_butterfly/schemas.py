"""Pydantic schemas for the linker real_yield_butterfly tool.

Owns the desk concept of a *same-country linker real-yield butterfly*
(curvature) — the 3-point curvature on a SINGLE sovereign linker curve
(e.g. USD_TIPS 5s10s30s real-yield butterfly, GBP_LINKER 2s10s30s
real-yield butterfly, EUR_FR_LINKER 2s10s15s real-yield butterfly,
CAD_RRB 5s10s30s real-yield butterfly).  Composed from three
``get_real_yield_level`` calls (one per endpoint tenor) into a
per-trade-date FIXED simple-butterfly weighting.

Construction rule
-----------------
``butterfly_pct = belly_real_yield_pct
                - 0.5 * (short_real_yield_pct + long_real_yield_pct)``

Equivalent to ``0.5 * (2 * belly - short - long)``.  Sign convention:
POSITIVE = belly is CHEAP versus the half-weighted wings; NEGATIVE =
belly is RICH.  Matches the sovereign_bonds/butterfly tool's sign
convention (which uses the doubled form in BPS).  All three endpoints
arrive from the level primitive in PERCENT; this tool keeps the
butterfly in PERCENT (same units as the underlying) — distinct from
the BPS convention the sovereign butterfly uses, because real yields
are NOT multiplied by 100.  Daily / weekly / monthly *changes* of the
butterfly are reported in BPS, matching the desk convention for
change reporting on any rate level.

Same-country / same-curve-family invariant
------------------------------------------
All three legs share a single linker ``curve_family`` (e.g.
``USD_TIPS``).  Cross-curve combinations are not expressible by this
primitive's input shape; the compute layer additionally re-asserts
that the resolved instrument_master rows for that curve_family map to
``instrument_type='inflation_linker'`` AND a single ``(country,
currency)`` tuple, surfacing a controlled error envelope on mismatch.

Validation layering
-------------------
- ``field_name`` defaults to ``None`` — the sentinel that resolves
  against the YAML's ``default_field_name`` convention.  Mirrors the
  empty-string / None-sentinel pattern used by every other rates tool.

- ``lookback_days`` controls the displayed window only (the bespoke
  ``time_series`` row count and the canonical TimeSeries length).
  Does NOT control the rolling z-score window or the trailing range
  window — those are independent conventions in ``config.yaml``.

- ``high_252d_pct`` / ``low_252d_pct`` / ``percentile_252d`` field
  names embed the trailing-range window length and are wire-frozen in
  V1.  See ``config.yaml``'s ``planned_extensions`` for the path to
  making the trailing window configurable.

Cross-field invariants stay here in code:
``_tenors_must_all_differ`` enforces three distinct tenors.
``_short_belly_long_strictly_ordered`` enforces
``short_years < belly_years < long_years`` computed via the shared
``tenor_to_years`` parser — so e.g. ``short='10Y', belly='5Y',
long='2Y'`` is rejected with a precise error message.  These are
structural inputs, not YAML knobs.

The same-curve / same-country / same-instrument-type invariant is
enforced at compute time by a local helper that queries
``macro_data.instrument_master`` for the resolved ``(country,
currency)`` tuple under ``instrument_type='inflation_linker'``
(re-asserting a single tuple per curve_family is the load-bearing
defensive guard against an instrument_master inconsistency).  See
``rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly.compute._enforce_same_curve_family_identity``
for the guard and the controlled ``{"error": ...}`` envelope it emits
on failure.

Canonical TimeSeries output
---------------------------
This tool emits TWO canonical
``shared.schemas.time_series.TimeSeries`` payloads alongside the
bespoke ``time_series`` list:

  - ``time_series_butterfly`` (units = PERCENT) — the real-yield
    butterfly series over the displayed window.  series_name pattern:
    ``<curve_family_lower>_<short_lower>_<belly_lower>_<long_lower>_real_yield_butterfly``.

  - ``time_series_zscore`` (units = Z_SCORE) — the rolling z-score of
    the butterfly series (computed on the percent-units butterfly)
    over the displayed window.  series_name pattern:
    ``<curve_family_lower>_<short_lower>_<belly_lower>_<long_lower>_real_yield_butterfly_zscore``.

All three (bespoke + two canonical) share the same display DataFrame
so they cannot drift.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.analytics.curve_bootstrap import tenor_to_years
from shared.schemas import TimeSeries


class RealYieldButterflyInput(BaseModel):
    """Parameters for computing a same-country linker real-yield
    butterfly (curvature) on a single sovereign linker curve.

    butterfly_pct = belly_real_yield_pct
                  - 0.5 * (short_real_yield_pct + long_real_yield_pct)

    Same-curve invariant
    --------------------
    The three legs share a single linker ``curve_family`` (e.g.
    ``USD_TIPS``).  Cross-curve combinations are not expressible by
    this primitive's input shape.  Compute additionally re-asserts
    that the resolved instrument_master rows for this curve_family map
    to ``instrument_type='inflation_linker'`` AND a single
    ``(country, currency)`` tuple — refused with a controlled error
    envelope otherwise.
    """

    model_config = ConfigDict(extra="forbid")

    curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "Linker curve family identifier exactly as stored in "
            "instrument_master.  Examples: 'USD_TIPS' (US TIPS), "
            "'GBP_LINKER' (UK inflation-linked Gilts), "
            "'EUR_FR_LINKER' (France OATi/OATei), 'CAD_RRB' "
            "(Canadian Real Return Bonds).  See "
            "rates_agent/playbooks/inflation_indexed_bonds.yml for "
            "the ingested universe.  Same-curve invariant: this "
            "single field is shared by all three endpoints; cross-"
            "curve real-yield butterflies are not expressible by "
            "this primitive."
        ),
    )
    short_tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Short wing tenor of the real-yield butterfly (e.g. '2Y' "
            "for 2s5s10s, '5Y' for 5s10s30s).  Must be a supported "
            "pillar on this linker curve_family — see "
            "rates_agent/playbooks/inflation_indexed_bonds.yml for "
            "the ingested grid (USD_TIPS exposes 5Y/10Y/20Y/30Y; "
            "GBP_LINKER exposes 1Y..50Y; EUR_FR_LINKER exposes "
            "2Y/5Y/7Y/10Y/15Y; CAD_RRB exposes 5Y..30Y).  Must map "
            "to a strictly smaller year fraction than "
            "``belly_tenor`` (validated via "
            "shared.analytics.curve_bootstrap.tenor_to_years)."
        ),
    )
    belly_tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Belly (body) tenor of the real-yield butterfly (e.g. "
            "'5Y' for 2s5s10s, '10Y' for 5s10s30s).  Must be a "
            "supported pillar on this linker curve_family and must "
            "map to a year fraction strictly between ``short_tenor`` "
            "and ``long_tenor``."
        ),
    )
    long_tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Long wing tenor of the real-yield butterfly (e.g. '10Y' "
            "for 2s5s10s, '30Y' for 5s10s30s).  Must be a supported "
            "pillar on this linker curve_family and must map to a "
            "strictly larger year fraction than ``belly_tenor``."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of *displayed* history.  Does NOT control "
            "the rolling z-score window (config: z_score_window_days, "
            "currently 252) or the trailing range window (config: "
            "trailing_range_window_days, locked at 252 in V1)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field used for ALL THREE endpoint "
            "real-yield series (short + belly + long).  When None "
            "(default), the tool falls through to "
            "``default_field_name`` from config.yaml (currently "
            "'YLD_YTM_MID' — the linker real-yield-to-maturity "
            "mnemonic).  Pass an explicit field name to override per "
            "query.  LLM/HTTP wrappers MUST translate their wire-"
            "level sentinel (empty string for MCP, missing param for "
            "FastAPI) to None before constructing this input — "
            "otherwise the YAML default is silently shadowed.  See "
            "the curve_move_classifier wrapper-shadowing fix (commit "
            "b2605ee) for the canonical pattern."
        ),
    )

    @model_validator(mode="after")
    def _tenors_must_all_differ(self) -> "RealYieldButterflyInput":
        tenors = [self.short_tenor, self.belly_tenor, self.long_tenor]
        if len(set(tenors)) != 3:
            raise ValueError(
                f"short_tenor, belly_tenor, and long_tenor must all "
                f"be different, but got {tenors}.  A real-yield "
                "butterfly requires three distinct endpoint tenors."
            )
        return self

    @model_validator(mode="after")
    def _short_belly_long_strictly_ordered(
        self,
    ) -> "RealYieldButterflyInput":
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
                "real-yield butterfly cannot be inverted; pass the "
                "shortest tenor as ``short_tenor``, the middle as "
                "``belly_tenor``, and the longest as ``long_tenor``."
            )
        return self


class RealYieldButterflyCurrentMetrics(BaseModel):
    """Snapshot metrics for a same-country linker real-yield butterfly.

    Carries both the butterfly (in PERCENT) and the three endpoint
    real yields + year fractions + component wing spreads used to form
    it, so the desk can sanity-check the decomposition end-to-end
    without a second tool call.

    The ``methodology_label`` field carries the wire-honesty
    disclosure sourced from the YAML's ``methodology.what_it_does`` —
    NOT a hardcoded Python literal — so a YAML edit to the disclosure
    flows through to runtime.  ``butterfly_label`` is a desk-friendly
    identifier like ``'USD_TIPS 5s10s30s real-yield'``.
    """

    as_of_date: str = Field(
        ..., description="Most recent aligned trade date (YYYY-MM-DD).",
    )
    curve_family: str
    short_tenor: str
    belly_tenor: str
    long_tenor: str
    butterfly_label: str = Field(
        ...,
        description=(
            "Human-readable label, e.g. 'USD_TIPS 5s10s30s real-"
            "yield' or 'GBP_LINKER 2s10s30s real-yield'."
        ),
    )
    current_butterfly_pct: Optional[float] = Field(
        None,
        description=(
            "Current real-yield butterfly in PERCENT "
            "(belly - 0.5*(short + long)).  Curvature of the REAL-"
            "YIELD curve — distinct from a real-yield curve spread "
            "(2-point difference) and from a nominal sovereign "
            "butterfly (3-point curvature of nominal yields, "
            "reported in BPS).  Sign convention: POSITIVE = belly "
            "is CHEAP versus the half-weighted wings; NEGATIVE = "
            "belly is RICH.  Can be negative across parts of the "
            "post-2008 / 2020-21 history."
        ),
    )
    daily_change_bps: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in the real-yield butterfly "
            "(BPS).  Daily change of a percent-units butterfly is "
            "reported in bps per desk convention."
        ),
    )
    weekly_change_bps: Optional[float] = Field(
        None,
        description=(
            "5-trading-day change in the real-yield butterfly (BPS)."
        ),
    )
    monthly_change_bps: Optional[float] = Field(
        None,
        description=(
            "22-trading-day (~1 month) change in the real-yield "
            "butterfly (BPS)."
        ),
    )
    current_z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the real-yield "
            "butterfly (in PERCENT) vs its own trailing window."
        ),
    )
    rolling_window_days: int = Field(
        ..., description="Window used for the z-score calculation.",
    )
    high_252d_pct: Optional[float] = Field(
        None,
        description=(
            "Highest real-yield butterfly over trailing 252 trading "
            "days (PERCENT)."
        ),
    )
    low_252d_pct: Optional[float] = Field(
        None,
        description=(
            "Lowest real-yield butterfly over trailing 252 trading "
            "days (PERCENT)."
        ),
    )
    percentile_252d: Optional[float] = Field(
        None,
        description=(
            "Percentile rank within trailing 252-day range (0-100)."
        ),
    )
    wing_short_pct: Optional[float] = Field(
        None,
        description=(
            "Component spread: belly_real_yield_pct - "
            "short_real_yield_pct (PERCENT)."
        ),
    )
    wing_long_pct: Optional[float] = Field(
        None,
        description=(
            "Component spread: long_real_yield_pct - "
            "belly_real_yield_pct (PERCENT)."
        ),
    )
    short_real_yield_pct: Optional[float] = Field(
        None,
        description=(
            "Latest real yield (in PERCENT) at ``short_tenor`` — "
            "exposed so the desk can audit the butterfly "
            "decomposition."
        ),
    )
    belly_real_yield_pct: Optional[float] = Field(
        None,
        description=(
            "Latest real yield (in PERCENT) at ``belly_tenor``."
        ),
    )
    long_real_yield_pct: Optional[float] = Field(
        None,
        description=(
            "Latest real yield (in PERCENT) at ``long_tenor``."
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
    country: str = Field(
        ...,
        description=(
            "Country identifier resolved from instrument_master "
            "(e.g. 'US', 'UK', 'France', 'Canada').  Surfaced on "
            "the wire so a desk reader can confirm the resolved "
            "linker identity without a second tool call."
        ),
    )
    currency: str = Field(
        ...,
        description=(
            "Currency identifier resolved from instrument_master "
            "(e.g. 'USD', 'GBP', 'EUR', 'CAD').  Shared by all "
            "three endpoints by the same-curve invariant."
        ),
    )
    methodology_label: str = Field(
        ...,
        description=(
            "Wire-honesty disclosure threaded from the YAML's "
            "``methodology.what_it_does``.  Carries the explicit "
            "butterfly formula (belly - 0.5*(short+long)) AND the "
            "curvature-of-real-yields framing so downstream "
            "operators and the LLM cannot misread the output's "
            "sign convention."
        ),
    )


class RealYieldButterflyTimeSeriesRow(BaseModel):
    """Single row in the bespoke real-yield-butterfly time-series.

    ``butterfly_pct`` is the real-yield butterfly in PERCENT;
    ``z_score`` is the rolling z-score (None for rows in the rolling-
    window warmup).
    """

    date: str
    butterfly_pct: float
    z_score: Optional[float] = None


class RealYieldButterflyOutput(BaseModel):
    """Top-level response for the real_yield_butterfly tool.

    ``time_series`` is the bespoke wire-frozen shape
    (``RealYieldButterflyTimeSeriesRow``) for callers that want
    butterfly + z-score in one row.

    ``time_series_butterfly`` and ``time_series_zscore`` are the
    canonical closed-enum shapes the workflow / operator layer
    consumes.  All three are computed from the same underlying display
    DataFrame — they cannot drift.
    """

    current_metrics: RealYieldButterflyCurrentMetrics
    time_series: List[RealYieldButterflyTimeSeriesRow]
    time_series_butterfly: TimeSeries = Field(
        ...,
        description=(
            "Historical same-country linker real-yield butterfly "
            "(in PERCENT) over the displayed window.  Closed-enum "
            "``TimeSeriesUnits.PERCENT``; series_name pattern: "
            "'<curve_family>_<short>_<belly>_<long>_real_yield_butterfly'.  "
            "Values match ``time_series[i].butterfly_pct`` 1-to-1 "
            "by construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the real-yield butterfly "
            "(in PERCENT) vs its own trailing window.  Closed-enum "
            "``TimeSeriesUnits.Z_SCORE``; series_name pattern: "
            "'<curve_family>_<short>_<belly>_<long>_real_yield_butterfly_zscore'.  "
            "Values match ``time_series[i].z_score`` 1-to-1 (None "
            "for rows in the rolling-window warmup)."
        ),
    )


__all__ = [
    "RealYieldButterflyInput",
    "RealYieldButterflyCurrentMetrics",
    "RealYieldButterflyTimeSeriesRow",
    "RealYieldButterflyOutput",
]
