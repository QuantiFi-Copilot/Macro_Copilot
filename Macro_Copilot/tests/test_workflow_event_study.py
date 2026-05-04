"""tests/test_workflow_event_study.py — first standard workflow template.

Phase 2 PR 5.  Covers ``rates_agent/workflows/event_study/`` end-to-
end:

  1. Template structural validity (loads, validates, registers).
  2. ``archetype_signature`` cues are well-formed (≥1 cue, all
     non-empty, all ≤120 chars per the workflow_architecture spec).
  3. Slot binding rejects malformed inputs cleanly.
  4. **Real-rates end-to-end** — bind to proof-Q1 inputs (UST 10Y
     yield ↔ swap_spread 2Y signal), run via the rates primitive
     resolver against synthetic raw data (mocked DB fetchers),
     assert the terminal artifact is a Series with conditional-
     aggregate semantics + lineage extends through every node.
  5. **Mandatory instrument-agnostic test** — same template runs
     unchanged against a finance-blind synthetic primitive
     resolver.  This is the load-bearing requirement from
     ``workflow_architecture.md``: "every template MUST be capable
     of running unchanged against a non-rates synthetic primitive
     that emits canonical TimeSeries payloads through the bridge."
  6. Template card content — primitives_used reflect $slot
     references; operators_used reflect template-locked operators.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel

from rates_agent.workflows import (
    rates_primitive_resolver,
    known_rates_primitives,
)
from rates_agent.workflows.event_study import (
    EVENT_STUDY_TEMPLATE_PATH,
    load_event_study_template,
)
from shared.artifacts import Series, TimeSeriesUnits
from shared.schemas import TimeSeries, TimeSeriesRow
from shared.workflow import (
    OperatorNodeTemplate,
    PrimitiveNodeTemplate,
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
    """Reset all process-wide caches between tests so registrations
    + cached configs don't bleed across runs."""
    from shared.config import clear_tool_config_cache
    clear_tool_config_cache()
    clear_workflow_template_cache()
    clear_template_registry()
    yield
    clear_tool_config_cache()
    clear_workflow_template_cache()
    clear_template_registry()


# Simulated DB date — frozen so all per-primitive ``date.today()``
# resolves consistently across the multi-primitive workflow.
_FROZEN_TODAY = date(2026, 4, 30)


class _FrozenDate(date):
    @classmethod
    def today(cls) -> date:
        return _FROZEN_TODAY


def _synthetic_swap_spread_df(*, days: int = 600) -> pd.DataFrame:
    """Two-curve long-format frame for fetch_cross_domain_pair
    (the swap_spread fetcher).  UST 2Y vs USD_SOFR_OIS 2Y."""
    bdays = pd.bdate_range(_FROZEN_TODAY - timedelta(days=days * 2), _FROZEN_TODAY)[-days:]
    rs = np.random.RandomState(7)
    rows = []
    for cf, base, drift in (
        ("UST", 4.30, +0.20),
        ("USD_SOFR_OIS", 4.10, -0.10),
    ):
        n = len(bdays)
        v = np.linspace(base, base + drift, n) + rs.randn(n) * 0.012
        for d, val in zip(bdays, v):
            rows.append({
                "trade_date": d.date(), "curve_family": cf,
                "field_value": val,
            })
    return pd.DataFrame(rows)


def _synthetic_yield_levels_df(*, days: int = 600) -> pd.DataFrame:
    """Single-tenor frame for fetch_single_tenor (yield_levels fetcher)."""
    bdays = pd.bdate_range(_FROZEN_TODAY - timedelta(days=days * 2), _FROZEN_TODAY)[-days:]
    rs = np.random.RandomState(11)
    n = len(bdays)
    yields = np.linspace(4.20, 4.40, n) + rs.randn(n) * 0.015
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": yields,
    })


# ===========================================================================
# 1. Template structural validity
# ===========================================================================


