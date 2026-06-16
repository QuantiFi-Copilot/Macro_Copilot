"""Pydantic schemas for the swap_breakeven_basis_simple tool.

Fifth tool in the ``inflation_swaps`` domain.  Owns the desk concept
of a *swap-breakeven basis*: the same-tenor, same-currency
difference between a zero-coupon inflation swap (ZCIS) rate and a
generic linker bond-implied breakeven inflation rate at the same
pillar.

Construction rule
-----------------
``basis_pct = zcis_pct - breakeven_pct``
``basis_bps = basis_pct * 100``

where ``zcis_pct`` is the par ZCIS rate at ``tenor`` on
``zcis_curve_family`` (computed by
``calculate_inflation_swap_rate_level``) and ``breakeven_pct`` is
the same-tenor, same-currency generic bond-implied breakeven
``nominal_yield_pct - real_yield_pct`` for the
``(nominal_curve_family, linker_curve_family)`` pair (computed by
``calculate_breakeven_inflation_simple``).

Concept honesty
---------------
This is NOT a clean liquidity-premium read.  The basis between a
ZCIS rate and a linker-implied breakeven also reflects:

  - index-lag differences between ZCIS conventions (e.g. 3M USD,
    3M EUR, 2M GBP) and the linker bond's realised CPI accrual
    convention,
  - linker bond on-the-run / liquidity premium effects in the
    nominal-vs-real decomposition,
  - structural ZCIS vs linker-breakeven basis present even in
    benign markets.

The honesty disclosure lives in the YAML's
``methodology.what_it_does`` and is threaded onto the wire via
``current_metrics.methodology_label``.  That field is sourced from
the YAML at runtime — NOT hardcoded as a Python literal — so a
YAML edit flows through to runtime behaviour.

Sign convention (canonical, wire-frozen)
----------------------------------------
``basis = zcis_pct - breakeven_pct``.  Per the catalog's
methodology_guardrails (``Must use the canonical sign convention
ZCIS minus breakeven``, ``Must not silently invert the sign``),
the convention is wire-locked in YAML at
``swap_breakeven_basis_sign_convention=zcis_minus_breakeven`` and
the compute layer raises ``NotImplementedError`` if the value is
ever set to anything else.

Same-currency invariant (V1 wire freeze)
----------------------------------------
The basis primitive is a *same-currency* object in V1: the ZCIS
leg's currency must match the breakeven primitive's underlying
country/currency identity.  Cross-currency basis (e.g. USD_ZCIS
minus EUR linker breakeven) is documented in
``methodology.planned_extensions`` but not exposed as a knob — it
would be a separate primitive shape.

The schema rejects ``nominal_curve_family == linker_curve_family``
at the basis layer (mirrors the breakeven primitive's structural
guard so errors surface at the swap-basis layer with a precise
re-route hint).  The same-country invariant on the breakeven leg
itself (e.g. nominal=UST + linker=EUR_FR_LINKER would be refused)
is enforced inside ``calculate_breakeven_inflation_simple`` and
inherited transitively here — no need to re-implement it.

Validation layering
-------------------
- ``field_name`` defaults to ``None`` — the sentinel that resolves
  against each inner primitive's own YAML default.  Mirrors the
  empty-string / None-sentinel pattern established by sibling
  rates tools.

- ``lookback_days`` controls the displayed window only (the
  bespoke ``time_series`` row count and the canonical TimeSeries
  length).  Does NOT control the rolling z-score window or the
  trailing range window — those are independent conventions in
  ``config.yaml``.

Cross-field invariants stay here in code:

- ``_curve_families_must_differ`` enforces
  ``nominal_curve_family != linker_curve_family`` at the basis
  layer (structural — same-issuer linker reads belong to a
  different concept entirely).
- ``_tenor_must_parse`` enforces ``tenor`` parses successfully via
  ``shared.analytics.curve_bootstrap.tenor_to_years`` so an unknown
  pillar is rejected with a precise error before any compute work.

Canonical TimeSeries output
---------------------------
This tool emits TWO canonical
``shared.schemas.time_series.TimeSeries`` payloads alongside the
bespoke ``time_series`` list:

  - ``time_series_basis`` (units = BPS) — the swap-breakeven basis
    series over the displayed window.  The basis is a spread
    object, NOT a level; it ships in BPS to mirror the sibling
    spread primitives' convention.  series_name pattern:
    ``<zcis_lower>_<nominal_lower>_<linker_lower>_<tenor_lower>_swap_breakeven_basis``.

  - ``time_series_zscore`` (units = Z_SCORE) — the rolling z-score
    of the basis series over the displayed window.  series_name
    pattern:
    ``<zcis_lower>_<nominal_lower>_<linker_lower>_<tenor_lower>_swap_breakeven_basis_zscore``.

All three (bespoke + two canonical) share the same display
DataFrame so they cannot drift.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.analytics.curve_bootstrap import tenor_to_years
from shared.schemas import TimeSeries


class SwapBreakevenBasisSimpleInput(BaseModel):
    """Parameters for computing a swap-breakeven basis.

    basis_pct = zcis_pct - breakeven_pct
    basis_bps = basis_pct * 100

    Same-currency invariant
    -----------------------
    The ZCIS leg's currency and the breakeven leg's currency must
    align (e.g. USD_ZCIS with UST/USD_TIPS, EUR_ZCIS with
    FR_OAT/EUR_FR_LINKER, GBP_ZCIS with UK_GILT/GBP_LINKER).
    Cross-currency basis is documented in
    ``methodology.planned_extensions`` but not exposed as a knob in
    V1.
    """

    model_config = ConfigDict(extra="forbid")

    zcis_curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "Inflation-swap curve family identifier as stored in "
            "instrument_master.  Examples: 'USD_ZCIS' (US CPI-U "
            "zero-coupon inflation swaps), 'EUR_ZCIS' (euro-area "
            "HICP ex-tobacco ZCIS), 'GBP_ZCIS' (UK RPI ZCIS).  See "
            "rates_agent/playbooks/inflation_swaps.yml for the "
            "ingested universe.  This leg is filtered with "
            "instrument_type='inflation_swap' AND "
            "pricing_type='zero_coupon_breakeven' on the inner DB "
            "read (the four-conjunct guard inherited transitively "
            "from ``calculate_inflation_swap_rate_level``)."
        ),
    )
    nominal_curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "Nominal sovereign curve family identifier feeding the "
            "breakeven leg.  Examples: 'UST' (paired with USD_TIPS "
            "for USD basis), 'FR_OAT' (paired with "
            "EUR_FR_LINKER for EUR/FR basis), 'UK_GILT' (paired with "
            "GBP_LINKER for GBP basis).  Filtered with "
            "instrument_type='sovereign_benchmark' inside the "
            "composed breakeven primitive.  Same-country invariant "
            "WITHIN the breakeven leg (nominal/linker share country "
            "+ currency) is enforced inside the breakeven "
            "primitive's compute and inherited transitively here."
        ),
    )
    linker_curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "Sovereign linker curve family identifier feeding the "
            "breakeven leg.  Examples: 'USD_TIPS', 'EUR_FR_LINKER', "
            "'GBP_LINKER'.  Must differ from ``nominal_curve_family``; "
            "same-issuer linker reads belong to a different concept "
            "(``real_yield_level``) and are rejected here at the "
            "schema layer."
        ),
    )
    tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Single tenor pillar shared by the ZCIS leg and both "
            "breakeven legs (e.g. '5Y', '10Y').  Validated at the "
            "schema layer via "
            "``shared.analytics.curve_bootstrap.tenor_to_years`` so "
            "unknown pillars are rejected with a precise error "
            "before any compute work.  Available tenors are "
            "country-specific intersections of the ZCIS grid (1Y / "
            "2Y / 3Y / 5Y / 10Y / 20Y / 30Y on each ZCIS curve) "
            "and the linker / nominal grids."
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
            "Bloomberg observation field threaded into BOTH inner "
            "calls (the ZCIS level call AND the breakeven call).  "
            "When None (default), each inner primitive falls "
            "through to its own bundled YAML default — the ZCIS "
            "level uses ``default_zcis_rate_field`` (PX_MID), the "
            "breakeven uses ``default_field_name`` (YLD_YTM_MID).  "
            "Pass an explicit field name to override per query; "
            "the same value is threaded into both inner calls.  "
            "Empty string '' is coerced to None at the schema "
            "layer so MCP/HTTP wrappers can pass their wire-level "
            "sentinel through unchanged.  See the "
            "curve_move_classifier wrapper-shadowing fix (commit "
            "b2605ee) for the canonical pattern."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=(
            "Optional as-of date (YYYY-MM-DD): compute as of this trade "
            "date instead of the latest available data.  None → latest "
            "(live snapshot).  Supply a date for a historical, replayable "
            "view.  Threaded into BOTH inner calls (the ZCIS level leg AND "
            "the breakeven leg) so both legs are read as of the same trade "
            "date by construction."
        ),
    )

    @field_validator("field_name", mode="before")
    @classmethod
    def _coerce_empty_field_name(cls, v):
        """Coerce empty / whitespace-only strings to None so each
        inner primitive's YAML default resolves through compute even
        when a wrapper forwards its empty-string sentinel directly
        into the schema.
        """
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @model_validator(mode="after")
    def _curve_families_must_differ(
        self,
    ) -> "SwapBreakevenBasisSimpleInput":
        if self.nominal_curve_family == self.linker_curve_family:
            raise ValueError(
                f"nominal_curve_family and linker_curve_family must "
                f"be different, but both are "
                f"'{self.nominal_curve_family}'.  Pass the nominal "
                "sovereign curve as ``nominal_curve_family`` (e.g. "
                "'UST') and the matching linker curve as "
                "``linker_curve_family`` (e.g. 'USD_TIPS').  This "
                "primitive composes the breakeven primitive on the "
                "(nominal, linker) pair; identical legs would "
                "collapse the breakeven leg to zero.  For a "
                "cross-curve ZCIS comparison rather than a basis "
                "decomposition, use the cross_market_inflation_swap_spread "
                "primitive instead (MCP tool: "
                "calculate_cross_market_inflation_swap_spread_tool)."
            )
        return self

    @model_validator(mode="after")
    def _tenor_must_parse(
        self,
    ) -> "SwapBreakevenBasisSimpleInput":
        try:
            _ = tenor_to_years(self.tenor)
        except ValueError as exc:
            raise ValueError(
                f"Could not parse tenor '{self.tenor}' for "
                f"swap-breakeven basis "
                f"({self.zcis_curve_family} - "
                f"{self.nominal_curve_family}/"
                f"{self.linker_curve_family}): {exc}.  Accepted "
                "forms are '<n>W', '<n>M', '<n>Y' (the current "
                "ingested ZCIS grid is 1Y / 2Y / 3Y / 5Y / 10Y / "
                "20Y / 30Y)."
            ) from exc
        return self


class SwapBreakevenBasisSimpleCurrentMetrics(BaseModel):
    """Snapshot metrics for a swap-breakeven basis.

    Carries the basis (in pct AND bps) PLUS each leg's load-bearing
    reference / decomposition metadata so the desk reader can
    interpret the basis under the index-family caveat without a
    second tool call.

    The ``methodology_label`` field carries the wire-honesty
    disclosure sourced from the YAML's
    ``methodology.what_it_does`` — NOT a hardcoded Python literal —
    so a YAML edit to the disclosure flows through to runtime.
    """

    as_of_date: str = Field(
        ...,
        description="Most recent aligned trade date (YYYY-MM-DD).",
    )
    zcis_curve_family: str
    nominal_curve_family: str
    linker_curve_family: str
    tenor: str
    tenor_years: float = Field(
        ...,
        description=(
            "Year fraction of ``tenor`` (e.g. 5.0 for '5Y'), parsed "
            "via ``shared.analytics.curve_bootstrap.tenor_to_years``."
        ),
    )
    basis_label: str = Field(
        ...,
        description=(
            "Human-readable label, e.g. "
            "'USD_ZCIS - UST/USD_TIPS 10Y swap-breakeven basis'."
        ),
    )
    basis_pct: float = Field(
        ...,
        description=(
            "Current swap-breakeven basis in PERCENT "
            "(zcis_pct - breakeven_pct).  Inflation-swap rate "
            "minus the same-tenor, same-currency generic linker "
            "bond-implied breakeven; NOT a clean liquidity-premium "
            "read (also reflects index-lag, linker on-the-run, and "
            "structural ZCIS basis effects)."
        ),
    )
    basis_bps: float = Field(
        ...,
        description=(
            "Current swap-breakeven basis in basis points "
            "(basis_pct * 100)."
        ),
    )
    zcis_pct: Optional[float] = Field(
        None,
        description=(
            "Latest ZCIS rate (percent) used to form the basis — "
            "exposed so the desk can audit the decomposition."
        ),
    )
    breakeven_pct: Optional[float] = Field(
        None,
        description=(
            "Latest generic bond-implied breakeven (percent) at "
            "the same tenor — surfaced for transparency."
        ),
    )
    breakeven_bps: Optional[float] = Field(
        None,
        description=(
            "Latest generic bond-implied breakeven (basis points) "
            "at the same tenor."
        ),
    )
    nominal_yield_pct: Optional[float] = Field(
        None,
        description=(
            "Latest nominal sovereign yield (percent) used to form "
            "the breakeven leg — passed through from the breakeven "
            "primitive's surface for end-to-end transparency."
        ),
    )
    real_yield_pct: Optional[float] = Field(
        None,
        description=(
            "Latest sovereign linker real yield (percent) used to "
            "form the breakeven leg — passed through from the "
            "breakeven primitive's surface."
        ),
    )
    change_1d_bps: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in the swap-breakeven basis (bps)."
        ),
    )
    change_1w_bps: Optional[float] = Field(
        None,
        description=(
            "5-trading-day change in the swap-breakeven basis (bps)."
        ),
    )
    change_1m_bps: Optional[float] = Field(
        None,
        description=(
            "22-trading-day (~1 month) change in the swap-breakeven "
            "basis (bps)."
        ),
    )
    z_score_252d: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the swap-breakeven "
            "basis (in bps) vs its own trailing window."
        ),
    )
    high_252d_bps: Optional[float] = Field(
        None,
        description=(
            "Highest swap-breakeven basis over trailing 252 trading "
            "days (bps)."
        ),
    )
    low_252d_bps: Optional[float] = Field(
        None,
        description=(
            "Lowest swap-breakeven basis over trailing 252 trading "
            "days (bps)."
        ),
    )
    percentile_252d: Optional[float] = Field(
        None,
        description="Percentile rank within trailing 252-day range (0-100).",
    )
    observation_count: int = Field(
        ...,
        description=(
            "Number of trading days in the displayed lookback_days "
            "window after inner-join alignment."
        ),
    )
    zcis_inflation_index_family: str = Field(
        ...,
        description=(
            "Inflation index family the ZCIS leg references (e.g. "
            "'US_CPI_URBAN' for USD_ZCIS, 'EU_HICP' for EUR_ZCIS, "
            "'UK_RPI' for GBP_ZCIS).  Surfaced so the desk reader "
            "can interpret the basis under the correct ZCIS "
            "convention."
        ),
    )
    zcis_index_lag: str = Field(
        ...,
        description=(
            "Indexation lag the ZCIS leg references (e.g. '3M' for "
            "USD_ZCIS / EUR_ZCIS, '2M' for GBP_ZCIS).  Surfaced "
            "because index-lag differences between the ZCIS "
            "convention and the linker's realised CPI accrual are "
            "one of the load-bearing drivers of the basis."
        ),
    )
    zcis_interpolation: str = Field(
        ...,
        description=(
            "Index-fixing interpolation convention the ZCIS leg "
            "uses (e.g. 'Daily' for USD_ZCIS, 'Monthly' for "
            "EUR_ZCIS / GBP_ZCIS)."
        ),
    )
    zcis_underlying_index: Optional[str] = Field(
        None,
        description=(
            "Underlying inflation index Bloomberg ticker on the "
            "ZCIS leg.  Examples: 'CPURNSA Index' (USD_ZCIS), "
            "'CPTFEMU Index' (EUR_ZCIS), 'UKRPI Index' (GBP_ZCIS)."
        ),
    )
    linker_inflation_index_family: Optional[str] = Field(
        None,
        description=(
            "Inflation index family the linker leg references — "
            "pulled from the breakeven primitive's surface when "
            "available.  Examples (when surfaced): 'US_CPI_URBAN' "
            "(USD_TIPS), 'EU_HICP' (EUR_*_LINKER), 'UK_RPI' "
            "(GBP_LINKER).  Empty / None when the breakeven "
            "primitive's current public surface does not yet "
            "expose this metadata; the basis primitive consumes "
            "what is available without re-fetching from "
            "instrument_master."
        ),
    )
    linker_index_lag: Optional[str] = Field(
        None,
        description=(
            "Indexation lag the linker leg references — pulled "
            "from the breakeven primitive's surface when "
            "available.  Empty / None when the breakeven "
            "primitive's current public surface does not yet "
            "expose this metadata."
        ),
    )
    index_families_match: bool = Field(
        ...,
        description=(
            "Derived top-level summary of the per-leg index-family "
            "comparison.  True iff "
            "``zcis_inflation_index_family == "
            "linker_inflation_index_family`` AND both per-leg "
            "values are non-empty.  False otherwise — including "
            "the common case where the linker primitive does not "
            "yet surface its index family on the wire (treated as "
            "False so the caveat is visible to the desk reader).  "
            "Surfaced at top-level on current_metrics so a desk "
            "reader can read the load-bearing index-family caveat "
            "from one field rather than reconciling two per-leg "
            "strings."
        ),
    )
    index_family_caveat: Optional[str] = Field(
        None,
        description=(
            "Human-readable one-sentence caveat the wire surfaces "
            "when ``index_families_match`` is False.  Names BOTH "
            "leg's index families verbatim (or notes when the "
            "linker leg's index family is not surfaced) and warns "
            "that the basis is NOT a clean basis read because the "
            "legs reference different inflation indices.  None "
            "when ``index_families_match`` is True."
        ),
    )
    methodology_label: str = Field(
        ...,
        description=(
            "Wire-honesty disclosure threaded from the YAML's "
            "``methodology.what_it_does``.  Spells out the basis "
            "formula, composition pattern, alignment discipline, "
            "the LOAD-BEARING basis caveat (NOT a clean liquidity-"
            "premium read; reflects index-lag differences, linker "
            "on-the-run / liquidity effects, AND structural ZCIS "
            "basis), AND the canonical sign convention "
            "``zcis_minus_breakeven`` so downstream operators and "
            "the LLM cannot misread the output."
        ),
    )


class SwapBreakevenBasisSimpleTimeSeriesRow(BaseModel):
    """Single row in the bespoke swap-breakeven basis time-series.

    ``basis_pct`` / ``basis_bps`` are the basis in percent / basis
    points; ``zcis_pct`` and ``breakeven_pct`` are the per-leg
    inputs used to form the basis.
    """

    date: str
    basis_pct: float
    basis_bps: float
    zcis_pct: Optional[float] = None
    breakeven_pct: Optional[float] = None


class SwapBreakevenBasisSimpleOutput(BaseModel):
    """Top-level response for the swap_breakeven_basis_simple tool.

    ``time_series`` is the bespoke wire-frozen shape
    (``SwapBreakevenBasisSimpleTimeSeriesRow``) for callers that
    want basis + per-leg inputs in one row.

    ``time_series_basis`` and ``time_series_zscore`` are the
    canonical closed-enum shapes the workflow / operator layer
    consumes.  The basis is a spread object (NOT a level), so it
    ships in BPS to mirror sibling spread primitives (do NOT ship
    in PERCENT — that's the level convention).  All three are
    computed from the same underlying display DataFrame — they
    cannot drift.
    """

    current_metrics: SwapBreakevenBasisSimpleCurrentMetrics
    time_series: List[SwapBreakevenBasisSimpleTimeSeriesRow]
    time_series_basis: TimeSeries = Field(
        ...,
        description=(
            "Historical swap-breakeven basis (in BPS) over the "
            "displayed window.  Closed-enum "
            "``TimeSeriesUnits.BPS``; series_name pattern: "
            "'<zcis_lower>_<nominal_lower>_<linker_lower>_"
            "<tenor_lower>_swap_breakeven_basis'.  Values match "
            "``time_series[i].basis_bps`` 1-to-1 by construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the swap-breakeven "
            "basis (in bps) vs its own trailing window.  "
            "Closed-enum ``TimeSeriesUnits.Z_SCORE``; series_name "
            "pattern: '<zcis_lower>_<nominal_lower>_<linker_lower>_"
            "<tenor_lower>_swap_breakeven_basis_zscore'.  None for "
            "rows in the rolling-window warmup."
        ),
    )


__all__ = [
    "SwapBreakevenBasisSimpleInput",
    "SwapBreakevenBasisSimpleCurrentMetrics",
    "SwapBreakevenBasisSimpleTimeSeriesRow",
    "SwapBreakevenBasisSimpleOutput",
]
