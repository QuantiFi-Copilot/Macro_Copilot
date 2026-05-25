"""api.routes.workflows.catalogue — read-only catalogue endpoints.

Surface
-------
- ``GET /workflows``                  → list of TemplateCards
- ``GET /workflows/{template_id}``    → one TemplateCard
- ``GET /tools``                      → list of ToolCards
- ``GET /tools/{tool_name}``          → one ToolCard

Discipline
----------
- Catalogue is sourced from the IN-PROCESS registries (no separate
  state, no extra config files).  Workflows come from
  ``shared.workflow.list_templates()``; tools come from
  ``rates_agent.workflows.rates_primitive_resolver`` + each
  primitive's bundled ``config.yaml`` (the same config the backend
  compute layer loads).

- Tool catalogue ``conventions`` are passed through as the structured
  ``OperatorConfig.conventions`` map (each entry: value, source,
  rationale, optional valid_range / valid_values).  The frontend
  renders these read-only — knob editing is not implemented in this
  PR (the tool cards' "configurations" surface is a preview of the
  forthcoming editor, not a live one).

Side-effect on import
---------------------
This module imports the registered template packages so the
substrate's process-wide template registry is populated by the time
``GET /workflows`` is called.  Same discipline as
``rates_agent.workflows.mcp_server``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# Side-effect import — populates the substrate's template registry.
import rates_agent.workflows.cross_sectional_screen  # noqa: F401
import rates_agent.workflows.event_study  # noqa: F401
import rates_agent.workflows.regime_conditioned_relationship  # noqa: F401

from rates_agent.workflows import (
    known_rates_primitives,
    rates_primitive_resolver,
)
from rates_agent.workflows._runner import (
    describe_workflow_card,
    list_workflow_cards,
)
from shared.config import load_tool_config
from shared.workflow import known_template_ids

logger = logging.getLogger("api.routes.workflows.catalogue")

router = APIRouter()


# ===========================================================================
# WORKFLOW CATALOGUE
# ===========================================================================


class WorkflowCatalogueResponse(BaseModel):
    """Response shape for ``GET /workflows``.

    Each entry is a dict matching the substrate's ``TemplateCard``
    Pydantic dump (``shared.workflow.template_card``).  The wire shape
    is intentionally loose — the substrate is the source of truth and
    the frontend is happy consuming the dict directly.
    """

    workflows: List[Dict[str, Any]] = Field(
        ...,
        description=(
            "List of TemplateCards in template_id sort order.  Each "
            "card carries template_id, archetype, description, "
            "slot_schema (typed), terminal_artifact_type, "
            "primitives_used, operators_used, node_count, edge_count, "
            "and archetype_signature cues."
        ),
    )


class WorkflowCardEnvelope(BaseModel):
    """Response shape for ``GET /workflows/{template_id}``."""

    ok: bool
    card: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    known_template_ids: Optional[List[str]] = None


@router.get(
    "/workflows",
    response_model=WorkflowCatalogueResponse,
    summary="List workflow templates",
)
def list_workflows():
    """Return every workflow template registered with the substrate."""
    return WorkflowCatalogueResponse(workflows=list_workflow_cards())


@router.get(
    "/workflows/{template_id}",
    response_model=WorkflowCardEnvelope,
    summary="Describe one workflow template",
)
def describe_workflow(template_id: str):
    """Return the full TemplateCard for one registered template."""
    envelope = describe_workflow_card(template_id)
    if not envelope.get("ok"):
        return WorkflowCardEnvelope(
            ok=False,
            error=envelope.get("error"),
            known_template_ids=envelope.get("known_template_ids"),
        )
    return WorkflowCardEnvelope(ok=True, card=envelope["card"])


# ===========================================================================
# TOOL (PRIMITIVE) CATALOGUE
# ===========================================================================
#
# A primitive ToolCard mirrors the workflow TemplateCard's role but for
# discrete primitives.  It surfaces:
#   - identity (tool_name, domain, category, description)
#   - input shape (parsed from the primitive's *Input Pydantic schema)
#   - output shape (declared output_field_units from PrimitiveSpec —
#     the LLM-facing canonical TimeSeries fields)
#   - methodology + conventions (the bundled config.yaml's structured
#     content; the conventions are the "knobs" the future editor will
#     mutate)
#
# All fields are derived from existing artifacts (Pydantic schemas +
# config.yaml) — no new config surface, no separate catalogue file.


class ToolFieldDescriptor(BaseModel):
    """One field of a primitive's *Input or *Output JSON schema."""

    name: str
    type: str
    required: bool
    default: Any = None
    description: Optional[str] = None
    examples: Optional[List[Any]] = None


class ToolConventionDescriptor(BaseModel):
    """One entry of a primitive's bundled config.yaml ``conventions``
    block — the read-only "knob" surface."""

    name: str
    value: Any
    source: str
    rationale: str
    valid_range: Optional[List[Any]] = None
    valid_values: Optional[List[Any]] = None


class ToolMethodologyDescriptor(BaseModel):
    """Read-only mirror of the bundled config.yaml ``methodology``
    block."""

    what_it_does: str
    assumptions: List[str] = Field(default_factory=list)
    citations: List[str] = Field(default_factory=list)
    planned_extensions: List[str] = Field(default_factory=list)


class ToolCard(BaseModel):
    """Read-only catalogue card for one rates-agent primitive."""

    tool_name: str
    domain: str
    category: Optional[str] = None
    description: str
    input_fields: List[ToolFieldDescriptor]
    output_fields: List[ToolFieldDescriptor]
    methodology: ToolMethodologyDescriptor
    conventions: List[ToolConventionDescriptor]


