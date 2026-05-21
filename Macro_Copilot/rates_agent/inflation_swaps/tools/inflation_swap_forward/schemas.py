"""Pydantic schemas for the inflation_swap_forward tool.

Third tool in the ``inflation_swaps`` domain.  Owns the desk
concept of a *forward zero-coupon inflation swap (ZCIS) rate*
between two pillars on the same ZCIS curve family (e.g.
``USD_ZCIS 5Y5Y``, ``EUR_ZCIS 5Y5Y``, ``GBP_ZCIS 2Y3Y``).
Composed from two ``inflation_swap_rate_level`` calls (one per
endpoint tenor) into a per-trade-date dual-compounding geometric
forward rate.

Construction rule (dual-compounding geometric)
----------------------------------------------
ZCIS is a true zero-coupon rate, so the desk-natural forward is the
dual-compounding geometric form:

    (1 + r_short)^T_short * (1 + f)^(T_long - T_short) =
        (1 + r_long)^T_long

    f = ((1 + r_long)^T_long / (1 + r_short)^T_short)
            ^ (1 / (T_long - T_short)) - 1

The endpoint reads come back from the level primitive in PERCENT;
this tool converts them to decimal for the geometric arithmetic and
then back to percent / bps for display.

Same-curve invariant
--------------------
Both legs share a single ``curve_family`` (e.g. ``USD_ZCIS``).
Cross-curve forward combinations are NOT in scope for V1 — this
primitive's input layer exposes a single ``curve_family`` field so
cross-curve attempts cannot be expressed.  The compute layer
additionally re-asserts that the two resolved instrument_master
rows share ``inflation_index_family``, ``index_lag``,
``interpolation``, and ``underlying_index``, surfacing a controlled
error envelope on mismatch.

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
``_tenors_must_differ`` enforces ``start_tenor != end_tenor``.
``_start_must_precede_end`` enforces ``start_years < end_years``
computed via the shared ``tenor_to_years`` parser — so e.g.
``start_tenor='10Y', end_tenor='2Y'`` is rejected with a precise
error message.  These are structural inputs, not YAML knobs.

Reference-metadata wire surface (load-bearing)
----------------------------------------------
``InflationSwapForwardCurrentMetrics`` carries
``inflation_index_family`` (e.g. ``US_CPI_URBAN`` / ``EU_HICP`` /
``UK_RPI``), ``index_lag``, ``interpolation``, and
``underlying_index`` — the same fields the level primitive
surfaces for a single pillar.  By the same-curve invariant both
legs share these attributes; the compute layer re-asserts the
match and refuses to emit a snapshot if the two endpoints
disagree.

Canonical TimeSeries output
---------------------------
This tool emits TWO canonical
``shared.schemas.time_series.TimeSeries`` payloads alongside the
bespoke ``time_series`` list:

  - ``time_series_forward`` (units = PERCENT) — the forward ZCIS
    rate series over the displayed window.  Forward ZCIS is a
    *level*, not a spread, so the canonical TimeSeries is in
    PERCENT (mirrors ``forward_breakeven_simple`` and OIS
    ``forward_rate``; do NOT confuse with curve-spread tools that
    ship in BPS).  series_name pattern:
    ``<curve_family_lower>_<start_tenor_lower>_<end_tenor_lower>_zcis_forward``.

  - ``time_series_zscore`` (units = Z_SCORE) — the rolling z-score
    of the forward ZCIS rate (in BPS) over the displayed window.
    series_name pattern:
    ``<curve_family_lower>_<start_tenor_lower>_<end_tenor_lower>_zcis_forward_zscore``.

All three (bespoke + two canonical) share the same display
DataFrame so they cannot drift.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.analytics.curve_bootstrap import tenor_to_years
from shared.schemas import TimeSeries


class InflationSwapForwardInput(BaseModel):
    """Parameters for computing a same-curve ZCIS forward rate.

    Forward formula (dual-compounding geometric):

        f = ((1 + r_long)^T_long / (1 + r_short)^T_short)
                ^ (1 / (T_long - T_short)) - 1

    Same-curve invariant
    --------------------
    The two legs MUST share a single ``curve_family``.  Cross-
    curve forward combinations (USD_ZCIS vs EUR_ZCIS) are NOT in
    scope for V1 — the input layer accepts a single
    ``curve_family`` field so cross-curve attempts cannot be
    expressed.
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
            "forwards are not in scope for V1."
        ),
    )
    start_tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Start tenor of the forward window.  For 5Y5Y use '5Y'; "
            "for 5Y10Y use '5Y'; for 2Y3Y use '2Y'.  Must be a "
            "supported pillar on this ``curve_family`` — the "
            "current ingested grid is 1Y / 2Y / 3Y / 5Y / 10Y / "
            "20Y / 30Y on each of USD_ZCIS / EUR_ZCIS / GBP_ZCIS.  "
            "Must map to a strictly smaller year fraction than "
            "``end_tenor`` (validated via "
            "shared.analytics.curve_bootstrap.tenor_to_years)."
        ),
    )
    end_tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "End tenor of the forward window.  For 5Y5Y use '10Y' "
            "(start=5Y + forward=5Y); for 5Y10Y use '15Y'; for "
            "2Y3Y use '5Y'.  Must be a supported pillar on this "
            "``curve_family`` and must map to a strictly larger "
            "year fraction than ``start_tenor``."
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
            "ZCIS rate series (start + end).  When None (default), "
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
    def _tenors_must_differ(self) -> "InflationSwapForwardInput":
        if self.start_tenor == self.end_tenor:
            raise ValueError(
                f"start_tenor and end_tenor must be different, but "
                f"both are '{self.start_tenor}'.  A forward window "
                "requires two distinct endpoint tenors."
            )
        return self

    @model_validator(mode="after")
    def _start_must_precede_end(
        self,
    ) -> "InflationSwapForwardInput":
        try:
            t_short = tenor_to_years(self.start_tenor)
            t_long = tenor_to_years(self.end_tenor)
        except ValueError as exc:
            raise ValueError(
                f"Could not parse tenor pair "
                f"start_tenor='{self.start_tenor}', "
                f"end_tenor='{self.end_tenor}': {exc}.  Accepted "
                "forms are '<n>W', '<n>M', '<n>Y'."
            ) from exc
        if t_long <= t_short:
            raise ValueError(
                f"end_tenor ('{self.end_tenor}', "
                f"{t_long:g}y) must map to a strictly larger year "
                f"fraction than start_tenor ('{self.start_tenor}', "
                f"{t_short:g}y).  A forward window cannot be "
                "inverted; pass the shorter tenor as ``start_tenor`` "
                "and the longer as ``end_tenor``."
            )
        return self