class TestTemplateStructure:
    def test_template_yaml_exists(self):
        assert EVENT_STUDY_TEMPLATE_PATH.is_file()

    def test_template_loads_cleanly(self):
        t = load_event_study_template()
        assert t.template_id == "event_study"
        assert t.archetype == "event_study"

    def test_template_registers_on_import(self):
        # The event_study __init__.py auto-registers on import.
        # Force a re-import to populate the registry after the
        # autouse fixture cleared it.
        import importlib

        import rates_agent.workflows.event_study as es_module
        importlib.reload(es_module)

        registered = get_template("event_study")
        assert registered.template_id == "event_study"

    def test_template_in_listed_templates(self):
        import importlib
        import rates_agent.workflows.event_study as es_module
        importlib.reload(es_module)
        templates = list_templates(archetype="event_study")
        assert any(t.template_id == "event_study" for t in templates)

    def test_template_has_expected_nodes(self):
        t = load_event_study_template()
        node_ids = {n.node_id for n in t.nodes}
        assert node_ids == {"signal", "target", "events", "windows", "aggregate"}

    def test_template_terminal_is_aggregate(self):
        t = load_event_study_template()
        assert t.terminal_node_id == "aggregate"

    def test_template_locks_canonical_methodology(self):
        """Topology-locked params are NOT slot-substitutable.
        Pin the locks so a future template edit can't accidentally
        relax them without explicit review."""
        t = load_event_study_template()
        events_node = next(n for n in t.nodes if n.node_id == "events")
        windows_node = next(n for n in t.nodes if n.node_id == "windows")
        aggregate_node = next(n for n in t.nodes if n.node_id == "aggregate")
        # threshold_events: rule + basis + look_ahead_safe locked
        assert events_node.params["rule"] == "abs_above"
        assert events_node.params["threshold_basis"] == "raw_value"
        assert events_node.params["look_ahead_safe"] is True
        # event_windows: pre_window=0, inclusive_event_day=true locked
        assert windows_node.params["pre_window"] == 0
        assert windows_node.params["inclusive_event_day"] is True
        # conditional_aggregate: aggregator=mean, dispersion=std locked
        assert aggregate_node.params["aggregator"] == "mean"
        assert aggregate_node.params["dispersion"] == "std"


# ===========================================================================
# 2. archetype_signature contract
# ===========================================================================


class TestArchetypeSignature:
    """Per workflow_architecture.md, every shipped template MUST
    declare ≥1 archetype_signature cue so the future
    route_to_template LLM step can match prompts."""

    def test_at_least_one_cue_declared(self):
        t = load_event_study_template()
        assert len(t.archetype_signature) >= 1

    def test_all_cues_well_formed(self):
        t = load_event_study_template()
        for cue in t.archetype_signature:
            assert isinstance(cue, str)
            assert cue.strip()
            assert len(cue) <= 120

    def test_cues_propagate_to_card(self):
        t = load_event_study_template()
        card = card_for_template(t)
        assert card.archetype_signature == t.archetype_signature


# ===========================================================================
# 3. Slot binding refusals
# ===========================================================================


class TestSlotBinding:
    def _full_slot_values(self, **overrides):
        """Canonical proof-Q1 slot binding."""
        defaults = {
            "signal_tool_name": "calculate_swap_spread_tool",
            "signal_params": {
                "sovereign_curve_family": "UST",
                "ois_curve_family": "USD_SOFR_OIS",
                "tenor": "2Y",
                "lookback_days": 1825,
            },
            "signal_output_field": "time_series_zscore",
            "target_tool_name": "get_yield_levels_tool",
            "target_params": {
                "curve_family": "UST",
                "tenor": "10Y",
                "lookback_days": 1825,
            },
            "target_output_field": "time_series",
            "threshold": 1.5,
            "post_window": 5,
        }
        defaults.update(overrides)
        return defaults

    def test_full_canonical_binding_succeeds(self):
        t = load_event_study_template()
        wf = t.bind(self._full_slot_values())
        assert isinstance(wf, Workflow)

    def test_missing_required_slot_raises(self):
        t = load_event_study_template()
        partial = self._full_slot_values()
        partial.pop("signal_tool_name")
        with pytest.raises(SlotBindingError, match="required slot"):
            t.bind(partial)

    def test_unknown_slot_raises(self):
        t = load_event_study_template()
        bad = self._full_slot_values()
        bad["ghost_slot"] = "x"
        with pytest.raises(SlotBindingError, match="unknown slot"):
            t.bind(bad)

    def test_wrong_slot_type_raises(self):
        t = load_event_study_template()
        bad = self._full_slot_values()
        bad["threshold"] = "not_a_float"
        with pytest.raises(SlotBindingError, match="declared type"):
            t.bind(bad)

    def test_post_window_default_applied(self):
        """post_window defaults to 5 when caller omits it."""
        t = load_event_study_template()
        partial = self._full_slot_values()
        partial.pop("post_window")
        wf = t.bind(partial)
        windows_node = next(n for n in wf.nodes if n.node_id == "windows")
        assert windows_node.params["post_window"] == 5

    def test_explicit_post_window_overrides_default(self):
        t = load_event_study_template()
        wf = t.bind(self._full_slot_values(post_window=21))
        windows_node = next(n for n in wf.nodes if n.node_id == "windows")
        assert windows_node.params["post_window"] == 21


# ===========================================================================
# 4. Real-rates end-to-end (proof Q1)
# ===========================================================================


