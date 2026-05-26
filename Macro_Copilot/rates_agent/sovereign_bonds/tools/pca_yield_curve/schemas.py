"""Pydantic schemas for the pca_yield_curve tool.

Per the v6 sprint plan (docs/architecture/tool_architecture.md, A13):
the user-facing input surface is intentionally narrow.  The structural
inputs (`curve_family`, `tenors`, `lookback_days`, `n_components`,
`change_frequency`) define what the fit IS; everything else lives in
YAML and is NOT user-overridable in V1.

Transport
---------
Per Delta Q of the v6 plan: pca_yield_curve has flat scalar inputs +
a `tenors: Optional[List[str]]` repeated-query-param field, so it
ships with **MCP and FastAPI GET** this sprint — same transport
shape as zscore_custom + beta_adjusted_spread.

Output
------
The snapshot includes:
  - `loadings`: list of {tenor, pc1, pc2, ...} rows (one per tenor).
  - `variance_explained`: list of {component_name, variance_share,
    cumulative_share} rows (one per component).
  - `current_factor_levels`: dict[component_name, latest factor level].
  - `component_metadata`: list of PcaComponentMetadata (per-component
    quality flag — Codex's v4 finding pinned a schema slot for the
    primitive's degenerate / sign_anchor_tied cases).

Plus per-component time series of factor scores (one ``TimeSeries``
per component, units=FACTOR_LEVEL).

Honest level/slope/curvature handling
-------------------------------------
The output uses ``pc1``, ``pc2``, ``pc3`` labels — NOT
``level``, ``slope``, ``curvature``.  The canonical interpretation
holds for normal yield-curve panels (sovereign / OIS / ZCIS /
linker) but is a property of the data, not enforced by the tool.
Downstream tools (yield_change_attribution_pca) consume the labels
as-is.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from shared.schemas import TimeSeries


# Locked sign anchor — must match shared.analytics.stats._LOCKED_SIGN_ANCHOR.
# Re-exported here as a module-level constant so wiring tests can pin
# both sides without importing from shared.analytics.
_LOCKED_SIGN_ANCHOR_LITERAL = "lock_pc_long_tenor_positive"


class PcaYieldCurveInput(BaseModel):
    """Parameters the LLM extracts to fit PCA on a rates curve."""

    model_config = ConfigDict(extra="forbid")

    curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "Rates curve identifier.  Accepts any curve_family declared "
            "in a tenor-keyed playbook under rates_agent/playbooks/ — "
            "sovereign benchmarks (e.g. 'UST', 'DE_BUND', 'IT_BTP', "
            "'FR_OAT', 'ES_BONO', 'UK_GILT', 'JGB', 'CANADA_GOVT', "
            "'AU_GOVT'), OIS curves (e.g. 'USD_SOFR_OIS', 'EUR_ESTR_OIS', "
            "'GBP_SONIA_OIS', 'JPY_OIS', 'AUD_OIS', 'CAD_OIS'), "
            "inflation swaps ('USD_ZCIS', 'EUR_ZCIS', 'GBP_ZCIS'), and "
            "sovereign linker real-yield curves ('USD_TIPS', "
            "'GBP_LINKER', 'EUR_FR_LINKER', 'CAD_RRB').  PCA fits on "
            "the curve_family's yield-changes panel regardless of the "
            "underlying instrument family; the math is curve-family "
            "agnostic."
        ),
    )
    tenors: Optional[List[str]] = Field(
        default=None,
        description=(
            "Subset of tenor labels to include in the fit, in any "
            "order (compute() sorts by numeric tenor before fitting).  "
            "When None (default), use ALL tenors of the curve_family "
            "from the playbook universe.  When supplied explicitly, "
            "the fit uses EXACTLY those tenors or returns an error; "
            "the tool does not silently drop missing tenors.  FastAPI "
            "consumers pass this as repeated query params: "
            "``?tenors=1Y&tenors=2Y&tenors=10Y``."
        ),
    )
    lookback_days: int = Field(
        default=1825,
        ge=400,
        le=7300,
        description=(
            "Calendar days of history fetched for the fit.  Default "
            "1825 (~5 years) — typical desk range for rates-curve "
            "PCA across sovereign / OIS / ZCIS / linker families.  "
            "Lower bound 400 is a conservative calendar-day floor "
            "intended to leave enough trading-day observations for the "
            "YAML's ``min_observations_for_pca`` requirement after "
            "differencing, including the weekly (``periods=5``) path.  "
            "The cross-layer observation-count guard remains the real "
            "authority."
        ),
    )
    n_components: int = Field(
        default=3,
        ge=1,
        le=8,
        description=(
            "Number of components to return.  Default 3 (level + "
            "slope + curvature on normal yield-curve panels).  Upper "
            "bound 8 reflects the widest tenor universe per curve_family "
            "currently ingested across rates playbooks; smaller "
            "universes (e.g. USD_TIPS at 4 tenors) cap n_components at "
            "that tenor count and the primitive returns a controlled "
            "error envelope if exceeded."
        ),
    )
    change_frequency: Literal["daily", "weekly"] = Field(
        default="daily",
        description=(
            "Frequency at which to take yield (or rate) differences "
            "before fitting PCA.  'daily' → 1-step diff; 'weekly' → "
            "5-trading-day diff.  Default 'daily' matches the desk-"
            "canonical input across sovereign / OIS / ZCIS / linker "
            "curves."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field.  When None (default), the "
            "tool auto-discovers the field from the owning playbook's "
            "``target_metrics[0].bloomberg_field`` — different per "
            "playbook: sovereign benchmarks + linkers use "
            "'YLD_YTM_MID', OIS curves use 'PX_LAST', ZCIS curves use "
            "'PX_MID'.  Pass an explicit field name to override per "
            "query.  LLM/HTTP wrappers MUST translate their wire-level "
            "sentinel (empty string for MCP, missing param for "
            "FastAPI) to None before constructing this input — "
            "otherwise the playbook auto-discovery is silently "
            "shadowed."
        ),
    )


class LoadingRow(BaseModel):
    """One row of the loadings table — values per component for a
    single tenor."""

    model_config = ConfigDict(extra="allow")  # pc1, pc2, pc3, ... keys

    tenor: str


class VarianceShareRow(BaseModel):
    """One component's variance share + cumulative share."""

    model_config = ConfigDict(extra="forbid")

    component_name: str
    variance_share: float
    cumulative_share: float


