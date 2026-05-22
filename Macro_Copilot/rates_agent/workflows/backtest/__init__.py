"""rates_agent.workflows.backtest — canonical signal-driven backtest workflow.

Phase 1 PR 19.

Loads ``template.yaml`` and registers the resulting ``WorkflowTemplate``
with the substrate's process-wide template registry on import.  Importing
this module is the entry point for making the backtest archetype
available to the executor / template-selection LLM step / template-card
catalogue.

Public surface
--------------
- ``BACKTEST_TEMPLATE_PATH`` — absolute Path to template.yaml.
- ``load_backtest_template()`` — load + return the WorkflowTemplate.
- ``register()`` — register the template (idempotent).
"""

from __future__ import annotations

from pathlib import Path

from shared.workflow import (
    WorkflowTemplate,
    load_workflow_template,
    register_template,
)


BACKTEST_TEMPLATE_PATH: Path = (
    Path(__file__).resolve().parent / "template.yaml"
)


def load_backtest_template() -> WorkflowTemplate:
    """Load + return the backtest template (cached by absolute path)."""
    return load_workflow_template(BACKTEST_TEMPLATE_PATH)


def register() -> None:
    """Register the backtest template with the substrate's
    process-wide registry.  Idempotent."""
    template = load_backtest_template()
    register_template(template)


# Auto-register on import.
register()


__all__ = [
    "BACKTEST_TEMPLATE_PATH",
    "load_backtest_template",
    "register",
]
