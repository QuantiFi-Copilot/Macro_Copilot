"""tests/state/test_wirp_extraction.py — Stage C of the D-wirp increment
(ADR 0009 §4): the WIRP extractor branch in ``utils/incremental_extractor.py``.

A WIRP playbook declares a ``wirp:`` section; the section-gated branch pulls,
per central-bank meeting, the four ticker-borne metrics (FR/PR/NM/CH), folds
them onto ONE synthetic per-meeting instrument (the four metrics become four
``field`` values), and emits the standard time-series parquet. These tests pin:

  * ``_wirp_real_ticker`` — the verified ``{prefix}{code} {token} Index``
    ticker, from the rendered key or reconstructed.
  * ``_extract_one_wirp_meeting`` — the four-metric pull + STRICT 4/4 rule: a
    meeting missing any metric is dropped WHOLE (returns None).
  * ``_extract_wirp_playbook`` — assembly into the standard parquet, the WIRP
    source-ticker provenance columns, and the 90% coverage gate (a partial
    WIRP artifact never uploads).

``xbbg`` / ``google.cloud`` are stubbed in ``sys.modules`` before importing the
extractor (the convention in ``test_otr_resolver_extraction.py``), so this
collects with no Bloomberg terminal. Related contract: ADR 0009 (D-wirp).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

with patch.dict(
    sys.modules,
    {
        "xbbg": MagicMock(),
        "xbbg.blp": MagicMock(),
        "google": MagicMock(),
        "google.cloud": MagicMock(),
        "google.cloud.storage": MagicMock(),
    },
):
    from utils import incremental_extractor as _incr


_METRICS: List[Dict[str, str]] = [
    {"code": "FR", "field": "WIRP_IMPLIED_RATE"},
    {"code": "PR", "field": "WIRP_MOVE_PROB"},
    {"code": "NM", "field": "WIRP_NUM_MOVES"},
    {"code": "CH", "field": "WIRP_RATE_CHANGE"},
]
_WIRP_FIELDS = {m["field"] for m in _METRICS}

_LINEAGE: Dict[str, Any] = {
    "playbook_name": "wirp",
    "playbook_version": "1.0",
    "dataset_name": "wirp",
    "asset_class": "rates",
    "playbook_hash": "hash",
    "git_commit_hash": "commit",
    "extractor_version": "test",
}


def _wirp_item(cb: str, prefix: str, token: str, date_iso: str) -> Dict[str, Any]:
    """A rendered WIRP universe row (one central-bank meeting)."""
    item: Dict[str, Any] = {
        "ticker": f"WIRP:{cb}:{date_iso}",
        "instrument_type": "wirp_meeting",
        "curve_family": "WIRP",
        "country": "US",
        "currency": "USD",
        "maturity_date": date_iso,
        "central_bank": cb,
        "meeting_date": date_iso,
        "wirp_region_prefix": prefix,
        "wirp_meeting_token": token,
    }
    for code in ("FR", "PR", "NM", "CH"):
        item[f"wirp_ticker_{code.lower()}"] = f"{prefix}{code} {token} Index"
    return item


def _wirp_playbook(universe: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "playbook_name": "wirp",
        "playbook_version": "1.0",
        "asset_class": "rates",
        "dataset_name": "wirp",
        "extraction": {"incremental_window_days": 1000},
        "wirp": {
            "bloomberg_field": "PX_LAST",
            "metrics": [dict(m) for m in _METRICS],
            "region_prefixes": {"FOMC": "US0B", "ECB": "EZ0B"},
        },
        "universe": universe,
    }


def _fake_normalize(empty_for: tuple = ()):
    """A stand-in for ``_normalize_bdh_output`` — returns a fresh non-empty
    long frame, or an empty one when the ticker matches an ``empty_for``
    substring (to simulate a metric WIRP doesn't price)."""

    def _f(raw: Any, fallback_ticker: str) -> pd.DataFrame:
        if any(bad in str(fallback_ticker) for bad in empty_for):
            return pd.DataFrame(
                columns=["trade_date", "ticker", "field_name", "field_value"]
            )
        return pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2026-05-01", "2026-05-02"]),
                "ticker": str(fallback_ticker),
                "field_name": "PX_LAST",
                "field_value": [2.0, 2.1],
            }
        )

    return _f


def _capturing_bucket():
    """A mock GCS bucket whose upload reads the parquet back so the test can
    inspect what would have shipped."""
    captured: Dict[str, Any] = {}

    def _upload(path: str) -> None:
        captured["df"] = pd.read_parquet(path)
        captured["path"] = path

    blob = MagicMock()
    blob.upload_from_filename.side_effect = _upload
    bucket = MagicMock()
    bucket.blob.return_value = blob
    return bucket, captured


