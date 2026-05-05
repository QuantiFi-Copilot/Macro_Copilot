"""api.routes.workflows.execute — execute a bound workflow template.

POST /workflows/{template_id}/run
---------------------------------
Body::

    { "slot_values": { ... } }

Response: same envelope shape ``rates_agent.workflows._runner.run_template``
returns:

    { "ok": true, "template_id": "...", "terminal_artifact": {...},
      "workflow_lineage_summary": "..." }

or on failure::

    { "ok": false, "template_id": "...", "error": "<diagnostic>" }

This endpoint is the workflow-template analog of the per-primitive
``/api/v1/rates/detail/*`` endpoints — same shape (POST a binding,
get an envelope back), same in-process compute path, no LLM in the
loop.

The LLM-driven entry point lives on the WebSocket (``/api/chat``) —
this REST endpoint is for direct execution from the workspace UI when
the user already knows the template + slot_values they want to run.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine

from api.dependencies import get_engine
from rates_agent.workflows import (
    known_rates_primitives,
    rates_primitive_resolver,
)
from rates_agent.workflows._runner import run_template
from shared.config import load_tool_config

logger = logging.getLogger("api.routes.workflows.execute")

router = APIRouter()


class RunWorkflowRequest(BaseModel):
    """Body shape for ``POST /workflows/{template_id}/run``."""

    slot_values: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Slot bindings keyed by slot_name.  Must satisfy the "
            "named template's slot_schema (use GET /workflows/"
            "{template_id} to inspect)."
        ),
    )


@router.post(
    "/workflows/{template_id}/run",
    summary="Run a bound workflow template",
)
def run_workflow_template(
    template_id: str,
    body: RunWorkflowRequest,
    engine: Engine = Depends(get_engine),
):
    """Execute a workflow template with the supplied slot_values.

    Same envelope shape as ``rates_agent.workflows._runner.run_template``.
    Slot binding errors / validate-time refusals / execution errors all
    surface as ``{"ok": false, "error": "..."}`` — HTTP status stays
    200 because the failure mode is "the user's binding was wrong",
    not "the API broke."  HTTP 500 is reserved for unexpected
    server-side faults.
    """
    try:
        envelope = run_template(
            template_id, body.slot_values, engine=engine,
        )
    except Exception as exc:
        logger.exception("[workflows/%s] unexpected execute error", template_id)
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected workflow execution error: {exc}",
        )
    return envelope


# ===========================================================================
# PRIMITIVE EXECUTION (per-tool)
# ===========================================================================
#
# POST /tools/{tool_name}/run
# ---------------------------
# Body: { "params": { ... } } where params satisfies the primitive's
# *Input schema (use GET /tools/{tool_name} to inspect).
#
# Response::
#
#     { "ok": true,  "tool_name": "...", "output": <raw primitive output> }
#     { "ok": false, "tool_name": "...", "error": "<diagnostic>" }
#
# This is the building block the workspace's PrimitiveModelView fires
# every time the user clicks "Run" — same call shape the workflow
# executor uses internally, exposed as a one-shot REST endpoint.


class RunPrimitiveRequest(BaseModel):
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Primitive input bindings.  Must satisfy the named "
            "primitive's *Input Pydantic schema (use GET "
            "/tools/{tool_name} to inspect input_fields)."
        ),
    )


@router.post(
    "/tools/{tool_name}/run",
    summary="Run a single primitive tool with the supplied params",
)
def run_primitive_tool(
    tool_name: str,
    body: RunPrimitiveRequest,
    engine: Engine = Depends(get_engine),
):
    """Execute a single primitive directly (no workflow, no LLM).

    Same call shape the substrate executor uses internally:
    ``spec.callable(engine=engine, params=spec.input_class(**params),
    config=load_tool_config(spec.config_path))``.  Returns the raw
    output dict (already schema-conformant with the primitive's
    *Output class).  Validation errors and primitive runtime errors
    surface as HTTP 200 with ``{"ok": false, "error": "..."}`` so the
    workspace's controls rail can show the failure inline.  HTTP 500
    is reserved for unexpected server-side faults.
    """
    if tool_name not in known_rates_primitives():
        return {
            "ok": False,
            "tool_name": tool_name,
            "error": (
                f"tool_name={tool_name!r} is not registered.  Use "
                "GET /tools for the full catalogue."
            ),
            "known_tool_names": known_rates_primitives(),
        }

    spec = rates_primitive_resolver(tool_name)
    config = load_tool_config(spec.config_path)

    # Pydantic validation step — surface clean errors back to the UI.
    try:
        params = spec.input_class(**body.params)
    except Exception as exc:
        return {
            "ok": False,
            "tool_name": tool_name,
            "error": f"Input validation failed: {exc}",
        }

    try:
        output = spec.callable(engine=engine, params=params, config=config)
    except Exception as exc:
        logger.exception("[tools/%s] execute failed", tool_name)
        return {
            "ok": False,
            "tool_name": tool_name,
            "error": f"Primitive execution failed: {exc}",
        }

    return {
        "ok": True,
        "tool_name": tool_name,
        "output": output,
    }
