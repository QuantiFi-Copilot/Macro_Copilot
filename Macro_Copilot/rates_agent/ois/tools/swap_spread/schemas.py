"""Pydantic schemas for the cross-domain swap-spread tool.

A CROSS-DOMAIN primitive — consumes one sovereign-curve leg and one
OIS-curve leg at the same tenor, and emits the asset-swap-spread
style differential ``(sovereign_yield − ois_rate) × 100`` in bps.

Why this primitive exists separately from cross_market_spread
-------------------------------------------------------------
``cross_market_spread`` (sovereign + OIS variants both) is a
SAME-DOMAIN primitive: two curves of the same type, single
``field_name`` for both legs.  The cross-domain spread cannot use
the same shape because the two legs use different field_name
mnemonics on the wire (sovereign ``YLD_YTM_MID`` vs OIS
``PX_LAST``).

Currency-match enforcement
--------------------------
A swap spread is only desk-meaningful when the two legs are in the
SAME CURRENCY (UST vs USD_SOFR_OIS, BUND vs EUR_ESTR_OIS, etc.).
Cross-currency pairings (UST vs EUR_ESTR_OIS) are desk-nonsensical
— they conflate level differences across currencies with the rich/
cheap signal the asset-swap spread is trying to capture.  Per the
``desk_invariant_primitive`` discipline (the tool category in
``config.yaml``), this assumption is load-bearing and is enforced
at the schema layer via ``CURVE_FAMILY_TO_CURRENCY`` +
``_currencies_must_match`` below.

The currency mapping is a closed family in v1: adding a new
sovereign or OIS curve requires extending the mapping AND
updating the SQL admission gate (which consumes the same mapping)
in lockstep.  This is the same closed-family discipline applied
to ``LineageStep`` and ``MissingnessPolicy``.

Validation layering
-------------------
- ``sovereign_field_name`` and ``ois_field_name`` BOTH default to
  ``None`` — the sentinels resolve to ``sovereign_leg_default_field``
  and ``ois_leg_default_field`` from the bundled ``config.yaml``.
  Mirrors the OIS migrations' wrapper-shadowing fix pattern (commit
  b2605ee).

- ``sovereign_curve_family`` and ``ois_curve_family`` are NOT
  validated for cross-domain membership at the schema layer
  (because the schema layer doesn't know the universe of OIS vs
  sovereign curves).  Instead, the compute layer's fetcher
  (``fetch_cross_domain_pair``) issues a query that requires both
  curves to exist with their respective field_names; if either leg
  has no rows, ``compute()`` returns a controlled error envelope.

- ``lookback_days`` controls the *displayed* window only.  Same
  semantics as the other rates primitives.

- ``high_252d_bps`` / ``low_252d_bps`` / ``percentile_252d`` field
  names embed the trailing-range window length and are wire-frozen.
  See ``config.yaml``'s ``planned_extensions``.

Canonical TimeSeries output
---------------------------
Per the legacy-sovereign TimeSeries tech-debt cleanup pattern: this
tool emits TWO canonical ``shared.schemas.time_series.TimeSeries``
fields:

  - ``time_series_spread: TimeSeries`` (units = BPS) — the
    historical spread values in bps.
  - ``time_series_zscore: TimeSeries`` (units = Z_SCORE) — the
    rolling z-score of the spread vs its own trailing window.

The wire-frozen ``time_series: List[SwapSpreadTimeSeriesRow]`` field
provides the consistent shape every other rates primitive emits.
All three series are computed from the same underlying display
DataFrame and cannot drift.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from shared.schemas import TimeSeries


# ============================================================================
# CURRENCY CLOSED FAMILY
# ============================================================================
#
# A swap spread is only desk-meaningful when the two legs are in the
# SAME CURRENCY.  Cross-currency pairings (e.g. UST vs EUR_ESTR_OIS)
# conflate level differences across currencies with the rich/cheap
# signal the asset-swap spread is trying to capture, and labelling
# such a number a "swap spread" violates the
# ``desk_invariant_primitive`` discipline declared in this tool's
# ``config.yaml``.
#
# CURVE_FAMILY_TO_CURRENCY is the SINGLE SOURCE OF TRUTH for the
# currency mapping.  The schema validator below consumes it; the
# SQL admission gate (``test_swap_spread_sql_validation.py``)
# imports it.  Adding a new sovereign or OIS curve requires
# extending this dict in lockstep with the SQL gate's regression
# cases — same closed-family discipline as ``LineageStep`` and
# ``MissingnessPolicy``.

CURVE_FAMILY_TO_CURRENCY: dict[str, str] = {
    # Sovereign curves
    "UST":     "USD",
    "DE_BUND": "EUR",
    "FR_OAT":  "EUR",
    "IT_BTP":  "EUR",
    "ES_BONO": "EUR",
    "UK_GILT": "GBP",
    "JGB":     "JPY",
    # OIS curves
    "USD_SOFR_OIS":  "USD",
    "EUR_ESTR_OIS":  "EUR",
    "GBP_SONIA_OIS": "GBP",
    "JPY_OIS":       "JPY",
    "AUD_OIS":       "AUD",
    "CAD_OIS":       "CAD",
}


def currency_for_curve(curve_family: str) -> str:
    """Return the currency code for a known curve family.

    Raises ValueError if the curve_family isn't in the closed
    mapping — a deliberately loud failure so a typo or a newly-
    added curve is caught at validator time rather than silently
    accepted.
    """
    try:
        return CURVE_FAMILY_TO_CURRENCY[curve_family]
    except KeyError:
        known = sorted(CURVE_FAMILY_TO_CURRENCY.keys())
        raise ValueError(
            f"curve_family={curve_family!r} is not in the swap-spread "
            f"currency mapping.  Known: {known}.  To support a new "
            "curve family, extend CURVE_FAMILY_TO_CURRENCY in "
            "rates_agent/ois/tools/swap_spread/schemas.py AND add a "
            "regression case to the SQL admission gate "
            "(tests/test_swap_spread_sql_validation.py)."
        )


class SwapSpreadInput(BaseModel):
    """Parameters for computing the cross-domain swap spread between
    a sovereign yield curve and an OIS curve at the same tenor.

    Sign convention: spread = (sovereign_yield − ois_rate) × 100
    in bps.  Positive means the sovereign trades CHEAP to OIS
    (asset-swap-spread convention).  The sign is locked in code;
    the validator does NOT support inverting it.
    """

    sovereign_curve_family: str = Field(
        ...,
        description=(
            "Sovereign curve family for the cash-bond leg.  "
            "Examples: 'UST', 'DE_BUND', 'IT_BTP', 'FR_OAT', "
            "'UK_GILT', 'JGB'."
        ),
    )
    ois_curve_family: str = Field(
        ...,
        description=(
            "OIS curve family for the swap leg.  Examples: "
            "'USD_SOFR_OIS', 'EUR_ESTR_OIS', 'GBP_SONIA_OIS', "
            "'JPY_OIS'.  Should be the OIS curve in the same "
            "currency as the sovereign leg (caller responsibility — "
            "the primitive does not enforce currency match)."
        ),
    )
    tenor: str = Field(
        ...,
        description=(
            "Tenor point.  Examples: '1Y', '2Y', '5Y', '10Y', '30Y'.  "
            "The same tenor is used for both legs."
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
    sovereign_field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field for the sovereign leg.  "
            "When None (default), falls through to "
            "``sovereign_leg_default_field`` from config.yaml "
            "(currently 'YLD_YTM_MID').  LLM/HTTP wrappers MUST "
            "translate their wire-level sentinel (empty string for "
            "MCP, missing param for FastAPI) to None before "
            "constructing this input — otherwise the YAML default is "
            "silently shadowed (see curve_move_classifier wrapper-"
            "shadowing fix, commit b2605ee)."
        ),
    )
    ois_field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field for the OIS leg.  When None "
            "(default), falls through to ``ois_leg_default_field`` "
            "from config.yaml (currently 'PX_LAST').  Same sentinel "
            "discipline as sovereign_field_name."
        ),
    )

    @model_validator(mode="after")
    def _validate_pair(self) -> "SwapSpreadInput":
        """Two checks: curves must differ AND must be in the same
        currency.

        The same-currency check enforces the
        ``desk_invariant_primitive`` discipline: a swap spread is
        only desk-meaningful when both legs are in the same currency
        (e.g. UST vs USD_SOFR_OIS, BUND vs EUR_ESTR_OIS).  Cross-
        currency pairings (UST vs EUR_ESTR_OIS) conflate currency-
        level differences with the rich/cheap signal the asset-swap
        spread is trying to capture, and labelling such a number a
        "swap spread" would silently lie to the desk.

        We do NOT validate that the two curves belong to different
        DOMAINS (sovereign vs OIS) at the schema layer — the
        currency mapping naturally keeps a sovereign on one side
        and an OIS on the other.  The compute layer's fetcher
        handles missing-leg cases via controlled error envelopes."""
        # 1. Curves must differ (string-identity check).
        if self.sovereign_curve_family == self.ois_curve_family:
            raise ValueError(
                f"sovereign_curve_family and ois_curve_family must be "
                f"different, but both are "
                f"'{self.sovereign_curve_family}'.  For same-domain "
                f"spreads use the cross_market_spread tool instead."
            )
        # 2. Currencies must match — desk-invariant assumption.
        sov_ccy = currency_for_curve(self.sovereign_curve_family)
        ois_ccy = currency_for_curve(self.ois_curve_family)
        if sov_ccy != ois_ccy:
            raise ValueError(
                f"swap_spread requires same-currency legs: "
                f"sovereign={self.sovereign_curve_family} ({sov_ccy}) "
                f"vs ois={self.ois_curve_family} ({ois_ccy}).  Cross-"
                f"currency swap spreads are not desk-meaningful — they "
                f"conflate currency-level differences with the "
                f"asset-swap rich/cheap signal.  Use a cross-market "
                f"spread (sovereign vs sovereign, or OIS vs OIS) for "
                f"cross-currency RV instead."
            )
        return self


class SwapSpreadCurrentMetrics(BaseModel):
    """Snapshot metrics for a cross-domain swap spread.

    Field names ``sovereign_yield_pct`` and ``ois_rate_pct`` make the
    domain of each leg explicit on the wire — no ambiguity about
    which side is the sovereign vs the OIS.
    """

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    sovereign_curve_family: str
    ois_curve_family: str
    tenor: str
    spread_label: str = Field(
        ...,
        description=(
            "Human-readable label, e.g. 'UST-USD_SOFR_OIS 10Y' "
            "(sovereign first per the sign convention)."
        ),
    )
    current_spread_bps: float = Field(
        ...,
        description=(
            "Current swap spread in basis points: "
            "(sovereign_yield − ois_rate) × 100.  Positive means "
            "the sovereign trades CHEAP to OIS."
        ),
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
    sovereign_yield_pct: Optional[float] = Field(
        None,
        description="Latest sovereign yield (percent) at the chosen tenor.",
    )
    ois_rate_pct: Optional[float] = Field(
        None,
        description="Latest OIS par swap rate (percent) at the chosen tenor.",
    )


class SwapSpreadTimeSeriesRow(BaseModel):
    """Single row in the bespoke wire-frozen swap-spread time-series."""

    date: str
    spread_bps: float
    z_score: Optional[float] = None


class SwapSpreadOutput(BaseModel):
    """Top-level response the MCP server returns to the orchestrator.

    ``time_series`` is the wire-frozen bespoke shape preserved for
    consistency with every other rates primitive.
    ``time_series_spread`` and ``time_series_zscore`` are the
    canonical closed-enum payloads for the upcoming primitive→
    operator bridge.  All three are computed from the same
    underlying display DataFrame — they cannot drift.
    """

    current_metrics: SwapSpreadCurrentMetrics
    time_series: List[SwapSpreadTimeSeriesRow]
    time_series_spread: TimeSeries = Field(
        ...,
        description=(
            "Historical swap-spread values (sovereign − OIS, in BPS) "
            "over the displayed window.  Closed-enum "
            "``TimeSeriesUnits.BPS``; series_name = '<sov_lower>_"
            "<ois_lower>_<tenor_lower>_swap_spread'.  Values match "
            "``time_series[i].spread_bps`` 1-to-1 by construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the swap spread vs its "
            "own trailing window.  Closed-enum "
            "``TimeSeriesUnits.Z_SCORE``; series_name = '<sov_lower>_"
            "<ois_lower>_<tenor_lower>_swap_spread_zscore'.  Values "
            "match ``time_series[i].z_score`` 1-to-1 (None for rows "
            "in the rolling-window warmup)."
        ),
    )


__all__ = [
    "SwapSpreadInput",
    "SwapSpreadCurrentMetrics",
    "SwapSpreadTimeSeriesRow",
    "SwapSpreadOutput",
]
