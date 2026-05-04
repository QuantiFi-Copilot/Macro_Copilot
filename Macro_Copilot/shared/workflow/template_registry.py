"""shared.workflow.template_registry — open-catalogue template registry.

The substrate is finance-blind, so the template REGISTRY is also
finance-blind: it does not import the rates_agent templates
directly.  Instead, agent layers (rates_agent today, FX/credit
tomorrow) call ``register_template`` to add their templates into
the catalogue.

Closed-family discipline at the archetype level
-----------------------------------------------
- ``WorkflowArchetype`` is closed (4 entries in V1 — see
  ``shared.workflow.template``).
- The TEMPLATE catalogue is OPEN: any number of templates per
  archetype can be registered (V1 plan ships exactly 1 per
  archetype, but the registry doesn't enforce that — multiple
  variants per archetype can be added in V2 without architecture
  changes).

Why an open catalogue
---------------------
Closed at the archetype level (closed-family discipline) +
open at the template level (extensibility).  This mirrors the
operator architecture's "closed type, open dispatch" split:
operator KINDS are closed (operator vs primitive vs adapter),
operator NAMES are open (any new operator can be added to the
shared layer once it passes the admission checklist).
"""

from __future__ import annotations

from typing import Dict, List, Optional

from shared.workflow.template import (
    WORKFLOW_ARCHETYPES,
    WorkflowArchetype,
    WorkflowTemplate,
)


class TemplateRegistryError(Exception):
    """Raised on registration / lookup errors (duplicate
    template_id, unknown template_id, archetype mismatch)."""


# Process-wide template catalogue.  Keyed by ``template_id``.
# Each entry's ``archetype`` field tells the LLM-selection layer
# which V1 archetype it belongs to.
_REGISTRY: Dict[str, WorkflowTemplate] = {}


def register_template(template: WorkflowTemplate) -> None:
    """Add a template to the process-wide catalogue.

    Idempotent: re-registering the SAME template (identical
    ``template_id`` AND identical content) is a no-op.  Re-
    registering with different content under the same
    ``template_id`` raises ``TemplateRegistryError`` — explicit
    update requires ``unregister_template`` first.

    Agent layers register their templates at import time (e.g.
    ``rates_agent/workflows/event_study/__init__.py`` registers
    the event_study template when the module is loaded).
    """
    existing = _REGISTRY.get(template.template_id)
    if existing is not None:
        if existing == template:
            return  # idempotent
        raise TemplateRegistryError(
            f"template_id={template.template_id!r} is already "
            "registered with different content.  Call "
            "unregister_template() first if you genuinely intend "
            "to replace it."
        )
    _REGISTRY[template.template_id] = template


def unregister_template(template_id: str) -> None:
    """Remove a template from the catalogue.  Used by tests + by
    agent layers that hot-swap templates during development."""
    _REGISTRY.pop(template_id, None)


def get_template(template_id: str) -> WorkflowTemplate:
    """Retrieve a registered template.  Raises
    ``TemplateRegistryError`` if not found, with the list of
    known templates for diagnostic clarity."""
    if template_id not in _REGISTRY:
        raise TemplateRegistryError(
            f"template_id={template_id!r} is not registered.  "
            f"Known templates: {sorted(_REGISTRY.keys())}."
        )
    return _REGISTRY[template_id]


def list_templates(
    archetype: Optional[WorkflowArchetype] = None,
) -> List[WorkflowTemplate]:
    """List registered templates, optionally filtered by archetype.

    Returns templates in ``template_id`` sort order for stable
    catalogue rendering.

    Parameters
    ----------
    archetype :
        Optional archetype filter.  When supplied, only templates
        owning that archetype are returned.
    """
    templates = sorted(_REGISTRY.values(), key=lambda t: t.template_id)
    if archetype is not None:
        templates = [t for t in templates if t.archetype == archetype]
    return templates


def known_template_ids() -> List[str]:
    """Return the sorted list of registered template IDs.  Used by
    diagnostic tooling and the LLM template-selection layer."""
    return sorted(_REGISTRY.keys())


def known_archetypes() -> tuple[str, ...]:
    """Return the closed V1 archetype set (echo of
    ``WORKFLOW_ARCHETYPES``).  Convenience for the LLM template-
    selection layer's archetype-routing step."""
    return WORKFLOW_ARCHETYPES


def clear_template_registry() -> None:
    """Drop all registered templates.  Used between tests so
    template registrations from one test don't leak into another."""
    _REGISTRY.clear()


__all__ = [
    "TemplateRegistryError",
    "register_template",
    "unregister_template",
    "get_template",
    "list_templates",
    "known_template_ids",
    "known_archetypes",
    "clear_template_registry",
]
