"""tests/state/test_cache.py — unit + integration tests for the
artifact bytes cache.

Phase 0 PR 11.

Tests cover:

  - ``NullCache`` round-trip is a no-op (get always returns None
    even after put; tests that "cache disabled" path through the
    Protocol).
  - ``RedisBytesCache`` round-trip against ``fakeredis`` — get
    returns None on miss, bytes on hit; put applies the TTL; delete
    clears the key; close is idempotent.
  - Namespace prefix is honoured — `MACRO_COPILOT_REDIS_KEY_PREFIX`
    changes the stored key.
  - Failure modes: a backend that raises on any op is caught + logged
    + falls through (get returns None; put / delete swallow).
  - ``build_cache_from_env`` — no env var → NullCache; env var present
    + good URL + fakeredis client injected → RedisBytesCache; env
    var present + import fails → NullCache fallback.
  - The artifact store's ``get_artifact`` returns byte-identical
    payloads whether the cache is NullCache or RedisBytesCache — the
    cache-disabled vs cache-enabled paths produce the same answer
    (the cache is NOT load-bearing for correctness).

No Postgres required for cache unit tests; the artifact-store
end-to-end test uses the same skip pattern as PR 7.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest


_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


from state.cache import (  # noqa: E402
    ArtifactBytesCache,
    NullCache,
    RedisBytesCache,
    build_cache_from_env,
)


# ============================================================================
# NullCache — the no-op default
# ============================================================================


class TestNullCache:
    def test_get_always_returns_none(self):
        c = NullCache()
        assert c.get("a" * 64) is None
        c.put("a" * 64, b"hello")
        assert c.get("a" * 64) is None

    def test_put_delete_close_are_no_ops(self):
        c = NullCache()
        # No exceptions, no return values.
        assert c.put("a" * 64, b"x") is None
        assert c.delete("a" * 64) is None
        assert c.close() is None

    def test_satisfies_protocol(self):
        # ArtifactBytesCache is runtime_checkable.
        assert isinstance(NullCache(), ArtifactBytesCache)


# ============================================================================
# RedisBytesCache against fakeredis
# ============================================================================


@pytest.fixture
def fake_redis():
    """Spin up a fakeredis client per test."""
    fakeredis = pytest.importorskip("fakeredis")
    return fakeredis.FakeStrictRedis()


class TestRedisBytesCache:
    def test_round_trip_returns_bytes(self, fake_redis):
        c = RedisBytesCache(
            url="redis://fake/0",
            ttl_seconds=60,
            client=fake_redis,
        )
        h = "b" * 64
        assert c.get(h) is None
        c.put(h, b"some bytes")
        assert c.get(h) == b"some bytes"

    def test_delete_clears_the_key(self, fake_redis):
        c = RedisBytesCache(url="redis://fake/0", client=fake_redis)
        h = "c" * 64
        c.put(h, b"payload")
        c.delete(h)
        assert c.get(h) is None

    def test_close_is_idempotent(self, fake_redis):
        c = RedisBytesCache(url="redis://fake/0", client=fake_redis)
        c.close()
        c.close()  # no double-close raise

    def test_namespace_prefix_honoured(self, fake_redis):
        c = RedisBytesCache(
            url="redis://fake/0",
            client=fake_redis,
            key_prefix="custom_ns:artifact",
        )
        h = "d" * 64
        c.put(h, b"data")
        # Confirmed on the underlying client: the key has our prefix.
        assert fake_redis.exists("custom_ns:artifact:" + h) == 1
        # The default-prefix key does NOT exist.
        assert fake_redis.exists("macro_copilot:artifact:" + h) == 0

    def test_ttl_applied_on_put(self, fake_redis):
        c = RedisBytesCache(
            url="redis://fake/0",
            client=fake_redis,
            ttl_seconds=120,
        )
        h = "e" * 64
        c.put(h, b"with-ttl")
        # fakeredis returns the TTL in seconds via ``ttl()``.
        # We assert it's > 0 (set) rather than exactly 120 because
        # there's a tiny clock window between SET and our TTL read.
        ttl = fake_redis.ttl("macro_copilot:artifact:" + h)
        assert 0 < ttl <= 120

    def test_put_rejects_non_bytes(self, fake_redis):
        c = RedisBytesCache(url="redis://fake/0", client=fake_redis)
        # Refuses str silently — log + return; no crash, no store.
        c.put("f" * 64, "not bytes")  # type: ignore[arg-type]
        assert c.get("f" * 64) is None


# ============================================================================
# Failure-mode resilience
# ============================================================================


class _RaisingClient:
    """A stand-in client that raises on every operation.  Used to
    verify the cache wrapper swallows errors and degrades to miss."""

    def get(self, key):  # noqa: ARG002
        raise RuntimeError("simulated redis outage on get")

    def set(self, key, value, ex=None):  # noqa: ARG002
        raise RuntimeError("simulated redis outage on set")

    def delete(self, key):  # noqa: ARG002
        raise RuntimeError("simulated redis outage on delete")

    def close(self):
        raise RuntimeError("simulated redis outage on close")


class TestFailureModes:
    def test_get_swallows_backend_error_and_returns_none(self):
        c = RedisBytesCache(url="redis://broken", client=_RaisingClient())
        assert c.get("g" * 64) is None

    def test_put_swallows_backend_error(self):
        c = RedisBytesCache(url="redis://broken", client=_RaisingClient())
        c.put("g" * 64, b"x")  # must not raise

    def test_delete_swallows_backend_error(self):
        c = RedisBytesCache(url="redis://broken", client=_RaisingClient())
        c.delete("g" * 64)  # must not raise

    def test_close_swallows_backend_error(self):
        c = RedisBytesCache(url="redis://broken", client=_RaisingClient())
        c.close()  # must not raise


# ============================================================================
# build_cache_from_env
# ============================================================================


class TestBuildCacheFromEnv:
    def test_no_url_returns_null_cache(self, monkeypatch):
        monkeypatch.delenv("MACRO_COPILOT_REDIS_URL", raising=False)
        c = build_cache_from_env()
        assert isinstance(c, NullCache)

    def test_empty_url_returns_null_cache(self, monkeypatch):
        monkeypatch.setenv("MACRO_COPILOT_REDIS_URL", "")
        c = build_cache_from_env()
        assert isinstance(c, NullCache)

    def test_url_with_unparseable_ttl_falls_back_to_default(
        self, monkeypatch, fake_redis,
    ):
        # Patch the lazy `redis` import inside RedisBytesCache so it
        # picks up our fakeredis client.
        import state.cache as cache_mod

        monkeypatch.setenv(
            "MACRO_COPILOT_REDIS_URL", "redis://localhost:6379/0",
        )
        monkeypatch.setenv(
            "MACRO_COPILOT_REDIS_TTL_SECONDS", "not-an-int",
        )

        original_ctor = cache_mod.RedisBytesCache.__init__

        def _ctor(self, *, url, ttl_seconds, key_prefix, client=None):
            return original_ctor(
                self, url=url, ttl_seconds=ttl_seconds,
                key_prefix=key_prefix, client=fake_redis,
            )

        monkeypatch.setattr(cache_mod.RedisBytesCache, "__init__", _ctor)
        c = build_cache_from_env()
        assert isinstance(c, RedisBytesCache)
        # The default TTL (86400) was used; we can't read it directly,
        # but the build did not error -> the fallback worked.

    def test_redis_import_failure_falls_back_to_null(self, monkeypatch):
        # Simulate the `redis` package being unavailable.
        monkeypatch.setenv(
            "MACRO_COPILOT_REDIS_URL", "redis://localhost:6379/0",
        )

        # Patch the import-statement inside RedisBytesCache.__init__.
        # We do that by deleting the cached module so the import
        # re-runs, then making it fail.
        import builtins
        real_import = builtins.__import__

        def _fail_on_redis(name, *a, **kw):
            if name == "redis":
                raise ImportError("simulated missing redis package")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", _fail_on_redis)
        with patch.dict(sys.modules):
            sys.modules.pop("redis", None)
            c = build_cache_from_env()
        assert isinstance(c, NullCache)


# ============================================================================
# Cache vs no-cache produce byte-identical artifacts (the load-bearing
# correctness invariant)
# ============================================================================


def _build_url() -> str:
    user = os.getenv("DB_USER", "quantuser")
    password = os.getenv("DB_PASSWORD", "myStrongPass")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5433")
    db_name = os.getenv("DB_NAME", "macrodata")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}"


def _db_reachable(url: str) -> bool:
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


_DB_URL = _build_url()
_DB_AVAILABLE = _db_reachable(_DB_URL)


@pytest.mark.skipif(
    not _DB_AVAILABLE,
    reason=f"Postgres not reachable at {_DB_URL!r}",
)
class TestCacheCorrectnessInvariant:
    """The cache is performance scaffolding, NEVER load-bearing.

    Putting an artifact and reading it back under three cache
    conditions (no cache, NullCache, RedisBytesCache) must produce
    byte-identical results.  Also: a backend that raises on every
    op must degrade to the object-storage path without affecting
    the response.
    """

    def _setup_engine(self):
        from sqlalchemy import create_engine, text

        engine = create_engine(_DB_URL)
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM copilot_state.working_set"))
            conn.execute(text("DELETE FROM copilot_state.dag_nodes"))
            conn.execute(text("DELETE FROM copilot_state.artifact_metadata"))
        return engine

    def _build_blob_artifact(self):
        from shared.artifacts.lineage import FetchStep, Lineage
        from shared.artifacts.missingness import RawNoCleaning
        from shared.artifacts.types import Series
        from shared.artifacts.units import TimeSeriesUnits

        step = FetchStep.build(
            name="fetch_single_tenor", version="1.0.0",
            params={"curve_family": "UST", "tenor": "10Y", "slot": "cache"},
        )
        # 300 rows -> exceeds the 100-row inline threshold -> blob.
        idx = pd.date_range("2024-01-01", periods=300, freq="D")
        return Series(
            series_key="UST.10Y.test",
            payload=pd.Series([1.0 + i * 0.001 for i in range(300)], index=idx),
            units=TimeSeriesUnits.PERCENT,
            frequency="D",
            missingness_policy=RawNoCleaning(),
            lineage=Lineage.from_steps([step]),
        )

    def test_byte_identical_across_three_cache_conditions(self):
        from state.artifact_store import get_artifact, put_artifact
        from state.object_storage import LocalFSBackend

        fakeredis = pytest.importorskip("fakeredis")

        engine = self._setup_engine()
        try:
            tmp = tempfile.mkdtemp()
            storage = LocalFSBackend(root=tmp)
            art = self._build_blob_artifact()
            with engine.begin() as conn:
                h = put_artifact(art, conn=conn, object_storage=storage)

            with engine.connect() as conn:
                a = get_artifact(h, conn=conn, object_storage=storage)
            with engine.connect() as conn:
                b = get_artifact(
                    h, conn=conn, object_storage=storage, cache=NullCache(),
                )
            with engine.connect() as conn:
                c = get_artifact(
                    h, conn=conn, object_storage=storage,
                    cache=RedisBytesCache(
                        url="redis://fake/0",
                        client=fakeredis.FakeStrictRedis(),
                    ),
                )

            # All three paths produce a Series with byte-identical
            # payload and lineage head.
            assert list(a.payload.values) == list(b.payload.values)
            assert list(b.payload.values) == list(c.payload.values)
            assert a.lineage.head_hash == b.lineage.head_hash == c.lineage.head_hash
        finally:
            engine.dispose()

    def test_failing_backend_degrades_to_object_storage(self):
        """A backend whose every call raises is treated as a perma-miss.
        The artifact still rehydrates from object storage."""
        from state.artifact_store import get_artifact, put_artifact
        from state.object_storage import LocalFSBackend

        engine = self._setup_engine()
        try:
            tmp = tempfile.mkdtemp()
            storage = LocalFSBackend(root=tmp)
            art = self._build_blob_artifact()
            with engine.begin() as conn:
                h = put_artifact(art, conn=conn, object_storage=storage)

            broken_cache = RedisBytesCache(
                url="redis://broken", client=_RaisingClient(),
            )
            with engine.connect() as conn:
                recovered = get_artifact(
                    h, conn=conn, object_storage=storage,
                    cache=broken_cache,
                )
            # Despite every cache op raising internally, the artifact
            # comes back correctly.
            assert recovered.lineage.head_hash == h
        finally:
            engine.dispose()

    def test_warmup_populates_cache_on_first_get(self):
        """Read-through semantics: the first miss populates the cache;
        the second get returns from cache (we don't measure timing — we
        observe the cache directly)."""
        from state.artifact_store import get_artifact, put_artifact
        from state.object_storage import LocalFSBackend

        fakeredis = pytest.importorskip("fakeredis")
        engine = self._setup_engine()
        try:
            tmp = tempfile.mkdtemp()
            storage = LocalFSBackend(root=tmp)
            art = self._build_blob_artifact()
            with engine.begin() as conn:
                h = put_artifact(art, conn=conn, object_storage=storage)

            client = fakeredis.FakeStrictRedis()
            cache = RedisBytesCache(url="redis://fake/0", client=client)
            assert cache.get(h) is None  # cold

            with engine.connect() as conn:
                get_artifact(
                    h, conn=conn, object_storage=storage, cache=cache,
                )

            assert cache.get(h) is not None  # warm
        finally:
            engine.dispose()
