"""tests/test_workflow_cross_sectional_screen.py — first template for the
``cross_sectional_screen`` archetype.

Round 3 / Stage 4 PR-A8.  Covers
``rates_agent/workflows/cross_sectional_screen/`` end-to-end against the
WT15 five-layer contract:

  1. Structural validity — template loads, registers idempotently, has
     the locked 9-node DAG shape; archetype_signature cues meet WT14's
     constraints (4–10 cues, each ≤120 chars, non-empty); the
     topology-locked params declared in ``template.yaml``'s comment
     block are pinned so a future edit can't relax them silently.
  2. Slot-binding rejection — each of the four bind-time failure modes
     from WT12 (missing required slot / unknown slot / wrong type /
     declared-slot type-mismatch) raises ``SlotBindingError``.
  3. Real-rates end-to-end — bind to a canonical cross-country 10Y
     z-score scan (UST / BUND / GILT / JGB) and execute via
     ``rates_primitive_resolver`` with mocked ``fetch_single_tenor``;
     assert the terminal artifact is a 4-keyed ``SeriesSet``.
  4. MANDATORY instrument-agnostic test — same template runs unchanged
     against a finance-blind synthetic resolver (Pattern A from the
     workflow_template runbook: build a local ``PrimitiveResolver``
     from synthetic ``PrimitiveSpec`` entries).
  5. Topology-archetype-fit gate — the operators this template uses
     belong to the cross_sectional_screen archetype's structural family.
     Allow-list = {``summarize_series``, ``align_series``}.  Asserts the
     template does NOT use operators that belong to OTHER archetypes
     (e.g. ``event_windows`` for event_study; ``apply_mask`` +
     ``rolling_regression`` for regime_conditioned_relationship;
     ``construct_trades`` + ``evaluate_trades`` for backtest).  The gate
     is what prevents an `event_study`-shaped edit from drifting this
     template into the wrong archetype over time.

Per ADR 0013 (`docs_revamped/05_decisions/0013-cross-sectional-screen-
output-shape.md`): Option A verdict (SeriesSet keyed by member labels),
no closed-family extension, ranking deferred.  The terminal artifact is
``SeriesSet`` — the four-value desk surface for cross-sectional
comparison; ranking-by-value is a desk read at V1.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel

from rates_agent.workflows import rates_primitive_resolver
from rates_agent.workflows.cross_sectional_screen import (
    CROSS_SECTIONAL_SCREEN_TEMPLATE_PATH,
    load_cross_sectional_screen_template,
)
from shared.artifacts import Series, SeriesSet
from shared.schemas import TimeSeries, TimeSeriesRow
from shared.workflow import (
    OperatorNodeTemplate,
    PrimitiveResolver,
    PrimitiveSpec,
    SlotBindingError,
    Workflow,
    card_for_template,
    clear_template_registry,
    clear_workflow_template_cache,
    execute_workflow,
    get_template,
    list_templates,
    validate_workflow,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset all process-wide caches between tests so registrations +
    cached configs don't bleed across runs.  Mirrors the discipline in
    ``test_workflow_event_study.py``'s ``_clean_state``."""
    from shared.config import clear_tool_config_cache
    clear_tool_config_cache()
    clear_workflow_template_cache()
    clear_template_registry()
    yield
    clear_tool_config_cache()
    clear_workflow_template_cache()
    clear_template_registry()


# Frozen "as-of" so per-primitive ``date.today()`` resolves consistently
# across all four member branches.
_FROZEN_TODAY = date(2026, 4, 30)


class _FrozenDate(date):
    @classmethod
    def today(cls) -> date:
        return _FROZEN_TODAY


def _synthetic_yield_levels_df(*, days: int = 600, seed: int = 11) -> pd.DataFrame:
    """Single-tenor frame for ``fetch_single_tenor`` (used by
    ``get_yield_levels_tool`` AND ``calculate_zscore_custom_tool``).
    Mirrors the synthesis pattern in test_workflow_event_study.py so
    the two test suites share their data-shape conventions."""
    bdays = pd.bdate_range(
        _FROZEN_TODAY - timedelta(days=days * 2), _FROZEN_TODAY,
    )[-days:]
    rs = np.random.RandomState(seed)
    n = len(bdays)
    yields = np.linspace(4.20, 4.40, n) + rs.randn(n) * 0.015
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": yields,
    })


