"""tests/test_workflow_runner.py — plumbing tests for the workflow
runtime helpers in ``rates_agent.workflows._runner``.

These tests exercise the same code path the MCP server (Phase A
runner) and the CLI (Phase C) use to bind + execute workflow
templates.  They DO NOT depend on the ``mcp`` package being
installed, because the runner is deliberately a transport-blind
runtime layer.

Coverage
--------
1. Catalogue helpers (``list_workflow_cards``,
   ``describe_workflow_card``).
2. Terminal-artifact summarizer for each artifact kind in the
   closed family.
3. ``run_template`` happy-path on the canonical Q1 and Q2 bindings
   (against synthetic DB fetchers).
4. ``run_template`` error envelopes (unknown template_id, slot
   binding failure, slot_constraints violation, validate-time
   refusal).
"""

from __future__ import annotations

import importlib

import pandas as pd
import pytest

from rates_agent.workflows._runner import (
    describe_workflow_card,
    list_workflow_cards,
    run_template,
    summarize_terminal,
)
from shared.artifacts.lineage import Lineage, OperatorStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import (
    EventSet,
    Panel,
    Series,
    SeriesSet,
    WindowedPanel,
)
from shared.artifacts.units import TimeSeriesUnits
from shared.workflow import (
    clear_template_registry,
    clear_workflow_template_cache,
    known_template_ids,
)

from tests._workflow_synthetic_fetchers import (
    CANONICAL_Q1_SLOT_VALUES,
    CANONICAL_Q2_SLOT_VALUES,
    q1_canonical_fetchers_context,
    q2_canonical_fetchers_context,
)


# ---------------------------------------------------------------------------
# Auto-fixture: ensure the templates are registered for every test in
# this file.  The substrate's process-wide registry is cleared between
# tests in the workflow-template suites; we re-import here to guarantee
# both templates are present when these runner tests run.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _registered_templates():
    """Reset the registry, then auto-register both templates by
    reloading their packages (each package's ``__init__.py`` calls
    ``register_template``).  Mirrors the discipline in the workflow-
    template tests."""
    from shared.config import clear_tool_config_cache
    clear_tool_config_cache()
    clear_workflow_template_cache()
    clear_template_registry()
    import rates_agent.workflows.event_study as es
    import rates_agent.workflows.regime_conditioned_relationship as rcr
    importlib.reload(es)
    importlib.reload(rcr)
    yield
    clear_tool_config_cache()
    clear_workflow_template_cache()
    clear_template_registry()


# ===========================================================================
# 1. Catalogue helpers
# ===========================================================================


class TestCatalogue:
    def test_list_workflow_cards_returns_both_templates(self):
        cards = list_workflow_cards()
        ids = [c["template_id"] for c in cards]
        assert "event_study" in ids
        assert "regime_conditioned_relationship" in ids

    def test_cards_are_sorted_by_template_id(self):
        cards = list_workflow_cards()
        ids = [c["template_id"] for c in cards]
        assert ids == sorted(ids), (
            "list_workflow_cards must return template-id-sorted "
            "cards for deterministic catalogue rendering"
        )

    def test_each_card_carries_the_uniform_fields(self):
        """Every card MUST carry the workflow-architecture-spec uniform
        shape so the LLM's catalogue iteration can be branch-free."""
        cards = list_workflow_cards()
        required_keys = {
            "template_id", "archetype", "description",
            "slot_schema", "terminal_artifact_type",
            "primitives_used", "operators_used",
            "node_count", "edge_count", "archetype_signature",
        }
        for card in cards:
            assert set(card.keys()) >= required_keys, (
                f"card {card.get('template_id')!r} missing required "
                f"keys: {required_keys - set(card.keys())}"
            )

    def test_describe_workflow_card_known_template(self):
        env = describe_workflow_card("event_study")
        assert env["ok"] is True
        card = env["card"]
        assert card["template_id"] == "event_study"
        assert card["archetype"] == "event_study"

    def test_describe_workflow_card_unknown_template_envelopes_error(self):
        env = describe_workflow_card("not_a_real_template")
        assert env["ok"] is False
        assert "not_a_real_template" in env["error"]
        # Caller can recover the catalogue from the error envelope.
        assert "known_template_ids" in env
        assert sorted(env["known_template_ids"]) == sorted(known_template_ids())


# ===========================================================================
# 2. Terminal-artifact summarizer
# ===========================================================================


