"""Pydantic schemas for the yield_change_attribution_pca tool.

Per the v6 sprint plan (docs/architecture/tool_architecture.md, A13):
the user-facing input surface exposes the structural choices that
define WHAT change is being attributed (`curve_family`,
`target_tenor`, `start_date`, `end_date`) AND HOW the loadings come
in (either a `pasted_loadings` payload OR inline-fit params).  Every
methodology ancillary lives in YAML and is NOT user-overridable in
V1.

Standardisation (hierarchical config principle)
-----------------------------------------------
This tool's output meaning depends on the upstream PCA fit, but ALL
upstream choices are explicit in the input + echoed in the output:

  * fit_inline → `pca_lookback_days`, `n_components`,
    `change_frequency`, `tenors` are inputs that flow to
    pca_yield_curve's compute(); the resulting fit window /
    variance shares / sign anchor / quality flags are echoed in
    the output.
  * pasted → `pasted_loadings` carries the original fit's full
    provenance (variance shares, change frequency,
    n_observations_in_fit, sign anchor, fit window, per-component
    quality flags).  Same provenance fields are echoed in the
    output.

In BOTH cases the output snapshot is reproducible from the (this
tool's config + the upstream config or pasted provenance) closure.
That is what makes T14 standard by our definition.

Transport
---------
Per Delta H of the v6 plan: yield_change_attribution_pca takes the
nested ``Optional[PastedPcaLoadings]`` input, so it ships **MCP only
this sprint**.  No FastAPI route until the UI-integration PR.

Output uses unit-honest naming
------------------------------
Per-component contributions are in BPS (after the explicit *100
conversion in compute.py); `total_change_bps` and `residual_bps`
also.  `loading_at_target_tenor` is a unitless ratio; variance
shares are in [0, 1].  Component labels are pc1, pc2, pc3, ... —
NOT level/slope/curvature.
"""

from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.schemas import PastedPcaLoadings


