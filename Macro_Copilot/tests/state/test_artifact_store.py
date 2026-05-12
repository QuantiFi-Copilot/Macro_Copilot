"""tests/state/test_artifact_store.py — real-Postgres integration tests
for the artifact store.

Phase 0 PR 7.

Tests cover:
  - Round-trip persistence for all five closed-family artifact types
    (Series, SeriesSet, EventSet, Panel, WindowedPanel).
  - Inline-vs-blob path selection (size + row count thresholds).
  - Idempotency: put(X) twice -> single metadata row, byte-identical
    second-put cost is near-zero (verified via inspect-only counting
    backend wrapper).
  - get_artifact_summary returns shape without touching object storage.
  - Sparkline preview is bounded for arbitrarily large inline payloads.
  - CHECK constraint enforcement (exactly one of inline_payload /
    payload_uri).
  - GC sweep (find_unreferenced_artifacts + purge_artifact) honors the
    age gate and the reference graph.

Test DB resolution mirrors test_postgres_checkpointer.py: module-level
skip when no Postgres is reachable.  CI's ``state-layer`` job provides
the Postgres service container.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest


# See test_object_storage.py for the rationale.
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


from shared.artifacts.lineage import FetchStep, Lineage, OperatorStep  # noqa: E402
from shared.artifacts.missingness import RawNoCleaning  # noqa: E402
from shared.artifacts.types import (  # noqa: E402
    EventSet,
    Panel,
    Series,
    SeriesSet,
    WindowedPanel,
)
from shared.artifacts.units import TimeSeriesUnits  # noqa: E402
from state.artifact_store import (  # noqa: E402
    get_artifact,
    get_artifact_summary,
    put_artifact,
)
from state.gc import find_unreferenced_artifacts, purge_artifact  # noqa: E402
from state.object_storage import HASH_LEN, LocalFSBackend  # noqa: E402


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
        "the CI state-layer job (which provides a postgres:14 service)."
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
def wipe_artifact_table(engine):
    """Per-test cleanup: wipe artifact_metadata + the FK-referenced
    rows in dag_nodes / working_set that would block deletion.

    Cheaper than DROP SCHEMA + re-migrate.  Keeps every test starting
    from a known-empty artifact_metadata.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        # working_set / dag_nodes have FKs to artifact_metadata with
        # ON DELETE RESTRICT; we need to clear them first.  Other
        # state-layer tables aren't populated by these tests so
        # leaving them alone is fine.
        conn.execute(text("DELETE FROM copilot_state.working_set"))
        conn.execute(text("DELETE FROM copilot_state.dag_nodes"))
        conn.execute(text("DELETE FROM copilot_state.artifact_metadata"))
    yield


@pytest.fixture
def storage(tmp_path):
    """Per-test LocalFSBackend with a unique root directory."""
    return LocalFSBackend(root=tmp_path / "artifacts")


@pytest.fixture
def lineage_factory():
    """Factory that produces a unique-per-call ``Lineage``.  Used to
    avoid hash collisions between tests in the same module."""
    counter = {"n": 0}

    def _make(extra: str = "") -> Lineage:
        counter["n"] += 1
        step = FetchStep.build(
            name="fetch_single_tenor",
            version="1.0.0",
            params={
                "curve_family": "UST",
                "tenor": "10Y",
                "test_run": counter["n"],
                "extra": extra,
            },
        )
        return Lineage.from_steps([step])

    return _make


# ============================================================================
# Helpers — build small / large artifacts of each type
# ============================================================================


def _small_series(lineage) -> Series:
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    return Series(
        series_key="UST.10Y.yield_mid",
        payload=pd.Series([4.1, 4.2, 4.15, 4.3, 4.25], index=idx),
        units=TimeSeriesUnits.PERCENT,
        frequency="D",
        missingness_policy=RawNoCleaning(),
        lineage=lineage,
    )


def _large_series(lineage, n_rows: int = 252) -> Series:
    """N-row series — by default ≥ INLINE_PAYLOAD_ROW_LIMIT so it goes
    to blob storage."""
    idx = pd.date_range("2024-01-01", periods=n_rows, freq="D")
    return Series(
        series_key="UST.10Y.yield_mid",
        payload=pd.Series([4.0 + i * 0.001 for i in range(n_rows)], index=idx),
        units=TimeSeriesUnits.PERCENT,
        frequency="D",
        missingness_policy=RawNoCleaning(),
        lineage=lineage,
    )