def _trivial_lineage(name: str = "synthetic") -> Lineage:
    step = OperatorStep.build(
        name=name, version="1.0.0", params={}, input_hashes=(),
    )
    return Lineage.from_steps([step])


def _make_series(
    values, dates, *,
    units=TimeSeriesUnits.PERCENT,
    series_key: str = "x",
) -> Series:
    payload = pd.Series(
        values, index=pd.DatetimeIndex(dates), dtype=float,
    )
    return Series(
        series_key=series_key,
        payload=payload,
        units=units,
        frequency="B",
        missingness_policy=RawNoCleaning(),
        lineage=_trivial_lineage(),
    )


class TestSummarizeTerminal:
    def test_series_summary_carries_units_and_summary_stats(self):
        s = _make_series(
            [1.0, 2.0, 3.0, 4.0, 5.0],
            pd.bdate_range("2025-01-01", periods=5),
            units=TimeSeriesUnits.BPS,
        )
        out = summarize_terminal(s)
        assert out["type"] == "Series"
        assert out["units"] == "bps"
        assert out["n_rows"] == 5
        assert out["first_row"]["value"] == 1.0
        assert out["last_row"]["value"] == 5.0
        assert out["summary_stats"]["mean"] == 3.0
        assert out["summary_stats"]["min"] == 1.0
        assert out["summary_stats"]["max"] == 5.0

    def test_series_summary_handles_NaN_robustly(self):
        s = _make_series(
            [1.0, float("nan"), 3.0],
            pd.bdate_range("2025-01-01", periods=3),
        )
        out = summarize_terminal(s)
        assert out["n_rows"] == 3
        assert out["summary_stats"]["n_finite"] == 2
        # JSON-safe: NaN summary scalars are returned as None, not NaN.
        for key in ("mean", "min", "max"):
            assert out["summary_stats"][key] is not None

    def test_empty_series_summary_does_not_emit_first_last_row(self):
        # Pydantic forbids zero-row Series via the underlying Series
        # validator; build the summary directly with an empty payload
        # by constructing a near-empty Series and summarizing the
        # individual edge-case path through the empty branch.
        # We do this by passing a shaped-but-empty payload via the
        # internal summarize function (skip the wrapper).
        from rates_agent.workflows._runner import _summarize_series

        class _Stub:
            payload = pd.Series([], index=pd.DatetimeIndex([]), dtype=float)
            series_key = "empty"
            class _Units:
                value = "bps"
            units = _Units()
            frequency = None

        out = _summarize_series(_Stub())  # type: ignore[arg-type]
        assert out["type"] == "Series"
        assert out["n_rows"] == 0
        assert "first_row" not in out
        assert "last_row" not in out

    def test_series_set_summary(self):
        a = _make_series(
            [1.0, 2.0],
            pd.bdate_range("2025-01-01", periods=2),
            units=TimeSeriesUnits.PERCENT,
            series_key="alpha",
        )
        b = _make_series(
            [10.0, 20.0],
            pd.bdate_range("2025-01-01", periods=2),
            units=TimeSeriesUnits.BPS,
            series_key="beta",
        )
        from shared.operators.align_series import align_series, AlignSeriesParams
        sset = align_series(
            [a, b],
            AlignSeriesParams(
                join_policy="inner",
                require_matching_missingness=False,
            ),
        )
        out = summarize_terminal(sset)
        assert out["type"] == "SeriesSet"
        assert sorted(out["keys"]) == ["alpha", "beta"]
        assert out["units_by_key"]["alpha"] == "percent"
        assert out["units_by_key"]["beta"] == "bps"

    def test_event_set_summary(self):
        from shared.operators.threshold_events import (
            threshold_events,
            ThresholdEventsParams,
        )
        s = _make_series(
            [1.0, 2.0, 3.0, 4.0, 5.0],
            pd.bdate_range("2025-01-01", periods=5),
        )
        es_ = threshold_events(
            s, params=ThresholdEventsParams(
                rule="above", threshold=3.0,
                threshold_basis="raw_value", look_ahead_safe=True,
            ),
        )
        out = summarize_terminal(es_)
        assert out["type"] == "EventSet"
        assert out["n_dates"] == 5
        # rule=above threshold=3 → events at values 4,5 → n_events=2.
        assert out["n_events"] == 2

    def test_unknown_artifact_falls_back_gracefully(self):
        out = summarize_terminal("not_an_artifact")
        assert out["type"] == "str"
        assert out["summary"] == "unknown artifact"


# ===========================================================================
# 3. run_template happy paths
# ===========================================================================


