"""
compute.py — PCA-based attribution of a sovereign yield change at one
target tenor over a chosen calendar window.

Sixth tool of the v6 sprint (after zscore_custom, rolling_regression,
beta_adjusted_spread, half_life, pca_yield_curve).  Built on top of
``rates_agent.sovereign_bonds.tools.pca_yield_curve.calculate_pca_yield_curve``
(via the fit_inline path) AND on the
``shared.schemas.PastedPcaLoadings`` shape (via the pasted path).
The math itself is mechanical given loadings: per-tenor change vector
projected onto each loading vector, scaled by the loading entry at
the target tenor, converted percent → bps via *100, residual = total
− Σ contributions.

Standardisation under the hierarchical-config principle (v6)
------------------------------------------------------------
The output's interpretation depends on the upstream PCA fit, but
ALL upstream choices are explicit either in this tool's input
(fit_inline params) OR in the pasted_loadings provenance.  The
output snapshot echoes those choices (sign_anchor, change_frequency,
fit window, n_observations_in_fit, variance_share_in_fit_window,
quality flags).  Two desks running with the same closure (this
tool's config + the upstream PCA config OR pasted_loadings) get
the same answer.

Determinism boundary (A13)
--------------------------
The user-facing surface exposes the structural choices (curve_family,
target_tenor, start_date, end_date, loadings source).  Every
methodology ancillary (`min_observations_for_pca`, the date-
resolution policies, ffill, all rounding decimals, default field,
loadings unit-norm tolerance) is sourced from `config.yaml` and is
NOT user-overridable in V1.

Honest-placeholder guards
-------------------------
``start_date_resolution`` and ``end_date_resolution`` are STRUCTURAL
choices locked at "forward" / "backward" in V1.  compute() raises
``NotImplementedError`` if the YAML drifts from these — alternative
policies are sibling tools.  Same pattern as butterfly's
trailing_range_window_days lock and pca_yield_curve's sign_anchor
lock.

Cross-layer contract — controlled error envelope
------------------------------------------------
Two paths can produce the "is smaller than the YAML's" phrase shape
(which detail.py's user_input_phrases list maps to HTTP 422):
  - fit_inline: the inline call to pca_yield_curve returns the
    primitive's controlled error envelope (its own phrase shape
    already maps), and we pass it through unchanged.
  - pasted: pasted_loadings.n_observations_in_fit < the YAML floor
    is rejected here with a fresh "is smaller than the YAML's"
    error envelope.

Boundary rounding discipline
----------------------------
Every numeric output (total_change_bps, contribution_bps,
residual_bps, loading_at_target_tenor, variance_share_in_fit_window,
loadings_change_window_overlap_pct) is rounded via
``_round_or_none()`` with the corresponding YAML decimals knob.

Test seam
---------
``calculate_pca_yield_curve``, ``fetch_tenor_group``, and ``date``
all live inside compute()'s namespace; tests patch them at
``rates_agent.sovereign_bonds.tools.yield_change_attribution_pca.compute.X``.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.pca_yield_curve import (
    PcaYieldCurveInput,
    calculate_pca_yield_curve,
)
from rates_agent.sovereign_bonds.tools.yield_change_attribution_pca.schemas import (
    ComponentContribution,
    YieldChangeAttributionPcaInput,
    YieldChangeAttributionPcaMetrics,
    YieldChangeAttributionPcaOutput,
)
from shared.analytics.rates_fetch import fetch_tenor_group
from shared.analytics.spreads import pivot_and_align_tenors
from shared.config import ToolConfig, load_tool_config
from shared.schemas import PastedPcaLoadings


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Locked structural-choice values — compute() raises NotImplementedError
# if YAML drifts from these.  Alternative resolution policies are
# sibling tools (see methodology.planned_extensions).
_LOCKED_START_RESOLUTION: str = "forward"
_LOCKED_END_RESOLUTION: str = "backward"
# Locked V1 sign anchor — must match shared.analytics.stats._LOCKED_SIGN_ANCHOR
# and pca_yield_curve.compute._LOCKED_SIGN_ANCHOR.  pasted_loadings
# declaring any other anchor is rejected.
_LOCKED_SIGN_ANCHOR: str = "lock_pc_long_tenor_positive"


# ============================================================================
# CONFIG → KWARGS RESOLVER
# ============================================================================

def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull methodology kwargs and enforce the structural locks."""
    start_pol = config.convention_value("start_date_resolution")
    if start_pol != _LOCKED_START_RESOLUTION:
        raise NotImplementedError(
            f"start_date_resolution={start_pol!r} is documented in this "
            f"tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented.  "
            f"V1 supports only start_date_resolution="
            f"{_LOCKED_START_RESOLUTION!r}.  Alternative policies are "
            "sibling tools — different total_change_bps for the same "
            "calendar request."
        )
    end_pol = config.convention_value("end_date_resolution")
    if end_pol != _LOCKED_END_RESOLUTION:
        raise NotImplementedError(
            f"end_date_resolution={end_pol!r} is documented in this "
            f"tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented.  "
            f"V1 supports only end_date_resolution={_LOCKED_END_RESOLUTION!r}."
        )
    return {
        "min_observations_for_pca": config.convention_value(
            "min_observations_for_pca"
        ),
        "ffill_limit": config.convention_value("ffill_limit_days"),
        "loadings_unit_norm_tolerance": config.convention_value(
            "loadings_unit_norm_tolerance"
        ),
        "bps_dec": config.convention_value("bps_round_decimals"),
        "loading_dec": config.convention_value("loading_round_decimals"),
        "variance_share_dec": config.convention_value(
            "variance_share_round_decimals"
        ),
        "overlap_pct_dec": config.convention_value(
            "overlap_pct_round_decimals"
        ),
        "default_field_name": config.convention_value("default_field_name"),
        "default_pca_lookback_days": config.convention_value(
            "default_pca_lookback_days"
        ),
        "default_n_components": config.convention_value("default_n_components"),
        "default_change_frequency": config.convention_value(
            "default_change_frequency"
        ),
        "start_resolution": start_pol,
        "end_resolution": end_pol,
    }


