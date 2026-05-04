"""tests/test_workflow_regime_conditioned_relationship.py — second standard
workflow template.

Phase 2 PR 6.  Covers
``rates_agent/workflows/regime_conditioned_relationship/`` end-to-end:

  1. Template structural validity (loads, validates, registers, has
     the canonical 9-node high-vs-low binary-regime DAG shape).
  2. ``archetype_signature`` cues are well-formed (≥1 cue, all
     non-empty, all ≤120 chars per the workflow_architecture spec).
  3. Slot binding rejects malformed inputs cleanly + the post_window
     slot propagates into BOTH regime branches' windowing nodes.
  4. **Real-rates end-to-end** — bind to the reference desk binding
     (OIS 2s10s curve regime → UST 10Y yield response), run via the
     rates primitive resolver against synthetic raw data (mocked DB
     fetchers), assert the terminal artifact is the per-offset
     regime-difference Series in BPS (PERCENT target × level_change
     → BPS) and lineage extends through every node in both branches
     plus the comparison.
  5. **Mandatory instrument-agnostic test** — same template runs
     unchanged against a finance-blind synthetic primitive resolver.
     This is the load-bearing requirement from
     ``workflow_architecture.md``: "every template MUST be capable
     of running unchanged against a non-rates synthetic primitive
     that emits canonical TimeSeries payloads through the bridge."
  6. Template card content — primitives_used reflect $slot
     references; operators_used reflect template-locked operators
     (including the comparison operator).
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
from rates_agent.workflows.regime_conditioned_relationship import (
    REGIME_CONDITIONED_TEMPLATE_PATH,
    load_regime_conditioned_template,
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


def _synthetic_curve_pair_df(*, days: int = 600) -> pd.DataFrame:
    """Long-format frame for fetch_tenor_pair — two tenors on a
    single OIS curve.  USD_SOFR_OIS 2Y vs 10Y.  The spread
    (long − short) drifts across regimes so the high (>+50bps) and
    low (<-50bps) thresholds both fire enough days for the
    conditional aggregates to be well-defined."""
    bdays = pd.bdate_range(_FROZEN_TODAY - timedelta(days=days * 2), _FROZEN_TODAY)[-days:]
    rs = np.random.RandomState(13)
    rows = []
    n = len(bdays)
    # Build short + long so spread oscillates from ~+150 bps to ~-100 bps.
    short = np.linspace(3.50, 4.80, n) + rs.randn(n) * 0.02
    long_drift = np.sin(np.linspace(0, 6 * np.pi, n)) * 1.0
    long = short + long_drift + rs.randn(n) * 0.015
    for tenor, vals in (("2Y", short), ("10Y", long)):
        for d, v in zip(bdays, vals):
            rows.append({
                "trade_date": d.date(), "tenor": tenor, "field_value": float(v),
            })
    return pd.DataFrame(rows)


def _synthetic_yield_levels_df(*, days: int = 600) -> pd.DataFrame:
    """Single-tenor frame for fetch_single_tenor (yield_levels fetcher)."""
    bdays = pd.bdate_range(_FROZEN_TODAY - timedelta(days=days * 2), _FROZEN_TODAY)[-days:]
    rs = np.random.RandomState(17)
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
        assert REGIME_CONDITIONED_TEMPLATE_PATH.is_file()

    def test_template_loads_cleanly(self):
        t = load_regime_conditioned_template()
        assert t.template_id == "regime_conditioned_relationship"
        assert t.archetype == "regime_conditioned_relationship"

    def test_template_registers_on_import(self):
        # The regime_conditioned_relationship __init__.py auto-registers on
        # import.  Force a re-import to populate the registry after the
        # autouse fixture cleared it.
        import importlib

        import rates_agent.workflows.regime_conditioned_relationship as rcr_module
        importlib.reload(rcr_module)

        registered = get_template("regime_conditioned_relationship")
        assert registered.template_id == "regime_conditioned_relationship"

    def test_template_in_listed_templates(self):
        import importlib
        import rates_agent.workflows.regime_conditioned_relationship as rcr_module
        importlib.reload(rcr_module)
        templates = list_templates(archetype="regime_conditioned_relationship")
        assert any(
            t.template_id == "regime_conditioned_relationship"
            for t in templates
        )

    def test_template_has_expected_nodes(self):
        """The canonical regime_conditioned_relationship DAG shape per
        workflow_architecture.md (lines 75-77): two parallel regime
        branches (high + low) joined by a per-offset subtraction =
        regime-difference Series."""
        t = load_regime_conditioned_template()
        node_ids = {n.node_id for n in t.nodes}
        assert node_ids == {
            # Primitives
            "signal", "target",
            # High-regime branch
            "high_regime_events", "high_windows", "high_aggregate",
            # Low-regime branch
            "low_regime_events", "low_windows", "low_aggregate",
            # Comparison
            "compare",
        }

    def test_template_terminal_is_compare(self):
        """Terminal is the high-vs-low comparison (the regime-
        difference Series), per the canonical archetype shape."""
        t = load_regime_conditioned_template()
        assert t.terminal_node_id == "compare"

    def test_template_locks_canonical_methodology(self):
        """Topology-locked params are NOT slot-substitutable.  Pin
        the locks so a future template edit can't accidentally
        relax them without explicit review."""
        t = load_regime_conditioned_template()
        nodes = {n.node_id: n for n in t.nodes}
        # High-regime threshold_events: rule=above + basis=raw_value
        # + look_ahead_safe=true locked
        assert nodes["high_regime_events"].params["rule"] == "above"
        assert nodes["high_regime_events"].params["threshold_basis"] == "raw_value"
        assert nodes["high_regime_events"].params["look_ahead_safe"] is True
        # Low-regime threshold_events: rule=below mirrored
        assert nodes["low_regime_events"].params["rule"] == "below"
        assert nodes["low_regime_events"].params["threshold_basis"] == "raw_value"
        assert nodes["low_regime_events"].params["look_ahead_safe"] is True
        # Both event_windows: pre=0, inclusive_event_day=True,
        # units_basis=level_change all locked.  level_change is the
        # one that makes the cells "event-relative forward MOVES"
        # rather than "raw target levels".
        for nid in ("high_windows", "low_windows"):
            assert nodes[nid].params["pre_window"] == 0
            assert nodes[nid].params["inclusive_event_day"] is True
            assert nodes[nid].params["units_basis"] == "level_change"
        # Both conditional_aggregate: aggregator=mean, dispersion=std locked
        for nid in ("high_aggregate", "low_aggregate"):
            assert nodes[nid].params["aggregator"] == "mean"
            assert nodes[nid].params["dispersion"] == "std"
        # Comparison: subtract is the canonical regime-difference op
        assert nodes["compare"].params["op"] == "subtract"


# ===========================================================================
# 2. archetype_signature contract
# ===========================================================================


class TestArchetypeSignature:
    """Per workflow_architecture.md, every shipped template MUST
    declare ≥1 archetype_signature cue so the future
    route_to_template LLM step can match prompts."""

    def test_at_least_one_cue_declared(self):
        t = load_regime_conditioned_template()
        assert len(t.archetype_signature) >= 1

    def test_all_cues_well_formed(self):
        t = load_regime_conditioned_template()
        for cue in t.archetype_signature:
            assert isinstance(cue, str)
            assert cue.strip()
            assert len(cue) <= 120

    def test_cues_propagate_to_card(self):
        t = load_regime_conditioned_template()
        card = card_for_template(t)
        assert card.archetype_signature == t.archetype_signature


# ===========================================================================
# 3. Slot binding refusals
# ===========================================================================


class TestSlotBinding:
    def _full_slot_values(self, **overrides):
        """Reference desk binding: OIS 2s10s curve regime → UST 10Y yield."""
        defaults = {
            "signal_tool_name": "calculate_ois_curve_spread_tool",
            "signal_params": {
                "curve_family": "USD_SOFR_OIS",
                "short_tenor": "2Y",
                "long_tenor": "10Y",
                "lookback_days": 1825,
            },
            "signal_output_field": "time_series_spread",
            "target_tool_name": "get_yield_levels_tool",
            "target_params": {
                "curve_family": "UST",
                "tenor": "10Y",
                "lookback_days": 1825,
            },
            "target_output_field": "time_series",
            "high_threshold": 50.0,
            "low_threshold": -50.0,
            "post_window": 5,
        }
        defaults.update(overrides)
        return defaults

    def test_full_canonical_binding_succeeds(self):
        t = load_regime_conditioned_template()
        wf = t.bind(self._full_slot_values())
        assert isinstance(wf, Workflow)

    def test_missing_required_slot_raises(self):
        t = load_regime_conditioned_template()
        partial = self._full_slot_values()
        partial.pop("high_threshold")
        with pytest.raises(SlotBindingError, match="required slot"):
            t.bind(partial)

    def test_unknown_slot_raises(self):
        t = load_regime_conditioned_template()
        bad = self._full_slot_values()
        bad["ghost_slot"] = "x"
        with pytest.raises(SlotBindingError, match="unknown slot"):
            t.bind(bad)

    def test_wrong_slot_type_raises(self):
        t = load_regime_conditioned_template()
        bad = self._full_slot_values()
        bad["high_threshold"] = "not_a_float"
        with pytest.raises(SlotBindingError, match="declared type"):
            t.bind(bad)

    def test_post_window_default_applied(self):
        """post_window defaults to 5 when caller omits it."""
        t = load_regime_conditioned_template()
        partial = self._full_slot_values()
        partial.pop("post_window")
        wf = t.bind(partial)
        nodes = {n.node_id: n for n in wf.nodes}
        assert nodes["high_windows"].params["post_window"] == 5
        assert nodes["low_windows"].params["post_window"] == 5

    def test_post_window_drives_both_branches(self):
        """The post_window slot must propagate into BOTH the high
        and low event_windows nodes — otherwise the per-offset
        subtraction in ``compare`` would mix windows of different
        shapes and the regime-difference series would be meaningless."""
        t = load_regime_conditioned_template()
        wf = t.bind(self._full_slot_values(post_window=21))
        nodes = {n.node_id: n for n in wf.nodes}
        assert nodes["high_windows"].params["post_window"] == 21
        assert nodes["low_windows"].params["post_window"] == 21

    def test_high_and_low_thresholds_are_independent(self):
        """The two thresholds bind to their respective regime branches
        independently — the template does not constrain the gap or
        ordering between them (caller judgment call)."""
        t = load_regime_conditioned_template()
        wf = t.bind(self._full_slot_values(
            high_threshold=25.0, low_threshold=-100.0,
        ))
        nodes = {n.node_id: n for n in wf.nodes}
        assert nodes["high_regime_events"].params["threshold"] == 25.0
        assert nodes["low_regime_events"].params["threshold"] == -100.0


# ===========================================================================
# 4. Real-rates end-to-end (curve-regime reference binding)
# ===========================================================================


class TestEndToEndRealRates:
    """Bind to the reference desk binding (OIS 2s10s curve regime →
    UST 10Y yield response) and execute against the rates primitive
    resolver with mocked DB fetchers."""

    def _slot_values(self):
        return {
            "signal_tool_name": "calculate_ois_curve_spread_tool",
            "signal_params": {
                "curve_family": "USD_SOFR_OIS",
                "short_tenor": "2Y",
                "long_tenor": "10Y",
                "lookback_days": 1825,
            },
            "signal_output_field": "time_series_spread",
            "target_tool_name": "get_yield_levels_tool",
            "target_params": {
                "curve_family": "UST",
                "tenor": "10Y",
                "lookback_days": 1825,
            },
            "target_output_field": "time_series",
            "high_threshold": 50.0,
            "low_threshold": -50.0,
            "post_window": 5,
        }

    def _patches(self):
        return (
            patch(
                "rates_agent.ois.tools.curve_spread.compute.fetch_tenor_pair",
                return_value=_synthetic_curve_pair_df(),
            ),
            patch(
                "rates_agent.ois.tools.curve_spread.compute.date",
                _FrozenDate,
            ),
            patch(
                "rates_agent.sovereign_bonds.tools.yield_levels.compute.fetch_single_tenor",
                return_value=_synthetic_yield_levels_df(),
            ),
            patch(
                "rates_agent.sovereign_bonds.tools.yield_levels.compute.date",
                _FrozenDate,
            ),
        )

    def test_runs_end_to_end(self):
        t = load_regime_conditioned_template()
        wf = t.bind(self._slot_values())

        p1, p2, p3, p4 = self._patches()
        with p1, p2, p3, p4:
            result = execute_workflow(
                wf, engine=None,
                primitive_resolver=rates_primitive_resolver,
            )

        # Terminal is the regime-difference Series.  Target was PERCENT
        # yield levels; event_windows.units_basis=level_change
        # converted PERCENT to BPS event-relative moves in both
        # branches; subtraction stays BPS.
        assert isinstance(result.terminal_artifact, Series)
        assert result.terminal_artifact.units == TimeSeriesUnits.BPS

    def test_workflow_validates_with_real_resolver(self):
        """Pre-flight validation passes against the real rates
        resolver — every primitive tool_name resolves cleanly + every
        operator branch unit-checks coherently."""
        t = load_regime_conditioned_template()
        wf = t.bind(self._slot_values())
        # Should not raise.
        validate_workflow(wf, primitive_resolver=rates_primitive_resolver)

    def test_terminal_offsets_match_post_window(self):
        """The terminal regime-difference Series carries one value
        per event-relative offset (pre_window=0, post_window=5,
        inclusive_event_day=True → 6 offsets: 0..5)."""
        t = load_regime_conditioned_template()
        wf = t.bind(self._slot_values())
        p1, p2, p3, p4 = self._patches()
        with p1, p2, p3, p4:
            result = execute_workflow(
                wf, engine=None,
                primitive_resolver=rates_primitive_resolver,
            )
        # Both branches' aggregates emit Series of length
        # window_length = post_window + 1 (since pre=0, inclusive=True);
        # subtraction preserves that.
        assert len(result.terminal_artifact.payload) == 6

    def test_lineage_extends_through_every_node(self):
        t = load_regime_conditioned_template()
        wf = t.bind(self._slot_values())

        p1, p2, p3, p4 = self._patches()
        with p1, p2, p3, p4:
            result = execute_workflow(
                wf, engine=None,
                primitive_resolver=rates_primitive_resolver,
            )

        # Every node produced an artifact (both regime branches + comparison).
        assert set(result.node_artifacts.keys()) == {
            "signal", "target",
            "high_regime_events", "high_windows", "high_aggregate",
            "low_regime_events", "low_windows", "low_aggregate",
            "compare",
        }

    def test_workflow_lineage_summary_includes_every_node(self):
        t = load_regime_conditioned_template()
        wf = t.bind(self._slot_values())
        p1, p2, p3, p4 = self._patches()
        with p1, p2, p3, p4:
            result = execute_workflow(
                wf, engine=None,
                primitive_resolver=rates_primitive_resolver,
            )
        summary = result.workflow_lineage_summary
        for nid in (
            "signal", "target",
            "high_regime_events", "high_windows", "high_aggregate",
            "low_regime_events", "low_windows", "low_aggregate",
            "compare",
        ):
            assert nid in summary


# ===========================================================================
# 5. MANDATORY instrument-agnostic test (workflow_architecture.md gate)
# ===========================================================================


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
    """Synthetic regime-classifier signal: a simulated random walk
    centered around base_value with given volatility.  Calibrated so
    high_threshold=+1.0 and low_threshold=-1.0 each catch a healthy
    fraction of dates → both branches' aggregates are well-defined."""
    rs = np.random.RandomState(43)
    bdays = pd.bdate_range(
        _FROZEN_TODAY - timedelta(days=900), _FROZEN_TODAY,
    )[-params.n_rows:]
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
            "description": "Synthetic regime-classifier signal.",
            "rows": [r.model_dump() for r in rows],
        },
    }


