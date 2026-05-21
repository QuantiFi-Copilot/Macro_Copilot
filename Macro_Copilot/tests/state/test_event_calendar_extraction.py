"""tests/state/test_event_calendar_extraction.py — the `--mode event-calendar`
extractor (work order B2 Stage C.2, ADR 0008).

Covers the pure logic of `run_event_calendar_extraction`'s helpers — the
event-playbook section parser, the run-date-aware economic-release join
(ADR 0008 §2), the central-bank as-of rate lookup (§3) — plus the two
family extraction functions with `xbbg` mocked.

`xbbg` and `google.cloud` are stubbed in `sys.modules` BEFORE importing the
extractor (the convention in test_otr_resolver_extraction.py), so this collects
on a machine with no Bloomberg terminal and never does a real GCS import.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
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


# ============================================================================
# _resolve_event_calendar_section
# ============================================================================
class TestResolveEventCalendarSection:
    def test_absent_section_returns_none(self):
        # No event_calendar key -> a genuine non-event playbook (skipped).
        assert _incr._resolve_event_calendar_section({}) is None

    def test_unknown_family_raises(self):
        # A PRESENT but malformed section fails loudly, never silent-skipped.
        pb = {"event_calendar": {"family": "speeches", "event_category": "x",
                                 "events": [{"event_type": "a"}]}}
        with pytest.raises(ValueError, match="family"):
            _incr._resolve_event_calendar_section(pb)

    def test_eventless_section_raises(self):
        pb = {"event_calendar": {"family": "economic_release",
                                 "event_category": "economic_release", "events": []}}
        with pytest.raises(ValueError, match="events"):
            _incr._resolve_event_calendar_section(pb)

    def test_non_mapping_section_raises(self):
        with pytest.raises(ValueError, match="mapping"):
            _incr._resolve_event_calendar_section({"event_calendar": "oops"})

    def test_economic_release_section_normalised(self):
        pb = {"event_calendar": {
            "family": "economic_release",
            "event_category": "economic_release",
            "actual_field": "px_last",
            "survey_fields": {"consensus_median": "bn_survey_median"},
            "events": [{"event_type": "cpi_yoy", "ticker": "CPI YOY Index"}],
        }}
        cfg = _incr._resolve_event_calendar_section(pb)
        assert cfg is not None
        assert cfg["family"] == "economic_release"
        assert cfg["actual_field"] == "PX_LAST"
        assert cfg["release_date_field"] == "ECO_RELEASE_DT_LIST"  # default
        assert cfg["survey_fields"] == {"consensus_median": "BN_SURVEY_MEDIAN"}

    def test_central_bank_meeting_section_normalised(self):
        pb = {"event_calendar": {
            "family": "central_bank_meeting",
            "event_category": "central_bank_meeting",
            "events": [{"event_type": "fomc_decision", "rate_ticker": "FDTR Index"}],
        }}
        cfg = _incr._resolve_event_calendar_section(pb)
        assert cfg["family"] == "central_bank_meeting"
        assert cfg["meeting_calendar_field"] == "ECO_RELEASE_DT_LIST"
        assert cfg["rate_field"] == "PX_LAST"


# ============================================================================
# _join_econ_releases — ADR 0008 §2, the run-date-aware join
# ============================================================================
def _periods(*spec):
    """spec: (period_date_iso, actual) tuples -> period records."""
    return [{"period_date": d, "actual": a} for d, a in spec]


class TestJoinEconReleases:
    _PERIODS = _periods(
        ("2026-01-31", 1.0), ("2026-02-28", 2.0), ("2026-03-31", 3.0)
    )

    def test_basic_three_to_three(self):
        releases = [date(2026, 2, 12), date(2026, 3, 12), date(2026, 4, 10)]
        pairs, scheduled, warnings = _incr._join_econ_releases(
            self._PERIODS, releases, run_date=date(2026, 4, 15)
        )
        assert scheduled == []
        assert [(R.isoformat(), P["period_date"], prior) for R, P, prior in pairs] == [
            ("2026-02-12", "2026-01-31", None),
            ("2026-03-12", "2026-02-28", 1.0),
            ("2026-04-10", "2026-03-31", 2.0),
        ]

    def test_future_release_is_scheduled_not_mispaired(self):
        # A future release date must NOT pair with the latest actual.
        releases = [date(2026, 2, 12), date(2026, 3, 12), date(2026, 4, 10),
                    date(2026, 5, 12)]
        pairs, scheduled, _ = _incr._join_econ_releases(
            self._PERIODS, releases, run_date=date(2026, 4, 15)
        )
        assert len(pairs) == 3
        assert scheduled == [date(2026, 5, 12)]
        # the newest pair is still the Mar period, not the May future release
        assert pairs[-1][0] == date(2026, 4, 10)

    def test_realized_release_with_unposted_actual_is_demoted(self):
        # The Mar CPI release happened (2026-04-10 <= run_date) but its actual
        # is not in the bdh series yet (only Jan + Feb periods present). The
        # 04-10 release must be demoted to scheduled — NOT mispaired with Feb.
        periods = _periods(("2026-01-31", 1.0), ("2026-02-28", 2.0))
        releases = [date(2026, 2, 12), date(2026, 3, 12), date(2026, 4, 10)]
        pairs, scheduled, _ = _incr._join_econ_releases(
            periods, releases, run_date=date(2026, 4, 15)
        )
        assert [(R.isoformat(), P["period_date"]) for R, P, _ in pairs] == [
            ("2026-02-12", "2026-01-31"),
            ("2026-03-12", "2026-02-28"),
        ]
        assert scheduled == [date(2026, 4, 10)]

    def test_old_periods_without_release_dates_are_skipped(self):
        # 5 periods but the release list only covers the recent 2 → the 3 old
        # periods get no event row (ADR 0008 §6 — release-dated window only).
        periods = _periods(
            ("2025-11-30", -3.0), ("2025-12-31", -2.0), ("2026-01-31", 1.0),
            ("2026-02-28", 2.0), ("2026-03-31", 3.0),
        )
        releases = [date(2026, 3, 12), date(2026, 4, 10)]
        pairs, scheduled, _ = _incr._join_econ_releases(
            periods, releases, run_date=date(2026, 4, 15)
        )
        assert [P["period_date"] for _, P, _ in pairs] == ["2026-02-28", "2026-03-31"]
        assert scheduled == []

    def test_stale_old_release_is_skipped_not_scheduled(self):
        # A release far older than every bdh period must NOT become a scheduled
        # placeholder — it is stale (out of window), skipped with a warning.
        releases = [date(2024, 1, 15),  # stale — older than all periods
                    date(2026, 2, 12), date(2026, 3, 12), date(2026, 4, 10)]
        pairs, scheduled, warnings = _incr._join_econ_releases(
            self._PERIODS, releases, run_date=date(2026, 4, 15)
        )
        assert len(pairs) == 3
        assert date(2024, 1, 15) not in scheduled
        assert scheduled == []
        assert any("SKIPPED" in w for w in warnings)


# ============================================================================
# _rate_asof — ADR 0008 §3, last observation strictly before a cutoff
# ============================================================================
class TestRateAsof:
    _RATES = [
        (date(2026, 1, 1), 4.50),
        (date(2026, 2, 1), 4.25),
        (date(2026, 3, 1), 4.00),
    ]

    def test_returns_last_obs_before_cutoff(self):
        assert _incr._rate_asof(self._RATES, date(2026, 2, 15)) == 4.25

    def test_cutoff_is_strict(self):
        # cutoff exactly on an observation date -> that obs is excluded.
        assert _incr._rate_asof(self._RATES, date(2026, 2, 1)) == 4.50

    def test_before_all_returns_none(self):
        assert _incr._rate_asof(self._RATES, date(2025, 1, 1)) is None

    def test_after_all_returns_latest(self):
        assert _incr._rate_asof(self._RATES, date(2027, 1, 1)) == 4.00


# ============================================================================
# _cb_meeting_rows — ADR 0008 §3
# ============================================================================
class TestCbMeetingRows:
    def test_realized_and_scheduled_meetings(self):
        meetings = [date(2026, 1, 28), date(2026, 3, 18), date(2026, 5, 6)]
        # rate cuts after the Jan and Mar meetings.
        rates = [
            (date(2026, 1, 1), 4.50),
            (date(2026, 1, 29), 4.25),
            (date(2026, 3, 19), 4.00),
            (date(2026, 3, 25), 4.00),
        ]
        event = {"event_type": "fomc_decision", "country": "US",
                 "currency": "USD", "central_bank": "FOMC"}
        rows = _incr._cb_meeting_rows(meetings, rates, run_date=date(2026, 4, 1),
                                      event=event)
        assert len(rows) == 3

        jan, mar, may = rows
        # Jan meeting (realized): actual = rate before Mar meeting = 4.25,
        # prior = rate before Jan meeting = 4.50 -> a cut.
        assert jan["release_date"] == "2026-01-28"
        assert jan["actual"] == 4.25 and jan["prior"] == 4.50
        assert '"decision": "cut"' in jan["attributes"]
        # Mar meeting (realized): actual = rate before May = 4.00, prior = 4.25.
        assert mar["actual"] == 4.00 and mar["prior"] == 4.25
        assert '"decision": "cut"' in mar["attributes"]
        # May meeting is in the future -> scheduled placeholder.
        assert may["release_date"] == "2026-05-06"
        assert may["actual"] is None and may["prior"] is None
        assert may["attributes"] is None
        assert may["event_category"] == "central_bank_meeting"

    def test_hold_decision(self):
        meetings = [date(2026, 1, 28), date(2026, 3, 18)]
        rates = [(date(2026, 1, 1), 4.50), (date(2026, 2, 1), 4.50)]
        rows = _incr._cb_meeting_rows(
            meetings, rates, run_date=date(2026, 4, 1),
            event={"event_type": "fomc_decision", "central_bank": "FOMC"},
        )
        assert '"decision": "hold"' in rows[0]["attributes"]


# ============================================================================
# _bdh_to_period_records
# ============================================================================
class TestBdhToPeriodRecords:
    def test_pivots_long_frame_to_period_records(self):
        long_df = pd.DataFrame([
            {"trade_date": "2026-01-31", "ticker": "T", "field_name": "PX_LAST", "field_value": 1.0},
            {"trade_date": "2026-01-31", "ticker": "T", "field_name": "BN_SURVEY_MEDIAN", "field_value": 0.9},
            {"trade_date": "2026-02-28", "ticker": "T", "field_name": "PX_LAST", "field_value": 2.0},
            {"trade_date": "2026-02-28", "ticker": "T", "field_name": "BN_SURVEY_MEDIAN", "field_value": 1.9},
        ])
        recs = _incr._bdh_to_period_records(
            long_df, "PX_LAST", {"consensus_median": "BN_SURVEY_MEDIAN"}
        )
        assert recs == [
            {"period_date": "2026-01-31", "actual": 1.0, "consensus_median": 0.9},
            {"period_date": "2026-02-28", "actual": 2.0, "consensus_median": 1.9},
        ]


# ============================================================================
# _extract_economic_releases / _extract_cb_meetings — orchestration, xbbg mocked
# ============================================================================
def _wide_bdh(index_dates, **field_cols):
    return pd.DataFrame(field_cols, index=pd.to_datetime(index_dates))


class TestExtractEconomicReleases:
    def test_builds_event_rows_from_bdh_and_bds(self, monkeypatch):
        section = {
            "family": "economic_release",
            "event_category": "economic_release",
            "actual_field": "PX_LAST",
            "release_date_field": "ECO_RELEASE_DT_LIST",
            "survey_fields": {"consensus_median": "BN_SURVEY_MEDIAN"},
            "events": [{"event_type": "cpi_yoy", "country": "US",
                        "currency": "USD", "ticker": "CPI YOY Index"}],
        }
        bdh_frame = _wide_bdh(
            ["2026-01-31", "2026-02-28", "2026-03-31"],
            PX_LAST=[1.0, 2.0, 3.0], BN_SURVEY_MEDIAN=[0.9, 1.9, 2.9],
        )
        bds_frame = pd.DataFrame({
            "ticker": ["CPI YOY Index"] * 3,
            "field": ["ECO_RELEASE_DT_LIST"] * 3,
            "Future Ecostats Release Date Time": ["20260212", "20260312", "20260410"],
        })
        monkeypatch.setattr(_incr.blp, "bdh", MagicMock(return_value=bdh_frame))
        monkeypatch.setattr(_incr.blp, "bds", MagicMock(return_value=bds_frame))

        rows, failed = _incr._extract_economic_releases(
            section, "2024-01-01", "2026-04-15", date(2026, 4, 15), {}
        )
        assert failed == []
        assert len(rows) == 3
        newest = max(rows, key=lambda r: r["release_date"])
        assert newest["event_type"] == "cpi_yoy"
        assert newest["event_category"] == "economic_release"
        assert newest["release_date"] == "2026-04-10"
        assert newest["period"] == "2026-03-31"
        assert newest["actual"] == 3.0
        assert newest["consensus_median"] == 2.9
        assert newest["prior"] == 2.0

    def test_survey_false_event_yields_null_consensus(self, monkeypatch):
        # Per-event survey opt-out (JP composite PMI carries no consensus).
        section = {
            "family": "economic_release",
            "event_category": "economic_release",
            "actual_field": "PX_LAST",
            "release_date_field": "ECO_RELEASE_DT_LIST",
            "survey_fields": {"consensus_median": "BN_SURVEY_MEDIAN"},
            "events": [{"event_type": "jp_pmi", "country": "JP", "currency": "JPY",
                        "ticker": "MPMIJPCA Index", "survey": False}],
        }
        bdh_frame = _wide_bdh(
            ["2026-01-31", "2026-02-28", "2026-03-31"], PX_LAST=[50.0, 51.0, 52.0],
        )
        bds_frame = pd.DataFrame({
            "Future Ecostats Release Date Time": ["20260212", "20260312", "20260410"],
        })
        monkeypatch.setattr(_incr.blp, "bdh", MagicMock(return_value=bdh_frame))
        monkeypatch.setattr(_incr.blp, "bds", MagicMock(return_value=bds_frame))
        rows, failed = _incr._extract_economic_releases(
            section, "2024-01-01", "2026-04-15", date(2026, 4, 15), {}
        )
        assert failed == []
        assert rows and all(r["consensus_median"] is None for r in rows)

    def test_missing_ticker_event_is_a_coverage_failure(self):
        # Coverage gate: a configured event with no ticker fails extraction.
        section = {
            "family": "economic_release",
            "event_category": "economic_release",
            "actual_field": "PX_LAST",
            "release_date_field": "ECO_RELEASE_DT_LIST",
            "survey_fields": {},
            "events": [{"event_type": "broken", "country": "US"}],  # no ticker
        }
        rows, failed = _incr._extract_economic_releases(
            section, "2024-01-01", "2026-04-15", date(2026, 4, 15), {}
        )
        assert rows == []
        assert len(failed) == 1 and "broken" in failed[0]


class TestExtractCbMeetings:
    def test_builds_meeting_rows_from_bds_and_bdh(self, monkeypatch):
        section = {
            "family": "central_bank_meeting",
            "event_category": "central_bank_meeting",
            "meeting_calendar_field": "ECO_RELEASE_DT_LIST",
            "rate_field": "PX_LAST",
            "events": [{"event_type": "fomc_decision", "country": "US",
                        "currency": "USD", "central_bank": "FOMC",
                        "rate_ticker": "FDTR Index"}],
        }
        bds_frame = pd.DataFrame({
            "ticker": ["FDTR Index"] * 2,
            "Future Ecostats Release Date Time": ["20260128", "20260318"],
        })
        bdh_frame = _wide_bdh(
            ["2026-01-01", "2026-01-29"], PX_LAST=[4.50, 4.25],
        )
        monkeypatch.setattr(_incr.blp, "bds", MagicMock(return_value=bds_frame))
        monkeypatch.setattr(_incr.blp, "bdh", MagicMock(return_value=bdh_frame))

        rows, failed = _incr._extract_cb_meetings(
            section, "2024-01-01", "2026-04-15", date(2026, 4, 15), {}
        )
        assert failed == []
        assert len(rows) == 2
        jan = rows[0]
        assert jan["event_category"] == "central_bank_meeting"
        assert jan["central_bank"] == "FOMC"
        assert jan["release_date"] == "2026-01-28"
        assert jan["prior"] == 4.50
        assert jan["actual"] == 4.25
