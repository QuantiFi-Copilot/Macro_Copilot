"""shared.workflow.template_loader — YAML → WorkflowTemplate.

Process-cached loader matching the discipline of
``shared.config.load_tool_config`` and
``shared.config.load_operator_config``: load once, reuse
everywhere, no hot-reload in V1.

YAML shape
----------
The YAML root is a single mapping with the same field names as
``WorkflowTemplate``::

    template_id: event_study
    archetype: event_study
    description: >-
      Conditional aggregation across event windows...
    archetype_signature:
      - "average move after / forward move when X exceeds Y"
      - "conditional aggregation across event windows"
    slot_schema:
      - name: signal_curve_family
        type: str
        required: true
        description: which curve family the signal series uses
      - name: signal_short_tenor
        type: str
        required: true
        description: short tenor for the signal curve spread
      - name: signal_long_tenor
        type: str
        required: true
        description: long tenor for the signal curve spread
      - name: threshold
        type: float
        required: true
        description: |z| threshold for the event mask
    nodes:
      - kind: primitive
        node_id: signal
        tool_name: calculate_curve_spread_tool
        output_field: time_series_zscore
        params:
          curve_family: {$slot: signal_curve_family}
          short_tenor: {$slot: signal_short_tenor}
          long_tenor: {$slot: signal_long_tenor}
      - kind: operator
        node_id: events
        operator_name: threshold_events
        params:
          rule: abs_above
          threshold: {$slot: threshold}
          threshold_basis: raw_value
    edges:
      - source_node_id: signal
        target_node_id: events
        target_input_slot: series
    terminal_node_id: events

Slot reference syntax
---------------------
Placeholders use the EXACT slot name only — the binder does NOT
support dotted-path resolution into nested objects.  Two patterns
for handling structured caller input:

  - **Flat slots (preferred for V1)**: declare one slot per
    leaf value, as in the example above
    (``signal_curve_family`` / ``signal_short_tenor`` /
    ``signal_long_tenor``).  Each ``$slot`` reference is an
    exact slot name lookup.  Type-checked at bind time.

  - **Whole-dict slot (when the consuming primitive accepts a
    nested dict)**: declare a ``type: dict`` slot and reference
    it whole.  The consuming primitive's ``*Input`` schema
    handles the nested-field validation::

        slot_schema:
          - name: signal_spec
            type: dict
            required: true
            description: SeriesSpec-shaped dict (curve_family, tenor, ...)

        nodes:
          - kind: primitive
            ...
            params:
              spec: {$slot: signal_spec}   # whole dict substituted

The dotted-path style ``{$slot: signal_spec.curve_family}`` is
NOT supported — it would fail at template construction time as
an undeclared slot reference.

Loading + validation
--------------------
``load_workflow_template(path)`` parses the YAML, constructs the
``WorkflowTemplate`` (which runs the substrate-template
construction validators), and caches by absolute path.

Errors are wrapped as ``WorkflowTemplateError`` with the file
path included so a malformed template surfaces with a clear
diagnostic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Union

import yaml

from shared.workflow.template import WorkflowTemplate


class WorkflowTemplateError(Exception):
    """Raised when a workflow template's YAML cannot be loaded or
    validated.  Message names the file path so debugging is fast."""


# Process-wide cache, keyed by absolute resolved path.  Mirrors
# ``shared.config.tool_config._CACHE`` exactly.
_TEMPLATE_CACHE: Dict[Path, WorkflowTemplate] = {}


def load_workflow_template(path: Union[str, Path]) -> WorkflowTemplate:
    """Load and validate a workflow template YAML.

    Cached process-wide by absolute path.  Subsequent calls for
    the same file return the same ``WorkflowTemplate`` instance.

    Parameters
    ----------
    path :
        Path to the template YAML file.  Relative paths are
        resolved against the current working directory.

    Returns
    -------
    WorkflowTemplate
        Frozen, validated template.

    Raises
    ------
    WorkflowTemplateError
        If the file is missing, malformed YAML, or fails the
        ``WorkflowTemplate`` construction validator.  Wraps the
        underlying error and prefixes the file path.
    """
    p = Path(path).resolve()

    if p in _TEMPLATE_CACHE:
        return _TEMPLATE_CACHE[p]

    if not p.is_file():
        raise WorkflowTemplateError(
            f"Workflow template not found: {p}\n"
            "Hint: each template's folder must contain a "
            "template.yaml at its root."
        )

    try:
        with p.open("r") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise WorkflowTemplateError(
            f"YAML parse error in {p}: {exc}"
        ) from exc

    if raw is None:
        raise WorkflowTemplateError(f"Workflow template is empty: {p}")
    if not isinstance(raw, dict):
        raise WorkflowTemplateError(
            f"Workflow template root must be a mapping, got "
            f"{type(raw).__name__}: {p}"
        )

    try:
        template = WorkflowTemplate(**raw)
    except Exception as exc:
        raise WorkflowTemplateError(
            f"Schema validation failed for {p}:\n{exc}"
        ) from exc

    _TEMPLATE_CACHE[p] = template
    return template


def clear_workflow_template_cache() -> None:
    """Drop all cached templates.  Call between tests that load
    tweaked versions of the same file (mirrors
    ``clear_tool_config_cache``)."""
    _TEMPLATE_CACHE.clear()


__all__ = [
    "WorkflowTemplateError",
    "load_workflow_template",
    "clear_workflow_template_cache",
]
