"""tests/test_workflow_event_study.py — first standard workflow template.

Phase 2 PR 5.  Covers ``rates_agent/workflows/event_study/`` end-to-
end:

  1. Template structural validity (loads, validates, registers, has
     the canonical conditional-vs-unconditional 9-node DAG shape).
  2. ``archetype_signature`` cues are well-formed (≥1 cue, all
     non-empty, all ≤120 chars per the workflow_architecture spec).
  3. Slot binding rejects malformed inputs cleanly.
  4. **Real-rates end-to-end** — bind to proof-Q1 inputs (UST 10Y
     yield ↔ swap_spread 2Y signal), run via the rates primitive
     resolver against synthetic raw data (mocked DB fetchers),
     assert the terminal artifact is the abnormal Series in BPS
     (PERCENT target × level_change → BPS) and lineage extends
     through every node in both branches plus the comparison.
  5. **Mandatory instrument-agnostic test** — same template runs
     unchanged against a finance-blind synthetic primitive
     resolver.  This is the load-bearing requirement from
     ``workflow_architecture.md``: "every template MUST be capable
     of running unchanged against a non-rates synthetic primitive
     that emits canonical TimeSeries payloads through the bridge."
  6. Template card content — primitives_used reflect $slot
     references; operators_used reflect template-locked operators
     (including the comparison operator).
  7. Resolver completeness — every primitive surfaced in the
     template's slot-schema docstring examples is registered in
     ``rates_primitive_resolver`` (Codex P2 follow-up: the docs
     promised ``calculate_ois_forward_rate_tool`` and the resolver
     must back that promise).
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
        """The canonical event_study DAG shape per
        workflow_architecture.md (lines 73-74): two parallel
        branches (conditional + unconditional) joined by a
        per-offset subtraction = abnormal forward move.  Codex P2
        follow-up to PR #86 added an explicit alignment branch
        (``align`` + 2× ``select_from_series_set``) so the same
        template runs unchanged across instrument families with
        different trading-day calendars."""
        t = load_event_study_template()
        node_ids = {n.node_id for n in t.nodes}
        assert node_ids == {
            # Primitives
            "signal", "target",
            # Cross-calendar alignment branch (Codex P2 follow-up)
            "align", "signal_aligned", "target_aligned",
            # Conditional branch
            "events", "windows", "aggregate",
            # Unconditional branch
            "unconditional_events", "unconditional_windows",
            "unconditional_aggregate",
            # Comparison
            "compare",
        }

    def test_template_terminal_is_compare(self):
        """Terminal is the conditional-vs-unconditional comparison
        (the abnormal forward-move Series), not the conditional
        branch alone — per workflow_architecture.md's canonical
        archetype shape."""
        t = load_event_study_template()
        assert t.terminal_node_id == "compare"

    def test_template_locks_canonical_methodology(self):
        """Topology-locked params are NOT slot-substitutable.
        Pin the locks so a future template edit can't accidentally
        relax them without explicit review."""
        t = load_event_study_template()
        nodes = {n.node_id: n for n in t.nodes}
        # threshold_events (conditional): rule + basis + look_ahead_safe locked
        assert nodes["events"].params["rule"] == "abs_above"
        assert nodes["events"].params["threshold_basis"] == "raw_value"
        assert nodes["events"].params["look_ahead_safe"] is True
        # event_windows (conditional): pre_window=0, inclusive_event_day=True,
        # units_basis=level_change all locked.  The level_change lock is the
        # one that makes the cells "event-relative forward MOVES" rather
        # than "raw target levels" — Codex P1 fix.
        assert nodes["windows"].params["pre_window"] == 0
        assert nodes["windows"].params["inclusive_event_day"] is True
        assert nodes["windows"].params["units_basis"] == "level_change"
        # conditional_aggregate (conditional): aggregator=mean, dispersion=std locked
        assert nodes["aggregate"].params["aggregator"] == "mean"
        assert nodes["aggregate"].params["dispersion"] == "std"
        # Unconditional branch: same locks for parity (so the
        # subtraction in ``compare`` is unit-/methodology-coherent).
        assert nodes["unconditional_events"].params["rule"] == "above"
        assert nodes["unconditional_events"].params["threshold"] == -1.0e18
        assert nodes["unconditional_events"].params["threshold_basis"] == "raw_value"
        assert nodes["unconditional_events"].params["look_ahead_safe"] is True
        assert nodes["unconditional_windows"].params["pre_window"] == 0
        assert nodes["unconditional_windows"].params["inclusive_event_day"] is True
        assert nodes["unconditional_windows"].params["units_basis"] == "level_change"
        assert nodes["unconditional_aggregate"].params["aggregator"] == "mean"
        assert nodes["unconditional_aggregate"].params["dispersion"] == "std"
        # Comparison: subtract is the canonical abnormal-move op
        assert nodes["compare"].params["op"] == "subtract"

    def test_template_post_window_drives_both_branches(self):
        """The post_window slot must propagate into BOTH the
        conditional and unconditional event_windows nodes — otherwise
        the per-offset subtraction in ``compare`` would mix windows
        of different shapes and the abnormal series would be
        meaningless."""
        t = load_event_study_template()
        binding = {
            "signal_tool_name": "calculate_swap_spread_tool",
            "signal_params": {
                "sovereign_curve_family": "UST",
                "ois_curve_family": "USD_SOFR_OIS",
                "tenor": "2Y",
                "lookback_days": 1825,
            },
            "signal_output_field": "time_series_change_zscore",
            "target_tool_name": "get_yield_levels_tool",
            "target_params": {
                "curve_family": "UST", "tenor": "10Y", "lookback_days": 1825,
            },
            "target_output_field": "time_series",
            "threshold": 1.5,
            "post_window": 21,
        }
        wf = t.bind(binding)
        nodes = {n.node_id: n for n in wf.nodes}
        assert nodes["windows"].params["post_window"] == 21
        assert nodes["unconditional_windows"].params["post_window"] == 21


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
        """Canonical proof-Q1 slot binding (single-day widening event
        study: z-score of the day-over-day spread CHANGE).  The
        ``signal_series_key`` / ``target_series_key`` slots that
        previously appeared on this template were dropped in Codex
        P2 follow-up to PR #87 — the alignment branch's SeriesSet
        members are now renamed to template-controlled "signal" /
        "target" via ``align_series.output_keys``, so the template
        no longer leaks bridge-naming details into its slot
        surface."""
        defaults = {
            "signal_tool_name": "calculate_swap_spread_tool",
            "signal_params": {
                "sovereign_curve_family": "UST",
                "ois_curve_family": "USD_SOFR_OIS",
                "tenor": "2Y",
                "lookback_days": 1825,
            },
            "signal_output_field": "time_series_change_zscore",
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
        # Canonical proof-Q1 single-day-widening binding.  Uses
        # ``time_series_change_zscore`` (z-score of the day-over-day
        # spread CHANGE) — the signal that fires "spread widened a
        # lot today" events, exactly the Q1 question shape.
        return {
            "signal_tool_name": "calculate_swap_spread_tool",
            "signal_params": {
                "sovereign_curve_family": "UST",
                "ois_curve_family": "USD_SOFR_OIS",
                "tenor": "2Y",
                "lookback_days": 1825,
            },
            "signal_output_field": "time_series_change_zscore",
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

        # Terminal is the abnormal-move Series (compare = conditional
        # - unconditional).  Target was PERCENT yield levels;
        # event_windows.units_basis=level_change converted the
        # PERCENT levels to BPS event-relative moves, so both
        # branches' aggregates are BPS, and subtraction stays BPS.
        assert isinstance(result.terminal_artifact, Series)
        assert result.terminal_artifact.units == TimeSeriesUnits.BPS

    def test_terminal_offsets_match_post_window(self):
        """The terminal abnormal-move Series carries one value per
        event-relative offset (pre_window=0, post_window=5,
        inclusive_event_day=True → 6 offsets: 0..5)."""
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
        # Conditional + unconditional aggregates emit Series of length
        # window_length = post_window + 1 (since pre=0, inclusive=True);
        # subtraction preserves that.
        assert len(result.terminal_artifact.payload) == 6

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

        # Every node produced an artifact (both branches + alignment +
        # comparison).
        assert set(result.node_artifacts.keys()) == {
            "signal", "target",
            "align", "signal_aligned", "target_aligned",
            "events", "windows", "aggregate",
            "unconditional_events", "unconditional_windows",
            "unconditional_aggregate",
            "compare",
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
        for nid in (
            "signal", "target",
            "align", "signal_aligned", "target_aligned",
            "events", "windows", "aggregate",
            "unconditional_events", "unconditional_windows",
            "unconditional_aggregate",
            "compare",
        ):
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
                "units": "z_score",
            },
            "signal_output_field": "time_series",
            "target_tool_name": "synthetic_target_tool",
            "target_params": {
                "series_name": "synthetic_pct",
                "n_rows": 600,
                "base_value": 4.0,
                "units": "percent",
            },
            "target_output_field": "time_series",
            "threshold": 1.5,
            "post_window": 5,
        })

        result = execute_workflow(
            wf, engine=None, primitive_resolver=synthetic_resolver,
        )

        # Same shape as the real-rates run.  Terminal is the abnormal-
        # move Series in BPS (synthetic target declared "percent" units;
        # event_windows.units_basis=level_change converts PERCENT to BPS).
        assert isinstance(result.terminal_artifact, Series)
        assert result.terminal_artifact.units == TimeSeriesUnits.BPS
        # Lineage chain extends through every node in both branches +
        # the cross-calendar alignment branch (Codex P2 follow-up).
        assert set(result.node_artifacts.keys()) == {
            "signal", "target",
            "align", "signal_aligned", "target_aligned",
            "events", "windows", "aggregate",
            "unconditional_events", "unconditional_windows",
            "unconditional_aggregate",
            "compare",
        }


# ===========================================================================
# 5b. Cross-calendar genericity (Codex P3 follow-up to PR #87)
# ===========================================================================
#
# The TestInstrumentAgnostic suite proves the template runs unchanged
# against a finance-blind synthetic primitive — but signal AND target
# in that test share the same bdate_range (US business days), so the
# cross-calendar genericity claim is structurally present in the DAG
# but not actually exercised by the test.  Codex P3 follow-up to
# PR #87: add a dedicated test where signal and target use DIFFERENT
# trading-day grids (different holiday sets), prove the workflow
# succeeds end-to-end, and prove the alignment branch's inner-join
# computed the calendar intersection.
#
# Implementation: two new synthetic primitive callables emit Series
# whose indexes deliberately drop different holidays:
#
#   _us_calendar_signal_callable : standard bdate_range MINUS one
#                                  US-only holiday (Independence Day
#                                  2025-07-04).  Mimics a US-rooted
#                                  primitive's calendar.
#   _uk_calendar_target_callable : standard bdate_range MINUS one
#                                  UK-only holiday (UK Spring Bank
#                                  Holiday 2025-05-26).  Mimics a
#                                  UK-rooted primitive's calendar.
#
# The two indexes overlap on every business day EXCEPT those two
# holidays.  Inner-join of the two indexes drops both, so the aligned
# common index has exactly len(bdate_range) - 2 dates (when the
# bdate_range covers both 2025-05-26 AND 2025-07-04).


# Hard-coded "country-specific" holiday dates for the cross-calendar
# fixtures.  Locked in code so the fixtures' "calendar mismatch" is
# explicit, deterministic, and surfaces clearly in lineage.
_US_ONLY_HOLIDAY = pd.Timestamp("2025-07-04").date()
_UK_ONLY_HOLIDAY = pd.Timestamp("2025-05-26").date()


def _us_calendar_signal_callable(*, engine, params, config) -> dict:
    """Synthetic signal on a US calendar — bdate_range minus one
    US-only holiday (Independence Day).  UK Spring Bank Holiday
    (2025-05-26) is a normal business day on the US calendar so it
    DOES appear in this signal's index."""
    rs = np.random.RandomState(43)
    bdays_full = pd.bdate_range(
        _FROZEN_TODAY - timedelta(days=900), _FROZEN_TODAY,
    )[-params.n_rows:]
    # Drop the US-only holiday.
    us_calendar = bdays_full[
        ~(bdays_full.normalize().date == _US_ONLY_HOLIDAY)
    ]
    values = rs.randn(len(us_calendar)) * params.volatility + params.base_value
    rows = [
        TimeSeriesRow(date=d.strftime("%Y-%m-%d"), value=float(v))
        for d, v in zip(us_calendar, values)
    ]
    return {
        "current_metrics": {"as_of_date": rows[-1].date},
        "time_series": {
            "series_name": params.series_name,
            "units": params.units,
            "description": "Synthetic US-calendar signal series.",
            "rows": [r.model_dump() for r in rows],
        },
    }


