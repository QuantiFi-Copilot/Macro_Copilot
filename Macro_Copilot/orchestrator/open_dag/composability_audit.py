"""orchestrator.open_dag.composability_audit — open-DAG primitive composability classifier.

Per plan ``tmp/orchestration.md`` §2.5 (the "honesty check"): the repo
has 58 MCP primitives, but not all of them are currently bridgeable
into the typed Workflow executor.  The executor today bridges primitive
outputs into ``Series`` or ``Panel`` artifacts.  Several
snapshot/scanner primitives intentionally return ranked or
snapshot-shaped objects that do not bridge to those two artifact types.

This module classifies every primitive into one of four buckets:

  - ``BRIDGEABLE_SERIES``   — primitive declares ``output_artifact_type
    == "Series"`` AND ``output_field_units`` carries at least one
    declared field.  L3 can use this primitive as a Series leaf.  A
    declared field may be a top-level ``TimeSeries`` field OR a named
    COMPONENT of a ``List[TimeSeries]`` fit (e.g. PCA's ``pc1`` /
    ``pc2`` / ``pc3`` factor scores) — the Series bridge resolves a
    component ``output_field`` by token-matching the element's
    ``series_name`` (see ``shared.artifacts.adapters.from_time_series.
    _resolve_list_component_series`` and the multi-component contract in
    ``docs_revamped/02_components/primitive/README.md``).  Either way the
    leaf binds a single ``Series``.
  - ``BRIDGEABLE_PANEL``    — primitive declares
    ``output_artifact_type == "Panel"``.  L3 can use this primitive as
    a Panel leaf.  Per the executor's bridge contract, Panel
    primitives don't need a per-field unit map — the panel's payload
    carries its own structure.
  - ``TERMINAL_ONLY_SNAPSHOT`` — primitive declares an artifact type
    (``Series`` today) but ``output_field_units`` is empty.  This is
    the scanner/snapshot pattern (ranked results, current_metrics
    objects) that the existing domain-agent path can still answer but
    the open-DAG executor cannot bridge as a typed leaf.
  - ``UNDECLARED``          — the primitive's metadata is incomplete:
    the artifact type is missing, unknown, or otherwise insufficient
    for classification.

The plan's PR-3 acceptance criterion #4 mandates:
    'every MCP primitive is classified for open-DAG composability;
     terminal-only snapshot/scanner tools are explicitly marked rather
     than silently treated as typed workflow leaves.  UNDECLARED fails
     the PR.'

A test in ``tests/orchestrator/open_dag/test_composability_audit.py``
runs ``audit_resolver`` over the full rates resolver and asserts no
primitive is ``UNDECLARED``.

Domain layering
===============
This module sits in ``orchestrator/open_dag/`` because the audit is a
property of the open-DAG composition layer: the executor itself
doesn't classify primitives, but the open-DAG composer + selector
pipeline needs the classification to honestly refuse "I can't compose
on a terminal-only primitive" rather than failing at runtime.  Per the
plan's "no domain-aware code in shared/workflow/" rule, this audit
lives outside the substrate.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet, Iterable, Optional

from pydantic import BaseModel, ConfigDict

from shared.workflow.registry import (
    ARTIFACT_TYPE_NAMES,
    PrimitiveResolver,
    PrimitiveSpec,
)


# ============================================================================
# CLASSIFICATION ENUM
# ============================================================================


class Composability(str, Enum):
    """Closed family of open-DAG composability buckets.

    Per the plan's §2.5 audit table.  Adding a new value requires an
    ADR; the four current values cover every classifiable primitive
    in the registered universe today.
    """

    BRIDGEABLE_SERIES = "BRIDGEABLE_SERIES"
    BRIDGEABLE_PANEL = "BRIDGEABLE_PANEL"
    TERMINAL_ONLY_SNAPSHOT = "TERMINAL_ONLY_SNAPSHOT"
    UNDECLARED = "UNDECLARED"


# ============================================================================
# AUDIT RESULT RECORD
# ============================================================================


class CompositionAuditEntry(BaseModel):
    """One audit row: the resolver-safe ``tool_name`` plus its
    classification and the reasoning the audit applied.  Frozen."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_name: str
    classification: Composability
    rationale: str


# ============================================================================
# CLASSIFIER
# ============================================================================


_VALID_ARTIFACT_NAMES: FrozenSet[str] = frozenset(ARTIFACT_TYPE_NAMES)


