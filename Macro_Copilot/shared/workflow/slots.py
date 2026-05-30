"""shared.workflow.slots — structured slot + output descriptors.

OPR refactor (PART B): the operator registry's per-slot metadata was
previously a plain ``Dict[str, str]`` mapping slot name → an artifact
type name (with a special ``"List[X]"`` string-encoding for fan-in
slots) and a sibling ``accepts_scalar_input: tuple[str, ...]`` tuple on
the ``OperatorSpec``.  That string-encoded shape has accumulated four
ad-hoc concerns:

  - the *artifact type* the slot expects,
  - whether the slot is *list-shaped* (encoded as a ``"List[…]"``
    string prefix the validator + executor have to substring-parse),
  - whether the slot accepts a *scalar literal* in lieu of an
    artifact edge (encoded on a sibling tuple, with the slot name
    duplicated across both data structures),
  - a one-line human description of the slot (currently absent —
    the only documentation lives in the operator's docstring or
    ``config.yaml``).

This module introduces typed descriptors so each concern has a single
declarative field and the validator / executor can read structured
metadata rather than parse string formats.  The artifact-type field is
validated against the closed-family ``ArtifactTypeName`` enum on
construction, so an unknown value trips a clear ``ValidationError``
instead of silently propagating as a free-form string.

The companion ``.of(...)`` / ``.list_of(...)`` classmethods are
ergonomic constructors so registry entries stay readable
(``SlotDescriptor.of("Series", "...")``) without the caller having to
remember every keyword-only flag.
"""

from __future__ import annotations

from typing import Union

from pydantic import BaseModel, ConfigDict, Field

from shared.artifacts.registry import ArtifactTypeName


# Type alias for ergonomics: callers may pass either an enum member
# or its string value to ``SlotDescriptor.of`` / ``OutputDescriptor.of``.
# Pydantic coerces strings into the enum at construction time and
# raises ``ValidationError`` if the value is not a member.
ArtifactTypeLike = Union[ArtifactTypeName, str]


class SlotDescriptor(BaseModel):
    """Structured descriptor for one operator input slot.

    Replaces the prior ``Dict[str, str]`` slot encoding on
    ``OperatorSpec``.  Each instance captures the four concerns the
    string-encoded shape muddled together:

      - ``artifact_type``: the closed-family artifact type the slot
        expects (validated on construction; unknown values raise
        ``ValidationError``).
      - ``description``: a short, slot-specific human description.
      - ``required``: whether the slot must be bound at validate-time.
        Defaults ``True`` (the historical implicit policy).
      - ``is_list``: whether the slot is *list-shaped* — i.e. the
        validator / executor should fan multiple inbound edges in as
        a list of artifacts.  Replaces the prior ``"List[X]"`` string
        prefix.
      - ``accepts_scalar``: whether the slot may be filled by a
        ``LiteralBinding`` scalar in lieu of an artifact edge.
        Replaces the prior ``OperatorSpec.accepts_scalar_input``
        sibling tuple (now per-slot rather than per-operator).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_type: ArtifactTypeName = Field(
        ...,
        description=(
            "The closed-family artifact type the slot expects "
            "(one of ``ArtifactTypeName``)."
        ),
    )
    description: str = Field(
        ...,
        min_length=1,
        description="One-line human description of the slot.",
    )
    required: bool = Field(
        default=True,
        description=(
            "Whether the slot must be bound (via edge or scalar "
            "literal) at validate-time.  Defaults ``True``."
        ),
    )
    is_list: bool = Field(
        default=False,
        description=(
            "Whether the slot is list-shaped — i.e. multiple inbound "
            "edges fan in as a list of artifacts (replaces the prior "
            "``\"List[X]\"`` string-encoded prefix)."
        ),
    )
    accepts_scalar: bool = Field(
        default=False,
        description=(
            "Whether the slot may be filled by a scalar literal in "
            "lieu of an artifact edge (replaces the prior "
            "``OperatorSpec.accepts_scalar_input`` sibling tuple)."
        ),
    )

    @classmethod
    def of(
        cls,
        artifact_type: ArtifactTypeLike,
        description: str,
        *,
        required: bool = True,
        accepts_scalar: bool = False,
    ) -> "SlotDescriptor":
        """Ergonomic constructor for a scalar (non-list) slot.

        ``artifact_type`` may be passed as an ``ArtifactTypeName``
        member OR as its plain string value (``"Series"``,
        ``"SeriesSet"`` etc.) — Pydantic's enum coercion handles
        both at construction.  An unknown string raises
        ``ValidationError`` with a clear message.
        """
        return cls(
            artifact_type=artifact_type,
            description=description,
            required=required,
            is_list=False,
            accepts_scalar=accepts_scalar,
        )

    @classmethod
    def list_of(
        cls,
        artifact_type: ArtifactTypeLike,
        description: str,
        *,
        required: bool = True,
    ) -> "SlotDescriptor":
        """Ergonomic constructor for a *list-shaped* slot (fan-in).

        The validator / executor will collect every inbound edge
        targeting this slot and pass them to the operator as a list
        of artifacts (replacing the prior ``"List[X]"`` string-
        encoded prefix).  ``accepts_scalar`` is intentionally NOT
        exposed for list slots — a list slot is bound by zero-or-
        more edges, not by a scalar literal.
        """
        return cls(
            artifact_type=artifact_type,
            description=description,
            required=required,
            is_list=True,
            accepts_scalar=False,
        )


class OutputDescriptor(BaseModel):
    """Structured descriptor for an operator's single output.

    Replaces the prior bare ``output_type: str`` field on
    ``OperatorSpec``.  The validator + executor only ever need the
    artifact type to route the produced artifact downstream, but a
    short description makes registry entries self-documenting and
    keeps the symmetry with ``SlotDescriptor``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_type: ArtifactTypeName = Field(
        ...,
        description=(
            "The closed-family artifact type the operator emits "
            "(one of ``ArtifactTypeName``)."
        ),
    )
    description: str = Field(
        ...,
        min_length=1,
        description="One-line human description of the output.",
    )

    @classmethod
    def of(
        cls,
        artifact_type: ArtifactTypeLike,
        description: str,
    ) -> "OutputDescriptor":
        """Ergonomic constructor.  See ``SlotDescriptor.of`` for the
        accepted ``artifact_type`` forms (enum member OR plain str)."""
        return cls(
            artifact_type=artifact_type,
            description=description,
        )


__all__ = [
    "SlotDescriptor",
    "OutputDescriptor",
]
