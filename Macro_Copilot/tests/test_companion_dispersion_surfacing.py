"""tests/test_companion_dispersion_surfacing.py — GAP G02/T6 lock tests.

A summary operator that computes a central statistic PLUS a companion
dispersion (``summarize_series``: ``statistic=mean, dispersion=std``)
records the dispersion ONLY in its head lineage step — the artifact is
a single-value ``ScalarMetric``.  Pre-fix, the L6 answer prose and the
``workflow_result`` card could quote only the central value: a user
asking "mean AND std of US 2s10s" got the mean's number and a
qualitative description of the std (FRONTEND_SCORECARD T6 FAIL).

The fix surfaces the recorded companion at both consumer layers via ONE
shared reader (``shared.artifacts.lineage.companion_dispersion_from_lineage``):

  1. ``TerminalArtifactSummary`` (orchestrator/open_dag/executed_dag.py)
     carries ``dispersion_key`` / ``dispersion_value`` and its
     ``to_executed_summary()`` hands the L6 LLM BOTH numbers.
  2. ``summarize_terminal`` (rates_agent/workflows/_runner.py) carries
     the same pair on the ScalarMetric wire dict the frontend
     ``workflow_result`` card renders.

These tests pin all three layers end-to-end through the REAL
``summarize_series`` operator (not synthetic lineage), so a regression
in the operator's lineage recording breaks them too.
"""

from __future__ import annotations

import pandas as pd
import pytest

from orchestrator.open_dag.executed_dag import TerminalArtifactSummary
from rates_agent.workflows._runner import summarize_terminal
from shared.artifacts.lineage import (
    Lineage,
    PrimitiveStep,
    companion_dispersion_from_lineage,
)
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Series
from shared.artifacts.units import TimeSeriesUnits
from shared.operators.summarize_series import (
    SummarizeSeriesParams,
    summarize_series,
)


def _primitive_lineage(series_key: str) -> Lineage:
    step = PrimitiveStep.build(
        name="synthetic_primitive",
        version="1.0.0",
        params={"series_key": series_key},
        tool_config_hash="test_config_hash",
        output_field="time_series",
        as_of_date="2025-01-01",
    )
    return Lineage.from_steps([step])


def _make_series(values: list, *, series_key: str = "spread") -> Series:
    idx = pd.bdate_range("2025-01-01", periods=len(values))
    payload = pd.Series(values, index=idx, name=series_key, dtype=float)
    return Series(
        series_key=series_key,
        payload=payload,
        units=TimeSeriesUnits.BPS,
        frequency="B",
        missingness_policy=RawNoCleaning(),
        lineage=_primitive_lineage(series_key),
    )


def _mean_with_std(values: list):
    return summarize_series(
        _make_series(values),
        params=SummarizeSeriesParams(statistic="mean", dispersion="std"),
    )


def _mean_no_dispersion(values: list):
    return summarize_series(
        _make_series(values),
        params=SummarizeSeriesParams(statistic="mean", dispersion="none"),
    )


# ===========================================================================
# 1. The shared reader
# ===========================================================================


class TestCompanionDispersionReader:
    def test_reads_recorded_std(self):
        sm = _mean_with_std([1.0, 2.0, 3.0, 4.0])
        key, value = companion_dispersion_from_lineage(sm.lineage)
        assert key == "std"
        # Sample std (ddof=1) of 1..4 = ~1.2910.
        assert value == pytest.approx(1.2909944, rel=1e-6)

    def test_dispersion_none_reads_as_absent(self):
        sm = _mean_no_dispersion([1.0, 2.0, 3.0, 4.0])
        key, value = companion_dispersion_from_lineage(sm.lineage)
        assert key is None
        assert value is None

    def test_degenerate_n1_reads_as_absent(self):
        # n=1 → dispersion_value recorded as None per OPR10; the reader
        # must NOT surface a half-pair.
        sm = _mean_with_std([7.5])
        key, value = companion_dispersion_from_lineage(sm.lineage)
        assert key is None
        assert value is None

    def test_non_operator_head_step_reads_as_absent(self):
        # A bare primitive-leaf lineage (no summary step) has no
        # dispersion keys in its head params.
        series = _make_series([1.0, 2.0])
        key, value = companion_dispersion_from_lineage(series.lineage)
        assert key is None
        assert value is None


# ===========================================================================
# 2. TerminalArtifactSummary (L6 executed_summary)
# ===========================================================================


class TestTerminalArtifactSummaryDispersion:
    def test_summary_carries_companion_pair(self):
        sm = _mean_with_std([1.0, 2.0, 3.0, 4.0])
        summary = TerminalArtifactSummary.from_terminal_artifact(sm)
        assert summary.dispersion_key == "std"
        assert summary.dispersion_value == pytest.approx(
            1.2909944, rel=1e-6,
        )

    def test_executed_summary_quotes_both_numbers(self):
        sm = _mean_with_std([1.0, 2.0, 3.0, 4.0])
        summary = TerminalArtifactSummary.from_terminal_artifact(sm)
        rendered = summary.to_executed_summary()
        # Both the central value AND the companion dispersion, same
        # units, in the string the L6 LLM authors prose from.
        assert rendered == (
            "ScalarMetric(mean=2.5 bps; std=1.291 bps)"
        )

    def test_no_dispersion_format_is_byte_identical_to_legacy(self):
        # The stable pre-fix format must not change when no dispersion
        # was recorded (PR-11A format contract).
        sm = _mean_no_dispersion([1.0, 2.0, 3.0, 4.0])
        summary = TerminalArtifactSummary.from_terminal_artifact(sm)
        assert summary.dispersion_key is None
        assert summary.dispersion_value is None
        assert summary.to_executed_summary() == (
            "ScalarMetric(mean=2.5 bps)"
        )


# ===========================================================================
# 3. summarize_terminal (frontend workflow_result wire dict)
# ===========================================================================


class TestSummarizeTerminalDispersion:
    def test_wire_dict_carries_companion_pair(self):
        sm = _mean_with_std([1.0, 2.0, 3.0, 4.0])
        payload = summarize_terminal(sm)
        assert payload["type"] == "ScalarMetric"
        assert payload["metric_key"] == "mean"
        assert payload["value"] == pytest.approx(2.5)
        assert payload["dispersion_key"] == "std"
        assert payload["dispersion_value"] == pytest.approx(
            1.2909944, rel=1e-6,
        )

    def test_wire_keys_stable_when_absent(self):
        sm = _mean_no_dispersion([1.0, 2.0, 3.0, 4.0])
        payload = summarize_terminal(sm)
        assert payload["dispersion_key"] is None
        assert payload["dispersion_value"] is None