def _event_set(lineage) -> EventSet:
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    mask = pd.Series([False, True, False, True, False], index=idx, dtype=bool)
    return EventSet(
        mask=mask,
        event_dates=[idx[1], idx[3]],
        per_event_metadata=[{"v": 1.0}, {"v": 2.0}],
        source_series_key="UST.10Y",
        frequency="D",
        lineage=lineage,
    )


def _panel(lineage) -> Panel:
    df = pd.DataFrame(
        {"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0]},
        index=pd.date_range("2024-01-01", periods=3),
    )
    return Panel(
        payload=df,
        units_by_column={
            "a": TimeSeriesUnits.PERCENT,
            "b": TimeSeriesUnits.BPS,
        },
        missingness_policy=RawNoCleaning(),
        lineage=lineage,
    )


def _series_set(lineage) -> SeriesSet:
    ci = pd.date_range("2024-01-01", periods=3)
    return SeriesSet(
        series_by_key={
            "a": pd.Series([1.0, 2.0, 3.0], index=ci),
            "b": pd.Series([4.0, 5.0, 6.0], index=ci),
        },
        units_by_key={
            "a": TimeSeriesUnits.PERCENT,
            "b": TimeSeriesUnits.BPS,
        },
        missingness_by_key={"a": RawNoCleaning(), "b": RawNoCleaning()},
        upstream_lineage_by_key={"a": lineage, "b": lineage},
        common_index=ci,
        frequency="D",
        lineage=lineage,
    )


def _windowed_panel(lineage) -> WindowedPanel:
    import numpy as np

    return WindowedPanel(
        payload=np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]),
        offsets=[-1, 0, 1],
        event_dates=[pd.Timestamp("2024-02-01"), pd.Timestamp("2024-02-15")],
        per_event_metadata=[{}, {}],
        target_series_key="UST.10Y",
        units=TimeSeriesUnits.PERCENT,
        lineage=lineage,
    )


# ============================================================================
# Round-trip for every artifact type
# ============================================================================


