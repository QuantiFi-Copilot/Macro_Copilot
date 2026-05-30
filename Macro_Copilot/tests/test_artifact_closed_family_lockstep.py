"""tests/test_artifact_closed_family_lockstep.py — the closed-family lock-step gate.

ART2 / ART6 (``docs_revamped/02_components/artifact/README.md``) require the
artifact closed family to be declared **once** — the canonical enum
``shared.workflow.registry.ARTIFACT_TYPE_NAMES`` — with every other authority
*derived* from it, plus a lock-step test asserting they all agree.  This is
that test.  ART6 names it explicitly: it "would have caught the v1
``TradeSet``-missing-from-``TerminalArtifact`` drift" (README ART6).  ADR 0016's
migration step (0) is "canonical closed-family enum + meta-test"; this file is
the meta-test.

The test is **membership-only** and intentionally agnostic to *which* types are
in the family: it compares each derived site to whatever ``ARTIFACT_TYPE_NAMES``
declares *today*.  So it keeps enforcing consistency across the pending ADR 0016
step-(6) trade-trio relocation — when ``TradeSet`` leaves the canonical enum,
this test fails until it leaves every derived site too.  That is the lock-step
guarantee: no site may drift from the single source of truth in either
direction.

Derived sites asserted equal to ``set(ARTIFACT_TYPE_NAMES)`` (ART2):

  1. ``state.schemas.ArtifactTypeLiteral``            — persisted-type discriminator
  2. ``shared.workflow.result.TerminalArtifact``      — executor WorkflowResult union
  3. ``shared.workflow.registry._ARTIFACT_TYPE_MAP``  — executor class->name type-map
  4. ``state.artifact_store._ARTIFACT_CLASSES``       — store codec (name, class) map
  5. ``state.artifact_store.Artifact``                — store put/get union

The operator-side direction (every registry ``output_type`` / ``input_slots``
type is a family member) is owned by OPR16 —
``tests/test_operator_registry_consistency.py`` check (d) — and is not
duplicated here.  Note that OPR16 alone does *not* catch the drift this file
guards: ``construct_trades`` declares ``output_type='TradeSet'`` which *is* a
member of ``ARTIFACT_TYPE_NAMES``, so OPR16 stays green while a derived site
silently omits ``TradeSet``.  The derived-site equality below is what bites.
"""

from __future__ import annotations

import typing

from shared.workflow.registry import ARTIFACT_TYPE_NAMES, _ARTIFACT_TYPE_MAP
from shared.workflow.result import TerminalArtifact
from state.artifact_store import Artifact, _ARTIFACT_CLASSES
from state.schemas import ArtifactTypeLiteral


# The single source of truth.  Every assertion below compares a derived
# site's member-name set to this.
CANONICAL = frozenset(ARTIFACT_TYPE_NAMES)


def _union_member_names(union_type: object) -> set:
    """Set of class ``__name__``s for a ``Union[...]`` of artifact classes."""
    return {member.__name__ for member in typing.get_args(union_type)}


def test_canonical_enum_well_formed() -> None:
    """The single source of truth is non-empty and duplicate-free — otherwise
    the ``set(...)`` comparisons below could mask a real drift."""
    assert ARTIFACT_TYPE_NAMES, "ARTIFACT_TYPE_NAMES must not be empty"
    assert len(ARTIFACT_TYPE_NAMES) == len(CANONICAL), (
        f"ARTIFACT_TYPE_NAMES contains duplicates: {ARTIFACT_TYPE_NAMES}"
    )


def test_discriminator_literal_matches_canonical() -> None:
    """``state.schemas.ArtifactTypeLiteral`` — the persisted artifact-type
    discriminator — must enumerate exactly the canonical family."""
    assert set(typing.get_args(ArtifactTypeLiteral)) == CANONICAL


def test_terminal_artifact_union_matches_canonical() -> None:
    """``shared.workflow.result.TerminalArtifact`` — the executor's
    ``WorkflowResult.terminal_artifact`` union — must enumerate exactly the
    canonical family.

    This is the site that drifted in v1: it omitted ``TradeSet`` though the
    registry's ``construct_trades`` emits one, so any workflow terminating on
    ``construct_trades`` raised a pydantic ``ValidationError`` when the executor
    built the result.  This assertion is the regression guard.
    """
    assert _union_member_names(TerminalArtifact) == CANONICAL


def test_executor_type_map_matches_canonical() -> None:
    """``shared.workflow.registry._ARTIFACT_TYPE_MAP`` — the class->name map
    behind ``artifact_type_name`` — must cover exactly the canonical family and
    be internally self-consistent (each class maps to its own name)."""
    assert set(_ARTIFACT_TYPE_MAP.values()) == CANONICAL
    assert {cls.__name__ for cls in _ARTIFACT_TYPE_MAP} == CANONICAL
    for cls, name in _ARTIFACT_TYPE_MAP.items():
        assert cls.__name__ == name, (
            f"_ARTIFACT_TYPE_MAP maps {cls.__name__} -> {name!r} (must be self-naming)"
        )


def test_store_codec_classes_match_canonical() -> None:
    """``state.artifact_store._ARTIFACT_CLASSES`` — the codec's ordered
    ``(name, class)`` tuple — must enumerate exactly the canonical family, with
    each declared name equal to its class ``__name__``."""
    assert {name for name, _cls in _ARTIFACT_CLASSES} == CANONICAL
    assert {cls.__name__ for _name, cls in _ARTIFACT_CLASSES} == CANONICAL
    for name, cls in _ARTIFACT_CLASSES:
        assert name == cls.__name__, (
            f"_ARTIFACT_CLASSES pairs {name!r} with class {cls.__name__} (names must match)"
        )


def test_store_artifact_union_matches_canonical() -> None:
    """``state.artifact_store.Artifact`` — the union ``put_artifact`` accepts
    and ``get_artifact`` returns — must enumerate exactly the canonical
    family."""
    assert _union_member_names(Artifact) == CANONICAL
