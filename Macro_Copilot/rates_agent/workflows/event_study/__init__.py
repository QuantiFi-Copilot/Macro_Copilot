"""rates_agent.workflows.event_study — canonical event-study template.

Loads ``template.yaml`` and registers the resulting
``WorkflowTemplate`` with the substrate's process-wide template
registry on import.  Importing this module is the entry point for
making the event_study template available to the executor /
template-selection LLM step / template-card catalogue.

Per docs/architecture/workflow_architecture.md, this is the V1
template for the ``event_study`` archetype.  The full DAG topology
+ slot schema + topology-locked methodology choices live in
``template.yaml``.

Public surface
--------------
- ``EVENT_STUDY_TEMPLATE_PATH`` — absolute Path to template.yaml.
- ``load_event_study_template()`` — load + return the
  WorkflowTemplate (cached via the substrate's loader).
- ``register()`` — register the template with the substrate's
  registry.  Idempotent (re-registering identical content is a
  no-op).  Auto-invoked on module import.
"""

from __future__ import annotations

from pathlib import Path

from shared.workflow import (
    WorkflowTemplate,
    load_workflow_template,
    register_template,
)


EVENT_STUDY_TEMPLATE_PATH: Path = (
    Path(__file__).resolve().parent / "template.yaml"
)


def load_event_study_template() -> WorkflowTemplate:
    """Load + return the event_study template.  Cached by absolute
    path via ``shared.workflow.load_workflow_template``."""
    return load_workflow_template(EVENT_STUDY_TEMPLATE_PATH)


def register() -> None:
    """Register the event_study template with the substrate's
    process-wide registry.  Idempotent."""
    template = load_event_study_template()
    register_template(template)


# Auto-register on import so a single ``import
# rates_agent.workflows.event_study`` is enough to make the
# template discoverable through ``shared.workflow.get_template``
# and ``list_templates``.
register()


__all__ = [
    "EVENT_STUDY_TEMPLATE_PATH",
    "load_event_study_template",
    "register",
]
