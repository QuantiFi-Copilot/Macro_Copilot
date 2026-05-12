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
    "persist_dag_from_lineage",
    "get_dag",
    "list_node_artifact_hashes",
]