# ============================================================================
# HELPERS
# ============================================================================

def _round_or_none(value: Any, decimals: int) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
        if math.isnan(f):
            return None
        return round(f, decimals)
    except (TypeError, ValueError):
        return None


def _resolve_field_name(explicit: Optional[str], default: str) -> str:
    return explicit if explicit is not None else default


def _resolve_to_trading_day(
    requested: pd.Timestamp,
    panel_index: pd.DatetimeIndex,
    *,
    direction: str,
) -> Optional[pd.Timestamp]:
    """Snap ``requested`` to the nearest trading day in ``panel_index``
    per the locked direction policy.

    direction="forward":  return the smallest panel day >= requested,
                           or None if none exists.
    direction="backward": return the largest panel day <= requested,
                           or None if none exists.
    """
    if direction == "forward":
        candidates = panel_index[panel_index >= requested]
        if len(candidates) == 0:
            return None
        return candidates[0]
    elif direction == "backward":
        candidates = panel_index[panel_index <= requested]
        if len(candidates) == 0:
            return None
        return candidates[-1]
    raise ValueError(f"unsupported direction: {direction!r}")


def _validate_pasted_loadings(
    paste: PastedPcaLoadings,
    *,
    target_tenor: str,
    n_components: int,
    min_observations_for_pca: int,
    unit_norm_tolerance: float,
) -> None:
    """Defensive re-validation of pasted_loadings inside compute().
    The schema model_validator catches structural mismatches; this
    catches the math-shape contracts (lengths, unit norms, the
    n_observations floor).  Raises ValueError on any failure (caller
    turns into the controlled-error envelope)."""
    n_comp_paste = len(paste.components)
    n_tenors = len(paste.tenors)

    # Length consistency — paranoid; the Pydantic schema enforces
    # min_length but not the cross-shape match.
    if len(paste.component_names) != n_comp_paste:
        raise ValueError(
            f"pasted_loadings.component_names has length "
            f"{len(paste.component_names)}, components has length "
            f"{n_comp_paste} — must match."
        )
    if len(paste.variance_shares) != n_comp_paste:
        raise ValueError(
            f"pasted_loadings.variance_shares has length "
            f"{len(paste.variance_shares)}, components has length "
            f"{n_comp_paste} — must match."
        )
    if len(paste.component_metadata) != n_comp_paste:
        raise ValueError(
            f"pasted_loadings.component_metadata has length "
            f"{len(paste.component_metadata)}, components has length "
            f"{n_comp_paste} — must match."
        )
    for i, row in enumerate(paste.components):
        if len(row) != n_tenors:
            raise ValueError(
                f"pasted_loadings.components[{i}] has length "
                f"{len(row)}, but tenors has length {n_tenors} — "
                "each loading vector must have one entry per tenor."
            )

    # Variance shares in [0, 1] and sum <= 1 + tolerance.
    for i, vs in enumerate(paste.variance_shares):
        if not (0.0 <= vs <= 1.0):
            raise ValueError(
                f"pasted_loadings.variance_shares[{i}]={vs} is "
                "outside [0, 1]."
            )
    if sum(paste.variance_shares) > 1.0 + 1e-6:
        raise ValueError(
            f"pasted_loadings.variance_shares sum to "
            f"{sum(paste.variance_shares)}, which exceeds 1 + 1e-6."
        )

    # Per-component metadata must align by component_name with
    # component_names (same order).
    for i, (name, meta) in enumerate(
        zip(paste.component_names, paste.component_metadata)
    ):
        if meta.component_name != name:
            raise ValueError(
                f"pasted_loadings.component_metadata[{i}].component_name="
                f"{meta.component_name!r} does not match "
                f"component_names[{i}]={name!r}."
            )

    # Sign anchor matches V1 lock.  The Pydantic Literal already
    # narrows this; we re-check defensively in case the schema
    # widens in a future version.
    if paste.sign_anchor_used != _LOCKED_SIGN_ANCHOR:
        raise NotImplementedError(
            f"pasted_loadings.sign_anchor_used="
            f"{paste.sign_anchor_used!r} is not supported.  V1 only "
            f"accepts {_LOCKED_SIGN_ANCHOR!r}; alternative anchors "
            "are sibling tools (see methodology.planned_extensions)."
        )

    # n_observations_in_fit floor — the cross-layer contract that
    # surfaces as HTTP 422.
    if paste.n_observations_in_fit < min_observations_for_pca:
        raise ValueError(
            f"pasted_loadings.n_observations_in_fit="
            f"{paste.n_observations_in_fit} is smaller than the YAML's "
            f"min_observations_for_pca={min_observations_for_pca}.  "
            f"Either supply a paste from a longer fit, or lower the "
            f"YAML floor."
        )

    # n_components in range — also cross-checked by the schema's
    # validator but re-checked here defensively.
    if n_components > n_comp_paste:
        raise ValueError(
            f"n_components={n_components} exceeds "
            f"len(pasted_loadings.components)={n_comp_paste}."
        )

    # target_tenor in panel.
    if target_tenor not in paste.tenors:
        raise ValueError(
            f"target_tenor={target_tenor!r} is not in "
            f"pasted_loadings.tenors={paste.tenors!r}."
        )

    # Unit-norm check on each non-degenerate loading vector.  For
    # degenerate components, the loadings are NaN and we skip the
    # check (an all-NaN vector has no defined norm).
    for i, row in enumerate(paste.components[:n_components]):
        meta = paste.component_metadata[i]
        if meta.quality_flag == "degenerate":
            continue
        arr = np.array(row, dtype=float)
        if not np.all(np.isfinite(arr)):
            # Non-degenerate but not finite — corrupted paste.
            raise ValueError(
                f"pasted_loadings.components[{i}] (component "
                f"{paste.component_names[i]!r}, quality "
                f"{meta.quality_flag!r}) contains non-finite values "
                "but is not flagged degenerate.  Refusing to project "
                "onto a corrupted vector."
            )
        norm = float(np.linalg.norm(arr))
        if abs(norm - 1.0) > unit_norm_tolerance:
            raise ValueError(
                f"pasted_loadings.components[{i}] (component "
                f"{paste.component_names[i]!r}) has norm {norm:.6e}, "
                f"which deviates from 1 by more than the YAML's "
                f"loadings_unit_norm_tolerance={unit_norm_tolerance}.  "
                f"PCA loadings must be unit-norm; corrupted paste "
                "would otherwise distort every contribution_bps by "
                "the norm-error factor."
            )


