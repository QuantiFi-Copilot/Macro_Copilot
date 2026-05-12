"""tests/state/test_methodology_pinning.py — end-to-end spec test
for methodology version pinning.

Phase 0 PR 9.

This is the canonical test from the brief:

  > Change a YAML convention (z_score_window_days: 252 -> 200),
  > run the same workspace under both modes, assert different
  > artifacts under current mode, identical artifacts under
  > original mode.

What the test covers
--------------------
1. Build an artifact under YAML v1 (window=252).  Record:
   - The artifact hash H_v1.
   - The methodology_version_id M_v1 that backs this artifact.
   - The application_version_id pinned at put time.
2. Mutate the YAML on disk (window 252 -> 200).
3. Hit the workspace replay route under both modes:
   - ``?mode=current``:  asserts a methodology_diff is reported and
     names ``conventions.z_score_window_days`` as the changed field.
   - ``?mode=original``: asserts the YAML can be reconstructed from
     the registry, the round-trip into ToolConfig succeeds, and the
     reconstructed ``tool_config_hash`` matches the one pinned in
     the artifact's lineage.
4. Compute hash-level invariants directly (no executor needed):
   - Building a PrimitiveStep with the original YAML's
     ``tool_config_hash`` produces the SAME artifact hash (H_v1)
     because hashes are content-addressed and the YAML content is
     byte-identical to what produced H_v1.
   - Building a PrimitiveStep with the CURRENT YAML's
     ``tool_config_hash`` produces a DIFFERENT artifact hash —
     proves the methodology change really would produce a fresh
     artifact were we to re-execute.

Real-Postgres integration test; module-level skip when DB
unreachable.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import pytest


_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


from shared.artifacts.lineage import (  # noqa: E402
    Lineage,
    PrimitiveStep,
)
from shared.artifacts.missingness import RawNoCleaning  # noqa: E402
from shared.artifacts.types import Series  # noqa: E402
from shared.artifacts.units import TimeSeriesUnits  # noqa: E402
from shared.config.tool_config import (  # noqa: E402
    ToolConfig,
    clear_tool_config_cache,
    load_tool_config,
)
from state.artifact_store import get_artifact, put_artifact  # noqa: E402
from state.methodology_versions import (  # noqa: E402
    clear_caches,
    get_yaml,
)
from state.object_storage import LocalFSBackend  # noqa: E402


# ============================================================================
# DB availability
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

pytestmark = pytest.mark.skipif(
    not _DB_AVAILABLE,
    reason=(
        f"Postgres not reachable at {_DB_URL!r}.  Set DB_* env vars or run "
        "the CI state-layer job."
    ),
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def engine():
    from sqlalchemy import create_engine

    e = create_engine(_DB_URL)
    yield e
    e.dispose()


@pytest.fixture(autouse=True)
def wipe(engine):
    from sqlalchemy import text

    clear_caches()
    clear_tool_config_cache()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM copilot_state.working_set"))
        conn.execute(text("DELETE FROM copilot_state.dag_nodes"))
        conn.execute(text("DELETE FROM copilot_state.message_events"))
        conn.execute(text("DELETE FROM copilot_state.turns"))
        conn.execute(text("DELETE FROM copilot_state.sessions"))
        conn.execute(text("DELETE FROM copilot_state.artifact_metadata"))
        conn.execute(text("DELETE FROM copilot_state.methodology_versions"))
        conn.execute(text("DELETE FROM copilot_state.application_version"))
    yield


@pytest.fixture
def storage(tmp_path):
    return LocalFSBackend(root=tmp_path / "artifacts")


# YAML v1: window = 252.  This is the "produced under" version.
_YAML_V1 = """\
tool:
  name: zscore_custom
  domain: ois
  description: Custom z-score for an OIS series.
conventions:
  z_score_window_days:
    value: 252
    source: industry_standard_1y_window
    rationale: 252 trading days = ~1 year of business days
  ffill_limit:
    value: 5
    source: team_judgment_pending_review
    rationale: Cap forward fill at one trading week
methodology:
  what_it_does: Z-score of a series over a rolling window
"""

# YAML v2: window mutated to 200.  Same file path on disk, different
# semantic content.
_YAML_V2 = """\
tool:
  name: zscore_custom
  domain: ois
  description: Custom z-score for an OIS series.
conventions:
  z_score_window_days:
    value: 200
    source: industry_standard_1y_window
    rationale: 252 trading days = ~1 year of business days
  ffill_limit:
    value: 5
    source: team_judgment_pending_review
    rationale: Cap forward fill at one trading week