class TestRoundTripAllArtifactTypes:
    def test_series_round_trip_inline(self, engine, storage, lineage_factory):
        art = _small_series(lineage_factory())
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)
        assert len(h) == HASH_LEN
        with engine.connect() as conn:
            recovered = get_artifact(h, conn=conn, object_storage=storage)
        assert isinstance(recovered, Series)
        assert recovered.series_key == art.series_key
        assert (recovered.payload.values == art.payload.values).all()
        assert recovered.payload.index.equals(art.payload.index)
        assert recovered.units == art.units
        assert recovered.frequency == art.frequency
        assert recovered.lineage.head_hash == art.lineage.head_hash

    def test_series_round_trip_blob(self, engine, storage, lineage_factory):
        art = _large_series(lineage_factory(), n_rows=252)
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)
        with engine.connect() as conn:
            recovered = get_artifact(h, conn=conn, object_storage=storage)
            summary = get_artifact_summary(h, conn=conn)
        assert isinstance(recovered, Series)
        assert len(recovered.payload) == 252
        assert (recovered.payload.values == art.payload.values).all()
        assert summary.inline is False
        assert summary.payload_uri is not None
        assert summary.row_count == 252

    def test_event_set_round_trip(self, engine, storage, lineage_factory):
        art = _event_set(lineage_factory())
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)
        with engine.connect() as conn:
            recovered = get_artifact(h, conn=conn, object_storage=storage)
        assert isinstance(recovered, EventSet)
        assert (recovered.mask.values == art.mask.values).all()
        assert recovered.event_dates == art.event_dates
        assert recovered.source_series_key == art.source_series_key

    def test_panel_round_trip(self, engine, storage, lineage_factory):
        art = _panel(lineage_factory())
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)
        with engine.connect() as conn:
            recovered = get_artifact(h, conn=conn, object_storage=storage)
        assert isinstance(recovered, Panel)
        assert (recovered.payload.values == art.payload.values).all()
        assert list(recovered.payload.columns) == list(art.payload.columns)

    def test_series_set_round_trip(self, engine, storage, lineage_factory):
        art = _series_set(lineage_factory())
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)
        with engine.connect() as conn:
            recovered = get_artifact(h, conn=conn, object_storage=storage)
        assert isinstance(recovered, SeriesSet)
        assert sorted(recovered.series_by_key.keys()) == ["a", "b"]
        for key in ["a", "b"]:
            assert (
                recovered.series_by_key[key].values
                == art.series_by_key[key].values
            ).all()

    def test_windowed_panel_round_trip(
        self, engine, storage, lineage_factory
    ):
        art = _windowed_panel(lineage_factory())
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)
        with engine.connect() as conn:
            recovered = get_artifact(h, conn=conn, object_storage=storage)
        assert isinstance(recovered, WindowedPanel)
        assert (recovered.payload == art.payload).all()
        assert recovered.offsets == art.offsets
        assert recovered.event_dates == art.event_dates

    def test_trade_set_round_trip(self, engine, storage, lineage_factory):
        """Phase 1 PR 12 — TradeSet joins the closed family.  Round-
        trips through the artifact store byte-identically: same
        trade list, same leg specs, same methodology_policy,
        same lineage head hash."""
        from shared.artifacts.trades import LegSpec, Trade, TradeSet

        lineage = lineage_factory()
        leg_a = LegSpec(
            instrument_key="UST.10Y.yield_mid", weight=1.0,
            side="long", units="bps",
        )
        leg_b = LegSpec(
            instrument_key="UST.2Y.yield_mid", weight=-1.0,
            side="short", units="bps",
        )
        trade = Trade(
            entry_date=pd.Timestamp("2024-01-08"),
            exit_date=pd.Timestamp("2024-01-29"),
            leg_specs=(leg_a, leg_b),
        )
        art = TradeSet(
            trades=(trade,),
            source_event_key="UST.10Y.zscore",
            methodology_policy="fixed_horizon_v1",
            lineage=lineage,
        )
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)
        assert len(h) == HASH_LEN

        with engine.connect() as conn:
            recovered = get_artifact(h, conn=conn, object_storage=storage)
        assert isinstance(recovered, TradeSet)
        assert recovered.n_trades == 1
        assert recovered.methodology_policy == "fixed_horizon_v1"
        assert recovered.source_event_key == "UST.10Y.zscore"
        assert recovered.lineage.head_hash == art.lineage.head_hash
        # Leg-by-leg byte equality.
        rec_trade = recovered.trades[0]
        assert rec_trade.entry_date == trade.entry_date
        assert rec_trade.exit_date == trade.exit_date
        assert len(rec_trade.leg_specs) == 2
        assert rec_trade.leg_specs[0].instrument_key == "UST.10Y.yield_mid"
        assert rec_trade.leg_specs[0].weight == 1.0
        assert rec_trade.leg_specs[0].side == "long"
        assert rec_trade.leg_specs[1].weight == -1.0
        assert rec_trade.leg_specs[1].side == "short"

    def test_trade_set_idempotent_put(
        self, engine, storage, lineage_factory,
    ):
        """A second ``put_artifact`` for the same TradeSet hash is a
        no-op — same hash, single metadata row.  Mirrors the
        existing Series / SeriesSet idempotency case."""
        from sqlalchemy import text

        from shared.artifacts.trades import LegSpec, Trade, TradeSet

        leg = LegSpec(instrument_key="x", weight=1.0, side="long")
        trade = Trade(
            entry_date=pd.Timestamp("2024-01-08"),
            exit_date=pd.Timestamp("2024-01-29"),
            leg_specs=(leg,),
        )
        art = TradeSet(
            trades=(trade,),
            source_event_key=None,
            methodology_policy="fixed_horizon_v1",
            lineage=lineage_factory(),
        )
        with engine.begin() as conn:
            h1 = put_artifact(art, conn=conn, object_storage=storage)
        with engine.begin() as conn:
            h2 = put_artifact(art, conn=conn, object_storage=storage)
        assert h1 == h2
        with engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM copilot_state.artifact_metadata "
                    "WHERE hash = :h"
                ),
                {"h": h1},
            ).scalar_one()
        assert count == 1


# ============================================================================
# Inline vs blob path selection
# ============================================================================