def classify_primitive(spec: PrimitiveSpec) -> CompositionAuditEntry:
    """Classify one ``PrimitiveSpec``.

    Decision rules (in order — first match wins):

      1. ``output_artifact_type`` missing or not in
         ``ARTIFACT_TYPE_NAMES`` → ``UNDECLARED``.
      2. ``output_artifact_type == "Panel"`` → ``BRIDGEABLE_PANEL``.
         (Panel primitives carry their own typed payload via the
         ``tool_output_to_artifact_panel`` bridge; per-field unit
         declarations are not required.)
      3. ``output_artifact_type == "Series"`` AND ``output_field_units``
         non-empty → ``BRIDGEABLE_SERIES``.
      4. ``output_artifact_type == "Series"`` AND ``output_field_units``
         empty → ``TERMINAL_ONLY_SNAPSHOT`` (the scanner/snapshot
         pattern — primitive returns a non-time-series result that the
         existing executor cannot bridge as a typed leaf).
      5. Any other ``output_artifact_type`` (``SeriesSet`` / ``EventSet``
         / ``WindowedPanel`` / ``ScalarMetric``) → ``UNDECLARED``.
         Primitives don't currently emit these artifact types at all;
         if one does, the audit MUST be extended (per the plan's
         closed-family discipline).
    """
    declared = getattr(spec, "output_artifact_type", None)
    if not declared or declared not in _VALID_ARTIFACT_NAMES:
        return CompositionAuditEntry(
            tool_name=spec.tool_name,
            classification=Composability.UNDECLARED,
            rationale=(
                f"output_artifact_type={declared!r} is missing or not "
                f"in ARTIFACT_TYPE_NAMES ({sorted(_VALID_ARTIFACT_NAMES)})."
            ),
        )

    if declared == "Panel":
        return CompositionAuditEntry(
            tool_name=spec.tool_name,
            classification=Composability.BRIDGEABLE_PANEL,
            rationale=(
                "output_artifact_type='Panel'; bridges via "
                "tool_output_to_artifact_panel without per-field "
                "unit declarations."
            ),
        )

    if declared == "Series":
        if spec.output_field_units:
            return CompositionAuditEntry(
                tool_name=spec.tool_name,
                classification=Composability.BRIDGEABLE_SERIES,
                rationale=(
                    "output_artifact_type='Series' with "
                    f"{len(spec.output_field_units)} declared output_field_units; "
                    "bridges via tool_output_to_artifact_series."
                ),
            )
        return CompositionAuditEntry(
            tool_name=spec.tool_name,
            classification=Composability.TERMINAL_ONLY_SNAPSHOT,
            rationale=(
                "output_artifact_type='Series' but output_field_units "
                "is empty.  Snapshot/scanner primitive returning ranked "
                "or current_metrics-shaped output that the open-DAG "
                "executor cannot bridge as a typed Series leaf."
            ),
        )

    # output_artifact_type is one of the other valid enum values
    # (SeriesSet, EventSet, WindowedPanel, ScalarMetric).  No primitive
    # registered today emits these; treat as undeclared so the failure
    # surfaces during the audit, and update this classifier in lock-step.
    return CompositionAuditEntry(
        tool_name=spec.tool_name,
        classification=Composability.UNDECLARED,
        rationale=(
            f"output_artifact_type={declared!r} is a valid artifact "
            "type but no primitive-emission pattern is defined for it "
            "in classify_primitive.  Extend the classifier (and the "
            "bridge) before composing on this primitive."
        ),
    )


# ============================================================================
# RESOLVER-WIDE AUDIT
# ============================================================================


def audit_resolver(
    resolver: PrimitiveResolver,
    tool_names: Iterable[str],
) -> Dict[str, CompositionAuditEntry]:
    """Classify every primitive in ``tool_names`` via the supplied
    resolver.

    Returns
    -------
    dict
        Map from resolver-safe tool_name to its CompositionAuditEntry.

    Notes
    -----
    The caller MUST supply the iterable of resolver-safe tool names —
    the substrate's resolver Protocol does not expose enumeration.
    Today the rates resolver provides a ``known_rates_primitives()``
    helper that returns the full list; pass its result here.
    """
    out: Dict[str, CompositionAuditEntry] = {}
    for name in tool_names:
        spec = resolver(name)
        out[name] = classify_primitive(spec)
    return out


def by_classification(
    audit: Dict[str, CompositionAuditEntry],
    classification: Composability,
) -> Dict[str, CompositionAuditEntry]:
    """Filter an audit result to one classification.  Convenience for
    diagnostic surfaces and tests."""
    return {
        name: entry
        for name, entry in audit.items()
        if entry.classification == classification
    }


__all__ = [
    "Composability",
    "CompositionAuditEntry",
    "classify_primitive",
    "audit_resolver",
    "by_classification",
]
