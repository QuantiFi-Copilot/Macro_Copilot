"""Pydantic schemas for the forward_breakeven_simple tool.

Third tool in the ``inflation_indexed_bonds`` domain.  Owns the desk
concept of a *forward bond-implied breakeven inflation*: the
year-weighted differential between two same-country spot
bond-implied breakevens at two endpoint tenors (e.g. 5Y5Y, 5Y10Y,
2Y3Y), presented as forward inflation compensation.  NOT a clean
forward expected-inflation read — see ``methodology.what_it_does`` in
the tool's ``config.yaml`` and the ``methodology_label`` field on
``ForwardBreakevenSimpleCurrentMetrics``.

Composition path
----------------
This primitive composes two calls to
``calculate_breakeven_inflation_simple`` (one at ``start_tenor``, one
at ``end_tenor``) into a year-weighted forward.  The spot primitive's
no-proxy guard (instrument_type filter on each leg) and same-country
invariant are inherited transitively — both fire BEFORE any
market-data SELECT on either endpoint.

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
``_curve_families_must_differ`` enforces nominal != linker (a
caller asking for ``nominal=USD_TIPS, linker=USD_TIPS`` is a
structural error, not a methodology choice).  ``_tenors_must_differ``
enforces start_tenor != end_tenor.  ``_start_must_precede_end``
enforces start_years < end_years computed via the shared
``tenor_to_years`` parser — so e.g. ``start_tenor='10Y',
end_tenor='5Y'`` is rejected with a precise error message.  These
are structural inputs, not YAML knobs.

The *same-country / same-currency* invariant is enforced at compute
time by the spot breakeven primitive's helper, NOT here in the
Pydantic validator (it requires a DB lookup against
``macro_data.instrument_master``).  See
``rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute._enforce_same_country_invariant``
for the guard and the controlled ``{"error": ...}`` envelope it
emits on mismatch — both of those flow through this primitive
unchanged because every endpoint composes the spot primitive.

Canonical TimeSeries output
---------------------------
This tool emits TWO canonical ``shared.schemas.time_series.TimeSeries``
payloads alongside the bespoke ``time_series`` list:

  - ``time_series_forward`` (units = BPS) — the forward breakeven
    series over the displayed window.  series_name pattern:
    ``<nominal_lower>_<linker_lower>_<startten_lower>_<endten_lower>_forward_breakeven``.

  - ``time_series_zscore`` (units = Z_SCORE) — the rolling z-score
    of the forward breakeven series over the displayed window.
    series_name pattern:
    ``<nominal_lower>_<linker_lower>_<startten_lower>_<endten_lower>_forward_breakeven_zscore``.

All three (bespoke + two canonical) share the same display
DataFrame so they cannot drift.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from shared.analytics.curve_bootstrap import tenor_to_years
from shared.schemas import TimeSeries


class ForwardBreakevenSimpleInput(BaseModel):
    """Parameters for computing a forward bond-implied breakeven
    inflation between two same-country curve points.

    forward_breakeven_bps =
        (BE_long_bps * T_long - BE_short_bps * T_short)
        / (T_long - T_short)

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
    this primitive's two endpoint calls.
    """

    nominal_curve_family: str = Field(
        ...,
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
        description=(
            "Sovereign linker curve family identifier as stored in "
            "instrument_master.  Examples: 'USD_TIPS', 'GBP_LINKER', "
            "'EUR_FR_LINKER', 'CAD_RRB'.  See "
            "rates_agent/playbooks/inflation_indexed_bonds.yml for the "
            "ingested universe.  Same-country invariant: this leg "
            "must share country AND currency with "
            "``nominal_curve_family``."
        ),
    )
    start_tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Start tenor of the forward window.  For 5Y5Y use '5Y'; "
            "for 5Y10Y use '5Y'; for 2Y3Y use '2Y'.  Must be present "
            "on BOTH the nominal and linker curves at this country, "
            "otherwise the inner endpoint breakeven returns a "
            "controlled error envelope which this tool surfaces.  "
            "Must be strictly shorter than ``end_tenor`` (validated "
            "via shared.analytics.curve_bootstrap.tenor_to_years)."
        ),
    )
    end_tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "End tenor of the forward window.  For 5Y5Y use '10Y' "
            "(start=5Y + forward=5Y); for 5Y10Y use '15Y'; for 2Y3Y "
            "use '5Y'.  Must be present on BOTH curves at this "
            "country and must map to a strictly larger year fraction "
            "than ``start_tenor``."
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
            "Bloomberg observation field used for ALL FOUR underlying "
            "yield series (nominal at start_tenor, linker at "
            "start_tenor, nominal at end_tenor, linker at end_tenor).  "
            "When None (default), the tool falls through to "
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
        description=("Optional as-of date (YYYY-MM-DD): compute as of this trade "
                     "date instead of the latest available data.  None → latest "
                     "(live snapshot).  Supply a date for a historical, replayable view."),
    )

    @model_validator(mode="after")
    def _curve_families_must_differ(
        self,
    ) -> "ForwardBreakevenSimpleInput":
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
    def _tenors_must_differ(self) -> "ForwardBreakevenSimpleInput":
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
    ) -> "ForwardBreakevenSimpleInput":
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
                f"{t_short:g}y).  A forward window cannot be empty "
                "or inverted."
            )
        return self


class ForwardBreakevenSimpleCurrentMetrics(BaseModel):
    """Snapshot metrics for a forward bond-implied breakeven.

    Carries both the forward breakeven (in pct AND bps) and the two
    endpoint breakevens + year fractions used to form it, so the desk
    can sanity-check the year-weighted decomposition end-to-end
    without a second tool call.

    The ``methodology_label`` field carries the wire-honesty
    disclosure sourced from the YAML's ``methodology.what_it_does``
    — NOT a hardcoded Python literal — so a YAML edit to the
    disclosure flows through to runtime.  ``forward_window_label``
    is a desk-friendly identifier like ``'UST/USD_TIPS 5Y5Y'``.
    """

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    nominal_curve_family: str
    linker_curve_family: str
    start_tenor: str
    end_tenor: str
    forward_window_label: str = Field(
        ...,
        description=(
            "Human-readable label, e.g. 'UST/USD_TIPS 5Y5Y' or "
            "'FR_OAT/EUR_FR_LINKER 5Y10Y'."
        ),
    )
    forward_breakeven_pct: Optional[float] = Field(
        None,
        description=(
            "Current forward breakeven inflation in percent "
            "(forward_breakeven_bps / 100).  Forward inflation "
            "compensation, not pure forward expected inflation — "
            "see ``methodology_label``."
        ),
    )
    forward_breakeven_bps: Optional[float] = Field(
        None,
        description=(
            "Current forward breakeven inflation in basis points, "
            "computed as the year-weighted differential of the two "
            "endpoint breakevens (see "
            "``methodology_label`` for the exact formula)."
        ),
    )
    daily_change_bps: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in the forward breakeven (bps)."
        ),
    )
    weekly_change_bps: Optional[float] = Field(
        None,
        description=(
            "5-trading-day change in the forward breakeven (bps)."
        ),
    )
    monthly_change_bps: Optional[float] = Field(
        None,
        description=(
            "22-trading-day (~1 month) change in the forward "
            "breakeven (bps)."
        ),
    )
    current_z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the forward "
            "breakeven (in bps) vs its own trailing window."
        ),
    )
    rolling_window_days: int = Field(
        ..., description="Window used for the z-score calculation."
    )
    high_252d_bps: Optional[float] = Field(
        None,
        description=(
            "Highest forward breakeven over trailing 252 trading "
            "days (bps)."
        ),
    )
    low_252d_bps: Optional[float] = Field(
        None,
        description=(
            "Lowest forward breakeven over trailing 252 trading "
            "days (bps)."
        ),
    )
    percentile_252d: Optional[float] = Field(
        None,
        description="Percentile rank within trailing 252-day range (0-100).",
    )
    start_breakeven_bps: Optional[float] = Field(
        None,
        description=(
            "Latest spot bond-implied breakeven (bps) at "
            "``start_tenor`` — exposed so the desk can audit the "
            "year-weighted decomposition."
        ),
    )
    end_breakeven_bps: Optional[float] = Field(
        None,
        description=(
            "Latest spot bond-implied breakeven (bps) at "
            "``end_tenor``."
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
    methodology_label: str = Field(
        ...,
        description=(
            "Wire-honesty disclosure threaded from the YAML's "
            "``methodology.what_it_does``.  Carries the explicit "
            "year-weighted-linear forward formula AND the "
            "'forward inflation compensation; not a clean forward "
            "expected-inflation read' caveat so downstream operators "
            "and the LLM cannot misread the output."
        ),
    )


class ForwardBreakevenSimpleTimeSeriesRow(BaseModel):
    """Single row in the bespoke forward-breakeven time-series.

    ``forward_breakeven_bps`` is the forward breakeven in basis
    points; ``z_score`` is the rolling z-score (None for rows in the
    rolling-window warmup).
    """

    date: str
    forward_breakeven_bps: float
    z_score: Optional[float] = None


class ForwardBreakevenSimpleOutput(BaseModel):
    """Top-level response for the forward_breakeven_simple tool.

    ``time_series`` is the bespoke wire-frozen shape
    (``ForwardBreakevenSimpleTimeSeriesRow``) for callers that want
    forward breakeven + z-score in one row.

    ``time_series_forward`` and ``time_series_zscore`` are the
    canonical closed-enum shapes the workflow / operator layer
    consumes.  All three are computed from the same underlying
    display DataFrame — they cannot drift.
    """

    current_metrics: ForwardBreakevenSimpleCurrentMetrics
    time_series: List[ForwardBreakevenSimpleTimeSeriesRow]
    time_series_forward: TimeSeries = Field(
        ...,
        description=(
            "Historical forward bond-implied breakeven inflation "
            "(in BPS) over the displayed window.  Closed-enum "
            "``TimeSeriesUnits.BPS``; series_name pattern: "
            "'<nominal>_<linker>_<startten>_<endten>_forward_breakeven'.  "
            "Values match ``time_series[i].forward_breakeven_bps`` "
            "1-to-1 by construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the forward breakeven "
            "(in bps) vs its own trailing window.  Closed-enum "
            "``TimeSeriesUnits.Z_SCORE``; series_name pattern: "
            "'<nominal>_<linker>_<startten>_<endten>_forward_breakeven_zscore'.  "
            "Values match ``time_series[i].z_score`` 1-to-1 (None "
            "for rows in the rolling-window warmup)."
        ),
    )


__all__ = [
    "ForwardBreakevenSimpleInput",
    "ForwardBreakevenSimpleCurrentMetrics",
    "ForwardBreakevenSimpleTimeSeriesRow",
    "ForwardBreakevenSimpleOutput",
]
