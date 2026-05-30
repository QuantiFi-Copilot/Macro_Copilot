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

from shared.artifacts.registry import ARTIFACT_CLASS_TO_NAME, ArtifactTypeName
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


def test_artifact_type_name_enum_is_single_source_of_truth() -> None:
    """``shared.artifacts.registry.ArtifactTypeName`` is the ONE canonical
    enum; every other artifact-name surface (``ARTIFACT_TYPE_NAMES``,
    ``_ARTIFACT_TYPE_MAP``, ``ARTIFACT_CLASS_TO_NAME``) must derive from it.

    This is the meta-lockstep: it proves the SOURCE itself is consistent
    with its DERIVED views (and that the underlying wrapper classes
    self-name as the enum claims), so the cross-site assertions below
    have a trustworthy anchor.
    """
    # The enum members' string values are exactly the canonical name set.
    enum_values = {member.value for member in ArtifactTypeName}
    assert enum_values == CANONICAL, (
        f"ArtifactTypeName members {sorted(enum_values)} differ from "
        f"ARTIFACT_TYPE_NAMES {sorted(CANONICAL)} — the derived tuple "
        "must enumerate exactly the canonical enum."
    )

    # ARTIFACT_TYPE_NAMES is a faithful tuple-view of the enum (same order).
    assert ARTIFACT_TYPE_NAMES == tuple(m.value for m in ArtifactTypeName), (
        "ARTIFACT_TYPE_NAMES must equal tuple(m.value for m in "
        "ArtifactTypeName) — order included."
    )

    # ARTIFACT_CLASS_TO_NAME covers exactly the enum and is self-naming:
    # each wrapper class's __name__ equals the enum member's value.
    assert set(ARTIFACT_CLASS_TO_NAME.values()) == set(ArtifactTypeName), (
        "ARTIFACT_CLASS_TO_NAME values must enumerate every "
        "ArtifactTypeName member exactly once."
    )
    for cls, member in ARTIFACT_CLASS_TO_NAME.items():
        assert cls.__name__ == member.value, (
            f"ARTIFACT_CLASS_TO_NAME maps {cls.__name__} -> "
            f"{member.value!r} (wrapper class must self-name as its enum "
            "member's value)."
        )

    # _ARTIFACT_TYPE_MAP is the str-valued projection of the canonical
    # class -> enum mapping; the two must agree key-by-key.
    assert set(_ARTIFACT_TYPE_MAP) == set(ARTIFACT_CLASS_TO_NAME), (
        "_ARTIFACT_TYPE_MAP keys must equal ARTIFACT_CLASS_TO_NAME keys."
    )
    for cls, name_str in _ARTIFACT_TYPE_MAP.items():
        assert name_str == ARTIFACT_CLASS_TO_NAME[cls].value, (
            f"_ARTIFACT_TYPE_MAP[{cls.__name__}]={name_str!r} disagrees "
            f"with ARTIFACT_CLASS_TO_NAME[{cls.__name__}]="
            f"{ARTIFACT_CLASS_TO_NAME[cls].value!r}."
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
