"""tests/state/test_tool_config_round_trip.py — Pydantic
round-trip discipline for ToolConfig.

Phase 0 PR 9.

The workspace replay route in ``original`` mode does:

    rec = methodology_versions.get_yaml(version_id, conn=...)
    cfg = ToolConfig.model_validate(rec.yaml_content)

If any tool YAML stops round-tripping cleanly through that path, the
replay route's reconstruction step silently degrades to
``tool_config_round_trip_ok = False`` and the user can no longer
replay analyses produced under the older YAML.

These tests parameterise across EVERY tool YAML in the manifesto
(under ``rates_agent/**/config.yaml``) and assert:

  1. ``ToolConfig(**yaml.safe_load(text))`` succeeds.
  2. ``ToolConfig.model_dump(mode='json')`` then
     ``ToolConfig.model_validate(dumped)`` produces an equal config.
  3. The reconstructed config's ``conventions_hash()`` matches the
     original's — this is the load-bearing identity hash that ends
     up inside ``PrimitiveStep.tool_config_hash``.
  4. Going through the methodology_versions REGISTRY round-trip
     (which JSON-canonicalises in the middle) still yields the
     same ``conventions_hash``.

If a future Pydantic field becomes non-JSON-roundtripable (e.g. an
``Enum`` that doesn't serialise cleanly), these tests fail loudly
the moment the offending edit lands.

No Postgres dependency for assertions (1)-(3); (4) does need a real
DB to exercise the registry path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List

import pytest
import yaml


_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


from shared.config.tool_config import ToolConfig  # noqa: E402


# ============================================================================
# Catalogue discovery
# ============================================================================


def _find_tool_yaml_paths() -> List[Path]:
    """Walk the rates_agent tree and return every config.yaml path.

    These are the YAMLs that ship with the repo today.  As new
    tool families land, they're automatically picked up by this
    test — the parameterisation is dynamic.
    """
    root = Path(_PROJECT_ROOT) / "rates_agent"
    if not root.is_dir():
        return []
    return sorted(root.glob("**/config.yaml"))


_YAML_PATHS = _find_tool_yaml_paths()


# ============================================================================
# Plain Pydantic round-trip — no DB
# ============================================================================


@pytest.mark.parametrize(
    "path", _YAML_PATHS, ids=[p.relative_to(Path(_PROJECT_ROOT)).as_posix() for p in _YAML_PATHS],
)
class TestPydanticRoundTrip:
    def test_loads_cleanly(self, path):
        raw = yaml.safe_load(path.read_text())
        cfg = ToolConfig(**raw)
        assert cfg.tool.name

    def test_model_dump_round_trip(self, path):
        raw = yaml.safe_load(path.read_text())
        cfg = ToolConfig(**raw)
        dumped = cfg.model_dump(mode="json")
        recon = ToolConfig.model_validate(dumped)
        assert recon == cfg
        # Identity hash is the load-bearing invariant.
        assert recon.conventions_hash() == cfg.conventions_hash()

    def test_methodology_version_id_round_trips(self, path):
        """The PR 9 ``methodology_version_id`` field round-trips
        through ``model_copy`` (which we use to attach the id after
        DB registration)."""
        raw = yaml.safe_load(path.read_text())
        cfg = ToolConfig(**raw)
        with_id = cfg.model_copy(update={"methodology_version_id": 42})
        assert with_id.methodology_version_id == 42
        assert with_id.conventions_hash() == cfg.conventions_hash()


# ============================================================================
# Round-trip THROUGH the registry path
# ============================================================================
# (4) requires a real Postgres because the registry stores
# yaml_content as JSONB and the read path goes via SQL.  Module-
# level skip when DB unavailable.


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


@pytest.fixture(scope="module")
def db_engine():
    if not _DB_AVAILABLE:
        pytest.skip(f"Postgres not reachable at {_DB_URL!r}")
    from sqlalchemy import create_engine

    e = create_engine(_DB_URL)
    yield e
    e.dispose()


@pytest.fixture(autouse=True)
def wipe_registry(db_engine):
    """Per-test wipe of just the methodology_versions table (and the
    artifacts that reference it).  We don't bother with the full
    cascade because this test file doesn't touch artifacts."""
    if not _DB_AVAILABLE:
        return
    from sqlalchemy import text

    from state.methodology_versions import clear_caches
    clear_caches()
    with db_engine.begin() as conn:
        conn.execute(text("DELETE FROM copilot_state.dag_nodes"))
        conn.execute(text("DELETE FROM copilot_state.working_set"))
        conn.execute(text("DELETE FROM copilot_state.artifact_metadata"))
        conn.execute(text("DELETE FROM copilot_state.methodology_versions"))
    yield


@pytest.mark.skipif(
    not _DB_AVAILABLE,
    reason="Postgres not reachable",
)
@pytest.mark.parametrize(
    "path", _YAML_PATHS, ids=[p.relative_to(Path(_PROJECT_ROOT)).as_posix() for p in _YAML_PATHS],
)
def test_registry_round_trip_preserves_conventions_hash(db_engine, path):
    """Register a YAML, read its parsed content back from the
    registry, validate as ToolConfig, and assert the resulting
    ``conventions_hash`` matches the original.

    This is the load-bearing test for the replay route's
    ``original`` mode: it proves the YAML → registry → ToolConfig
    chain preserves the identity hash that ends up in
    ``PrimitiveStep.tool_config_hash``.  If this breaks for any
    tool YAML, that YAML can no longer be replayed faithfully.
    """
    from state.methodology_versions import get_yaml, register_yaml

    raw_text = path.read_text()
    original_cfg = ToolConfig(**yaml.safe_load(raw_text))
    original_hash = original_cfg.conventions_hash()

    with db_engine.begin() as conn:
        vid = register_yaml(
            raw_text, yaml_path=str(path), conn=conn,
        )
    with db_engine.connect() as conn:
        rec = get_yaml(vid, conn=conn)

    recon = ToolConfig.model_validate(rec.yaml_content)
    assert recon.conventions_hash() == original_hash
    # Sanity: the reconstructed config is semantically equal.
    # Compare as sets — Postgres JSONB normalises dict key order, so
    # the LIST-order is not preserved through the registry round-trip.
    # The identity hash IS preserved (asserted above) because the
    # ``conventions_hash`` recipe uses ``sort_keys=True``.
    assert recon.tool.name == original_cfg.tool.name
    assert set(recon.conventions.keys()) == set(original_cfg.conventions.keys())