# ===========================================================================
# 1. Structural validity (WT15 layer 1, WT13, WT14, WT16)
# ===========================================================================


class TestTemplateStructure:
    def test_template_yaml_exists(self):
        assert CROSS_SECTIONAL_SCREEN_TEMPLATE_PATH.is_file()

    def test_template_loads_cleanly(self):
        t = load_cross_sectional_screen_template()
        assert t.template_id == "cross_sectional_screen"
        assert t.archetype == "cross_sectional_screen"

    def test_template_registers_on_import(self):
        # Force a re-import to populate the registry after the
        # autouse fixture cleared it.  Same pattern as
        # test_workflow_event_study.py.
        import importlib

        import rates_agent.workflows.cross_sectional_screen as css_module
        importlib.reload(css_module)

        registered = get_template("cross_sectional_screen")
        assert registered.template_id == "cross_sectional_screen"

    def test_template_in_listed_templates(self):
        import importlib
        import rates_agent.workflows.cross_sectional_screen as css_module
        importlib.reload(css_module)
        templates = list_templates(archetype="cross_sectional_screen")
        assert any(
            t.template_id == "cross_sectional_screen" for t in templates
        )

    def test_template_has_expected_nodes(self):
        """Locked 9-node DAG: 4 member primitives → 4 per-member
        summarize_series → 1 align_series terminal (= ``screen``)."""
        t = load_cross_sectional_screen_template()
        node_ids = {n.node_id for n in t.nodes}
        assert node_ids == {
            # Member primitives
            "member_1", "member_2", "member_3", "member_4",
            # Per-member single-row summaries at sentinel 1900-01-01
            "summary_1", "summary_2", "summary_3", "summary_4",
            # Terminal: align the 4 summaries into a SeriesSet
            "screen",
        }

    def test_template_terminal_is_screen(self):
        t = load_cross_sectional_screen_template()
        assert t.terminal_node_id == "screen"

    def test_template_has_expected_edge_count(self):
        """8 edges: 4 × (member_n → summary_n) + 4 × (summary_n →
        screen.series_list).  Per align_series's contract, multiple
        edges into the same ``series_list`` slot aggregate into a list
        at execution time."""
        t = load_cross_sectional_screen_template()
        assert len(t.edges) == 8

    def test_terminal_node_is_align_series(self):
        """The terminal MUST be an ``align_series`` operator emitting
        a ``SeriesSet`` — that's the load-bearing decision of ADR 0013
        (Option A)."""
        t = load_cross_sectional_screen_template()
        terminal_node = next(n for n in t.nodes if n.node_id == "screen")
        assert isinstance(terminal_node, OperatorNodeTemplate)
        assert terminal_node.operator_name == "align_series"

    def test_template_locks_canonical_methodology(self):
        """Topology-locked params per WT8 — pin them so a future edit
        can't accidentally relax them without explicit review.  These
        choices are documented in ``template.yaml``'s comment block."""
        t = load_cross_sectional_screen_template()
        nodes = {n.node_id: n for n in t.nodes}
        # Each summary node's dispersion is locked to "std" (always-
        # report-dispersion discipline).  Only ``statistic`` is slot-
        # substituted (caller knob); dispersion is template-author-locked.
        for sid in ("summary_1", "summary_2", "summary_3", "summary_4"):
            assert nodes[sid].operator_name == "summarize_series"
            assert nodes[sid].params["dispersion"] == "std"
            # statistic is a $slot reference, not a literal.
            assert nodes[sid].params["statistic"] == {"$slot": "statistic"}
        # align_series terminal: inner-join + raw fill_policy + both
        # frequency / missingness gates relaxed (rationale in YAML).
        screen = nodes["screen"]
        assert screen.params["join_policy"] == "inner"
        assert screen.params["fill_policy"] == "raw"
        assert screen.params["require_matching_frequency"] is False
        assert screen.params["require_matching_missingness"] is False
        # output_keys is a $slot ref to member_labels (caller-controlled).
        assert screen.params["output_keys"] == {"$slot": "member_labels"}