methodology:
  what_it_does: Z-score of a series over a rolling window
"""


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(content)


def _build_artifact(
    series_key: str,
    cfg: ToolConfig,
    *,
    methodology_version_id,
    as_of_date: str = "2024-01-01",
) -> Series:
    """Build a Series artifact rooted at a PrimitiveStep that uses
    the config's identity hash.  No real primitive execution — this
    deterministically produces an artifact whose hash is fully
    determined by the config's content + a fixed series_key /
    as_of_date."""
    step = PrimitiveStep.build(
        name="calculate_zscore_custom",
        version="1.0.0",
        params={"curve_family": "USD_SOFR_OIS", "tenor": "10Y"},
        tool_config_hash=cfg.conventions_hash(),
        output_field="time_series_zscore",
        as_of_date=as_of_date,
        tool_config_path=None,
        methodology_version_id=methodology_version_id,
    )
    return Series(
        series_key=series_key,
        payload=pd.Series(
            [0.5], index=pd.date_range("2024-01-01", periods=1),
        ),
        units=TimeSeriesUnits.Z_SCORE,
        frequency="D",
        missingness_policy=RawNoCleaning(),
        lineage=Lineage.from_steps([step]),
    )


# ============================================================================
# The spec test
# ============================================================================


class TestMethodologyPinning:
    def test_yaml_mutation_changes_artifact_hash_under_current(
        self, engine, storage, tmp_path,
    ):
        """The hash-level invariant: a YAML edit on the same on-disk
        path produces a different ``conventions_hash``, which
        produces a different artifact hash.

        Pinning works iff this divergence is detectable AND the
        ORIGINAL artifact's hash can be reconstructed from the
        registry-stored YAML.
        """
        yaml_path = tmp_path / "config.yaml"
        _write_yaml(yaml_path, _YAML_V1)

        # ---- v1: load YAML, register, build artifact ----
        with engine.begin() as conn:
            cfg_v1 = load_tool_config(yaml_path, conn=conn)
        assert cfg_v1.methodology_version_id is not None
        m_v1 = cfg_v1.methodology_version_id

        art_v1 = _build_artifact(
            "USD_SOFR_OIS.10Y.zscore", cfg_v1,
            methodology_version_id=m_v1,
        )
        with engine.begin() as conn:
            h_v1 = put_artifact(
                art_v1, conn=conn, object_storage=storage,
            )

        # ---- mutate YAML on disk; clear caches so the next load
        # actually re-reads + re-registers ----
        _write_yaml(yaml_path, _YAML_V2)
        clear_caches()
        clear_tool_config_cache()

        with engine.begin() as conn:
            cfg_v2 = load_tool_config(yaml_path, conn=conn)
        assert cfg_v2.methodology_version_id is not None
        m_v2 = cfg_v2.methodology_version_id

        # The registry produced TWO distinct rows.
        assert m_v1 != m_v2
        # And the identity hashes differ.
        assert cfg_v1.conventions_hash() != cfg_v2.conventions_hash()

        # ---- "current" replay: building under YAML v2 yields a
        # different artifact hash ----
        art_v2 = _build_artifact(
            "USD_SOFR_OIS.10Y.zscore", cfg_v2,
            methodology_version_id=m_v2,
        )
        with engine.begin() as conn:
            h_v2 = put_artifact(
                art_v2, conn=conn, object_storage=storage,
            )
        assert h_v1 != h_v2, (
            "YAML mutation must change the artifact hash; otherwise the "
            "tool_config_hash is not feeding identity correctly."
        )

        # ---- "original" replay: reconstruct cfg_v1 from the
        # registry-stored YAML and confirm the rebuilt PrimitiveStep
        # has the SAME hash as the original ----
        with engine.connect() as conn:
            rec_v1 = get_yaml(m_v1, conn=conn)
        recon_cfg_v1 = ToolConfig.model_validate(rec_v1.yaml_content)
        assert recon_cfg_v1.conventions_hash() == cfg_v1.conventions_hash()

        # Rebuild the artifact under the reconstructed cfg, with the
        # same series_key / as_of_date / params.  Hash MUST match
        # the original.
        art_replay = _build_artifact(
            "USD_SOFR_OIS.10Y.zscore", recon_cfg_v1,
            methodology_version_id=m_v1,
        )
        assert art_replay.lineage.head_hash == h_v1, (
            "Reconstructed YAML must produce a byte-identical artifact "
            "hash to the original — otherwise the registry round-trip "
            "is lossy and replay is not faithful."
        )

    def test_artifact_metadata_records_both_version_columns(
        self, engine, storage, tmp_path,
    ):
        """A put under PR 9's wiring populates BOTH
        ``methodology_version_ids[]`` and ``application_version_id``."""
        from sqlalchemy import text

        yaml_path = tmp_path / "config.yaml"
        _write_yaml(yaml_path, _YAML_V1)

        with engine.begin() as conn:
            cfg = load_tool_config(yaml_path, conn=conn)
        art = _build_artifact(
            "x.y.z", cfg, methodology_version_id=cfg.methodology_version_id,
        )
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT methodology_version_ids, application_version_id
                    FROM copilot_state.artifact_metadata
                    WHERE hash = :h
                    """
                ),
                {"h": h},
            ).mappings().one()

        assert row["methodology_version_ids"] == [
            cfg.methodology_version_id,
        ]
        assert row["application_version_id"] is not None

    def test_round_trip_artifact_get_preserves_lineage_id(
        self, engine, storage, tmp_path,
    ):
        """The ``methodology_version_id`` round-trips through the
        artifact-store ``put -> get`` path on the PrimitiveStep."""
        yaml_path = tmp_path / "config.yaml"
        _write_yaml(yaml_path, _YAML_V1)
        with engine.begin() as conn:
            cfg = load_tool_config(yaml_path, conn=conn)
        art = _build_artifact(
            "round.trip.test", cfg,
            methodology_version_id=cfg.methodology_version_id,
        )
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)

        with engine.connect() as conn:
            recovered = get_artifact(
                h, conn=conn, object_storage=storage,
            )
        # The recovered Series' lineage has the PrimitiveStep with
        # the same methodology_version_id.
        primitive_steps = [
            s for s in recovered.lineage.steps
            if isinstance(s, PrimitiveStep)
        ]
        assert len(primitive_steps) == 1
        assert primitive_steps[0].methodology_version_id == (
            cfg.methodology_version_id
        )
        # And the recovered hash matches.
        assert recovered.lineage.head_hash == h


