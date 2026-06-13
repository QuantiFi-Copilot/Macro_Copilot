#!/usr/bin/env python3
"""test_north_star_acceptance.py — the §2 North-Star acceptance demo.

The plan's North-Star: *"Show me, only in the current curve-steepening
regime, the swap-curve points that are richest versus the curve's own PCA
fair value, ranked across the universe."*

Every node below is a Track-A build.  The literal single-DAG phrasing isn't
type-expressible (the shipped reconstruct_from_factors emits ONE tenor's
residual, which cannot be cross-sectionally ranked), so the demo is two
connected, deterministic open-DAG workflows that together cover all three
North-Star properties:

  A — PCA-FAIR-VALUE + REGIME-CONDITIONED:
      build_sovereign_yield_panel  →  reconstruct_from_factors  (the PCA
      fair-value residual of the belly)  →  apply_mask(regime), where the
      regime is curve_spread(2s10s) → threshold_events(above 0) = the
      steepening regime.

  B — PCA-FAIR-VALUE + CROSS-SECTIONALLY-RANKED:
      build_sovereign_yield_panel  →  pca_decompose  →  cross_sectional_rank.

Both VALIDATE through the typed DAG gate and EXECUTE deterministically
against the real DB (rerun → byte-identical terminal lineage).

Read-only.  Standalone, NOT pytest-collected:

    python3 -m tests.eval.test_north_star_acceptance
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from typing import List

from database.database import get_db_engine
from rates_agent.workflows import rates_primitive_resolver
from shared.artifacts.types import Series, SeriesSet
from shared.workflow import (
    OperatorNode,
    PrimitiveNode,
    Workflow,
    WorkflowEdge,
    execute_workflow,
    validate_workflow,
)

_CURVE = "UST"
_LEGS = [
    {"curve_family": _CURVE, "tenor": t} for t in ("2Y", "5Y", "10Y", "30Y")
]


def _panel_node() -> PrimitiveNode:
    start = (date.today() - timedelta(days=3650)).isoformat()
    return PrimitiveNode(
        node_id="panel",
        tool_name="build_sovereign_yield_panel_tool",
        output_field="panel",
        params={"legs": _LEGS, "start_date": start},
    )


def _workflow_a() -> Workflow:
    """PCA fair-value residual of the belly, masked to the steepening regime."""
    return Workflow(
        workflow_id="north_star_pca_residual_regime",
        nodes=[
            _panel_node(),
            OperatorNode(
                node_id="resid", operator_name="reconstruct_from_factors",
                params={"target_column": f"{_CURVE}_5Y", "n_components": 2},
            ),
            PrimitiveNode(
                node_id="slope", tool_name="calculate_curve_spread_tool",
                output_field="time_series_spread",
                params={"curve_family": _CURVE, "short_tenor": "2Y",
                        "long_tenor": "10Y", "lookback_days": 3650},
            ),
            OperatorNode(
                node_id="regime", operator_name="threshold_events",
                params={"rule": "above", "threshold": 0.0},
            ),
            # The Panel-derived residual carries frequency=None; the
            # curve_spread→EventSet carries 'B'.  Opt into mixed-frequency
            # masking explicitly (the dates still intersect cleanly).
            OperatorNode(
                node_id="masked", operator_name="apply_mask",
                params={"require_matching_frequency": False,
                        "require_matching_missingness": False},
            ),
        ],
        edges=[
            WorkflowEdge(source_node_id="panel", target_node_id="resid",
                         target_input_slot="features"),
            WorkflowEdge(source_node_id="slope", target_node_id="regime",
                         target_input_slot="series"),
            WorkflowEdge(source_node_id="resid", target_node_id="masked",
                         target_input_slot="series"),
            WorkflowEdge(source_node_id="regime", target_node_id="masked",
                         target_input_slot="mask"),
        ],
        terminal_node_id="masked",
    )


def _workflow_b() -> Workflow:
    """The curve's PCA factor scores, cross-sectionally ranked.

    panel → pca_decompose → cross_sectional_rank — the SeriesSet a
    pca_decompose emits is consumed by a downstream SeriesSet transformer
    (the fix for the ART9 SeriesSet-lineage gap this demo originally caught;
    see tests/test_seriesset_downstream_lineage.py)."""
    return Workflow(
        workflow_id="north_star_pca_cross_sectional_rank",
        nodes=[
            _panel_node(),
            OperatorNode(node_id="pca", operator_name="pca_decompose",
                         params={"n_components": 2}),
            OperatorNode(node_id="rank", operator_name="cross_sectional_rank",
                         params={"rank_method": "ordinal"}),
        ],
        edges=[
            WorkflowEdge(source_node_id="panel", target_node_id="pca",
                         target_input_slot="features"),
            WorkflowEdge(source_node_id="pca", target_node_id="rank",
                         target_input_slot="series_set"),
        ],
        terminal_node_id="rank",
    )


def _run(engine, wf: Workflow, expected_type) -> List[str]:
    failures: List[str] = []
    try:
        validate_workflow(wf, primitive_resolver=rates_primitive_resolver)
    except Exception as exc:  # noqa: BLE001
        return [f"{wf.workflow_id}: validate_workflow failed — {exc}"]
    try:
        r1 = execute_workflow(wf, engine=engine,
                              primitive_resolver=rates_primitive_resolver)
        r2 = execute_workflow(wf, engine=engine,
                              primitive_resolver=rates_primitive_resolver)
    except Exception as exc:  # noqa: BLE001
        return [f"{wf.workflow_id}: execute_workflow failed — {exc}"]
    t1, t2 = r1.terminal_artifact, r2.terminal_artifact
    if not isinstance(t1, expected_type):
        failures.append(
            f"{wf.workflow_id}: terminal is {type(t1).__name__}, "
            f"expected {expected_type.__name__}")
    if t1.lineage.head_hash != t2.lineage.head_hash:
        failures.append(f"{wf.workflow_id}: not deterministic (head hash differs)")
    return failures


def main(argv=None) -> int:
    engine = get_db_engine()
    cases = [
        ("A: PCA fair-value residual + steepening-regime mask",
         _workflow_a(), Series),
        ("B: PCA factor scores cross-sectionally ranked across the universe",
         _workflow_b(), SeriesSet),
    ]
    all_failures: List[str] = []
    for label, wf, typ in cases:
        fs = _run(engine, wf, typ)
        print(f"[{'PASS' if not fs else 'FAIL'}] {label}")
        for f in fs:
            print(f"    - {f}")
        all_failures += fs
    if all_failures:
        print(f"\n{len(all_failures)} failure(s).")
        return 1
    print("\nNorth-Star composes end-to-end and executes deterministically.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
