"""rates_agent.workflows.attribution_decomposition — V1 attribution template.

Round 3 Stage 2, work item A5.  First template registered under the
``attribution_decomposition`` archetype (reserved in
``WORKFLOW_ARCHETYPES`` since the workflow-template substrate landed,
forward-declared without a canonical V1 template).  This module
loads ``template.yaml`` and registers the resulting
``WorkflowTemplate`` with the substrate's process-wide template
registry on import.

Substrate-realistic V1 scope
----------------------------
The work-order spec for A5 calls for a workflow that composes
``pca_yield_curve`` + ``yield_change_attribution_pca`` +
``series_arithmetic`` as DAG nodes.  That literal composition is
NOT satisfiable in V1 because the workflow-bridge today only
dispatches single-``TimeSeries`` and ``Panel`` primitive outputs
(see ``shared/artifacts/adapters/from_time_series.py:357``):

  - ``pca_yield_curve`` emits ``time_series_factors`` as a
    ``List[TimeSeries]`` — the bridge's
    ``isinstance(ts_obj, TimeSeries)`` check rejects it.
  - ``yield_change_attribution_pca`` emits a snapshot dict
    (``current_metrics``) with no canonical ``TimeSeries`` field —
    same bridge rejection.

The bridge extension that would unblock the canonical PCA-driven
composition is tracked as substrate tech debt (see
``methodology.note`` in ``template.yaml``).  Until it lands, this V1
template closes the archetype slot with a simpler attribution
shape — cross-benchmark subtraction — that exercises the
attribution-decomposition CONCEPT (target = benchmark + residual,
sum-back invariant trivially holds) using bridge-compatible
primitives.  The ``yield_change_attribution_pca`` primitive remains
fully available as a standalone MCP tool for snapshot
PCA-loadings decomposition at a specific date — workflow-level
chaining of its snapshot output is the substrate gap.

V1 topology
-----------
A 6-node DAG.  Two ``get_yield_levels_tool`` primitive nodes (target
+ benchmark), aligned via ``align_series`` and split back into two
Series via ``select_from_series_set``, then differenced via
``series_arithmetic(op=subtract)`` to emit the per-date residual
that represents the target curve's local component (= the part of
the target curve's level that is not explained by the benchmark
anchor's level).

  target_yield ─┐
                ├─ align ─┬─ target_aligned ─┐
  bench_yield  ─┘         └─ bench_aligned  ─┤
                                              └─ residual (terminal)

Same pattern as the cross-calendar alignment branch in the
``event_study`` template — single common idiom for "fetch two
Series + align + extract members + arithmetic" so future workflow
templates inherit the bridge contract honestly.

Public surface
--------------
- ``ATTRIBUTION_DECOMPOSITION_TEMPLATE_PATH`` — absolute Path to
  template.yaml.
- ``load_attribution_decomposition_template()`` — load + return the
  WorkflowTemplate (cached via the substrate's loader).
- ``register()`` — register the template with the substrate's
  registry.  Idempotent (re-registering identical content is a
  no-op).  Auto-invoked on module import.
"""

from __future__ import annotations

from pathlib import Path

from shared.workflow import (
    WorkflowTemplate,
    load_workflow_template,
    register_template,
)


ATTRIBUTION_DECOMPOSITION_TEMPLATE_PATH: Path = (
    Path(__file__).resolve().parent / "template.yaml"
)


def load_attribution_decomposition_template() -> WorkflowTemplate:
    """Load + return the attribution_decomposition template.  Cached
    by absolute path via ``shared.workflow.load_workflow_template``."""
    return load_workflow_template(ATTRIBUTION_DECOMPOSITION_TEMPLATE_PATH)


def register() -> None:
    """Register the attribution_decomposition template with the
    substrate's process-wide registry.  Idempotent (re-registering
    identical content is a no-op)."""
    template = load_attribution_decomposition_template()
    register_template(template)


# Auto-register on import — same pattern as event_study,
# regime_conditioned_relationship, backtest.
register()


__all__ = [
    "ATTRIBUTION_DECOMPOSITION_TEMPLATE_PATH",
    "load_attribution_decomposition_template",
    "register",
]