# ============================================================================
# _wirp_real_ticker
# ============================================================================


class TestWirpRealTicker:
    def test_uses_the_rendered_explicit_ticker(self) -> None:
        item = _wirp_item("FOMC", "US0B", "JUN2026", "2026-06-17")
        assert _incr._wirp_real_ticker(item, "FR") == "US0BFR JUN2026 Index"

    def test_reconstructs_from_prefix_and_token(self) -> None:
        """With no explicit wirp_ticker_<code>, the verified grammar is rebuilt."""
        item = {"wirp_region_prefix": "EZ0B", "wirp_meeting_token": "SEP2026"}
        assert _incr._wirp_real_ticker(item, "PR") == "EZ0BPR SEP2026 Index"

    def test_returns_none_when_no_ticker_info(self) -> None:
        assert _incr._wirp_real_ticker({"ticker": "WIRP:FOMC:2026-06-17"}, "FR") is None


# ============================================================================
# _extract_one_wirp_meeting — strict 4/4
# ============================================================================


class TestExtractOneWirpMeeting:
    def test_all_metrics_fold_onto_the_meeting_instrument(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every metric's series is re-labelled onto the synthetic per-meeting
        ticker, with field_name = the WIRP metric field."""
        monkeypatch.setattr(_incr, "_normalize_bdh_output", _fake_normalize())
        item = _wirp_item("FOMC", "US0B", "JUN2026", "2026-06-17")

        out = _incr._extract_one_wirp_meeting(
            item, _METRICS, "PX_LAST", "2023-08-26", "2026-05-22", {}
        )

        assert out is not None
        # 4 metrics x 2 rows each.
        assert len(out) == 8
        assert set(out["ticker"].unique()) == {"WIRP:FOMC:2026-06-17"}
        assert set(out["field_name"].unique()) == _WIRP_FIELDS

    def test_one_missing_metric_drops_the_whole_meeting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """STRICT 4/4 (ADR 0009 §4): if PR returns no data the meeting is
        dropped whole — never a partial 3/4 emit."""
        monkeypatch.setattr(
            _incr, "_normalize_bdh_output", _fake_normalize(empty_for=("US0BPR",))
        )
        item = _wirp_item("FOMC", "US0B", "JUN2026", "2026-06-17")

        out = _incr._extract_one_wirp_meeting(
            item, _METRICS, "PX_LAST", "2023-08-26", "2026-05-22", {}
        )
        assert out is None

    def test_unbuildable_ticker_drops_the_meeting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_incr, "_normalize_bdh_output", _fake_normalize())
        item = {"ticker": "WIRP:FOMC:2026-06-17"}  # no prefix/token/explicit tickers
        out = _incr._extract_one_wirp_meeting(
            item, _METRICS, "PX_LAST", "2023-08-26", "2026-05-22", {}
        )
        assert out is None


# ============================================================================
# _extract_wirp_playbook — assembly, provenance, coverage gate
# ============================================================================


class TestExtractWirpPlaybook:
    def test_happy_path_uploads_standard_parquet(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(_incr, "_normalize_bdh_output", _fake_normalize())
        universe = [
            _wirp_item("FOMC", "US0B", "JUN2026", "2026-06-17"),
            _wirp_item("ECB", "EZ0B", "JUN2026", "2026-06-04"),
        ]
        bucket, captured = _capturing_bucket()

        ok = _incr._extract_wirp_playbook(
            playbook=_wirp_playbook(universe),
            lineage_meta=_LINEAGE,
            bucket=bucket,
            temp_data_dir=tmp_path,
        )

        assert ok is True
        df = captured["df"]
        # 2 meetings x 4 metrics x 2 rows.
        assert len(df) == 16
        for col in ("trade_date", "ticker", "field_name", "field_value"):
            assert col in df.columns
        assert set(df["field_name"].unique()) == _WIRP_FIELDS
        assert set(df["ticker"].unique()) == {
            "WIRP:FOMC:2026-06-17", "WIRP:ECB:2026-06-04"
        }

    def test_parquet_carries_source_ticker_provenance(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The four real Bloomberg tickers + the WIRP identity ride as columns
        so the ingester preserves them in instrument_master.attributes
        (ADR 0009 §1, Codex finding 4)."""
        monkeypatch.setattr(_incr, "_normalize_bdh_output", _fake_normalize())
        universe = [_wirp_item("FOMC", "US0B", "JUN2026", "2026-06-17")]
        bucket, captured = _capturing_bucket()

        _incr._extract_wirp_playbook(
            _wirp_playbook(universe), _LINEAGE, bucket, tmp_path
        )

        df = captured["df"]
        for col in (
            "wirp_ticker_fr", "wirp_ticker_pr", "wirp_ticker_nm", "wirp_ticker_ch",
            "central_bank", "meeting_date", "wirp_region_prefix",
            "wirp_meeting_token", "maturity_date", "instrument_type",
        ):
            assert col in df.columns, f"{col} must ride through to the parquet"
        assert set(df["instrument_type"].unique()) == {"wirp_meeting"}
        assert set(df["wirp_ticker_fr"].unique()) == {"US0BFR JUN2026 Index"}

    def test_one_failed_meeting_aborts_even_above_90pct(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """WIRP requires ALL meetings (ADR 0009 §4): 11/12 extracting — 92%,
        comfortably above the vanilla 90% gate — must STILL abort and upload
        nothing. The WIRP universe is the horizon-bounded verified-coverage
        band, so a single missing meeting is an error (transient fault /
        horizon drift), never a legitimate absence."""
        monkeypatch.setattr(
            _incr, "_normalize_bdh_output", _fake_normalize(empty_for=("BADTOK",))
        )
        universe = [
            _wirp_item("FOMC", "US0B", f"M{i:02d}2026", f"2026-{i:02d}-15")
            for i in range(1, 12)  # 11 good meetings
        ]
        universe.append(  # the 12th fails — its tickers carry the BADTOK token
            _wirp_item("FOMC", "US0B", "BADTOK", "2026-12-15")
        )
        bucket, _ = _capturing_bucket()

        ok = _incr._extract_wirp_playbook(
            _wirp_playbook(universe), _LINEAGE, bucket, tmp_path
        )

        assert ok is False  # 11/12 = 92% — still aborts under all-or-nothing
        bucket.blob.assert_not_called()  # nothing uploaded

    def test_full_universe_uploads(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The mirror case: when ALL meetings extract (100%), the gate passes
        and the parquet uploads."""
        monkeypatch.setattr(_incr, "_normalize_bdh_output", _fake_normalize())
        universe = [
            _wirp_item("FOMC", "US0B", f"M{i:02d}2026", f"2026-{i:02d}-15")
            for i in range(1, 13)  # all 12 good
        ]
        bucket, captured = _capturing_bucket()

        ok = _incr._extract_wirp_playbook(
            _wirp_playbook(universe), _LINEAGE, bucket, tmp_path
        )

        assert ok is True
        assert captured["df"]["ticker"].nunique() == 12

    def test_all_meetings_failing_returns_false(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            _incr, "_normalize_bdh_output", _fake_normalize(empty_for=("US0B",))
        )
        universe = [_wirp_item("FOMC", "US0B", "JUN2026", "2026-06-17")]
        bucket, _ = _capturing_bucket()

        ok = _incr._extract_wirp_playbook(
            _wirp_playbook(universe), _LINEAGE, bucket, tmp_path
        )
        assert ok is False
        bucket.blob.assert_not_called()

    def test_empty_universe_returns_false(self, tmp_path: Path) -> None:
        bucket, _ = _capturing_bucket()
        ok = _incr._extract_wirp_playbook(
            _wirp_playbook([]), _LINEAGE, bucket, tmp_path
        )
        assert ok is False
        bucket.blob.assert_not_called()

    def test_no_metrics_returns_false(self, tmp_path: Path) -> None:
        pb = _wirp_playbook([_wirp_item("FOMC", "US0B", "JUN2026", "2026-06-17")])
        pb["wirp"]["metrics"] = []
        bucket, _ = _capturing_bucket()
        ok = _incr._extract_wirp_playbook(pb, _LINEAGE, bucket, tmp_path)
        assert ok is False
        bucket.blob.assert_not_called()

    def test_available_false_metric_is_not_required(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A metric marked available: false is excluded — a meeting that lacks
        only that metric still extracts cleanly (ADR 0009 §4)."""
        monkeypatch.setattr(
            _incr, "_normalize_bdh_output", _fake_normalize(empty_for=("US0BCH",))
        )
        pb = _wirp_playbook([_wirp_item("FOMC", "US0B", "JUN2026", "2026-06-17")])
        # CH would return empty, but it is opted out -> the meeting is still 3/3.
        pb["wirp"]["metrics"] = [
            {"code": "FR", "field": "WIRP_IMPLIED_RATE"},
            {"code": "PR", "field": "WIRP_MOVE_PROB"},
            {"code": "NM", "field": "WIRP_NUM_MOVES"},
            {"code": "CH", "field": "WIRP_RATE_CHANGE", "available": False},
        ]
        bucket, captured = _capturing_bucket()

        ok = _incr._extract_wirp_playbook(pb, _LINEAGE, bucket, tmp_path)

        assert ok is True
        assert set(captured["df"]["field_name"].unique()) == {
            "WIRP_IMPLIED_RATE", "WIRP_MOVE_PROB", "WIRP_NUM_MOVES",
        }
