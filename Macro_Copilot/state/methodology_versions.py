"""state.methodology_versions — YAML + code-revision registries.

Phase 0 PR 9.

Wraps two Phase 0 PR 4 tables that PR 9 finally lights up:

  - ``copilot_state.methodology_versions``
        One row per distinct YAML content snapshot.  Dedup'd by
        ``yaml_content_hash`` so re-loading the same YAML across
        deploys / processes does not bloat the table.

  - ``copilot_state.application_version``
        One row per distinct code revision (git commit SHA).  Dedup'd
        by ``git_commit`` so a long-running process doesn't insert
        on every put.

Why both tables go through this module
--------------------------------------
Both registries share the same operational shape — idempotent INSERT
keyed by a content-derived identifier, with a small in-process cache
to avoid round-trip on every artifact put — so they're co-located.

YAML content-hash discipline
----------------------------
``register_yaml`` does NOT hash the raw YAML text.  Whitespace,
key-ordering, and comment formatting are NOT meaningful to the
methodology — only the parsed-and-canonicalised content is.  We
therefore:

  1. ``yaml.safe_load`` the text into a Python dict.
  2. Run it through ``shared.artifacts.lineage._canonicalize_for_hash``
     (the same canonicalizer the lineage layer uses for step hashes —
     keeps the recipe in ONE place).
  3. SHA-256 the canonical JSON.

Consequences:

  - Two YAML files that differ only in indentation / comment / key
    order register as the SAME version (same hash, same id).
  - A real semantic edit (e.g. ``z_score_window_days: 252 -> 200``)
    registers as a NEW version.
  - The stored ``yaml_content`` JSONB is the parsed-and-canonicalised
    dict — round-trips losslessly into ``ToolConfig`` via
    ``ToolConfig.model_validate(dict_from_get_yaml(...))``.

Connection injection
--------------------
``register_yaml`` and ``register_application_version`` follow the
PR 3 ``*, conn`` convention.  Caller owns the transaction; the
typical pattern wraps both registration ops in the same
``engine.begin()`` block as the artifact put they're feeding.

In-process caching
------------------
Both registries memoise on first hit so a process that puts
thousands of artifacts does not pay a Postgres round-trip per put.
Caches are content-keyed (``yaml_content_hash`` / ``git_commit``)
so a future hot-reload that re-registered a tweaked YAML would
miss cache and re-INSERT correctly.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional

import yaml
from sqlalchemy import text
from sqlalchemy.engine import Connection

from shared.artifacts.lineage import _canonical_json

logger = logging.getLogger("state.methodology_versions")


# ============================================================================
# Constants
# ============================================================================

_COPILOT_STATE_SCHEMA = "copilot_state"

# Git SHA-1 hex digest length.  Mirrors the migration's
# ``GIT_SHA_LEN`` so the registry refuses to write a malformed
# commit string at the application layer (the DB CHECK constraint
# is the backstop).
_GIT_SHA_LEN = 40

# Sentinel used when the current process has no resolvable git
# commit (running outside a checkout, or no ``git`` binary on PATH).
# The sentinel is hex-encoded so it still satisfies the
# ``length(git_commit) = 40`` CHECK constraint.
_UNKNOWN_COMMIT_SENTINEL = "u" * _GIT_SHA_LEN


# ============================================================================
# Public dataclass — what get_yaml callers see
# ============================================================================


@dataclass(frozen=True)
class MethodologyVersionRecord:
    """One row from ``methodology_versions``.

    ``yaml_content`` is the parsed-and-canonicalised dict — feed it
    directly to ``ToolConfig.model_validate(record.yaml_content)``
    to reconstruct the original config.
    """

    id: int
    yaml_path: str
    yaml_content_hash: str
    yaml_content: Dict[str, Any]


@dataclass(frozen=True)
class ApplicationVersionRecord:
    """One row from ``application_version``."""

    id: int
    git_commit: str


# ============================================================================
# In-process caches
# ============================================================================
# Keyed by content (not by path / by engine identity) so two callers
# loading the same YAML get the same id with one DB hit.

_yaml_hash_to_id: Dict[str, int] = {}
_yaml_cache_lock = Lock()

_git_commit_to_id: Dict[str, int] = {}
_app_version_cache_lock = Lock()

# Resolved-once cache for the current process's git SHA.  Populated
# on first ``current_application_version_id`` call.
_current_process_commit: Optional[str] = None
_current_process_commit_lock = Lock()


def clear_caches() -> None:
    """Drop all in-process caches.  Used by tests that rebuild the
    schema mid-test (a fresh DB has different auto-incremented ids
    than the cache remembered)."""
    with _yaml_cache_lock:
        _yaml_hash_to_id.clear()
    with _app_version_cache_lock:
        _git_commit_to_id.clear()
    global _current_process_commit
    with _current_process_commit_lock:
        _current_process_commit = None


# ============================================================================
# YAML registry
# ============================================================================


def _hash_yaml_content(parsed: Dict[str, Any]) -> str:
    """Compute the canonical ``yaml_content_hash`` for a parsed YAML.

    Re-uses ``_canonical_json`` from the lineage layer so the
    canonicalization recipe lives in ONE place.  This is the
    invariant the registry's dedup gate relies on: two YAMLs with
    the same semantic content produce the same hash even if their
    raw text differs (whitespace, key order, comments).
    """
    canonical = _canonical_json(parsed)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonicalize_yaml_content(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Public helper: return the canonical-JSON-equivalent dict of
    a parsed YAML.  Used by the workspace replay route when it
    needs to compare two YAMLs at the semantic level.

    The return value is the result of ``_canonicalize_for_hash`` —
    a dict with the same keys/values but with non-JSON-safe types
    (datetimes, numpy scalars, etc.) coerced to their stable
    representation.  Round-trips cleanly through ``json.dumps`` and
    ``ToolConfig.model_validate``.
    """
    from shared.artifacts.lineage import _canonicalize_for_hash

    out = _canonicalize_for_hash(parsed)
    if not isinstance(out, dict):
        raise TypeError(
            f"canonicalize_yaml_content expected dict, got "
            f"{type(out).__name__}.  YAMLs must parse to a mapping at the "
            "top level."
        )
    return out


