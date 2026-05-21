"""Pydantic schemas for the breakeven_inflation_simple tool.

Second tool in the ``inflation_indexed_bonds`` domain.  Owns the
desk concept of *generic bond-implied breakeven inflation*: the
nominal-minus-real yield differential at the same tenor, presented as
inflation compensation.  NOT a clean expected-inflation read — see
``methodology.what_it_does`` in the tool's ``config.yaml`` and the
``methodology_label`` field on ``BreakevenInflationSimpleCurrentMetrics``.

Validation layering
-------------------
- ``field_name`` defaults to ``None`` — the sentinel that resolves
  against the YAML's ``default_field_name`` convention.  Mirrors the
  empty-string / None-sentinel pattern established by sovereign
  curve_move_classifier (commit 9f741ea), tightened by commit b2605ee,
  and used uniformly by yield_levels, butterfly,
  cross_market_spread, and linker real_yield_level.

- ``lookback_days`` controls the displayed window (the bespoke
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

The *same-country / same-currency* invariant — i.e. that the two
legs must come from the same issuer (e.g. UST + USD_TIPS, never UST
+ EUR_FR_LINKER) — is enforced at compute time, NOT here in the
Pydantic validator.  It requires a DB lookup against
``macro_data.instrument_master`` for ``(country, currency)``, so the
schema layer cannot see it without an engine.  See
``compute._enforce_same_country_invariant`` for the guard and the
controlled ``{"error": ...}`` envelope it emits on mismatch.

Canonical TimeSeries output
---------------------------
This tool emits TWO canonical ``shared.schemas.time_series.TimeSeries``
payloads alongside the bespoke ``time_series`` list:

  - ``time_series_breakeven`` (units = BPS) — the breakeven series
    over the displayed window.  series_name =
    ``<nominal_lower>_<linker_lower>_<tenor_lower>_breakeven``.

  - ``time_series_zscore`` (units = Z_SCORE) — the rolling z-score of
    the breakeven series over the displayed window.  series_name =
    ``<nominal_lower>_<linker_lower>_<tenor_lower>_zscore``.

All three (bespoke + two canonical) share the same display
DataFrame so they cannot drift.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from shared.schemas import TimeSeries


class BreakevenInflationSimpleInput(BaseModel):
    """Parameters for computing generic bond-implied breakeven
    inflation between a nominal sovereign curve and a sovereign linker
    curve at the same tenor.

    breakeven_pct = nominal_yield_pct - real_yield_pct
    breakeven_bps = breakeven_pct * 100

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
    ``{"error": "..."}`` envelope.  This invariant is enforced in
    ``compute._enforce_same_country_invariant`` (it requires a DB
    lookup), NOT in this Pydantic validator.
    """

    nominal_curve_family: str = Field(
        ...,
        description=(
            "Nominal sovereign curve family identifier as stored in "
            "instrument_master.  Examples: 'UST', 'UK_GILT', 'FR_OAT', "
            "'CANADA_GOVT'.  See "
            "rates_agent/playbooks/sovereign_bonds.yml for the "
            "ingested universe.  This leg is filtered with "
            "instrument_type='sovereign_benchmark' on the DB read; a "
            "caller passing a linker curve_family here yields a "
            "controlled error envelope (see compute.py).  Same-country "
            "invariant: this leg must share country AND currency with "
            "``linker_curve_family`` (e.g. UST/USD pairs only with "
            "USD_TIPS/USD; DE_BUND/Germany/EUR does NOT pair with "
            "EUR_FR_LINKER/France/EUR).  Cross-country pairs are "
            "refused at compute time."
        ),
    )
    linker_curve_family: str = Field(
        ...,
        description=(
            "Sovereign linker curve family identifier as stored in "
            "instrument_master.  Examples: 'USD_TIPS', 'GBP_LINKER', "
            "'EUR_FR_LINKER', 'CAD_RRB'.  See "
            "rates_agent/playbooks/inflation_indexed_bonds.yml for the "
            "ingested universe.  This leg is filtered with "
            "instrument_type='inflation_linker' on the DB read; a "
            "caller passing a nominal sovereign curve_family here "
            "yields a controlled error envelope (see compute.py).  "
            "Same-country invariant: this leg must share country AND "
            "currency with ``nominal_curve_family`` (e.g. USD_TIPS/US/"
            "USD pairs only with UST/US/USD; EUR_FR_LINKER/France/EUR "
            "does NOT pair with DE_BUND/Germany/EUR even though the "
            "currency matches).  Cross-country pairs are refused at "
            "compute time."
        ),
    )
    tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Tenor point for both legs — e.g. '5Y', '10Y', '30Y'.  "
            "Both legs must have data at this tenor; otherwise the "
            "tool returns a controlled error envelope.  Available "
            "tenor intersections are country-specific (e.g. UST + "
            "USD_TIPS overlap at 5Y/10Y/20Y/30Y)."
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
            "Bloomberg observation field used for BOTH legs.  When "
            "None (default), the tool falls through to "
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
    ) -> "BreakevenInflationSimpleInput":
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


class BreakevenInflationSimpleCurrentMetrics(BaseModel):
    """Snapshot metrics for a generic bond-implied breakeven.

    Carries both the breakeven (in pct AND bps) and the two underlying
    yields used to form it, so the desk can sanity-check the
    decomposition end-to-end without a second tool call.

    The ``methodology_label`` field carries the wire-honesty
    disclosure sourced from the YAML's ``methodology.what_it_does``
    — NOT a hardcoded Python literal — so a YAML edit to the
    disclosure flows through to runtime.
    """

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    nominal_curve_family: str
    linker_curve_family: str
    tenor: str
    breakeven_label: str = Field(
        ...,
        description=(
            "Human-readable label, e.g. 'UST-USD_TIPS 10Y breakeven'."
        ),
    )
    breakeven_pct: float = Field(
        ...,
        description=(
            "Current breakeven inflation in percent "
            "(nominal_yield_pct - real_yield_pct).  Inflation "
            "compensation, not pure expected inflation — see "
            "``methodology_label``."
        ),
    )
    breakeven_bps: float = Field(
        ...,
        description=(
            "Current breakeven inflation in basis points "
            "(breakeven_pct * 100)."
        ),
    )
    daily_change_bps: Optional[float] = Field(
        None, description="1-trading-day change in the breakeven (bps)."
    )
    weekly_change_bps: Optional[float] = Field(
        None,
        description="5-trading-day change in the breakeven (bps).",
    )
    monthly_change_bps: Optional[float] = Field(
        None,
        description=(
            "22-trading-day (~1 month) change in the breakeven (bps)."
        ),
    )
    current_z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the breakeven (in "
            "bps) vs its own trailing window."
        ),
    )
    rolling_window_days: int = Field(
        ..., description="Window used for the z-score calculation."
    )
    high_252d_bps: Optional[float] = Field(
        None,
        description=(
            "Highest breakeven over trailing 252 trading days (bps)."
        ),
    )
    low_252d_bps: Optional[float] = Field(
        None,
        description=(
            "Lowest breakeven over trailing 252 trading days (bps)."
        ),
    )
    percentile_252d: Optional[float] = Field(
        None,
        description="Percentile rank within trailing 252-day range (0-100).",
    )
    nominal_yield_pct: Optional[float] = Field(
        None,
        description=(
            "Latest nominal sovereign yield (percent) used to form "
            "the breakeven — exposed so the desk can sanity-check the "
            "decomposition."
        ),
    )
    real_yield_pct: Optional[float] = Field(
        None,
        description=(
            "Latest sovereign linker real yield (percent) used to "
            "form the breakeven."
        ),
    )
    methodology_label: str = Field(
        ...,
        description=(
            "Wire-honesty disclosure threaded from the YAML's "
            "``methodology.what_it_does``.  Carries the explicit "
            "'generic bond-implied breakeven; not a clean "
            "expected-inflation read' caveat so downstream operators "
            "and the LLM cannot misread the output as expected "
            "inflation."
        ),
    )


class BreakevenInflationSimpleTimeSeriesRow(BaseModel):
    """Single row in the bespoke breakeven time-series.

    ``breakeven_bps`` is the breakeven in basis points; ``z_score`` is
    the rolling z-score (None for rows in the rolling-window warmup).
    """

    date: str
    breakeven_bps: float
    z_score: Optional[float] = None


class BreakevenInflationSimpleOutput(BaseModel):
    """Top-level response for the breakeven_inflation_simple tool.

    ``time_series`` is the bespoke wire-frozen shape
    (``BreakevenInflationSimpleTimeSeriesRow``) for callers that want
    breakeven + z-score in one row.

    ``time_series_breakeven`` and ``time_series_zscore`` are the
    canonical closed-enum shapes the workflow / operator layer
    consumes.  All three are computed from the same underlying display
    DataFrame — they cannot drift.
    """

    current_metrics: BreakevenInflationSimpleCurrentMetrics
    time_series: List[BreakevenInflationSimpleTimeSeriesRow]
    time_series_breakeven: TimeSeries = Field(
        ...,
        description=(
            "Historical bond-implied breakeven inflation (in BPS) over "
            "the displayed window.  Closed-enum "
            "``TimeSeriesUnits.BPS``; series_name = "
            "'<nominal_lower>_<linker_lower>_<tenor_lower>_breakeven'.  "
            "Values match ``time_series[i].breakeven_bps`` 1-to-1 by "
            "construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the breakeven (in bps) vs "
            "its own trailing window.  Closed-enum "
            "``TimeSeriesUnits.Z_SCORE``; series_name = "
            "'<nominal_lower>_<linker_lower>_<tenor_lower>_zscore'.  "
            "Values match ``time_series[i].z_score`` 1-to-1 (None for "
            "rows in the rolling-window warmup)."
        ),
    )


__all__ = [
    "BreakevenInflationSimpleInput",
    "BreakevenInflationSimpleCurrentMetrics",
    "BreakevenInflationSimpleTimeSeriesRow",
    "BreakevenInflationSimpleOutput",
]
