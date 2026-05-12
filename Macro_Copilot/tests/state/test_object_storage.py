"""tests/state/test_object_storage.py — unit tests for the pluggable
object-storage backends.

Phase 0 PR 7.  Two backends:

  - ``LocalFSBackend``  — tested with a ``tmp_path`` fixture; covers
                          the full put / get / delete / idempotency
                          surface.
  - ``GCSBackend``      — tested with a stubbed
                          ``google.cloud.storage.Client``; verifies
                          method-call shape without hitting real GCS.

Plus the shared helpers: hash validation, two-char fan-out path,
gs:// URI parsing, ``build_backend`` config dispatch.

These are unit tests — they do NOT require Postgres.  Module is NOT
DB-skipped.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


# Make the project root importable.  ``Macro_Copilot/tests/conftest.py``
# already does this for all tests; redundant here but safe.
#
# Note on the historical naming collision: ``tests/state/`` used to
# contain an ``__init__.py`` (since pre-PR 7) which made pytest treat
# the directory as a package called ``state`` and shadowed
# ``Macro_Copilot/state/`` when imported.  Phase 0 PR 7 removed that
# ``__init__.py`` so pytest uses file-path-based module naming
# instead, freeing the ``state`` namespace for the real package.
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


from state.object_storage import (  # noqa: E402
    HASH_LEN,
    GCSBackend,
    LocalFSBackend,
    _hash_to_relpath,
    _parse_gs_uri,
    _validate_hash,
    build_backend,
)
from state.schemas import ObjectStorageConfig  # noqa: E402


VALID_HASH = "a" * HASH_LEN
OTHER_HASH = "b" * HASH_LEN


# ============================================================================
# Hash validation
# ============================================================================


class TestHashValidation:
    def test_accepts_valid_lowercase_hex(self) -> None:
        _validate_hash(VALID_HASH)  # no raise

    def test_accepts_mixed_lowercase_hex(self) -> None:
        _validate_hash("0123456789abcdef" * 4)

    @pytest.mark.parametrize(
        "bad,expected_exc",
        [
            ("", ValueError),
            ("a" * 63, ValueError),
            ("a" * 65, ValueError),
            ("A" * HASH_LEN, ValueError),  # uppercase rejected
            ("z" * HASH_LEN, ValueError),  # non-hex rejected
        ],
    )
    def test_rejects_malformed(self, bad: str, expected_exc) -> None:
        with pytest.raises(expected_exc):
            _validate_hash(bad)

    def test_rejects_non_string(self) -> None:
        with pytest.raises(TypeError):
            _validate_hash(12345)  # type: ignore[arg-type]


# ============================================================================
# Hash → relpath fan-out
# ============================================================================


class TestRelpathFanout:
    def test_two_char_prefix_directory(self) -> None:
        path = _hash_to_relpath(VALID_HASH)
        assert path.startswith("aa/")
        assert path.endswith(".bin")
        # The 62-char tail is the rest of the hash.
        assert len(path) == len("aa/") + (HASH_LEN - 2) + len(".bin")

    def test_different_hashes_different_paths(self) -> None:
        assert _hash_to_relpath(VALID_HASH) != _hash_to_relpath(OTHER_HASH)


# ============================================================================
# gs:// URI parsing
# ============================================================================


class TestGsUriParsing:
    def test_valid_uri(self) -> None:
        bucket, key = _parse_gs_uri("gs://my-bucket/path/to/blob.bin")
        assert bucket == "my-bucket"
        assert key == "path/to/blob.bin"

    @pytest.mark.parametrize(
        "bad",
        [
            "file:///tmp/x.bin",      # wrong scheme
            "gs://only-bucket",        # no key
            "gs:///just-key",          # no bucket
            "gs://",                   # empty
        ],
    )
    def test_rejects_malformed(self, bad: str) -> None:
        with pytest.raises(ValueError):
            _parse_gs_uri(bad)


# ============================================================================
# LocalFSBackend
# ============================================================================


class TestLocalFSBackend:
    def test_put_returns_file_uri(self, tmp_path: Path) -> None:
        backend = LocalFSBackend(root=tmp_path)
        uri = backend.put_bytes(VALID_HASH, b"hello")
        assert uri.startswith("file://")
        assert VALID_HASH[2:] in uri

    def test_get_round_trip(self, tmp_path: Path) -> None:
        backend = LocalFSBackend(root=tmp_path)
        uri = backend.put_bytes(VALID_HASH, b"hello world")
        assert backend.get_bytes(uri) == b"hello world"

    def test_get_nonexistent_raises_file_not_found(
        self, tmp_path: Path
    ) -> None:
        backend = LocalFSBackend(root=tmp_path)
        fake_uri = (tmp_path / "aa" / "missing.bin").as_uri()
        with pytest.raises(FileNotFoundError):
            backend.get_bytes(fake_uri)

    def test_idempotent_re_put_same_content(self, tmp_path: Path) -> None:
        backend = LocalFSBackend(root=tmp_path)
        uri1 = backend.put_bytes(VALID_HASH, b"data")
        uri2 = backend.put_bytes(VALID_HASH, b"data")
        assert uri1 == uri2
        assert backend.get_bytes(uri1) == b"data"

    def test_re_put_different_content_overwrites(
        self, tmp_path: Path
    ) -> None:
        """Same hash, different content shouldn't happen in production
        (the hash is supposed to be content-addressed) — but if a buggy
        caller tries it, the backend overwrites cleanly rather than
        leaving a half-written file."""
        backend = LocalFSBackend(root=tmp_path)
        uri = backend.put_bytes(VALID_HASH, b"first")
        backend.put_bytes(VALID_HASH, b"second")
        assert backend.get_bytes(uri) == b"second"

    def test_delete(self, tmp_path: Path) -> None:
        backend = LocalFSBackend(root=tmp_path)
        uri = backend.put_bytes(VALID_HASH, b"x")
        backend.delete(uri)
        with pytest.raises(FileNotFoundError):
            backend.get_bytes(uri)

    def test_delete_missing_is_idempotent(self, tmp_path: Path) -> None:
        backend = LocalFSBackend(root=tmp_path)
        fake_uri = (tmp_path / "aa" / "missing.bin").as_uri()
        # Should not raise.
        backend.delete(fake_uri)
        backend.delete(fake_uri)  # second call also no-op

    def test_rejects_non_file_uri(self, tmp_path: Path) -> None:
        backend = LocalFSBackend(root=tmp_path)
        with pytest.raises(ValueError):
            backend.get_bytes("gs://bucket/key.bin")

    def test_put_rejects_invalid_hash(self, tmp_path: Path) -> None:
        backend = LocalFSBackend(root=tmp_path)
        with pytest.raises(ValueError):
            backend.put_bytes("not-a-valid-hash", b"x")

    def test_two_hashes_with_same_prefix_coexist(
        self, tmp_path: Path
    ) -> None:
        """``aa/<tail1>.bin`` and ``aa/<tail2>.bin`` live happily in
        the same fan-out directory."""
        backend = LocalFSBackend(root=tmp_path)
        h1 = "aa" + "1" * (HASH_LEN - 2)
        h2 = "aa" + "2" * (HASH_LEN - 2)
        u1 = backend.put_bytes(h1, b"one")
        u2 = backend.put_bytes(h2, b"two")
        assert u1 != u2
        assert backend.get_bytes(u1) == b"one"
        assert backend.get_bytes(u2) == b"two"


# ============================================================================
# GCSBackend — stubbed client
# ============================================================================


class _FakeBlob:
    """Minimal stand-in for ``google.cloud.storage.Blob``."""

    def __init__(self, key: str, store: dict):
        self._key = key
        self._store = store

    def upload_from_string(self, content: bytes) -> None:
        self._store[self._key] = content

    def download_as_bytes(self) -> bytes:
        if self._key not in self._store:
            raise FileNotFoundError(f"not in store: {self._key}")
        return self._store[self._key]

    def exists(self) -> bool:
        return self._key in self._store

    def delete(self) -> None:
        if self._key not in self._store:
            from google.api_core.exceptions import NotFound

            raise NotFound("404")
        del self._store[self._key]


class _FakeBucket:
    def __init__(self, name: str, store: dict):
        self.name = name
        self._store = store

    def blob(self, key: str) -> _FakeBlob:
        return _FakeBlob(key, self._store)


class _FakeGCSClient:
    """Drop-in for ``google.cloud.storage.Client`` in tests."""

    def __init__(self) -> None:
        self._buckets: dict = {}

    def bucket(self, name: str) -> _FakeBucket:
        if name not in self._buckets:
            self._buckets[name] = (name, {})
        bucket_name, store = self._buckets[name]
        return _FakeBucket(bucket_name, store)


class TestGCSBackend:
    """Stubbed-client tests.  Verifies method shape + URI format
    without hitting real GCS."""

    def test_put_returns_gs_uri(self) -> None:
        backend = GCSBackend(
            bucket="test-bucket", prefix="artifacts", client=_FakeGCSClient()
        )
        uri = backend.put_bytes(VALID_HASH, b"hi")
        assert uri == (
            f"gs://test-bucket/artifacts/{VALID_HASH[:2]}/"
            f"{VALID_HASH[2:]}.bin"
        )

    def test_round_trip(self) -> None:
        backend = GCSBackend(
            bucket="test-bucket", prefix="artifacts", client=_FakeGCSClient()
        )
        uri = backend.put_bytes(VALID_HASH, b"hello")
        assert backend.get_bytes(uri) == b"hello"

    def test_delete(self) -> None:
        backend = GCSBackend(
            bucket="test-bucket", prefix="artifacts", client=_FakeGCSClient()
        )
        uri = backend.put_bytes(VALID_HASH, b"x")
        backend.delete(uri)
        with pytest.raises(FileNotFoundError):
            backend.get_bytes(uri)

    def test_delete_missing_is_idempotent(self) -> None:
        backend = GCSBackend(
            bucket="test-bucket", prefix="artifacts", client=_FakeGCSClient()
        )
        # Synthesize a URI for a hash that was never put.
        uri = f"gs://test-bucket/artifacts/{VALID_HASH[:2]}/{VALID_HASH[2:]}.bin"
        backend.delete(uri)  # no raise

    def test_get_missing_raises_file_not_found(self) -> None:
        backend = GCSBackend(
            bucket="test-bucket", prefix="artifacts", client=_FakeGCSClient()
        )
        uri = f"gs://test-bucket/artifacts/{VALID_HASH[:2]}/{VALID_HASH[2:]}.bin"
        with pytest.raises(FileNotFoundError):
            backend.get_bytes(uri)

    def test_wrong_bucket_rejected(self) -> None:
        backend = GCSBackend(
            bucket="my-bucket", prefix="artifacts", client=_FakeGCSClient()
        )
        with pytest.raises(ValueError):
            backend.get_bytes(f"gs://other-bucket/x/{VALID_HASH}.bin")

    def test_empty_bucket_name_rejected(self) -> None:
        with pytest.raises(ValueError):
            GCSBackend(bucket="", prefix="x")

    def test_empty_prefix_works(self) -> None:
        backend = GCSBackend(
            bucket="b", prefix="", client=_FakeGCSClient()
        )
        uri = backend.put_bytes(VALID_HASH, b"x")
        # No double-slash when prefix is empty.
        assert uri == f"gs://b/{VALID_HASH[:2]}/{VALID_HASH[2:]}.bin"


# ============================================================================
# build_backend dispatch
# ============================================================================


class TestBuildBackend:
    def test_localfs_from_config(self, tmp_path: Path) -> None:
        cfg = ObjectStorageConfig(backend="localfs", local_root=str(tmp_path))
        backend = build_backend(cfg)
        assert isinstance(backend, LocalFSBackend)
        assert backend.root == tmp_path

    def test_localfs_requires_local_root(self) -> None:
        cfg = ObjectStorageConfig(backend="localfs", local_root=None)
        with pytest.raises(ValueError, match="local_root"):
            build_backend(cfg)

    def test_gcs_from_config(self) -> None:
        cfg = ObjectStorageConfig(
            backend="gcs", gcs_bucket="my-bucket", gcs_prefix="prod"
        )
        backend = build_backend(cfg)
        assert isinstance(backend, GCSBackend)
        assert backend.bucket_name == "my-bucket"
        assert backend.prefix == "prod"

    def test_gcs_requires_bucket(self) -> None:
        cfg = ObjectStorageConfig(backend="gcs", gcs_bucket=None)
        with pytest.raises(ValueError, match="gcs_bucket"):
            build_backend(cfg)


# ============================================================================
# Stubs for google.api_core to avoid hard dependency in CI when
# google-cloud-storage is installed (for the .delete() NotFound test)
# ============================================================================


@pytest.fixture(autouse=True)
def _maybe_stub_google_api_core(monkeypatch):
    """Some GCSBackend tests reference ``google.api_core.exceptions.NotFound``
    in the fake-blob delete path.  When google-cloud-storage is
    installed in the dev env, this module exists.  When it's not (CI
    state-layer job intentionally doesn't depend on it), stub it.
    """
    try:
        import google.api_core.exceptions  # noqa: F401
    except ImportError:
        import types

        fake_pkg = types.ModuleType("google")
        fake_api_core = types.ModuleType("google.api_core")
        fake_excs = types.ModuleType("google.api_core.exceptions")

        class NotFound(Exception):
            pass

        fake_excs.NotFound = NotFound
        fake_api_core.exceptions = fake_excs
        fake_pkg.api_core = fake_api_core

        monkeypatch.setitem(sys.modules, "google", fake_pkg)
        monkeypatch.setitem(sys.modules, "google.api_core", fake_api_core)
        monkeypatch.setitem(sys.modules, "google.api_core.exceptions", fake_excs)
    yield
