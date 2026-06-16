#!/usr/bin/env python3
"""test_north_star_acceptance.py — the §2 North-Star acceptance demo.

The plan's North-Star (§2, line 63):

    "Show me, only in the current curve-steepening regime, the swap-curve
     points that are richest versus the curve's own PCA fair value, ranked
     across the universe."

This file composes that literal screen as ONE typed DAG on the real DB —
all three North-Star properties meet in a single terminal artifact:

  (1) REGIME-CONDITIONED   — the per-tenor residuals are masked to the
      CURRENT contiguous steepening regime (the most-recent unbroken run
      where 2s10s > 0), not the union of every historical steepening date.
  (2) PCA FAIR-VALUE       — each tenor's value is reduced to its PCA
      fair-value residual (observed − the top-k-PC reconstruction of the
      curve), in the tenor's own yield units (PERCENT), via
      reconstruct_from_factors.
  (3) CROSS-SECTIONALLY    — the four masked residuals are aligned into one
      RANKED            SeriesSet and ranked across the tenor universe at
                        each date (richest first).

The literal single-DAG tail (exactly the chain cross_sectional_rank's own
config card labels "the North-Star tail", config.yaml:98-100):

    build_sovereign_yield_panel
      → reconstruct_from_factors × N        (one per tenor: per-tenor residual Series)
      → apply_mask(current-regime)          (each residual masked to the current run)
      → align_series(List[Series]→SeriesSet)(the masked residuals into one SeriesSet)
      → cross_sectional_rank(ascending=True)(rank across the universe — richest first)

align_series' registered List[Series]→SeriesSet fan-in slot
(registry.py:622) binds the N per-tenor residuals; the four edges into
``series_list`` aggregate into the list the executor hands the operator.

DISCLOSURES (P5 — honest disclosure):

  * LOOK-AHEAD (M38).  reconstruct_from_factors fits the PCA loadings over
    the WHOLE panel (``fit_scope='full_sample'``, stamped in lineage), so
    every date's residual uses full-sample loadings.  This is a DESCRIPTIVE
    current-state read of where each tenor sits versus the in-sample PCA
    curve — NOT a point-in-time / look-ahead-free signal and NOT a forecast.
    Disclosed here and in the demo banner.  ``rolling_pca`` is the
    look-ahead-safe sibling (planned).

  * SIGN CONVENTION.  The residual is ``observed − PCA-implied`` in yield
    (PERCENT) space.  A NEGATIVE residual = observed yield BELOW its
    PCA-implied level = RICH; a positive residual = above = cheap.  We rank
    ``ascending=True`` so RANK 1 = the most-negative residual = the RICHEST
    tenor versus the curve's PCA fair value — answering the North-Star's
    "richest … ranked across the universe" literally.  The operator
    discloses the sign; naming rich/cheap is this demo's framing, not the
    finance-blind operator's verdict.

  * SCOPE (§1.2 / P12).  This is a descriptive cross-sectional read of the
    CURRENT state.  No forecast, no buy/sell recommendation.

Both pytest-collected (the §5.5 gate runs ``test_*`` below, DB-guarded) and
runnable standalone against the live DB:

    python3 -m tests.eval.test_north_star_acceptance
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import pytest

from database.database import get_db_engine
from rates_agent.workflows import rates_primitive_resolver
from shared.artifacts.types import EventSet, Series, SeriesSet
from shared.schemas.time_series import TimeSeriesUnits
from shared.workflow import (
    OperatorNode,
    PrimitiveNode,
    Workflow,
    WorkflowEdge,
    WorkflowResult,
    execute_workflow,
    validate_workflow,
)

# ---------------------------------------------------------------------------
# Universe + windows.
# ---------------------------------------------------------------------------
_CURVE = "UST"
_TENORS: Tuple[str, ...] = ("2Y", "5Y", "10Y", "30Y")
_LEGS = [{"curve_family": _CURVE, "tenor": t} for t in _TENORS]
_PANEL_LOOKBACK_DAYS = 3650  # ~10y of curve history for the PCA fit
_N_COMPONENTS = 2  # level + slope — the curve's first two PCs

# Pinned ranked-head fixture (M10).  The ordinal rank (richest=1) of each
# tenor at the most-recent screened date, on the live macro-tsdb panel as of
# the close-out review.  Pins the WHICH-IS-RICHEST ordering — the actual
# North-Star answer — so a regression that reranks the wrong artifact, drops
# the mask, or stubs the residual flips at least one of these and fails.
_EXPECTED_RANKED_HEAD: Dict[str, float] = {
    "pca_residual__UST_10Y": 1.0,  # richest (residual ≈ -0.073 pct)
    "pca_residual__UST_5Y": 2.0,   # residual ≈ -0.035 pct
    "pca_residual__UST_30Y": 3.0,  # residual ≈ +0.053 pct
    "pca_residual__UST_2Y": 4.0,   # cheapest (residual ≈ +0.063 pct)
}


# ===========================================================================
# WORKFLOW CONSTRUCTION — the single composed North-Star DAG.
# ===========================================================================


def _panel_node() -> PrimitiveNode:
    start = (date.today() - timedelta(days=_PANEL_LOOKBACK_DAYS)).isoformat()
    return PrimitiveNode(
        node_id="panel",
        tool_name="build_sovereign_yield_panel_tool",
        output_field="panel",
        params={"legs": _LEGS, "start_date": start},
    )


def _slope_node(node_id: str, lookback_days: int) -> PrimitiveNode:
    """The 2s10s curve-spread leg whose sign defines the steepening regime."""
    return PrimitiveNode(
        node_id=node_id,
        tool_name="calculate_curve_spread_tool",
        output_field="time_series_spread",
        params={
            "curve_family": _CURVE,
            "short_tenor": "2Y",
            "long_tenor": "10Y",
            "lookback_days": lookback_days,
        },
    )


def _current_regime_lookback_days(engine) -> Tuple[int, date, date]:
    """Compute the CURRENT contiguous steepening regime (M39).

    Probe the full 2s10s history, threshold for steepening (spread > 0),
    and walk back from the most-recent True date over the unbroken run.
    Return the calendar lookback from today to that run's START date, plus
    the run's (start, end) for disclosure.  Deterministic: the run
    boundaries are data-driven (stable while the DB is stable); only the
    today-relative lookback shifts with the calendar.

    This is the look-ahead-free part of the regime definition — "are we in
    a steepening regime right now, and since when" is a current-state read,
    not a forecast (§1.2 scope).
    """
    probe = Workflow(
        workflow_id="north_star_current_regime_probe",
        nodes=[
            _slope_node("slope", _PANEL_LOOKBACK_DAYS),
            OperatorNode(
                node_id="regime",
                operator_name="threshold_events",
                params={"rule": "above", "threshold": 0.0},
            ),
        ],
        edges=[
            WorkflowEdge(
                source_node_id="slope",
                target_node_id="regime",
                target_input_slot="series",
            ),
        ],
        terminal_node_id="regime",
    )
    result = execute_workflow(
        probe, engine=engine, primitive_resolver=rates_primitive_resolver
    )
    mask: pd.Series = result.terminal_artifact.mask
    arr = mask.to_numpy(dtype=bool)
    if not arr.any():
        raise RuntimeError(
            "north_star: the 2s10s curve is not steepening on any date in "
            "the probe window — there is no current steepening regime to "
            "screen.  (Relabel the demo to the prevailing regime.)"
        )
    idx = mask.index
    last = int(np.where(arr)[0][-1])
    start = last
    while start - 1 >= 0 and arr[start - 1]:
        start -= 1
    run_start = idx[start].date()
    run_end = idx[last].date()
    # +1 so the window's first displayed date is on/just before the run
    # start; threshold_events then masks the single pre-run date back out
    # (n_true < n_total holds), leaving exactly the current contiguous run.
    lookback = (date.today() - run_start).days + 1
    return lookback, run_start, run_end


def build_north_star_workflow(engine) -> Tuple[Workflow, date, date]:
    """Build the single composed North-Star DAG, returning it with the
    current-regime (start, end) for disclosure.

    Node/edge structure (all three properties in ONE terminal SeriesSet):

        panel ─┬─► resid_2Y ─► masked_2Y ─┐
               ├─► resid_5Y ─► masked_5Y ─┤
               ├─► resid_10Y─► masked_10Y─┼─► align ─► rank   (terminal)
               └─► resid_30Y─► masked_30Y─┘      ▲
        slope ─► regime ──────────────────(mask)─┘
    """
    regime_lookback, run_start, run_end = _current_regime_lookback_days(engine)

    nodes: List = [_panel_node(), _slope_node("slope", regime_lookback)]
    edges: List[WorkflowEdge] = []

    # Regime leg: 2s10s spread (restricted to the current run window) →
    # threshold_events(above 0) → the current-regime boolean EventSet.
    nodes.append(
        OperatorNode(
            node_id="regime",
            operator_name="threshold_events",
            params={"rule": "above", "threshold": 0.0},
        )
    )
    edges.append(
        WorkflowEdge(
            source_node_id="slope",
            target_node_id="regime",
            target_input_slot="series",
        )
    )

    # Per-tenor PCA fair-value residual, then mask each to the regime.
    for tenor in _TENORS:
        rid = f"resid_{tenor}"
        mid = f"masked_{tenor}"
        nodes.append(
            OperatorNode(
                node_id=rid,
                operator_name="reconstruct_from_factors",
                params={
                    "target_column": f"{_CURVE}_{tenor}",
                    "n_components": _N_COMPONENTS,
                },
            )
        )
        edges.append(
            WorkflowEdge(
                source_node_id="panel",
                target_node_id=rid,
                target_input_slot="features",
            )
        )
        nodes.append(
            OperatorNode(
                node_id=mid,
                operator_name="apply_mask",
                params={
                    # The residual rides the panel's business-day calendar;
                    # the regime EventSet rides the 2s10s spread's calendar.
                    # They intersect cleanly; opt into the mixed-frequency
                    # mask explicitly (the residual carries frequency=None).
                    "index_policy": "intersect",
                    "require_matching_frequency": False,
                    "require_matching_missingness": False,
                },
            )
        )
        edges.append(
            WorkflowEdge(
                source_node_id=rid,
                target_node_id=mid,
                target_input_slot="series",
            )
        )
        edges.append(
            WorkflowEdge(
                source_node_id="regime",
                target_node_id=mid,
                target_input_slot="mask",
            )
        )
        # Fan the masked residual into align_series' list slot.  Four edges
        # into ``series_list`` aggregate into List[Series] at execution.
        edges.append(
            WorkflowEdge(
                source_node_id=mid,
                target_node_id="align",
                target_input_slot="series_list",
            )
        )

    # Align the four masked residuals into one SeriesSet (List[Series] →
    # SeriesSet via the registered fan-in slot), then rank across tenors.
    nodes.append(
        OperatorNode(
            node_id="align",
            operator_name="align_series",
            params={
                "join_policy": "outer",
                "fill_policy": "raw",
                "require_matching_frequency": False,
                "require_matching_missingness": False,
            },
        )
    )
    nodes.append(
        OperatorNode(
            node_id="rank",
            operator_name="cross_sectional_rank",
            params={
                "rank_method": "ordinal",
                # ascending=True → rank 1 = most-negative residual = RICHEST
                # versus the curve's PCA fair value (see module docstring).
                "ascending": True,
            },
        )
    )
    edges.append(
        WorkflowEdge(
            source_node_id="align",
            target_node_id="rank",
            target_input_slot="series_set",
        )
    )

    workflow = Workflow(
        workflow_id="north_star_steepening_pca_residual_ranked",
        nodes=nodes,
        edges=edges,
        terminal_node_id="rank",
    )
    return workflow, run_start, run_end


# ===========================================================================
# EXECUTION HELPER (shared by the pytest tests and the standalone runner).
# ===========================================================================


def _ranked_head(result: WorkflowResult, when: pd.Timestamp) -> Dict[str, float]:
    """The ordinal rank of every tenor at ``when`` (the screened date)."""
    terminal: SeriesSet = result.terminal_artifact
    return {
        key: float(terminal.series_by_key[key].loc[when])
        for key in terminal.series_by_key
    }


def run_north_star(engine) -> Dict[str, object]:
    """Build + validate + execute the composed North-Star twice; return the
    artifacts + diagnostics both the pytest assertions and the standalone
    runner read."""
    workflow, run_start, run_end = build_north_star_workflow(engine)

    validate_workflow(workflow, primitive_resolver=rates_primitive_resolver)

    r1 = execute_workflow(
        workflow, engine=engine, primitive_resolver=rates_primitive_resolver
    )
    r2 = execute_workflow(
        workflow, engine=engine, primitive_resolver=rates_primitive_resolver
    )
    screened_date = r1.terminal_artifact.common_index[-1]
    return {
        "workflow": workflow,
        "run_start": run_start,
        "run_end": run_end,
        "result": r1,
        "result_rerun": r2,
        "screened_date": screened_date,
        "head_hash": r1.terminal_artifact.lineage.head_hash,
        "head_hash_rerun": r2.terminal_artifact.lineage.head_hash,
        "ranked_head": _ranked_head(r1, screened_date),
        "ranked_head_rerun": _ranked_head(r2, screened_date),
        "lineage_summary": r1.workflow_lineage_summary,
    }


# ===========================================================================
# PYTEST — the §5.5-collected acceptance (DB-guarded).
# ===========================================================================


def _db_reachable() -> bool:
    try:
        engine = get_db_engine()
        with engine.connect():
            return True
    except Exception:  # noqa: BLE001 — any connection failure = unreachable
        return False


requires_db = pytest.mark.skipif(
    not _db_reachable(),
    reason="macro-tsdb not reachable; the North-Star acceptance demo needs "
    "the live DB (run inside the api-server container).",
)


@pytest.fixture(scope="module")
def north_star() -> Dict[str, object]:
    return run_north_star(get_db_engine())


@requires_db
def test_north_star_composes_one_dag(north_star: Dict[str, object]) -> None:
    """The three North-Star properties meet in ONE terminal artifact: a
    cross-sectionally-ranked SeriesSet of the four per-tenor PCA fair-value
    residuals, masked to the current steepening regime — not two disconnected
    workflows, not the wrong artifact."""
    workflow: Workflow = north_star["workflow"]  # type: ignore[assignment]
    terminal: SeriesSet = north_star["result"].terminal_artifact  # type: ignore[union-attr]

    # ONE DAG whose terminal is the rank of the per-tenor residuals.
    assert workflow.terminal_node_id == "rank"
    assert isinstance(terminal, SeriesSet)
    assert set(terminal.series_by_key) == {
        f"pca_residual__{_CURVE}_{t}" for t in _TENORS
    }, "terminal must rank the per-tenor PCA RESIDUALS across the universe"
    # Cross-section cardinality is the tenor universe (4), not 2 PCA scores.
    assert len(terminal.series_by_key) == len(_TENORS) == 4

    # The DAG actually wires residual → mask → align → rank (property meet).
    summary: str = north_star["lineage_summary"]  # type: ignore[assignment]
    for tenor in _TENORS:
        assert f"resid_{tenor}" in summary
        assert f"masked_{tenor}" in summary
    assert "align" in summary and "rank" in summary


@requires_db
def test_regime_mask_is_current_run_and_subsets(
    north_star: Dict[str, object],
) -> None:
    """Property 1 — REGIME-CONDITIONED, and a REAL mask (M10 + M39).

    The mask genuinely subsets (n_true < n_total — not an all-True no-op),
    and it is the CURRENT contiguous steepening run, not the union of every
    historical steepening date."""
    result: WorkflowResult = north_star["result"]  # type: ignore[assignment]
    regime: EventSet = result.node_artifacts["regime"]
    n_true = int(regime.mask.sum())
    n_total = int(len(regime.mask))

    # M10: a genuine subset — the mask is not an all-True no-op.
    assert 0 < n_true < n_total, (
        f"mask must genuinely subset the dates (got n_true={n_true}, "
        f"n_total={n_total})"
    )

    # M39: the mask is the CURRENT contiguous run.  The pre-fix façade
    # masked ~1981 union dates across ~4 separate runs spanning a decade;
    # the current run is a single sub-3-year window.  Assert the masked
    # window is contiguous from run_start to the most-recent date.
    masked = result.node_artifacts["masked_5Y"]
    kept = masked.payload.index
    run_start = north_star["run_start"]
    run_end = north_star["run_end"]
    assert kept[0].date() >= run_start, (
        "masked residual must start on/after the current regime's start"
    )
    assert kept[-1].date() == run_end, (
        "masked residual must end on the most-recent steepening date"
    )
    # The current run is bounded — far smaller than the ~10y panel.  Guards
    # against the union-of-all-history regression (which would keep ~1981
    # dates over the full window).
    assert n_total < _PANEL_LOOKBACK_DAYS // 3, (
        f"the current-regime window ({n_total} dates) must be the recent "
        "run, not the full-history union"
    )


@requires_db
def test_residual_is_pca_fair_value_in_yield_units(
    north_star: Dict[str, object],
) -> None:
    """Property 2 — PCA FAIR-VALUE residual, real (M10).

    Each masked member is a PCA fair-value residual in YIELD units
    (PERCENT) — not a z-score, not a COUNT/rank, not a constant stub — with
    genuine cross-date variation and a curve-scale magnitude."""
    result: WorkflowResult = north_star["result"]  # type: ignore[assignment]
    for tenor in _TENORS:
        masked = result.node_artifacts[f"masked_{tenor}"]
        # Units: the residual is in the target column's yield units.
        assert masked.units == TimeSeriesUnits.PERCENT, (
            f"masked_{tenor} units must be PERCENT (yield), not "
            f"{masked.units} — a residual in z-score/COUNT units would be "
            "the wrong artifact"
        )
        vals = masked.payload.to_numpy(dtype=float)
        finite = vals[np.isfinite(vals)]
        assert finite.size >= 2, f"masked_{tenor} has too few finite points"

        # Not a constant stub: the residual genuinely varies across dates.
        assert float(np.nanstd(finite)) > 1e-6, (
            f"masked_{tenor} residual is (near-)constant — a stub, not a "
            "real PCA fair-value residual"
        )
        # Curve-scale magnitude: a 2-PC reconstruction residual of UST
        # yields sits in fractions of a percent, well under 5 pct.  A
        # z-score stub (~unit sd, |z| often >1) or a raw-yield leak (~3-5
        # pct level) would blow past this.
        assert float(np.nanmax(np.abs(finite))) < 5.0, (
            f"masked_{tenor} residual magnitude {np.nanmax(np.abs(finite))} "
            "is too large for a yield-space PCA residual"
        )

    # Sanity on the lineage disclosure: the residual step records the
    # full-sample look-ahead (M38) so the disclosure cannot silently drop.
    resid_5y = result.node_artifacts["resid_5Y"]
    steps = [s for s in resid_5y.lineage.steps
             if getattr(s, "name", None) == "reconstruct_from_factors"]
    assert steps, "expected a reconstruct_from_factors step in lineage"
    assert steps[-1].params.get("fit_scope") == "full_sample", (
        "the full-sample look-ahead must stay disclosed in lineage (M38)"
    )


@requires_db
def test_ranked_head_pinned_and_richest_first(
    north_star: Dict[str, object],
) -> None:
    """Property 3 — CROSS-SECTIONALLY RANKED, pinned (M10).

    The ranked head at the screened date matches the stored fixture, and the
    ranking is richest-first: rank 1 has the most-negative residual."""
    result: WorkflowResult = north_star["result"]  # type: ignore[assignment]
    ranked_head: Dict[str, float] = north_star["ranked_head"]  # type: ignore[assignment]
    screened_date = north_star["screened_date"]

    # Pinned ranked-head fixture — the actual North-Star answer (which tenor
    # is richest right now).  A regression that reranks the wrong artifact,
    # drops the mask, or stubs the residual flips at least one rank.
    assert ranked_head == _EXPECTED_RANKED_HEAD, (
        f"ranked head at {pd.Timestamp(screened_date).date()} drifted from "
        f"the pinned fixture: got {ranked_head}, expected "
        f"{_EXPECTED_RANKED_HEAD}"
    )

    # Ranks are a full permutation 1..N of the universe (a real ordinal rank
    # across 4 tenors, not the cardinality-2 {1,2} of the old factor-score
    # façade).
    assert sorted(ranked_head.values()) == [1.0, 2.0, 3.0, 4.0]

    # Richest-first invariant: the rank-1 tenor has the most-negative
    # residual at the screened date (richest vs PCA fair value).
    residual_at_date = {
        f"pca_residual__{_CURVE}_{t}": float(
            result.node_artifacts[f"masked_{t}"].payload.loc[screened_date]
        )
        for t in _TENORS
    }
    rank1_key = min(ranked_head, key=ranked_head.get)  # the rank-1 member
    assert residual_at_date[rank1_key] == min(residual_at_date.values()), (
        "rank 1 must be the most-negative (richest) residual"
    )


@requires_db
def test_deterministic_rerun(north_star: Dict[str, object]) -> None:
    """Determinism (OPR14 / P4): a byte-identical rerun — same terminal head
    hash AND same ranked head."""
    assert north_star["head_hash"] == north_star["head_hash_rerun"], (
        "terminal head hash differs across reruns"
    )
    assert north_star["ranked_head"] == north_star["ranked_head_rerun"], (
        "ranked head differs across reruns"
    )


# ===========================================================================
# STANDALONE RUNNER (live-DB acceptance path).
# ===========================================================================


def main(argv=None) -> int:
    engine = get_db_engine()
    diag = run_north_star(engine)

    terminal: SeriesSet = diag["result"].terminal_artifact  # type: ignore[union-attr]
    regime: EventSet = diag["result"].node_artifacts["regime"]  # type: ignore[union-attr]
    n_true = int(regime.mask.sum())
    n_total = int(len(regime.mask))
    screened = pd.Timestamp(diag["screened_date"]).date()

    print("§2 NORTH-STAR — one composed DAG on the real DB")
    print(f"  DB                : {engine.url}")
    print(f"  workflow          : {diag['workflow'].workflow_id}")
    print(f"  lineage           : {diag['lineage_summary']}")
    print(
        f"  (1) regime        : current contiguous steepening run "
        f"{diag['run_start']} → {diag['run_end']}  "
        f"(mask n_true={n_true} < n_total={n_total})"
    )
    print(
        "  (2) PCA fair-value: per-tenor reconstruct_from_factors residual "
        f"(PERCENT units, n_components={_N_COMPONENTS}, "
        "fit_scope='full_sample' — full-sample look-ahead disclosed)"
    )
    print(
        "  (3) ranked        : cross_sectional_rank across "
        f"{sorted(terminal.series_by_key)} (ascending=True → rank 1 = richest)"
    )
    print(f"  screened date     : {screened}")
    print("  ranked head (rank 1 = richest vs PCA fair value):")
    resid_at_date = {
        t: float(diag["result"].node_artifacts[f"masked_{t}"].payload.loc[diag["screened_date"]])  # type: ignore[union-attr]
        for t in _TENORS
    }
    for key, rank in sorted(diag["ranked_head"].items(), key=lambda kv: kv[1]):  # type: ignore[union-attr]
        tenor = key.split("_")[-1]
        print(f"      rank {rank:.0f}: {key}  residual={resid_at_date[tenor]:+.4f} pct")
    print(
        f"  determinism       : head_hash {'MATCH' if diag['head_hash'] == diag['head_hash_rerun'] else 'DIFFERS'} "
        f"({diag['head_hash'][:16]}…)"
    )

    ok = (
        isinstance(terminal, SeriesSet)
        and set(terminal.series_by_key) == {f"pca_residual__{_CURVE}_{t}" for t in _TENORS}
        and 0 < n_true < n_total
        and diag["head_hash"] == diag["head_hash_rerun"]
        and diag["ranked_head"] == diag["ranked_head_rerun"]
    )
    if ok:
        print(
            "\nNorth-Star composes end-to-end: regime-conditioned (current "
            "steepening run) ∧ PCA fair-value residual ∧ cross-sectionally "
            "ranked, in ONE deterministic DAG on the real DB."
        )
        return 0
    print("\nNorth-Star composition FAILED an acceptance check.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