# ============================================================================
# Workspace replay route — exercised via TestClient
# ============================================================================
# These tests need FastAPI + httpx (for TestClient).  They are skipped
# at the CLASS level when either is missing; CI installs both via the
# state-layer job.  The non-route tests above still run.


def _route_deps_available() -> bool:
    try:
        import fastapi  # noqa: F401
        import httpx  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(
    not _route_deps_available(),
    reason="fastapi + httpx are required for the route tests",
)
class TestWorkspaceReplayRoute:
    """The route is exercised with FastAPI's TestClient.

    We mount only the workspace router (skipping the full app +
    lifespan) so the test doesn't need ANTHROPIC_API_KEY or any
    other production dependency.  Engine binding is wired via
    ``api.dependencies.init_engine`` against the test DB.
    """

    def _client(self):
        """Build a TestClient that mounts the artifact replay route.

        PR 10 relocated PR 9's single-artifact replay endpoint from
        ``/api/v1/workspace/{hash}`` to ``/api/v1/artifacts/{hash}/replay``
        (the singular ``/workspace`` path is now slug-routed).  The
        tests below hit the new URL.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.routes.artifacts import router

        app = FastAPI()
        app.include_router(router, prefix="/api/v1/artifacts")
        return TestClient(app)

    def _bind_engine(self):
        from api import dependencies

        # Reset any prior engine, then bind to our test DB.
        dependencies._engine = None
        from sqlalchemy import create_engine

        dependencies._engine = create_engine(_DB_URL)

    def test_404_for_unknown_artifact(self, engine):
        self._bind_engine()
        client = self._client()
        resp = client.get("/api/v1/artifacts/" + "0" * 64 + "/replay")
        assert resp.status_code == 404

    def test_400_for_malformed_hash(self, engine):
        self._bind_engine()
        client = self._client()
        resp = client.get("/api/v1/artifacts/not-a-hash/replay")
        assert resp.status_code == 400

    def test_original_mode_reconstructs_yaml(
        self, engine, storage, tmp_path,
    ):
        """``mode=original`` returns the reconstructed methodology
        from the registry — file edits do NOT taint the response."""
        yaml_path = tmp_path / "config.yaml"
        _write_yaml(yaml_path, _YAML_V1)

        with engine.begin() as conn:
            cfg_v1 = load_tool_config(yaml_path, conn=conn)
        art = _build_artifact(
            "x", cfg_v1,
            methodology_version_id=cfg_v1.methodology_version_id,
        )
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)

        # Mutate the YAML AFTER the artifact is produced.
        _write_yaml(yaml_path, _YAML_V2)

        self._bind_engine()
        client = self._client()
        resp = client.get(f"/api/v1/artifacts/{h}/replay?mode=original")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mode"] == "original"
        assert body["methodology_version_ids"] == [
            cfg_v1.methodology_version_id,
        ]
        assert len(body["reconstructed"]) == 1
        rec = body["reconstructed"][0]
        assert rec["tool_config_round_trip_ok"] is True
        assert rec["version_id"] == cfg_v1.methodology_version_id
        # The reconstructed config's conventions_hash matches what
        # the artifact was built with.
        assert rec["tool_config_hash"] == cfg_v1.conventions_hash()

    def test_current_mode_flags_yaml_diff(
        self, engine, storage, tmp_path,
    ):
        """``mode=current`` reports the on-disk YAML drift."""
        yaml_path = tmp_path / "config.yaml"
        _write_yaml(yaml_path, _YAML_V1)

        with engine.begin() as conn:
            cfg_v1 = load_tool_config(yaml_path, conn=conn)
        art = _build_artifact(
            "x", cfg_v1,
            methodology_version_id=cfg_v1.methodology_version_id,
        )
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)

        # Mutate AFTER pinning.
        _write_yaml(yaml_path, _YAML_V2)

        self._bind_engine()
        client = self._client()
        resp = client.get(f"/api/v1/artifacts/{h}/replay?mode=current")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mode"] == "current"
        assert len(body["methodology_diffs"]) == 1
        diff = body["methodology_diffs"][0]
        assert diff["original_version_id"] == cfg_v1.methodology_version_id
        assert diff["file_exists_on_disk"] is True
        assert (
            "conventions.z_score_window_days" in diff["fields_changed"]
        )

    def test_current_mode_no_diff_when_yaml_unchanged(
        self, engine, storage, tmp_path,
    ):
        """When the on-disk YAML matches what was pinned, the
        response carries an empty ``methodology_diffs``."""
        yaml_path = tmp_path / "config.yaml"
        _write_yaml(yaml_path, _YAML_V1)

        with engine.begin() as conn:
            cfg = load_tool_config(yaml_path, conn=conn)
        art = _build_artifact(
            "x", cfg, methodology_version_id=cfg.methodology_version_id,
        )
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)

        self._bind_engine()
        client = self._client()
        resp = client.get(f"/api/v1/artifacts/{h}/replay?mode=current")
        assert resp.status_code == 200
        body = resp.json()
        assert body["methodology_diffs"] == []

    def test_current_mode_handles_deleted_yaml(
        self, engine, storage, tmp_path,
    ):
        """When the YAML file no longer exists, the diff entry sets
        ``file_exists_on_disk=False`` instead of failing."""
        yaml_path = tmp_path / "config.yaml"
        _write_yaml(yaml_path, _YAML_V1)
        with engine.begin() as conn:
            cfg = load_tool_config(yaml_path, conn=conn)
        art = _build_artifact(
            "x", cfg, methodology_version_id=cfg.methodology_version_id,
        )
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)

        yaml_path.unlink()

        self._bind_engine()
        client = self._client()
        resp = client.get(f"/api/v1/artifacts/{h}/replay?mode=current")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["methodology_diffs"]) == 1
        assert body["methodology_diffs"][0]["file_exists_on_disk"] is False
        assert body["methodology_diffs"][0]["current_content_hash"] is None

    def test_response_surfaces_application_version(
        self, engine, storage, tmp_path,
    ):
        """``produced_under_commit`` and ``current_commit`` are
        always populated when the artifact carries an
        ``application_version_id`` (i.e. it was produced under
        PR 9 wiring)."""
        yaml_path = tmp_path / "config.yaml"
        _write_yaml(yaml_path, _YAML_V1)
        with engine.begin() as conn:
            cfg = load_tool_config(yaml_path, conn=conn)
        art = _build_artifact(
            "x", cfg, methodology_version_id=cfg.methodology_version_id,
        )
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)

        self._bind_engine()
        client = self._client()
        resp = client.get(f"/api/v1/artifacts/{h}/replay")
        body = resp.json()
        assert body["produced_under_commit"] is not None
        assert len(body["produced_under_commit"]) == 40
        assert body["current_commit"] is not None
        # Same process produced and queries → no divergence.
        assert body["commit_differs"] is False