def register_yaml(
    yaml_text: str,
    *,
    yaml_path: str,
    conn: Connection,
) -> int:
    """Register a YAML's content snapshot and return its
    ``methodology_versions.id``.

    Idempotent under content: two calls with the same parsed-
    and-canonicalised content return the same id, even across
    processes (the dedup gate is the ``yaml_content_hash`` unique
    index).

    Parameters
    ----------
    yaml_text : str
        Raw YAML text.  Parsed via ``yaml.safe_load``.
    yaml_path : str
        Path the YAML was loaded from.  Stored as metadata for human
        debugging; NOT part of the dedup key.
    conn : Connection
        SQLAlchemy connection.  The caller owns the transaction.

    Returns
    -------
    int
        The ``methodology_versions.id`` for this content.
    """
    parsed = yaml.safe_load(yaml_text)
    if parsed is None:
        raise ValueError("YAML content is empty / null at the top level")
    if not isinstance(parsed, dict):
        raise ValueError(
            f"YAML root must be a mapping, got {type(parsed).__name__}"
        )

    canonical = canonicalize_yaml_content(parsed)
    content_hash = _hash_yaml_content(canonical)

    # Fast in-process cache hit first.
    with _yaml_cache_lock:
        cached = _yaml_hash_to_id.get(content_hash)
    if cached is not None:
        return cached

    # INSERT ON CONFLICT DO NOTHING.  The dedup is the
    # ``uq_methodology_versions_content_hash`` unique index from PR 4.
    # We then SELECT in a follow-up to recover the id whether we
    # inserted or someone else did.
    inserted = conn.execute(
        text(
            f"""
            INSERT INTO {_COPILOT_STATE_SCHEMA}.methodology_versions (
                yaml_path, yaml_content_hash, yaml_content
            )
            VALUES (
                :yaml_path, :yaml_content_hash,
                CAST(:yaml_content AS JSONB)
            )
            ON CONFLICT (yaml_content_hash) DO NOTHING
            RETURNING id
            """
        ),
        {
            "yaml_path": yaml_path,
            "yaml_content_hash": content_hash,
            "yaml_content": _canonical_json(canonical),
        },
    ).first()

    if inserted is not None:
        version_id = int(inserted[0])
        logger.debug(
            "register_yaml: inserted %s (id=%d, path=%s)",
            content_hash[:12], version_id, yaml_path,
        )
    else:
        # Someone else's transaction beat us OR the row already existed.
        # Fetch the id.
        row = conn.execute(
            text(
                f"""
                SELECT id FROM {_COPILOT_STATE_SCHEMA}.methodology_versions
                WHERE yaml_content_hash = :h
                """
            ),
            {"h": content_hash},
        ).first()
        if row is None:  # pragma: no cover — would require a concurrent delete
            raise RuntimeError(
                f"INSERT skipped but SELECT found no row for "
                f"yaml_content_hash {content_hash[:12]}... — concurrent "
                "delete on methodology_versions?  This should not happen "
                "in normal operation."
            )
        version_id = int(row[0])
        logger.debug(
            "register_yaml: existing id %d for %s",
            version_id, content_hash[:12],
        )

    with _yaml_cache_lock:
        _yaml_hash_to_id[content_hash] = version_id
    return version_id


