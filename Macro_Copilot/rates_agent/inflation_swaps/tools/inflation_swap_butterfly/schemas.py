"""Pydantic schemas for the inflation_swap_butterfly tool.

Owns the desk concept of a *same-curve zero-coupon inflation swap
(ZCIS) butterfly* (3-point ZCIS curve curvature) — the 3-point
curvature on a SINGLE ZCIS curve family (e.g. USD_ZCIS 5s10s30s,
EUR_ZCIS 2s5s10s, GBP_ZCIS 2s10s30s).  Composed from three
``calculate_inflation_swap_rate_level`` calls (one per endpoint
tenor) into a per-trade-date FIXED simple-butterfly weighting.

Construction rule
-----------------
``butterfly_bps = (belly_zcis_pct
                   - 0.5 * (short_zcis_pct + long_zcis_pct)) * 100``

Equivalent to ``0.5 * (2 * belly - short - long) * 100``.  Sign
convention: POSITIVE = belly is CHEAP versus the half-weighted wings
(i.e. belly ZCIS rate is HIGH relative to the wings); NEGATIVE =
belly is RICH.  Matches the sovereign_bonds/butterfly tool's, the
inflation_indexed_bonds/real_yield_butterfly tool's, and the
inflation_indexed_bonds/breakeven_butterfly tool's sign convention.
All three endpoints arrive from the inflation_swap_rate_level
primitive in PERCENT; this tool keeps the butterfly in BPS (the BPS
output convention the inflation_swaps domain established via
``inflation_swap_curve_spread`` for curve-shape views).  Daily /
weekly / monthly *changes* of the butterfly are also reported in BPS
(already-bps subtraction).

Same-curve invariant
--------------------
All three endpoints share a single ``curve_family`` (e.g.
``USD_ZCIS``).  Cross-curve butterflies (e.g. mixing USD_ZCIS,
EUR_ZCIS, and GBP_ZCIS pillars in a 3-point curvature) are forbidden
by this primitive's input shape.  The compute layer additionally
re-asserts that the three resolved endpoint reads share
``inflation_index_family``, ``index_lag``, ``interpolation``, and
``underlying_index``, surfacing a controlled error envelope on
mismatch.

Raw inflation-swap-rate space
-----------------------------
The arithmetic is performed on raw ZCIS par rates only — NO
subtraction of a model-derived basis, NO inflation-risk-premium
adjustment, and NO routing through a fitted curve object.  The wire-
locked promise is surfaced via ``current_metrics.methodology_label``
so a downstream operator cannot misread the output as a cleaner
expected-inflation curvature.

Validation layering
-------------------
- ``field_name`` defaults to ``None`` — the sentinel that resolves
  against the YAML's ``default_zcis_rate_field`` convention.  Mirrors
  the empty-string / None-sentinel pattern used by every other rates
  tool.

- ``lookback_days`` controls the displayed window only (the bespoke
  ``time_series`` row count and the canonical TimeSeries length).
  Does NOT control the rolling z-score window or the trailing range
  window — those are independent conventions in ``config.yaml``.

- ``high_252d_bps`` / ``low_252d_bps`` / ``percentile_252d`` field
  names embed the trailing-range window length and are wire-frozen
  in V1.  See ``config.yaml``'s ``planned_extensions`` for the path
  to making the trailing window configurable.

Cross-field invariants stay here in code:
``_tenors_must_all_differ`` enforces three distinct tenors.
``_short_belly_long_strictly_ordered`` enforces
``short_years < belly_years < long_years`` computed via the shared
``tenor_to_years`` parser — so e.g. ``short='10Y', belly='5Y',
long='2Y'`` is rejected with a precise error message.  These are
structural inputs, not YAML knobs.

The *same-curve / same-reference-metadata* invariant is enforced at
compute time (it requires resolving instrument_master metadata from
the live DB read).  See
``rates_agent.inflation_swaps.tools.inflation_swap_butterfly.compute._assert_same_curve_reference_metadata``
for the guard and the controlled ``{"error": ...}`` envelope it
emits on mismatch.

Reference-metadata wire surface (load-bearing)
----------------------------------------------
``InflationSwapButterflyCurrentMetrics`` carries
``inflation_index_family`` (e.g. ``US_CPI_URBAN`` / ``EU_HICP`` /
``UK_RPI``), ``index_lag``, ``interpolation``, and
``underlying_index`` — the same fields the level primitive
surfaces for a single pillar.  By the same-curve invariant all three
legs share these attributes; the compute layer re-asserts the match
and refuses to emit a snapshot if the three endpoints disagree.

Canonical TimeSeries output
---------------------------
This tool emits TWO canonical
``shared.schemas.time_series.TimeSeries`` payloads alongside the
bespoke ``time_series`` list:

  - ``time_series_butterfly`` (units = BPS) — the ZCIS butterfly
    series over the displayed window.  series_name pattern:
    ``<curve_family_lower>_<short_lower>_<belly_lower>_<long_lower>_inflation_swap_butterfly``.
    The ``_inflation_swap_butterfly`` suffix distinguishes from
    sovereign nominal butterflies (``_butterfly``), real-yield
    butterflies (``_real_yield_butterfly``), and breakeven
    butterflies (``_breakeven_butterfly``) when they end up in the
    same operator panel downstream.

  - ``time_series_zscore`` (units = Z_SCORE) — the rolling z-score
    of the butterfly series over the displayed window.  series_name
    pattern:
    ``<curve_family_lower>_<short_lower>_<belly_lower>_<long_lower>_inflation_swap_butterfly_zscore``.

All three (bespoke + two canonical) share the same display DataFrame
so they cannot drift.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.analytics.curve_bootstrap import tenor_to_years
from shared.schemas import TimeSeries


class InflationSwapButterflyInput(BaseModel):
    """Parameters for computing a same-curve ZCIS butterfly (3-point
    ZCIS curve curvature) between three pillars of the same ZCIS
    curve family.

    butterfly_bps = (belly_zcis_pct
                     - 0.5 * (short_zcis_pct + long_zcis_pct)) * 100

    Same-curve invariant
    --------------------
    All three endpoints share a single ``curve_family``.  Cross-
    curve butterflies are forbidden by this primitive's input shape.
    The compute layer additionally re-asserts that the three
    resolved endpoint reads share ``inflation_index_family``,
    ``index_lag``, ``interpolation``, and ``underlying_index`` —
    surfacing a controlled error envelope on mismatch (which would
    indicate a real instrument_master inconsistency at the
    underlying pillars, not a methodology choice).
    """

    model_config = ConfigDict(extra="forbid")

    curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "Inflation-swap curve family identifier exactly as "
            "stored in instrument_master.  Examples: 'USD_ZCIS' "
            "(US CPI-U zero-coupon inflation swaps), 'EUR_ZCIS' "
            "(euro-area HICP ex-tobacco ZCIS), 'GBP_ZCIS' (UK RPI "
            "ZCIS).  See rates_agent/playbooks/inflation_swaps.yml "
            "for the ingested universe.  Same-curve invariant: this "
            "single field is shared by all three endpoints; cross-"
            "curve butterflies are forbidden by this primitive's "
            "input shape."
        ),
    )
    short_tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Short wing tenor of the ZCIS butterfly (e.g. '2Y' for "
            "2s5s10s, '5Y' for 5s10s30s).  Must be a supported "
            "pillar on this curve_family — the current ingested "
            "grid is 1Y / 2Y / 3Y / 5Y / 10Y / 20Y / 30Y on each of "
            "USD_ZCIS / EUR_ZCIS / GBP_ZCIS.  Must map to a "
            "strictly smaller year fraction than ``belly_tenor`` "
            "(validated via "
            "shared.analytics.curve_bootstrap.tenor_to_years)."
        ),
    )
    belly_tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Belly (body) tenor of the ZCIS butterfly (e.g. '5Y' "
            "for 2s5s10s, '10Y' for 5s10s30s).  Must be a supported "
            "pillar on this curve_family and must map to a year "
            "fraction strictly between ``short_tenor`` and "
            "``long_tenor``."
        ),
    )
    long_tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Long wing tenor of the ZCIS butterfly (e.g. '10Y' for "
            "2s5s10s, '30Y' for 5s10s30s).  Must be a supported "
            "pillar on this curve_family and must map to a strictly "
            "larger year fraction than ``belly_tenor``."
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
            "Bloomberg observation field used for ALL THREE "
            "endpoint ZCIS rate series (short + belly + long).  "
            "When None (default), the tool falls through to "
            "``default_zcis_rate_field`` from config.yaml "
            "(currently 'PX_MID' — the canonical mid quoted ZCIS "
            "rate Bloomberg publishes for inflation swaps).  Pass "
            "an explicit field name to override per query.  "
            "LLM/HTTP wrappers MUST translate their wire-level "
            "sentinel (empty string for MCP, missing param for "
            "FastAPI) to None before constructing this input — "
            "otherwise the YAML default is silently shadowed.  See "
            "the curve_move_classifier wrapper-shadowing fix "
            "(commit b2605ee) for the canonical pattern."
        ),
    )

    @model_validator(mode="after")
    def _tenors_must_all_differ(
        self,
    ) -> "InflationSwapButterflyInput":
        tenors = [self.short_tenor, self.belly_tenor, self.long_tenor]
        if len(set(tenors)) != 3:
            raise ValueError(
                f"short_tenor, belly_tenor, and long_tenor must all "
                f"be different, but got {tenors}.  A ZCIS butterfly "
                "requires three distinct endpoint tenors."
            )
        return self

    @model_validator(mode="after")
    def _short_belly_long_strictly_ordered(
        self,
    ) -> "InflationSwapButterflyInput":
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
                "ZCIS butterfly cannot be inverted; pass the "
                "shortest tenor as ``short_tenor``, the middle as "
                "``belly_tenor``, and the longest as ``long_tenor``."
            )
        return self


class InflationSwapButterflyCurrentMetrics(BaseModel):
    """Snapshot metrics for a same-curve ZCIS butterfly.

    Carries both the butterfly (in BPS) and the three endpoint ZCIS
    rates + year fractions + component wing spreads used to form it,
    so the desk can sanity-check the decomposition end-to-end
    without a second tool call.  Also carries the load-bearing
    reference metadata (``inflation_index_family``, ``index_lag``,
    ``interpolation``, ``underlying_index``) shared by all three
    legs by the same-curve invariant.

    The ``methodology_label`` field carries the wire-honesty
    disclosure sourced from the YAML's ``methodology.what_it_does``
    — NOT a hardcoded Python literal — so a YAML edit to the
    disclosure flows through to runtime.  ``butterfly_label`` is a
    desk-friendly identifier like ``'USD_ZCIS 5s10s30s'``.
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
            "Human-readable label, e.g. 'USD_ZCIS 5s10s30s' or "
            "'EUR_ZCIS 2s5s10s'."
        ),
    )
    current_butterfly_bps: Optional[float] = Field(
        None,
        description=(
            "Current ZCIS butterfly in basis points "
            "((belly_zcis_pct - 0.5*(short_zcis_pct + "
            "long_zcis_pct)) * 100).  Curvature of the ZCIS rate "
            "curve in raw inflation-swap-rate space — distinct "
            "from a ZCIS curve spread (2-point difference), from "
            "a ZCIS forward (year-weighted forward of two "
            "endpoints), and from the linker bond-implied breakeven "
            "butterfly (3-point curvature of bond-implied "
            "breakevens).  Sign convention: POSITIVE = belly is "
            "CHEAP versus the half-weighted wings (i.e. belly ZCIS "
            "rate is HIGH relative to the wings); NEGATIVE = belly "
            "is RICH.  Raw-rate-space arithmetic — see "
            "``methodology_label`` for the no-basis / no-IRP-"
            "adjustment caveat."
        ),
    )
    daily_change_bps: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in the ZCIS butterfly (bps)."
        ),
    )
    weekly_change_bps: Optional[float] = Field(
        None,
        description=(
            "5-trading-day change in the ZCIS butterfly (bps)."
        ),
    )
    monthly_change_bps: Optional[float] = Field(
        None,
        description=(
            "22-trading-day (~1 month) change in the ZCIS butterfly "
            "(bps)."
        ),
    )
    current_z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the ZCIS butterfly "
            "(in bps) vs its own trailing window."
        ),
    )
    rolling_window_days: int = Field(
        ..., description="Window used for the z-score calculation.",
    )
    high_252d_bps: Optional[float] = Field(
        None,
        description=(
            "Highest ZCIS butterfly over trailing 252 trading days "
            "(bps)."
        ),
    )
    low_252d_bps: Optional[float] = Field(
        None,
        description=(
            "Lowest ZCIS butterfly over trailing 252 trading days "
            "(bps)."
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
            "Component spread (belly_zcis_pct - short_zcis_pct) * "
            "100 (bps)."
        ),
    )
    wing_long_bps: Optional[float] = Field(
        None,
        description=(
            "Component spread (long_zcis_pct - belly_zcis_pct) * "
            "100 (bps)."
        ),
    )
    short_zcis_rate_pct: Optional[float] = Field(
        None,
        description=(
            "Latest short-tenor ZCIS rate (percent) — exposed so "
            "the desk can audit the butterfly decomposition."
        ),
    )
    belly_zcis_rate_pct: Optional[float] = Field(
        None,
        description=(
            "Latest belly-tenor ZCIS rate (percent)."
        ),
    )
    long_zcis_rate_pct: Optional[float] = Field(
        None,
        description=(
            "Latest long-tenor ZCIS rate (percent)."
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
    inflation_index_family: str = Field(
        ...,
        description=(
            "Inflation index family the ZCIS curve references "
            "(shared by all three legs by the same-curve "
            "invariant).  Examples: 'US_CPI_URBAN' (USD_ZCIS), "
            "'EU_HICP' (EUR_ZCIS), 'UK_RPI' (GBP_ZCIS).  Surfaced "
            "on the wire so a desk reader can interpret the "
            "butterfly honestly."
        ),
    )
    index_lag: str = Field(
        ...,
        description=(
            "Indexation lag the ZCIS curve references (shared by "
            "all three legs).  Examples: '3M' (USD_ZCIS, "
            "EUR_ZCIS), '2M' (GBP_ZCIS)."
        ),
    )
    interpolation: str = Field(
        ...,
        description=(
            "Index-fixing interpolation convention the ZCIS curve "
            "uses (shared by all three legs).  Examples: 'Daily' "
            "(USD_ZCIS), 'Monthly' (EUR_ZCIS, GBP_ZCIS)."
        ),
    )
    underlying_index: Optional[str] = Field(
        None,
        description=(
            "Underlying inflation index Bloomberg ticker (shared "
            "by all three legs).  Examples: 'CPURNSA Index' "
            "(USD_ZCIS), 'CPTFEMU Index' (EUR_ZCIS), 'UKRPI Index' "
            "(GBP_ZCIS)."
        ),
    )
    methodology_label: str = Field(
        ...,
        description=(
            "Wire-honesty disclosure threaded from the YAML's "
            "``methodology.what_it_does``.  Spells out the FIXED "
            "simple-butterfly weight tuple ``(-0.5, +1.0, -0.5)``, "
            "the * 100 BPS conversion, the sign convention "
            "(POSITIVE = belly cheap), the raw-inflation-swap-rate"
            "-space promise (no basis subtraction, no IRP "
            "adjustment, no fitted curve), and the same-curve "
            "invariant — so downstream operators and the LLM "
            "cannot misread the output."
        ),
    )


class InflationSwapButterflyTimeSeriesRow(BaseModel):
    """Single row in the bespoke inflation-swap-butterfly time-series.

    ``butterfly_bps`` is the ZCIS butterfly in basis points;
    ``z_score`` is the rolling z-score (None for rows in the
    rolling-window warmup).
    """

    date: str
    butterfly_bps: float
    z_score: Optional[float] = None


class InflationSwapButterflyOutput(BaseModel):
    """Top-level response for the inflation_swap_butterfly tool.

    ``time_series`` is the bespoke wire-frozen shape
    (``InflationSwapButterflyTimeSeriesRow``) for callers that want
    butterfly + z-score in one row.

    ``time_series_butterfly`` and ``time_series_zscore`` are the
    canonical closed-enum shapes the workflow / operator layer
    consumes.  All three are computed from the same underlying
    display DataFrame — they cannot drift.
    """

    current_metrics: InflationSwapButterflyCurrentMetrics
    time_series: List[InflationSwapButterflyTimeSeriesRow]
    time_series_butterfly: TimeSeries = Field(
        ...,
        description=(
            "Historical same-curve ZCIS butterfly (in BPS) over the "
            "displayed window.  Closed-enum ``TimeSeriesUnits.BPS``;"
            " series_name pattern: "
            "'<curve_family>_<short>_<belly>_<long>_inflation_swap_butterfly'.  "
            "Values match ``time_series[i].butterfly_bps`` 1-to-1 "
            "by construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the ZCIS butterfly (in "
            "bps) vs its own trailing window.  Closed-enum "
            "``TimeSeriesUnits.Z_SCORE``; series_name pattern: "
            "'<curve_family>_<short>_<belly>_<long>_inflation_swap_butterfly_zscore'.  "
            "Values match ``time_series[i].z_score`` 1-to-1 (None "
            "for rows in the rolling-window warmup)."
        ),
    )


__all__ = [
    "InflationSwapButterflyInput",
    "InflationSwapButterflyCurrentMetrics",
    "InflationSwapButterflyTimeSeriesRow",
    "InflationSwapButterflyOutput",
]
