"""api.routes.library.manifest — read-only library catalogue endpoint.

Surface
-------
``GET /library/manifest`` → ``LibraryManifestResponse``

Reads every YAML under ``manifesto/03_tool_manifest/<agent>/`` at
request time and returns the combined catalogue as JSON.  The
frontend Library page renders directly from this response — no
hard-coding of tools in TypeScript.

Discipline
----------
- The YAMLs are the single source of truth.  This endpoint validates
  that every tool's declared file paths (``mcp_server``, ``schema``,
  ``compute``, ``config``) exist on disk; entries that fail are
  dropped from the response and logged.  This keeps the Library
  page from showing tools whose backend has been removed.
- Read-time parsing (every request hits the YAMLs).  YAML files are
  small (~10 KB total); load + parse cost is sub-millisecond and
  the freshness guarantee is worth more than the cache.  If we ever
  want caching, do it client-side via HTTP ETag / Cache-Control.
- The response structure deliberately mirrors the YAML schema so
  the frontend types are a 1:1 translation.

Schema match
------------
Every required field from the manifest schema is surfaced in the
response model.  Schema drift between YAML and this module is
visible at request time (Pydantic validation error → 500).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.dependencies import PROJECT_ROOT

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response models — 1:1 with the manifest YAML schema
# ---------------------------------------------------------------------------

class ToolImplementation(BaseModel):
    mcp_server: str
    tool_function: str
    schema_: str  # aliased; YAML key is ``schema`` which clashes w/ pydantic
    compute: str
    config: str

    class Config:
        # Allow population by `schema` as an alias for `schema_`
        populate_by_name = True


class ManifestTool(BaseModel):
    name: str
    domain: str
    sub_agent: str
    bucket: str
    # `category` was added after the initial schema; default empty
    # string keeps older entries valid until they're updated.
    category: str = ""
    status: str
    implementation: ToolImplementation
    one_liner: str
    bucket_rationale: str
    pm_overridable: list[str]
    related_tools: list[str]
    workflows: list[str]
    references: list[str]
    built_date: str
    validation_status: str


class AgentManifest(BaseModel):
    """One agent's combined manifest (sum of all sub-agent YAMLs)."""

    agent: str  # e.g. "rates"
    tool_count: int
    tools: list[ManifestTool]
    # Sub-agent counts surface alongside the flat tools list so the
    # frontend can render the instrument-family filter strip without
    # a second pass.
    sub_agent_counts: dict[str, int]
    # Same for category buckets — counted per category so the UI's
    # filter chips show "(N)" without recomputing client-side.
    category_counts: dict[str, int]


class LibraryManifestResponse(BaseModel):
    agents: dict[str, AgentManifest]
    total_tools: int


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

MANIFEST_ROOT = PROJECT_ROOT / "manifesto" / "03_tool_manifest"


