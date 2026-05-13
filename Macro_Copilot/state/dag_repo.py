"""state.dag_repo — content-addressed DAG persistence.

Phase 0 PR 10.

A ``Lineage`` chain (``shared.artifacts.lineage.Lineage``) describes
how an artifact was produced as an ordered list of steps.  PR 7's
``put_artifact`` already persists the full ``Lineage`` JSON inside
``artifact_metadata.lineage``.  PR 10 lights up the THREE additional
PR 4 tables that store the DAG view of the same information:

  - ``dags``       — one row per distinct (content-addressed) topology.
  - ``dag_nodes``  — one row per step within a DAG, pointing at the
                     artifact produced by that step (when known).
  - ``dag_edges``  — one row per directed edge between steps.

Why the normalised tables exist alongside ``dags.topology`` JSONB
-----------------------------------------------------------------
The full ``topology`` column carries the unambiguous reconstruction
shape, but it is not query-friendly.  The normalised tables let SQL
answer:

  - "every DAG that touched primitive X" — index on
    ``dag_nodes(kind, name)``.
  - "every DAG that produced artifact H" — index on
    ``dag_nodes(artifact_hash)``.
  - "the input set for node N in DAG D" — primary-key lookup on
    ``dag_edges(dag_hash, to_node, ...)``.

The workspace replay route (Phase 0 PR 10) walks
``dag_nodes(dag_hash)`` to surface per-node artifact summaries
without paying for the full payload deserialization.

Content-addressing
------------------
The ``dag_hash`` is the SHA-256 of the canonical-JSON-encoded
topology.  Two ``Lineage`` chains whose steps are identical produce
the same ``dag_hash``; the table's PK enforces dedup at the DB
layer.  ``persist_dag_from_lineage`` is idempotent.

Linear-chain v1 contract
------------------------
Phase 0 ``Lineage`` chains are LINEAR — each step (except the head)
flows into the next via a single implicit ``input`` slot.  Operator
steps with auxiliary lineages (``OperatorStep.auxiliary_lineages``)
exist in the model but Phase 0's executor produces only linear
chains.  PR 10 therefore writes:

  - one ``dag_nodes`` row per step in ``Lineage.steps``,
  - one ``dag_edges`` row per (step_i, step_{i+1}) pair with
    ``slot_name = "input"``,
  - ``artifact_hash`` on the LAST node is the artifact's head hash;
    intermediate nodes carry NULL until / unless they're persisted
    individually.

Future-proofing
---------------
- Topology JSON is stored AS WELL as the normalised tables, so a
  later schema upgrade (richer ``slot_name`` taxonomy, multi-output
  nodes) can read+rewrite from the JSONB without losing data.
- ``persist_dag_from_lineage`` accepts an optional
  ``head_artifact_hash`` so callers that have not yet put the head
  artifact (e.g. dry-run plan persistence) can omit it.  PR 10's
  production path always supplies it.

Connection injection
--------------------
``persist_dag_from_lineage``, ``get_dag``, and
``list_node_artifact_hashes`` follow the PR 3 ``*, conn`` convention.
The caller owns the transaction; the typical pattern wraps the
artifact put and the DAG persist in one ``engine.begin()`` block so
the FK from ``dag_nodes.artifact_hash`` to ``artifact_metadata.hash``
is honoured atomically.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

from shared.artifacts.lineage import (
    Lineage,
    PrimitiveStep,
    _canonical_json,
)

logger = logging.getLogger("state.dag_repo")


# ============================================================================
# Constants
# ============================================================================

_COPILOT_STATE_SCHEMA = "copilot_state"
_HASH_LEN = 64

# Phase 0 ``Lineage`` is linear; the single implicit slot is named
# ``input`` so the SQL surface has a fixed enum to query on.  Multi-
# input operators are deferred to a later schema upgrade.
_DEFAULT_SLOT_NAME = "input"


# ============================================================================
# Public dataclasses
# ============================================================================


@dataclass(frozen=True)
class StoredDagNode:
    """One row from ``dag_nodes``."""

    node_id: str
    kind: str
    name: str
    params: Dict[str, Any]
    artifact_hash: Optional[str]


@dataclass(frozen=True)
class StoredDagEdge:
    """One row from ``dag_edges``."""

    from_node: str
    to_node: str
    slot_name: str


@dataclass(frozen=True)
class StoredDag:
    """The aggregate view returned by ``get_dag``."""

    dag_hash: str
    topology: Dict[str, Any]
    nodes: List[StoredDagNode]
    edges: List[StoredDagEdge]


@dataclass(frozen=True)
class PersistedWorkflowDag:
    """Hashes produced while persisting an executed workflow DAG.

    Returned by ``persist_dag_from_workflow_result`` so the caller
    can immediately mint a workspace pointing at the new
    ``dag_hash`` plus surface the terminal artifact hash to the
    UI without a follow-up read.
    """

    dag_hash: str
    terminal_artifact_hash: str
    node_artifact_hashes: Dict[str, str]


# ============================================================================
# Public API
# ============================================================================


def persist_dag_from_lineage(
    lineage: Lineage,
    *,
    conn: Connection,
    head_artifact_hash: Optional[str] = None,
) -> str:
    """Persist a linear lineage as a DAG (1 row in ``dags`` +
    N rows in ``dag_nodes`` + N-1 rows in ``dag_edges``).

    Returns the ``dag_hash`` — content-addressed by the canonical
    topology JSON.  Idempotent: the same lineage persists to the
    same hash and the second call is a no-op.

    Parameters
    ----------
    lineage : Lineage
        The chain to persist.  Must be non-empty (the Pydantic
        model enforces ``min_length=1`` so this is structurally
        guaranteed).
    conn : Connection
        SQLAlchemy connection; caller owns the transaction.
    head_artifact_hash : Optional[str]
        The artifact produced by the FINAL step.  Stamped on the
        last ``dag_nodes`` row.  When omitted (None), the last
        node's ``artifact_hash`` is NULL — used by plan-only
        persistence that has not yet executed.

    Returns
    -------
    str
        The 64-char hex ``dag_hash``.

    Foreign-key safety
    ------------------
    ``dag_nodes.artifact_hash`` is ``ON DELETE RESTRICT`` against
    ``artifact_metadata.hash``.  Supplying a head artifact hash
    that does NOT exist in ``artifact_metadata`` causes the INSERT
    to fail with an ``IntegrityError`` and the caller's transaction
    to roll back.  Order calls so ``put_artifact`` precedes
    ``persist_dag_from_lineage`` in the same transaction.
    """
    topology = _lineage_to_topology(lineage)
    dag_hash = _hash_topology(topology)

    # Idempotency gate — cheap PK lookup before we touch any of the
    # three tables.  ``put_artifact`` uses the same pattern.
    if _dag_exists(conn, dag_hash):
        logger.debug(
            "persist_dag_from_lineage: %s already present, no-op",
            dag_hash[:12],
        )
        return dag_hash

    # Insert ``dags`` first so the FK from ``dag_nodes`` resolves.
    conn.execute(
        text(
            f"""
            INSERT INTO {_COPILOT_STATE_SCHEMA}.dags (hash, topology)
            VALUES (:hash, CAST(:topology AS JSONB))
            ON CONFLICT (hash) DO NOTHING
            """
        ),
        {"hash": dag_hash, "topology": json.dumps(topology)},
    )

    # Per-step row.  ``artifact_hash`` is set only on the FINAL node
    # (and only when the caller supplied it); intermediate nodes
    # carry NULL because Phase 0 does not persist intermediate
    # artifacts individually.
    n_steps = len(lineage.steps)
    for idx, step in enumerate(lineage.steps):
        node_id = _node_id_for_index(idx)
        artifact_hash = (
            head_artifact_hash
            if (idx == n_steps - 1 and head_artifact_hash is not None)
            else None
        )
        conn.execute(
            text(
                f"""
                INSERT INTO {_COPILOT_STATE_SCHEMA}.dag_nodes (
                    dag_hash, node_id, kind, name, params, artifact_hash
                )
                VALUES (
                    :dag_hash, :node_id, :kind, :name,
                    CAST(:params AS JSONB), :artifact_hash
                )
                ON CONFLICT (dag_hash, node_id) DO NOTHING
                """
            ),
            {
                "dag_hash": dag_hash,
                "node_id": node_id,
                "kind": step.kind,
                "name": step.name,
                "params": json.dumps(_step_params_for_storage(step)),
                "artifact_hash": artifact_hash,
            },
        )

    # Linear edges: step[i] -> step[i+1] via the single implicit slot.
    for idx in range(n_steps - 1):
        conn.execute(
            text(
                f"""
                INSERT INTO {_COPILOT_STATE_SCHEMA}.dag_edges (
                    dag_hash, from_node, to_node, slot_name
                )
                VALUES (
                    :dag_hash, :from_node, :to_node, :slot_name
                )
                ON CONFLICT
                    (dag_hash, from_node, to_node, slot_name)
                    DO NOTHING
                """
            ),
            {
                "dag_hash": dag_hash,
                "from_node": _node_id_for_index(idx),
                "to_node": _node_id_for_index(idx + 1),
                "slot_name": _DEFAULT_SLOT_NAME,
            },
        )

    logger.info(
        "persist_dag_from_lineage: stored %s (steps=%d, head=%s)",
        dag_hash[:12], n_steps,
        head_artifact_hash[:12] if head_artifact_hash else None,
    )
    return dag_hash


def persist_dag_from_workflow_result(
    workflow,
    result,
    *,
    conn: Connection,
    object_storage,
) -> PersistedWorkflowDag:
    """Persist an executed workflow's true DAG shape.

    ``persist_dag_from_lineage`` (above) is intentionally linear
    because it normalises a single artifact's lineage chain.
    Workflow execution has richer topology: branches, joins,
    literal bindings, and one artifact per executed node.  This
    helper stores that shape without changing the existing
    tables:

      - every ``result.node_artifacts`` value is put into
        ``artifact_metadata`` via ``state.artifact_store.put_artifact``;
      - ``dags.topology`` carries workflow nodes / edges / literal
        bindings plus per-node artifact hashes;
      - ``dag_nodes.artifact_hash`` is populated for EVERY executed
        node, not only the terminal node.

    The DAG hash includes per-node artifact hashes, so the same
    logical template re-run against a revised data snapshot gets a
    distinct ``dag_hash`` instead of silently pointing at older
    artifacts.

    Imports of ``shared.workflow`` are deferred to call-time to
    avoid a circular-import path (``state`` → ``shared.workflow`` →
    ``shared.artifacts``).  The module-level imports above stay
    finance-blind.

    Parameters
    ----------
    workflow :
        ``shared.workflow.types.Workflow`` instance — the bound
        DAG that was executed.
    result :
        ``shared.workflow.result.WorkflowResult`` — the executor's
        return value carrying per-node artifacts.
    conn :
        Caller-owned transaction.  The helper does NOT open a
        sub-transaction; it inherits the caller's atomicity.
    object_storage :
        Object-storage backend instance passed through to
        ``put_artifact``.

    Returns
    -------
    PersistedWorkflowDag
        Identity bits for the persisted DAG: the ``dag_hash``
        suitable for ``POST /workspace``, the terminal artifact's
        hash, and the full per-node hash map.

    Raises
    ------
    ValueError
        When ``result.node_artifacts`` does not match
        ``workflow.nodes`` 1:1.  Surfaces the mismatch loudly so a
        partial-execute that didn't bubble up an error doesn't
        silently persist an inconsistent DAG.
    """
    # Lazy imports — see docstring.
    from shared.workflow.types import OperatorNode, PrimitiveNode  # noqa: F401
    from state.artifact_store import put_artifact

    workflow_node_ids = {node.node_id for node in workflow.nodes}
    result_node_ids = set(result.node_artifacts.keys())
    missing = workflow_node_ids - result_node_ids
    extra = result_node_ids - workflow_node_ids
    if missing or extra:
        raise ValueError(
            "WorkflowResult node_artifacts must match workflow nodes "
            "1:1 — persistence cannot proceed.  "
            f"Missing from result: {sorted(missing)}; "
            f"unexpected in result: {sorted(extra)}.  "
            "This is usually a workflow-executor bug where a node "
            "raised mid-execute without bubbling up."
        )

    # 1. Put every node's artifact, capturing each hash by node_id.
    node_artifact_hashes: Dict[str, str] = {}
    for node_id in sorted(result.node_artifacts.keys()):
        node_artifact_hashes[node_id] = put_artifact(
            result.node_artifacts[node_id],
            conn=conn,
            object_storage=object_storage,
        )

    terminal_artifact_hash = node_artifact_hashes[workflow.terminal_node_id]

    # 2. Build the canonical topology JSON + content-hash it.
    topology = _workflow_result_to_topology(workflow, node_artifact_hashes)
    dag_hash = _hash_topology(topology)

    # 3. Persist the DAG row.  ON CONFLICT DO NOTHING because the
    #    same logical DAG re-run with identical params + identical
    #    upstream data will hash identically — idempotent.
    conn.execute(
        text(
            f"""
            INSERT INTO {_COPILOT_STATE_SCHEMA}.dags (hash, topology)
            VALUES (:hash, CAST(:topology AS JSONB))
            ON CONFLICT (hash) DO NOTHING
            """
        ),
        {"hash": dag_hash, "topology": _canonical_json(topology)},
    )

    # 4. Per-node row with the executed artifact_hash filled in.
    for node in workflow.nodes:
        conn.execute(
            text(
                f"""
                INSERT INTO {_COPILOT_STATE_SCHEMA}.dag_nodes (
                    dag_hash, node_id, kind, name, params, artifact_hash
                )
                VALUES (
                    :dag_hash, :node_id, :kind, :name,
                    CAST(:params AS JSONB), :artifact_hash
                )
                ON CONFLICT (dag_hash, node_id) DO NOTHING
                """
            ),
            {
                "dag_hash": dag_hash,
                "node_id": node.node_id,
                "kind": node.kind,
                "name": _workflow_node_name(node),
                "params": _canonical_json(
                    _workflow_node_params_for_storage(node)
                ),
                "artifact_hash": node_artifact_hashes[node.node_id],
            },
        )

    # 5. Per-edge row with the substrate's actual input-slot names
    #    (not a fixed sentinel as ``persist_dag_from_lineage`` uses).
    for edge in workflow.edges:
        conn.execute(
            text(
                f"""
                INSERT INTO {_COPILOT_STATE_SCHEMA}.dag_edges (
                    dag_hash, from_node, to_node, slot_name
                )
                VALUES (
                    :dag_hash, :from_node, :to_node, :slot_name
                )
                ON CONFLICT
                    (dag_hash, from_node, to_node, slot_name)
                    DO NOTHING
                """
            ),
            {
                "dag_hash": dag_hash,
                "from_node": edge.source_node_id,
                "to_node": edge.target_node_id,
                "slot_name": edge.target_input_slot,
            },
        )

    logger.info(
        "persist_dag_from_workflow_result: stored %s "
        "(workflow=%s nodes=%d terminal=%s)",
        dag_hash[:12],
        workflow.workflow_id,
        len(workflow.nodes),
        terminal_artifact_hash[:12],
    )
    return PersistedWorkflowDag(
        dag_hash=dag_hash,
        terminal_artifact_hash=terminal_artifact_hash,
        node_artifact_hashes=node_artifact_hashes,
    )


def get_dag(dag_hash: str, *, conn: Connection) -> StoredDag:
    """Load a DAG and its normalised node + edge rows.

    Raises ``KeyError`` if the hash is unknown.
    """
    _validate_hash(dag_hash)
    dag_row = conn.execute(
        text(
            f"""
            SELECT hash, topology
            FROM {_COPILOT_STATE_SCHEMA}.dags
            WHERE hash = :h
            """
        ),
        {"h": dag_hash},
    ).mappings().first()
    if dag_row is None:
        raise KeyError(f"No dag with hash {dag_hash!r}")

    node_rows = conn.execute(
        text(
            f"""
            SELECT node_id, kind, name, params, artifact_hash
            FROM {_COPILOT_STATE_SCHEMA}.dag_nodes
            WHERE dag_hash = :h
            ORDER BY node_id
            """
        ),
        {"h": dag_hash},
    ).mappings().all()

    edge_rows = conn.execute(
        text(
            f"""
            SELECT from_node, to_node, slot_name
            FROM {_COPILOT_STATE_SCHEMA}.dag_edges
            WHERE dag_hash = :h
            ORDER BY from_node, to_node
            """
        ),
        {"h": dag_hash},
    ).mappings().all()

    return StoredDag(
        dag_hash=dag_row["hash"],
        topology=dict(dag_row["topology"]),
        nodes=[
            StoredDagNode(
                node_id=r["node_id"],
                kind=r["kind"],
                name=r["name"],
                params=dict(r["params"]),
                artifact_hash=r["artifact_hash"],
            )
            for r in node_rows
        ],
        edges=[
            StoredDagEdge(
                from_node=r["from_node"],
                to_node=r["to_node"],
                slot_name=r["slot_name"],
            )
            for r in edge_rows
        ],
    )


def list_node_artifact_hashes(
    dag_hash: str, *, conn: Connection,
) -> List[str]:
    """Return every ``artifact_hash`` (non-null only) recorded for
    the DAG, in ``node_id`` order.

    Used by the workspace replay route's "hash all node artifacts"
    assertion — sorted-stable so a re-fetch after restart produces
    the same sequence.
    """
    _validate_hash(dag_hash)
    rows = conn.execute(
        text(
            f"""
            SELECT artifact_hash
            FROM {_COPILOT_STATE_SCHEMA}.dag_nodes
            WHERE dag_hash = :h
              AND artifact_hash IS NOT NULL
            ORDER BY node_id
            """
        ),
        {"h": dag_hash},
    ).all()
    return [r[0] for r in rows]


# ============================================================================
# Internals
# ============================================================================


def _node_id_for_index(idx: int) -> str:
    """Stable per-step node id.

    Zero-padded so lexicographic ``ORDER BY node_id`` returns the
    chain in execution order even for chains with > 10 steps.
    Width of 4 is plenty for Phase 0 (longest expected chain is
    ~10-15 steps); future schema can extend if needed.
    """
    return f"n{idx:04d}"


def _lineage_to_topology(lineage: Lineage) -> Dict[str, Any]:
    """Canonical topology JSON: ``{"nodes": [...], "edges": [...]}``.

    Each node carries its full ``kind``, ``name``, ``params``,
    ``hash``, and step-kind-specific fields (e.g.
    ``PrimitiveStep.methodology_version_id``).  Sorted by node_id so
    canonical-JSON is deterministic.

    ``tool_config_path`` is EXCLUDED from the canonical topology —
    it's hash-irrelevant bookkeeping (matches the lineage layer's
    exclusion).  Including it would let two DAGs that came from
    byte-identical YAMLs at different paths register as distinct
    rows.  ``methodology_version_id`` is also EXCLUDED for the
    same reason ``PrimitiveStep`` excludes it from ``hashed_params``
    — it's a registry pointer, not identity.
    """
    nodes: List[Dict[str, Any]] = []
    for idx, step in enumerate(lineage.steps):
        node: Dict[str, Any] = {
            "node_id": _node_id_for_index(idx),
            "kind": step.kind,
            "name": step.name,
            "version": step.version,
            "step_hash": step.hash,
            "params": _step_params_for_storage(step),
        }
        # Carry step-kind-specific identity bits (those NOT folded
        # into ``params``) so the topology JSON is reconstructible.
        # We deliberately do NOT carry the metadata pointers
        # (tool_config_path, methodology_version_id) — see docstring.
        if isinstance(step, PrimitiveStep):
            node["primitive"] = {
                "tool_config_hash": step.tool_config_hash,
                "output_field": step.output_field,
                "as_of_date": step.as_of_date,
            }
        nodes.append(node)

    edges: List[Dict[str, Any]] = [
        {
            "from": _node_id_for_index(idx),
            "to": _node_id_for_index(idx + 1),
            "slot": _DEFAULT_SLOT_NAME,
        }
        for idx in range(len(lineage.steps) - 1)
    ]

    return {"nodes": nodes, "edges": edges}


def _workflow_result_to_topology(
    workflow,
    node_artifact_hashes: Dict[str, str],
) -> Dict[str, Any]:
    """Canonical topology JSON for an executed workflow DAG.

    Sort order on nodes / edges / literal_bindings is the load-
    bearing determinism guarantee — the topology hash is computed
    from the canonical-JSON representation, so two byte-identical
    workflows must produce byte-identical topology JSON.

    Differences from ``_lineage_to_topology``:
      - Carries the substrate's real edge ``slot`` names (not the
        single ``_DEFAULT_SLOT_NAME`` sentinel).
      - Captures the workflow's ``literal_bindings`` so a workspace
        replay can reconstruct the exact bound shape.
      - Records per-node ``artifact_hash`` inside each node entry
        rather than only at the terminal — gives multi-node DAGs
        a complete content-addressing surface.
    """
    nodes: List[Dict[str, Any]] = []
    for node in sorted(workflow.nodes, key=lambda n: n.node_id):
        nodes.append(
            {
                "node_id": node.node_id,
                "kind": node.kind,
                "name": _workflow_node_name(node),
                "params": _workflow_node_params_for_storage(node),
                "artifact_hash": node_artifact_hashes[node.node_id],
            }
        )

    edges: List[Dict[str, Any]] = [
        {
            "from": edge.source_node_id,
            "to": edge.target_node_id,
            "slot": edge.target_input_slot,
        }
        for edge in sorted(
            workflow.edges,
            key=lambda e: (
                e.source_node_id,
                e.target_node_id,
                e.target_input_slot,
            ),
        )
    ]

    literals: List[Dict[str, Any]] = [
        {
            "to": literal.target_node_id,
            "slot": literal.target_input_slot,
            "value": literal.value,
        }
        for literal in sorted(
            workflow.literal_bindings,
            key=lambda lit: (
                lit.target_node_id,
                lit.target_input_slot,
                str(lit.value),
            ),
        )
    ]

    return {
        "workflow_id": workflow.workflow_id,
        "terminal_node_id": workflow.terminal_node_id,
        "nodes": nodes,
        "edges": edges,
        "literal_bindings": literals,
    }


def _workflow_node_name(node) -> str:
    """Display name for a workflow node row.

    ``PrimitiveNode`` carries ``tool_name`` (e.g.
    ``calculate_curve_spread_tool``); ``OperatorNode`` carries
    ``operator_name`` (e.g. ``threshold_events``).  Falls back to
    ``node_id`` for any forward-compat node kind so the column
    is never NULL.
    """
    tool_name = getattr(node, "tool_name", None)
    if isinstance(tool_name, str) and tool_name:
        return tool_name
    operator_name = getattr(node, "operator_name", None)
    if isinstance(operator_name, str) and operator_name:
        return operator_name
    return getattr(node, "node_id", "unknown")


def _workflow_node_params_for_storage(node) -> Dict[str, Any]:
    """Return the ``params`` payload to persist in ``dag_nodes.params``
    for a workflow node.

    The JSON shape is keyed by node kind so a downstream reader
    can decode it without consulting another table:

      - PrimitiveNode → ``{tool_name, output_field, params}``
      - OperatorNode → ``{operator_name, params}``

    Falls back to an empty dict for forward-compat kinds so the
    column is always a valid JSONB object.
    """
    tool_name = getattr(node, "tool_name", None)
    if isinstance(tool_name, str) and tool_name:
        return {
            "tool_name": tool_name,
            "output_field": getattr(node, "output_field", None),
            "params": dict(getattr(node, "params", {}) or {}),
        }
    operator_name = getattr(node, "operator_name", None)
    if isinstance(operator_name, str) and operator_name:
        return {
            "operator_name": operator_name,
            "params": dict(getattr(node, "params", {}) or {}),
        }
    return {}


def _hash_topology(topology: Dict[str, Any]) -> str:
    """Content-hash the topology via the same canonical-JSON
    pipeline lineage uses.  Re-using ``_canonical_json`` keeps the
    canonicalisation recipe in ONE place — a future canonicalisation
    fix benefits both the step hash and the DAG hash."""
    return hashlib.sha256(
        _canonical_json(topology).encode("utf-8")
    ).hexdigest()


def _step_params_for_storage(step) -> Dict[str, Any]:
    """Return the ``params`` payload to persist in ``dag_nodes.params``
    for a single step.

    For ``PrimitiveStep`` the model's ``.params`` is the primitive
    input dict; for other step kinds it's the operator / fetch /
    clean / adapter params dict.  Either way it's already
    JSON-serializable (the lineage layer guarantees this via
    ``_canonicalize_for_hash`` upstream)."""
    return dict(step.params)


def _dag_exists(conn: Connection, dag_hash: str) -> bool:
    row = conn.execute(
        text(
            f"SELECT 1 FROM {_COPILOT_STATE_SCHEMA}.dags "
            "WHERE hash = :h LIMIT 1"
        ),
        {"h": dag_hash},
    ).first()
    return row is not None


def _validate_hash(h: str) -> None:
    if not isinstance(h, str) or len(h) != _HASH_LEN:
        raise ValueError(
            f"dag_hash must be a {_HASH_LEN}-char hex digest, got "
            f"{h!r} (len={len(h) if isinstance(h, str) else 'n/a'})"
        )
    try:
        int(h, 16)
    except ValueError:
        raise ValueError(f"dag_hash is not hex: {h!r}")


__all__ = [
    "StoredDag",
    "StoredDagNode",
    "StoredDagEdge",
    "PersistedWorkflowDag",
    "persist_dag_from_lineage",
    "persist_dag_from_workflow_result",
    "get_dag",
    "list_node_artifact_hashes",
]