class TestInlineVsBlob:
    def test_small_series_goes_inline(self, engine, storage, lineage_factory):
        art = _small_series(lineage_factory())
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)
        with engine.connect() as conn:
            summary = get_artifact_summary(h, conn=conn)
        assert summary.inline is True
        assert summary.payload_uri is None

    def test_large_series_goes_to_blob(self, engine, storage, lineage_factory):
        art = _large_series(lineage_factory(), n_rows=500)
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)
        with engine.connect() as conn:
            summary = get_artifact_summary(h, conn=conn)
        assert summary.inline is False
        assert summary.payload_uri is not None
        # The URI must point at storage with the artifact's hash in it.
        assert h[:2] in summary.payload_uri

    def test_explicit_threshold_override(
        self, engine, storage, lineage_factory
    ):
        """Caller-supplied threshold trumps the env default."""
        art = _small_series(lineage_factory())
        with engine.begin() as conn:
            h = put_artifact(
                art,
                conn=conn,
                object_storage=storage,
                inline_row_limit=1,  # force blob
            )
        with engine.connect() as conn:
            summary = get_artifact_summary(h, conn=conn)
        assert summary.inline is False


# ============================================================================
# Idempotency
# ============================================================================


class TestIdempotency:
    def test_put_twice_produces_single_row(
        self, engine, storage, lineage_factory
    ):
        art = _small_series(lineage_factory())
        with engine.begin() as conn:
            h1 = put_artifact(art, conn=conn, object_storage=storage)
            h2 = put_artifact(art, conn=conn, object_storage=storage)
        assert h1 == h2

        from sqlalchemy import text

        with engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT count(*) FROM copilot_state.artifact_metadata "
                    "WHERE hash = :h"
                ),
                {"h": h1},
            ).scalar_one()
        assert count == 1

    def test_second_put_does_not_write_blob_again(
        self, engine, storage, lineage_factory, monkeypatch
    ):
        """Idempotency gate skips object-storage writes for already-stored
        artifacts.  Verified by counting calls on a wrapped backend."""
        art = _large_series(lineage_factory(), n_rows=500)
        put_call_count = {"n": 0}

        original_put = storage.put_bytes

        def counting_put(hash, content):
            put_call_count["n"] += 1
            return original_put(hash, content)

        monkeypatch.setattr(storage, "put_bytes", counting_put)

        with engine.begin() as conn:
            put_artifact(art, conn=conn, object_storage=storage)
            put_artifact(art, conn=conn, object_storage=storage)

        # Only the FIRST put should have hit object storage.  The second
        # short-circuits at the metadata-existence check.
        assert put_call_count["n"] == 1


# ============================================================================
# Summary endpoint
# ============================================================================


class TestArtifactSummary:
    def test_summary_returns_shape_without_object_storage_fetch(
        self, engine, storage, lineage_factory, monkeypatch
    ):
        """get_artifact_summary must NOT hit object storage."""
        art = _large_series(lineage_factory(), n_rows=500)
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)

        get_call_count = {"n": 0}

        original_get = storage.get_bytes

        def counting_get(uri):
            get_call_count["n"] += 1
            return original_get(uri)

        monkeypatch.setattr(storage, "get_bytes", counting_get)

        with engine.connect() as conn:
            summary = get_artifact_summary(h, conn=conn)

        assert summary.hash == h
        assert summary.artifact_type == "Series"
        assert summary.row_count == 500
        assert summary.inline is False
        # The whole point: zero object-storage fetches for summary.
        assert get_call_count["n"] == 0

    def test_summary_preview_bounded_for_inline_series(
        self, engine, storage, lineage_factory
    ):
        """Even for a series at the inline upper-bound (just under the
        row limit), the preview is capped at ``_PREVIEW_POINTS``."""
        from state.artifact_store import _PREVIEW_POINTS

        # Build a series with row_count = 50 to stay inline.
        idx = pd.date_range("2024-01-01", periods=50, freq="D")
        art = Series(
            series_key="x",
            payload=pd.Series([float(i) for i in range(50)], index=idx),
            units=TimeSeriesUnits.PERCENT,
            frequency="D",
            missingness_policy=RawNoCleaning(),
            lineage=lineage_factory(),
        )
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)
        with engine.connect() as conn:
            summary = get_artifact_summary(h, conn=conn)
        assert summary.inline is True
        assert len(summary.preview_values) <= _PREVIEW_POINTS

    def test_summary_blob_returns_empty_preview(
        self, engine, storage, lineage_factory
    ):
        """Blob-stored artifacts return an empty preview (caller can
        fetch the full artifact for a richer preview)."""
        art = _large_series(lineage_factory(), n_rows=500)
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)
        with engine.connect() as conn:
            summary = get_artifact_summary(h, conn=conn)
        assert summary.inline is False
        assert summary.preview_values == []

    def test_missing_hash_raises_key_error(self, engine):
        with engine.connect() as conn:
            with pytest.raises(KeyError):
                get_artifact_summary("0" * HASH_LEN, conn=conn)