class TestRunTemplateHappyPath:
    def test_q1_canonical_binding_runs_end_to_end(self):
        """Canonical Q1 binding (single-day swap-spread widening →
        UST 10Y forward move) returns ok=True with a Series terminal
        artifact in BPS."""
        with q1_canonical_fetchers_context():
            envelope = run_template(
                "event_study", dict(CANONICAL_Q1_SLOT_VALUES),
            )
        assert envelope["ok"] is True, envelope.get("error")
        assert envelope["template_id"] == "event_study"
        terminal = envelope["terminal_artifact"]
        assert terminal["type"] == "Series"
        assert terminal["units"] == "bps"
        # Conditional-vs-unconditional series has post_window+1 = 6 rows.
        assert terminal["n_rows"] == 6

    @pytest.mark.skip(
        reason=(
            "regime_conditioned_relationship template is on development "
            "pause (DISABLE_TEMPLATE_ROUTER=1); summarize_series migrated "
            "to ScalarMetric (GAP_LEDGER G01), intentionally breaking the "
            "template's sentinel-Series → series_arithmetic.subtract wiring."
        )
    )
    def test_q2_canonical_binding_runs_end_to_end(self):
        """Canonical Q2 binding (UST 10Y change vs 2Y SOFR OIS change,
        regime-conditioned on 2s10s curve daily move) returns ok=True
        with a 1-row Series at the summarize sentinel date."""
        with q2_canonical_fetchers_context():
            envelope = run_template(
                "regime_conditioned_relationship",
                dict(CANONICAL_Q2_SLOT_VALUES),
            )
        assert envelope["ok"] is True, envelope.get("error")
        assert envelope["template_id"] == "regime_conditioned_relationship"
        terminal = envelope["terminal_artifact"]
        assert terminal["type"] == "Series"
        # β has units RATIO (BPS / BPS).
        assert terminal["units"] == "ratio"
        # The terminal collapses to a single row at the
        # summarize_series sentinel date (1900-01-01).
        assert terminal["n_rows"] == 1
        assert terminal["first_row"]["date"] == "1900-01-01"

    def test_non_canonical_curve_family_binding_runs_end_to_end(self):
        """Param-aware synthetic fetchers (PR9 follow-up to the
        post-#91 gauntlet failure): an LLM binding that picks a
        non-canonical curve_family (UK_GILT + GBP_SONIA_OIS) MUST
        execute end-to-end.  Earlier the synthetic fetchers were
        hardcoded to UST/USD_SOFR_OIS; non-canonical bindings hit
        "Missing leg data" inside the swap_spread compute and the
        bridge then failed to validate the error envelope as
        SwapSpreadOutput.  This test locks the new behavior."""
        non_canonical = dict(CANONICAL_Q1_SLOT_VALUES)
        non_canonical["signal_params"] = {
            "sovereign_curve_family": "UK_GILT",
            "ois_curve_family": "GBP_SONIA_OIS",
            "tenor": "10Y",
            "lookback_days": 1825,
        }
        non_canonical["target_params"] = {
            "curve_family": "UK_GILT",
            "tenor": "10Y",
            "lookback_days": 1830,
        }
        non_canonical["threshold"] = 2.0

        with q1_canonical_fetchers_context():
            envelope = run_template("event_study", non_canonical)
        assert envelope["ok"] is True, envelope.get("error")
        assert envelope["terminal_artifact"]["type"] == "Series"
        # Sanity: lineage names every node, including align_series
        # (which exercises the pair of select_from_series_set
        # nodes), so the cross-calendar genericity branch ran.
        for nid in (
            "signal", "target",
            "align", "signal_aligned", "target_aligned",
            "events", "windows", "aggregate",
            "compare",
        ):
            assert nid in envelope["workflow_lineage_summary"]

    def test_non_canonical_binding_with_sovereign_curve_spread_signal(self):
        """An LLM picking ``calculate_curve_spread_tool`` (sovereign,
        UST 2s10s) for the signal MUST also execute end-to-end —
        proves the comprehensive synthetic-fetcher coverage covers
        every fetcher every V1 primitive uses, not just swap_spread
        + yield_levels."""
        non_canonical = dict(CANONICAL_Q1_SLOT_VALUES)
        non_canonical["signal_tool_name"] = "calculate_curve_spread_tool"
        non_canonical["signal_params"] = {
            "curve_family": "UST",
            "short_tenor": "2Y",
            "long_tenor": "10Y",
            "lookback_days": 1825,
        }
        non_canonical["signal_output_field"] = "time_series_zscore"
        non_canonical["target_tool_name"] = "get_yield_levels_tool"
        non_canonical["target_params"] = {
            "curve_family": "UST", "tenor": "30Y", "lookback_days": 1825,
        }
        non_canonical["threshold"] = 1.5
        with q1_canonical_fetchers_context():
            envelope = run_template("event_study", non_canonical)
        assert envelope["ok"] is True, envelope.get("error")

    def test_envelope_carries_workflow_lineage_summary(self):
        """The envelope MUST carry the substrate's
        ``workflow_lineage_summary`` so callers (LLM, CLI, eval
        harness) can render a human-readable trace of the executed
        DAG without parsing the structured per-artifact lineage."""
        with q1_canonical_fetchers_context():
            envelope = run_template(
                "event_study", dict(CANONICAL_Q1_SLOT_VALUES),
            )
        assert "workflow_lineage_summary" in envelope
        summary = envelope["workflow_lineage_summary"]
        # The summary names every executed node.
        for nid in (
            "signal", "target", "align",
            "signal_aligned", "target_aligned",
            "events", "windows", "aggregate",
            "unconditional_events", "unconditional_windows",
            "unconditional_aggregate", "compare",
        ):
            assert nid in summary, f"node {nid!r} missing from lineage summary"