class ToolCatalogueResponse(BaseModel):
    """Response shape for ``GET /tools``."""

    tools: List[ToolCard]


class ToolCardEnvelope(BaseModel):
    ok: bool
    card: Optional[ToolCard] = None
    error: Optional[str] = None
    known_tool_names: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# Helpers — translate Pydantic / config artifacts into the wire schemas
# above.  The translations are deliberately one-way: catalogue endpoints
# READ from the existing artifacts, never write back.
# ---------------------------------------------------------------------------


def _json_schema_type_label(field_schema: dict) -> str:
    """Map a JSON-schema field's type to a short label for the wire."""
    if "type" in field_schema:
        return str(field_schema["type"])
    any_of = field_schema.get("anyOf")
    if any_of:
        type_tags = [
            t.get("type", "?") for t in any_of if isinstance(t, dict)
        ]
        non_null = [t for t in type_tags if t != "null"]
        if non_null:
            return "|".join(non_null)
    return "any"


def _fields_from_pydantic(
    schema_cls,
) -> List[ToolFieldDescriptor]:
    """Inspect a Pydantic *Input or *Output schema and return its
    fields as ToolFieldDescriptor list."""
    json_schema = schema_cls.model_json_schema()
    properties = json_schema.get("properties") or {}
    required_set = set(json_schema.get("required") or ())
    out: List[ToolFieldDescriptor] = []
    for name, field_schema in properties.items():
        out.append(
            ToolFieldDescriptor(
                name=name,
                type=_json_schema_type_label(field_schema),
                required=name in required_set,
                default=field_schema.get("default"),
                description=field_schema.get("description"),
                examples=field_schema.get("examples"),
            )
        )
    return out


def _methodology_from_config(cfg) -> ToolMethodologyDescriptor:
    """Pull the methodology block off a loaded ToolConfig."""
    meth = cfg.methodology
    return ToolMethodologyDescriptor(
        what_it_does=meth.what_it_does,
        assumptions=list(meth.assumptions or []),
        citations=list(meth.citations or []),
        planned_extensions=list(meth.planned_extensions or []),
    )


def _conventions_from_config(cfg) -> List[ToolConventionDescriptor]:
    """Pull the conventions block off a loaded ToolConfig.  Each entry
    becomes one ToolConventionDescriptor; downstream the frontend renders
    these as the read-only "knobs"."""
    out: List[ToolConventionDescriptor] = []
    for name, conv in (cfg.conventions or {}).items():
        out.append(
            ToolConventionDescriptor(
                name=name,
                value=conv.value,
                source=conv.source,
                rationale=conv.rationale,
                valid_range=getattr(conv, "valid_range", None),
                valid_values=getattr(conv, "valid_values", None),
            )
        )
    return out


def _build_tool_card(tool_name: str) -> ToolCard:
    """Translate a registered rates primitive into a ToolCard.  Pulls:
      - identity from the bundled config.yaml's ``tool`` block
      - input/output shape from PrimitiveSpec.input_class /
        output_class via JSON schema introspection
      - methodology + conventions from the bundled config.yaml
    """
    spec = rates_primitive_resolver(tool_name)
    cfg = load_tool_config(spec.config_path)
    return ToolCard(
        tool_name=tool_name,
        domain=cfg.tool.domain,
        category=getattr(cfg.tool, "category", None),
        description=cfg.tool.description,
        input_fields=_fields_from_pydantic(spec.input_class),
        output_fields=_fields_from_pydantic(spec.output_class),
        methodology=_methodology_from_config(cfg),
        conventions=_conventions_from_config(cfg),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/tools",
    response_model=ToolCatalogueResponse,
    summary="List primitive tools",
)
def list_tools():
    """Return every primitive registered in
    ``rates_agent.workflows.rates_primitive_resolver`` as a ToolCard.

    Sorted by tool_name for deterministic catalogue rendering.
    """
    cards: List[ToolCard] = []
    for name in known_rates_primitives():
        try:
            cards.append(_build_tool_card(name))
        except Exception as exc:
            logger.exception("[tools/%s] catalogue card build failed", name)
            # Surface a placeholder so the frontend lists the tool
            # even when its config can't be parsed.
            cards.append(
                ToolCard(
                    tool_name=name,
                    domain="unknown",
                    description=f"(failed to load card: {exc})",
                    input_fields=[],
                    output_fields=[],
                    methodology=ToolMethodologyDescriptor(
                        what_it_does="(unavailable)"
                    ),
                    conventions=[],
                )
            )
    return ToolCatalogueResponse(tools=cards)


@router.get(
    "/tools/{tool_name}",
    response_model=ToolCardEnvelope,
    summary="Describe one primitive tool",
)
def describe_tool(tool_name: str):
    """Return the full ToolCard for one registered primitive."""
    if tool_name not in known_rates_primitives():
        return ToolCardEnvelope(
            ok=False,
            error=(
                f"tool_name={tool_name!r} is not registered.  "
                "Use GET /tools for the full catalogue."
            ),
            known_tool_names=known_rates_primitives(),
        )
    try:
        card = _build_tool_card(tool_name)
    except Exception as exc:
        logger.exception("[tools/%s] describe failed", tool_name)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to build tool card: {exc}",
        )
    return ToolCardEnvelope(ok=True, card=card)
