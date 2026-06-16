"""Pydantic schemas for the OIS butterfly (curvature) tool.

Same-curve 3-point butterfly on a SINGLE OIS par-swap curve (e.g.
USD_SOFR_OIS 2s5s10s, EUR_ESTR_OIS 2s5s10s, GBP_SONIA_OIS 2s5s10s).
Mirrors the sovereign ``butterfly`` per-tool-folder shape with three
OIS-specific differences:

  1. ``curve_family`` is restricted to a closed enum sourced from
     ``rates_agent/playbooks/ois.yml`` (USD_SOFR_OIS, EUR_ESTR_OIS,
     GBP_SONIA_OIS, JPY_OIS, AUD_OIS, CAD_OIS).  Refused at the schema
     layer for any value not in that set.  See config.yaml's
     ``planned_extensions`` for the honest disclosure of the
     JPY_TONA_OIS / AUD_AONIA_OIS / CAD_CORRA_OIS gap.
  2. ``field_name`` resolves against the YAML's
     ``default_swap_rate_field`` (= 'PX_LAST') — the OIS Bloomberg
     mid-rate field, NOT the sovereign ``default_field_name`` (=
     'YLD_YTM_MID') yield-to-maturity field.
  3. Output snapshot field names use "rate" terminology rather than
     "yield" (``short_tenor_rate`` / ``belly_tenor_rate`` /
     ``long_tenor_rate``) because OIS quotes are par swap rates, not
     bond yields — same naming choice OIS rate_level / curve_spread
     made.

Validation layering
-------------------
- ``field_name`` defaults to ``None`` — the sentinel that means "use
  the YAML's ``default_swap_rate_field`` convention".  Callers can
  still override per-query.  ``compute()`` is the one place that
  resolves the sentinel against the active config, so editing
  ``default_swap_rate_field`` in YAML actually changes runtime
  behaviour.  Mirrors the pattern established by sovereign
  curve_move_classifier (commit 9f741ea / b2605ee) and every OIS tool.

- ``lookback_days`` semantics are unchanged from the sovereign
  butterfly: it controls the *displayed* window only.  The rolling
  z-score window and the trailing range window are independent
  conventions in config.yaml.

- ``high_252d_bps`` / ``low_252d_bps`` / ``percentile_252d`` field
  names embed the trailing-range window length and are wire-frozen.
  See config.yaml's ``planned_extensions`` for the path to making the
  trailing window configurable.

Validators that encode invariants stay here in code: the
``_tenors_must_all_differ`` validator is a structural input check, not
a methodology choice, so it is NOT a config knob.

Canonical TimeSeries output
---------------------------
Per the legacy-sovereign TimeSeries tech-debt cleanup pattern: this
tool emits TWO canonical ``shared.schemas.time_series.TimeSeries``
fields matching the v6 sovereign-primitive naming convention
(``time_series_<purpose>: TimeSeries``):

  - ``time_series_butterfly: TimeSeries`` (units = BPS) — the
    historical OIS butterfly values
    (``(2 * belly - short - long) * 100``, in bps).
  - ``time_series_zscore: TimeSeries`` (units = Z_SCORE) — the rolling
    z-score of the butterfly vs its own trailing window.

The wire-frozen ``time_series: List[OISButterflyTimeSeriesRow]`` field
provides the bespoke row shape every other rates butterfly emits.
All three series are computed from the same underlying display
DataFrame and cannot drift — proven by point-by-point parity tests.
"""

from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from shared.schemas import TimeSeries


