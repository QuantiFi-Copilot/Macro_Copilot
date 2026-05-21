"""Pydantic schemas for the inflation_swap_curve_spread tool.

Second tool in the ``inflation_swaps`` domain.  Owns the desk
concept of the *same-curve zero-coupon inflation swap (ZCIS)
tenor spread* between two pillars of the same ZCIS curve family
(e.g. USD_ZCIS 5s10s, EUR_ZCIS 5s30s, GBP_ZCIS 2s10s).  Composed
from two ``inflation_swap_rate_level`` calls (one per endpoint
tenor) into a per-trade-date difference.

Construction rule
-----------------
``spread_bps = (long_zcis_pct - short_zcis_pct) * 100``

The endpoint reads come back from the level primitive in PERCENT;
this tool converts the per-trade-date difference to BPS for
display and z-score / trailing-range stats.

Same-curve invariant
--------------------
Both legs share a single ``curve_family`` (e.g. ``USD_ZCIS``).
Cross-curve combinations (USD_ZCIS 5Y vs EUR_ZCIS 5Y) are a
separate primitive — ``cross_market_inflation_swap_spread`` —
NOT a config knob on this one.  This tool exposes a single
``curve_family`` field at the input layer so cross-curve
attempts cannot be expressed; the compute layer additionally
re-asserts that the two resolved instrument_master rows share
``inflation_index_family``, ``index_lag``, ``interpolation``, and
``underlying_index``, surfacing a controlled error envelope on
mismatch.

Validation layering
-------------------
- ``field_name`` defaults to ``None`` — the sentinel that resolves
  against the YAML's ``default_zcis_rate_field`` convention.
  Mirrors the empty-string / None-sentinel pattern used by every
  other rates tool.

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
``_tenors_must_differ`` enforces short_tenor != long_tenor.
``_short_must_precede_long`` enforces short_years < long_years
computed via the shared ``tenor_to_years`` parser — so e.g.
``short_tenor='10Y', long_tenor='2Y'`` is rejected with a precise
error message.  These are structural inputs, not YAML knobs.

Reference-metadata wire surface (load-bearing)
----------------------------------------------
``InflationSwapCurveSpreadCurrentMetrics`` carries
``inflation_index_family`` (e.g. ``US_CPI_URBAN`` / ``EU_HICP`` /
``UK_RPI``), ``index_lag``, ``interpolation``, and
``underlying_index`` — the same fields the level primitive
surfaces for a single pillar.  By the same-curve invariant both
legs share these attributes; the compute layer re-asserts the
match and refuses to emit a snapshot if the two endpoints
disagree.

Canonical TimeSeries output
---------------------------
This tool emits the canonical
``shared.schemas.time_series.TimeSeries`` for the spread series
in BPS units (mirrors sovereign ``curve_spread`` and linker
``breakeven_curve_spread`` — curve spreads are in basis points,
NOT percent).  series_name pattern:
``<curve_family_lower>_<short_tenor_lower>_<long_tenor_lower>_zcis_curve_spread``.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.analytics.curve_bootstrap import tenor_to_years
from shared.schemas import TimeSeries


class InflationSwapCurveSpreadInput(BaseModel):
    """Parameters for computing a same-curve ZCIS tenor spread.

    spread_bps = (long_zcis_pct - short_zcis_pct) * 100

    Same-curve invariant
    --------------------
    The two legs MUST share a single ``curve_family``.  Cross-
    curve combinations (USD_ZCIS vs EUR_ZCIS) are a separate
    primitive (``cross_market_inflation_swap_spread``), NOT a
    config knob on this one.
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
            "single field is shared by both endpoints; cross-curve "
            "spreads are a separate primitive "
            "(``cross_market_inflation_swap_spread``)."
        ),
    )
    short_tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Short tenor of the ZCIS curve spread (e.g. '2Y' for "
            "2s10s, '5Y' for 5s10s, '5Y' for 5s30s).  Must be a "
            "supported pillar on this curve_family — the current "
            "ingested grid is 1Y / 2Y / 3Y / 5Y / 10Y / 20Y / 30Y "
            "on each of USD_ZCIS / EUR_ZCIS / GBP_ZCIS.  Must map "
            "to a strictly smaller year fraction than ``long_tenor`` "
            "(validated via "
            "shared.analytics.curve_bootstrap.tenor_to_years)."
        ),
    )
    long_tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Long tenor of the ZCIS curve spread (e.g. '10Y' for "
            "2s10s / 5s10s, '30Y' for 5s30s).  Must be a supported "
            "pillar on this curve_family and must map to a strictly "
            "larger year fraction than ``short_tenor``."
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
            "ZCIS rate series (short + long).  When None (default), "
            "the tool falls through to ``default_zcis_rate_field`` "
            "from config.yaml (currently 'PX_MID' — the canonical "
            "mid quoted ZCIS rate Bloomberg publishes for inflation "
            "swaps).  Pass an explicit field name to override per "
            "query.  LLM/HTTP wrappers MUST translate their "
            "wire-level sentinel (empty string for MCP, missing "
            "param for FastAPI) to None before constructing this "
            "input — otherwise the YAML default is silently "
            "shadowed.  See the curve_move_classifier "
            "wrapper-shadowing fix (commit b2605ee) for the "
            "canonical pattern."
        ),
    )

    @model_validator(mode="after")
    def _tenors_must_differ(self) -> "InflationSwapCurveSpreadInput":
        if self.short_tenor == self.long_tenor:
            raise ValueError(
                f"short_tenor and long_tenor must be different, but "
                f"both are '{self.short_tenor}'.  A ZCIS curve "
                "spread requires two distinct endpoint tenors."
            )
        return self

    @model_validator(mode="after")
    def _short_must_precede_long(
        self,
    ) -> "InflationSwapCurveSpreadInput":
        try:
            t_short = tenor_to_years(self.short_tenor)
            t_long = tenor_to_years(self.long_tenor)
        except ValueError as exc:
            raise ValueError(
                f"Could not parse tenor pair "
                f"short_tenor='{self.short_tenor}', "
                f"long_tenor='{self.long_tenor}': {exc}.  Accepted "
                "forms are '<n>W', '<n>M', '<n>Y'."
            ) from exc
        if t_long <= t_short:
            raise ValueError(
                f"long_tenor ('{self.long_tenor}', "
                f"{t_long:g}y) must map to a strictly larger year "
                f"fraction than short_tenor ('{self.short_tenor}', "
                f"{t_short:g}y).  A ZCIS curve spread cannot be "
                "inverted; pass the shorter tenor as ``short_tenor`` "
                "and the longer as ``long_tenor``."
            )
        return self