def get_yaml(version_id: int, *, conn: Connection) -> MethodologyVersionRecord:
    """Load a methodology_versions row by id.

    Used by the workspace replay route when reconstructing the
    original ``ToolConfig``: feed ``record.yaml_content`` directly
    to ``ToolConfig.model_validate(...)``.

    Raises ``KeyError`` if the id does not exist.
    """
    row = conn.execute(
        text(
            f"""
            SELECT id, yaml_path, yaml_content_hash, yaml_content
            FROM {_COPILOT_STATE_SCHEMA}.methodology_versions
            WHERE id = :id
            """
        ),
        {"id": version_id},
    ).mappings().first()
    if row is None:
        raise KeyError(f"No methodology_versions row with id {version_id}")
    return MethodologyVersionRecord(
        id=int(row["id"]),
        yaml_path=row["yaml_path"],
        yaml_content_hash=row["yaml_content_hash"],
        yaml_content=row["yaml_content"],
    )


def get_yaml_by_hash(
    content_hash: str, *, conn: Connection,
) -> Optional[MethodologyVersionRecord]:
    """Look up by ``yaml_content_hash``.  Returns None when absent.

    Used by tests that want to assert a particular YAML was
    registered without knowing the auto-incremented id.
    """
    row = conn.execute(
        text(
            f"""
            SELECT id, yaml_path, yaml_content_hash, yaml_content
            FROM {_COPILOT_STATE_SCHEMA}.methodology_versions
            WHERE yaml_content_hash = :h
            """
        ),
        {"h": content_hash},
    ).mappings().first()
    if row is None:
        return None
    return MethodologyVersionRecord(
        id=int(row["id"]),
        yaml_path=row["yaml_path"],
        yaml_content_hash=row["yaml_content_hash"],
        yaml_content=row["yaml_content"],
    )


# ============================================================================
# Application-version registry
# ============================================================================


def _detect_current_git_commit() -> str:
    """Resolve the current code revision's git SHA.

    Resolution order:

      1. ``MACRO_COPILOT_GIT_COMMIT`` env var — set by container
         entry-points / CI workflows that bake the SHA in at image
         build time.  Honoured first so a process started without
         a workspace (no ``.git`` directory) still records a real
         commit.
      2. ``git rev-parse HEAD`` — used during local dev + the CI
         workflows that have a checkout.
      3. ``_UNKNOWN_COMMIT_SENTINEL`` — fallback.  Returned only
         when both above fail.  The artifact store still stamps
         this id; the workspace replay route surfaces it as
         "application version unknown" in the response header.

    Validation: the returned string MUST be exactly 40 hex
    characters (the DB column's CHECK constraint).  Anything else
    is rejected and we fall through to the next strategy.
    """
    env_commit = os.environ.get("MACRO_COPILOT_GIT_COMMIT")
    if env_commit and _looks_like_sha(env_commit):
        return env_commit.lower()

    try:
        # The git command runs from this module's directory.  The
        # codebase always lives under a checkout in production usage;
        # if it doesn't, the subprocess errors out cleanly and we
        # fall through to the sentinel.
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            check=True,
            timeout=5.0,
        )
        sha = result.stdout.decode("utf-8", errors="replace").strip()
        if _looks_like_sha(sha):
            return sha.lower()
    except Exception as exc:
        logger.debug("git rev-parse HEAD failed: %s", exc)

    logger.info(
        "could not resolve current git commit; using unknown-sentinel "
        "(MACRO_COPILOT_GIT_COMMIT not set and git rev-parse failed)"
    )
    return _UNKNOWN_COMMIT_SENTINEL