class YieldChangeAttributionPcaInput(BaseModel):
    """Parameters the LLM extracts to attribute a sovereign yield
    change to PCA component contributions.

    Either ``pasted_loadings`` is supplied (use those loadings
    directly) OR it is omitted (fit inline via pca_yield_curve with
    the inline-fit params).  ``loadings_source`` is DERIVED from
    which path is taken — not a user input — so the input contract
    stays minimal and the user can't supply a contradictory pair.
    """

    model_config = ConfigDict(extra="forbid")

    curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "Sovereign curve identifier — e.g. 'UST', 'DE_BUND', "
            "'IT_BTP', 'FR_OAT', 'ES_BONO', 'UK_GILT', 'JGB'."
        ),
    )
    target_tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Tenor whose yield change we are attributing — e.g. "
            "'10Y'.  Must be present in the fit's tenor universe "
            "(fit_inline → in `tenors` if supplied, else in the "
            "default playbook universe; pasted → in "
            "pasted_loadings.tenors).  When missing, compute() "
            "returns a controlled error envelope."
        ),
    )
    start_date: str = Field(
        ...,
        description=(
            "Start of the change window (YYYY-MM-DD).  Resolved to "
            "the nearest trading day on or AFTER start_date per "
            "the YAML-locked `start_date_resolution: forward` "
            "policy.  Must be strictly before end_date."
        ),
    )
    end_date: str = Field(
        ...,
        description=(
            "End of the change window (YYYY-MM-DD).  Resolved to "
            "the nearest trading day on or BEFORE end_date per "
            "the YAML-locked `end_date_resolution: backward` "
            "policy.  Must be strictly after start_date."
        ),
    )
    pasted_loadings: Optional[PastedPcaLoadings] = Field(
        default=None,
        description=(
            "When supplied, attribution uses these loadings AS-IS "
            "(after validation) and compute() does NOT read the "
            "inline-fit params below.  When None (default), compute() "
            "fits PCA inline via pca_yield_curve using the inline-"
            "fit params.  Note: the inline-fit params still go "
            "through their schema validators (range/Literal/etc.) "
            "in BOTH modes — pasting loadings exempts them from "
            "compute() reading them, not from validation.  Pass "
            "in-range placeholder values when paste-only.  Paste "
            "carries the original PCA fit's full provenance "
            "(variance_shares, change_frequency, "
            "n_observations_in_fit, sign_anchor, fit window, per-"
            "component quality flags) — enforced by the schema's "
            "`PastedPcaLoadings` contract."
        ),
    )

    # ------------------------------------------------------------------
    # Inline-fit parameters (used only when pasted_loadings is None).
    # YAML defaults are the source of truth; these schema defaults
    # mirror the YAML so the input contract is self-contained.
    # ------------------------------------------------------------------
    pca_lookback_days: int = Field(
        default=1825,
        ge=400,
        le=7300,
        description=(
            "Calendar days of history for the inline PCA fit.  "
            "Default 1825 (~5y).  Lower bound 400 mirrors "
            "pca_yield_curve's own ``lookback_days`` floor — keeping "
            "the two in sync means values that pass T14's schema "
            "also pass T13's when the inline path constructs "
            "PcaYieldCurveInput.  Note: this validator runs "
            "unconditionally — supplying ``pasted_loadings`` does "
            "NOT bypass it, even though compute() never reads "
            "pca_lookback_days on the pasted path.  If you need to "
            "submit a paste-only request with a placeholder out-of-"
            "range value, change THIS bound; the ``ignored on "
            "pasted`` semantic is about compute() reading the field, "
            "not about validation skipping it."
        ),
    )
    n_components: int = Field(
        default=3,
        ge=1,
        le=8,
        description=(
            "Number of PCA components for the inline fit AND the "
            "attribution.  Default 3 (level + slope + curvature on "
            "normal sovereign panels).  When pasted_loadings is "
            "supplied, this MUST NOT exceed "
            "len(pasted_loadings.components); compute() rejects "
            "the request otherwise."
        ),
    )
    change_frequency: Literal["daily", "weekly"] = Field(
        default="daily",
        description=(
            "Frequency at which the inline PCA fit takes yield "
            "differences.  Default 'daily' — matches the desk-"
            "canonical sovereign-curve PCA input.  compute() does "
            "not read this field on the pasted path (the paste's "
            "change_frequency_used is authoritative there), but "
            "the Literal validator still applies in both modes."
        ),
    )
    tenors: Optional[List[str]] = Field(
        default=None,
        description=(
            "Tenor list for the inline PCA fit.  When None "
            "(default), the playbook's default universe is used.  "
            "On the inline path, when non-None, target_tenor MUST "
            "be in this list (enforced by the schema's "
            "model_validator).  compute() does not read this field "
            "on the pasted path; the analogous tenor-membership "
            "check there is against `pasted_loadings.tenors`."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field for the change window AND "
            "the inline PCA fit.  When None (default), falls "
            "through to YAML's default_field_name.  Same sentinel "
            "pattern as the rest of the rates roster."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=(
            "Optional as-of date (YYYY-MM-DD): compute as of this trade "
            "date instead of the latest available data.  None → latest "
            "(live snapshot).  Supply a date for a historical, replayable "
            "view.  Caps both the inline PCA fit (passed through to "
            "pca_yield_curve) and the change-window panel fetch at this "
            "trade date.  On the pasted-loadings path the change-window "
            "panel is still capped here, but the loadings themselves carry "
            "their own (paste-supplied) fit-window provenance."
        ),
    )

    @model_validator(mode="after")
    def _validate_dates_and_consistency(
        self,
    ) -> "YieldChangeAttributionPcaInput":
        # Strict YYYY-MM-DD parse (defensive — fastapi/MCP usually
        # delivers strings, not dates).
        from datetime import date as _date
        try:
            sd = _date.fromisoformat(self.start_date)
            ed = _date.fromisoformat(self.end_date)
        except ValueError as exc:
            raise ValueError(
                f"start_date / end_date must be YYYY-MM-DD; got "
                f"start_date={self.start_date!r}, end_date={self.end_date!r}: "
                f"{exc}"
            )
        if sd >= ed:
            raise ValueError(
                f"start_date ({self.start_date}) must be strictly "
                f"before end_date ({self.end_date})."
            )

        # If tenors is supplied for the inline-fit path, target_tenor
        # must be in it.  (When pasted_loadings is supplied, this
        # check is done in compute() against pasted_loadings.tenors.)
        if (
            self.pasted_loadings is None
            and self.tenors is not None
            and self.target_tenor not in self.tenors
        ):
            raise ValueError(
                f"target_tenor={self.target_tenor!r} is not in the "
                f"supplied tenors list {self.tenors!r}; the "
                "attribution would have no row to read for the "
                "target."
            )

        # Cross-check pasted_loadings consistency at validation
        # time (compute() also re-checks defensively).
        if self.pasted_loadings is not None:
            if self.curve_family != self.pasted_loadings.curve_family:
                raise ValueError(
                    f"curve_family mismatch: input={self.curve_family!r}, "
                    f"pasted_loadings.curve_family="
                    f"{self.pasted_loadings.curve_family!r}.  Refusing "
                    "to attribute a change against loadings from a "
                    "different curve."
                )
            if self.target_tenor not in self.pasted_loadings.tenors:
                raise ValueError(
                    f"target_tenor={self.target_tenor!r} not in "
                    f"pasted_loadings.tenors={self.pasted_loadings.tenors!r}."
                )
            if self.n_components > len(self.pasted_loadings.components):
                raise ValueError(
                    f"n_components={self.n_components} exceeds "
                    f"len(pasted_loadings.components)="
                    f"{len(self.pasted_loadings.components)}."
                )

        return self


class ComponentContribution(BaseModel):
    """One PCA component's contribution to the target tenor's
    yield change."""

    model_config = ConfigDict(extra="forbid")

    component_name: str = Field(
        ...,
        min_length=1,
        description=(
            "Lower snake-case component label — pc1, pc2, pc3, ... "
            "Matches the upstream PCA fit's component naming."
        ),
    )
    contribution_bps: Optional[float] = Field(
        ...,
        description=(
            "Contribution of this component to the target tenor's "
            "yield change, in BPS.  None for degenerate components "
            "(their loadings are NaN; the contribution falls into "
            "residual_bps).  Sign convention matches the upstream "
            "PCA's sign anchor (V1: each PC's loading at the "
            "longest tenor is non-negative)."
        ),
    )
    loading_at_target_tenor: Optional[float] = Field(
        ...,
        description=(
            "v_k[target_idx] — the loading entry for this component "
            "at the target tenor.  Unitless eigenvector entry; "
            "rounded per `loading_round_decimals`.  None for "
            "degenerate components."
        ),
    )
    variance_share_in_fit_window: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Share of total variance in the upstream PCA fit window "
            "explained by this component, in [0, 1].  Rounded per "
            "`variance_share_round_decimals`.  Sourced from the "
            "upstream PCA primitive (fit_inline) or from "
            "pasted_loadings.variance_shares (pasted)."
        ),
    )
    quality_flag: Literal["ok", "degenerate", "sign_anchor_tied"] = Field(
        ...,
        description=(
            "Mirrored from the upstream PCA's per-component quality "
            "flag.  ``ok`` → safe to interpret; ``degenerate`` → "
            "loadings were suppressed; contribution_bps is None and "
            "this component's missing variance falls into "
            "residual_bps; ``sign_anchor_tied`` → component "
            "direction is ambiguous, downstream interpretation may "
            "need a different sign anchor."
        ),
    )