class InflationSwapCurveSpreadCurrentMetrics(BaseModel):
    """Snapshot metrics for a same-curve ZCIS tenor spread.

    Carries both the curve spread (in bps) and the load-bearing
    reference metadata shared by both legs (by the same-curve
    invariant).  ``methodology_label`` is sourced from the YAML's
    ``methodology.what_it_does`` at runtime — NOT a hardcoded
    Python literal — so a YAML edit to the disclosure flows
    through.
    """

    as_of_date: str = Field(
        ..., description="Most recent aligned trade date (YYYY-MM-DD).",
    )
    curve_family: str
    short_tenor: str
    long_tenor: str
    spread_label: str = Field(
        ...,
        description=(
            "Human-readable label, e.g. 'USD_ZCIS 5s10s' or "
            "'EUR_ZCIS 5s30s'."
        ),
    )
    spread_bps: float = Field(
        ...,
        description=(
            "Current ZCIS curve spread in basis points "
            "((long_zcis_pct - short_zcis_pct) * 100)."
        ),
    )
    change_1d_bps: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in the ZCIS curve spread (bps)."
        ),
    )
    change_1w_bps: Optional[float] = Field(
        None,
        description=(
            "5-trading-day change in the ZCIS curve spread (bps)."
        ),
    )
    change_1m_bps: Optional[float] = Field(
        None,
        description=(
            "22-trading-day (~1 month) change in the ZCIS curve "
            "spread (bps)."
        ),
    )
    z_score_252d: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the ZCIS curve "
            "spread (in bps) vs its own trailing window."
        ),
    )
    high_252d_bps: Optional[float] = Field(
        None,
        description=(
            "Highest ZCIS curve spread over trailing 252 trading "
            "days (bps)."
        ),
    )
    low_252d_bps: Optional[float] = Field(
        None,
        description=(
            "Lowest ZCIS curve spread over trailing 252 trading "
            "days (bps)."
        ),
    )
    percentile_252d: Optional[float] = Field(
        None,
        description=(
            "Percentile rank within trailing 252-day range (0-100)."
        ),
    )
    short_zcis_rate_pct: Optional[float] = Field(
        None,
        description=(
            "Latest short-tenor ZCIS rate in percent — exposed so "
            "the desk can audit the curve-spread decomposition."
        ),
    )
    long_zcis_rate_pct: Optional[float] = Field(
        None,
        description=(
            "Latest long-tenor ZCIS rate in percent."
        ),
    )
    short_years: float = Field(
        ...,
        description=(
            "Year fraction of ``short_tenor`` (e.g. 2.0 for '2Y')."
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
            "(shared by both legs by the same-curve invariant).  "
            "Examples: 'US_CPI_URBAN' (USD_ZCIS), 'EU_HICP' "
            "(EUR_ZCIS), 'UK_RPI' (GBP_ZCIS).  Surfaced on the "
            "wire so a desk reader can interpret the spread "
            "honestly."
        ),
    )
    index_lag: str = Field(
        ...,
        description=(
            "Indexation lag the ZCIS curve references (shared by "
            "both legs).  Examples: '3M' (USD_ZCIS, EUR_ZCIS), "
            "'2M' (GBP_ZCIS)."
        ),
    )
    interpolation: str = Field(
        ...,
        description=(
            "Index-fixing interpolation convention the ZCIS curve "
            "uses (shared by both legs).  Examples: 'Daily' "
            "(USD_ZCIS), 'Monthly' (EUR_ZCIS, GBP_ZCIS)."
        ),
    )
    underlying_index: Optional[str] = Field(
        None,
        description=(
            "Underlying inflation index Bloomberg ticker (shared by "
            "both legs).  Examples: 'CPURNSA Index' (USD_ZCIS), "
            "'CPTFEMU Index' (EUR_ZCIS), 'UKRPI Index' (GBP_ZCIS)."
        ),
    )
    methodology_label: str = Field(
        ...,
        description=(
            "Wire-honesty disclosure threaded from the YAML's "
            "``methodology.what_it_does``.  Spells out the spread "
            "formula, composition pattern, alignment discipline, "
            "AND the same-curve-only caveat so downstream "
            "operators and the LLM cannot misread the output."
        ),
    )