def _looks_like_sha(s: str) -> bool:
    if not isinstance(s, str) or len(s) != _GIT_SHA_LEN:
        return False
    try:
        int(s, 16)
    except ValueError:
        return False
    return True


def register_application_version(
    git_commit: str,
    *,
    conn: Connection,
    notes: Optional[str] = None,
) -> int:
    """Register a git_commit and return its ``application_version.id``.

    Idempotent under ``git_commit`` (UNIQUE).  In-process cached.
    """
    if not _looks_like_sha(git_commit):
        raise ValueError(
            f"git_commit must be 40 hex chars, got {git_commit!r} "
            f"(len={len(git_commit)})"
        )
    normalised = git_commit.lower()

    with _app_version_cache_lock:
        cached = _git_commit_to_id.get(normalised)
    if cached is not None:
        return cached

    inserted = conn.execute(
        text(
            f"""
            INSERT INTO {_COPILOT_STATE_SCHEMA}.application_version (
                git_commit, notes
            )
            VALUES (:git_commit, :notes)
            ON CONFLICT (git_commit) DO NOTHING
            RETURNING id
            """
        ),
        {"git_commit": normalised, "notes": notes},
    ).first()

    if inserted is not None:
        version_id = int(inserted[0])
    else:
        row = conn.execute(
            text(
                f"""
                SELECT id FROM {_COPILOT_STATE_SCHEMA}.application_version
                WHERE git_commit = :h
                """
            ),
            {"h": normalised},
        ).first()
        if row is None:  # pragma: no cover
            raise RuntimeError(
                f"application_version row for {normalised!r} vanished."
            )
        version_id = int(row[0])

    with _app_version_cache_lock:
        _git_commit_to_id[normalised] = version_id
    return version_id


def current_application_version_id(*, conn: Connection) -> int:
    """Resolve + register the current process's git commit and
    return its ``application_version.id``.

    Resolution is cached per-process (a long-running server does
    one ``git rev-parse`` ever).  Registration uses the
    in-process cache too, so steady-state cost is zero round-trips.
    """
    global _current_process_commit
    with _current_process_commit_lock:
        if _current_process_commit is None:
            _current_process_commit = _detect_current_git_commit()
        commit = _current_process_commit
    notes = (
        "Resolved via _UNKNOWN_COMMIT_SENTINEL — see "
        "state.methodology_versions._detect_current_git_commit"
        if commit == _UNKNOWN_COMMIT_SENTINEL else None
    )
    return register_application_version(commit, conn=conn, notes=notes)


def get_application_version(
    version_id: int, *, conn: Connection,
) -> ApplicationVersionRecord:
    """Load an application_version row by id.  Used by the workspace
    replay route to render the produced-under-commit header.

    Raises ``KeyError`` if the id does not exist.
    """
    row = conn.execute(
        text(
            f"""
            SELECT id, git_commit
            FROM {_COPILOT_STATE_SCHEMA}.application_version
            WHERE id = :id
            """
        ),
        {"id": version_id},
    ).mappings().first()
    if row is None:
        raise KeyError(f"No application_version row with id {version_id}")
    return ApplicationVersionRecord(
        id=int(row["id"]),
        git_commit=row["git_commit"],
    )


__all__ = [
    "MethodologyVersionRecord",
    "ApplicationVersionRecord",
    "canonicalize_yaml_content",
    "register_yaml",
    "get_yaml",
    "get_yaml_by_hash",
    "register_application_version",
    "current_application_version_id",
    "get_application_version",
    "clear_caches",
]
