"""
shared.schemas.time_series — generic time-series + caller-input schemas

This module defines the *uniform* shapes new tools (v6 sprint and
onward) use for their time-series outputs and structured caller
inputs.  The five existing migrated sovereign tools — curve_spread,
yield_levels, butterfly, cross_market_spread, curve_move_classifier —
do NOT use these shapes; their wire formats are frozen for frontend
compatibility and will be retro-fitted in a separate cleanup PR.

Why a separate package
----------------------
Putting these in ``shared/schemas/`` rather than per-tool keeps a
single canonical definition that future event-study primitives
(``find_event_dates``, ``event_study_panel``, ``event_study_aggregate``)
can consume uniformly: any tool that returns ``TimeSeries`` is
event-study-ready by construction.

Closed-enum units
-----------------
``TimeSeriesUnits`` is a closed ``Enum`` (string-valued).  Free-form
``units: str`` was rejected during the v6 plan review because it
invites drift across tools.  Adding a new unit requires extending the
enum here, in this single place.

Caller-input schemas
--------------------
``SeriesSpec``, ``PairSpec``, ``PastedTimeSeries``, ``PastedPcaLoadings``
are the input shapes tools accept when an LLM orchestrator pipes
output of a prior tool call into a downstream tool.  Each pasted
shape carries the full provenance the consuming tool needs to
honestly emit its outputs (e.g., ``PastedPcaLoadings`` MUST include
variance shares and component-quality metadata because T14's
per-component variance and quality handling cannot be derived from
loadings alone).
"""

from __future__ import annotations

from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# UNIT ENUM
# ============================================================================

class TimeSeriesUnits(str, Enum):
    """Closed enum for the units of a ``TimeSeries.value`` series.

    Every tool that emits a ``TimeSeries`` declares one of these.  Free-
    form strings are NOT accepted — adding a new unit requires extending
    this enum, which forces the addition into review.
    """

    PERCENT = "percent"            # yields, rates (e.g., 4.25 = 4.25%)
    BPS = "bps"                    # spreads, butterflies, residuals in bps
    Z_SCORE = "z_score"            # rolling z-scores (unitless)
    RATIO = "ratio"                # R², percentile in [0, 1], variance share
    PCT_RANK = "pct_rank"          # 0–100 percentile rank
    FACTOR_LEVEL = "factor_level"  # PCA factor scores (eigen-units)
    COUNT = "count"                # observation counts, quality flags as 0/1
    PRICE = "price"                # price levels (FX spots, equity prices,
                                   # commodity quotes, etc.) — non-percent
                                   # absolute levels. Added Phase B 2026-05-25
                                   # for fx_panel; equally usable cross-asset.


# ============================================================================
# TIME SERIES OUTPUT SHAPE
# ============================================================================

class TimeSeriesRow(BaseModel):
    """One observation in a ``TimeSeries``.

    Date is a string in ``YYYY-MM-DD`` form (matches the rest of the
    repo's wire convention).  Value may be None to represent gaps that
    the tool chose not to ffill (e.g., before a rolling stat's
    ``min_periods`` is reached).
    """

    model_config = ConfigDict(extra="forbid")

    date: str = Field(..., description="Trade date in YYYY-MM-DD form.")
    value: Optional[float] = Field(
        ...,
        description=(
            "Observation value in ``TimeSeries.units``.  None when the "
            "tool emits a gap (e.g., warmup period for a rolling stat)."
        ),
    )


