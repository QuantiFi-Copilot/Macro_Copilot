"""
compute.py — PCA on the yield-CHANGES panel of one rates curve.

Fifth tool of the v6 sprint.  Built on
``shared.analytics.stats.pca_yield_changes`` — the SVD math has a
single authoritative implementation.  Future siblings (correlation
PCA, sparse PCA, robust PCA) will share the primitive's surface
where they overlap.

Curve-family-agnostic scope (Round 3 Stage 2, work item A3 — PR5
coverage extension)
-------------------------------------------------------------------
The primitive accepts ANY tenor-keyed rates curve_family declared
in any playbook under ``rates_agent/playbooks/``.  Today that
covers sovereign benchmarks (UST / DE_BUND / IT_BTP / FR_OAT /
ES_BONO / UK_GILT / JGB / CANADA_GOVT / AU_GOVT), OIS curves
(USD_SOFR_OIS / EUR_ESTR_OIS / GBP_SONIA_OIS / JPY_OIS / AUD_OIS /
CAD_OIS), inflation swaps (USD_ZCIS / EUR_ZCIS / GBP_ZCIS), and
sovereign linker real-yield curves (USD_TIPS / GBP_LINKER /
EUR_FR_LINKER / CAD_RRB).  The PCA math itself is curve-family
agnostic — the SVD operates on whatever centered-yield-change panel
the fetcher returns.  Per the primitive runbook's "When NOT to use
this runbook" section, this is the PR5 coverage-extension path
(extend an existing primitive's allowed input values) rather than
shipping a sibling per curve family.

Per-playbook field-name discovery
---------------------------------
Different playbooks declare different Bloomberg primary fields:
sovereign benchmarks + linkers use ``YLD_YTM_MID``; OIS curves use
``PX_LAST``; ZCIS curves use ``PX_MID``.  The primitive auto-
discovers each curve_family's default field from the owning
playbook's ``target_metrics[0].bloomberg_field`` so callers do not
need to know the per-vendor field convention.  Resolution priority
when ``params.field_name`` is None:
  1. The owning playbook's ``target_metrics[0].bloomberg_field``.
  2. The YAML ``default_field_name`` (preserved as a final fallback,
     identical to sovereign's ``YLD_YTM_MID`` so legacy behaviour is
     unchanged for sovereign callers).
An explicit non-None ``params.field_name`` always wins.

Determinism boundary (A13)
--------------------------
The user-facing input surface exposes the structural choices that
define what the fit IS (`curve_family`, `tenors`, `lookback_days`,
`n_components`, `change_frequency`, optional `field_name`).  Every
ancillary methodology decision (`min_observations_for_pca`,
`sign_anchor`, `degenerate_variance_share_threshold`,
`ffill_limit_days`, all rounding decimals, default field) is sourced
from `config.yaml` and is NOT user-overridable in V1.

Honest-placeholder guard
------------------------
``sign_anchor`` is a STRUCTURAL choice locked at one value in V1.
compute() raises ``NotImplementedError`` if the YAML drifts from
``"lock_pc_long_tenor_positive"`` (matching the
butterfly trailing_range_window_days + rolling_regression
add_constant honest-placeholder pattern).

Cross-layer contract — controlled error envelope
------------------------------------------------
The PCA primitive raises ``ValueError`` when the centered change
panel has fewer than ``min_observations_for_pca`` rows.  compute()
catches and turns into the standard error envelope, with the
"is smaller than the YAML's" phrase shape that maps to HTTP 422 via
detail.py's user_input_phrases list — same client-error class as
zscore_custom + rolling_regression's small-window guards.

Boundary-rounding discipline
----------------------------
Every numeric output (loadings, variance_share, cumulative_share,
factor scores in both snapshot AND time-series surfaces) uses
``_round_or_none()`` with the corresponding YAML rounding knob.

Tenor sort
----------
Caller's tenor list is re-sorted into numeric-ascending order before
the fit (so "10Y" sorts before "20Y", not alphabetically).  The
result's ``tenors_used`` field reports the actual order used.

Test seam
---------
``fetch_tenor_group``, ``date``, and the playbook-tenor lookup
all live inside compute()'s namespace; tests patch them at
``rates_agent.sovereign_bonds.tools.pca_yield_curve.compute.X``.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.pca_yield_curve.schemas import (
    LoadingRow,
    PcaComponentMetadata,
    PcaYieldCurveInput,
    PcaYieldCurveMetrics,
    PcaYieldCurveOutput,
    VarianceShareRow,
)
from shared.analytics.playbook_discovery import (
    PLAYBOOK_ROOT,
    playbook_default_field_for_curve_family,
    playbook_tenors_for_curve_family,
    tenor_to_years,
)
from shared.analytics.rates_fetch import fetch_tenor_group, latest_trade_date
from shared.analytics.spreads import pivot_and_align_tenors
from shared.analytics.stats import pca_yield_changes
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


# Bundled config — public symbol so external callers can build a
# ToolConfig from the same source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# PLAYBOOK_ROOT, playbook_curve_family_index(),
# playbook_tenors_for_curve_family(), and
# playbook_default_field_for_curve_family() are imported above from
# shared.analytics.playbook_discovery — the single source of truth
# for the multi-playbook scanner (per P10).  This module previously
# defined those helpers locally (Round 3 A3 v1, PR #195 first
# revision); they were extracted to shared/ on Codex review to
# prevent the Round 3 A4 sibling primitive (classify_curve_move)
# from duplicating the same scanner per-folder.


# Locked structural-choice value.  compute() raises NotImplementedError
# if YAML drifts from this.
_LOCKED_SIGN_ANCHOR: str = "lock_pc_long_tenor_positive"


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull methodology kwargs the PCA tool needs from a ToolConfig."""
    sign_anchor = config.convention_value("sign_anchor")
    if sign_anchor != _LOCKED_SIGN_ANCHOR:
        raise NotImplementedError(
            f"sign_anchor={sign_anchor!r} is documented in this tool's "
            f"config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented.  "
            f"V1 supports only sign_anchor='{_LOCKED_SIGN_ANCHOR}' (each "
            f"PC's loading at the longest tenor is non-negative).  "
            f"Alternative anchors (max_abs_loading_positive, "
            f"first_tenor_positive) are sibling tools, not configurable "
            f"in V1; their downstream interpretation in "
            f"yield_change_attribution_pca differs."
        )
    return {
        "min_observations_for_pca": config.convention_value(
            "min_observations_for_pca"
        ),
        "sign_anchor": sign_anchor,
        "degenerate_variance_share_threshold": config.convention_value(
            "degenerate_variance_share_threshold"
        ),
        "ffill_limit": config.convention_value("ffill_limit_days"),
        "loading_dec": config.convention_value("loading_round_decimals"),
        "variance_share_dec": config.convention_value(
            "variance_share_round_decimals"
        ),
        "factor_dec": config.convention_value("factor_round_decimals"),
        "default_field_name": config.convention_value("default_field_name"),
    }


