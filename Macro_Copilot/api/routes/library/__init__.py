"""api.routes.library — Library catalogue REST surface.

Single-file router (no sub-routers yet) that exposes the
``/api/v1/library/manifest`` endpoint.  This is the canonical
machine-readable form of the tool catalogue: it parses every YAML
under ``manifesto/03_tool_manifest/<agent>/`` and returns the
combined catalogue as JSON.

Why a separate router (not bolted onto api/routes/workflows/)
-------------------------------------------------------------
- ``api/routes/workflows/catalogue.py`` reads from the IN-PROCESS
  registry (``rates_primitive_resolver`` + each tool's runtime
  ``config.yaml``).  That's a fine surface for the Workspace's
  per-tool detail page, but it's tied to runtime state and doesn't
  surface manifest-level metadata (sub_agent, bucket, category,
  related_tools, workflows, references).
- The Library page wants an EDITORIALLY-CURATED catalogue (manifest
  YAMLs are the source of truth — adding a tool here means writing
  the manifest entry).  Different concern, different surface, kept
  in its own module.

Mounted under ``/api/v1`` in ``api/server.py``.
"""

from __future__ import annotations

from fastapi import APIRouter

from api.routes.library.manifest import router as manifest_router

router = APIRouter()
router.include_router(manifest_router)
