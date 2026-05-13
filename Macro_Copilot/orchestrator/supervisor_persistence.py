"""orchestrator/supervisor_persistence.py — R5.3 persistence bridge.

When the supervisor turn calls a single workspace-eligible primitive tool
(e.g. ``calculate_curve_spread_tool`` for "what is the 2s10s spread?"),
the workflow path doesn't fire because no template matched — execution
falls through to the supervisor, the tool runs via MCP, and the result
is rendered inline in the chat.  No workspace is persisted, so the
frontend's "Open in Build" CTA has no slug to navigate to.

This module bridges that gap.  Given:
  - the tool name
  - the params dict
  - the raw tool output dict (already validated by MCP)
  - a Postgres connection + object storage

it converts the tool output into a typed ``Series`` Artifact via the
existing ``tool_output_to_artifact_series`` bridge, persists it via
``put_artifact``, persists a one-step DAG via
``persist_dag_from_lineage``, and creates a workspace pointing at the
DAG.  The returned envelope mirrors what the workflow runner emits in
its ``workflow_result`` event so consumers can treat the two surfaces
identically.

Scope notes
-----------
- v1 handles ``output_artifact_type == "Series"`` only.  Panel-emitting
  primitives (build_sovereign_yield_panel_tool, compute_financing_rate
  _tool) return None from this helper — the caller falls back to the
  inline chat answer.  Panel persistence ships in a follow-up.
- ``output_field`` is inferred from the spec's ``output_field_units``
  declaration when present (we pick the first declared field).  When
  no hint is available we look for any field named ``time_series`` /
  ``time_series_spread`` / ``time_series_zscore`` etc. and pick the
  first one with rows.  Returns None if no candidate field is found.
- ``template_id`` and ``bound_slot_values`` are NULL on supervisor-
  persisted workspaces (see R5.6 — the workspace_repo guard enforces
  consistent NULL-pairing).  These workspaces are non-forkable; the
  frontend's PendingOverridesBar already surfaces this with the
  "Forkable workspaces only" caption.

Where it's called from
----------------------
``orchestrator/session.py``'s supervisor done-emission path is the
canonical caller.  The plumbing required to make that call is:
  1. Capture each tool's raw output dict in the domain-agent trace
     (currently ``trace_dicts`` only carries name + params + domain).
  2. Forward that into ``ChildResponse.tool_trace`` so the supervisor
     can read it at done-time.
  3. After ``extract_workspace_context`` returns a single-tool context,
     call this helper with the captured output dict.
This module does NOT do that plumbing — see the TODO in session.py.
The helper is fully implemented + tested in isolation; the wiring is
mechanical follow-up work.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from sqlalchemy.engine import Connection

from orchestrator.events import is_workspace_tool
from shared.artifacts.adapters.from_time_series import (
    tool_output_to_artifact_series,
)
from shared.config.tool_config import load_tool_config
from shared.workflow.registry import PrimitiveResolver

logger = logging.getLogger("orchestrator.supervisor_persistence")


def persist_supervisor_workspace_from_tool_call(
    *,
    tool_name: str,
    params: Dict[str, Any],
    tool_output: Dict[str, Any],
    conn: Connection,
    object_storage,
    primitive_resolver: PrimitiveResolver,
    created_by: Optional[str] = None,
    workspace_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Persist a single-tool supervisor turn as a Build workspace.

    Returns a workspace envelope (id / slug / name / dag_hash / url) on
    success, or None when the tool isn't workspace-eligible, the output
    shape isn't supported by the v1 bridge (Panel etc.), or no
    extractable time-series field is present.

    Errors propagate (transaction rollback at the caller's discretion)
    — the helper does NOT catch internal exceptions.  Callers should
    wrap in a try/except and log failures rather than failing the chat
    turn.
    """
    if not is_workspace_tool(tool_name):
        logger.debug(
            "supervisor_persistence: %s not in workspace tools, skipping",
            tool_name,
        )
        return None

    # Resolve the tool spec via the caller-supplied resolver so this
    # module stays finance-blind (matches the workflow executor's
    # contract — see PrimitiveResolver Protocol).
    try:
        spec = primitive_resolver(tool_name)
    except KeyError:
        logger.warning(
            "supervisor_persistence: no resolver entry for %s", tool_name,
        )
        return None

    # v1 — Series outputs only.  Panel-emitting primitives need a
    # different bridge function; fall through to the chat-inline
    # answer until that lands.
    output_artifact_type = getattr(spec, "output_artifact_type", "Series")
    if output_artifact_type != "Series":
        logger.info(
            "supervisor_persistence: %s emits %s; "
            "Panel-side persistence is a follow-up.  Skipping.",
            tool_name, output_artifact_type,
        )
        return None

    # Pick an output field to extract.  Spec declares ``output_field_units``
    # — its keys are the candidate time-series fields.  Use the FIRST
    # declared field (deterministic) when present; otherwise fall back
    # to any ``time_series*`` field on the output dict that's non-empty.
    declared_fields = list(
        getattr(spec, "output_field_units", {}).keys()
    )
    output_field = _pick_output_field(tool_output, declared_fields)
    if output_field is None:
        logger.info(
            "supervisor_persistence: %s output has no extractable "
            "time_series field; skipping.",
            tool_name,
        )
        return None

    # Validate input params via the spec's *Input class so the
    # PrimitiveStep's identity bits are consistent with the original
    # MCP call.  Validation errors are programmer errors here (the
    # MCP server already validated); surfacing them as exceptions is
    # the right thing.
    input_params = spec.input_class(**params)
    tool_config = load_tool_config(spec.config_path)

    # Build the Series artifact + its single-step Lineage.
    series_artifact = tool_output_to_artifact_series(
        tool_output,
        output_class=spec.output_class,
        output_field=output_field,
        tool_name=tool_name,
        tool_config=tool_config,
        params=input_params,
        tool_config_path=str(spec.config_path),
    )

    # Persist artifact + DAG + workspace.  Imports are lazy so this
    # module loads cleanly in unit-test contexts that don't have a
    # live Postgres engine yet.
    from state.artifact_store import put_artifact
    from state.dag_repo import persist_dag_from_lineage
    from state.workspace_repo import create_workspace

    artifact_hash = put_artifact(
        series_artifact,
        conn=conn,
        object_storage=object_storage,
    )
    dag_hash = persist_dag_from_lineage(
        series_artifact.lineage,
        conn=conn,
        head_artifact_hash=artifact_hash,
    )
    workspace = create_workspace(
        dag_hash,
        conn=conn,
        name=workspace_name or _default_workspace_name(tool_name, input_params),
        created_by=created_by,
        focus_node=_terminal_node_id(series_artifact.lineage),
        template_id=None,
        bound_slot_values=None,
    )

    envelope = {
        "id": str(workspace.id),
        "slug": workspace.slug,
        "name": workspace.name,
        "dag_hash": workspace.dag_hash,
        "url": f"/workspace/{workspace.slug}",
    }
    logger.info(
        "supervisor_persistence: persisted %s as workspace slug=%s",
        tool_name, workspace.slug,
    )
    return envelope


