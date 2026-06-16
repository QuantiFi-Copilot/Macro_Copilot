"""Pydantic schemas for the inflation_swap_rate_level tool.

First tool in the new ``inflation_swaps`` domain.  Owns the desk
concept of a *zero-coupon inflation swap (ZCIS) rate level* at a
single curve pillar — distinct from the linker
``real_yield_level`` primitive (linker real-yield-to-maturity, a
bond-implied object) and from any nominal sovereign yield (different
instrument family entirely).

Validation layering
-------------------
- ``field_name`` defaults to ``None`` — the sentinel that resolves
  against the YAML's ``default_field_name`` convention.  Mirrors the
  empty-string / None-sentinel pattern used by every other rates
  tool.

- ``lookback_days`` controls the displayed window only (the
  ``time_series`` row count and the ``observation_count`` cutoff).
  Does NOT control the rolling z-score window or the trailing range
  window — those are independent conventions in ``config.yaml``
  (currently 252 / 252).

- ``high_252d_pct`` / ``low_252d_pct`` / ``percentile_252d`` field
  names embed the trailing-range window length and are wire-frozen
  in V1.  See ``config.yaml``'s ``planned_extensions`` for the path
  to making the trailing window configurable.

Reference-metadata wire surface (load-bearing)
----------------------------------------------
``InflationSwapRateLevelCurrentMetrics`` carries
``inflation_index_family`` (e.g. ``US_CPI_URBAN`` / ``EU_HICP`` /
``UK_RPI``), ``index_lag`` (e.g. ``2M`` / ``3M``), and
``interpolation`` (e.g. ``Daily`` / ``Monthly``).  These are NOT
methodology knobs — they are reference-metadata invariants of the
underlying ZCIS curve as recorded in
``macro_data.instrument_master``.  The primitive surfaces them on
the wire so a desk reader can interpret the rate level honestly:
USD ZCIS references CPI-U with a 3M lag and daily interpolation,
EUR ZCIS references HICP with a 3M lag and monthly interpolation,
GBP ZCIS references RPI with a 2M lag and monthly interpolation —
these conventions are NOT comparable cross-curve without
harmonising index family / lag / interpolation.

``methodology_label`` is sourced from the YAML's
``methodology.what_it_does`` at compute time (NOT hardcoded as a
Python literal), so a YAML edit to the disclosure flows through
to runtime.  Same threading pattern as
``cross_country_breakeven_spread_simple``.

Canonical TimeSeries output
---------------------------
This tool emits the canonical
``shared.schemas.time_series.TimeSeries`` shape via
``time_series: TimeSeries`` (singular, matching the v6 primitive
convention used by ``yield_levels`` / OIS ``rate_level`` / linker
``real_yield_level``).  Each row is rounded with the same
``yield_round_decimals`` convention the snapshot uses, so the
snapshot's ``zcis_rate_pct`` equals
``time_series.rows[-1].value`` exactly (not just within tolerance).
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from shared.schemas import TimeSeries


class InflationSwapRateLevelInput(BaseModel):
    """Parameters the LLM must extract from the user to fetch a ZCIS
    rate level.  Every field maps to a filter on
    ``macro_data.v_market_data_daily_enriched`` × ``instrument_master``.
    """

    model_config = ConfigDict(extra="forbid")

    curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "Inflation-swap curve family identifier as stored in "
            "instrument_master.  Examples: 'USD_ZCIS' (US CPI-U "
            "zero-coupon inflation swaps), 'EUR_ZCIS' (euro-area "
            "HICP ex-tobacco ZCIS), 'GBP_ZCIS' (UK RPI ZCIS).  See "
            "rates_agent/playbooks/inflation_swaps.yml for the "
            "ingested universe."
        ),
    )
    tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Tenor point on the ZCIS curve.  Available tenors are "
            "curve-family specific; the current ingested grid is "
            "1Y / 2Y / 3Y / 5Y / 10Y / 20Y / 30Y on each of "
            "USD_ZCIS / EUR_ZCIS / GBP_ZCIS."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of *displayed* history.  Does NOT control "
            "the rolling z-score window (config: "
            "z_score_window_days, currently 252) or the trailing "
            "range window (config: trailing_range_window_days, "
            "locked at 252 in V1)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field for the ZCIS rate.  When "
            "None (default), the tool falls through to "
            "``default_zcis_rate_field`` from config.yaml (currently "
            "'PX_MID' — the canonical mid quoted ZCIS rate "
            "Bloomberg publishes for inflation swaps; matches the "
            "playbook's ``target_metrics`` mapping yield_mid → "
            "PX_MID).  Pass an explicit field name to override per "
            "query.  LLM/HTTP wrappers MUST translate their "
            "wire-level sentinel (empty string for MCP, missing "
            "param for FastAPI) to None before constructing this "
            "input — otherwise the YAML default is silently "
            "shadowed.  See the curve_move_classifier "
            "wrapper-shadowing fix (commit b2605ee) for the "
            "canonical pattern."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=("Optional as-of date (YYYY-MM-DD): compute as of this trade "
                     "date instead of the latest available data.  None → latest "
                     "(live snapshot).  Supply a date for a historical, replayable view."),
    )


class InflationSwapRateLevelCurrentMetrics(BaseModel):
    """Deterministic snapshot for a single ZCIS curve point.

    Carries the rate level / period changes / rolling z-score /
    trailing range stats AND the load-bearing reference metadata
    (``inflation_index_family``, ``index_lag``, ``interpolation``,
    ``underlying_index``) so a desk reader can interpret the rate
    level honestly without a second tool call.

    Field names ``high_252d_pct``, ``low_252d_pct``, and
    ``percentile_252d`` embed the trailing-range window length (252)
    in their identifiers and are therefore wire-frozen in V1.  See
    config.yaml's ``planned_extensions`` for the path to making the
    trailing window configurable.

    The ``methodology_label`` field carries the wire-honesty
    disclosure sourced from the YAML's
    ``methodology.what_it_does`` — NOT a hardcoded Python literal —
    so a YAML edit to the disclosure flows through to runtime.
    """

    as_of_date: str = Field(
        ..., description="Most recent trade date (YYYY-MM-DD).",
    )
    curve_family: str
    tenor: str
    zcis_rate_pct: float = Field(
        ...,
        description=(
            "Current zero-coupon inflation swap rate in percent.  "
            "Quoted as the par-rate the swap pays for inflation "
            "compensation over ``tenor``; structurally distinct from "
            "the linker bond-implied breakeven (which carries the "
            "two-bond IRP / liquidity premia) and from a pure "
            "expected-inflation read."
        ),
    )
    daily_change_bps: Optional[float] = Field(
        None, description="1-trading-day change in basis points.",
    )
    weekly_change_bps: Optional[float] = Field(
        None, description="5-trading-day change in basis points.",
    )
    monthly_change_bps: Optional[float] = Field(
        None,
        description="22-trading-day (~1 month) change in basis points.",
    )
    z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the ZCIS rate vs "
            "its own history."
        ),
    )
    high_252d_pct: Optional[float] = Field(
        None,
        description="Highest ZCIS rate over trailing 252 trading days (percent).",
    )
    low_252d_pct: Optional[float] = Field(
        None,
        description="Lowest ZCIS rate over trailing 252 trading days (percent).",
    )
    percentile_252d: Optional[float] = Field(
        None,
        description="Percentile rank within trailing 252-day range (0-100).",
    )
    observation_count: int = Field(
        ...,
        description="Number of trading days in the lookback_days window.",
    )
    inflation_index_family: str = Field(
        ...,
        description=(
            "Inflation index family the ZCIS references, as recorded "
            "in instrument_master attributes.  Examples: "
            "'US_CPI_URBAN' (USD_ZCIS), 'EU_HICP' (EUR_ZCIS), "
            "'UK_RPI' (GBP_ZCIS).  Surfaced on the wire so a desk "
            "reader can interpret the rate level — these indices are "
            "NOT identical inflation references and the ZCIS rates "
            "are NOT directly comparable cross-curve without "
            "harmonising index family / lag / interpolation."
        ),
    )
    index_lag: str = Field(
        ...,
        description=(
            "Indexation lag the ZCIS references, as recorded in "
            "instrument_master attributes.  Examples: '3M' (USD_ZCIS, "
            "EUR_ZCIS), '2M' (GBP_ZCIS).  Different lags mean the "
            "rates are quoted against differently dated index fixings."
        ),
    )
    interpolation: str = Field(
        ...,
        description=(
            "Index-fixing interpolation convention the ZCIS uses, as "
            "recorded in instrument_master attributes.  Examples: "
            "'Daily' (USD_ZCIS), 'Monthly' (EUR_ZCIS, GBP_ZCIS)."
        ),
    )
    underlying_index: Optional[str] = Field(
        None,
        description=(
            "Underlying inflation index Bloomberg ticker (the "
            "instrument_master ``underlying_index`` column).  "
            "Examples: 'CPURNSA Index' (USD_ZCIS), 'CPTFEMU Index' "
            "(EUR_ZCIS), 'UKRPI Index' (GBP_ZCIS).  Surfaced for "
            "trace-back to the actual inflation observation feed."
        ),
    )
    methodology_label: str = Field(
        ...,
        description=(
            "Wire-honesty disclosure threaded from the YAML's "
            "``methodology.what_it_does``.  Spells out that this is "
            "the ZCIS rate level for a single curve pillar read "
            "directly from instrument_master × "
            "v_market_data_daily_enriched filtered by "
            "instrument_type='inflation_swap' AND "
            "pricing_type='zero_coupon_breakeven', and that the "
            "wire surface carries the index family / lag / "
            "interpolation so the user can interpret the level "
            "honestly."
        ),
    )


class InflationSwapRateLevelTimeSeriesRow(BaseModel):
    """Single row in the canonical ZCIS rate-level time-series.

    The canonical ``TimeSeries`` output is the authoritative payload
    the workflow / operator layer consumes; this row schema is
    re-exported only so callers that prefer per-row Pydantic
    validation (e.g. tests, REST contract checks) have a class to
    validate against.  Each row's ``value`` is rounded with the
    same ``yield_round_decimals`` convention the snapshot uses, so
    the snapshot's ``zcis_rate_pct`` equals
    ``time_series.rows[-1].value`` strictly.
    """

    date: str
    value: Optional[float] = None


class InflationSwapRateLevelOutput(BaseModel):
    """Top-level response for the inflation_swap_rate_level tool.

    ``current_metrics`` is the snapshot the desk consumes;
    ``time_series`` carries the canonical closed-enum TimeSeries
    the primitive-to-operator bridge expects.  Singular
    ``TimeSeries`` field matches the v6 sovereign primitive
    convention (see ``yield_levels`` / OIS ``rate_level`` / linker
    ``real_yield_level``).
    """

    current_metrics: InflationSwapRateLevelCurrentMetrics
    time_series: TimeSeries = Field(
        ...,
        description=(
            "Historical ZCIS rate levels at the requested "
            "(curve_family, tenor) pillar over the displayed "
            "``lookback_days`` window.  Closed-enum "
            "``TimeSeriesUnits.PERCENT`` units; series_name = "
            "'<curve_family_lower>_<tenor_lower>_zcis_rate' (the "
            "``_zcis_rate`` suffix distinguishes from nominal "
            "sovereign yield series, OIS rate series, and linker "
            "real-yield series when they end up in the same "
            "operator panel downstream).  Description includes the "
            "index family / lag / interpolation detail.  Each row "
            "is rounded with the same ``yield_round_decimals`` "
            "convention the snapshot uses, so the snapshot's "
            "``zcis_rate_pct`` equals "
            "``time_series.rows[-1].value`` STRICTLY (not just "
            "within tolerance)."
        ),
    )


__all__ = [
    "InflationSwapRateLevelInput",
    "InflationSwapRateLevelCurrentMetrics",
    "InflationSwapRateLevelTimeSeriesRow",
    "InflationSwapRateLevelOutput",
]