class InflationSwapForwardCurrentMetrics(BaseModel):
    """Snapshot metrics for a same-curve ZCIS forward rate.

    Carries both the forward ZCIS rate (in pct AND bps) and the
    two endpoint ZCIS rates + year fractions used to form it, so
    the desk can sanity-check the dual-compounding decomposition
    end-to-end without a second tool call.

    The ``methodology_label`` field carries the wire-honesty
    disclosure sourced from the YAML's ``methodology.what_it_does``
    — NOT a hardcoded Python literal — so a YAML edit to the
    disclosure flows through to runtime.  ``forward_window_label``
    is a desk-friendly identifier like ``'USD_ZCIS 5Y5Y'``.
    """

    as_of_date: str = Field(
        ..., description="Most recent aligned trade date (YYYY-MM-DD).",
    )
    curve_family: str
    start_tenor: str
    end_tenor: str
    forward_window_label: str = Field(
        ...,
        description=(
            "Human-readable label, e.g. 'USD_ZCIS 5Y5Y', "
            "'EUR_ZCIS 5Y5Y', or 'GBP_ZCIS 2Y3Y'."
        ),
    )
    forward_zcis_pct: float = Field(
        ...,
        description=(
            "Current forward ZCIS rate in percent.  Forward "
            "inflation compensation, not pure forward expected "
            "inflation — see ``methodology_label``."
        ),
    )
    forward_zcis_bps: float = Field(
        ...,
        description=(
            "Current forward ZCIS rate in basis points "
            "(forward_zcis_pct * 100), computed via the "
            "dual-compounding geometric formula on the two "
            "endpoint ZCIS rates (see ``methodology_label`` for "
            "the exact formula)."
        ),
    )
    change_1d_bps: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in the forward ZCIS rate (bps)."
        ),
    )
    change_1w_bps: Optional[float] = Field(
        None,
        description=(
            "5-trading-day change in the forward ZCIS rate (bps)."
        ),
    )
    change_1m_bps: Optional[float] = Field(
        None,
        description=(
            "22-trading-day (~1 month) change in the forward "
            "ZCIS rate (bps)."
        ),
    )
    z_score_252d: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the forward "
            "ZCIS rate (in bps) vs its own trailing window."
        ),
    )
    high_252d_bps: Optional[float] = Field(
        None,
        description=(
            "Highest forward ZCIS rate over trailing 252 trading "
            "days (bps)."
        ),
    )
    low_252d_bps: Optional[float] = Field(
        None,
        description=(
            "Lowest forward ZCIS rate over trailing 252 trading "
            "days (bps)."
        ),
    )
    percentile_252d: Optional[float] = Field(
        None,
        description=(
            "Percentile rank within trailing 252-day range (0-100)."
        ),
    )
    start_zcis_pct: Optional[float] = Field(
        None,
        description=(
            "Latest start-tenor ZCIS rate in percent — exposed so "
            "the desk can audit the dual-compounding "
            "decomposition."
        ),
    )
    end_zcis_pct: Optional[float] = Field(
        None,
        description=(
            "Latest end-tenor ZCIS rate in percent."
        ),
    )
    start_years: float = Field(
        ...,
        description=(
            "Year fraction of ``start_tenor`` (e.g. 5.0 for '5Y'). "
            "Constant across the displayed window in tenor-pair "
            "mode (the only mode supported in V1)."
        ),
    )
    end_years: float = Field(
        ...,
        description=(
            "Year fraction of ``end_tenor`` (e.g. 10.0 for '10Y')."
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
            "wire so a desk reader can interpret the forward "
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
            "Underlying inflation index Bloomberg ticker (shared "
            "by both legs).  Examples: 'CPURNSA Index' "
            "(USD_ZCIS), 'CPTFEMU Index' (EUR_ZCIS), "
            "'UKRPI Index' (GBP_ZCIS)."
        ),
    )
    methodology_label: str = Field(
        ...,
        description=(
            "Wire-honesty disclosure threaded from the YAML's "
            "``methodology.what_it_does``.  Spells out the "
            "dual-compounding forward formula, composition "
            "pattern, alignment discipline, AND the "
            "'forward inflation compensation; not a clean forward "
            "expected-inflation read' caveat so downstream "
            "operators and the LLM cannot misread the output."
        ),
    )