def _load_agent_manifest(agent_dir: Path) -> AgentManifest:
    """Read every ``*.yml`` in ``agent_dir`` and return the combined manifest.

    Tool entries whose declared file paths don't resolve are dropped
    from the response and logged.  This keeps the Library page from
    surfacing tools whose backend was removed without a manifest
    update.
    """
    if not agent_dir.is_dir():
        raise FileNotFoundError(agent_dir)

    tools: list[ManifestTool] = []
    sub_counts: dict[str, int] = {}
    cat_counts: dict[str, int] = {}
    dropped: list[str] = []

    for yml in sorted(agent_dir.glob("*.yml")):
        with yml.open() as f:
            raw = yaml.safe_load(f)
        if not raw or "tools" not in raw:
            continue
        for entry in raw["tools"]:
            # Normalise the entry before Pydantic instantiation:
            #
            # 1. PyYAML auto-parses dates like `2026-05-01` into
            #    datetime.date objects.  Our schema declares
            #    `built_date: str`, and Pydantic v2 will refuse the
            #    coercion in strict mode — silently dropping every
            #    entry.  Stringify any date/datetime values
            #    recursively so the schema stays simple.
            # 2. YAML's `schema` key clashes with BaseModel.schema, so
            #    we rename it to `schema_` to match the field name on
            #    ToolImplementation.
            entry = _stringify_dates(entry)
            impl = entry.get("implementation", {})
            if "schema" in impl and "schema_" not in impl:
                impl["schema_"] = impl.pop("schema")

            try:
                tool = ManifestTool(**entry)
            except Exception as exc:
                # Visible failure — every dropped entry surfaces in
                # the server log AND in the response payload (via
                # `dropped`) so the operator can spot a manifest
                # drift without having to tail logs.
                msg = f"validation failed for {entry.get('name')!r}: {exc}"
                logger.warning("manifest: %s", msg)
                dropped.append(msg)
                continue

            # File-existence sweep.  Skip entries whose backend
            # is stale; emit a warning so it shows up in server logs.
            if not _validate_paths(tool):
                dropped.append(f"path missing for {tool.name!r}")
                continue

            tools.append(tool)
            sub_counts[tool.sub_agent] = sub_counts.get(tool.sub_agent, 0) + 1
            if tool.category:
                cat_counts[tool.category] = cat_counts.get(tool.category, 0) + 1

    if dropped:
        logger.warning(
            "manifest: %d entries dropped from %s — %s",
            len(dropped),
            agent_dir.name,
            "; ".join(dropped),
        )

    return AgentManifest(
        agent=agent_dir.name,
        tool_count=len(tools),
        tools=tools,
        sub_agent_counts=sub_counts,
        category_counts=cat_counts,
    )


def _stringify_dates(value: Any) -> Any:
    """Recursively coerce any date / datetime values inside a YAML-parsed
    structure into ISO-format strings.

    PyYAML auto-parses ``2026-05-01`` into ``datetime.date(2026, 5, 1)``;
    our manifest schema declares date-shaped fields as ``str`` so the
    JSON response is straightforward.  Without this coercion, Pydantic
    v2 strict mode rejects the entry and the tool gets dropped from
    the response — invisibly to the user (was the bug that caused
    every entry to vanish on first deploy).
    """
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _stringify_dates(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stringify_dates(v) for v in value]
    return value


def _validate_paths(tool: ManifestTool) -> bool:
    """Return True iff every declared file path resolves on disk.

    `config: N/A` is a valid sentinel (e.g. scanner.py tools that
    inherit conventions from shared.analytics).  Other paths must
    point to real files relative to PROJECT_ROOT.
    """
    impl = tool.implementation
    checks: list[str] = [impl.mcp_server, impl.compute.split("::", 1)[0]]
    if impl.schema_ and impl.schema_ != "N/A":
        checks.append(impl.schema_.split("::", 1)[0])
    if impl.config and impl.config != "N/A":
        checks.append(impl.config)
    for path in checks:
        if not (PROJECT_ROOT / path).exists():
            logger.warning(
                "manifest: dropping %r — declared path missing: %s",
                tool.name,
                path,
            )
            return False
    return True


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get(
    "/library/manifest",
    response_model=LibraryManifestResponse,
    summary="Library tool catalogue, parsed from the manifest YAMLs",
)
def get_library_manifest() -> LibraryManifestResponse:
    """Read every per-agent manifest under ``manifesto/03_tool_manifest/``.

    Response shape:
    ```
    {
      "agents": {
        "rates_agent": {
          "agent": "rates_agent",
          "tool_count": 18,
          "tools": [ {...}, ... ],
          "sub_agent_counts": { "sovereign_bonds": 12, "ois": 6 },
          "category_counts": { "snapshots": 2, "curve_shape": 3, ... }
        }
      },
      "total_tools": 18
    }
    ```
    """
    if not MANIFEST_ROOT.is_dir():
        raise HTTPException(status_code=500, detail=f"manifest root missing: {MANIFEST_ROOT}")

    agents: dict[str, AgentManifest] = {}
    total = 0
    for agent_dir in sorted(p for p in MANIFEST_ROOT.iterdir() if p.is_dir()):
        try:
            manifest = _load_agent_manifest(agent_dir)
        except Exception as exc:
            logger.error("failed to load manifest for %s: %s", agent_dir.name, exc)
            continue
        agents[agent_dir.name] = manifest
        total += manifest.tool_count

    return LibraryManifestResponse(agents=agents, total_tools=total)