class PcaComponentMetadata(BaseModel):
    """Per-component quality metadata.

    Carries forward the Codex v4 review finding: the primitive
    detects 'degenerate' (eigenvalue ≈ 0) and 'sign_anchor_tied'
    (loadings at longest tenor exactly zero AND
    longest-shortest exactly zero) cases; this schema slot makes
    those visible to downstream consumers.
    """

    model_config = ConfigDict(extra="forbid")

    component_name: str
    quality_flag: Literal["ok", "degenerate", "sign_anchor_tied"]
    quality_note: Optional[str] = None


class PcaYieldCurveMetrics(BaseModel):
    """Snapshot metrics for a rates-curve PCA fit."""

    model_config = ConfigDict(extra="forbid")

    as_of_date: str = Field(..., description="Latest date in the fit window (YYYY-MM-DD).")
    fit_window_start: str = Field(
        ...,
        description=(
            "First trading day of the centered-change panel actually "
            "used to fit the PCA (YYYY-MM-DD).  This is one diff step "
            "after the first raw-yield row, since one observation is "
            "consumed by differencing.  Distinct from the user's "
            "``lookback_days`` request, which is a calendar-day "
            "request for the raw fetch — the realised window depends "
            "on holidays, weekends, and the change_frequency lag.  "
            "Downstream consumers (e.g. yield_change_attribution_pca) "
            "use this to compute change-vs-fit overlap honestly."
        ),
    )
    fit_window_end: str = Field(
        ...,
        description=(
            "Last trading day of the centered-change panel "
            "(YYYY-MM-DD).  Equal to ``as_of_date`` — the alias is "
            "retained for symmetry with ``fit_window_start`` and to "
            "make downstream provenance code less surprising."
        ),
    )
    curve_family: str
    tenors_used: List[str] = Field(
        ...,
        description=(
            "Tenor labels in numeric-ascending order — the order "
            "compute() actually used for the fit (and therefore the "
            "row order of `loadings`)."
        ),
    )
    lookback_days_used: int = Field(
        ...,
        description="Echoes the user's lookback_days input.",
    )
    n_components_returned: int = Field(
        ...,
        description="Number of components in the output (echoes the user's n_components).",
    )
    change_frequency_used: Literal["daily", "weekly"] = Field(
        ...,
        description="Echoes the user's change_frequency input.",
    )
    sign_anchor_used: str = Field(
        ...,
        description=(
            "PCA sign-anchor rule used in the fit.  V1: always "
            "'lock_pc_long_tenor_positive' (locked in YAML)."
        ),
    )
    loadings: List[LoadingRow] = Field(
        ...,
        description=(
            "One row per tenor (in tenors_used order); each row has "
            "the per-component loading values keyed by component name "
            "(pc1, pc2, pc3, ...)."
        ),
    )
    variance_explained: List[VarianceShareRow] = Field(
        ...,
        description=(
            "One row per component; per-component share + cumulative "
            "share of total variance."
        ),
    )
    total_variance_explained: float = Field(
        ...,
        description=(
            "Sum of the returned components' variance shares "
            "(equivalent to the last cumulative_share row)."
        ),
    )
    current_factor_levels: Dict[str, Optional[float]] = Field(
        ...,
        description=(
            "Latest factor scores keyed by component name.  None "
            "for degenerate components."
        ),
    )
    component_metadata: List[PcaComponentMetadata] = Field(
        ...,
        description=(
            "Per-component quality flag + note.  ok / degenerate / "
            "sign_anchor_tied per the locked sign anchor's tie-break "
            "rules."
        ),
    )
    observation_count: int = Field(
        ...,
        ge=0,
        description=(
            "Number of trading-day rows in the centered change panel "
            "after dropna."
        ),
    )


class PcaYieldCurveOutput(BaseModel):
    """Top-level response for the pca_yield_curve tool."""

    model_config = ConfigDict(extra="forbid")

    current_metrics: PcaYieldCurveMetrics
    time_series_factors: List[TimeSeries] = Field(
        ...,
        description=(
            "One TimeSeries per component; units=FACTOR_LEVEL.  "
            "series_name is "
            "'<curve_family>_pc<k>_factor_<change_frequency>'."
        ),
    )


__all__ = [
    "PcaYieldCurveInput",
    "PcaYieldCurveMetrics",
    "PcaYieldCurveOutput",
    "LoadingRow",
    "VarianceShareRow",
    "PcaComponentMetadata",
]
