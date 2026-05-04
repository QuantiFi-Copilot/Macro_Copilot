"""rates_agent.workflows.regime_conditioned_relationship — canonical template.

Loads ``template.yaml`` and registers the resulting
``WorkflowTemplate`` with the substrate's process-wide template
registry on import.  Importing this module is the entry point for
making the regime_conditioned_relationship template available to the
executor / template-selection LLM step / template-card catalogue.

Per docs/architecture/workflow_architecture.md, this is the V1
template for the ``regime_conditioned_relationship`` archetype.  The
full DAG topology + slot schema + topology-locked methodology choices
live in ``template.yaml``.

Public surface
--------------
- ``REGIME_CONDITIONED_TEMPLATE_PATH`` — absolute Path to template.yaml.
- ``load_regime_conditioned_template()`` — load + return the
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


REGIME_CONDITIONED_TEMPLATE_PATH: Path = (
    Path(__file__).resolve().parent / "template.yaml"
)


def load_regime_conditioned_template() -> WorkflowTemplate:
    """Load + return the regime_conditioned_relationship template.
    Cached by absolute path via
    ``shared.workflow.load_workflow_template``."""
    return load_workflow_template(REGIME_CONDITIONED_TEMPLATE_PATH)


def register() -> None:
    """Register the regime_conditioned_relationship template with the
    substrate's process-wide registry.  Idempotent."""
    template = load_regime_conditioned_template()
    register_template(template)


# Auto-register on import so a single
# ``import rates_agent.workflows.regime_conditioned_relationship`` is
# enough to make the template discoverable through
# ``shared.workflow.get_template`` and ``list_templates``.
register()


__all__ = [
    "REGIME_CONDITIONED_TEMPLATE_PATH",
    "load_regime_conditioned_template",
    "register",
]
