"""state.object_storage — pluggable backend for artifact payload blobs.

Large artifact payloads (rows > inline threshold, or serialized JSON
> 8KB) live outside Postgres in object storage.  This module defines
the ``ObjectStorageBackend`` Protocol and two implementations:

  - ``LocalFSBackend``  — writes to a configurable local directory.
                          Default backend; suitable for dev + CI.
  - ``GCSBackend``      — writes to Google Cloud Storage.  Mirrors the
                          existing ``utils.historical_extractor`` GCS
                          auth pattern.

Backend selection happens at app startup via
``state.schemas.ObjectStorageConfig``; the API server's lifespan
constructs the chosen backend once and exposes it through
``api.dependencies.get_object_storage``.

URI conventions
---------------
All backends emit content-addressed URIs of the same shape:

  - LocalFSBackend  →  ``file://{root_abspath}/{hash[:2]}/{hash[2:]}.bin``
  - GCSBackend      →  ``gs://{bucket}/{prefix}/{hash[:2]}/{hash[2:]}.bin``

The two-char ``hash[:2]`` prefix gives filesystem-level fan-out — a
single directory full of 100K files is slow to ``ls``; fan-out keeps
each leaf directory under ~400 entries even at 100K artifacts.

Idempotency contract
--------------------
``put_bytes(hash, content)`` is idempotent: re-putting the same hash
with the same content is a no-op (or an overwrite with identical
bytes; either is acceptable since artifacts are content-addressed).
This is what makes ``put_artifact`` safe to retry — an interrupted
write that already pushed the blob doesn't leave the system in an
inconsistent state, because re-running the put produces the same
URI and the same metadata row.

Phase 0 PR 7.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger("state.object_storage")


# Length of the SHA-256 hex digest used as the artifact key.  Mirrors
# the value used by ``copilot_state.artifact_metadata.hash`` (CHAR(64)
# CHECK length=64).  Declared here so the storage layer can validate
# input hashes without importing from the migration module.
HASH_LEN = 64


# ============================================================================
# Protocol
# ============================================================================


@runtime_checkable
class ObjectStorageBackend(Protocol):
    """Pluggable backend for artifact payload blobs.

    Implementations MUST satisfy:

      1. Content-addressed by ``hash``.  ``put_bytes`` derives the
         URI deterministically from the hash so that re-putting the
         same hash yields the same URI.

      2. ``put_bytes`` is idempotent.  Re-putting the same hash with
         the same content does not error (it may overwrite, but the
         post-condition is the same bytes at the same URI).

      3. ``get_bytes(uri)`` returns the bytes most recently written
         to that URI, or raises ``FileNotFoundError`` if no bytes
         are there.

      4. ``delete(uri)`` removes the bytes; idempotent (deleting a
         missing URI is a no-op, not an error).
    """

    def put_bytes(self, hash: str, content: bytes) -> str:
        """Write ``content`` keyed by ``hash``.  Return the URI."""
        ...

    def get_bytes(self, uri: str) -> bytes:
        """Return the bytes at ``uri``.

        Raises:
            FileNotFoundError: ``uri`` does not exist.
        """
        ...

    def delete(self, uri: str) -> None:
        """Remove the bytes at ``uri``.  Idempotent."""
        ...


# ============================================================================
# Helpers
# ============================================================================


def _validate_hash(hash: str) -> None:
    """Reject malformed hashes loudly rather than producing a confusing
    "file not found" error downstream.

    A valid hash is a 64-character lowercase hex string (the SHA-256
    digest form used throughout this project).
    """
    if not isinstance(hash, str):
        raise TypeError(f"hash must be str, got {type(hash).__name__}")
    if len(hash) != HASH_LEN:
        raise ValueError(
            f"hash must be exactly {HASH_LEN} hex chars; got length {len(hash)}"
        )
    if not all(c in "0123456789abcdef" for c in hash):
        raise ValueError(
            f"hash must be lowercase hex; got {hash!r}"
        )


def _hash_to_relpath(hash: str) -> str:
    """Two-char fan-out: ``ab/cdef...`` instead of one flat directory.

    Reduces per-directory entry counts at scale.  Same shape for both
    LocalFS and GCS backends so URIs are visually parseable across
    deployments.
    """
    return f"{hash[:2]}/{hash[2:]}.bin"


# ============================================================================
# LocalFSBackend
# ============================================================================


class LocalFSBackend:
    """Writes artifact blobs to a local directory.

    Default backend for dev + CI.  The root directory is created on
    first ``put_bytes`` if it doesn't exist; no separate setup step
    is required.

    Example:
        >>> backend = LocalFSBackend(root="/tmp/macro_copilot_artifacts")
        >>> uri = backend.put_bytes("abc...64chars", b"hello")
        >>> backend.get_bytes(uri)
        b'hello'
    """

    def __init__(self, root: str | os.PathLike[str]):
        self._root = Path(root).resolve()

    @property
    def root(self) -> Path:
        return self._root

    def put_bytes(self, hash: str, content: bytes) -> str:
        _validate_hash(hash)
        relpath = _hash_to_relpath(hash)
        full_path = self._root / relpath
        full_path.parent.mkdir(parents=True, exist_ok=True)

        # Write to a sibling tempfile + rename for atomicity.  This
        # prevents a partial write from being visible to a concurrent
        # reader (e.g. if two callers race on the same hash, neither
        # ever sees a half-written file).
        tmp_path = full_path.with_suffix(full_path.suffix + ".tmp")
        try:
            tmp_path.write_bytes(content)
            os.replace(tmp_path, full_path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        uri = full_path.as_uri()  # ``file:///absolute/path/...``
        logger.debug("LocalFSBackend put %s (%d bytes)", uri, len(content))
        return uri

    def get_bytes(self, uri: str) -> bytes:
        path = self._uri_to_path(uri)
        if not path.exists():
            raise FileNotFoundError(f"No artifact at {uri}")
        return path.read_bytes()

    def delete(self, uri: str) -> None:
        path = self._uri_to_path(uri)
        try:
            path.unlink()
        except FileNotFoundError:
            # Idempotent: deleting a missing artifact is a no-op,
            # consistent with the contract documented on the
            # Protocol.
            return
        logger.debug("LocalFSBackend deleted %s", uri)

    def _uri_to_path(self, uri: str) -> Path:
        if not uri.startswith("file://"):
            raise ValueError(
                f"LocalFSBackend can only resolve file:// URIs; got {uri!r}"
            )
        # ``Path.from_uri`` exists in 3.13+; fall back to manual parse
        # for 3.11 / 3.12 compatibility.
        return Path(uri.removeprefix("file://"))


# ============================================================================
# GCSBackend
# ============================================================================


class GCSBackend:
    """Writes artifact blobs to a Google Cloud Storage bucket.

    Mirrors the auth pattern used by ``utils.historical_extractor``:
    expects ``google.cloud.storage`` to be installed and either
    ``GOOGLE_APPLICATION_CREDENTIALS`` to point at a service-account
    JSON or the runtime environment to provide application-default
    credentials (e.g. a GCE / GKE workload identity).

    The bucket is NOT created automatically — the operator is
    expected to provision it (typically the same bucket used by
    parquet ingestion, with a separate key prefix to avoid
    collisions).
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "artifacts",
        *,
        client: Optional[object] = None,
    ):
        """``client`` is for test injection: pass a stubbed
        ``google.cloud.storage.Client`` to avoid hitting real GCS.
        Production callers leave it None and let the backend
        construct its own client lazily (deferred until first
        ``put_bytes`` so module import doesn't require GCP creds).
        """
        if not bucket:
            raise ValueError("GCSBackend requires a non-empty bucket name")
        self._bucket_name = bucket
        self._prefix = prefix.strip("/")
        self._client = client
        self._bucket = None  # lazy

    @property
    def bucket_name(self) -> str:
        return self._bucket_name

    @property
    def prefix(self) -> str:
        return self._prefix

    def _ensure_bucket(self):
        if self._bucket is not None:
            return self._bucket
        if self._client is None:
            # Lazy import keeps the module load-light when GCS isn't
            # in use (e.g. CI runs the artifact-store tests on
            # LocalFSBackend only).
            from google.cloud import storage  # noqa: WPS433

            self._client = storage.Client()
        self._bucket = self._client.bucket(self._bucket_name)
        return self._bucket

    def _hash_to_key(self, hash: str) -> str:
        relpath = _hash_to_relpath(hash)
        return f"{self._prefix}/{relpath}" if self._prefix else relpath

    def put_bytes(self, hash: str, content: bytes) -> str:
        _validate_hash(hash)
        bucket = self._ensure_bucket()
        key = self._hash_to_key(hash)
        blob = bucket.blob(key)
        # GCS uploads are atomic from the client's perspective — the
        # bytes only become visible after the upload completes.
        blob.upload_from_string(content)
        uri = f"gs://{self._bucket_name}/{key}"
        logger.debug("GCSBackend put %s (%d bytes)", uri, len(content))
        return uri

    def get_bytes(self, uri: str) -> bytes:
        bucket_name, key = _parse_gs_uri(uri)
        if bucket_name != self._bucket_name:
            raise ValueError(
                f"URI bucket {bucket_name!r} does not match this "
                f"backend's bucket {self._bucket_name!r}"
            )
        bucket = self._ensure_bucket()
        blob = bucket.blob(key)
        if not blob.exists():
            raise FileNotFoundError(f"No artifact at {uri}")
        return blob.download_as_bytes()

    def delete(self, uri: str) -> None:
        bucket_name, key = _parse_gs_uri(uri)
        if bucket_name != self._bucket_name:
            raise ValueError(
                f"URI bucket {bucket_name!r} does not match this "
                f"backend's bucket {self._bucket_name!r}"
            )
        bucket = self._ensure_bucket()
        blob = bucket.blob(key)
        try:
            blob.delete()
        except Exception as exc:
            # google.cloud.storage raises NotFound (a subclass of
            # google.api_core.exceptions.GoogleAPIError) on missing
            # objects.  Treat as the documented idempotent no-op.
            if "404" in str(exc) or "not found" in str(exc).lower():
                return
            raise
        logger.debug("GCSBackend deleted %s", uri)


