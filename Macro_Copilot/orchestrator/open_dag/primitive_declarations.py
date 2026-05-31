"""orchestrator.open_dag.primitive_declarations — typed primitive metadata snapshot.

Replaces the thin ``declare_primitive_output_type`` helper from the
PR-3 first draft.  The plan (``tmp/orchestration.md`` §PR-3) asked for a
richer ``PrimitiveDeclaration`` carrying the metadata an L2 Selector
needs to populate every closed-substrate declared_* field on a
``BoundLeaf`` without redoing resolver lookups elsewhere.

Two surfaces
============

  - ``PrimitiveDeclaration``: frozen Pydantic carrying ``tool_name``,
    ``output_artifact_type``, ``output_field_units`` (the bridge's
    ``time_series*`` → unit map), and ``available_output_fields``
    (the keys of ``output_field_units``, exposed as a tuple for
    selector-side enumeration).  Empty maps are preserved verbatim so
    callers can distinguish "primitive declares no units" from
    "primitive declares some units but missing the one we asked for".

  - ``declare_primitive_output(resolver, tool_name) -> PrimitiveDeclaration``:
    calls the supplied resolver, reads the static ``PrimitiveSpec``
    fields, and returns the frozen snapshot.  NO callable is invoked,
    NO DB engine is constructed.  Re-raises ``KeyError`` from the
    resolver when ``tool_name`` is unknown.

Why a module-level helper rather than a method on ``PrimitiveResolver``
=======================================================================

The Protocol is a single-callable contract.  Today's agent-side
implementation (``rates_agent.workflows.rates_primitive_resolver``)
is a plain function; the substrate's test suite uses
function/lambda-shaped resolvers throughout.  Adding a method would
break every existing call site for zero architectural benefit.  The
helper achieves the SAME goal — static declaration without execution
— with zero back-compat cost.

Domain layering
===============

This module sits in ``orchestrator/open_dag/`` because the
``PrimitiveDeclaration`` is what an L2 Selector consults while binding
a hole — that is, while making a domain-aware choice.  The substrate
beneath (``shared/workflow/``) stays finance-blind: it carries the
``PrimitiveResolver`` Protocol + ``PrimitiveSpec`` schema, but does
not know about declarations or the open-DAG composability story.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from shared.workflow.registry import PrimitiveResolver


class PrimitiveDeclaration(BaseModel):
    """Frozen snapshot of a primitive's declared output metadata.

    Returned by ``declare_primitive_output`` so an L2 Selector can
    populate every ``BoundLeaf.declared_*`` field that the Assembler's
    contract check (PR-4 Boundary A) hard-validates against the
    LeafRequest:

      - ``declared_output_artifact_type`` ← ``output_artifact_type``
      - ``declared_units`` ← ``output_field_units[output_field]``
      - ``declared_output_meaning`` / ``declared_semantic_role``
        the Selector authors itself (the LLM authors the
        free-form description; this object provides the structured
        anchors).

    Carries the resolver-safe ``tool_name`` so the caller can pair the
    declaration back with the binding without re-querying the resolver.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_name: str = Field(..., min_length=1)
    output_artifact_type: str = Field(
        ...,
        min_length=1,
        description=(
            "One of ``shared.workflow.ARTIFACT_TYPE_NAMES`` values "
            "(``Series``, ``SeriesSet``, ``EventSet``, ``Panel``, "
            "``WindowedPanel``, ``ScalarMetric``).  Substrate-level "
            "closed family — never an arbitrary string."
        ),
    )
    output_field_units: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Verbatim copy of ``PrimitiveSpec.output_field_units`` — "
            "the bridge's per-field unit map (e.g. "
            "{'time_series': 'bps', 'time_series_spread': 'bps'}).  "
            "Empty dict means the primitive declares no per-field "
            "units (typical of scanner/snapshot tools)."
        ),
    )
    available_output_fields: Tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            "Tuple of the ``output_field_units`` keys, exposed for "
            "selector-side enumeration when the LLM needs to pick a "
            "specific output_field for the BoundLeaf.  Empty when the "
            "underlying map is empty."
        ),
    )


def declare_primitive_output(
    resolver: PrimitiveResolver,
    tool_name: str,
    params: Optional[Dict[str, object]] = None,
) -> PrimitiveDeclaration:
    """Return a ``PrimitiveDeclaration`` for the given resolver-safe
    tool name, WITHOUT invoking the primitive callable.

    Parameters
    ----------
    resolver :
        The agent-side ``PrimitiveResolver`` (e.g.
        ``rates_agent.workflows.rates_primitive_resolver``).
    tool_name :
        The resolver-safe tool key (NOT the visible MCP tool name).
        For domains following the prefixed convention (today only
        ``policy_futures``), pass the prefixed key — derive via
        ``orchestrator.open_dag.resolver_keys.domain_to_resolver_key``.
    params :
        Reserved for primitives whose declaration may depend on
        params (none today; a forward-compatibility seam).  Currently
        ignored.

    Returns
    -------
    PrimitiveDeclaration
        Frozen snapshot of the primitive's metadata.

    Raises
    ------
    KeyError
        Propagated from the resolver when ``tool_name`` is unknown.
    """
    spec = resolver(tool_name)
    field_units = dict(spec.output_field_units)
    return PrimitiveDeclaration(
        tool_name=tool_name,
        output_artifact_type=spec.output_artifact_type,
        output_field_units=field_units,
        available_output_fields=tuple(sorted(field_units.keys())),
    )


__all__ = [
    "PrimitiveDeclaration",
    "declare_primitive_output",
]
