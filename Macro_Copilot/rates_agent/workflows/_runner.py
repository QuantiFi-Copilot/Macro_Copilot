"""rates_agent.workflows._runner — finance-aware workflow runtime helpers.

The MCP server (``rates_agent.workflows.mcp_server``), the CLI
(``rates_agent.workflows.cli``), the WorkflowRouter
(``orchestrator.workflow_router``), and any future eval harness all
need the same primitives:

  - render the live template catalogue as a list of TemplateCards
  - resolve a template_id + slot_values triple to a concrete
    Workflow, validate it, execute it against the rates primitive
    resolver, and summarize the terminal artifact

Keeping that runtime logic in one finance-aware (but transport-blind)
module means:

  - the MCP server decorator surface stays thin (one ``@mcp.tool()``
    wrapper per template; body delegates to ``run_template``)
  - the CLI imports the same code, NOT a parallel re-implementation
  - the test suite can exercise ``run_template`` directly without
    requiring the ``mcp`` package (which isn't always available in
    every test environment, e.g. CI containers without it installed)

Why this module is finance-aware
--------------------------------
It imports from ``rates_agent.workflows`` (the rates primitive
resolver) and from the registered template packages — that's the
WHOLE POINT.  The substrate stays finance-blind by accepting a
``primitive_resolver`` callable; the runner here is the agent-side
binding that supplies the rates resolver.  An FX agent's runner
would import ``fx_agent.workflows.fx_primitive_resolver`` instead,
following the same shape.

Public surface
--------------
- ``run_template(template_id, slot_values, *, engine=None) -> dict``
- ``run_template_with_resolver(template_id, slot_values, *, engine, primitive_resolver) -> dict``
  (split out so the unit tests can pass synthetic resolvers without
  monkey-patching the module-level ``rates_primitive_resolver``)
- ``summarize_terminal(artifact) -> dict``
- ``list_workflow_cards() -> List[dict]``
- ``describe_workflow_card(template_id) -> dict``
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

from rates_agent.workflows import rates_primitive_resolver
from shared.artifacts.types import (
    EventSet,
    Panel,
    Series,
    SeriesSet,
    WindowedPanel,
)
from shared.workflow import (
    PrimitiveResolver,
    SlotBindingError,
    WorkflowExecutionError,
    card_for_template,
    execute_workflow,
    get_template,
    known_template_ids,
    list_templates,
    validate_workflow,
)
from shared.workflow.template_registry import TemplateRegistryError

logger = logging.getLogger("rates_agent.workflows._runner")


# ===========================================================================
# TERMINAL-ARTIFACT SUMMARIZER
# ===========================================================================
# Workflows return one of five typed artifact kinds.  The wire shape
# (MCP / CLI / eval) is JSON-friendly dicts, so we summarize each
# kind into a uniform dict.  Full pandas payloads are intentionally
# NOT included — they would blow the LLM's context budget and most
# downstream consumers only need {units, length, head/tail values,
# summary stats}.


def _safe_float(value: Any) -> Optional[float]:
    """Convert numpy / pandas scalars to plain Python floats, ``None``
    for non-finite or non-numeric values (so JSON serialization cannot
    trip on NaN / inf)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _detect_event_relative_index(s: Series) -> Optional[Dict[str, Any]]:
    """Inspect the Series lineage for a synthetic-anchor (event-relative
    offset) index encoding.

    Some operators (notably ``conditional_aggregate``) emit a Series whose
    ``DatetimeIndex`` is *not* a calendar — it's an integer offset
    (event-relative day) packed onto a synthetic anchor (1970-01-01) so
    the canonical ``Series`` shape is preserved.  Without this hint, UI
    renderers will literally print "1970-01-01" which is misleading.

    The encoding is signalled by the operator step's params carrying
    BOTH ``offset_anchor`` (an ISO date string) AND
    ``event_relative_offsets`` (a list of ints).  Returns the offsets +
    anchor when detected; ``None`` for normal calendar series.

    We walk lineage in reverse so the most recent step wins (operators
    that re-anchor to calendar after the conditional_aggregate would
    correctly suppress the offset semantic — though no such operator
    exists today).
    """
    try:
        steps = list(s.lineage.steps)
    except Exception:
        return None
    for step in reversed(steps):
        params = getattr(step, "params", None) or {}
        anchor = params.get("offset_anchor")
        offsets = params.get("event_relative_offsets")
        if anchor and isinstance(offsets, list):
            return {
                "anchor": str(anchor),
                "offsets": [int(x) for x in offsets],
                "produced_by": getattr(step, "name", None),
            }
    return None


