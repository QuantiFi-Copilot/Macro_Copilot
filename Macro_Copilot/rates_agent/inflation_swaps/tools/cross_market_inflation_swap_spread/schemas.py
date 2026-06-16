"""Pydantic schemas for the cross_market_inflation_swap_spread tool.

Fourth tool in the ``inflation_swaps`` domain.  Owns the desk
concept of a *same-tenor cross-market zero-coupon inflation swap
(ZCIS) spread* between two ZCIS curve families at the same pillar
(e.g. USD_ZCIS 5Y minus EUR_ZCIS 5Y, USD_ZCIS 10Y minus
GBP_ZCIS 10Y, EUR_ZCIS 5Y minus GBP_ZCIS 5Y).  Composed from two
``inflation_swap_rate_level`` calls (one per leg) into a per-trade-
date difference.

Construction rule
-----------------
``spread_pct = leg_a_pct - leg_b_pct``
``spread_bps = spread_pct * 100``

The endpoint reads come back from the level primitive in PERCENT;
this tool reports the cross-market spread in BOTH percent (the
natural unit since both legs are PERCENT-quoted ZCIS rates) and bps
(for desk-display alignment with sibling ZCIS spread tools).

Cross-market invariant
----------------------
The two legs MUST share a single ``tenor`` and MUST have distinct
``curve_family`` values.  Same-curve, two-tenor spreads
(e.g. USD_ZCIS 5Y vs USD_ZCIS 10Y) belong to
``inflation_swap_curve_spread`` and are rejected here at the schema
layer (``leg_a_curve_family != leg_b_curve_family``).

Sign convention
---------------
``spread = leg_a_pct - leg_b_pct`` — left minus right.  The LEFT
leg (``leg_a_curve_family``) is the numerator and the RIGHT leg
(``leg_b_curve_family``) is the denominator, so the spread sign is
predictable from the input ordering.  The convention is also
documented in YAML's ``cross_market_sign_convention`` for
auditability.

INDEX-FAMILY CAVEAT (load-bearing)
----------------------------------
USD_ZCIS, EUR_ZCIS, and GBP_ZCIS reference DIFFERENT inflation
indices (US CPI-U / Eurozone HICP-xT / UK RPI), so this spread
captures BOTH inflation-expectation differentials AND structural
index-family differences; it is NOT a clean expected-inflation
divergence.  ``CrossMarketInflationSwapSpreadCurrentMetrics``
surfaces each leg's ``inflation_index_family`` / ``index_lag`` /
``interpolation`` / ``underlying_index`` separately on the wire so
the desk reader can decompose the spread without a second tool
call.  ``methodology_label`` is sourced from the YAML's
``methodology.what_it_does`` at runtime so the caveat cannot be
lost in a refactor.

Validation layering
-------------------
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
- ``_curves_must_differ`` enforces ``leg_a_curve_family !=
  leg_b_curve_family`` (same-curve spreads belong to
  ``inflation_swap_curve_spread``).
- ``_tenor_must_parse`` enforces ``tenor`` parses successfully via
  ``shared.analytics.curve_bootstrap.tenor_to_years`` so an unknown
  pillar is rejected at the schema layer with a precise error
  rather than producing a downstream compute failure.

Canonical TimeSeries output
---------------------------
This tool emits TWO canonical
``shared.schemas.time_series.TimeSeries`` payloads alongside the
bespoke ``time_series`` list:

  - ``time_series_spread`` (units = BPS) — the cross-market ZCIS
    spread series over the displayed window.  Cross-market ZCIS
    spreads are spread objects in BPS (mirrors the sovereign
    cross_market_spread convention; do NOT ship in PERCENT — that's
    the level convention).  series_name pattern:
    ``<leg_a_lower>_<leg_b_lower>_<tenor_lower>_zcis_cross_market_spread``.

  - ``time_series_zscore`` (units = Z_SCORE) — the rolling z-score
    of the cross-market ZCIS spread (in BPS) over the displayed
    window.  series_name pattern:
    ``<leg_a_lower>_<leg_b_lower>_<tenor_lower>_zcis_cross_market_spread_zscore``.

All three (bespoke + two canonical) share the same display
DataFrame so they cannot drift.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.analytics.curve_bootstrap import tenor_to_years
from shared.schemas import TimeSeries


class CrossMarketInflationSwapSpreadInput(BaseModel):
    """Parameters for computing a same-tenor cross-market ZCIS spread.

    spread_pct = leg_a_pct - leg_b_pct
    spread_bps = spread_pct * 100

    Cross-market invariant
    ----------------------
    The two legs MUST have distinct ``curve_family`` values.
    Same-curve, two-tenor spreads belong to
    ``inflation_swap_curve_spread`` (rejected here at the schema
    layer).
    """

    model_config = ConfigDict(extra="forbid")

    leg_a_curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "Left (numerator) inflation-swap curve family identifier "
            "exactly as stored in instrument_master.  Examples: "
            "'USD_ZCIS' (US CPI-U zero-coupon inflation swaps), "
            "'EUR_ZCIS' (euro-area HICP ex-tobacco ZCIS), 'GBP_ZCIS' "
            "(UK RPI ZCIS).  See "
            "rates_agent/playbooks/inflation_swaps.yml for the "
            "ingested universe.  Cross-market invariant: must differ "
            "from ``leg_b_curve_family``; same-curve spreads belong "
            "to ``inflation_swap_curve_spread``."
        ),
    )
    leg_b_curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "Right (denominator) inflation-swap curve family "
            "identifier.  Spread direction: leg_a_pct - leg_b_pct."
        ),
    )
    tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Single tenor pillar shared by both legs (e.g. '2Y', "
            "'5Y', '10Y', '30Y').  Must be a supported pillar on "
            "BOTH ``leg_a_curve_family`` and ``leg_b_curve_family`` "
            "— the current ingested grid is 1Y / 2Y / 3Y / 5Y / "
            "10Y / 20Y / 30Y on each of USD_ZCIS / EUR_ZCIS / "
            "GBP_ZCIS.  Validated at the schema layer via "
            "``shared.analytics.curve_bootstrap.tenor_to_years`` so "
            "unknown pillars (e.g. '11Y') are rejected with a precise "
            "error before any compute work."
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
            "ZCIS rate series (leg_a + leg_b).  When None (default), "
            "the tool falls through to ``default_zcis_rate_field`` "
            "from config.yaml (currently 'PX_MID' — the canonical "
            "mid quoted ZCIS rate Bloomberg publishes for inflation "
            "swaps).  Pass an explicit field name to override per "
            "query; the same value is threaded into BOTH inner "
            "``calculate_inflation_swap_rate_level`` calls so the "
            "two legs are read off the same Bloomberg field by "
            "construction.  Empty string '' is coerced to None at "
            "the schema layer so MCP/HTTP wrappers can pass their "
            "wire-level sentinel through unchanged — None then falls "
            "through to the YAML default.  See the curve_move_classifier "
            "wrapper-shadowing fix (commit b2605ee) for the canonical "
            "pattern."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=("Optional as-of date (YYYY-MM-DD): compute as of this trade "
                     "date instead of the latest available data.  None → latest "
                     "(live snapshot).  Supply a date for a historical, replayable view."),
    )

    @field_validator("field_name", mode="before")
    @classmethod
    def _coerce_empty_field_name(cls, v):
        """Coerce empty / whitespace-only strings to None so the
        YAML default ``default_zcis_rate_field`` resolves through
        compute even when a wrapper forwards its empty-string
        sentinel directly into the schema.
        """
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @model_validator(mode="after")
    def _curves_must_differ(
        self,
    ) -> "CrossMarketInflationSwapSpreadInput":
        if self.leg_a_curve_family == self.leg_b_curve_family:
            raise ValueError(
                f"leg_a_curve_family and leg_b_curve_family must be "
                f"different, but both are "
                f"'{self.leg_a_curve_family}'.  A cross-market ZCIS "
                "spread requires two distinct curve families.  For "
                "same-curve, two-tenor ZCIS spreads, use the "
                "``inflation_swap_curve_spread`` primitive instead."
            )
        return self

    @model_validator(mode="after")
    def _tenor_must_parse(
        self,
    ) -> "CrossMarketInflationSwapSpreadInput":
        try:
            _ = tenor_to_years(self.tenor)
        except ValueError as exc:
            raise ValueError(
                f"Could not parse tenor '{self.tenor}' for cross-"
                f"market ZCIS spread "
                f"({self.leg_a_curve_family} - "
                f"{self.leg_b_curve_family}): {exc}.  Accepted forms "
                "are '<n>W', '<n>M', '<n>Y' (the current ingested "
                "ZCIS grid is 1Y / 2Y / 3Y / 5Y / 10Y / 20Y / 30Y "
                "on each of USD_ZCIS / EUR_ZCIS / GBP_ZCIS)."
            ) from exc
        return self


class CrossMarketInflationSwapSpreadCurrentMetrics(BaseModel):
    """Snapshot metrics for a same-tenor cross-market ZCIS spread.

    Carries the cross-market spread (in pct AND bps) PLUS each
    leg's load-bearing reference metadata (per-leg
    ``inflation_index_family`` / ``index_lag`` / ``interpolation``
    / ``underlying_index``) so the desk reader can interpret the
    spread under the index-family caveat without a second tool call.

    The ``methodology_label`` field carries the wire-honesty
    disclosure sourced from the YAML's
    ``methodology.what_it_does`` — NOT a hardcoded Python literal —
    so a YAML edit to the disclosure flows through to runtime.
    """

    as_of_date: str = Field(
        ..., description="Most recent aligned trade date (YYYY-MM-DD).",
    )
    leg_a_curve_family: str
    leg_b_curve_family: str
    tenor: str
    tenor_years: float = Field(
        ...,
        description=(
            "Year fraction of ``tenor`` (e.g. 5.0 for '5Y'), parsed "
            "via ``shared.analytics.curve_bootstrap.tenor_to_years``."
        ),
    )
    spread_label: str = Field(
        ...,
        description=(
            "Human-readable label, e.g. 'USD_ZCIS-EUR_ZCIS 5Y'."
        ),
    )
    spread_pct: float = Field(
        ...,
        description=(
            "Current cross-market ZCIS spread in PERCENT "
            "(leg_a_pct - leg_b_pct).  Reported alongside the BPS "
            "form so callers can choose the unit that matches the "
            "neighbouring tool surface."
        ),
    )
    spread_bps: float = Field(
        ...,
        description=(
            "Current cross-market ZCIS spread in basis points "
            "(spread_pct * 100)."
        ),
    )
    change_1d_bps: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in the cross-market ZCIS spread "
            "(bps)."
        ),
    )
    change_1w_bps: Optional[float] = Field(
        None,
        description=(
            "5-trading-day change in the cross-market ZCIS spread "
            "(bps)."
        ),
    )
    change_1m_bps: Optional[float] = Field(
        None,
        description=(
            "22-trading-day (~1 month) change in the cross-market "
            "ZCIS spread (bps)."
        ),
    )
    z_score_252d: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the cross-market "
            "ZCIS spread (in bps) vs its own trailing window."
        ),
    )
    high_252d_bps: Optional[float] = Field(
        None,
        description=(
            "Highest cross-market ZCIS spread over trailing 252 "
            "trading days (bps)."
        ),
    )
    low_252d_bps: Optional[float] = Field(
        None,
        description=(
            "Lowest cross-market ZCIS spread over trailing 252 "
            "trading days (bps)."
        ),
    )
    percentile_252d: Optional[float] = Field(
        None,
        description=(
            "Percentile rank within trailing 252-day range (0-100)."
        ),
    )
    leg_a_pct: Optional[float] = Field(
        None,
        description=(
            "Latest leg_a ZCIS rate in percent — exposed so the desk "
            "can audit the cross-market decomposition end-to-end."
        ),
    )
    leg_b_pct: Optional[float] = Field(
        None,
        description=(
            "Latest leg_b ZCIS rate in percent."
        ),
    )
    observation_count: int = Field(
        ...,
        description=(
            "Number of trading days in the displayed lookback_days "
            "window after inner-join alignment."
        ),
    )
    leg_a_inflation_index_family: str = Field(
        ...,
        description=(
            "Inflation index family the LEFT leg's ZCIS curve "
            "references.  Examples: 'US_CPI_URBAN' (USD_ZCIS), "
            "'EU_HICP' (EUR_ZCIS), 'UK_RPI' (GBP_ZCIS).  Surfaced "
            "per-leg on the wire so the index-family caveat is "
            "visible — this spread captures BOTH inflation-"
            "expectation differentials AND structural index-family "
            "differences."
        ),
    )
    leg_b_inflation_index_family: str = Field(
        ...,
        description=(
            "Inflation index family the RIGHT leg's ZCIS curve "
            "references."
        ),
    )
    index_families_match: bool = Field(
        ...,
        description=(
            "Derived top-level summary of the per-leg index-family "
            "comparison.  True iff "
            "``leg_a_inflation_index_family == "
            "leg_b_inflation_index_family`` AND both per-leg values "
            "are non-empty.  False otherwise — including the common "
            "case where the two legs reference different inflation "
            "indices (e.g. USD_ZCIS / EUR_ZCIS / GBP_ZCIS each "
            "reference distinct indices, so this flag is False on "
            "every cross-market pair drawn from the V1 universe).  "
            "Surfaced at top-level on current_metrics so a desk reader "
            "can read the load-bearing index-family caveat from one "
            "field rather than reconciling two per-leg strings."
        ),
    )
    index_family_caveat: Optional[str] = Field(
        None,
        description=(
            "Human-readable one-sentence caveat the wire surfaces "
            "when ``index_families_match`` is False.  Names BOTH "
            "leg's index families verbatim and warns that the spread "
            "mixes inflation-compensation regimes (i.e. the spread "
            "captures BOTH inflation-expectation differentials AND "
            "structural index-family differences; it is NOT a clean "
            "expected-inflation divergence).  None when "
            "``index_families_match`` is True."
        ),
    )
    leg_a_index_lag: str = Field(
        ...,
        description=(
            "Indexation lag the LEFT leg's ZCIS curve references "
            "(e.g. '3M' for USD_ZCIS / EUR_ZCIS, '2M' for "
            "GBP_ZCIS).  Different lags mean the rates are quoted "
            "against differently dated index fixings."
        ),
    )
    leg_b_index_lag: str = Field(
        ...,
        description=(
            "Indexation lag the RIGHT leg's ZCIS curve references."
        ),
    )
    leg_a_interpolation: str = Field(
        ...,
        description=(
            "Index-fixing interpolation convention the LEFT leg's "
            "ZCIS curve uses (e.g. 'Daily' for USD_ZCIS, 'Monthly' "
            "for EUR_ZCIS / GBP_ZCIS)."
        ),
    )
    leg_b_interpolation: str = Field(
        ...,
        description=(
            "Index-fixing interpolation convention the RIGHT leg's "
            "ZCIS curve uses."
        ),
    )
    leg_a_underlying_index: Optional[str] = Field(
        None,
        description=(
            "Underlying inflation index Bloomberg ticker for the "
            "LEFT leg.  Examples: 'CPURNSA Index' (USD_ZCIS), "
            "'CPTFEMU Index' (EUR_ZCIS), 'UKRPI Index' (GBP_ZCIS)."
        ),
    )
    leg_b_underlying_index: Optional[str] = Field(
        None,
        description=(
            "Underlying inflation index Bloomberg ticker for the "
            "RIGHT leg."
        ),
    )
    methodology_label: str = Field(
        ...,
        description=(
            "Wire-honesty disclosure threaded from the YAML's "
            "``methodology.what_it_does``.  Spells out the spread "
            "formula, composition pattern, alignment discipline, "
            "the LOAD-BEARING index-family caveat (USD_ZCIS / "
            "EUR_ZCIS / GBP_ZCIS reference different inflation "
            "indices, so this is NOT a clean expected-inflation "
            "divergence), AND the sign convention so downstream "
            "operators and the LLM cannot misread the output."
        ),
    )


class CrossMarketInflationSwapSpreadTimeSeriesRow(BaseModel):
    """Single row in the bespoke cross-market ZCIS spread time-series.

    ``spread_pct`` is the cross-market ZCIS spread in percent;
    ``spread_bps`` is the same value in basis points
    (= spread_pct * 100); ``leg_a_pct`` and ``leg_b_pct`` are the
    per-leg ZCIS rates used to form the spread.
    """

    date: str
    spread_pct: float
    spread_bps: float
    leg_a_pct: float
    leg_b_pct: float


class CrossMarketInflationSwapSpreadOutput(BaseModel):
    """Top-level response for the cross_market_inflation_swap_spread
    tool.

    ``time_series`` is the bespoke wire-frozen shape
    (``CrossMarketInflationSwapSpreadTimeSeriesRow``) for callers
    that want spread + each leg in one row.

    ``time_series_spread`` and ``time_series_zscore`` are the
    canonical closed-enum shapes the workflow / operator layer
    consumes.  Cross-market ZCIS spreads are spread objects in BPS
    (mirrors the sovereign cross_market_spread convention; do NOT
    ship in PERCENT — that's the level convention).  All three are
    computed from the same underlying display DataFrame — they
    cannot drift.
    """

    current_metrics: CrossMarketInflationSwapSpreadCurrentMetrics
    time_series: List[CrossMarketInflationSwapSpreadTimeSeriesRow]
    time_series_spread: TimeSeries = Field(
        ...,
        description=(
            "Historical same-tenor cross-market ZCIS spread (in "
            "BPS) over the displayed window.  Closed-enum "
            "``TimeSeriesUnits.BPS``; series_name pattern: "
            "'<leg_a_lower>_<leg_b_lower>_<tenor_lower>_"
            "zcis_cross_market_spread'.  Values match "
            "``time_series[i].spread_bps`` 1-to-1 by construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the cross-market ZCIS "
            "spread (in bps) vs its own trailing window.  Closed-"
            "enum ``TimeSeriesUnits.Z_SCORE``; series_name pattern: "
            "'<leg_a_lower>_<leg_b_lower>_<tenor_lower>_"
            "zcis_cross_market_spread_zscore'.  None for rows in "
            "the rolling-window warmup."
        ),
    )


__all__ = [
    "CrossMarketInflationSwapSpreadInput",
    "CrossMarketInflationSwapSpreadCurrentMetrics",
    "CrossMarketInflationSwapSpreadTimeSeriesRow",
    "CrossMarketInflationSwapSpreadOutput",
]
