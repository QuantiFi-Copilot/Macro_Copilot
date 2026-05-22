"""state.cache — bytes cache in front of the artifact store.

Phase 0 PR 11.

What this is
------------
A read-through cache mapping ``artifact_hash → serialized bytes``.
Sits between ``state.artifact_store.get_artifact`` and the object-
storage backend so a hot artifact does not pay the object-storage
round-trip on every read.

What this is NOT
----------------
- **NOT load-bearing.**  Every cache call is wrapped so a Redis
  outage / parse error / network blip falls through to the
  object-storage path.  The artifact store NEVER returns wrong
  bytes from cache logic; the worst case is a missed cache hit.

- **NOT a write-through layer.**  ``put_artifact`` does not
  populate the cache.  The first ``get`` after a put pays the
  warmup cost.  This eliminates the cache-vs-DB-ordering question
  at write time and matches the workload (artifacts are
  hot-on-read, rarely re-written by hash).

- **NOT a metadata cache.**  Only the bytes payload of an artifact
  is cached.  Metadata reads (artifact_metadata row, lineage
  JSONB) go straight to Postgres, where the row + index cache
  is already efficient.

Backends
--------
Two implementations behind the ``ArtifactBytesCache`` Protocol:

  - ``NullCache`` — no-op default.  Used when
    ``MACRO_COPILOT_REDIS_URL`` is unset, or when the Redis init
    fails.  Every ``get`` returns None; every ``put`` is a no-op.

  - ``RedisBytesCache`` — wraps a ``redis.Redis`` client.
    Namespaces keys as ``macro_copilot:artifact:{hash}``; per-key
    TTL via ``MACRO_COPILOT_REDIS_TTL_SECONDS`` (default 86400 /
    1 day).  Server-side LRU eviction cooperates with the TTL
    (Redis ``maxmemory-policy allkeys-lru`` is the
    operator's deployment choice; this module doesn't try to
    configure it from the client side).

Configuration
-------------
Read from environment in ``build_cache_from_env()``:

  - ``MACRO_COPILOT_REDIS_URL``  — e.g. ``redis://localhost:6379/0``.
    Absent / empty → ``NullCache``.
  - ``MACRO_COPILOT_REDIS_TTL_SECONDS`` — integer seconds, default 86400.
  - ``MACRO_COPILOT_REDIS_KEY_PREFIX`` — namespace prefix, default
    ``macro_copilot:artifact``.  Overridable so two deployments
    sharing a Redis instance don't collide.

Failure mode
------------
Any exception raised by the underlying Redis client is caught and
logged at WARNING.  ``get`` returns None on error (treat as miss);
``put`` swallows on error.  The artifact store proceeds with the
object-storage path either way.  A persistent Redis outage will
degrade performance (every read becomes a full fetch) but will
NOT break correctness.
"""

from __future__ import annotations

import logging
import os
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger("state.cache")


# ============================================================================
# Constants
# ============================================================================

_DEFAULT_TTL_SECONDS = 86_400  # 1 day
_DEFAULT_KEY_PREFIX = "macro_copilot:artifact"

_ENV_URL = "MACRO_COPILOT_REDIS_URL"
_ENV_TTL = "MACRO_COPILOT_REDIS_TTL_SECONDS"
_ENV_PREFIX = "MACRO_COPILOT_REDIS_KEY_PREFIX"


# ============================================================================
# Protocol
# ============================================================================


@runtime_checkable
class ArtifactBytesCache(Protocol):
    """The cache contract the artifact store calls through.

    Every method is best-effort and MUST NOT raise into the caller.
    Implementations log + swallow backend errors.
    """

    def get(self, artifact_hash: str) -> Optional[bytes]:
        """Return the cached bytes for ``artifact_hash``, or None
        on miss / error."""
        ...

    def put(self, artifact_hash: str, content: bytes) -> None:
        """Cache ``content`` under ``artifact_hash``.  Best-effort;
        errors are swallowed."""
        ...

    def delete(self, artifact_hash: str) -> None:
        """Drop the entry for ``artifact_hash``.  No-op when absent."""
        ...

    def close(self) -> None:
        """Release any underlying client resources.  Idempotent."""
        ...


# ============================================================================
# NullCache
# ============================================================================


class NullCache:
    """No-op cache.  Default when Redis is not configured.

    Implementing the Protocol explicitly (rather than relying on
    structural typing) so type checkers and ``isinstance`` checks
    against ``ArtifactBytesCache`` both work.
    """

    def get(self, artifact_hash: str) -> Optional[bytes]:  # noqa: ARG002
        return None

    def put(self, artifact_hash: str, content: bytes) -> None:  # noqa: ARG002
        return None

    def delete(self, artifact_hash: str) -> None:  # noqa: ARG002
        return None

    def close(self) -> None:
        return None


