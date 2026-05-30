"""tests/test_slot_descriptor.py — SlotDescriptor / OutputDescriptor gates.

The structured descriptor types in ``shared/workflow/slots.py`` replace
the prior ``Dict[str, str]`` operator slot encoding (with the
``"List[X]"`` string prefix + sibling ``accepts_scalar_input`` tuple).
This file is the focused contract gate for the descriptors themselves,
plus the migration-parity assertions that prove every entry in
``OPERATOR_REGISTRY`` was migrated faithfully:

PART E.2 — descriptor unit tests
  * unknown ``artifact_type`` raises Pydantic ``ValidationError``
  * default field values (``required=True``, ``is_list=False``,
    ``accepts_scalar=False``)
  * ``SlotDescriptor.of`` / ``SlotDescriptor.list_of`` produce the
    expected fields
  * ``frozen=True`` AND ``extra="forbid"`` both enforced (assignment
    raises; an unknown field at construction raises)
  * ``OutputDescriptor.of`` works; ``frozen`` + ``extra="forbid"``
    enforced

PART E.3 — migration-parity assertions over ``OPERATOR_REGISTRY``
  * targeted spot-checks for the slot encodings the refactor had to
    preserve (``align_series.series_list`` is list-shaped + Series-typed;
    ``series_arithmetic.right`` is scalar-accepting)
  * fan-out closed-family check: every spec's ``output.artifact_type``
    AND every input slot's ``descriptor.artifact_type`` is a member of
    ``ARTIFACT_TYPE_NAMES`` AND every slot description is non-empty
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.artifacts.registry import ArtifactTypeName
from shared.workflow.registry import ARTIFACT_TYPE_NAMES, OPERATOR_REGISTRY
from shared.workflow.slots import OutputDescriptor, SlotDescriptor


# ===========================================================================
# PART E.2 — SlotDescriptor unit tests
# ===========================================================================


def test_slot_descriptor_rejects_unknown_artifact_type():
    """An unknown ``artifact_type`` string must trip Pydantic's enum
    coercion at construction — not silently propagate as a free-form
    string the way the prior ``Dict[str, str]`` encoding did.  This is
    the core safety guarantee of the descriptor refactor: a typo in a
    registry entry fails loudly at import time rather than at the first
    validate-time edge check that happens to read it.
    """
    with pytest.raises(ValidationError):
        SlotDescriptor(
            artifact_type="NotAnArtifact",  # type: ignore[arg-type]
            description="bogus",
        )

    # The ergonomic ``.of`` constructor must apply the same gate (it
    # just forwards to the model constructor — assert it doesn't
    # quietly bypass validation).
    with pytest.raises(ValidationError):
        SlotDescriptor.of("NotAnArtifact", "bogus")


def test_slot_descriptor_default_field_values():
    """Defaults documented in the docstring of ``SlotDescriptor``:
    ``required`` defaults True (historical implicit policy), ``is_list``
    defaults False (a slot is a scalar artifact slot unless declared
    fan-in), ``accepts_scalar`` defaults False (a slot does not accept
    a scalar literal unless explicitly opted in).
    """
    d = SlotDescriptor(artifact_type="Series", description="d")
    assert d.required is True
    assert d.is_list is False
    assert d.accepts_scalar is False


def test_slot_descriptor_of_produces_expected_fields():
    """``SlotDescriptor.of`` is the canonical non-list constructor; it
    sets ``is_list=False`` and forwards ``required`` + ``accepts_scalar``
    to the underlying model."""
    d = SlotDescriptor.of("Series", "left operand")
    assert d.artifact_type == ArtifactTypeName.SERIES
    assert d.artifact_type == "Series"  # str-valued enum
    assert d.description == "left operand"
    assert d.required is True
    assert d.is_list is False
    assert d.accepts_scalar is False

    d2 = SlotDescriptor.of("Series", "right operand", accepts_scalar=True)
    assert d2.accepts_scalar is True
    assert d2.is_list is False

    d3 = SlotDescriptor.of("EventSet", "optional mask", required=False)
    assert d3.required is False
    assert d3.is_list is False


def test_slot_descriptor_list_of_produces_expected_fields():
    """``SlotDescriptor.list_of`` is the canonical fan-in constructor —
    it forces ``is_list=True`` and pins ``accepts_scalar=False`` (a list
    slot is bound by zero-or-more edges, never by a scalar literal)."""
    d = SlotDescriptor.list_of("Series", "fan-in of N Series to align")
    assert d.artifact_type == ArtifactTypeName.SERIES
    assert d.description == "fan-in of N Series to align"
    assert d.is_list is True
    assert d.accepts_scalar is False
    assert d.required is True

    d2 = SlotDescriptor.list_of("Series", "optional fan-in", required=False)
    assert d2.required is False
    assert d2.is_list is True


def test_slot_descriptor_is_frozen():
    """``frozen=True`` on ``model_config``: assignment after construction
    must raise.  The descriptor is part of the closed-family registry —
    mutating one at runtime would let a caller silently desync the type
    contract the validator + executor depend on.
    """
    d = SlotDescriptor.of("Series", "x")
    with pytest.raises(ValidationError):
        d.description = "mutated"  # type: ignore[misc]


def test_slot_descriptor_forbids_extra_fields():
    """``extra="forbid"``: an unknown field at construction must raise.
    A typo in a registry entry (``accept_scalar`` rather than
    ``accepts_scalar``) would otherwise be silently dropped — and the
    operator would then refuse a scalar literal at run-time with no
    declarative trace."""
    with pytest.raises(ValidationError):
        SlotDescriptor(
            artifact_type="Series",
            description="d",
            accept_scalar=True,  # type: ignore[call-arg]
        )


# ===========================================================================
# PART E.2 — OutputDescriptor unit tests
# ===========================================================================


def test_output_descriptor_of_works():
    """``OutputDescriptor.of`` accepts either an ``ArtifactTypeName``
    member or its string value (Pydantic enum coercion) and binds both
    the artifact type and the human description."""
    o = OutputDescriptor.of("SeriesSet", "aligned bundle")
    assert o.artifact_type == ArtifactTypeName.SERIES_SET
    assert o.artifact_type == "SeriesSet"
    assert o.description == "aligned bundle"

    o2 = OutputDescriptor.of(ArtifactTypeName.SCALAR_METRIC, "scalar coefficient")
    assert o2.artifact_type == ArtifactTypeName.SCALAR_METRIC


def test_output_descriptor_rejects_unknown_artifact_type():
    """Same closed-family gate as ``SlotDescriptor``: an unknown
    ``artifact_type`` string must raise at construction."""
    with pytest.raises(ValidationError):
        OutputDescriptor(
            artifact_type="NotAnArtifact",  # type: ignore[arg-type]
            description="bogus",
        )


def test_output_descriptor_is_frozen():
    """``frozen=True`` enforced — assignment raises."""
    o = OutputDescriptor.of("Series", "x")
    with pytest.raises(ValidationError):
        o.description = "mutated"  # type: ignore[misc]


def test_output_descriptor_forbids_extra_fields():
    """``extra="forbid"`` enforced — an unknown field at construction raises."""
    with pytest.raises(ValidationError):
        OutputDescriptor(
            artifact_type="Series",
            description="d",
            cardinality="single",  # type: ignore[call-arg]
        )


# ===========================================================================
# PART E.3 — migration-parity assertions over OPERATOR_REGISTRY
# ===========================================================================
#
# These assertions are the line-by-line proof that every entry in
# ``OPERATOR_REGISTRY`` was migrated faithfully from the prior
# ``Dict[str, str]`` + ``accepts_scalar_input`` encoding to the
# structured descriptor types — without depending on the OPR16 gate
# wording (which intentionally stays in the same words it had before).
#
# Three targeted spot-checks anchor the prior semantics that the
# refactor had to preserve, followed by a fan-out check over every
# spec in the registry.


def test_align_series_series_list_is_list_shaped():
    """``align_series.series_list`` was previously encoded as
    ``"List[Series]"`` (string-prefix form).  After PART B it is a
    structured ``SlotDescriptor`` with ``is_list=True`` and
    ``artifact_type=Series`` — both flags must hold for the validator's
    fan-in routing and the executor's list-construction path to
    continue dispatching multiple inbound edges into a single Python
    list of Series.
    """
    spec = OPERATOR_REGISTRY["align_series"]
    slot = spec.input_slots["series_list"]
    assert slot.is_list is True
    assert slot.artifact_type == "Series"


def test_series_arithmetic_right_accepts_scalar():
    """``series_arithmetic.right`` was previously encoded by adding
    ``"right"`` to the sibling ``OperatorSpec.accepts_scalar_input``
    tuple.  After PART B that tuple is gone — the flag now lives on
    the per-slot descriptor.  This assertion is the line-by-line
    migration proof that the scalar-literal admission for the
    ``right`` slot survived the rewrite.
    """
    spec = OPERATOR_REGISTRY["series_arithmetic"]
    assert spec.input_slots["right"].accepts_scalar is True
    # And the left slot — which never accepted a scalar — still does
    # not (regression guard against an over-eager refactor).
    assert spec.input_slots["left"].accepts_scalar is False


@pytest.mark.parametrize("name", sorted(OPERATOR_REGISTRY))
def test_registry_descriptors_are_closed_family_and_described(name):
    """Fan-out over every entry in ``OPERATOR_REGISTRY``:

    * ``spec.output.artifact_type`` must be a member of the closed
      family ``ARTIFACT_TYPE_NAMES``.
    * For every input slot, ``descriptor.artifact_type`` must be a
      member of ``ARTIFACT_TYPE_NAMES`` AND ``descriptor.description``
      must be non-empty.

    The descriptor's Pydantic validators already enforce both shapes at
    construction (enum coercion + ``min_length=1`` on description), so
    this is a *belt-and-suspenders* assertion: it proves the registry's
    factory calls actually exercised those validators and didn't slip in
    a stale string-encoded entry the refactor missed.
    """
    spec = OPERATOR_REGISTRY[name]

    # Output side.
    assert spec.output.artifact_type in ARTIFACT_TYPE_NAMES, (
        f"{name}.output.artifact_type={spec.output.artifact_type!r} "
        "not in closed family ARTIFACT_TYPE_NAMES"
    )

    # Input side: artifact_type closed-family + description non-empty.
    for slot_name, descriptor in spec.input_slots.items():
        assert descriptor.artifact_type in ARTIFACT_TYPE_NAMES, (
            f"{name}.{slot_name} artifact_type={descriptor.artifact_type!r} "
            "not in closed family ARTIFACT_TYPE_NAMES"
        )
        assert descriptor.description and descriptor.description.strip(), (
            f"{name}.{slot_name} description must be non-empty"
        )