class YieldChangeAttributionPcaMetrics(BaseModel):
    """Snapshot for one PCA-based yield-change attribution."""

    model_config = ConfigDict(extra="forbid")

    # ------------------------------------------------------------------
    # Window provenance
    # ------------------------------------------------------------------
    start_date_requested: str = Field(
        ...,
        description="Caller's requested start_date (YYYY-MM-DD).",
    )
    start_date_resolved: str = Field(
        ...,
        description=(
            "Trading day actually used for the start of the change "
            "window — nearest trading day on or AFTER "
            "start_date_requested per the locked forward policy."
        ),
    )
    end_date_requested: str = Field(
        ...,
        description="Caller's requested end_date (YYYY-MM-DD).",
    )
    end_date_resolved: str = Field(
        ...,
        description=(
            "Trading day actually used for the end of the change "
            "window — nearest trading day on or BEFORE "
            "end_date_requested per the locked backward policy."
        ),
    )
    # ------------------------------------------------------------------
    # Identity + attribution result
    # ------------------------------------------------------------------
    curve_family: str
    target_tenor: str
    total_change_bps: float = Field(
        ...,
        description=(
            "Yield change at the target tenor between "
            "start_date_resolved and end_date_resolved, in BPS "
            "(percent change × 100).  Rounded per "
            "`bps_round_decimals`."
        ),
    )
    component_contributions: List[ComponentContribution] = Field(
        ...,
        description=(
            "Per-component contributions in BPS, in component-name "
            "order (pc1, pc2, pc3, ...).  Sums (over non-degenerate "
            "components) plus residual_bps equals total_change_bps "
            "(modulo float-precision noise).  See ComponentContribution "
            "for per-field semantics."
        ),
    )
    residual_bps: float = Field(
        ...,
        description=(
            "Part of the change at the target tenor that lies in the "
            "orthogonal complement of the chosen n_components (plus "
            "any contribution from degenerate components whose "
            "loadings were suppressed).  When n_components == "
            "n_tenors and no component is degenerate, residual_bps "
            "≈ 0 modulo float noise.  Rounded per "
            "`bps_round_decimals`."
        ),
    )
    n_components_used: int = Field(
        ...,
        ge=1,
        description=(
            "Number of components in the attribution — equals the "
            "user-supplied n_components (clamped against the "
            "available number of components in pasted_loadings)."
        ),
    )
    # ------------------------------------------------------------------
    # Loadings provenance — mirrors the v6 plan Delta A discipline.
    # The source of every methodology decision affecting interpretation
    # is echoed here so the consumer can audit without reaching for
    # the upstream config.
    # ------------------------------------------------------------------
    loadings_source: Literal["fit_inline", "pasted"] = Field(
        ...,
        description=(
            "Where the loadings came from: ``fit_inline`` → "
            "compute() called pca_yield_curve with the inline-fit "
            "params; ``pasted`` → loadings were supplied via "
            "pasted_loadings.  The other provenance fields below "
            "describe THAT source."
        ),
    )
    loadings_window_start: str = Field(
        ...,
        description=(
            "First trading day of the loadings fit window.  For "
            "fit_inline, this is the first date in the inline PCA "
            "primitive's centered-change panel.  For pasted, this "
            "is `pasted_loadings.fit_window_start`."
        ),
    )
    loadings_window_end: str = Field(
        ...,
        description=(
            "Last trading day of the loadings fit window.  Same "
            "semantics as loadings_window_start."
        ),
    )
    loadings_change_frequency_used: Literal["daily", "weekly"] = Field(
        ...,
        description=(
            "Yield-change frequency used for the upstream PCA fit "
            "(daily diff vs 5-day diff).  Mismatch with the "
            "intended interpretation of the change being attributed "
            "is the caller's responsibility — surfaced here for "
            "transparency."
        ),
    )
    loadings_n_observations_in_fit: int = Field(
        ...,
        ge=1,
        description=(
            "Number of yield-change observations used to fit the "
            "PCA whose loadings are being projected onto.  Must be "
            "≥ the YAML's min_observations_for_pca (enforced at "
            "validation time)."
        ),
    )
    loadings_sign_anchor_used: Literal["lock_pc_long_tenor_positive"] = Field(
        ...,
        description=(
            "PCA sign-anchor rule from the upstream fit.  V1 "
            "supports only `lock_pc_long_tenor_positive`; pasted "
            "payloads with any other anchor are rejected at "
            "validation time."
        ),
    )
    loadings_change_window_overlap_pct: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description=(
            "Percentage of trading days in the change window "
            "(start_date_resolved..end_date_resolved) that ALSO lie "
            "within the loadings fit window.  100% means the change "
            "is being attributed using loadings fit on a window "
            "that includes it.  0% means the change is fully "
            "out-of-sample relative to the fit window — a "
            "legitimate forecasting-style query, but the consumer "
            "should know.  Rounded per `overlap_pct_round_decimals`."
        ),
    )


class YieldChangeAttributionPcaOutput(BaseModel):
    """Top-level response for the yield_change_attribution_pca tool.

    Pure snapshot — no time-series payload.  The "time series" of
    this tool would be a time-varying attribution and is documented
    under planned_extensions as a sibling tool (different output
    schema).
    """

    model_config = ConfigDict(extra="forbid")

    current_metrics: YieldChangeAttributionPcaMetrics


__all__ = [
    "YieldChangeAttributionPcaInput",
    "YieldChangeAttributionPcaMetrics",
    "ComponentContribution",
    "YieldChangeAttributionPcaOutput",
]