# ============================================================================
# HELPERS
# ============================================================================

def _round_or_none(value: Any, decimals: int) -> Optional[float]:
    """Round a value to `decimals` places, returning None on
    None/NaN."""
    if value is None:
        return None
    try:
        f = float(value)
        if math.isnan(f):
            return None
        return round(f, decimals)
    except (TypeError, ValueError):
        return None


def _tenor_to_years(t: str) -> float:
    """Backward-compatible re-export of the shared utility so any
    test that previously patched
    ``rates_agent.sovereign_bonds.tools.pca_yield_curve.compute
    ._tenor_to_years`` keeps working.  The canonical implementation
    lives in ``shared.analytics.playbook_discovery.tenor_to_years``."""
    return tenor_to_years(t)


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_pca_yield_curve(
    engine: Engine,
    params: PcaYieldCurveInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Run PCA on the yield-changes panel of one rates curve and return
    the loadings + variance shares + factor scores.

    Accepts any ``curve_family`` declared in any tenor-keyed playbook
    under ``rates_agent/playbooks/`` — sovereign benchmarks, OIS
    curves, inflation swaps, and sovereign linker real-yield curves
    all share the same PCA code path.  See module docstring's
    "Curve-family-agnostic scope" block.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : PcaYieldCurveInput
        Validated input.  ``field_name=None`` resolves to the owning
        playbook's ``target_metrics[0].bloomberg_field`` first, then
        falls back to the YAML's ``default_field_name``.  ``tenors=
        None`` uses all available tenors discovered in the fetched
        panel.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.

    Returns
    -------
    dict
        Serialised ``PcaYieldCurveOutput``, or ``{"error": "..."}``
        on recoverable failure.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    conv = _conventions_from_config(config)
    min_obs = conv["min_observations_for_pca"]
    sign_anchor = conv["sign_anchor"]
    degenerate_threshold = conv["degenerate_variance_share_threshold"]
    ffill_limit = conv["ffill_limit"]
    loading_dec = conv["loading_dec"]
    variance_share_dec = conv["variance_share_dec"]
    factor_dec = conv["factor_dec"]
    default_field = conv["default_field_name"]

    # Field-name resolution priority:
    #   1. Explicit params.field_name (LLM / API caller override).
    #   2. The owning playbook's target_metrics[0].bloomberg_field
    #      (auto-discovered from rates_agent/playbooks/ — different
    #      per playbook: sovereign + linker use YLD_YTM_MID, OIS uses
    #      PX_LAST, ZCIS uses PX_MID).
    #   3. The YAML's ``default_field_name`` (final fallback;
    #      preserved as YLD_YTM_MID for sovereign backward compat).
    if params.field_name is not None:
        field_name_resolved = params.field_name
    else:
        discovered_field = playbook_default_field_for_curve_family(
            params.curve_family
        )
        field_name_resolved = (
            discovered_field if discovered_field else default_field
        )

    # ------------------------------------------------------------------
    # 1. Resolve the exact tenor universe this fit is allowed to use.
    #    * tenors=None   -> use the curve_family's playbook universe.
    #    * tenors=[...]  -> validate against that universe and fit
    #                      exactly those tenors (no silent dropping).
    #    The frozen T13 contract requires explicit-tenor requests to be
    #    validated rather than silently altered by data availability.
    # ------------------------------------------------------------------
    try:
        playbook_tenors = playbook_tenors_for_curve_family(params.curve_family)
    except ValueError as exc:
        return {"error": str(exc)}

    if params.tenors is None:
        requested_tenors = playbook_tenors
    else:
        requested_tenors = list(params.tenors)
        duplicate_tenors = [
            tenor for tenor in requested_tenors
            if requested_tenors.count(tenor) > 1
        ]
        if duplicate_tenors:
            duplicates_unique = list(dict.fromkeys(duplicate_tenors))
            return {
                "error": (
                    f"Duplicate tenor(s) requested for "
                    f"curve_family='{params.curve_family}': "
                    f"{duplicates_unique}.  PCA requires each tenor at "
                    "most once in the fit."
                )
            }
        invalid_tenors = [t for t in requested_tenors if t not in playbook_tenors]
        if invalid_tenors:
            return {
                "error": (
                    f"Missing tenor(s) from the playbook universe for "
                    f"curve_family='{params.curve_family}': {invalid_tenors}.  "
                    f"Playbook tenors: {playbook_tenors}."
                )
            }

    if params.n_components > len(requested_tenors):
        return {
            "error": (
                f"n_components={params.n_components} exceeds the number "
                f"of requested tenors ({len(requested_tenors)}: "
                f"{requested_tenors}).  Either request fewer components "
                "or supply more tenors."
            )
        }

    # Anchor to the latest available trade_date (not date.today()) so the
    # window resolves to real data when ingestion lags; falls back to today
    # only when the curve has no rows (e.g. mocked engine=None in unit tests).
    anchor = (
        latest_trade_date(engine, curve_family=params.curve_family)
        or date.today()
    )
    start_date = anchor - timedelta(days=params.lookback_days)

    raw_df = fetch_tenor_group(
        engine=engine,
        curve_family=params.curve_family,
        tenors=requested_tenors,
        field_name=field_name_resolved,
        start_date=start_date,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No data found for curve_family='{params.curve_family}', "
                f"tenors={requested_tenors}, field='{field_name_resolved}' "
                f"since {start_date.isoformat()}.  Verify the curve "
                "family + tenors exist in the database."
            )
        }

    # ------------------------------------------------------------------
    # 2. The fit must use the exact requested tenor set.  Missing tenors
    #    are a data-availability problem, not something the tool is
    #    allowed to silently paper over by fitting a different panel.
    # ------------------------------------------------------------------
    available = set(raw_df["tenor"].unique())
    missing_from_panel = [t for t in requested_tenors if t not in available]
    if missing_from_panel:
        return {
            "error": (
                f"Missing tenor(s) in the fetched panel for "
                f"curve_family='{params.curve_family}': {missing_from_panel}.  "
                f"Requested tenors: {requested_tenors}; available in fetched "
                f"data: {sorted(available)}."
            )
        }
    tenors_for_fit = requested_tenors

    # ------------------------------------------------------------------
    # 3. Pivot to wide format + ffill holiday gaps + dropna rows
    #    where any required tenor is still missing
    # ------------------------------------------------------------------
    wide = pivot_and_align_tenors(
        raw_df,
        required_tenors=tenors_for_fit,
        ffill_limit=ffill_limit,
    )
    if wide.empty:
        return {
            "error": (
                f"After aligning dates for tenors {tenors_for_fit} on "
                f"'{params.curve_family}', no overlapping observations "
                "remain."
            )
        }

    # ------------------------------------------------------------------
    # 4. Sort tenors numerically-ascending so position[0] = shortest,
    #    position[-1] = longest.  The PCA primitive's sign-anchor
    #    tie-break uses these positions.
    # ------------------------------------------------------------------
    tenors_ordered = sorted(tenors_for_fit, key=_tenor_to_years)

    # ------------------------------------------------------------------
    # 5. Run the PCA primitive
    # ------------------------------------------------------------------
    try:
        fit = pca_yield_changes(
            panel=wide,
            tenors_ordered=tenors_ordered,
            n_components=params.n_components,
            change_frequency=params.change_frequency,
            sign_anchor=sign_anchor,
            min_observations=min_obs,
            degenerate_variance_share_threshold=degenerate_threshold,
        )
    except ValueError as exc:
        msg = str(exc)
        # Reword the primitive's "PCA primitive requires at least N"
        # message into the "is smaller than the YAML's" phrase shape
        # that detail.py's user_input_phrases list maps to HTTP 422.
        if "PCA primitive requires at least" in msg:
            return {
                "error": (
                    f"change panel length is smaller than the YAML's "
                    f"min_observations_for_pca={min_obs}.  Either "
                    f"supply a longer lookback_days, or edit "
                    f"min_observations_for_pca in pca_yield_curve/"
                    f"config.yaml.  Underlying detail: {msg}"
                )
            }
        return {"error": msg}

    # ------------------------------------------------------------------
    # 6. Build snapshot output
    # ------------------------------------------------------------------
    component_names: List[str] = list(fit.loadings.columns)

    # loadings: one row per tenor; each row has tenor + per-component
    # values.
    loading_rows: List[LoadingRow] = []
    for tenor in tenors_ordered:
        row_kwargs: Dict[str, Any] = {"tenor": tenor}
        for comp in component_names:
            value = fit.loadings.at[tenor, comp]
            row_kwargs[comp] = _round_or_none(value, loading_dec)
        loading_rows.append(LoadingRow(**row_kwargs))

    # variance_explained: one row per component
    variance_rows: List[VarianceShareRow] = [
        VarianceShareRow(
            component_name=comp,
            variance_share=float(_round_or_none(
                fit.variance_share[comp], variance_share_dec
            ) or 0.0),
            cumulative_share=float(_round_or_none(
                fit.cumulative_variance_share[comp], variance_share_dec
            ) or 0.0),
        )
        for comp in component_names
    ]
    total_var_explained = float(
        _round_or_none(
            fit.cumulative_variance_share.iloc[-1], variance_share_dec,
        ) or 0.0
    )

    # current_factor_levels: latest row of factor_scores, per component
    last_row = fit.factor_scores.iloc[-1]
    current_factor_levels: Dict[str, Optional[float]] = {
        comp: _round_or_none(last_row[comp], factor_dec)
        for comp in component_names
    }

    # component_metadata: lift from the primitive
    metadata = [
        PcaComponentMetadata(
            component_name=m.component_name,
            quality_flag=m.quality_flag,  # type: ignore[arg-type]
            quality_note=m.quality_note,
        )
        for m in fit.component_metadata
    ]

    metrics = PcaYieldCurveMetrics(
        as_of_date=fit.fit_window_end,
        fit_window_start=fit.fit_window_start,
        fit_window_end=fit.fit_window_end,
        curve_family=params.curve_family,
        tenors_used=tenors_ordered,
        lookback_days_used=params.lookback_days,
        n_components_returned=params.n_components,
        change_frequency_used=params.change_frequency,
        sign_anchor_used=sign_anchor,
        loadings=loading_rows,
        variance_explained=variance_rows,
        total_variance_explained=total_var_explained,
        current_factor_levels=current_factor_levels,
        component_metadata=metadata,
        observation_count=fit.n_observations_in_fit,
    )

    # ------------------------------------------------------------------
    # 7. Build TimeSeries — one per component
    # ------------------------------------------------------------------
    time_series_factors: List[TimeSeries] = []
    for comp in component_names:
        col = fit.factor_scores[comp]
        rows = [
            TimeSeriesRow(
                date=ts_idx.strftime("%Y-%m-%d"),
                value=_round_or_none(val, factor_dec),
            )
            for ts_idx, val in col.items()
        ]
        time_series_factors.append(
            TimeSeries(
                series_name=(
                    f"{params.curve_family.lower()}_{comp}_factor_"
                    f"{params.change_frequency}"
                ),
                units=TimeSeriesUnits.FACTOR_LEVEL,
                description=(
                    f"{comp.upper()} factor score (per-row projection "
                    f"of centered yield {params.change_frequency} "
                    f"changes onto the eigenvector at "
                    f"{tenors_ordered}).  Sign anchor: "
                    f"{sign_anchor}."
                ),
                rows=rows,
            )
        )

    output = PcaYieldCurveOutput(
        current_metrics=metrics,
        time_series_factors=time_series_factors,
    )
    return output.model_dump()