class TestArchetypeSignature:
    """WT14: every shipped user-facing template declares 4–10 cues,
    each non-empty + ≤120 chars."""

    def test_cue_count_in_review_gate_range(self):
        t = load_cross_sectional_screen_template()
        assert 4 <= len(t.archetype_signature) <= 10

    def test_all_cues_well_formed(self):
        t = load_cross_sectional_screen_template()
        for cue in t.archetype_signature:
            assert isinstance(cue, str)
            assert cue.strip()
            assert len(cue) <= 120

    def test_cues_disjoint_from_event_study(self):
        """WT5 + WT14: sibling templates within the same archetype
        family have disjoint cues; templates across archetypes also
        should not share cues so the LLM router routes cleanly.  This
        test pins disjoint-ness vs the existing user-facing templates'
        cues."""
        from rates_agent.workflows.event_study import (
            load_event_study_template,
        )
        from rates_agent.workflows.regime_conditioned_relationship import (
            load_regime_conditioned_template,
        )

        css = load_cross_sectional_screen_template()
        es = load_event_study_template()
        rcr = load_regime_conditioned_template()

        css_cues = set(css.archetype_signature)
        # No exact overlap with either existing template's cues.
        assert css_cues.isdisjoint(set(es.archetype_signature))
        assert css_cues.isdisjoint(set(rcr.archetype_signature))

    def test_cues_propagate_to_card(self):
        t = load_cross_sectional_screen_template()
        card = card_for_template(t)
        assert card.archetype_signature == t.archetype_signature


# ===========================================================================
# 2. Slot-binding rejection (WT12, WT15 layer 2)
# ===========================================================================


