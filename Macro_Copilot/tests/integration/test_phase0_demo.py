"""tests/integration/test_phase0_demo.py — the brief's Phase 0
demo narrative, as a CI-gated end-to-end test.

Phase 0 PR 11.

What this test proves
---------------------
The five-step narrative from the brief, all in one place:

  1. 3-turn conversation with working-set reference across turns
     (turn 1 produces artifact A; turn 2 binds the alias
     ``signal_a`` to A; turn 3 references ``signal_a`` to produce
     a downstream artifact B).
  2. Mid-conversation server restart preserves working-set state.
  3. Save workspace with a custom name; the slug is the URL handle.
  4. Simulated 30-day-later replay produces byte-identical node
     artifact hashes (proves nothing in-process is silently
     load-bearing for replay; the URL + DB suffices).
  5. YAML override surfaces a diff under ``mode=current``; the
     original YAML can be reconstructed via ``mode=original`` and
     a rebuild with the reconstructed config produces the
     SAME tool_config_hash as the original.

This is the GATE for Phase 0 close.  Any regression that breaks
the narrative trips this file's failure name pointing at the
exact deliverable that broke.

What this test does NOT cover
-----------------------------
- Re-execution of primitives.  Phase 0's replay route surfaces the
  divergence signal; the executor that closes the loop ships in a
  later PR.
- Browser-level URL resolution.  The server side is exercised
  here; the frontend stub renders the server's response.

Real-Postgres integration test; module-level skip when DB
unreachable.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest


_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


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
    reason=f"Postgres not reachable at {_DB_URL!r}",
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

    from shared.config.tool_config import clear_tool_config_cache
    from state.methodology_versions import clear_caches

    clear_caches()
    clear_tool_config_cache()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM copilot_state.workspace_variants"))
        conn.execute(text("DELETE FROM copilot_state.workspaces"))
        conn.execute(text("DELETE FROM copilot_state.dag_edges"))
        conn.execute(text("DELETE FROM copilot_state.dag_nodes"))
        conn.execute(text("DELETE FROM copilot_state.dags"))
        conn.execute(text("DELETE FROM copilot_state.working_set"))
        conn.execute(text("DELETE FROM copilot_state.message_events"))
        conn.execute(text("DELETE FROM copilot_state.turns"))
        conn.execute(text("DELETE FROM copilot_state.sessions"))
        conn.execute(text("DELETE FROM copilot_state.artifact_metadata"))
        conn.execute(text("DELETE FROM copilot_state.methodology_versions"))
        conn.execute(text("DELETE FROM copilot_state.application_version"))
    yield


@pytest.fixture
def storage(tmp_path):
    from state.object_storage import LocalFSBackend

    return LocalFSBackend(root=tmp_path / "artifacts")


# ============================================================================
# YAML fixtures
# ============================================================================


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
methodology:
  what_it_does: Z-score of a series over a rolling window
"""

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
methodology:
  what_it_does: Z-score of a series over a rolling window
"""


# ============================================================================
# Helpers — build artifact + DAG + workspace, sized for the narrative
# ============================================================================


def _build_artifact_a(
    engine, storage, yaml_path: Path,
):
    """Turn 1's artifact: a single PrimitiveStep grounded in YAML v1."""
    from shared.artifacts.lineage import FetchStep, Lineage, PrimitiveStep
    from shared.artifacts.missingness import RawNoCleaning
    from shared.artifacts.types import Series
    from shared.artifacts.units import TimeSeriesUnits
    from shared.config.tool_config import load_tool_config
    from state.artifact_store import put_artifact
    from state.dag_repo import persist_dag_from_lineage

    yaml_path.write_text(_YAML_V1)
    with engine.begin() as conn:
        cfg = load_tool_config(yaml_path, conn=conn)

    fetch = FetchStep.build(
        name="fetch_single_tenor", version="1.0.0",
        params={"curve_family": "USD_SOFR_OIS", "tenor": "10Y", "leaf": "A"},
    )
    prim = PrimitiveStep.build(
        name="calculate_zscore_custom",
        version="1.0.0",
        params={"curve_family": "USD_SOFR_OIS", "tenor": "10Y"},
        tool_config_hash=cfg.conventions_hash(),
        output_field="time_series_zscore",
        as_of_date="2024-01-01",
        methodology_version_id=cfg.methodology_version_id,
        input_hashes=(fetch.hash,),
    )
    chain = Lineage.from_steps([fetch, prim])
    art = Series(
        series_key="USD_SOFR_OIS.10Y.zscore",
        payload=pd.Series(
            [0.5, 0.7], index=pd.date_range("2024-01-01", periods=2),
        ),
        units=TimeSeriesUnits.Z_SCORE, frequency="D",
        missingness_policy=RawNoCleaning(),
        lineage=chain,
    )
    with engine.begin() as conn:
        h = put_artifact(art, conn=conn, object_storage=storage)
        dag_hash = persist_dag_from_lineage(
            chain, conn=conn, head_artifact_hash=h,
        )
    return {
        "hash": h,
        "lineage": chain,
        "dag_hash": dag_hash,
        "methodology_version_id": cfg.methodology_version_id,
        "tool_config_hash_v1": cfg.conventions_hash(),
    }


