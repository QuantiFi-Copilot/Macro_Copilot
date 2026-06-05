"""shared.workflow.answer_shape — the SOFT output-shape contract (plan D2).

The orchestration-upgrade plan (``tmp/prompt_tests/orchestration_upgrade_plan.md``
Decision D2) introduces an *expected answer shape* the L1 router declares and the
deterministic verifier (Boundary A, ``shared.workflow.validate``) enforces against a
DAG's terminal artifact type.  This module owns the closed vocabulary + the
shape→artifact-type mapping.  Nothing here reasons over the user's words (ruling 1):
it is a pure structural mapping from a small closed enum to the artifact closed family.

Design (plan D2 — SOFT, to preserve flexibility, plan P4)
---------------------------------------------------------
- The contract is a SET of acceptable shapes, never exactly one.  An ambiguous
  question gets a multi-element set (e.g. {SCALAR, SERIES}); a genuinely open-ended
  question gets ``ANY`` (unconstrained → the check is skipped).
- The check passes iff the terminal's artifact type is in the UNION of acceptable
  artifact types across the contract set.  So the verifier rejects only CLEAR
  contradictions ("asked for one number, produced a 500-point series"), never novel
  paths — flexibility lives in the path, which the contract never constrains.

This file deliberately has only one dependency (``ArtifactTypeName``) so both the
orchestrator (L1 router) and the workflow substrate (validator) can import it with no
risk of an import cycle.
"""

from __future__ import annotations

from enum import Enum
from typing import Collection, FrozenSet

from shared.artifacts.registry import ArtifactTypeName


class AnswerShape(str, Enum):
    """Closed family of expected answer shapes the L1 router may declare.

    ``str``-valued so it serializes cleanly in the router's structured output and in
    persisted lineage.  ``ANY`` means "unconstrained" — the deterministic shape check
    is skipped (the question is genuinely open-ended; flexibility wins).

    Extending this enum is a closed-family change (mirror ``ArtifactTypeName``
    discipline): add the member here AND its acceptable-type set in
    ``_ACCEPTABLE`` below, AND a lockstep assertion in the tests.
    """

    SCALAR = "scalar"
    SERIES = "series"
    SERIES_SET = "series_set"
    EVENT_SET = "event_set"
    PANEL = "panel"
    ANY = "any"


# Shape → the artifact types that satisfy it.  PANEL accepts BOTH Panel and
# WindowedPanel (a per-event panel is still a panel-shaped answer).  ANY is handled
# specially (returns the whole family) so callers never special-case it twice.
_ACCEPTABLE: dict[AnswerShape, FrozenSet[ArtifactTypeName]] = {
    AnswerShape.SCALAR: frozenset({ArtifactTypeName.SCALAR_METRIC}),
    AnswerShape.SERIES: frozenset({ArtifactTypeName.SERIES}),
    AnswerShape.SERIES_SET: frozenset({ArtifactTypeName.SERIES_SET}),
    AnswerShape.EVENT_SET: frozenset({ArtifactTypeName.EVENT_SET}),
    AnswerShape.PANEL: frozenset(
        {ArtifactTypeName.PANEL, ArtifactTypeName.WINDOWED_PANEL}
    ),
    AnswerShape.ANY: frozenset(ArtifactTypeName),  # all — unconstrained
}


def is_unconstrained(shapes: Collection[AnswerShape]) -> bool:
    """True when the contract imposes no constraint: empty, or contains ``ANY``.

    The verifier skips the terminal-shape check in this case — the question is
    open-ended and any terminal artifact type is acceptable (plan D2/P4)."""
    if not shapes:
        return True
    return AnswerShape.ANY in set(shapes)


def acceptable_artifact_types(
    shapes: Collection[AnswerShape],
) -> FrozenSet[ArtifactTypeName]:
    """Union of the artifact types acceptable under ANY shape in the contract.

    Returns the whole artifact family when the contract is unconstrained (empty or
    containing ``ANY``) — so a caller can always do a plain membership test."""
    if is_unconstrained(shapes):
        return frozenset(ArtifactTypeName)
    acc: set[ArtifactTypeName] = set()
    for s in shapes:
        acc |= _ACCEPTABLE[s]
    return frozenset(acc)


def shape_contract_satisfied(
    shapes: Collection[AnswerShape], artifact_type_name: str
) -> bool:
    """True iff ``artifact_type_name`` (a closed-family artifact type *value*,
    e.g. ``"ScalarMetric"``) satisfies the contract.

    Unconstrained contracts are always satisfied.  Compares by string value because
    ``ArtifactTypeName`` is ``str``-valued and call sites carry the type as a plain
    string (the validator derives ``"Series"`` / ``spec.output.artifact_type.value``)."""
    if is_unconstrained(shapes):
        return True
    acceptable_values = {t.value for t in acceptable_artifact_types(shapes)}
    return artifact_type_name in acceptable_values


def parse_answer_shapes(raw: Collection[str]) -> FrozenSet[AnswerShape]:
    """Parse a collection of raw shape strings (e.g. from the L1 router's structured
    output) into a frozenset of ``AnswerShape``.  Unknown strings are dropped (the
    router is advisory — an unparseable shape degrades to no-constraint, never an
    error).  An empty / all-unknown input yields ``{ANY}`` (unconstrained)."""
    out: set[AnswerShape] = set()
    valid = {s.value: s for s in AnswerShape}
    for r in raw:
        member = valid.get(str(r).strip().lower())
        if member is not None:
            out.add(member)
    if not out:
        return frozenset({AnswerShape.ANY})
    return frozenset(out)


__all__ = [
    "AnswerShape",
    "is_unconstrained",
    "acceptable_artifact_types",
    "shape_contract_satisfied",
    "parse_answer_shapes",
]