# ===========================================================================
# 4. run_template error envelopes
# ===========================================================================


class TestRunTemplateErrorEnvelopes:
    """Each failure mode produces a uniform-shape envelope so the LLM
    / upstream router can react without parsing free-form prose."""

    def test_unknown_template_id(self):
        envelope = run_template("not_a_real_template", {})
        assert envelope["ok"] is False
        assert envelope["template_id"] == "not_a_real_template"
        assert "not_a_real_template" in envelope["error"]

    def test_slot_binding_failure_missing_required_slot(self):
        """Omitting a required slot surfaces as a SlotBindingError
        wrapped in the envelope's ``error`` field."""
        partial = dict(CANONICAL_Q1_SLOT_VALUES)
        partial.pop("threshold")
        envelope = run_template("event_study", partial)
        assert envelope["ok"] is False
        assert envelope["template_id"] == "event_study"
        assert "Slot binding failed" in envelope["error"]
        assert "threshold" in envelope["error"]

    def test_slot_binding_failure_unknown_slot(self):
        bad = dict(CANONICAL_Q1_SLOT_VALUES)
        bad["ghost_slot"] = "x"
        envelope = run_template("event_study", bad)
        assert envelope["ok"] is False
        assert "Slot binding failed" in envelope["error"]
        assert "ghost_slot" in envelope["error"]

    def test_slot_binding_failure_wrong_type(self):
        bad = dict(CANONICAL_Q1_SLOT_VALUES)
        bad["threshold"] = "not_a_float"
        envelope = run_template("event_study", bad)
        assert envelope["ok"] is False
        assert "Slot binding failed" in envelope["error"]

    def test_slot_constraints_violation_inverted_thresholds(self):
        """Codex P2 follow-up to PR #86: the regime template's
        ``high_threshold >= low_threshold`` constraint is enforced at
        bind time.  The runner must surface that as a slot-binding
        error, NOT as a downstream execution error."""
        bad = dict(CANONICAL_Q2_SLOT_VALUES)
        bad["high_threshold"] = -5.0
        bad["low_threshold"] = +5.0
        envelope = run_template(
            "regime_conditioned_relationship", bad,
        )
        assert envelope["ok"] is False
        assert "Slot binding failed" in envelope["error"]
        # The substrate's RelativeOrderConstraint message names both
        # slots; the rationale is included so the user sees WHY.
        assert "high_threshold" in envelope["error"]
        assert "low_threshold" in envelope["error"]

    def test_validate_time_refusal_unknown_primitive(self):
        """An unknown primitive tool_name surfaces at validate-time
        (the substrate validator probes the resolver before execute);
        the envelope tags it as a workflow-validation error, distinct
        from a slot-binding error."""
        bad = dict(CANONICAL_Q1_SLOT_VALUES)
        bad["signal_tool_name"] = "no_such_primitive_tool"
        envelope = run_template("event_study", bad)
        assert envelope["ok"] is False
        # Either binding or validation will reject; the envelope's
        # error MUST identify the offending primitive name so the
        # caller can self-correct.
        assert "no_such_primitive_tool" in envelope["error"]