class TestEndToEndRealRates:
    """Bind to proof-Q1 inputs (UST 2Y swap spread → UST 10Y yield)
    and execute against the rates primitive resolver with mocked
    DB fetchers."""

    def _slot_values(self):
        return {
            "signal_tool_name": "calculate_swap_spread_tool",
            "signal_params": {
                "sovereign_curve_family": "UST",
                "ois_curve_family": "USD_SOFR_OIS",
                "tenor": "2Y",
                "lookback_days": 1825,
            },
            "signal_output_field": "time_series_zscore",
            "target_tool_name": "get_yield_levels_tool",
            "target_params": {
                "curve_family": "UST",
                "tenor": "10Y",
                "lookback_days": 1825,
            },
            "target_output_field": "time_series",
            "threshold": 1.5,
            "post_window": 5,
        }

    def test_proof_q1_runs_end_to_end(self):
        t = load_event_study_template()
        wf = t.bind(self._slot_values())

        # Mock both primitive fetchers.
        with patch(
            "rates_agent.ois.tools.swap_spread.compute.fetch_cross_domain_pair",
            return_value=_synthetic_swap_spread_df(),
        ), patch(
            "rates_agent.ois.tools.swap_spread.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.fetch_single_tenor",
            return_value=_synthetic_yield_levels_df(),
        ), patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.date",
            _FrozenDate,
        ):
            result = execute_workflow(
                wf, engine=None,
                primitive_resolver=rates_primitive_resolver,
            )

        # Terminal is a Series (conditional_aggregate output).
        assert isinstance(result.terminal_artifact, Series)

    def test_workflow_validates_with_real_resolver(self):
        """Pre-flight validation passes against the real rates
        resolver — every primitive tool_name resolves cleanly."""
        t = load_event_study_template()
        wf = t.bind(self._slot_values())
        # Should not raise.
        validate_workflow(wf, primitive_resolver=rates_primitive_resolver)

    def test_lineage_extends_through_every_node(self):
        t = load_event_study_template()
        wf = t.bind(self._slot_values())

        with patch(
            "rates_agent.ois.tools.swap_spread.compute.fetch_cross_domain_pair",
            return_value=_synthetic_swap_spread_df(),
        ), patch(
            "rates_agent.ois.tools.swap_spread.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.fetch_single_tenor",
            return_value=_synthetic_yield_levels_df(),
        ), patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.date",
            _FrozenDate,
        ):
            result = execute_workflow(
                wf, engine=None,
                primitive_resolver=rates_primitive_resolver,
            )

        # Every node produced an artifact.
        assert set(result.node_artifacts.keys()) == {
            "signal", "target", "events", "windows", "aggregate",
        }

    def test_workflow_lineage_summary_includes_every_node(self):
        t = load_event_study_template()
        wf = t.bind(self._slot_values())
        with patch(
            "rates_agent.ois.tools.swap_spread.compute.fetch_cross_domain_pair",
            return_value=_synthetic_swap_spread_df(),
        ), patch(
            "rates_agent.ois.tools.swap_spread.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.fetch_single_tenor",
            return_value=_synthetic_yield_levels_df(),
        ), patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.date",
            _FrozenDate,
        ):
            result = execute_workflow(
                wf, engine=None,
                primitive_resolver=rates_primitive_resolver,
            )
        summary = result.workflow_lineage_summary
        for nid in ("signal", "target", "events", "windows", "aggregate"):
            assert nid in summary


# ===========================================================================
# 5. MANDATORY instrument-agnostic test (workflow_architecture.md gate)
# ===========================================================================


# A finance-blind synthetic primitive.  Same shape used by the
# substrate's instrument-agnostic substrate tests in PR #78.


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


def _synthetic_signal_callable(*, engine, params, config) -> dict:
    """Synthetic Z_SCORE-like signal: a simulated random walk
    centered around 0 with volatility 1.  Some rows will exceed
    |z|=1.5 → the event_study template will find events."""
    rs = np.random.RandomState(42)
    bdays = pd.bdate_range(_FROZEN_TODAY - timedelta(days=900), _FROZEN_TODAY)[-params.n_rows:]
    values = rs.randn(len(bdays)) * params.volatility + params.base_value
    rows = [
        TimeSeriesRow(date=d.strftime("%Y-%m-%d"), value=float(v))
        for d, v in zip(bdays, values)
    ]
    return {
        "current_metrics": {"as_of_date": rows[-1].date},
        "time_series": {
            "series_name": params.series_name,
            "units": params.units,
            "description": "Synthetic signal series.",
            "rows": [r.model_dump() for r in rows],
        },
    }