class TestSlotBinding:
    """Each of the four bind-time failure modes from WT12 raises
    ``SlotBindingError`` BEFORE a concrete ``Workflow`` is constructed.
    No silent fallback at any of the four boundaries."""

    def _full_slot_values(self, **overrides) -> dict:
        """Canonical V1 binding: cross-country 10Y z-score scan over
        the four largest sovereign families.  Mirrors the example in
        ``template.yaml``'s comment block."""
        defaults = {
            "member_1_tool_name": "calculate_zscore_custom_tool",
            "member_1_params": {
                "curve_family": "UST",
                "tenor": "10Y",
                "z_score_window_days": 252,
                "lookback_days": 30,
            },
            "member_1_output_field": "time_series_zscore",
            "member_2_tool_name": "calculate_zscore_custom_tool",
            "member_2_params": {
                "curve_family": "DE_BUND",
                "tenor": "10Y",
                "z_score_window_days": 252,
                "lookback_days": 30,
            },
            "member_2_output_field": "time_series_zscore",
            "member_3_tool_name": "calculate_zscore_custom_tool",
            "member_3_params": {
                "curve_family": "UK_GILT",
                "tenor": "10Y",
                "z_score_window_days": 252,
                "lookback_days": 30,
            },
            "member_3_output_field": "time_series_zscore",
            "member_4_tool_name": "calculate_zscore_custom_tool",
            "member_4_params": {
                "curve_family": "JGB",
                "tenor": "10Y",
                "z_score_window_days": 252,
                "lookback_days": 30,
            },
            "member_4_output_field": "time_series_zscore",
            "member_labels": ["UST_10Y", "BUND_10Y", "GILT_10Y", "JGB_10Y"],
            "statistic": "latest",
        }
        defaults.update(overrides)
        return defaults

    def test_full_canonical_binding_succeeds(self):
        t = load_cross_sectional_screen_template()
        wf = t.bind(self._full_slot_values())
        assert isinstance(wf, Workflow)
        assert len(wf.nodes) == 9
        assert len(wf.edges) == 8

    def test_missing_required_slot_raises(self):
        t = load_cross_sectional_screen_template()
        partial = self._full_slot_values()
        partial.pop("member_1_tool_name")
        with pytest.raises(SlotBindingError, match="required slot"):
            t.bind(partial)

    def test_unknown_slot_raises(self):
        t = load_cross_sectional_screen_template()
        bad = self._full_slot_values()
        bad["ghost_slot"] = "x"
        with pytest.raises(SlotBindingError, match="unknown slot"):
            t.bind(bad)

    def test_wrong_slot_type_raises(self):
        """``member_labels`` is declared ``list``; supplying a str
        must raise SlotBindingError, not silently coerce."""
        t = load_cross_sectional_screen_template()
        bad = self._full_slot_values()
        bad["member_labels"] = "UST_10Y,BUND_10Y,GILT_10Y,JGB_10Y"
        with pytest.raises(SlotBindingError, match="declared type"):
            t.bind(bad)

    def test_member_params_must_be_dict(self):
        """Each member_N_params is declared ``dict``; passing a str
        must raise."""
        t = load_cross_sectional_screen_template()
        bad = self._full_slot_values()
        bad["member_1_params"] = "not_a_dict"
        with pytest.raises(SlotBindingError, match="declared type"):
            t.bind(bad)

    def test_statistic_default_applied_when_omitted(self):
        """``statistic`` is optional with default ``latest`` — the
        desk's snapshot-at-as-of-date reading.  Default changed from
        ``mean`` to ``latest`` in PR-A8 follow-up (Codex F1 on PR #201)
        when ``latest`` was added to summarize_series."""
        t = load_cross_sectional_screen_template()
        partial = self._full_slot_values()
        partial.pop("statistic")
        wf = t.bind(partial)
        summary_1 = next(n for n in wf.nodes if n.node_id == "summary_1")
        assert summary_1.params["statistic"] == "latest"

    def test_statistic_override_to_mean_still_works(self):
        """Callers wanting a window-average read can override the
        default by passing ``statistic='mean'`` explicitly.  Pins the
        non-default path so the override surface stays exercised."""
        t = load_cross_sectional_screen_template()
        wf = t.bind(self._full_slot_values(statistic="mean"))
        summary_1 = next(n for n in wf.nodes if n.node_id == "summary_1")
        assert summary_1.params["statistic"] == "mean"

    def test_member_labels_propagate_to_align_output_keys(self):
        """The ``member_labels`` slot is substituted into
        ``align_series.output_keys`` at the terminal ``screen`` node."""
        t = load_cross_sectional_screen_template()
        wf = t.bind(self._full_slot_values(
            member_labels=["A", "B", "C", "D"],
        ))
        screen = next(n for n in wf.nodes if n.node_id == "screen")
        assert screen.params["output_keys"] == ["A", "B", "C", "D"]


# ===========================================================================
# 3. Real-rates end-to-end (WT15 layer 3)
# ===========================================================================


