"""Pydantic schemas for the OIS cross-market spread tool.

Migrated from ``rates_agent/ois/tools/schemas/cross_market.py`` into
the per-tool-folder pattern (see
``docs/architecture/tool_architecture.md``).  Third OIS tool brought
onto the pattern (after ``rate_level`` — PR #61 — and ``curve_spread``
— PR #62 + #63).

Validation layering
-------------------
- ``field_name`` defaults to ``None`` — the sentinel that means "use
  the YAML's ``default_swap_rate_field`` convention".  Callers can
  still override per-query.  ``compute()`` is the one place that
  resolves the sentinel against the active config, so editing
  ``default_swap_rate_field`` in YAML actually changes runtime
  behaviour.  Mirrors the pattern established by sovereign
  curve_move_classifier (commit 9f741ea / b2605ee) and OIS
  rate_level / curve_spread.

- ``lookback_days`` controls the *displayed* window only.  The rolling
  z-score window and the trailing range window are independent
  conventions in config.yaml.

- ``high_252d_bps`` / ``low_252d_bps`` / ``percentile_252d`` field
  names embed the trailing-range window length and are wire-frozen
  for backward-compat with the legacy single-file tool.  See
  config.yaml's ``planned_extensions`` for the path to making the
  trailing window configurable.

Validators that encode invariants stay here in code: the
``_curves_must_differ`` validator is a structural input check, not a
methodology choice, so it is NOT a config knob.

Canonical TimeSeries output
---------------------------
Per the legacy-sovereign TimeSeries tech-debt cleanup pattern: this
tool emits TWO canonical ``shared.schemas.time_series.TimeSeries``
fields matching the v6 sovereign-primitive naming convention:

  - ``time_series_spread: TimeSeries`` (units = BPS) — the historical
    spread values (curve_family_1 − curve_family_2, in bps).
  - ``time_series_zscore: TimeSeries`` (units = Z_SCORE) — the rolling
    z-score of the spread vs its own trailing window.

The wire-frozen ``time_series: List[OISCrossMarketSpreadTimeSeriesRow]``
field stays for backward-compat with the legacy single-file tool's
output shape; the canonical fields are what the upcoming
primitive-to-operator bridge consumes.  All three series are computed
from the same underlying display DataFrame and cannot drift —
proven by point-by-point parity tests.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from shared.schemas import TimeSeries


class OISCrossMarketSpreadInput(BaseModel):
    """Parameters for computing the rate differential between the same
    tenor on two different OIS curves (e.g. SOFR 2Y − ESTR 2Y).

    spread = (cf1_rate − cf2_rate) × 100  (bps)
    """

    curve_family_1: str = Field(
        ...,
        description=(
            "First (numerator) OIS curve.  spread = curve_family_1 − "
            "curve_family_2.  Examples: 'USD_SOFR_OIS', 'EUR_ESTR_OIS', "
            "'GBP_SONIA_OIS', 'JPY_OIS', 'AUD_OIS', 'CAD_OIS'."
        ),
    )
    curve_family_2: str = Field(
        ...,
        description=(
            "Second (denominator) OIS curve.  Must differ from "
            "curve_family_1."
        ),
    )
    tenor: str = Field(
        ...,
        description=(
            "Tenor point to compare.  Examples: '1M', '3M', '6M', "
            "'1Y', '2Y', '5Y', '10Y'."
        ),
    )
    lookback_days: int = Field(
        default=365, ge=30, le=7300,
        description=(
            "Calendar days of displayed history in the time_series "
            "output.  Defaults to 365 (1 year).  The z-score rolling "
            "window is always a fixed 252 trading days regardless of "
            "this value (fixed by config convention "
            "z_score_window_days)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field.  When None (default), the "
            "tool falls through to ``default_swap_rate_field`` from "
            "config.yaml (currently 'PX_LAST').  Pass an explicit "
            "field name to override per query.  LLM/HTTP wrappers MUST "
            "translate their wire-level sentinel (empty string for "
            "MCP, missing param for FastAPI) to None before "
            "constructing this input — otherwise the YAML default is "
            "silently shadowed.  See the curve_move_classifier "
            "wrapper-shadowing fix (commit b2605ee) for the canonical "
            "pattern."
        ),
    )

    @model_validator(mode="after")
    def _curves_must_differ(self) -> "OISCrossMarketSpreadInput":
        if self.curve_family_1 == self.curve_family_2:
            raise ValueError(
                f"curve_family_1 and curve_family_2 must be different, "
                f"but both are '{self.curve_family_1}'.  For same-curve "
                "tenor spreads, use the OIS curve_spread tool instead."
            )
        return self


class OISCrossMarketSpreadCurrentMetrics(BaseModel):
    """Snapshot metrics for an OIS cross-market rate differential.

    Field names ``curve_family_1_rate`` / ``curve_family_2_rate`` use
    "rate" rather than "yield" because OIS quotes are par swap rates,
    not bond yields.  Wire-frozen for backward-compat with the legacy
    single-file OIS cross_market_spread tool.
    """

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    curve_family_1: str
    curve_family_2: str
    tenor: str
    spread_label: str = Field(
        ..., description="Human-readable label, e.g. 'USD_SOFR_OIS-EUR_ESTR_OIS 2Y'.",
    )
    current_spread_bps: float = Field(
        ..., description="Current rate differential in basis points.",
    )
    daily_change_bps: Optional[float] = Field(
        None, description="1-day change in the spread (bps).",
    )
    weekly_change_bps: Optional[float] = Field(
        None, description="5-trading-day change in the spread (bps).",
    )
    monthly_change_bps: Optional[float] = Field(
        None, description="22-trading-day change in the spread (bps).",
    )
    current_z_score: Optional[float] = Field(
        None, description="Rolling 252-day z-score of the spread.",
    )
    rolling_window_days: int = Field(
        ..., description="Window used for the z-score calculation.",
    )
    high_252d_bps: Optional[float] = Field(
        None,
        description="Highest spread over trailing 252 trading days (bps).",
    )
    low_252d_bps: Optional[float] = Field(
        None,
        description="Lowest spread over trailing 252 trading days (bps).",
    )
    percentile_252d: Optional[float] = Field(
        None,
        description="Percentile rank within trailing 252-day range (0-100).",
    )
    curve_family_1_rate: Optional[float] = Field(
        None, description="Latest par swap rate on curve_family_1 (percent).",
    )
    curve_family_2_rate: Optional[float] = Field(
        None, description="Latest par swap rate on curve_family_2 (percent).",
    )


class OISCrossMarketSpreadTimeSeriesRow(BaseModel):
    """Single row in the bespoke wire-frozen OIS cross-market spread
    time-series array.
    """

    date: str
    spread_bps: float
    z_score: Optional[float] = None


class OISCrossMarketSpreadOutput(BaseModel):
    """Top-level response the MCP server returns to the orchestrator.

    ``time_series`` is the wire-frozen bespoke shape preserved for
    backward-compat with the legacy single-file tool.
    ``time_series_spread`` and ``time_series_zscore`` are the canonical
    closed-enum payloads added by this migration so the upcoming
    primitive-to-operator bridge has uniform shapes to consume.  All
    three are computed from the same underlying display DataFrame —
    they cannot drift.
    """

    current_metrics: OISCrossMarketSpreadCurrentMetrics
    time_series: List[OISCrossMarketSpreadTimeSeriesRow]
    time_series_spread: TimeSeries = Field(
        ...,
        description=(
            "Historical OIS cross-market spread (curve_family_1 − "
            "curve_family_2 par-swap rate, in BPS) over the displayed "
            "window.  Closed-enum ``TimeSeriesUnits.BPS``; series_name "
            "= '<cf1_lower>_<cf2_lower>_<tenor_lower>_ois_cross_spread'.  "
            "Values match ``time_series[i].spread_bps`` 1-to-1 by "
            "construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the OIS cross-market spread "
            "vs its own trailing window.  Closed-enum "
            "``TimeSeriesUnits.Z_SCORE``; series_name = "
            "'<cf1_lower>_<cf2_lower>_<tenor_lower>_ois_cross_zscore'.  "
            "Values match ``time_series[i].z_score`` 1-to-1 (None for "
            "rows in the rolling-window warmup)."
        ),
    )


__all__ = [
    "OISCrossMarketSpreadInput",
    "OISCrossMarketSpreadCurrentMetrics",
    "OISCrossMarketSpreadTimeSeriesRow",
    "OISCrossMarketSpreadOutput",
]