# ============================================================================
# OIS CURVE_FAMILY CLOSED FAMILY
# ============================================================================
#
# Sourced from ``rates_agent/playbooks/ois.yml`` — the authoritative
# enumeration of OIS curve families currently in the substrate.  This
# is a CLOSED FAMILY (P8 + PR8 discipline): adding a new family
# requires editing the playbook AND extending this whitelist in
# lockstep.  Cross-domain mappings (e.g. CURVE_FAMILY_TO_CURRENCY in
# ``rates_agent/ois/tools/swap_spread/schemas.py``) must also be
# updated in the same PR to avoid silent drift.
#
# Honest disclosure: the primitive_catalog's ``concept_summary`` for
# this tool references the longer overnight-index-suffixed family
# names (JPY_TONA_OIS, AUD_AONIA_OIS, CAD_CORRA_OIS).  The playbook
# uses the shorter form today (JPY_OIS, AUD_OIS, CAD_OIS); this
# whitelist mirrors the playbook exactly so a non-existent
# curve_family cannot be silently accepted.  The gap is documented
# under ``methodology.planned_extensions`` in this tool's
# ``config.yaml``.

OIS_CURVE_FAMILY = Literal[
    "USD_SOFR_OIS",
    "EUR_ESTR_OIS",
    "GBP_SONIA_OIS",
    "JPY_OIS",
    "AUD_OIS",
    "CAD_OIS",
]


class OISButterflyInput(BaseModel):
    """Parameters for computing the 3-point OIS butterfly (curvature)
    on a single OIS swap curve.

    butterfly_bps = (2 * belly_rate_pct
                     - short_rate_pct
                     - long_rate_pct) * 100

    Sign convention: POSITIVE = belly cheap (belly OIS rate HIGH
    relative to the linear interpolation of the wings); NEGATIVE =
    belly rich.  Matches the sovereign butterfly sign convention.
    """

    curve_family: OIS_CURVE_FAMILY = Field(
        ...,
        description=(
            "OIS curve family identifier — closed enum sourced from "
            "rates_agent/playbooks/ois.yml.  Accepted values: "
            "'USD_SOFR_OIS', 'EUR_ESTR_OIS', 'GBP_SONIA_OIS', "
            "'JPY_OIS', 'AUD_OIS', 'CAD_OIS'.  Any other value is "
            "refused at the schema layer.  See config.yaml's "
            "planned_extensions for the disclosure on the longer "
            "JPY_TONA_OIS / AUD_AONIA_OIS / CAD_CORRA_OIS labels."
        ),
    )
    short_tenor: str = Field(
        ...,
        description=(
            "The short wing, e.g. '2Y'.  Tenor as stored on the OIS "
            "curve family in instrument_master (OIS curves have a "
            "dense short-end grid: '1W', '1M', '3M', '6M', '1Y', "
            "'2Y', '3Y')."
        ),
    )
    belly_tenor: str = Field(
        ...,
        description=(
            "The belly (body) of the butterfly, e.g. '5Y'.  Must be "
            "strictly between short_tenor and long_tenor on the OIS "
            "curve."
        ),
    )
    long_tenor: str = Field(
        ...,
        description="The long wing, e.g. '10Y', '20Y', '30Y'.",
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
            "Bloomberg observation field.  When None (default), the "
            "tool falls through to ``default_swap_rate_field`` from "
            "config.yaml (currently 'PX_LAST').  Pass an explicit "
            "field name to override per query.  LLM/HTTP wrappers MUST "
            "translate their wire-level sentinel (empty string for MCP, "
            "missing param for FastAPI) to None before constructing "
            "this input — otherwise the YAML default is silently "
            "shadowed.  See the curve_move_classifier wrapper-shadowing "
            "fix (commit b2605ee) for the canonical pattern."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=("Optional as-of date (YYYY-MM-DD): compute as of this trade "
                     "date instead of the latest available data.  None → latest "
                     "(live snapshot).  Supply a date for a historical, replayable view."),
    )

    @model_validator(mode="after")
    def _tenors_must_all_differ(self) -> "OISButterflyInput":
        tenors = [self.short_tenor, self.belly_tenor, self.long_tenor]
        if len(set(tenors)) != 3:
            raise ValueError(
                f"short_tenor, belly_tenor, and long_tenor must all be "
                f"different, but got {tenors}."
            )
        return self