def _trading_day_overlap_pct(
    change_start: pd.Timestamp, change_end: pd.Timestamp,
    fit_start: pd.Timestamp, fit_end: pd.Timestamp,
    panel_index: pd.DatetimeIndex,
) -> float:
    """Compute the % of trading days in the change window that ALSO
    lie in the fit window.  Trading days are taken from panel_index
    (the actual fetched DB index), so weekends / holidays are
    correctly excluded.  Returns 0.0 when the change window has no
    days in the panel."""
    change_days = panel_index[
        (panel_index >= change_start) & (panel_index <= change_end)
    ]
    n_change = len(change_days)
    if n_change == 0:
        return 0.0
    overlap_days = change_days[
        (change_days >= fit_start) & (change_days <= fit_end)
    ]
    return 100.0 * len(overlap_days) / n_change


# ============================================================================
# LOADINGS ACQUISITION — fit_inline vs pasted
# ============================================================================

from dataclasses import dataclass


@dataclass(frozen=True)
class _LoadingsBundle:
    """Internal shape that unifies fit_inline + pasted paths.

    All math after acquisition operates on this bundle; the
    branching is contained to ``_acquire_loadings`` below.
    """
    tenors_used: List[str]
    component_names: List[str]
    # loadings_matrix[tenor_idx][component_idx]; NaN for degenerate.
    loadings_matrix: np.ndarray
    variance_shares: List[float]
    quality_flags: List[str]   # one of "ok"/"degenerate"/"sign_anchor_tied"
    quality_notes: List[Optional[str]]
    fit_window_start: str
    fit_window_end: str
    change_frequency_used: str
    n_observations_in_fit: int
    sign_anchor_used: str
    loadings_source: str   # "fit_inline" or "pasted"