def _summarize_series(s: Series) -> Dict[str, Any]:
    payload = s.payload
    n_rows = int(len(payload))
    if n_rows == 0:
        return {
            "type": "Series",
            "series_key": s.series_key,
            "units": s.units.value,
            "frequency": s.frequency,
            "n_rows": 0,
            "index_kind": "calendar",
        }

    # Detect synthetic-anchor (event-relative offset) encoding.  If
    # detected, we emit *offset-keyed* head/tail rather than calendar
    # dates — same payload, different framing — so the consumer renders
    # "Day 0 → Day 5" instead of "1970-01-01 → 1970-01-06".
    offset_meta = _detect_event_relative_index(s)
    head_val = payload.iloc[0]
    tail_val = payload.iloc[-1]
    cleaned = payload.dropna()
    summary_stats: Dict[str, Optional[float]] = {}
    if len(cleaned) > 0:
        summary_stats = {
            "mean": _safe_float(cleaned.mean()),
            "std": _safe_float(cleaned.std(ddof=1)) if len(cleaned) > 1 else None,
            "min": _safe_float(cleaned.min()),
            "max": _safe_float(cleaned.max()),
            "n_finite": int(len(cleaned)),
        }

    out: Dict[str, Any] = {
        "type": "Series",
        "series_key": s.series_key,
        "units": s.units.value,
        "frequency": s.frequency,
        "n_rows": n_rows,
        "summary_stats": summary_stats,
    }

    if offset_meta is not None:
        # Event-relative offset Series — surface the integer offsets
        # explicitly + offset-keyed head/tail.  Keep ``first_row`` /
        # ``last_row`` for backwards compatibility with consumers that
        # haven't read ``index_kind`` yet, but their ``date`` field is
        # the anchor-encoded date (existing behaviour, not a regression).
        offsets = offset_meta["offsets"]
        out["index_kind"] = "event_relative_offset"
        out["offset_anchor"] = offset_meta["anchor"]
        out["offsets"] = offsets
        out["offset_unit"] = "days"
        out["first_row"] = {
            "offset": offsets[0] if offsets else None,
            "value": _safe_float(head_val),
        }
        out["last_row"] = {
            "offset": offsets[-1] if offsets else None,
            "value": _safe_float(tail_val),
        }
        # All offset/value pairs — useful for the UI to render a small
        # bar / line chart of "mean move by horizon".  Cheap: capped by
        # the workflow's window_length (typically <= 30).
        try:
            out["offset_rows"] = [
                {
                    "offset": int(k),
                    "value": _safe_float(v),
                }
                for k, v in zip(offsets, payload.tolist())
            ]
        except Exception:
            pass
    else:
        head_idx = payload.index[0]
        tail_idx = payload.index[-1]
        out["index_kind"] = "calendar"
        out["first_row"] = {
            "date": head_idx.strftime("%Y-%m-%d"),
            "value": _safe_float(head_val),
        }
        out["last_row"] = {
            "date": tail_idx.strftime("%Y-%m-%d"),
            "value": _safe_float(tail_val),
        }

    return out


def _summarize_series_set(s: SeriesSet) -> Dict[str, Any]:
    return {
        "type": "SeriesSet",
        "keys": s.keys(),
        "units_by_key": {k: u.value for k, u in s.units_by_key.items()},
        "frequency": s.frequency,
        "n_rows": int(len(s.common_index)),
        "first_date": (
            s.common_index[0].strftime("%Y-%m-%d")
            if len(s.common_index) > 0 else None
        ),
        "last_date": (
            s.common_index[-1].strftime("%Y-%m-%d")
            if len(s.common_index) > 0 else None
        ),
    }