class InflationSwapCurveSpreadTimeSeriesRow(BaseModel):
    """Single row in the bespoke ZCIS curve-spread time-series.

    ``spread_bps`` is the ZCIS curve spread in basis points;
    ``z_score`` is the rolling z-score (None for rows in the
    rolling-window warmup).
    """

    date: str
    spread_bps: float
    z_score: Optional[float] = None


class InflationSwapCurveSpreadOutput(BaseModel):
    """Top-level response for the inflation_swap_curve_spread tool.

    ``time_series`` is the bespoke wire-frozen shape
    (``InflationSwapCurveSpreadTimeSeriesRow``) for callers that
    want spread + z-score in one row.

    ``time_series_spread`` and ``time_series_zscore`` are the
    canonical closed-enum shapes the workflow / operator layer
    consumes.  All three are computed from the same underlying
    display DataFrame — they cannot drift.
    """

    current_metrics: InflationSwapCurveSpreadCurrentMetrics
    time_series: List[InflationSwapCurveSpreadTimeSeriesRow]
    time_series_spread: TimeSeries = Field(
        ...,
        description=(
            "Historical same-curve ZCIS tenor spread (in BPS) over "
            "the displayed window.  Closed-enum "
            "``TimeSeriesUnits.BPS``; series_name pattern: "
            "'<curve_family>_<short_tenor>_<long_tenor>_zcis_curve_spread'.  "
            "Values match ``time_series[i].spread_bps`` 1-to-1 by "
            "construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the ZCIS curve spread "
            "(in bps) vs its own trailing window.  Closed-enum "
            "``TimeSeriesUnits.Z_SCORE``; series_name pattern: "
            "'<curve_family>_<short_tenor>_<long_tenor>_zcis_curve_spread_zscore'.  "
            "Values match ``time_series[i].z_score`` 1-to-1 (None "
            "for rows in the rolling-window warmup)."
        ),
    )


__all__ = [
    "InflationSwapCurveSpreadInput",
    "InflationSwapCurveSpreadCurrentMetrics",
    "InflationSwapCurveSpreadTimeSeriesRow",
    "InflationSwapCurveSpreadOutput",
]