# ============================================================================
# RedisBytesCache
# ============================================================================


class RedisBytesCache:
    """Redis-backed bytes cache.

    Lazy-imports the ``redis`` package inside ``__init__`` so
    callers that never touch this backend (the default
    ``NullCache`` path) don't pay for the import.
    """

    def __init__(
        self,
        *,
        url: str,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        key_prefix: str = _DEFAULT_KEY_PREFIX,
        client=None,  # tests can inject a fake (e.g. fakeredis)
    ):
        self._url = url
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._key_prefix = key_prefix.rstrip(":")
        if client is None:
            try:
                import redis  # local import — see class docstring
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "redis package is required for RedisBytesCache; "
                    "install with `pip install redis>=5` or unset "
                    f"{_ENV_URL} to use NullCache instead"
                ) from exc
            client = redis.Redis.from_url(url)
        self._client = client

    # ------------------------------------------------------------------
    # Keying
    # ------------------------------------------------------------------

    def _key(self, artifact_hash: str) -> str:
        return f"{self._key_prefix}:{artifact_hash}"

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def get(self, artifact_hash: str) -> Optional[bytes]:
        try:
            raw = self._client.get(self._key(artifact_hash))
        except Exception as exc:
            logger.warning(
                "cache.get failed for %s (treating as miss): %s",
                artifact_hash[:12], exc,
            )
            return None
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        # Unexpected — a typed client returned a string.  Treat as miss
        # rather than guess; logs the surprise for triage.
        logger.warning(
            "cache.get for %s returned %s, not bytes; treating as miss",
            artifact_hash[:12], type(raw).__name__,
        )
        return None

    def put(self, artifact_hash: str, content: bytes) -> None:
        if not isinstance(content, (bytes, bytearray)):
            logger.warning(
                "cache.put refused non-bytes content (%s) for %s",
                type(content).__name__, artifact_hash[:12],
            )
            return
        try:
            self._client.set(
                self._key(artifact_hash),
                bytes(content),
                ex=self._ttl_seconds,
            )
        except Exception as exc:
            logger.warning(
                "cache.put failed for %s (swallowing): %s",
                artifact_hash[:12], exc,
            )

    def delete(self, artifact_hash: str) -> None:
        try:
            self._client.delete(self._key(artifact_hash))
        except Exception as exc:
            logger.warning(
                "cache.delete failed for %s (swallowing): %s",
                artifact_hash[:12], exc,
            )

    def close(self) -> None:
        try:
            close = getattr(self._client, "close", None)
            if callable(close):
                close()
        except Exception as exc:
            logger.warning("cache.close raised (swallowing): %s", exc)


# ============================================================================
# Env-driven factory
# ============================================================================


def build_cache_from_env() -> ArtifactBytesCache:
    """Build the cache backend from environment variables.

    Returns ``NullCache`` when ``MACRO_COPILOT_REDIS_URL`` is unset
    or empty.  Returns ``RedisBytesCache`` when the URL is set
    AND the client constructs cleanly.  On construction error,
    logs the failure and falls back to ``NullCache`` — degraded
    operation, same contract as the PR 5 checkpointer pool and
    the PR 7 object-storage backend.
    """
    url = os.environ.get(_ENV_URL, "").strip()
    if not url:
        return NullCache()

    ttl_raw = os.environ.get(_ENV_TTL, "").strip()
    try:
        ttl = int(ttl_raw) if ttl_raw else _DEFAULT_TTL_SECONDS
    except ValueError:
        logger.warning(
            "%s=%r is not an int; using default %d",
            _ENV_TTL, ttl_raw, _DEFAULT_TTL_SECONDS,
        )
        ttl = _DEFAULT_TTL_SECONDS

    prefix = (
        os.environ.get(_ENV_PREFIX, "").strip() or _DEFAULT_KEY_PREFIX
    )

    try:
        return RedisBytesCache(url=url, ttl_seconds=ttl, key_prefix=prefix)
    except Exception as exc:
        logger.warning(
            "could not build RedisBytesCache for %s; falling back to "
            "NullCache: %s",
            url, exc,
        )
        return NullCache()


__all__ = [
    "ArtifactBytesCache",
    "NullCache",
    "RedisBytesCache",
    "build_cache_from_env",
]