class TestEndToEndRealRates:
    """Bind the canonical cross-country 10Y z-score scan and execute
    against ``rates_primitive_resolver`` with mocked
    ``fetch_single_tenor``.  Validates the full operator chain runs
    end-to-end on real-shaped primitive output."""

    def _slot_values(self):
        return {
            "member_1_tool_name": "calculate_zscore_custom_tool",
            "member_1_params": {
                "curve_family": "UST",
                "tenor": "10Y",
                "z_score_window_days": 252,
                "lookback_days": 30,
            },
            "member_1_output_field": "time_series_zscore",
            "member_2_tool_name": "calculate_zscore_custom_tool",
            "member_2_params": {
                "curve_family": "DE_BUND",
                "tenor": "10Y",
                "z_score_window_days": 252,
                "lookback_days": 30,
            },
            "member_2_output_field": "time_series_zscore",
            "member_3_tool_name": "calculate_zscore_custom_tool",
            "member_3_params": {
                "curve_family": "UK_GILT",
                "tenor": "10Y",
                "z_score_window_days": 252,
                "lookback_days": 30,
            },
            "member_3_output_field": "time_series_zscore",
            "member_4_tool_name": "calculate_zscore_custom_tool",
            "member_4_params": {
                "curve_family": "JGB",
                "tenor": "10Y",
                "z_score_window_days": 252,
                "lookback_days": 30,
            },
            "member_4_output_field": "time_series_zscore",
            "member_labels": ["UST_10Y", "BUND_10Y", "GILT_10Y", "JGB_10Y"],
            "statistic": "latest",
        }

    def test_workflow_validates_with_real_resolver(self):
        """Pre-flight validation passes against the real rates
        resolver — every primitive ``tool_name`` resolves and every
        edge's source-output type is compatible with the target
        slot's declared type."""
        t = load_cross_sectional_screen_template()
        wf = t.bind(self._slot_values())
        validate_workflow(wf, primitive_resolver=rates_primitive_resolver)

    def test_canonical_binding_runs_end_to_end(self):
        """The full DAG executes; terminal is a ``SeriesSet`` keyed by
        the four caller-supplied labels."""
        t = load_cross_sectional_screen_template()
        wf = t.bind(self._slot_values())

        # All four members fetch via ``fetch_single_tenor`` (zscore_custom's
        # fetcher).  One mock serves all four — same DataFrame per call
        # since zscore_custom's compute pulls per-(curve_family, tenor)
        # rows and we don't need cross-curve cross-validation here.
        with patch(
            "rates_agent.sovereign_bonds.tools.zscore_custom.compute.fetch_single_tenor",
            return_value=_synthetic_yield_levels_df(),
        ), patch(
            "rates_agent.sovereign_bonds.tools.zscore_custom.compute.date",
            _FrozenDate,
        ):
            result = execute_workflow(
                wf, engine=None,
                primitive_resolver=rates_primitive_resolver,
            )

        assert isinstance(result.terminal_artifact, SeriesSet)
        # The SeriesSet has exactly four members keyed by member_labels.
        assert set(result.terminal_artifact.keys()) == {
            "UST_10Y", "BUND_10Y", "GILT_10Y", "JGB_10Y",
        }

    def test_lineage_extends_through_every_node(self):
        """Every node in the DAG produced an artifact (no orphan
        branches, no skipped nodes)."""
        t = load_cross_sectional_screen_template()
        wf = t.bind(self._slot_values())

        with patch(
            "rates_agent.sovereign_bonds.tools.zscore_custom.compute.fetch_single_tenor",
            return_value=_synthetic_yield_levels_df(),
        ), patch(
            "rates_agent.sovereign_bonds.tools.zscore_custom.compute.date",
            _FrozenDate,
        ):
            result = execute_workflow(
                wf, engine=None,
                primitive_resolver=rates_primitive_resolver,
            )

        assert set(result.node_artifacts.keys()) == {
            "member_1", "member_2", "member_3", "member_4",
            "summary_1", "summary_2", "summary_3", "summary_4",
            "screen",
        }

    def test_envelope_includes_member_values_by_key(self):
        """Codex F2 fix verification: the MCP envelope returned by
        ``run_template`` must include the actual member values, not
        just keys + units + dates.  Without this fix, a terminal
        SeriesSet would render to the LLM/user as "4 keys + 0 numbers"
        — the screen's product would be structurally invisible.

        Asserts both ``values_by_key`` (single-row case, the desk's
        snapshot reading) and ``latest_value_by_key`` (always present)
        are populated in the envelope's terminal_artifact summary."""
        from rates_agent.workflows._runner import run_template_with_resolver

        # Re-register after the autouse fixture's clear_template_registry()
        # call.  ``run_template_with_resolver`` resolves the template by id
        # against the substrate's process-wide registry — unlike the other
        # tests in this class which call ``execute_workflow`` directly on a
        # bound Workflow.
        import rates_agent.workflows.cross_sectional_screen as css_module
        css_module.register()

        with patch(
            "rates_agent.sovereign_bonds.tools.zscore_custom.compute.fetch_single_tenor",
            return_value=_synthetic_yield_levels_df(),
        ), patch(
            "rates_agent.sovereign_bonds.tools.zscore_custom.compute.date",
            _FrozenDate,
        ):
            envelope = run_template_with_resolver(
                template_id="cross_sectional_screen",
                slot_values=self._slot_values(),
                engine=None,
                primitive_resolver=rates_primitive_resolver,
                persist=False,
            )

        assert envelope["ok"] is True, envelope.get("error")
        terminal = envelope["terminal_artifact"]
        assert terminal["type"] == "SeriesSet"
        # n_rows = 1 (single-row sentinel-aligned summaries).
        assert terminal["n_rows"] == 1
        # latest_value_by_key is ALWAYS present for SeriesSet terminals.
        latest = terminal["latest_value_by_key"]
        assert set(latest.keys()) == {
            "UST_10Y", "BUND_10Y", "GILT_10Y", "JGB_10Y",
        }
        for k, v in latest.items():
            assert isinstance(v, float), (
                f"latest_value_by_key[{k!r}]={v!r} must be a float; "
                f"got {type(v).__name__}"
            )
        # values_by_key is also present because every member is single-
        # row (the cross_sectional_screen / summarize_series terminal
        # case).  Single-row case ⇒ values == latest values.
        assert "values_by_key" in terminal, (
            "Single-row SeriesSet terminal must expose values_by_key "
            "(Codex F2 fix); got terminal keys: "
            f"{sorted(terminal.keys())}"
        )
        assert terminal["values_by_key"] == latest


