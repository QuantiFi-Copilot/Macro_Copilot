"""Pydantic schemas for the breakeven_curve_spread tool.

Fourth tool in the ``inflation_indexed_bonds`` domain.  Owns the
desk concept of the *term structure of bond-implied breakeven
inflation*: the same-country breakeven curve spread between two
tenors of the same nominal/linker pair (e.g. UST/USD_TIPS 2s10s
breakeven, UK_GILT/GBP_LINKER 5s30s breakeven), presented as the
inflation-compensation term-structure object.  NOT the term
structure of pure expected inflation — see ``methodology.what_it_does``
in the tool's ``config.yaml`` and the ``methodology_label`` field
on ``BreakevenCurveSpreadCurrentMetrics``.

Composition path
----------------
This primitive composes two calls to
``calculate_breakeven_inflation_simple`` (one at ``short_tenor``,
one at ``long_tenor``) into a per-trade-date difference.  The spot
primitive's no-proxy guard (instrument_type filter on each leg)
and same-country invariant are inherited transitively — both fire
BEFORE any market-data SELECT on either endpoint.

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

- ``high_252d_bps`` / ``low_252d_bps`` / ``percentile_252d`` field
  names embed the trailing-range window length and are wire-frozen
  in V1.  See ``config.yaml``'s ``planned_extensions`` for the
  path to making the trailing window configurable.

Cross-field invariants stay here in code:
``_curve_families_must_differ`` enforces nominal != linker (a
caller asking for ``nominal=USD_TIPS, linker=USD_TIPS`` is a
structural error, not a methodology choice).  ``_tenors_must_differ``
enforces short_tenor != long_tenor.  ``_short_must_precede_long``
enforces short_years < long_years computed via the shared
``tenor_to_years`` parser — so e.g. ``short_tenor='10Y',
long_tenor='2Y'`` is rejected with a precise error message.
These are structural inputs, not YAML knobs.

The *same-country / same-currency* invariant is enforced at
compute time by the spot breakeven primitive's helper, NOT here
in the Pydantic validator (it requires a DB lookup against
``macro_data.instrument_master``).  See
``rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute._enforce_same_country_invariant``
for the guard and the controlled ``{"error": ...}`` envelope it
emits on mismatch — both of those flow through this primitive
unchanged because every endpoint composes the spot primitive.

Canonical TimeSeries output
---------------------------
This tool emits TWO canonical
``shared.schemas.time_series.TimeSeries`` payloads alongside the
bespoke ``time_series`` list:

  - ``time_series_spread`` (units = BPS) — the breakeven curve
    spread series over the displayed window.  series_name pattern:
    ``<nominal_lower>_<linker_lower>_<shortten_lower>_<longten_lower>_breakeven_spread``.

  - ``time_series_zscore`` (units = Z_SCORE) — the rolling z-score
    of the spread series over the displayed window.  series_name
    pattern:
    ``<nominal_lower>_<linker_lower>_<shortten_lower>_<longten_lower>_breakeven_spread_zscore``.

All three (bespoke + two canonical) share the same display
DataFrame so they cannot drift.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.analytics.curve_bootstrap import tenor_to_years
from shared.schemas import TimeSeries


class BreakevenCurveSpreadInput(BaseModel):
    """Parameters for computing a same-country breakeven curve
    spread between two tenors of the same nominal/linker pair.

    spread_bps = long_breakeven_bps - short_breakeven_bps

    Same-country invariant
    ----------------------
    The two legs MUST come from the same issuer — i.e. share both
    ``country`` and ``currency`` as stored in
    ``macro_data.instrument_master``.  Valid pairs include
    ``UST + USD_TIPS``, ``UK_GILT + GBP_LINKER``,
    ``FR_OAT + EUR_FR_LINKER``, ``CANADA_GOVT + CAD_RRB``.  Cross-
    country pairs (e.g. ``DE_BUND + EUR_FR_LINKER`` — same
    currency, different country; or ``UK_GILT + USD_TIPS`` —
    different currency) are refused at compute time with a
    controlled ``{"error": "..."}`` envelope.  This invariant is
    enforced by the spot breakeven primitive's
    ``_enforce_same_country_invariant`` helper (which fires before
    either market-data SELECT) and is inherited transitively by
    this primitive's two endpoint calls.
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
            "Short tenor of the curve spread (e.g. '2Y' for 2s10s, "
            "'5Y' for 5s30s).  Must be present on BOTH the nominal "
            "and linker curves at this country, otherwise the inner "
            "endpoint breakeven returns a controlled error envelope "
            "which this tool surfaces.  Must map to a strictly "
            "smaller year fraction than ``long_tenor`` (validated "
            "via shared.analytics.curve_bootstrap.tenor_to_years)."
        ),
    )
    long_tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Long tenor of the curve spread (e.g. '10Y' for 2s10s, "
            "'30Y' for 5s30s).  Must be present on BOTH curves at "
            "this country and must map to a strictly larger year "
            "fraction than ``short_tenor``."
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
            "Bloomberg observation field used for ALL FOUR "
            "underlying yield series (nominal at short_tenor, "
            "linker at short_tenor, nominal at long_tenor, linker "
            "at long_tenor).  When None (default), the tool falls "
            "through to ``default_field_name`` from config.yaml "
            "(currently 'YLD_YTM_MID' — same mnemonic for nominal "
            "yield-to-maturity and linker real-yield-to-maturity). "
            " Pass an explicit field name to override per query.  "
            "LLM/HTTP wrappers MUST translate their wire-level "
            "sentinel (empty string for MCP, missing param for "
            "FastAPI) to None before constructing this input — "
            "otherwise the YAML default is silently shadowed."
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
    ) -> "BreakevenCurveSpreadInput":
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
    def _tenors_must_differ(self) -> "BreakevenCurveSpreadInput":
        if self.short_tenor == self.long_tenor:
            raise ValueError(
                f"short_tenor and long_tenor must be different, but "
                f"both are '{self.short_tenor}'.  A breakeven curve "
                "spread requires two distinct endpoint tenors."
            )
        return self

    @model_validator(mode="after")
    def _short_must_precede_long(
        self,
    ) -> "BreakevenCurveSpreadInput":
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
                f"{t_short:g}y).  A breakeven curve spread cannot "
                "be inverted; pass the shorter tenor as "
                "``short_tenor`` and the longer as ``long_tenor``."
            )
        return self