# ----------------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------------


def _pick_output_field(
    tool_output: Dict[str, Any],
    declared_fields: list[str],
) -> Optional[str]:
    """Decide which ``time_series*`` field to extract.  Prefers the
    spec's first declared field; falls back to heuristic discovery."""
    # Prefer the declaration — first declared field wins, but only if
    # it's present and non-empty on the actual output dict.
    for field in declared_fields:
        value = tool_output.get(field)
        if _looks_like_time_series(value):
            return field
    # Heuristic fallback — find any time_series* field.
    for key in tool_output.keys():
        if not isinstance(key, str):
            continue
        if not key.startswith("time_series"):
            continue
        if _looks_like_time_series(tool_output.get(key)):
            return key
    return None


def _looks_like_time_series(value: Any) -> bool:
    """A time-series field is a non-empty list of dicts."""
    return isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict)


def _default_workspace_name(tool_name: str, params: Any) -> str:
    """Synth a short human-readable name for the workspace sidebar.
    Uses the tool's MCP name + a short hash of its params so two
    runs of the same tool with different params surface distinctly."""
    seed = f"{tool_name}:{params}"
    suffix = uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex[:6]
    pretty = tool_name.replace("calculate_", "").replace("_tool", "")
    pretty = pretty.replace("_", " ").title()
    return f"{pretty} · {suffix}"


def _terminal_node_id(lineage) -> Optional[str]:
    """Single-step lineage's terminal node id.  Matches the convention
    ``state.dag_repo._node_id_for_index`` uses for the last step
    (``f"n{idx:04d}"``)."""
    n_steps = len(lineage.steps)
    if n_steps == 0:
        return None
    return f"n{(n_steps - 1):04d}"