# ===========================================================================
# 4. MANDATORY instrument-agnostic test (WT15 layer 4 — load-bearing)
# ===========================================================================
#
# Pattern A per workflow_template/runbook.md §6d: every member's
# ``tool_name`` is a $slot reference, so we build a local
# ``PrimitiveResolver`` from synthetic ``PrimitiveSpec`` entries and
# verify the template runs unchanged.  No rates-specific assumption
# can leak into the operator substrate.


class _SyntheticInput(BaseModel):
    series_name: str = "synthetic"
    n_rows: int = 600
    base_value: float = 0.0
    volatility: float = 1.0
    units: str = "z_score"


class _SyntheticOutput(BaseModel):
    class _Metrics(BaseModel):
        as_of_date: str

    current_metrics: "_SyntheticOutput._Metrics"
    time_series: TimeSeries


_SyntheticOutput.model_rebuild()


def _synthetic_member_callable_factory(seed: int):
    """Each member needs an independent synthetic Series; this factory
    builds a callable per-member with a distinct RNG seed so the
    summaries are not byte-identical (which would mask a real bug
    where align_series fails to differentiate inputs)."""

    def _callable(*, engine, params, config) -> dict:
        rs = np.random.RandomState(seed)
        bdays = pd.bdate_range(
            _FROZEN_TODAY - timedelta(days=900), _FROZEN_TODAY,
        )[-params.n_rows:]
        values = (
            rs.randn(len(bdays)) * params.volatility + params.base_value
        )
        rows = [
            TimeSeriesRow(date=d.strftime("%Y-%m-%d"), value=float(v))
            for d, v in zip(bdays, values)
        ]
        return {
            "current_metrics": {"as_of_date": rows[-1].date},
            "time_series": {
                "series_name": params.series_name,
                "units": params.units,
                "description": (
                    f"Synthetic member series (seed={seed})."
                ),
                "rows": [r.model_dump() for r in rows],
            },
        }

    return _callable


_SYNTHETIC_CONFIG_YAML = """
tool:
  name: synthetic_workflow_primitive
  domain: synthetic
  description: Synthetic primitive for instrument-agnostic template tests.
  category: desk_invariant_primitive
conventions:
  ffill_limit_days:
    value: 5
    source: synthetic_test_default
    rationale: synthetic config
methodology:
  what_it_does: >-
    Returns a deterministic synthetic series for instrument-agnostic
    template tests.
"""


