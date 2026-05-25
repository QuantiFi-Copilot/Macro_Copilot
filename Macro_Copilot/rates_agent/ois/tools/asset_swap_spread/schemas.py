"""Pydantic schemas + typed exception for the asset_swap_spread tool.

Per-bond INGESTED Bloomberg asset-swap spread (``ASSET_SWAP_SPD_MID``)
for one sovereign cash bond identified by ``vendor_ticker`` (the
canonical per-bond key against the sovereign_cash_bonds universe).

PR4 differentiation (load-bearing)
----------------------------------
This is the **ingested** Bloomberg ASW per cash bond.  It is NOT the
``rates_agent/ois/tools/swap_spread/`` cross-domain par-par approximation
``(sovereign_yield - ois_rate) * 100``.  Both primitives ship in the OIS
domain; both methodology cards cite each other.  See config.yaml's
header block + the symmetric ``related_primitives`` cite in
swap_spread/config.yaml for the side-by-side.

Typed-boundary discipline (P3 + typed_boundary_discipline.md)
-------------------------------------------------------------
``model_config = ConfigDict(extra='forbid')`` — unrecognised input
fields raise loudly so the LLM cannot smuggle methodology knobs
under cover of unknown-field tolerance.  Same protection Codex
applied to nfp_surprise (PR #188) and wirp_meeting_pricing.

``vendor_ticker`` carries ``min_length=1`` so empty strings fail at
the Pydantic boundary, not in the middle of compute.

Typed P6 refusal — ``AssetSwapSpreadUnavailableError``
------------------------------------------------------
Raised by compute() on:

  - ``vendor_ticker`` not in ``instrument_master`` with
    ``instrument_type='sovereign_cash_bond'``
  - NULL ``ASSET_SWAP_SPD_MID`` on the requested ``as_of_date``
    (no ffill, no proxy, no swap_spread fall-through)
  - empty result set for the lookback window

Per the catalog guardrail:
> On NULL asw_spread for a bond/date, raise a typed exception per
> P6 — do NOT fall back to computing the spread.

Validators that encode invariants stay here in code; this tool has
no cross-field invariants beyond Pydantic's basic Field constraints.

Canonical TimeSeries output
---------------------------
This tool emits a canonical ``shared.schemas.time_series.TimeSeries``
payload (units = BPS) over the displayed lookback window.  Each row
carries the RAW Bloomberg-ingested ``ASSET_SWAP_SPD_MID`` value
verbatim — no rounding, so the SQL-validation runner can assert
1e-9 parity against a raw SELECT.
"""

from __future__ import annotations

from datetime import date as _date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from shared.schemas import TimeSeries


# ============================================================================
# TYPED REFUSAL EXCEPTION — catalog guardrail
# ============================================================================


class AssetSwapSpreadUnavailableError(Exception):
    """Raised when the requested per-bond ASW value cannot be honestly
    surfaced.

    Per the catalog guardrail "On NULL asw_spread for a bond/date,
    raise a typed exception per P6 — do NOT fall back to computing
    the spread."  Three trigger conditions:

      1. ``vendor_ticker`` not present in
         ``macro_data.instrument_master`` with
         ``instrument_type='sovereign_cash_bond'``.
      2. NULL ``ASSET_SWAP_SPD_MID`` on the requested
         ``as_of_date``.
      3. Empty result set for the lookback window (no observations
         at all — distinct from CAD-sparse where SOME observations
         exist but the rolling z-score floor is not met).

    Callers receive a typed Python exception (not an error envelope
    inside the compute layer); the MCP wrapper at the transport
    boundary converts to the controlled error envelope per P6's
    transport-boundary rule.  The exception message names the
    (vendor_ticker, date_context) so the caller can diagnose
    without re-running.
    """

    def __init__(
        self,
        *,
        vendor_ticker: str,
        reason: str,
        as_of_date: Optional[_date] = None,
    ) -> None:
        self.vendor_ticker = vendor_ticker
        self.reason = reason
        self.as_of_date = as_of_date
        msg = (
            f"AssetSwapSpread unavailable for vendor_ticker="
            f"{vendor_ticker!r}"
        )
        if as_of_date is not None:
            msg += f" on as_of_date={as_of_date.isoformat()}"
        msg += f": {reason}"
        super().__init__(msg)


# ============================================================================
# INPUT SCHEMA
# ============================================================================


