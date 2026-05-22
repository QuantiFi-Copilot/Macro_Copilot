"""tests/state/test_wirp_universe_renderer.py — Stage B of the D-wirp increment
(ADR 0009 §3): the WIRP universe renderer + the wirp.yml seed.

`utils/render_wirp_universe.py` expands the empty `wirp.yml` seed into the
effective playbook — one synthetic instrument per central-bank meeting — by
reading the `event_calendar` meeting calendar D-cb ingested. These tests pin:

  * `build_wirp_universe` — the pure meeting -> universe-row mapping: the
    synthetic vendor_ticker, the verified ticker grammar, the typed/attributes
    column split (ADR 0009 §1), horizon filtering, the unmapped-bank skip, the
    `available: false` metric opt-out, deterministic ordering.
  * `render_wirp_playbook` — assembles the rendered playbook + provenance
    header (engine mocked — no Postgres).
  * `push_playbooks.py` skips a `wirp:` playbook so the empty seed never
    clobbers the rendered universe.
  * the shipped `wirp.yml` seed is a well-formed WIRP playbook.

No Postgres is touched. Related contract: ADR 0009 (D-wirp).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest
import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils import push_playbooks  # noqa: E402
from utils.render_wirp_universe import (  # noqa: E402
    build_wirp_universe,
    playbook_has_wirp_section,
    render_wirp_playbook,
)

_SEED_PATH = _PROJECT_ROOT / "rates_agent" / "playbooks" / "wirp.yml"

_WIRP_CONFIG: Dict[str, Any] = {
    "bloomberg_field": "PX_LAST",
    "metrics": [
        {"code": "FR", "field": "WIRP_IMPLIED_RATE"},
        {"code": "PR", "field": "WIRP_MOVE_PROB"},
        {"code": "NM", "field": "WIRP_NUM_MOVES"},
        {"code": "CH", "field": "WIRP_RATE_CHANGE"},
    ],
    "region_prefixes": {
        "FOMC": "US0B", "ECB": "EZ0B", "BOE": "GB0B", "BOJ": "JP0B",
    },
}


def _meeting(cb: str, country: str, currency: str, d: date) -> Dict[str, Any]:
    return {
        "central_bank": cb, "country": country,
        "currency": currency, "release_date": d,
    }


_MEETINGS: List[Dict[str, Any]] = [
    _meeting("FOMC", "US", "USD", date(2026, 6, 17)),
    _meeting("ECB", "EU", "EUR", date(2026, 6, 4)),
    _meeting("BOE", "UK", "GBP", date(2026, 8, 6)),
    _meeting("BOJ", "JP", "JPY", date(2026, 1, 23)),
]


# ============================================================================
# build_wirp_universe — the pure meeting -> universe-row mapping
# ============================================================================


class TestBuildWirpUniverse:
    def test_one_row_per_meeting(self) -> None:
        rows = build_wirp_universe(_MEETINGS, _WIRP_CONFIG, date(2026, 5, 22))
        assert len(rows) == len(_MEETINGS)

    def test_synthetic_vendor_ticker(self) -> None:
        """ticker is the synthetic WIRP:{bank}:{meeting_date} id (ADR 0009 §1)."""
        rows = build_wirp_universe(
            [_meeting("FOMC", "US", "USD", date(2026, 6, 17))],
            _WIRP_CONFIG, date(2026, 5, 22),
        )
        assert rows[0]["ticker"] == "WIRP:FOMC:2026-06-17"

    def test_meeting_token_is_mmmYYYY_uppercase(self) -> None:
        rows = build_wirp_universe(
            [_meeting("FOMC", "US", "USD", date(2026, 6, 17))],
            _WIRP_CONFIG, date(2026, 5, 22),
        )
        assert rows[0]["wirp_meeting_token"] == "JUN2026"

    def test_source_tickers_follow_the_verified_grammar(self) -> None:
        """{region_prefix}{code} {token} Index — VERIFIED 2026-05-21."""
        rows = build_wirp_universe(
            [_meeting("FOMC", "US", "USD", date(2026, 6, 17))],
            _WIRP_CONFIG, date(2026, 5, 22),
        )
        row = rows[0]
        assert row["wirp_ticker_fr"] == "US0BFR JUN2026 Index"
        assert row["wirp_ticker_pr"] == "US0BPR JUN2026 Index"
        assert row["wirp_ticker_nm"] == "US0BNM JUN2026 Index"
        assert row["wirp_ticker_ch"] == "US0BCH JUN2026 Index"

    def test_region_prefix_per_bank(self) -> None:
        rows = build_wirp_universe(_MEETINGS, _WIRP_CONFIG, date(2026, 5, 22))
        by_bank = {r["central_bank"]: r["wirp_region_prefix"] for r in rows}
        assert by_bank == {
            "FOMC": "US0B", "ECB": "EZ0B", "BOE": "GB0B", "BOJ": "JP0B",
        }

    def test_typed_and_attribute_columns(self) -> None:
        """maturity_date is the typed instrument_master column (a date);
        central_bank / meeting_date / prefix / token ride in attributes
        (ADR 0009 §1)."""
        rows = build_wirp_universe(
            [_meeting("ECB", "EU", "EUR", date(2026, 6, 4))],
            _WIRP_CONFIG, date(2026, 5, 22),
        )
        row = rows[0]
        assert row["instrument_type"] == "wirp_meeting"
        assert row["curve_family"] == "WIRP"
        assert row["country"] == "EU"
        assert row["currency"] == "EUR"
        assert row["maturity_date"] == date(2026, 6, 4)
        assert row["central_bank"] == "ECB"
        assert row["meeting_date"] == "2026-06-04"

    def test_maturity_date_is_a_date_meeting_date_is_iso_string(self) -> None:
        """maturity_date -> typed DATE column; meeting_date -> attributes
        JSONB, an ISO string mirror."""
        rows = build_wirp_universe(
            [_meeting("FOMC", "US", "USD", date(2026, 6, 17))],
            _WIRP_CONFIG, date(2026, 5, 22),
        )
        assert isinstance(rows[0]["maturity_date"], date)
        assert isinstance(rows[0]["meeting_date"], str)

    def test_unbounded_when_no_horizon(self) -> None:
        """No forward/past horizon -> every meeting is emitted."""
        rows = build_wirp_universe(_MEETINGS, _WIRP_CONFIG, date(2020, 1, 1))
        assert len(rows) == len(_MEETINGS)

    def test_forward_horizon_drops_far_future_meetings(self) -> None:
        cfg = dict(_WIRP_CONFIG, forward_horizon_days=30)
        # as_of 2026-05-22, +30d = 2026-06-21: keeps Jun 4 & Jun 17, drops Aug 6.
        rows = build_wirp_universe(_MEETINGS, cfg, date(2026, 5, 22))
        kept = {r["meeting_date"] for r in rows}
        assert "2026-06-17" in kept
        assert "2026-08-06" not in kept

    def test_past_horizon_drops_far_past_meetings(self) -> None:
        cfg = dict(_WIRP_CONFIG, past_horizon_days=60)
        # as_of 2026-05-22, -60d = 2026-03-23: drops the Jan 23 BOJ meeting.
        rows = build_wirp_universe(_MEETINGS, cfg, date(2026, 5, 22))
        kept = {r["meeting_date"] for r in rows}
        assert "2026-01-23" not in kept
        assert "2026-06-17" in kept

    def test_unknown_central_bank_is_skipped(self) -> None:
        """A meeting for a bank not in region_prefixes cannot get a WIRP
        ticker — it is skipped, not emitted with a broken ticker."""
        meetings = _MEETINGS + [_meeting("RBA", "AU", "AUD", date(2026, 7, 7))]
        rows = build_wirp_universe(meetings, _WIRP_CONFIG, date(2020, 1, 1))
        assert len(rows) == len(_MEETINGS)
        assert all(r["central_bank"] != "RBA" for r in rows)

    def test_available_false_metric_is_excluded(self) -> None:
        """A metric carrying available: false (ADR 0009 §4) gets no
        wirp_ticker_<code> key."""
        cfg = dict(_WIRP_CONFIG)
        cfg["metrics"] = [
            {"code": "FR", "field": "WIRP_IMPLIED_RATE"},
            {"code": "CH", "field": "WIRP_RATE_CHANGE", "available": False},
        ]
        rows = build_wirp_universe(
            [_meeting("FOMC", "US", "USD", date(2026, 6, 17))],
            cfg, date(2020, 1, 1),
        )
        assert "wirp_ticker_fr" in rows[0]
        assert "wirp_ticker_ch" not in rows[0]

    def test_output_is_sorted_deterministically(self) -> None:
        """Output sorted by (central_bank, meeting_date) — deterministic
        rendering (P4)."""
        shuffled = list(reversed(_MEETINGS)) + [
            _meeting("FOMC", "US", "USD", date(2026, 1, 28)),
        ]
        rows = build_wirp_universe(shuffled, _WIRP_CONFIG, date(2020, 1, 1))
        keys = [(r["central_bank"], r["meeting_date"]) for r in rows]
        assert keys == sorted(keys)

    def test_empty_meetings_yields_empty_universe(self) -> None:
        assert build_wirp_universe([], _WIRP_CONFIG, date(2026, 5, 22)) == []


# ============================================================================
# playbook_has_wirp_section
# ============================================================================


class TestPlaybookHasWirpSection:
    def test_true_when_wirp_dict_present(self) -> None:
        assert playbook_has_wirp_section({"wirp": {"metrics": []}}) is True

    def test_false_when_absent(self) -> None:
        assert playbook_has_wirp_section({"playbook_name": "ois"}) is False

    def test_false_when_wirp_is_not_a_mapping(self) -> None:
        assert playbook_has_wirp_section({"wirp": "yes"}) is False


# ============================================================================
# render_wirp_playbook — assembly + provenance (engine mocked)
# ============================================================================


class TestRenderWirpPlaybook:
    def _render(self, monkeypatch: pytest.MonkeyPatch) -> str:
        import utils.render_wirp_universe as rwu

        monkeypatch.setattr(
            rwu, "load_central_bank_meetings",
            lambda engine: (
                _MEETINGS,
                {"event_calendar_meeting_rows": 4,
                 "event_calendar_latest_created_at": "2026-05-21T23:58:29"},
            ),
        )
        text, _prov = rwu.render_wirp_playbook(
            _SEED_PATH, MagicMock(), as_of=date(2026, 5, 22)
        )
        return text

    def test_rendered_text_round_trips_to_a_valid_playbook(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rendered = self._render(monkeypatch)
        playbook = yaml.safe_load(rendered)
        assert playbook["playbook_name"] == "wirp"
        assert isinstance(playbook["wirp"], dict)
        assert len(playbook["universe"]) == len(_MEETINGS)

    def test_rendered_universe_carries_source_tickers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        playbook = yaml.safe_load(self._render(monkeypatch))
        fomc = [r for r in playbook["universe"] if r["central_bank"] == "FOMC"][0]
        assert fomc["wirp_ticker_fr"] == "US0BFR JUN2026 Index"

    def test_rendered_file_has_a_machine_artifact_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rendered = self._render(monkeypatch)
        assert "MACHINE ARTIFACT — DO NOT EDIT, DO NOT COMMIT" in rendered
        assert "rendered_body_sha256" in rendered
        assert "horizon" in rendered

    def test_provenance_reports_counts_and_horizon(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import utils.render_wirp_universe as rwu

        monkeypatch.setattr(
            rwu, "load_central_bank_meetings",
            lambda engine: (
                _MEETINGS,
                {"event_calendar_meeting_rows": 4,
                 "event_calendar_latest_created_at": None},
            ),
        )
        _text, prov = rwu.render_wirp_playbook(
            _SEED_PATH, MagicMock(), as_of=date(2026, 5, 22)
        )
        assert prov["universe_count"] == 4
        assert prov["event_calendar_meeting_rows"] == 4
        # Stage D set the horizons -> the provenance records the bounded render
        # (all four test meetings fall inside the band, so the count holds).
        seed_wirp = yaml.safe_load(_SEED_PATH.read_text(encoding="utf-8"))["wirp"]
        assert f"forward_horizon_days={seed_wirp['forward_horizon_days']}" in prov["horizon"]
        assert f"past_horizon_days={seed_wirp['past_horizon_days']}" in prov["horizon"]


# ============================================================================
# push_playbooks — the wirp: skip
# ============================================================================


class TestPushPlaybooksWirpSkip:
    def test_wirp_seed_is_detected(self, tmp_path: Path) -> None:
        f = tmp_path / "wirp.yml"
        f.write_text("playbook_name: wirp\nwirp:\n  metrics: []\nuniverse: []\n")
        assert push_playbooks._has_wirp_section(f) is True

    def test_non_wirp_playbook_is_not_detected(self, tmp_path: Path) -> None:
        f = tmp_path / "ois.yml"
        f.write_text("playbook_name: ois\ntarget_metrics: []\nuniverse: []\n")
        assert push_playbooks._has_wirp_section(f) is False

    def test_shipped_seed_is_skipped_by_push(self) -> None:
        """The real wirp.yml seed is recognised — push_playbooks must skip it
        so the empty universe never clobbers the rendered one."""
        assert push_playbooks._has_wirp_section(_SEED_PATH) is True

    def test_wirp_seed_is_not_a_resolver_playbook(self) -> None:
        """The two skip predicates are independent — the WIRP seed declares no
        otr_resolution block."""
        assert push_playbooks._has_enabled_resolver(_SEED_PATH) is False


# ============================================================================
# The shipped wirp.yml seed
# ============================================================================


class TestWirpSeed:
    def _seed(self) -> Dict[str, Any]:
        return yaml.safe_load(_SEED_PATH.read_text(encoding="utf-8"))

    def test_seed_is_a_time_series_playbook(self) -> None:
        seed = self._seed()
        assert seed["playbook_name"] == "wirp"
        assert seed["asset_class"] == "rates"
        assert seed["dataset_name"] == "wirp"

    def test_seed_universe_ships_empty(self) -> None:
        """The universe is GENERATED — the git seed carries an empty list."""
        assert self._seed()["universe"] == []

    def test_seed_carries_no_target_metrics(self) -> None:
        """The documented exception (ADR 0009 §2): WIRP metrics are
        ticker-borne, so the playbook has no target_metrics/reference_metrics."""
        seed = self._seed()
        assert "target_metrics" not in seed
        assert "reference_metrics" not in seed

    def test_seed_wirp_section_declares_four_metrics(self) -> None:
        metrics = self._seed()["wirp"]["metrics"]
        codes = {m["code"] for m in metrics}
        assert codes == {"FR", "PR", "NM", "CH"}

    def test_seed_metric_fields_carry_no_unit(self) -> None:
        """CH must be WIRP_RATE_CHANGE, not WIRP_BP_CHANGE — the field name
        asserts a quantity, never an unverified unit (ADR 0009 §1, P12)."""
        fields = {m["code"]: m["field"] for m in self._seed()["wirp"]["metrics"]}
        assert fields["CH"] == "WIRP_RATE_CHANGE"
        assert fields["FR"] == "WIRP_IMPLIED_RATE"

    def test_seed_region_prefixes_cover_the_four_banks(self) -> None:
        prefixes = self._seed()["wirp"]["region_prefixes"]
        assert prefixes == {
            "FOMC": "US0B", "ECB": "EZ0B", "BOE": "GB0B", "BOJ": "JP0B",
        }

    def test_seed_horizons_set_from_the_coverage_probe(self) -> None:
        """Stage D sets the horizons from the Stage-B probe: forward in the
        verified [342, 382] gap (includes the 73 covered meetings, excludes the
        far-future empties that begin at +383d), past >= 483d (reaches the
        earliest covered meeting, 2025-01-24)."""
        wirp = self._seed()["wirp"]
        assert 342 <= wirp["forward_horizon_days"] <= 382
        assert wirp["past_horizon_days"] >= 483

    def test_seed_extraction_window_is_deep(self) -> None:
        """Incremental-only with a deep window spanning every meeting run-up."""
        assert self._seed()["extraction"]["incremental_window_days"] >= 800