class BreakevenCurveSpreadCurrentMetrics(BaseModel):
    """Snapshot metrics for a same-country breakeven curve spread.

    Carries both the curve spread (in bps) and the two endpoint
    breakevens + year fractions used to form it, so the desk can
    sanity-check the decomposition end-to-end without a second
    tool call.

    The ``methodology_label`` field carries the wire-honesty
    disclosure sourced from the YAML's
    ``methodology.what_it_does`` — NOT a hardcoded Python literal —
    so a YAML edit to the disclosure flows through to runtime.
    ``spread_label`` is a desk-friendly identifier like
    ``'UST/USD_TIPS 2s10s breakeven'``.
    """

    as_of_date: str = Field(
        ..., description="Most recent trade date (YYYY-MM-DD).",
    )
    nominal_curve_family: str
    linker_curve_family: str
    short_tenor: str
    long_tenor: str
    spread_label: str = Field(
        ...,
        description=(
            "Human-readable label, e.g. 'UST/USD_TIPS 2s10s "
            "breakeven' or 'FR_OAT/EUR_FR_LINKER 5s10s breakeven'."
        ),
    )
    current_spread_bps: Optional[float] = Field(
        None,
        description=(
            "Current breakeven curve spread in basis points "
            "(long_breakeven_bps - short_breakeven_bps).  Term "
            "structure of inflation compensation; not the term "
            "structure of pure expected inflation — see "
            "``methodology_label``."
        ),
    )
    daily_change_bps: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in the breakeven curve spread (bps)."
        ),
    )
    weekly_change_bps: Optional[float] = Field(
        None,
        description=(
            "5-trading-day change in the breakeven curve spread (bps)."
        ),
    )
    monthly_change_bps: Optional[float] = Field(
        None,
        description=(
            "22-trading-day (~1 month) change in the breakeven "
            "curve spread (bps)."
        ),
    )
    current_z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the breakeven "
            "curve spread (in bps) vs its own trailing window."
        ),
    )
    rolling_window_days: int = Field(
        ..., description="Window used for the z-score calculation.",
    )
    high_252d_bps: Optional[float] = Field(
        None,
        description=(
            "Highest breakeven curve spread over trailing 252 "
            "trading days (bps)."
        ),
    )
    low_252d_bps: Optional[float] = Field(
        None,
        description=(
            "Lowest breakeven curve spread over trailing 252 "
            "trading days (bps)."
        ),
    )
    percentile_252d: Optional[float] = Field(
        None,
        description=(
            "Percentile rank within trailing 252-day range (0-100)."
        ),
    )
    short_breakeven_bps: Optional[float] = Field(
        None,
        description=(
            "Latest spot bond-implied breakeven (bps) at "
            "``short_tenor`` — exposed so the desk can audit the "
            "curve-spread decomposition."
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
    long_years: float = Field(
        ...,
        description=(
            "Year fraction of ``long_tenor`` (e.g. 10.0 for '10Y')."
        ),
    )
    methodology_label: str = Field(
        ...,
        description=(
            "Wire-honesty disclosure threaded from the YAML's "
            "``methodology.what_it_does``.  Carries the explicit "
            "spread formula (long_breakeven_bps - "
            "short_breakeven_bps) AND the 'term structure of "
            "inflation compensation; not the term structure of "
            "pure expected inflation' caveat so downstream "
            "operators and the LLM cannot misread the output."
        ),
    )


class BreakevenCurveSpreadTimeSeriesRow(BaseModel):
    """Single row in the bespoke breakeven-curve-spread time-series.

    ``spread_bps`` is the breakeven curve spread in basis points;
    ``z_score`` is the rolling z-score (None for rows in the
    rolling-window warmup).
    """

    date: str
    spread_bps: float
    z_score: Optional[float] = None


class BreakevenCurveSpreadOutput(BaseModel):
    """Top-level response for the breakeven_curve_spread tool.

    ``time_series`` is the bespoke wire-frozen shape
    (``BreakevenCurveSpreadTimeSeriesRow``) for callers that want
    spread + z-score in one row.

    ``time_series_spread`` and ``time_series_zscore`` are the
    canonical closed-enum shapes the workflow / operator layer
    consumes.  All three are computed from the same underlying
    display DataFrame — they cannot drift.
    """

    current_metrics: BreakevenCurveSpreadCurrentMetrics
    time_series: List[BreakevenCurveSpreadTimeSeriesRow]
    time_series_spread: TimeSeries = Field(
        ...,
        description=(
            "Historical same-country breakeven curve spread (in "
            "BPS) over the displayed window.  Closed-enum "
            "``TimeSeriesUnits.BPS``; series_name pattern: "
            "'<nominal>_<linker>_<shortten>_<longten>_breakeven_spread'.  "
            "Values match ``time_series[i].spread_bps`` 1-to-1 by "
            "construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the breakeven curve "
            "spread (in bps) vs its own trailing window.  "
            "Closed-enum ``TimeSeriesUnits.Z_SCORE``; series_name "
            "pattern: "
            "'<nominal>_<linker>_<shortten>_<longten>_breakeven_spread_zscore'.  "
            "Values match ``time_series[i].z_score`` 1-to-1 (None "
            "for rows in the rolling-window warmup)."
        ),
    )


__all__ = [
    "BreakevenCurveSpreadInput",
    "BreakevenCurveSpreadCurrentMetrics",
    "BreakevenCurveSpreadTimeSeriesRow",
    "BreakevenCurveSpreadOutput",
]