class OISButterflyCurrentMetrics(BaseModel):
    """Snapshot metrics for a 3-point OIS butterfly.

    Field names ``short_tenor_rate`` / ``belly_tenor_rate`` /
    ``long_tenor_rate`` use "rate" rather than "yield" because OIS
    quotes are par swap rates, not bond yields.  Matches OIS
    rate_level / curve_spread / cross_market_spread naming.
    """

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    curve_family: str
    butterfly_label: str = Field(
        ..., description="Human-readable label, e.g. '2s5s10s'.",
    )
    current_butterfly_bps: float = Field(
        ...,
        description=(
            "Current OIS butterfly in bps.  Positive = belly is cheap, "
            "negative = belly is rich."
        ),
    )
    daily_change_bps: Optional[float] = Field(
        None, description="1-day change in the butterfly (bps).",
    )
    current_z_score: Optional[float] = Field(
        None, description="Rolling 252-day z-score of the butterfly.",
    )
    rolling_window_days: int = Field(
        ..., description="Window used for the z-score calculation.",
    )
    high_252d_bps: Optional[float] = Field(
        None,
        description="Highest butterfly over trailing 252 trading days (bps).",
    )
    low_252d_bps: Optional[float] = Field(
        None,
        description="Lowest butterfly over trailing 252 trading days (bps).",
    )
    percentile_252d: Optional[float] = Field(
        None,
        description="Percentile rank within trailing 252-day range (0-100).",
    )
    wing_short_bps: Optional[float] = Field(
        None, description="Component spread: belly - short (bps).",
    )
    wing_long_bps: Optional[float] = Field(
        None, description="Component spread: long - belly (bps).",
    )
    short_tenor_rate: Optional[float] = Field(
        None, description="Latest OIS par-swap rate on the short wing (percent).",
    )
    belly_tenor_rate: Optional[float] = Field(
        None, description="Latest OIS par-swap rate on the belly (percent).",
    )
    long_tenor_rate: Optional[float] = Field(
        None, description="Latest OIS par-swap rate on the long wing (percent).",
    )


class OISButterflyTimeSeriesRow(BaseModel):
    """Single row in the bespoke wire-frozen OIS butterfly time-series."""

    date: str
    butterfly_bps: float
    z_score: Optional[float] = None


class OISButterflyOutput(BaseModel):
    """Top-level response for the OIS butterfly tool.

    ``time_series`` is the bespoke ``OISButterflyTimeSeriesRow`` list
    every other rates butterfly emits.  ``time_series_butterfly`` and
    ``time_series_zscore`` are the canonical closed-enum payloads so
    the upcoming primitive-to-operator bridge has uniform shapes to
    consume.  All three are computed from the same underlying display
    DataFrame — they cannot drift.
    """

    current_metrics: OISButterflyCurrentMetrics
    time_series: List[OISButterflyTimeSeriesRow]
    time_series_butterfly: TimeSeries = Field(
        ...,
        description=(
            "Historical OIS butterfly value "
            "(2*belly - short - long, in BPS) over the displayed "
            "window.  Closed-enum ``TimeSeriesUnits.BPS``; "
            "series_name = "
            "'<curve_family_lower>_<short>_<belly>_<long>_ois_butterfly'.  "
            "Values match ``time_series[i].butterfly_bps`` 1-to-1 by "
            "construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the OIS butterfly vs its "
            "own trailing window.  Closed-enum "
            "``TimeSeriesUnits.Z_SCORE``; series_name = "
            "'<curve_family_lower>_<short>_<belly>_<long>_ois_zscore'.  "
            "Values match ``time_series[i].z_score`` 1-to-1 (None for "
            "rows in the rolling-window warmup)."
        ),
    )


__all__ = [
    "OIS_CURVE_FAMILY",
    "OISButterflyInput",
    "OISButterflyCurrentMetrics",
    "OISButterflyTimeSeriesRow",
    "OISButterflyOutput",
]