def _synthetic_target_callable(*, engine, params, config) -> dict:
    """Synthetic target series: linear walk in PERCENT-like units."""
    rs = np.random.RandomState(99)
    bdays = pd.bdate_range(_FROZEN_TODAY - timedelta(days=900), _FROZEN_TODAY)[-params.n_rows:]
    values = np.linspace(
        params.base_value, params.base_value + 1.0, len(bdays),
    ) + rs.randn(len(bdays)) * 0.02
    rows = [
        TimeSeriesRow(date=d.strftime("%Y-%m-%d"), value=float(v))
        for d, v in zip(bdays, values)
    ]
    return {
        "current_metrics": {"as_of_date": rows[-1].date},
        "time_series": {
            "series_name": params.series_name,
            "units": params.units,
            "description": "Synthetic target series.",
            "rows": [r.model_dump() for r in rows],
        },
    }


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
    """Resolver that knows about exactly two synthetic primitives:
    one for the signal slot, one for the target slot.  Together they
    let the event_study template run against finance-blind data."""
    cfg_signal = tmp_path / "synthetic_signal.yaml"
    cfg_signal.write_text(_SYNTHETIC_CONFIG_YAML)
    cfg_target = tmp_path / "synthetic_target.yaml"
    cfg_target.write_text(_SYNTHETIC_CONFIG_YAML)

    signal_spec = PrimitiveSpec(
        tool_name="synthetic_signal_tool",
        callable=_synthetic_signal_callable,
        input_class=_SyntheticInput,
        output_class=_SyntheticOutput,
        config_path=cfg_signal,
        output_field_units={"time_series": "z_score"},
    )
    target_spec = PrimitiveSpec(
        tool_name="synthetic_target_tool",
        callable=_synthetic_target_callable,
        input_class=_SyntheticInput,
        output_class=_SyntheticOutput,
        config_path=cfg_target,
        output_field_units={"time_series": "percent"},
    )
    catalog = {
        "synthetic_signal_tool": signal_spec,
        "synthetic_target_tool": target_spec,
    }

    def _resolve(tool_name: str) -> PrimitiveSpec:
        if tool_name not in catalog:
            raise KeyError(f"unknown synthetic tool: {tool_name!r}")
        return catalog[tool_name]

    return _resolve


class TestInstrumentAgnostic:
    """MANDATORY per workflow_architecture.md: every template
    must run unchanged against a non-rates synthetic primitive
    that emits canonical TimeSeries through the bridge.

    This is the load-bearing test for instrument-agnosticism —
    if event_study can't run against synthetic primitives, it
    has hidden rates-specific assumptions and is overfit."""

    def test_event_study_runs_on_synthetic_primitives(
        self, synthetic_resolver,
    ):
        """Same template, same DAG topology, same operator
        configuration — only the (tool_name, output_field, params)
        slot bindings differ.  No template code change."""
        t = load_event_study_template()

        wf = t.bind({
            "signal_tool_name": "synthetic_signal_tool",
            "signal_params": {
                "series_name": "synthetic_z",
                "n_rows": 600,
                "volatility": 1.0,
            },
            "signal_output_field": "time_series",
            "target_tool_name": "synthetic_target_tool",
            "target_params": {
                "series_name": "synthetic_pct",
                "n_rows": 600,
                "base_value": 4.0,
            },
            "target_output_field": "time_series",
            "threshold": 1.5,
            "post_window": 5,
        })

        result = execute_workflow(
            wf, engine=None, primitive_resolver=synthetic_resolver,
        )

        # Same shape as the real-rates run.  Terminal is a Series.
        assert isinstance(result.terminal_artifact, Series)
        # Lineage chain extends through every node.
        assert set(result.node_artifacts.keys()) == {
            "signal", "target", "events", "windows", "aggregate",
        }


# ===========================================================================
# 6. Template card content
# ===========================================================================


class TestTemplateCard:
    def test_card_records_slot_substituted_primitives(self):
        t = load_event_study_template()
        card = card_for_template(t)
        # Both primitives are referenced via $slot — card records
        # the slot-substituted form, not concrete tool names.
        assert "<via $slot:signal_tool_name>" in card.primitives_used
        assert "<via $slot:target_tool_name>" in card.primitives_used

    def test_card_records_concrete_operator_names(self):
        t = load_event_study_template()
        card = card_for_template(t)
        # Operators are template-locked (NOT slot-substitutable),
        # so the card records their concrete names.
        assert set(card.operators_used) == {
            "threshold_events", "event_windows", "conditional_aggregate",
        }

    def test_card_terminal_artifact_type_is_Series(self):
        t = load_event_study_template()
        card = card_for_template(t)
        # conditional_aggregate emits Series per OPERATOR_REGISTRY.
        assert card.terminal_artifact_type == "Series"

    def test_card_node_and_edge_counts(self):
        t = load_event_study_template()
        card = card_for_template(t)
        assert card.node_count == 5
        assert card.edge_count == 4