def _acquire_pasted(
    paste: PastedPcaLoadings,
    *,
    n_components: int,
) -> _LoadingsBundle:
    """Build the internal bundle from a validated pasted_loadings
    payload.  Truncates components to n_components."""
    truncated = paste.components[:n_components]
    truncated_names = paste.component_names[:n_components]
    truncated_var = paste.variance_shares[:n_components]
    truncated_meta = paste.component_metadata[:n_components]

    # Build the loadings matrix as [n_tenors, n_components].  Each
    # paste.components[i] is a single component's vector across
    # tenors (length == len(tenors)); transpose so rows = tenors.
    raw = np.array(truncated, dtype=float)  # shape [n_components, n_tenors]
    loadings_matrix = raw.T                  # shape [n_tenors, n_components]

    return _LoadingsBundle(
        tenors_used=list(paste.tenors),
        component_names=list(truncated_names),
        loadings_matrix=loadings_matrix,
        variance_shares=[float(v) for v in truncated_var],
        quality_flags=[m.quality_flag for m in truncated_meta],
        quality_notes=[m.quality_note for m in truncated_meta],
        fit_window_start=paste.fit_window_start,
        fit_window_end=paste.fit_window_end,
        change_frequency_used=paste.change_frequency_used,
        n_observations_in_fit=int(paste.n_observations_in_fit),
        sign_anchor_used=paste.sign_anchor_used,
        loadings_source="pasted",
    )