def _synthetic_target_callable(*, engine, params, config) -> dict:
    """Synthetic target series: linear walk in PERCENT-like units."""
    rs = np.random.RandomState(101)
    bdays = pd.bdate_range(
        _FROZEN_TODAY - timedelta(days=900), _FROZEN_TODAY,
    )[-params.n_rows:]
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
    let the regime_conditioned_relationship template run against
    finance-blind data."""
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
        # Synthetic signal carries z_score units to mimic a normalized
        # regime classifier; the high/low thresholds in the binding are
        # calibrated against this scale.
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
    """MANDATORY per workflow_architecture.md: every template must
    run unchanged against a non-rates synthetic primitive that emits
    canonical TimeSeries through the bridge.

    This is the load-bearing test for instrument-agnosticism — if
    regime_conditioned_relationship can't run against synthetic
    primitives, it has hidden rates-specific assumptions and is
    overfit."""

    def test_runs_on_synthetic_primitives(self, synthetic_resolver):
        """Same template, same DAG topology, same operator
        configuration — only the (tool_name, output_field, params)
        slot bindings differ.  No template code change."""
        t = load_regime_conditioned_template()

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
            # |z|=1.0 catches a healthy fraction of dates on both sides.
            "high_threshold": 1.0,
            "low_threshold": -1.0,
            "post_window": 5,
        })

        result = execute_workflow(
            wf, engine=None, primitive_resolver=synthetic_resolver,
        )

        # Same shape as the real-rates run.  Terminal is the regime-
        # difference Series in BPS (synthetic target declared "percent"
        # units; event_windows.units_basis=level_change converts
        # PERCENT to BPS).
        assert isinstance(result.terminal_artifact, Series)
        assert result.terminal_artifact.units == TimeSeriesUnits.BPS
        # Lineage chain extends through every node in both branches.
        assert set(result.node_artifacts.keys()) == {
            "signal", "target",
            "high_regime_events", "high_windows", "high_aggregate",
            "low_regime_events", "low_windows", "low_aggregate",
            "compare",
        }


# ===========================================================================
# 6. Template card content
# ===========================================================================


class TestTemplateCard:
    def test_card_records_slot_substituted_primitives(self):
        t = load_regime_conditioned_template()
        card = card_for_template(t)
        # Both primitives are referenced via $slot — card records
        # the slot-substituted form, not concrete tool names.
        assert "<via $slot:signal_tool_name>" in card.primitives_used
        assert "<via $slot:target_tool_name>" in card.primitives_used

    def test_card_records_concrete_operator_names(self):
        t = load_regime_conditioned_template()
        card = card_for_template(t)
        # Operators are template-locked (NOT slot-substitutable),
        # so the card records their concrete names.  The DAG uses
        # threshold_events (twice — high + low), event_windows
        # (twice), conditional_aggregate (twice), and
        # series_arithmetic (compare); the card de-duplicates.
        assert set(card.operators_used) == {
            "threshold_events", "event_windows",
            "conditional_aggregate", "series_arithmetic",
        }

    def test_card_terminal_artifact_type_is_Series(self):
        t = load_regime_conditioned_template()
        card = card_for_template(t)
        # ``compare`` (series_arithmetic op=subtract) emits Series
        # per OPERATOR_REGISTRY.
        assert card.terminal_artifact_type == "Series"

    def test_card_node_and_edge_counts(self):
        t = load_regime_conditioned_template()
        card = card_for_template(t)
        # Canonical binary-regime shape: 9 nodes (2 primitives + 3
        # high-branch + 3 low-branch + 1 compare), 10 edges.
        assert card.node_count == 9
        assert card.edge_count == 10

    def test_card_archetype_is_regime_conditioned_relationship(self):
        t = load_regime_conditioned_template()
        card = card_for_template(t)
        assert card.archetype == "regime_conditioned_relationship"
