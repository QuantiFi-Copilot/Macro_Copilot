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
from rates_agent.workflows._runner import run_template

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