def _bundled_default_lookback_days() -> int:
    """Read the default lookback from the bundled config.yaml.

    Used as the Pydantic factory default so an edit to YAML changes
    the runtime default.  Same lazy-factory pattern as get_otr_history
    / cpi_surprise / nfp_surprise / wirp_meeting_pricing.

    Raises loudly on YAML-load failure per P6 — no silent fallback.
    """
    from rates_agent.ois.tools.asset_swap_spread.compute import (
        CONFIG_PATH,
    )
    from shared.config import load_tool_config

    cfg = load_tool_config(CONFIG_PATH)
    # The YAML does not declare a ``default_lookback_days`` convention
    # by name (cross-config lint conflicts with the existing 365 value
    # used by inflation_swaps tools); the Pydantic default is hard-
    # coded at 365 below.  This factory is reserved for a future
    # widening if the per-tool default needs to differ.
    # See methodology.planned_extensions for the path.
    return 365


class AssetSwapSpreadInput(BaseModel):
    """Parameters the LLM extracts to query the INGESTED per-bond ASW.

    Strict typed-boundary discipline (model_config below):
      - ``extra='forbid'`` — unrecognised inputs raise loudly so a
        methodology knob cannot be smuggled in.
      - ``vendor_ticker`` min_length=1 — empty strings refused at
        the boundary.

    PR8 — single central methodology knob is ``lookback_days``
    (display window length).  ``vendor_ticker`` and the date selectors
    are instrument-selection / per-query parameters, not methodology.
    """

    model_config = ConfigDict(extra="forbid")

    vendor_ticker: str = Field(
        ...,
        min_length=1,
        description=(
            "Per-bond canonical key against ``macro_data.instrument_master``. "
            "Convention: ``/isin/<ISIN>`` (e.g. ``/isin/DE000BU22130``, "
            "``/isin/US91282CQQ77``).  The sovereign_cash_bonds playbook "
            "addresses every bond by ISIN universally (header note 2).  "
            "Compute verifies the vendor_ticker exists in instrument_master "
            "with ``instrument_type='sovereign_cash_bond'`` and raises "
            "``AssetSwapSpreadUnavailableError`` if not (no proxy, no "
            "fall-through to a sibling bond)."
        ),
    )
    as_of_date: Optional[_date] = Field(
        default=None,
        description=(
            "Optional explicit snapshot date.  When omitted, the snapshot "
            "is the most recent NON-NULL observation in the lookback "
            "window (the LATEST mode, mirroring yield_levels).  When "
            "supplied, the snapshot is computed for THIS date — and the "
            "snapshot's ``current_asw_spread_bps`` MUST be non-NULL on "
            "the requested date or compute() raises "
            "``AssetSwapSpreadUnavailableError`` (no ffill on the "
            "explicit-date path; P12 + P6 honest refusal)."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of trailing ASW history.  Anchored to "
            "``as_of_date`` when supplied, else to the data's latest "
            "observation (NOT to date.today() — ASW publish calendars "
            "lag, and CAD ASW is sparse).  This is the single LLM-"
            "controlled methodology knob per PR8.  Does NOT control the "
            "rolling z-score window (fixed by ``z_score_window_days``, "
            "currently 252) or the trailing range window (fixed by "
            "``trailing_range_window_days``, locked at 252 in V1)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field.  When None (default), the "
            "tool falls through to ``default_field_name`` from "
            "config.yaml (currently 'ASSET_SWAP_SPD_MID').  Pass an "
            "explicit field name to override per query (e.g. an "
            "ASSET_SWAP_SPD_BID variant once ingested).  LLM/HTTP "
            "wrappers MUST translate their wire-level sentinel (empty "
            "string for MCP, missing param for FastAPI) to None before "
            "constructing this input — otherwise the YAML default is "
            "silently shadowed.  See the curve_move_classifier "
            "wrapper-shadowing fix (commit b2605ee)."
        ),
    )


# ============================================================================
# OUTPUT SCHEMA
# ============================================================================