def _uk_calendar_target_callable(*, engine, params, config) -> dict:
    """Synthetic target on a UK calendar — bdate_range minus one
    UK-only holiday (UK Spring Bank Holiday).  US Independence Day
    (2025-07-04) is a normal business day on the UK calendar so it
    DOES appear in this target's index."""
    rs = np.random.RandomState(57)
    bdays_full = pd.bdate_range(
        _FROZEN_TODAY - timedelta(days=900), _FROZEN_TODAY,
    )[-params.n_rows:]
    # Drop the UK-only holiday.
    uk_calendar = bdays_full[
        ~(bdays_full.normalize().date == _UK_ONLY_HOLIDAY)
    ]
    values = np.linspace(
        params.base_value, params.base_value + 1.0, len(uk_calendar),
    ) + rs.randn(len(uk_calendar)) * 0.02
    rows = [
        TimeSeriesRow(date=d.strftime("%Y-%m-%d"), value=float(v))
        for d, v in zip(uk_calendar, values)
    ]
    return {
        "current_metrics": {"as_of_date": rows[-1].date},
        "time_series": {
            "series_name": params.series_name,
            "units": params.units,
            "description": "Synthetic UK-calendar target series.",
            "rows": [r.model_dump() for r in rows],
        },
    }


@pytest.fixture
def cross_calendar_resolver(tmp_path) -> PrimitiveResolver:
    """Resolver pairing a US-calendar signal primitive with a
    UK-calendar target primitive.  The two indexes overlap on every
    business day EXCEPT 2025-05-26 (UK-only holiday) and 2025-07-04
    (US-only holiday).  Inner-join drops both.
    """
    cfg_signal = tmp_path / "us_calendar_signal.yaml"
    cfg_signal.write_text(_SYNTHETIC_CONFIG_YAML)
    cfg_target = tmp_path / "uk_calendar_target.yaml"
    cfg_target.write_text(_SYNTHETIC_CONFIG_YAML)

    signal_spec = PrimitiveSpec(
        tool_name="us_calendar_signal_tool",
        callable=_us_calendar_signal_callable,
        input_class=_SyntheticInput,
        output_class=_SyntheticOutput,
        config_path=cfg_signal,
        output_field_units={"time_series": "z_score"},
    )
    target_spec = PrimitiveSpec(
        tool_name="uk_calendar_target_tool",
        callable=_uk_calendar_target_callable,
        input_class=_SyntheticInput,
        output_class=_SyntheticOutput,
        config_path=cfg_target,
        output_field_units={"time_series": "percent"},
    )
    catalog = {
        "us_calendar_signal_tool": signal_spec,
        "uk_calendar_target_tool": target_spec,
    }

    def _resolve(tool_name: str) -> PrimitiveSpec:
        if tool_name not in catalog:
            raise KeyError(f"unknown synthetic tool: {tool_name!r}")
        return catalog[tool_name]

    return _resolve