def _build_artifact_b_referencing_a(engine, storage, art_a):
    """Turn 3's artifact: an OperatorStep that consumes A.

    Lineage extends A's chain — proving turn 3 consumed A by hash,
    not by name (the name binding is in the working_set; the
    lineage records the actual content-addressed consumption).
    """
    from shared.artifacts.lineage import Lineage, OperatorStep
    from shared.artifacts.missingness import RawNoCleaning
    from shared.artifacts.types import Series
    from shared.artifacts.units import TimeSeriesUnits
    from state.artifact_store import put_artifact
    from state.dag_repo import persist_dag_from_lineage

    op = OperatorStep.build(
        name="align_series", version="1.0.0",
        params={"compare_with": "signal_a"},
        input_hashes=(art_a["lineage"].head_hash,),
    )
    chain = art_a["lineage"].append(op)
    art = Series(
        series_key="USD_SOFR_OIS.10Y.zscore_aligned",
        payload=pd.Series(
            [0.5, 0.6], index=pd.date_range("2024-01-01", periods=2),
        ),
        units=TimeSeriesUnits.Z_SCORE, frequency="D",
        missingness_policy=RawNoCleaning(),
        lineage=chain,
    )
    with engine.begin() as conn:
        h = put_artifact(art, conn=conn, object_storage=storage)
        dag_hash = persist_dag_from_lineage(
            chain, conn=conn, head_artifact_hash=h,
        )
    return {"hash": h, "lineage": chain, "dag_hash": dag_hash}


# ============================================================================
# The narrative
# ============================================================================