def _parse_gs_uri(uri: str) -> tuple[str, str]:
    """Split ``gs://bucket/key/path`` into ``(bucket, key/path)``."""
    if not uri.startswith("gs://"):
        raise ValueError(
            f"GCSBackend can only resolve gs:// URIs; got {uri!r}"
        )
    rest = uri.removeprefix("gs://")
    if "/" not in rest:
        raise ValueError(f"Malformed gs URI (missing key): {uri!r}")
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        raise ValueError(f"Malformed gs URI: {uri!r}")
    return bucket, key


# ============================================================================
# Backend construction from config
# ============================================================================


def build_backend(config) -> ObjectStorageBackend:
    """Construct a backend from an ``ObjectStorageConfig`` instance.

    Centralised here so ``api.dependencies`` doesn't sprout
    isinstance-checks on config types.
    """
    if config.backend == "localfs":
        if not config.local_root:
            raise ValueError(
                "ObjectStorageConfig.local_root is required for "
                "backend='localfs'"
            )
        return LocalFSBackend(root=config.local_root)
    if config.backend == "gcs":
        if not config.gcs_bucket:
            raise ValueError(
                "ObjectStorageConfig.gcs_bucket is required for "
                "backend='gcs'"
            )
        return GCSBackend(bucket=config.gcs_bucket, prefix=config.gcs_prefix)
    raise ValueError(f"Unknown ObjectStorageConfig.backend: {config.backend!r}")


__all__ = [
    "HASH_LEN",
    "ObjectStorageBackend",
    "LocalFSBackend",
    "GCSBackend",
    "build_backend",
]
