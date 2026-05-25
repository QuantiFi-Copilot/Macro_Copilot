"""rates_agent.workflows.cross_sectional_screen — first template for the
cross_sectional_screen archetype.

Loads ``template.yaml`` and registers the resulting ``WorkflowTemplate``
with the substrate's process-wide template registry on import.  Importing
this module is the entry point for making the cross_sectional_screen
template available to the executor / template-selection LLM step /
template-card catalogue.

Per ADR 0013 (docs_revamped/05_decisions/0013-cross-sectional-screen-
output-shape.md), this is the V1 fixed-arity (N=4) template that opens
the cross_sectional_screen archetype under Option A — terminal SeriesSet
keyed by caller-supplied member labels, no closed-family extension.

Public surface
--------------
- ``CROSS_SECTIONAL_SCREEN_TEMPLATE_PATH`` — absolute Path to template.yaml.
- ``load_cross_sectional_screen_template()`` — load + return the
  WorkflowTemplate (cached via the substrate's loader).
- ``register()`` — register the template with the substrate's registry.
  Idempotent (re-registering identical content is a no-op).  Auto-invoked
  on module import.
"""

from __future__ import annotations

from pathlib import Path

from shared.workflow import (
    WorkflowTemplate,
    load_workflow_template,
    register_template,
)


CROSS_SECTIONAL_SCREEN_TEMPLATE_PATH: Path = (
    Path(__file__).resolve().parent / "template.yaml"
)


def load_cross_sectional_screen_template() -> WorkflowTemplate:
    """Load + return the cross_sectional_screen template.  Cached by
    absolute path via ``shared.workflow.load_workflow_template``."""
    return load_workflow_template(CROSS_SECTIONAL_SCREEN_TEMPLATE_PATH)


def register() -> None:
    """Register the cross_sectional_screen template with the substrate's
    process-wide registry.  Idempotent."""
    template = load_cross_sectional_screen_template()
    register_template(template)


# Auto-register on import so a single ``import
# rates_agent.workflows.cross_sectional_screen`` is enough to make the
# template discoverable through ``shared.workflow.get_template`` and
# ``list_templates``.
register()


__all__ = [
    "CROSS_SECTIONAL_SCREEN_TEMPLATE_PATH",
    "load_cross_sectional_screen_template",
    "register",
]