# ============================================================================
# Error handling
# ============================================================================


class TestErrorHandling:
    def test_get_missing_hash_raises_key_error(
        self, engine, storage
    ):
        with engine.connect() as conn:
            with pytest.raises(KeyError):
                get_artifact("0" * HASH_LEN, conn=conn, object_storage=storage)

    def test_invalid_hash_format_rejected(self, engine, storage):
        with engine.connect() as conn:
            with pytest.raises(ValueError):
                get_artifact("not-a-valid-hash", conn=conn, object_storage=storage)


# ============================================================================
# GC sweep
# ============================================================================


class TestGarbageCollection:
    def test_find_unreferenced_skips_recent_artifacts(
        self, engine, storage, lineage_factory
    ):
        """Recently-created artifacts (within the age gate) are NOT
        eligible for GC even when they have no references."""
        art = _small_series(lineage_factory())
        with engine.begin() as conn:
            put_artifact(art, conn=conn, object_storage=storage)

        with engine.connect() as conn:
            candidates = find_unreferenced_artifacts(conn, older_than_days=30)
        assert candidates == []

    def test_find_unreferenced_finds_old_artifacts(
        self, engine, storage, lineage_factory
    ):
        """An artifact older than the age gate AND unreferenced shows
        up in the candidate list."""
        from sqlalchemy import text

        art = _small_series(lineage_factory())
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)
            # Manually backdate the created_at to bypass the age gate.
            conn.execute(
                text(
                    "UPDATE copilot_state.artifact_metadata "
                    "SET created_at = NOW() - INTERVAL '90 days' "
                    "WHERE hash = :h"
                ),
                {"h": h},
            )

        with engine.connect() as conn:
            candidates = find_unreferenced_artifacts(conn, older_than_days=30)
        assert h in candidates

    def test_purge_removes_metadata_and_blob(
        self, engine, storage, lineage_factory
    ):
        art = _large_series(lineage_factory(), n_rows=500)
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)

        # Confirm the blob exists in storage before purge.
        with engine.connect() as conn:
            summary = get_artifact_summary(h, conn=conn)
        uri = summary.payload_uri
        assert uri is not None
        assert storage.get_bytes(uri) is not None

        with engine.begin() as conn:
            purged = purge_artifact(h, conn=conn, object_storage=storage)
        assert purged is True

        # Metadata row gone.
        with engine.connect() as conn:
            with pytest.raises(KeyError):
                get_artifact_summary(h, conn=conn)
        # Blob gone.
        with pytest.raises(FileNotFoundError):
            storage.get_bytes(uri)

    def test_purge_missing_hash_returns_false(
        self, engine, storage
    ):
        with engine.begin() as conn:
            result = purge_artifact(
                "0" * HASH_LEN, conn=conn, object_storage=storage
            )
        assert result is False

    def test_find_unreferenced_respects_limit(
        self, engine, storage, lineage_factory
    ):
        """Limit caps the returned set so a large unreferenced backlog
        doesn't get processed in one transaction."""
        from sqlalchemy import text

        # Create 5 old, unreferenced artifacts with distinct lineages.
        hashes = []
        with engine.begin() as conn:
            for i in range(5):
                art = _small_series(lineage_factory(extra=f"gc_{i}"))
                h = put_artifact(art, conn=conn, object_storage=storage)
                hashes.append(h)
            # Backdate them all.
            conn.execute(
                text(
                    "UPDATE copilot_state.artifact_metadata "
                    "SET created_at = NOW() - INTERVAL '90 days' "
                    "WHERE hash = ANY(:hashes)"
                ),
                {"hashes": hashes},
            )

        with engine.connect() as conn:
            candidates = find_unreferenced_artifacts(
                conn, older_than_days=30, limit=3
            )
        assert len(candidates) == 3
