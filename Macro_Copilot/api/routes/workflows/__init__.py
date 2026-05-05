"""api.routes.workflows — REST surface for the LLM-orchestrated workflow
template layer (PR 9 backend) + the per-primitive tool catalogue.

Two sub-routers, mounted under ``/api/v1/workflows``:

  - ``catalogue.py`` — read-only catalogue endpoints:
        GET  /workflows                       → list TemplateCards
        GET  /workflows/{template_id}         → one full TemplateCard
        GET  /tools                           → list primitive ToolCards
        GET  /tools/{tool_name}               → one full ToolCard
                                                (descriptions, slot/Input
                                                shape, methodology +
                                                conventions from config.yaml)

  - ``execute.py``   — execute a bound workflow template:
        POST /workflows/{template_id}/run     → bind + execute the template,
                                                return the same envelope the
                                                MCP server returns

ALL endpoints are READ-ONLY w.r.t. backend state — no DB writes, no
config edits, no MCP-subprocess spawning (we re-use the substrate's
in-process registry + the rates_primitive_resolver directly).

Why a separate router (not bolted onto api/routes/rates/)
---------------------------------------------------------
Rates routes are PRIMITIVE-LEVEL endpoints (one HTTP route per primitive
output shape).  Workflows are DAG-LEVEL endpoints — the contract is
"give the bound template + slot_values, get a workflow execution
envelope back."  Distinct concern, distinct prefix; mirrors the
backend's own primitive vs workflow architectural split.
"""

from __future__ import annotations

from fastapi import APIRouter

from api.routes.workflows.catalogue import router as catalogue_router
from api.routes.workflows.execute import router as execute_router

router = APIRouter()
router.include_router(catalogue_router)
router.include_router(execute_router)
