"""Campaign FM-1 / FM-2 locks — grounded answer context.

The 2026-06-12 prompt campaign found the L6 renderer improvising when
the executed summary carried no number (FM-1: fabricated values,
unfilled "[value from ...]" templates, narration outside the fetched
window) and misreading diagnostic test statistics as probabilities
(FM-2: F=0.93 reported as "p ~93%").  These tests lock the data-side
fixes:

  - Series terminals surface last finite value + date + fetched span
    into ``TerminalArtifactSummary`` and ``to_executed_summary``.
  - A ``p_value`` recorded in the head lineage step surfaces, labelled
    as NOT-a-probability alongside the test statistic.
  - The unfilled-placeholder regex catches the exact campaign
    artifacts and leaves normal trader prose alone.
"""

from __future__ import annotations

import pandas as pd
import pytest

from orchestrator.open_dag.answer import _UNFILLED_PLACEHOLDER_RE
from orchestrator.open_dag.executed_dag import TerminalArtifactSummary
from shared.artifacts.lineage import Lineage, OperatorStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import ScalarMetric, Series
from shared.artifacts.units import TimeSeriesUnits


def _lineage(params: dict | None = None) -> Lineage:
    step = OperatorStep.build(
        name="sentinel_op",
        version="1.0.0",
        params=params or {},
        input_hashes=(),
    )
    return Lineage.from_steps([step])


def _series(values, dates) -> Series:
    payload = pd.Series(
        values, index=pd.to_datetime(dates), name="us_10y", dtype=float,
    )
    return Series(
        series_key="us_10y",
        payload=payload,
        units=TimeSeriesUnits.PERCENT,
        frequency="B",
        missingness_policy=RawNoCleaning(),
        lineage=_lineage(),
    )


class TestSeriesTerminalGrounding:
    def test_last_value_and_span_surface(self):
        s = _series(
            [4.1, 4.2, float("nan"), 4.3],
            ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"],
        )
        summ = TerminalArtifactSummary.from_terminal_artifact(s)
        assert summ.last_value == pytest.approx(4.3)
        assert summ.last_date == "2026-01-07"
        assert summ.first_date == "2026-01-02"
        assert summ.finite_count == 3
        text = summ.to_executed_summary()
        assert "last=4.3 on 2026-01-07" in text
        assert "span=2026-01-02→2026-01-07" in text
        assert "finite=3" in text

    def test_trailing_nan_falls_back_to_last_finite(self):
        s = _series(
            [4.1, 4.25, float("nan")],
            ["2026-01-02", "2026-01-05", "2026-01-06"],
        )
        summ = TerminalArtifactSummary.from_terminal_artifact(s)
        assert summ.last_value == pytest.approx(4.25)
        assert summ.last_date == "2026-01-05"

    def test_all_nan_renders_valueless_legacy_format(self):
        s = _series(
            [float("nan"), float("nan")],
            ["2026-01-02", "2026-01-05"],
        )
        summ = TerminalArtifactSummary.from_terminal_artifact(s)
        assert summ.last_value is None
        assert summ.to_executed_summary() == (
            f"Series<{TimeSeriesUnits.PERCENT.value}>(n=2)"
        )


class TestDiagnosticPValueSurfacing:
    def test_p_value_from_head_step_surfaces_labelled(self):
        m = ScalarMetric(
            metric_key="granger_f_statistic",
            value=0.9327,
            units=TimeSeriesUnits.RATIO,
            lineage=_lineage({"p_value": 0.4621, "df1": 5, "n_lags": 5}),
        )
        summ = TerminalArtifactSummary.from_terminal_artifact(m)
        assert summ.p_value == pytest.approx(0.4621)
        text = summ.to_executed_summary()
        assert "p_value=0.4621" in text
        assert "NOT a probability" in text

    def test_no_p_value_keeps_legacy_format(self):
        m = ScalarMetric(
            metric_key="correlation_coefficient",
            value=-0.342,
            units=TimeSeriesUnits.RATIO,
            lineage=_lineage({"statistic": "pearson"}),
        )
        summ = TerminalArtifactSummary.from_terminal_artifact(m)
        assert summ.p_value is None
        assert summ.to_executed_summary() == (
            f"ScalarMetric(correlation_coefficient=-0.342 "
            f"{TimeSeriesUnits.RATIO.value})"
        )


class TestPlaceholderRegex:
    def test_matches_campaign_artifacts(self):
        # e05's literal shipped prose
        assert _UNFILLED_PLACEHOLDER_RE.search(
            "**z-score [value from the Series last observation]** is rich"
        )
        assert _UNFILLED_PLACEHOLDER_RE.search(
            "[if |z| < 1: the level is unremarkable]"
        )

    def test_leaves_normal_prose_alone(self):
        for prose in (
            "The US 2s10s has averaged 34.6 bps over two years.",
            "F = 0.93 (p = 0.46) — no causality at daily lags.",
            "the [sic] spread",  # single token — deliberately allowed
        ):
            assert not _UNFILLED_PLACEHOLDER_RE.search(prose), prose