class TimeSeries(BaseModel):
    """A named, unit-tagged time series.

    All v6-sprint-and-later tools that emit time-series output return
    this shape.  Existing migrated tools (curve_spread, yield_levels,
    butterfly, cross_market_spread, curve_move_classifier) keep their
    bespoke per-tool row arrays for frontend compatibility; that
    retro-fit is deferred to a separate cleanup PR.
    """

    model_config = ConfigDict(extra="forbid")

    series_name: str = Field(
        ...,
        min_length=1,
        description=(
            "Canonical identifier — e.g. 'ust_10y_yld_pct', "
            "'btp_bund_10y_spread_bps', 'ust_2y_zscore_60d'.  Lower "
            "snake-case; the consuming caller uses this to disambiguate "
            "multiple series in the same response."
        ),
    )
    units: TimeSeriesUnits = Field(
        ...,
        description=(
            "Closed enum — see ``TimeSeriesUnits``.  Required so "
            "downstream event-study / charting primitives can render "
            "and compose without inferring units from the name."
        ),
    )
    description: str = Field(
        ...,
        min_length=1,
        description="One-line human-readable description of what this series represents.",
    )
    rows: List[TimeSeriesRow] = Field(
        default_factory=list,
        description=(
            "Observations in chronological order.  May be empty when "
            "the requested window has no data."
        ),
    )


# ============================================================================
# CALLER-INPUT SCHEMAS
# ============================================================================

class SeriesSpec(BaseModel):
    """Caller specification of a single (curve_family, tenor) series.

    Used by tools that fetch one or more sovereign yield series given
    structured caller input (e.g., rolling_regression's target /
    regressors).  ``field_name=None`` is the sentinel that resolves
    to the consuming tool's YAML ``default_field_name`` convention,
    matching the existing flat-input pattern at every other tool.
    """

    model_config = ConfigDict(extra="forbid")

    curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "Curve family identifier — e.g. 'UST', 'DE_BUND', 'IT_BTP'."
        ),
    )
    tenor: str = Field(
        ...,
        min_length=1,
        description="Tenor point — e.g. '2Y', '10Y', '30Y'.",
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field.  When None (default), the "
            "consuming tool resolves this against its YAML "
            "``default_field_name`` convention (currently 'YLD_YTM_MID' "
            "across all sovereign tools)."
        ),
    )


class PairSpec(BaseModel):
    """Caller specification of a cross-market spread series.

    The spread direction is locked to ``(cf1_yield - cf2_yield) * 100``
    in bps, matching the sovereign ``cross_market_spread`` tool's
    convention exactly.  Pasting a series with the opposite direction
    is the caller's responsibility (use ``PastedTimeSeries`` with the
    desired sign).
    """

    model_config = ConfigDict(extra="forbid")

    cf1: str = Field(..., min_length=1, description="First curve family (numerator).")
    cf2: str = Field(..., min_length=1, description="Second curve family (subtrahend).")
    tenor: str = Field(..., min_length=1, description="Tenor point — e.g. '10Y'.")
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field used for both legs.  When None, "
            "consuming tool falls through to its YAML default."
        ),
    )


class PastedTimeSeries(BaseModel):
    """Caller-supplied time series the tool consumes directly.

    This is the orchestrator-paste shape: the LLM (or harness) takes
    the output of a prior tool call and feeds it to a downstream tool
    without re-fetching from the database.  ``units`` MUST be set
    correctly so the consuming tool knows how to interpret the values
    (e.g., a half-life tool produces unit-honest outputs based on this
    field).
    """

    model_config = ConfigDict(extra="forbid")

    series_name: str = Field(..., min_length=1)
    units: TimeSeriesUnits
    rows: List[TimeSeriesRow] = Field(
        ...,
        min_length=1,
        description="Non-empty list of observations.",
    )


class PastedPcaComponentMetadata(BaseModel):
    """Per-component PCA quality metadata carried across pasted fits."""

    model_config = ConfigDict(extra="forbid")

    component_name: str = Field(..., min_length=1)
    quality_flag: Literal["ok", "degenerate", "sign_anchor_tied"]
    quality_note: Optional[str] = None