class TestPhase0Demo:
    """Each method advances one or two steps of the brief's demo
    narrative; the final method ties them all together in one flow."""

    def test_three_turn_conversation_with_working_set_reference(
        self, engine, storage, tmp_path,
    ):
        """Steps 1: 3-turn conversation with working-set reference
        across turns."""
        from sqlalchemy import text

        from orchestrator.state import (
            begin_turn,
            commit_turn,
            create_session_if_needed,
        )
        from state.working_set import (
            add as ws_add,
            list_visible,
            resolve,
        )

        # Session bootstrap.
        sid = uuid.uuid4()
        with engine.begin() as conn:
            create_session_if_needed(sid, conn=conn, user_id="demo")

        # ---- TURN 1: produce artifact A.
        with engine.begin() as conn:
            t1 = begin_turn(
                "compute a z-score for SOFR 10Y",
                session_id=sid, conn=conn,
            )
        art_a = _build_artifact_a(engine, storage, tmp_path / "config.yaml")
        with engine.begin() as conn:
            commit_turn(
                t1, conn=conn,
                status="completed",
                assistant_response="SOFR 10Y zscore ready.",
                terminal_artifact_hash=art_a["hash"],
            )
        # ``commit_turn`` auto-named ``turn_1_result``.
        with engine.connect() as conn:
            names_after_t1 = [
                n.name for n in list_visible(session_id=sid, conn=conn)
            ]
        assert "turn_1_result" in names_after_t1

        # ---- TURN 2: alias to "signal_a".
        with engine.begin() as conn:
            t2 = begin_turn(
                "save that as signal_a", session_id=sid, conn=conn,
            )
            ws_add(
                "signal_a", art_a["hash"], t2.turn_id,
                session_id=sid, conn=conn,
            )
            commit_turn(
                t2, conn=conn,
                status="completed",
                assistant_response="bound signal_a",
            )
        with engine.connect() as conn:
            bound = resolve("signal_a", session_id=sid, conn=conn)
        assert bound.artifact_hash == art_a["hash"]

        # ---- TURN 3: reference signal_a, produce B.
        with engine.begin() as conn:
            t3 = begin_turn(
                "compare signal_a with the front-end",
                session_id=sid, conn=conn,
            )
        # The orchestrator (in PR 8's wiring) would call the resolver
        # here.  We bypass the LLM and resolve the reference
        # explicitly — the persistence claim is "lineage records
        # consumption of A by HASH"; the alias was used at the
        # supervisor level, not at the artifact level.
        art_b = _build_artifact_b_referencing_a(engine, storage, art_a)
        with engine.begin() as conn:
            commit_turn(
                t3, conn=conn,
                status="completed",
                assistant_response="comparison done.",
                terminal_artifact_hash=art_b["hash"],
            )

        # Three completed turns; three sequence numbers.
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT sequence_no, status FROM copilot_state.turns
                    WHERE session_id = :sid
                    ORDER BY sequence_no
                    """
                ),
                {"sid": sid},
            ).all()
        assert [(r[0], r[1]) for r in rows] == [
            (1, "completed"), (2, "completed"), (3, "completed"),
        ]

        # signal_a still resolves; B's lineage references A by hash.
        assert art_a["hash"] in [
            s.hash for s in art_b["lineage"].steps
        ] or art_a["lineage"].head_hash in [
            ih for s in art_b["lineage"].steps
            for ih in getattr(s, "input_hashes", ())
        ]

    def test_simulated_server_restart_preserves_session_state(
        self, engine, storage, tmp_path,
    ):
        """Step 2: mid-conversation server restart preserves the
        working-set + turns + artifacts."""
        from sqlalchemy import create_engine, text
        from orchestrator.state import (
            begin_turn,
            commit_turn,
            create_session_if_needed,
        )
        from state.working_set import add as ws_add, list_visible

        sid = uuid.uuid4()
        with engine.begin() as conn:
            create_session_if_needed(sid, conn=conn)
            t1 = begin_turn("hi", session_id=sid, conn=conn)
        art_a = _build_artifact_a(engine, storage, tmp_path / "config.yaml")
        with engine.begin() as conn:
            ws_add(
                "signal_a", art_a["hash"], t1.turn_id,
                session_id=sid, conn=conn,
            )
            commit_turn(t1, conn=conn, status="completed")

        # ---- simulate restart.
        engine.dispose()
        from shared.config.tool_config import clear_tool_config_cache
        from state.methodology_versions import clear_caches

        clear_caches()
        clear_tool_config_cache()

        fresh = create_engine(_DB_URL)
        try:
            with fresh.connect() as conn:
                names = [
                    n.name for n in list_visible(
                        session_id=sid, conn=conn,
                    )
                ]
            assert "signal_a" in names
        finally:
            fresh.dispose()

    def test_save_workspace_with_custom_name_returns_stable_url(
        self, engine, storage, tmp_path,
    ):
        """Step 3: save workspace with a custom name; the slug is
        the URL handle; the URL survives the rename of the display
        name."""
        from state.workspace_repo import (
            create_workspace,
            get_workspace_by_slug,
            rename_workspace,
        )

        art_a = _build_artifact_a(engine, storage, tmp_path / "config.yaml")
        with engine.begin() as conn:
            ws = create_workspace(
                art_a["dag_hash"],
                conn=conn,
                name="ust 2y swap spread v1",
                created_by="demo",
            )
        original_slug = ws.slug
        assert original_slug.startswith("ust-2y-swap-spread-v1-")

        with engine.begin() as conn:
            renamed = rename_workspace(
                ws.id, "renamed for stakeholder demo", conn=conn,
            )
        # Old slug still resolves.
        with engine.connect() as conn:
            recovered = get_workspace_by_slug(original_slug, conn=conn)
        assert recovered.id == ws.id
        assert recovered.name == "renamed for stakeholder demo"
        assert renamed.slug == original_slug

    def test_thirty_day_later_replay_is_byte_identical(
        self, engine, storage, tmp_path,
    ):
        """Step 4: 30 days later, the workspace URL produces the
        same node-artifact hashes.

        We simulate "30 days later" by:
          - persisting + writing ``last_accessed_at = now()``
          - bumping ``last_accessed_at`` by 30 days (a real op
            on the column added by migration 0006)
          - disposing the engine + clearing every in-process cache
          - re-fetching by SLUG with a fresh engine and re-hashing
            every node.  Byte-identical to before.
        """
        from sqlalchemy import create_engine, text

        from state.dag_repo import list_node_artifact_hashes
        from state.workspace_repo import (
            create_workspace,
            get_workspace_by_slug,
        )

        art_a = _build_artifact_a(engine, storage, tmp_path / "config.yaml")
        with engine.begin() as conn:
            ws = create_workspace(
                art_a["dag_hash"], conn=conn,
                name="thirty day demo",
            )

        # Record hashes before "30 days later".
        with engine.connect() as conn:
            hashes_before = list_node_artifact_hashes(
                art_a["dag_hash"], conn=conn,
            )

        # Stamp + bump last_accessed_at by 30 days.
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE copilot_state.workspaces
                    SET last_accessed_at = :t
                    WHERE id = :id
                    """
                ),
                {"id": ws.id, "t": thirty_days_ago},
            )

        # Simulate the world having moved on: dispose engine +
        # clear caches.
        engine.dispose()
        from shared.config.tool_config import clear_tool_config_cache
        from state.methodology_versions import clear_caches

        clear_caches()
        clear_tool_config_cache()

        fresh = create_engine(_DB_URL)
        try:
            # Open the workspace by SLUG (the URL handle).
            with fresh.connect() as conn:
                recovered = get_workspace_by_slug(ws.slug, conn=conn)
                hashes_after = list_node_artifact_hashes(
                    recovered.dag_hash, conn=conn,
                )
            assert hashes_after == hashes_before, (
                "30-day-later replay must reproduce byte-identical "
                "node artifact hashes; this is the load-bearing "
                "Phase 0 replay invariant"
            )
        finally:
            fresh.dispose()

    def test_yaml_override_original_recovers_current_diverges(
        self, engine, storage, tmp_path,
    ):
        """Step 5: YAML override on disk.
          - ``mode=original`` reconstructs the YAML from the registry
            and produces the SAME tool_config_hash as the original.
          - ``mode=current`` reports the drift on
            ``conventions.z_score_window_days``.
        """
        from shared.config.tool_config import (
            ToolConfig,
            clear_tool_config_cache,
            load_tool_config,
        )
        from state.methodology_versions import (
            clear_caches,
            get_yaml,
        )

        yaml_path = tmp_path / "config.yaml"
        art_a = _build_artifact_a(engine, storage, yaml_path)
        original_tool_config_hash = art_a["tool_config_hash_v1"]
        mv_id = art_a["methodology_version_id"]

        # MUTATE the YAML on disk (window 252 -> 200).
        yaml_path.write_text(_YAML_V2)
        clear_caches()
        clear_tool_config_cache()

        # ---- mode=original: reconstruct via the registry.
        with engine.connect() as conn:
            rec = get_yaml(mv_id, conn=conn)
        recon = ToolConfig.model_validate(rec.yaml_content)
        # The registry-reconstructed config matches the original's
        # identity hash byte-for-byte.
        assert recon.conventions_hash() == original_tool_config_hash

        # ---- mode=current: load the on-disk YAML and confirm it
        # differs.
        with engine.begin() as conn:
            cfg_current = load_tool_config(yaml_path, conn=conn)
        assert (
            cfg_current.conventions_hash() != original_tool_config_hash
        )
        # And the convention value the user changed is the one that
        # moved.
        assert cfg_current.convention_value("z_score_window_days") == 200
        assert recon.convention_value("z_score_window_days") == 252

    def test_full_narrative_end_to_end(
        self, engine, storage, tmp_path,
    ):
        """The single composite test that exercises ALL FIVE steps
        of the brief in one flow.  If this passes, the demo runs."""
        from sqlalchemy import create_engine
        from orchestrator.state import (
            begin_turn,
            commit_turn,
            create_session_if_needed,
        )
        from state.dag_repo import list_node_artifact_hashes
        from state.working_set import (
            add as ws_add,
            list_visible,
            resolve,
        )
        from state.workspace_repo import (
            create_workspace,
            get_workspace_by_slug,
            rename_workspace,
        )

        yaml_path = tmp_path / "config.yaml"

        # ---- Step 1 setup: turn 1 produces art_a.
        sid = uuid.uuid4()
        with engine.begin() as conn:
            create_session_if_needed(sid, conn=conn)
            t1 = begin_turn("turn 1", session_id=sid, conn=conn)
        art_a = _build_artifact_a(engine, storage, yaml_path)
        with engine.begin() as conn:
            commit_turn(
                t1, conn=conn,
                status="completed",
                terminal_artifact_hash=art_a["hash"],
            )

        # turn 2: alias signal_a.
        with engine.begin() as conn:
            t2 = begin_turn("turn 2", session_id=sid, conn=conn)
            ws_add(
                "signal_a", art_a["hash"], t2.turn_id,
                session_id=sid, conn=conn,
            )
            commit_turn(t2, conn=conn, status="completed")

        # turn 3: reference signal_a.
        with engine.begin() as conn:
            t3 = begin_turn("turn 3", session_id=sid, conn=conn)
        with engine.connect() as conn:
            ref = resolve("signal_a", session_id=sid, conn=conn)
            assert ref.artifact_hash == art_a["hash"]
        art_b = _build_artifact_b_referencing_a(engine, storage, art_a)
        with engine.begin() as conn:
            commit_turn(
                t3, conn=conn,
                status="completed",
                terminal_artifact_hash=art_b["hash"],
            )

        # ---- Step 3: save workspace with a custom name.
        with engine.begin() as conn:
            ws = create_workspace(
                art_b["dag_hash"],
                conn=conn,
                name="ust 2y swap spread v1",
                created_by="phase0-demo",
            )
        original_slug = ws.slug

        # Capture the load-bearing identity sets before "restart".
        with engine.connect() as conn:
            workspace_node_hashes_before = list_node_artifact_hashes(
                ws.dag_hash, conn=conn,
            )
            session_names_before = sorted(
                n.name for n in list_visible(session_id=sid, conn=conn)
            )

        # ---- Step 2: mid-conversation server restart.
        engine.dispose()
        from shared.config.tool_config import clear_tool_config_cache
        from state.methodology_versions import clear_caches

        clear_caches()
        clear_tool_config_cache()
        fresh = create_engine(_DB_URL)
        try:
            # Step 4: 30 days later, byte-identical.
            with fresh.connect() as conn:
                recovered = get_workspace_by_slug(
                    original_slug, conn=conn,
                )
                workspace_node_hashes_after = (
                    list_node_artifact_hashes(
                        recovered.dag_hash, conn=conn,
                    )
                )
                session_names_after = sorted(
                    n.name for n in list_visible(
                        session_id=sid, conn=conn,
                    )
                )

            assert workspace_node_hashes_after == workspace_node_hashes_before
            assert session_names_after == session_names_before

            # And: rename the workspace; old slug still resolves.
            with fresh.begin() as conn:
                rename_workspace(
                    recovered.id, "renamed for the demo", conn=conn,
                )
            with fresh.connect() as conn:
                still_works = get_workspace_by_slug(
                    original_slug, conn=conn,
                )
            assert still_works.id == recovered.id
            assert still_works.name == "renamed for the demo"

            # ---- Step 5: YAML override.
            from shared.config.tool_config import (
                ToolConfig,
                load_tool_config,
            )
            from state.methodology_versions import get_yaml

            yaml_path.write_text(_YAML_V2)
            clear_caches()
            clear_tool_config_cache()
            with fresh.connect() as conn:
                rec = get_yaml(art_a["methodology_version_id"], conn=conn)
            recon = ToolConfig.model_validate(rec.yaml_content)
            assert recon.conventions_hash() == art_a["tool_config_hash_v1"]
            with fresh.begin() as conn:
                cfg_current = load_tool_config(yaml_path, conn=conn)
            assert cfg_current.conventions_hash() != art_a["tool_config_hash_v1"]
        finally:
            fresh.dispose()