@pytest.fixture
def synthetic_resolver(tmp_path) -> PrimitiveResolver:
    """Resolver that knows about four synthetic member primitives —
    one per slot.  Each emits a Z_SCORE-typed Series with a distinct
    RNG seed so the four post-summary values are non-degenerate."""

    catalog: dict[str, PrimitiveSpec] = {}
    for i, seed in enumerate((42, 99, 137, 271), start=1):
        cfg = tmp_path / f"synthetic_member_{i}.yaml"
        cfg.write_text(_SYNTHETIC_CONFIG_YAML)
        catalog[f"synthetic_member_{i}_tool"] = PrimitiveSpec(
            tool_name=f"synthetic_member_{i}_tool",
            callable=_synthetic_member_callable_factory(seed),
            input_class=_SyntheticInput,
            output_class=_SyntheticOutput,
            config_path=cfg,
            output_field_units={"time_series": "z_score"},
        )

    def _resolve(tool_name: str) -> PrimitiveSpec:
        if tool_name not in catalog:
            raise KeyError(f"unknown synthetic tool: {tool_name!r}")
        return catalog[tool_name]

    return _resolve


class TestInstrumentAgnostic:
    """MANDATORY per WT15 layer 4 + WT3.  If this fails, the template's
    operator substrate has hidden rates-specific assumptions and the
    asset-class-blindness claim is unverified."""

    def test_template_runs_on_synthetic_primitives(
        self, synthetic_resolver,
    ):
        """Same template, same DAG topology, same operator
        configuration — only the (tool_name, output_field, params)
        slot bindings differ.  No template code change."""
        t = load_cross_sectional_screen_template()

        wf = t.bind({
            "member_1_tool_name": "synthetic_member_1_tool",
            "member_1_params": {
                "series_name": "synth_1",
                "n_rows": 600,
                "volatility": 1.0,
                "units": "z_score",
            },
            "member_1_output_field": "time_series",
            "member_2_tool_name": "synthetic_member_2_tool",
            "member_2_params": {
                "series_name": "synth_2",
                "n_rows": 600,
                "volatility": 1.0,
                "units": "z_score",
            },
            "member_2_output_field": "time_series",
            "member_3_tool_name": "synthetic_member_3_tool",
            "member_3_params": {
                "series_name": "synth_3",
                "n_rows": 600,
                "volatility": 1.0,
                "units": "z_score",
            },
            "member_3_output_field": "time_series",
            "member_4_tool_name": "synthetic_member_4_tool",
            "member_4_params": {
                "series_name": "synth_4",
                "n_rows": 600,
                "volatility": 1.0,
                "units": "z_score",
            },
            "member_4_output_field": "time_series",
            "member_labels": ["MBR_A", "MBR_B", "MBR_C", "MBR_D"],
            "statistic": "mean",
        })

        result = execute_workflow(
            wf, engine=None, primitive_resolver=synthetic_resolver,
        )

        # Same terminal shape as the real-rates run — SeriesSet keyed
        # by member labels.
        assert isinstance(result.terminal_artifact, SeriesSet)
        assert set(result.terminal_artifact.keys()) == {
            "MBR_A", "MBR_B", "MBR_C", "MBR_D",
        }
        # Lineage extends through every node in both the primitive
        # layer (4 members), the summary layer (4 summaries), and the
        # terminal alignment.
        assert set(result.node_artifacts.keys()) == {
            "member_1", "member_2", "member_3", "member_4",
            "summary_1", "summary_2", "summary_3", "summary_4",
            "screen",
        }

    def test_each_member_summary_is_a_distinct_single_row_series(
        self, synthetic_resolver,
    ):
        """The four summary_n nodes each emit a single-row Series at
        the sentinel date.  With distinct RNG seeds per member, the
        four summary values must NOT all be equal (degenerate
        substitution would have made every member's output identical;
        this test catches that failure mode)."""
        t = load_cross_sectional_screen_template()

        binding: dict[str, Any] = {
            "member_labels": ["A", "B", "C", "D"],
            "statistic": "mean",
        }
        for i in (1, 2, 3, 4):
            binding[f"member_{i}_tool_name"] = f"synthetic_member_{i}_tool"
            binding[f"member_{i}_params"] = {
                "series_name": f"synth_{i}",
                "n_rows": 600,
                "volatility": 1.0,
                "units": "z_score",
            }
            binding[f"member_{i}_output_field"] = "time_series"

        wf = t.bind(binding)
        result = execute_workflow(
            wf, engine=None, primitive_resolver=synthetic_resolver,
        )

        # Each summary node produced exactly one row (single-row
        # summary at the sentinel date 1900-01-01).  Pull the four
        # summary values; with distinct RNG seeds they should not all
        # coincide.
        summary_values = []
        for sid in ("summary_1", "summary_2", "summary_3", "summary_4"):
            summary_series = result.node_artifacts[sid]
            assert isinstance(summary_series, Series)
            assert len(summary_series.payload) == 1
            summary_values.append(float(summary_series.payload.iloc[0]))
        # Non-degenerate: at least two values differ.
        assert len(set(round(v, 8) for v in summary_values)) >= 2