class AssetSwapSpreadMetrics(BaseModel):
    """Deterministic snapshot for a single sovereign cash bond's ASW
    spread.

    ``current_asw_spread_bps`` is the RAW Bloomberg-ingested value,
    preserved bit-exact from the DB.  The level-stat decoration fields
    (period changes, z-score, high/low/percentile) ARE rounded per
    the YAML conventions.

    Field names ``high_252d_bps``, ``low_252d_bps``, and
    ``percentile_252d`` embed the trailing-range window length (252)
    in their identifiers and are therefore wire-frozen in V1.  See
    config.yaml's ``planned_extensions`` for the path to configurability.

    The bond-identity block (country, currency, cusip, isin,
    vendor_ticker, maturity_date) surfaces the catalog's
    ``required_reference_metrics`` (minus ``security_name`` and
    ``issuer``, both P5-disclosed as absent in V1; see config.yaml's
    ``methodology.field_availability_caveat`` block).
    """

    model_config = ConfigDict(extra="forbid")

    as_of_date: str = Field(..., description="Snapshot date (YYYY-MM-DD).")

    # Bond identity — catalog's required_reference_metrics (minus the
    # disclosed-absent security_name + issuer).
    vendor_ticker: str = Field(..., description="Canonical per-bond key (echoed).")
    country: Optional[str] = Field(
        default=None,
        description=(
            "Country of issue per the playbook universe (e.g. 'US', "
            "'Germany', 'UK', 'Japan').  Honest absence (None) when "
            "instrument_master row is missing this field."
        ),
    )
    currency: Optional[str] = Field(
        default=None,
        description="ISO-4217 currency code (e.g. 'USD', 'EUR', 'GBP', 'JPY').",
    )
    cusip: Optional[str] = Field(
        default=None,
        description="Bond CUSIP (NULL for non-US sovereigns; ADR 0003).",
    )
    isin: Optional[str] = Field(
        default=None,
        description="Bond ISIN (universal — the playbook addresses every bond by ISIN).",
    )
    maturity_date: Optional[str] = Field(
        default=None,
        description="ISO date (YYYY-MM-DD) of the bond's maturity.",
    )

    # RAW ASW value — NOT rounded, bit-exact from the DB.
    current_asw_spread_bps: float = Field(
        ...,
        description=(
            "INGESTED Bloomberg ``ASSET_SWAP_SPD_MID`` for the bond on "
            "``as_of_date``, in basis points.  RAW value preserved "
            "bit-exact from ``macro_data.market_data_daily`` — NOT "
            "rounded — so SQL-validation runners can assert 1e-9 "
            "parity against a raw SELECT.  Vendor-ingested per P12; "
            "never recomputed."
        ),
    )

    # Level-stat decoration — rounded per YAML.
    daily_change_bps: Optional[float] = Field(
        None,
        description="1-day change in basis points (rounded to bps_round_decimals).",
    )
    weekly_change_bps: Optional[float] = Field(
        None,
        description="5-trading-day change in basis points (rounded to bps_round_decimals).",
    )
    monthly_change_bps: Optional[float] = Field(
        None,
        description="22-trading-day change in basis points (rounded to bps_round_decimals).",
    )
    z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score (rounded to "
            "z_score_round_decimals).  May be None for CAD government "
            "bonds where the 60-observation floor is not met — see "
            "``methodology_note``."
        ),
    )
    high_252d_bps: Optional[float] = Field(
        None,
        description="Highest ASW over trailing 252 trading days (rounded to bps_round_decimals).",
    )
    low_252d_bps: Optional[float] = Field(
        None,
        description="Lowest ASW over trailing 252 trading days (rounded to bps_round_decimals).",
    )
    percentile_252d: Optional[float] = Field(
        None,
        description="Percentile rank within trailing 252-day range (0-100; rounded to bps_round_decimals).",
    )
    observation_count: int = Field(
        ...,
        ge=0,
        description=(
            "Number of trading days with NON-NULL ASW in the "
            "lookback_days window.  CAD government bonds typically "
            "have low counts here (~2% of trading days) per playbook "
            "header note 4."
        ),
    )
    lookback_days: int = Field(
        ...,
        ge=30,
        description="Echo of the input lookback_days for methodology-card visibility.",
    )


class AssetSwapSpreadOutput(BaseModel):
    """Top-level response for the asset_swap_spread tool.

    ``current_metrics`` is the snapshot + bond identity.
    ``time_series`` is the canonical BPS series with RAW (unrounded)
    values for parity.
    ``methodology_note`` surfaces the P12 vendor-ingest disclosure +
    CAD ASW sparsity caveat + security_name absence caveat per PR10.
    """

    model_config = ConfigDict(extra="forbid")

    current_metrics: AssetSwapSpreadMetrics
    time_series: TimeSeries = Field(
        ...,
        description=(
            "Historical ASW spread series for the requested bond over "
            "the display window (last ``lookback_days`` calendar days, "
            "anchored to ``as_of_date`` when supplied else to the data's "
            "latest observation).  Closed-enum ``TimeSeriesUnits.BPS`` "
            "units; series_name = "
            "'asw_spread_<sanitised_vendor_ticker>'.  Each row carries "
            "the RAW Bloomberg-ingested ASSET_SWAP_SPD_MID value (no "
            "rounding) so the snapshot's ``current_asw_spread_bps`` "
            "equals ``time_series.rows[-1].value`` STRICTLY at the "
            "snapshot date."
        ),
    )
    methodology_note: str = Field(
        ...,
        description=(
            "Plain-language disclosure of the P12 vendor-ingest "
            "discipline + the CAD ASW sparsity caveat (playbook "
            "header note 4) + the security_name / issuer absence "
            "caveat (P5).  Required field — removing it is a wire-"
            "contract change."
        ),
    )


__all__ = [
    "AssetSwapSpreadInput",
    "AssetSwapSpreadMetrics",
    "AssetSwapSpreadOutput",
    "AssetSwapSpreadUnavailableError",
]