def _summarize_event_set(e: EventSet) -> Dict[str, Any]:
    return {
        "type": "EventSet",
        "source_series_key": e.source_series_key,
        "frequency": e.frequency,
        "n_dates": int(len(e.mask)),
        "n_events": e.n_events,
    }


def _summarize_panel(p: Panel) -> Dict[str, Any]:
    return {
        "type": "Panel",
        "n_rows": int(len(p.payload)),
        "columns": list(p.payload.columns),
        "units_by_column": {k: u.value for k, u in p.units_by_column.items()},
    }


def _summarize_windowed_panel(w: WindowedPanel) -> Dict[str, Any]:
    return {
        "type": "WindowedPanel",
        "n_events": int(w.payload.shape[0]),
        "window_length": int(w.payload.shape[1]),
    }


def summarize_terminal(artifact: Any) -> Dict[str, Any]:
    """Dispatch on the closed-family artifact union and return a
    JSON-friendly summary dict.

    Adding a new artifact wrapper requires adding a branch here AND
    in ``shared.artifacts.types``.
    """
    if isinstance(artifact, Series):
        return _summarize_series(artifact)
    if isinstance(artifact, SeriesSet):
        return _summarize_series_set(artifact)
    if isinstance(artifact, EventSet):
        return _summarize_event_set(artifact)
    if isinstance(artifact, Panel):
        return _summarize_panel(artifact)
    if isinstance(artifact, WindowedPanel):
        return _summarize_windowed_panel(artifact)
    return {"type": type(artifact).__name__, "summary": "unknown artifact"}


# ===========================================================================
# CATALOGUE HELPERS
# ===========================================================================


def list_workflow_cards() -> List[Dict[str, Any]]:
    """Return every registered template's TemplateCard as a list of
    JSON-friendly dicts.  Stable sort order (by template_id) so
    catalogue rendering is deterministic across runs."""
    cards = [card_for_template(t) for t in list_templates()]
    return [c.model_dump(mode="json") for c in cards]


def describe_workflow_card(template_id: str) -> Dict[str, Any]:
    """Return one template's TemplateCard as a JSON-friendly dict.

    Returns an envelope:
      - on success: ``{"ok": true, "card": {...}}``
      - on unknown id: ``{"ok": false, "error": "...", "known_template_ids": [...]}``
    """
    try:
        template = get_template(template_id)
    except TemplateRegistryError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "known_template_ids": known_template_ids(),
        }
    card = card_for_template(template)
    return {"ok": True, "card": card.model_dump(mode="json")}


# ===========================================================================
# RUN-TEMPLATE
# ===========================================================================