class InflationSwapForwardTimeSeriesRow(BaseModel):
    """Single row in the bespoke forward ZCIS rate time-series.

    ``forward_zcis_pct`` is the forward ZCIS rate in percent;
    ``forward_zcis_bps`` is the same value in basis points
    (= forward_zcis_pct * 100); ``z_score`` is the rolling z-score
    (None for rows in the rolling-window warmup).
    """

    date: str
    forward_zcis_pct: float
    forward_zcis_bps: float
    z_score: Optional[float] = None


class InflationSwapForwardOutput(BaseModel):
    """Top-level response for the inflation_swap_forward tool.

    ``time_series`` is the bespoke wire-frozen shape
    (``InflationSwapForwardTimeSeriesRow``) for callers that want
    forward + z-score in one row.

    ``time_series_forward`` and ``time_series_zscore`` are the
    canonical closed-enum shapes the workflow / operator layer
    consumes.  Forward ZCIS is a *level* (PERCENT), the z-score is
    Z_SCORE.  All three are computed from the same underlying
    display DataFrame — they cannot drift.
    """

    current_metrics: InflationSwapForwardCurrentMetrics
    time_series: List[InflationSwapForwardTimeSeriesRow]
    time_series_forward: TimeSeries = Field(
        ...,
        description=(
            "Historical same-curve ZCIS forward rate (in PERCENT) "
            "over the displayed window.  Closed-enum "
            "``TimeSeriesUnits.PERCENT``; series_name pattern: "
            "'<curve_family>_<start_tenor>_<end_tenor>_zcis_forward'.  "
            "Values match ``time_series[i].forward_zcis_pct`` "
            "1-to-1 by construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the forward ZCIS rate "
            "(in bps) vs its own trailing window.  Closed-enum "
            "``TimeSeriesUnits.Z_SCORE``; series_name pattern: "
            "'<curve_family>_<start_tenor>_<end_tenor>_zcis_forward_zscore'.  "
            "Values match ``time_series[i].z_score`` 1-to-1 (None "
            "for rows in the rolling-window warmup)."
        ),
    )


__all__ = [
    "InflationSwapForwardInput",
    "InflationSwapForwardCurrentMetrics",
    "InflationSwapForwardTimeSeriesRow",
    "InflationSwapForwardOutput",
]