# ===========================================================================
# 5. Topology-archetype-fit gate (WT15 layer 5)
# ===========================================================================
#
# Per WT15: "assertions that the template uses only operators that
# belong to its archetype's structural family."  Allow-list, not
# deny-list (deny-lists drift; allow-lists encode the archetype's
# structural identity explicitly).
#
# For ``cross_sectional_screen`` Option A:
#   - ``summarize_series`` (per-member sentinel-date reduction)
#   - ``align_series``     (multi-member join → SeriesSet terminal)
#
# Any future edit that introduces an event-study operator
# (``event_windows``, ``conditional_aggregate``, ``threshold_events``)
# or a regime operator (``apply_mask``, ``rolling_regression``,
# ``select_from_series_set``) or a backtest operator
# (``construct_trades``, ``evaluate_trades``, ``summarize_trades``)
# fails this gate — and the template author must either justify the
# scope expansion via ADR or roll the change back.


_CROSS_SECTIONAL_SCREEN_ALLOWED_OPERATORS = frozenset({
    "summarize_series",
    "align_series",
})


class TestTopologyArchetypeFit:
    """Allow-list gate.  Re-asserts the archetype's structural identity
    every time the test suite runs."""

    def test_operator_set_matches_allow_list(self):
        """Every operator node in the DAG is in the allow-list.  An
        edit that adds a new operator outside this set must update
        the allow-list AND justify the scope (almost certainly via
        a sibling template per WT5 rather than a same-template
        addition)."""
        t = load_cross_sectional_screen_template()
        operator_names = {
            n.operator_name
            for n in t.nodes
            if isinstance(n, OperatorNodeTemplate)
        }
        assert operator_names == _CROSS_SECTIONAL_SCREEN_ALLOWED_OPERATORS

    def test_no_event_study_operators(self):
        """Explicit pin: an event-study-shaped operator showing up
        here would mean the template is drifting toward the wrong
        archetype.  Codex P3 follow-up shape from
        test_workflow_regime_conditioned_relationship.py."""
        t = load_cross_sectional_screen_template()
        operator_names = {
            n.operator_name
            for n in t.nodes
            if isinstance(n, OperatorNodeTemplate)
        }
        for forbidden in (
            "event_windows", "conditional_aggregate", "threshold_events",
        ):
            assert forbidden not in operator_names

    def test_no_regime_operators(self):
        """A regime-conditioned-relationship-shaped operator would
        mean the template is masquerading as the wrong archetype."""
        t = load_cross_sectional_screen_template()
        operator_names = {
            n.operator_name
            for n in t.nodes
            if isinstance(n, OperatorNodeTemplate)
        }
        for forbidden in (
            "apply_mask", "rolling_regression", "select_from_series_set",
        ):
            assert forbidden not in operator_names

    def test_no_backtest_operators(self):
        """A backtest-shaped operator (construct/evaluate/summarize
        trades) would mean the template has crossed into the wrong
        archetype's structural family."""
        t = load_cross_sectional_screen_template()
        operator_names = {
            n.operator_name
            for n in t.nodes
            if isinstance(n, OperatorNodeTemplate)
        }
        for forbidden in (
            "construct_trades", "evaluate_trades", "summarize_trades",
        ):
            assert forbidden not in operator_names