class TestCrossCalendarGenericity:
    """End-to-end proof that the alignment branch ACTUALLY closes
    the cross-calendar genericity gap — not just structurally
    present in the DAG.  Codex P3 follow-up to PR #87."""

    _BINDING_TEMPLATE = {
        "signal_tool_name": "us_calendar_signal_tool",
        "signal_params": {
            "series_name": "us_signal",
            "n_rows": 600,
            "volatility": 1.0,
            "units": "z_score",
        },
        "signal_output_field": "time_series",
        "target_tool_name": "uk_calendar_target_tool",
        "target_params": {
            "series_name": "uk_target",
            "n_rows": 600,
            "base_value": 4.0,
            "units": "percent",
        },
        "target_output_field": "time_series",
        "threshold": 1.5,
        "post_window": 5,
    }

    def test_workflow_runs_end_to_end_on_mismatched_calendars(
        self, cross_calendar_resolver,
    ):
        """The whole workflow executes without an
        index-mismatch error.  Without the alignment branch this
        would fail at event_windows' index-equality check (the
        operator-level invariant in
        shared/operators/event_windows/operator.py)."""
        t = load_event_study_template()
        wf = t.bind(self._BINDING_TEMPLATE)

        result = execute_workflow(
            wf, engine=None,
            primitive_resolver=cross_calendar_resolver,
        )
        assert isinstance(result.terminal_artifact, Series)

    def test_alignment_drops_both_country_specific_holidays(
        self, cross_calendar_resolver,
    ):
        """The aligned signal + aligned target Series's indexes both
        EXCLUDE the UK-only AND the US-only holiday — proving the
        inner-join actually computed the calendar intersection
        (not just passed through one side's index)."""
        t = load_event_study_template()
        wf = t.bind(self._BINDING_TEMPLATE)

        result = execute_workflow(
            wf, engine=None,
            primitive_resolver=cross_calendar_resolver,
        )

        signal_aligned = result.node_artifacts["signal_aligned"]
        target_aligned = result.node_artifacts["target_aligned"]
        # Both aligned indexes are the SAME (alignment contract).
        assert signal_aligned.payload.index.equals(
            target_aligned.payload.index
        )
        # Neither holiday appears in the aligned index.
        aligned_dates = {
            d.date() for d in signal_aligned.payload.index
        }
        assert _US_ONLY_HOLIDAY not in aligned_dates, (
            f"alignment did not drop the US-only holiday "
            f"{_US_ONLY_HOLIDAY}; the inner-join is broken."
        )
        assert _UK_ONLY_HOLIDAY not in aligned_dates, (
            f"alignment did not drop the UK-only holiday "
            f"{_UK_ONLY_HOLIDAY}; the inner-join is broken."
        )

    def test_raw_signal_index_includes_uk_holiday_pre_alignment(
        self, cross_calendar_resolver,
    ):
        """Sanity: BEFORE alignment, the raw signal Series (US
        calendar) DOES include the UK-only holiday — proving the
        alignment branch actually had something to drop, not
        merely a no-op."""
        t = load_event_study_template()
        wf = t.bind(self._BINDING_TEMPLATE)

        result = execute_workflow(
            wf, engine=None,
            primitive_resolver=cross_calendar_resolver,
        )
        raw_signal = result.node_artifacts["signal"]
        raw_signal_dates = {d.date() for d in raw_signal.payload.index}
        # UK holiday is a normal business day on the US calendar →
        # appears in the raw signal.
        assert _UK_ONLY_HOLIDAY in raw_signal_dates
        # US holiday is dropped on the US calendar → does NOT appear.
        assert _US_ONLY_HOLIDAY not in raw_signal_dates

    def test_raw_target_index_includes_us_holiday_pre_alignment(
        self, cross_calendar_resolver,
    ):
        """Sanity: BEFORE alignment, the raw target Series (UK
        calendar) DOES include the US-only holiday — mirror of the
        above."""
        t = load_event_study_template()
        wf = t.bind(self._BINDING_TEMPLATE)

        result = execute_workflow(
            wf, engine=None,
            primitive_resolver=cross_calendar_resolver,
        )
        raw_target = result.node_artifacts["target"]
        raw_target_dates = {d.date() for d in raw_target.payload.index}
        # US holiday is a normal business day on the UK calendar.
        assert _US_ONLY_HOLIDAY in raw_target_dates
        # UK holiday is dropped on the UK calendar.
        assert _UK_ONLY_HOLIDAY not in raw_target_dates

    def test_align_step_lineage_records_template_output_keys(
        self, cross_calendar_resolver,
    ):
        """The align node ran with output_keys=["signal","target"];
        the operator step's params record the template-controlled
        rename so a downstream lineage walker sees both the
        original primitive series_keys AND the workflow-graph
        names (Codex P2 follow-up to PR #87)."""
        t = load_event_study_template()
        wf = t.bind(self._BINDING_TEMPLATE)

        result = execute_workflow(
            wf, engine=None,
            primitive_resolver=cross_calendar_resolver,
        )
        align_set = result.node_artifacts["align"]
        # The SeriesSet exposes the template-controlled names, NOT
        # the primitive series_names.
        assert sorted(align_set.series_by_key.keys()) == [
            "signal", "target",
        ]
        # The align step's params capture the rename map so a lineage
        # consumer can recover which input went to which output name.
        align_step = align_set.lineage.steps[-1]
        assert align_step.params["output_series_keys"] == [
            "signal", "target",
        ]
        assert align_step.params["input_to_output_key_map"] == {
            "us_signal": "signal",
            "uk_target": "target",
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
        # so the card records their concrete names.  After the Codex
        # P2 follow-up to PR #86, the canonical DAG also includes
        # ``align_series`` and ``select_from_series_set`` for the
        # cross-calendar alignment branch.
        assert set(card.operators_used) == {
            "align_series",
            "select_from_series_set",
            "threshold_events",
            "event_windows",
            "conditional_aggregate",
            "series_arithmetic",
        }

    def test_card_terminal_artifact_type_is_Series(self):
        t = load_event_study_template()
        card = card_for_template(t)
        # ``compare`` (series_arithmetic op=subtract) emits Series
        # per OPERATOR_REGISTRY.
        assert card.terminal_artifact_type == "Series"

    def test_card_node_and_edge_counts(self):
        t = load_event_study_template()
        card = card_for_template(t)
        # Codex P2 follow-up to PR #86 added 3 nodes (align +
        # signal_aligned + target_aligned) and 4 net new edges
        # (signal/target → align ×2, align → 2 selects ×2, then
        # 4 select-output edges replace the prior direct
        # signal/target → events/windows/uncond_events/uncond_windows
        # edges).
        assert card.node_count == 12
        assert card.edge_count == 14


# ===========================================================================
# 7. Resolver completeness (Codex P2 follow-up)
# ===========================================================================


class TestResolverCompleteness:
    """Every primitive surfaced in the template's slot-schema docstring
    examples must be backed by an entry in ``rates_primitive_resolver``.
    Otherwise a documented binding raises at validate-time even though
    the docs claim it's supported."""

    def test_swap_spread_registered(self):
        spec = rates_primitive_resolver("calculate_swap_spread_tool")
        assert spec.tool_name == "calculate_swap_spread_tool"

    def test_yield_levels_registered(self):
        spec = rates_primitive_resolver("get_yield_levels_tool")
        assert spec.tool_name == "get_yield_levels_tool"

    def test_curve_spread_registered(self):
        # Sovereign curve_spread (the template docstring lists it as a
        # candidate signal_tool_name).
        spec = rates_primitive_resolver("calculate_curve_spread_tool")
        assert spec.tool_name == "calculate_curve_spread_tool"

    def test_forward_rate_registered(self):
        # Codex P2: the template's target_output_field docstring example
        # named ``time_series_forward for a forward_rate``; the resolver
        # must back that promise.
        spec = rates_primitive_resolver("calculate_ois_forward_rate_tool")
        assert spec.tool_name == "calculate_ois_forward_rate_tool"
        # Forward-rate primitive emits time_series_forward (PERCENT) and
        # time_series_zscore (Z_SCORE) — both must declare units so the
        # substrate's validate-time unit-compat checks fire.
        assert spec.output_field_units["time_series_forward"] == "percent"
        assert spec.output_field_units["time_series_zscore"] == "z_score"

    def test_known_primitives_includes_all_seven(self):
        # Registry has grown across Phase 0 + Phase 1 PRs.  We assert
        # the MINIMUM canonical set is present rather than an exact
        # count, so adding a new primitive doesn't trip this test
        # (the test exists to catch ACCIDENTAL deregistration of an
        # existing primitive, not to gate growth).
        registered = set(known_rates_primitives())
        canonical_minimum = {
            # OIS family
            "calculate_ois_curve_spread_tool",
            "calculate_ois_cross_market_spread_tool",
            "get_ois_rate_level_tool",
            "calculate_swap_spread_tool",
            "calculate_ois_forward_rate_tool",
            # Sovereign family
            "calculate_curve_spread_tool",
            "calculate_cross_market_spread_tool",
            "get_yield_levels_tool",
        }
        missing = canonical_minimum - registered
        assert not missing, (
            f"Canonical primitives missing from registry: {sorted(missing)}.  "
            f"Registered today: {sorted(registered)}."
        )
        # Sanity: we never expect FEWER than the canonical minimum.
        assert len(registered) >= len(canonical_minimum)

    def test_swap_spread_change_zscore_registered(self):
        """The canonical Q1 binding lifts
        ``calculate_swap_spread_tool.time_series_change_zscore`` —
        the new "spread WIDENED today" signal added in this PR.  The
        resolver MUST declare its Z_SCORE units so the substrate's
        validate-time unit-compat checks fire on the canonical
        binding."""
        spec = rates_primitive_resolver("calculate_swap_spread_tool")
        assert (
            spec.output_field_units["time_series_change_zscore"]
            == "z_score"
        )


# ===========================================================================
# 8. Topology-archetype-fit gate (workflow_architecture.md anti-overfit gate)
# ===========================================================================
#
# Codex follow-up on PR #84: the prior "instrument-agnostic" test
# proved slots accept synthetic primitives, but it did NOT prove the
# DAG topology matches the abstract event_study archetype.  Without
# this gate, a regime-conditioned-relationship template could
# accidentally drift into event-study shape (or vice versa) and the
# slot-driven test would still pass.  This class pins the topology
# explicitly:
#
#   event_study REQUIRES: threshold_events × 2 (conditional +
#   unconditional), event_windows × 2, conditional_aggregate × 2,
#   series_arithmetic.subtract for the compare step.
#
#   event_study FORBIDS:  rolling_regression, apply_mask,
#   summarize_series — those are relationship-archetype operators
#   and their presence here would mean we drifted off the canonical
#   event-study shape.


class TestTopologyArchetypeFit:
    """Pin the canonical event_study DAG topology so accidental
    drift into a different archetype's shape surfaces in code
    review."""

    def test_uses_threshold_events_twice(self):
        """Both the conditional branch (rule=abs_above) and the
        unconditional baseline branch (sentinel rule=above) use
        threshold_events."""
        t = load_event_study_template()
        op_names = [
            n.operator_name for n in t.nodes if n.kind == "operator"
        ]
        assert op_names.count("threshold_events") == 2

    def test_uses_event_windows_twice(self):
        """One per branch — windowing the target around conditional
        event days vs unconditional baseline days."""
        t = load_event_study_template()
        op_names = [
            n.operator_name for n in t.nodes if n.kind == "operator"
        ]
        assert op_names.count("event_windows") == 2

    def test_uses_conditional_aggregate_twice(self):
        """One per branch — the per-offset mean + std dispersion."""
        t = load_event_study_template()
        op_names = [
            n.operator_name for n in t.nodes if n.kind == "operator"
        ]
        assert op_names.count("conditional_aggregate") == 2

    def test_compare_is_series_arithmetic_subtract(self):
        """The terminal compare step is canonical: subtract the
        unconditional aggregate from the conditional aggregate to
        emit the abnormal forward-move Series."""
        t = load_event_study_template()
        compare_node = next(n for n in t.nodes if n.node_id == "compare")
        assert compare_node.kind == "operator"
        assert compare_node.operator_name == "series_arithmetic"
        assert compare_node.params["op"] == "subtract"

    def test_uses_align_series_for_cross_calendar_genericity(self):
        """Codex P2 follow-up to PR #86: the canonical event_study
        template must include an alignment branch so the same
        template runs unchanged across instrument families with
        different trading-day calendars (e.g. UK gilts target with
        US-calendar OIS signal)."""
        t = load_event_study_template()
        op_names = [
            n.operator_name for n in t.nodes if n.kind == "operator"
        ]
        assert op_names.count("align_series") == 1
        # Two select_from_series_set nodes — one per aligned input.
        assert op_names.count("select_from_series_set") == 2

    def test_align_branch_wires_signal_and_target_then_aligned(self):
        """The alignment branch's edges must be exactly:
          - signal → align (series_list)
          - target → align (series_list)
          - align → signal_aligned (series_set)
          - align → target_aligned (series_set)
          - signal_aligned → events / unconditional_events
          - target_aligned → windows / unconditional_windows
        Catches an accidental rewire that would feed raw signal /
        target into the threshold/window branches."""
        t = load_event_study_template()
        edges_by_target = {}
        for e in t.edges:
            edges_by_target.setdefault(
                e.target_node_id, {},
            ).setdefault(e.target_input_slot, []).append(e.source_node_id)
        # align: list slot fed by signal AND target.
        assert sorted(edges_by_target["align"]["series_list"]) == [
            "signal", "target",
        ]
        # signal_aligned / target_aligned: each fed by align via
        # the series_set slot.
        assert (
            edges_by_target["signal_aligned"]["series_set"] == ["align"]
        )
        assert (
            edges_by_target["target_aligned"]["series_set"] == ["align"]
        )
        # events / unconditional_events: fed by signal_aligned (NOT raw signal).
        assert edges_by_target["events"]["series"] == ["signal_aligned"]
        assert (
            edges_by_target["unconditional_events"]["series"]
            == ["signal_aligned"]
        )
        # windows / unconditional_windows target slot: fed by target_aligned.
        assert edges_by_target["windows"]["target"] == ["target_aligned"]
        assert (
            edges_by_target["unconditional_windows"]["target"]
            == ["target_aligned"]
        )

    def test_does_not_use_relationship_archetype_operators(self):
        """event_study MUST NOT use rolling_regression / apply_mask /
        summarize_series — those are relationship-archetype operators
        (regime_conditioned_relationship's per-subsample analysis
        family).  Their presence here would mean the template
        drifted off the event-study canonical shape."""
        t = load_event_study_template()
        op_names = {
            n.operator_name for n in t.nodes if n.kind == "operator"
        }
        forbidden = {
            "rolling_regression", "apply_mask", "summarize_series",
        }
        leak = op_names & forbidden
        assert not leak, (
            f"event_study template drifted into relationship-archetype "
            f"operators: {sorted(leak)}.  These belong to "
            "regime_conditioned_relationship; remove them or, if a "
            "real desk question motivates them, ship a separately-"
            "named template per the V1 1-template-per-archetype rule."
        )


# ===========================================================================
# 9. Numeric / sign-convention pin on the abnormal-move series
# ===========================================================================
#
# Codex P3 follow-up on PR #82+#83: the prior real-rates suite
# checked the terminal artifact's type, units, length, validation,
# and lineage — but it never asserted the load-bearing arithmetic
# identity ``compare == aggregate − unconditional_aggregate``.  A
# future regression that flips the operand order (left=unconditional,
# right=conditional) or swaps the op (add vs subtract) would still
# pass every existing test.  This class pins the sign convention
# numerically against synthetic data with a known structural answer.


class TestAbnormalMoveSignConvention:
    """Pin ``compare = aggregate − unconditional_aggregate`` so a
    sign or operand-order regression surfaces in the test suite,
    not in production output."""

    def _synthetic_slot_values(self, **overrides):
        defaults = {
            "signal_tool_name": "calculate_swap_spread_tool",
            "signal_params": {
                "sovereign_curve_family": "UST",
                "ois_curve_family": "USD_SOFR_OIS",
                "tenor": "2Y",
                "lookback_days": 1825,
            },
            "signal_output_field": "time_series_change_zscore",
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

    def test_compare_equals_conditional_minus_unconditional(self):
        """Numerically pin: terminal_artifact[t] ==
        aggregate[t] - unconditional_aggregate[t] for every offset.
        If a future edit inverts the operands (right=aggregate,
        left=unconditional) the abnormal series would silently flip
        sign and every other test would still pass — this catches it."""
        import numpy as np

        t = load_event_study_template()
        wf = t.bind(self._synthetic_slot_values())
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

        terminal = result.terminal_artifact.payload
        cond = result.node_artifacts["aggregate"].payload
        uncond = result.node_artifacts["unconditional_aggregate"].payload
        # All three Series must share length = post_window + 1.
        assert len(terminal) == len(cond) == len(uncond) == 6
        # Numeric identity.
        for i in range(len(terminal)):
            expected = float(cond.iloc[i]) - float(uncond.iloc[i])
            actual = float(terminal.iloc[i])
            assert np.isclose(actual, expected, atol=1e-9), (
                f"abnormal series at offset {i}: expected "
                f"{expected:.6f} (=conditional-unconditional) but got "
                f"{actual:.6f}.  Sign convention regression detected."
            )

    def test_compare_node_left_is_aggregate_right_is_unconditional(self):
        """Static gate (no execution): the compare node's incoming
        edges MUST wire ``aggregate`` → left and
        ``unconditional_aggregate`` → right.  Catches an operand-
        order regression at template-edit time, before any test
        execution."""
        t = load_event_study_template()
        compare_edges = [
            e for e in t.edges if e.target_node_id == "compare"
        ]
        # Exactly two edges feed compare.
        assert len(compare_edges) == 2
        slot_to_source = {
            e.target_input_slot: e.source_node_id for e in compare_edges
        }
        assert slot_to_source["left"] == "aggregate"
        assert slot_to_source["right"] == "unconditional_aggregate"