class PastedPcaLoadings(BaseModel):
    """Caller-supplied PCA loadings the tool consumes directly.

    Used by ``yield_change_attribution_pca`` (T14 of the v6 sprint)
    when the caller has already fit a PCA via ``pca_yield_curve``
    (T13) and wants to attribute a yield change against those exact
    loadings rather than fitting fresh.

    Provenance discipline
    ---------------------
    The consumer (T14) emits per-component ``variance_share_in_fit_window``
    in its output.  That quantity is NOT derivable from loadings alone,
    so the caller MUST include the variance shares from the original
    PCA fit.  Same for ``change_frequency_used`` (T13 supports both
    daily and weekly frequencies; mixing yields silent
    interpretive distortion) and ``n_observations_in_fit`` (a sanity
    check the consuming tool validates against its
    ``min_observations_for_pca`` convention).

    T13 also emits per-component quality metadata
    (``ok`` / ``degenerate`` / ``sign_anchor_tied``).  The consumer
    needs that metadata to distinguish a clean PCA fit from one with
    suppressed or ambiguous components, so the pasted shape carries it
    explicitly rather than coercing every fit into a dense numeric
    matrix.

    The ``sign_anchor_used`` field is a closed Literal (only one
    anchor is supported in V1); a paste declaring any other anchor is
    rejected at input validation by the consuming tool.
    """

    model_config = ConfigDict(extra="forbid")

    curve_family: str = Field(..., min_length=1)
    tenors: List[str] = Field(
        ...,
        min_length=1,
        description="Tenor labels matching the order of ``components`` columns.",
    )
    components: List[List[Optional[float]]] = Field(
        ...,
        min_length=1,
        description=(
            "[n_components][n_tenors] loading matrix.  Each loading vector "
            "should be unit-norm when the component is usable "
            "(validated by the consuming tool with a small tolerance).  "
            "None is allowed for suppressed loadings on degenerate "
            "components."
        ),
    )
    component_names: List[str] = Field(
        ...,
        min_length=1,
        description=(
            "Per-component labels — e.g. ['pc1', 'pc2', 'pc3'].  Length "
            "must match ``components``.  Names are arbitrary lower "
            "snake-case; T13 uses 'pc1', 'pc2', ..."
        ),
    )
    variance_shares: List[float] = Field(
        ...,
        min_length=1,
        description=(
            "Per-component variance share in [0, 1] from the original PCA "
            "fit.  REQUIRED because T14 emits this in its output; "
            "consuming tool validates ``len == len(components)``, each in "
            "[0, 1], ``sum <= 1.0 + 1e-6``."
        ),
    )
    component_metadata: List[PastedPcaComponentMetadata] = Field(
        ...,
        min_length=1,
        description=(
            "Per-component quality flags from the original PCA fit.  "
            "Length must match ``components`` and ``component_names`` so "
            "the consumer can preserve T13's ``ok`` / ``degenerate`` / "
            "``sign_anchor_tied`` semantics."
        ),
    )
    fit_window_start: str = Field(
        ...,
        min_length=1,
        description="YYYY-MM-DD start of the original PCA fit window.",
    )
    fit_window_end: str = Field(
        ...,
        min_length=1,
        description="YYYY-MM-DD end of the original PCA fit window.",
    )
    change_frequency_used: Literal["daily", "weekly"] = Field(
        ...,
        description=(
            "Yield-change frequency used in the original PCA fit.  T14 "
            "surfaces this in its output so the caller can detect "
            "frequency-mismatch interpretive distortion."
        ),
    )
    n_observations_in_fit: int = Field(
        ...,
        ge=1,
        description=(
            "Number of yield-change observations used in the original "
            "PCA fit.  T14 validates this against its "
            "``min_observations_for_pca`` convention."
        ),
    )
    sign_anchor_used: Literal["lock_pc_long_tenor_positive"] = Field(
        ...,
        description=(
            "PCA sign-anchor rule used in the original fit.  V1 supports "
            "only ``lock_pc_long_tenor_positive``; consuming tools raise "
            "NotImplementedError on any other value, with a pointer to "
            "``methodology.planned_extensions``."
        ),
    )