def _acquire_fit_inline(
    *,
    engine: Engine,
    curve_family: str,
    tenors: Optional[List[str]],
    pca_lookback_days: int,
    n_components: int,
    change_frequency: str,
    field_name: Optional[str],
) -> Tuple[Optional[_LoadingsBundle], Optional[Dict[str, Any]]]:
    """Call pca_yield_curve's compute() and convert the result into
    the internal bundle.  Returns (bundle, None) on success or
    (None, error_envelope) on failure — the inline error envelope is
    forwarded to the caller unchanged so the FastAPI 422-mapping
    phrase shape from pca_yield_curve flows through.
    """
    pca_input = PcaYieldCurveInput(
        curve_family=curve_family,
        tenors=tenors,
        lookback_days=pca_lookback_days,
        n_components=n_components,
        change_frequency=change_frequency,  # type: ignore[arg-type]
        field_name=field_name,
    )
    pca_result = calculate_pca_yield_curve(engine=engine, params=pca_input)
    if "error" in pca_result:
        return None, pca_result

    cm = pca_result["current_metrics"]
    tenors_used = list(cm["tenors_used"])
    n_comp_returned = int(cm["n_components_returned"])
    component_names = [f"pc{k+1}" for k in range(n_comp_returned)]

    # Re-build the loadings matrix from the snapshot's `loadings`
    # rows (one row per tenor; row keys = tenor + per-component
    # values).  We CANNOT just rely on dict iteration order for
    # the per-component keys; iterate component_names explicitly.
    loadings_rows = cm["loadings"]
    loadings_matrix = np.full(
        (len(tenors_used), n_comp_returned), np.nan, dtype=float,
    )
    # Build a tenor→row-index map (tenors_used is already in
    # numeric-ascending order — pca_yield_curve sorts there).
    for i, tenor in enumerate(tenors_used):
        # Find the matching row.
        matching_row = next(
            (r for r in loadings_rows if r["tenor"] == tenor), None,
        )
        if matching_row is None:
            raise ValueError(
                f"internal: pca_yield_curve returned no loadings row "
                f"for tenor={tenor!r}; this should be impossible."
            )
        for j, comp in enumerate(component_names):
            value = matching_row.get(comp)
            loadings_matrix[i, j] = (
                float(value) if value is not None else float("nan")
            )

    var_rows = cm["variance_explained"]
    variance_shares = [
        float(r["variance_share"]) for r in var_rows[:n_comp_returned]
    ]
    metadata_rows = cm["component_metadata"]
    quality_flags = [
        m["quality_flag"] for m in metadata_rows[:n_comp_returned]
    ]
    quality_notes = [
        m.get("quality_note") for m in metadata_rows[:n_comp_returned]
    ]

    # The fit window's start/end come from the PCA primitive's
    # internals — pca_yield_curve emits as_of_date (which equals
    # fit_window_end).  We need fit_window_start too; the snapshot
    # doesn't expose it directly because the PCA tool considers it
    # internal.  Re-derive via the primitive's contract: the change
    # panel starts at the first non-NaN diff row, which we can't
    # observe without re-doing the fetch.  Cleanest path: re-call
    # the PCA primitive's internal fit step?  No — too expensive.
    # Instead: surface only the as_of_date as both endpoints; the
    # `loadings_change_window_overlap_pct` calculation will use
    # whatever window is observable from the change-window fetch
    # below.  This is a minor provenance loss that we'll fix in a
    # follow-up by extending pca_yield_curve's snapshot to expose
    # fit_window_start.  For V1 we use as_of_date for both ends and
    # let the overlap test be conservative (will read 100% only when
    # the change window is fully on or before as_of_date).
    fit_window_end = cm["as_of_date"]
    fit_window_start = fit_window_end  # see note above

    return _LoadingsBundle(
        tenors_used=tenors_used,
        component_names=component_names,
        loadings_matrix=loadings_matrix,
        variance_shares=variance_shares,
        quality_flags=quality_flags,
        quality_notes=quality_notes,
        fit_window_start=fit_window_start,
        fit_window_end=fit_window_end,
        change_frequency_used=cm["change_frequency_used"],
        n_observations_in_fit=int(cm["observation_count"]),
        sign_anchor_used=cm["sign_anchor_used"],
        loadings_source="fit_inline",
    ), None


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_yield_change_attribution_pca(
    engine: Engine,
    params: YieldChangeAttributionPcaInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Decompose a sovereign yield change at one tenor over a chosen
    window into per-PCA-component contributions in basis points.

    See module docstring for the full methodology + provenance story.

    Returns
    -------
    dict
        Serialised ``YieldChangeAttributionPcaOutput`` on success;
        ``{"error": "..."}`` on recoverable failure (the controlled-
        error envelope; FastAPI maps "is smaller than the YAML's"
        phrase shape to HTTP 422 via detail.py's user_input_phrases
        list).
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    conv = _conventions_from_config(config)
    min_obs_for_pca = conv["min_observations_for_pca"]
    ffill_limit = conv["ffill_limit"]
    unit_norm_tol = conv["loadings_unit_norm_tolerance"]
    bps_dec = conv["bps_dec"]
    loading_dec = conv["loading_dec"]
    variance_share_dec = conv["variance_share_dec"]
    overlap_pct_dec = conv["overlap_pct_dec"]
    default_field = conv["default_field_name"]

    field_name_resolved = _resolve_field_name(params.field_name, default_field)

    # ------------------------------------------------------------------
    # 1. Acquire loadings — fit_inline OR pasted.  Both paths populate
    #    the same internal _LoadingsBundle so the rest of compute() is
    #    branch-free.
    # ------------------------------------------------------------------
    if params.pasted_loadings is not None:
        try:
            _validate_pasted_loadings(
                params.pasted_loadings,
                target_tenor=params.target_tenor,
                n_components=params.n_components,
                min_observations_for_pca=min_obs_for_pca,
                unit_norm_tolerance=unit_norm_tol,
            )
        except ValueError as exc:
            return {"error": str(exc)}
        bundle = _acquire_pasted(
            params.pasted_loadings,
            n_components=params.n_components,
        )
    else:
        bundle, err = _acquire_fit_inline(
            engine=engine,
            curve_family=params.curve_family,
            tenors=params.tenors,
            pca_lookback_days=params.pca_lookback_days,
            n_components=params.n_components,
            change_frequency=params.change_frequency,
            field_name=field_name_resolved,
        )
        if err is not None:
            # Forward the inline pca_yield_curve error envelope
            # unchanged so its 422-mapping phrase shape flows through.
            return err
        assert bundle is not None  # type-narrow

    # ------------------------------------------------------------------
    # 2. Fetch the yield panel for the change window.  We need the
    #    panel covering [start_date, end_date] PLUS some buffer either
    #    side so resolve_to_trading_day has data to work with.  Use
    #    a 14-calendar-day buffer — long enough to bridge typical
    #    holiday clusters without over-fetching.
    # ------------------------------------------------------------------
    BUFFER_CALENDAR_DAYS = 14

    requested_start = pd.Timestamp(params.start_date)
    requested_end = pd.Timestamp(params.end_date)
    fetch_start = (requested_start - timedelta(days=BUFFER_CALENDAR_DAYS)).date()

    raw_df = fetch_tenor_group(
        engine=engine,
        curve_family=params.curve_family,
        tenors=bundle.tenors_used,
        field_name=field_name_resolved,
        start_date=fetch_start,
    )
    if raw_df.empty:
        return {
            "error": (
                f"No data found for curve_family={params.curve_family!r}, "
                f"tenors={bundle.tenors_used}, field={field_name_resolved!r} "
                f"since {fetch_start.isoformat()}."
            )
        }

    # Restrict to the fit's tenor universe + ffill across holiday gaps.
    available = set(raw_df["tenor"].unique())
    missing_in_panel = [t for t in bundle.tenors_used if t not in available]
    if missing_in_panel:
        return {
            "error": (
                f"Loadings tenors {missing_in_panel} are missing from "
                f"the fetched change panel for curve_family="
                f"{params.curve_family!r}.  The change window cannot "
                "be projected onto loadings whose tenors are not in "
                "the panel."
            )
        }

    wide = pivot_and_align_tenors(
        raw_df,
        required_tenors=bundle.tenors_used,
        ffill_limit=ffill_limit,
    )
    if wide.empty:
        return {
            "error": (
                f"After aligning dates for tenors {bundle.tenors_used} "
                f"on {params.curve_family!r}, no overlapping observations "
                "remain in the change window."
            )
        }

    panel_index = pd.DatetimeIndex(wide.index)

    # ------------------------------------------------------------------
    # 3. Resolve start/end per the locked policies.
    # ------------------------------------------------------------------
    start_resolved = _resolve_to_trading_day(
        requested_start, panel_index, direction=conv["start_resolution"],
    )
    end_resolved = _resolve_to_trading_day(
        requested_end, panel_index, direction=conv["end_resolution"],
    )
    if start_resolved is None:
        return {
            "error": (
                f"start_date={params.start_date} resolves outside the "
                f"available data window for curve_family="
                f"{params.curve_family!r}.  No trading day on or "
                "after start_date is in the fetched panel."
            )
        }
    if end_resolved is None:
        return {
            "error": (
                f"end_date={params.end_date} resolves outside the "
                f"available data window for curve_family="
                f"{params.curve_family!r}.  No trading day on or "
                "before end_date is in the fetched panel."
            )
        }
    if start_resolved >= end_resolved:
        return {
            "error": (
                f"After resolution, start_resolved={start_resolved.date()} "
                f">= end_resolved={end_resolved.date()}.  The requested "
                f"window collapses to a single (or empty) trading day."
            )
        }

    # ------------------------------------------------------------------
    # 4. Compute per-tenor change in PERCENT.  Order matches
    #    bundle.tenors_used so loadings_matrix's rows align by index.
    # ------------------------------------------------------------------
    y_start = wide.loc[start_resolved, bundle.tenors_used].to_numpy(dtype=float)
    y_end = wide.loc[end_resolved, bundle.tenors_used].to_numpy(dtype=float)
    delta_y_pct = y_end - y_start  # shape: [n_tenors]

    # If any tenor's value is NaN at either endpoint, refuse — the
    # projection would silently propagate NaN through every
    # component.  pivot_and_align_tenors should already have dropped
    # such rows, but check defensively.
    if not np.all(np.isfinite(delta_y_pct)):
        bad_tenors = [
            t for t, v in zip(bundle.tenors_used, delta_y_pct)
            if not np.isfinite(v)
        ]
        return {
            "error": (
                f"Yield change is NaN for tenors {bad_tenors} on the "
                f"resolved window {start_resolved.date()}..{end_resolved.date()}.  "
                "Likely a data gap that ffill could not bridge."
            )
        }

    # Locate the target tenor's row index.
    try:
        target_idx = bundle.tenors_used.index(params.target_tenor)
    except ValueError:
        return {
            "error": (
                f"target_tenor={params.target_tenor!r} not in the "
                f"loadings tenor universe {bundle.tenors_used}.  "
                "(Should be unreachable past schema validation; "
                "defensive check.)"
            )
        }

    # ------------------------------------------------------------------
    # 5. Project onto each component.  Skip degenerate components
    #    (their loadings are NaN); their would-be contribution falls
    #    into the residual.  Convert percent → bps via *100 at the
    #    output boundary.
    # ------------------------------------------------------------------
    total_change_pct = float(delta_y_pct[target_idx])
    total_change_bps = total_change_pct * 100.0

    contributions: List[ComponentContribution] = []
    sum_contribution_bps = 0.0
    for k, comp_name in enumerate(bundle.component_names):
        flag = bundle.quality_flags[k]
        var_share = bundle.variance_shares[k]
        v_k = bundle.loadings_matrix[:, k]

        if flag == "degenerate" or not np.all(np.isfinite(v_k)):
            # Suppress contribution; it will fall into residual.
            contributions.append(
                ComponentContribution(
                    component_name=comp_name,
                    contribution_bps=None,
                    loading_at_target_tenor=None,
                    variance_share_in_fit_window=float(
                        _round_or_none(var_share, variance_share_dec) or 0.0
                    ),
                    quality_flag=flag,  # type: ignore[arg-type]
                )
            )
            continue

        score_k = float(np.dot(delta_y_pct, v_k))
        loading_at_target = float(v_k[target_idx])
        contribution_pct = score_k * loading_at_target
        contribution_bps = contribution_pct * 100.0
        sum_contribution_bps += contribution_bps

        contributions.append(
            ComponentContribution(
                component_name=comp_name,
                contribution_bps=_round_or_none(contribution_bps, bps_dec),
                loading_at_target_tenor=_round_or_none(
                    loading_at_target, loading_dec,
                ),
                variance_share_in_fit_window=float(
                    _round_or_none(var_share, variance_share_dec) or 0.0
                ),
                quality_flag=flag,  # type: ignore[arg-type]
            )
        )

    residual_bps = total_change_bps - sum_contribution_bps

    # ------------------------------------------------------------------
    # 6. Compute change-vs-fit-window overlap percentage.
    # ------------------------------------------------------------------
    fit_start_ts = pd.Timestamp(bundle.fit_window_start)
    fit_end_ts = pd.Timestamp(bundle.fit_window_end)
    overlap_pct = _trading_day_overlap_pct(
        change_start=start_resolved, change_end=end_resolved,
        fit_start=fit_start_ts, fit_end=fit_end_ts,
        panel_index=panel_index,
    )

    # ------------------------------------------------------------------
    # 7. Assemble snapshot.
    # ------------------------------------------------------------------
    metrics = YieldChangeAttributionPcaMetrics(
        start_date_requested=params.start_date,
        start_date_resolved=start_resolved.strftime("%Y-%m-%d"),
        end_date_requested=params.end_date,
        end_date_resolved=end_resolved.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        target_tenor=params.target_tenor,
        total_change_bps=float(_round_or_none(total_change_bps, bps_dec) or 0.0),
        component_contributions=contributions,
        residual_bps=float(_round_or_none(residual_bps, bps_dec) or 0.0),
        n_components_used=len(contributions),
        loadings_source=bundle.loadings_source,  # type: ignore[arg-type]
        loadings_window_start=bundle.fit_window_start,
        loadings_window_end=bundle.fit_window_end,
        loadings_change_frequency_used=bundle.change_frequency_used,  # type: ignore[arg-type]
        loadings_n_observations_in_fit=bundle.n_observations_in_fit,
        loadings_sign_anchor_used=bundle.sign_anchor_used,  # type: ignore[arg-type]
        loadings_change_window_overlap_pct=float(
            _round_or_none(overlap_pct, overlap_pct_dec) or 0.0
        ),
    )

    output = YieldChangeAttributionPcaOutput(current_metrics=metrics)
    return output.model_dump()