def run_template_with_resolver(
    template_id: str,
    slot_values: Dict[str, Any],
    *,
    engine: Any = None,
    primitive_resolver: PrimitiveResolver,
    persist: bool = False,
    object_storage: Any = None,
    workspace_name: Optional[str] = None,
    workspace_created_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve a template by id, bind the supplied slot_values,
    pre-flight validate, execute against the supplied resolver +
    engine, and return a JSON-friendly envelope summarizing the
    terminal artifact.

    This is the resolver-injected variant — tests pass synthetic
    resolvers; production callers (MCP server, CLI) wrap this via
    ``run_template`` which injects ``rates_primitive_resolver``.

    Failure modes (each surfaces a distinct envelope so callers can
    react appropriately):

      - Unknown template_id            → ``{"ok": false, "error": "..."}``
      - Slot binding failure           → ``{"ok": false, "error": "..."}``
      - Validate-time refusal          → same
      - Execution failure              → same

    The DB engine is the caller's responsibility — None is acceptable
    for synthetic-fetcher tests; production callers pass a live
    SQLAlchemy engine.

    Persistence
    -----------
    When ``persist=True`` and both ``engine`` and ``object_storage``
    are supplied, a successful execution ALSO writes:

      - every node artifact via ``state.artifact_store.put_artifact``;
      - the workflow's true DAG topology via
        ``state.dag_repo.persist_dag_from_workflow_result``;
      - a workspace row pointing at the new DAG hash.

    The returned envelope is extended with ``terminal_artifact_hash``,
    ``node_artifact_hashes``, ``dag_hash``, and ``workspace`` (slug +
    URL + name).  Persistence is best-effort: a failure here does NOT
    fail the envelope's primary execute path — the envelope reports
    ``persistence: {ok: false, error: ...}`` and otherwise stays
    identical.  This keeps the CLI / test paths (which pass
    ``persist=False``) unchanged while letting the chat path (which
    passes ``persist=True``) materialise a workspace per run.
    """
    logger.info("[%s] template invoked", template_id)

    # --- 1. Resolve template -----------------------------------------
    try:
        template = get_template(template_id)
    except TemplateRegistryError as exc:
        logger.warning("[%s] unknown template: %s", template_id, exc)
        return {
            "ok": False,
            "template_id": template_id,
            "error": str(exc),
        }

    # --- 2. Slot binding ---------------------------------------------
    try:
        workflow = template.bind(slot_values)
    except SlotBindingError as exc:
        logger.warning("[%s] slot binding failed: %s", template_id, exc)
        return {
            "ok": False,
            "template_id": template_id,
            "error": f"Slot binding failed: {exc}",
        }
    except Exception as exc:  # defensive guard
        logger.exception("[%s] unexpected error during bind", template_id)
        return {
            "ok": False,
            "template_id": template_id,
            "error": f"Unexpected error during bind: {exc}",
        }

    # --- 3. Pre-flight validate (catches resolver / unit / arity
    #        errors BEFORE any node runs).  ``execute_workflow`` re-
    #        runs validation internally, but doing it here too lets
    #        us surface validate-time refusals as a distinct error
    #        class from execution-time failures.
    try:
        validate_workflow(workflow, primitive_resolver=primitive_resolver)
    except Exception as exc:
        logger.warning("[%s] pre-flight validation failed: %s", template_id, exc)
        return {
            "ok": False,
            "template_id": template_id,
            "error": f"Workflow validation failed: {exc}",
        }

    # --- 4. Execute ---------------------------------------------------
    try:
        result = execute_workflow(
            workflow,
            engine=engine,
            primitive_resolver=primitive_resolver,
        )
    except WorkflowExecutionError as exc:
        logger.warning("[%s] execution failed: %s", template_id, exc)
        return {
            "ok": False,
            "template_id": template_id,
            "error": f"Workflow execution failed: {exc}",
        }
    except Exception as exc:
        logger.exception("[%s] unexpected error during execute", template_id)
        return {
            "ok": False,
            "template_id": template_id,
            "error": f"Unexpected error during execute: {exc}",
        }

    # --- 5. Summarize terminal artifact ------------------------------
    summary = summarize_terminal(result.terminal_artifact)
    envelope: Dict[str, Any] = {
        "ok": True,
        "template_id": template_id,
        "terminal_artifact": summary,
        "workflow_lineage_summary": result.workflow_lineage_summary,
    }
    logger.info(
        "[%s] template execution complete; terminal type=%s",
        template_id, summary.get("type"),
    )

    # --- 6. Optional persistence (chat path only) --------------------
    if persist:
        persistence = _persist_executed_workflow(
            template_id=template_id,
            workflow=workflow,
            result=result,
            engine=engine,
            object_storage=object_storage,
            workspace_name=workspace_name,
            workspace_created_by=workspace_created_by,
        )
        envelope["persistence"] = persistence
        if persistence.get("ok") is True:
            envelope["dag_hash"] = persistence["dag_hash"]
            envelope["terminal_artifact_hash"] = persistence[
                "terminal_artifact_hash"
            ]
            envelope["node_artifact_hashes"] = persistence[
                "node_artifact_hashes"
            ]
            envelope["workspace"] = persistence["workspace"]

    return envelope


def _persist_executed_workflow(
    *,
    template_id: str,
    workflow,
    result,
    engine: Any,
    object_storage: Any,
    workspace_name: Optional[str],
    workspace_created_by: Optional[str],
) -> Dict[str, Any]:
    """Persist node artifacts + DAG + workspace for a successful run.

    Best-effort: every failure mode is caught and returned as
    ``{"ok": False, "error": str}`` so the caller's primary envelope
    stays intact.  Logs at WARNING level with the template_id so
    debugging is straightforward.

    Imports of state-layer modules are deferred to call-time to
    avoid pulling them into the cold path used by CLI / test
    fixtures that pass ``persist=False``.
    """
    if engine is None:
        return {
            "ok": False,
            "error": (
                "persist=True requires a SQLAlchemy engine; got engine=None"
            ),
        }
    if object_storage is None:
        return {
            "ok": False,
            "error": (
                "persist=True requires an object_storage backend; "
                "got object_storage=None"
            ),
        }

    try:
        from state.dag_repo import persist_dag_from_workflow_result
        from state.workspace_repo import create_workspace, InvalidNameError
    except Exception as exc:  # defensive: import failure
        logger.warning(
            "[%s] persistence import failed: %s", template_id, exc,
        )
        return {"ok": False, "error": f"persistence import failed: {exc}"}

    try:
        with engine.begin() as conn:
            persisted = persist_dag_from_workflow_result(
                workflow,
                result,
                conn=conn,
                object_storage=object_storage,
            )
            try:
                workspace = create_workspace(
                    persisted.dag_hash,
                    conn=conn,
                    name=workspace_name,
                    created_by=workspace_created_by,
                    focus_node=workflow.terminal_node_id,
                )
            except InvalidNameError as exc:
                # Reserved / oversize names get rejected cleanly.
                # Retry with no name so the workflow still
                # materialises as a (slug-only, auto-named) workspace
                # — Build's sidebar can display it as "Untitled
                # workspace" until the caller renames it.
                logger.warning(
                    "[%s] workspace_name rejected (%s); "
                    "retrying with name=None",
                    template_id, exc,
                )
                workspace = create_workspace(
                    persisted.dag_hash,
                    conn=conn,
                    name=None,
                    created_by=workspace_created_by,
                    focus_node=workflow.terminal_node_id,
                )
    except Exception as exc:
        logger.exception("[%s] persistence failed", template_id)
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "dag_hash": persisted.dag_hash,
        "terminal_artifact_hash": persisted.terminal_artifact_hash,
        "node_artifact_hashes": persisted.node_artifact_hashes,
        "workspace": {
            "id": str(workspace.id),
            "slug": workspace.slug,
            "name": workspace.name,
            "dag_hash": workspace.dag_hash,
            "url": f"/workspace/{workspace.slug}",
        },
    }


def run_template(
    template_id: str,
    slot_values: Dict[str, Any],
    *,
    engine: Any = None,
    persist: bool = False,
    object_storage: Any = None,
    workspace_name: Optional[str] = None,
    workspace_created_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Production wrapper: same as ``run_template_with_resolver`` but
    with the rates_primitive_resolver injected.  This is what the MCP
    server, CLI, and orchestrator-side code call.

    Persistence kwargs (``persist`` / ``object_storage`` /
    ``workspace_name`` / ``workspace_created_by``) are forwarded
    verbatim — see ``run_template_with_resolver`` for semantics.
    """
    return run_template_with_resolver(
        template_id,
        slot_values,
        engine=engine,
        primitive_resolver=rates_primitive_resolver,
        persist=persist,
        object_storage=object_storage,
        workspace_name=workspace_name,
        workspace_created_by=workspace_created_by,
    )


__all__ = [
    "run_template",
    "run_template_with_resolver",
    "summarize_terminal",
    "list_workflow_cards",
    "describe_workflow_card",
]
