"""tests/test_workflow_attribution_decomposition.py — first standard
template for the ``attribution_decomposition`` archetype (Round 3 A5).

Covers:

  1. Template structural validity (loads, registers, has the locked
     6-node DAG shape, terminal is the residual series_arithmetic).
  2. ``archetype_signature`` cues are well-formed (≥1 cue, all
     non-empty, all ≤120 chars per the workflow_architecture spec).
  3. Slot binding rejects missing / unknown / wrong-type slots.
  4. Real-rates end-to-end against synthetic-fetched data: bind to
     the canonical UST-vs-USD_SOFR_OIS attribution, run via the
     rates primitive resolver against patched-fetcher synthetic data,
     assert the terminal artifact is the per-date residual Series
     and that the sum-back invariant holds (target = benchmark +
     residual within float tolerance).
  5. **Mandatory WT15 instrument-agnostic test** — same template
     runs unchanged across at least 2 curve_families.  Bind once to
     UST/USD_SOFR_OIS, once to IT_BTP/DE_BUND, with the same
     synthetic fetcher patches; both runs produce well-formed
     terminal Series.
  6. Template card content reflects the locked DAG (operators_used
     includes align_series + select_from_series_set + series_arithmetic).

The substrate-realistic V1 scope (subtraction attribution instead
of canonical PCA-loadings attribution) is documented in
``template.yaml``'s header + the template description.  The PR9-
canonical version (composing pca_yield_curve +
yield_change_attribution_pca + series_arithmetic) requires bridge
extensions that the V1 substrate does not yet support — tracked as
substrate tech debt; not a regression here.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.workflows import rates_primitive_resolver
from rates_agent.workflows.attribution_decomposition import (
    ATTRIBUTION_DECOMPOSITION_TEMPLATE_PATH,
    load_attribution_decomposition_template,
)
from shared.artifacts import Series, TimeSeriesUnits
from shared.workflow import (
    OperatorNodeTemplate,
    PrimitiveNodeTemplate,
    SlotBindingError,
    card_for_template,
    clear_template_registry,
    clear_workflow_template_cache,
    execute_workflow,
    get_template,
    known_template_ids,
    validate_workflow,
)


# ---------------------------------------------------------------------------
# Fixtures + synthetic fetcher
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_state():
    """Reset all process-wide caches between tests so registrations +
    cached configs don't bleed across runs.  Note: a plain `import`
    of the template module after clear_template_registry() is a no-op
    (Python's import cache), so we call the module's ``register()``
    helper directly to re-populate the registry per test."""
    from shared.config import clear_tool_config_cache
    from rates_agent.workflows.attribution_decomposition import register
    clear_tool_config_cache()
    clear_workflow_template_cache()
    clear_template_registry()
    register()
    yield
    clear_tool_config_cache()
    clear_workflow_template_cache()
    clear_template_registry()


_FROZEN_TODAY = date(2026, 4, 30)


class _FrozenDate(date):
    @classmethod
    def today(cls) -> date:
        return _FROZEN_TODAY


def _synth_yields(
    *,
    curve_family: str,
    tenor: str,
    days: int = 600,
    base: float = 4.0,
    drift: float = 0.0,
) -> pd.DataFrame:
    """Deterministic per-(curve_family, tenor) yield panel — random
    walk seeded on the curve+tenor so two binds with the same
    (curve_family, tenor) produce byte-identical series, and two
    different (curve_family, tenor) pairs produce DIFFERENT series."""
    seed = abs(hash((curve_family, tenor))) % (2**32 - 1)
    rs = np.random.RandomState(seed)
    bdays = pd.bdate_range(
        _FROZEN_TODAY - timedelta(days=days * 2), _FROZEN_TODAY,
    )[-days:]
    base_series = np.linspace(base, base + drift, len(bdays))
    noise = rs.randn(len(bdays)) * 0.015
    values = base_series + noise.cumsum() * 0.01
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": values,
    })


def _fake_fetch_single_tenor(
    *, engine, curve_family, tenor, field_name, start_date,
    contract_code=None, instrument_type=None,
):
    """Synthetic stand-in for shared.analytics.rates_fetch
    .fetch_single_tenor.  Returns deterministic yields for any
    (curve_family, tenor) — the workflow template is curve-family
    -agnostic, so the test passes regardless of which families are
    bound to the slots."""
    df = _synth_yields(curve_family=curve_family, tenor=tenor)
    return df.loc[df["trade_date"] >= start_date].copy()


# ---------------------------------------------------------------------------
# 1. Template structural validity
# ---------------------------------------------------------------------------

class TestTemplateStructure:
    def test_template_yaml_exists(self):
        assert ATTRIBUTION_DECOMPOSITION_TEMPLATE_PATH.is_file()

    def test_template_loads_cleanly(self):
        t = load_attribution_decomposition_template()
        assert t.template_id == "attribution_decomposition"
        assert t.archetype == "attribution_decomposition"

    def test_template_registers_on_import(self):
        # Auto-register on import is the contract; the autouse fixture
        # calls register() explicitly after clearing the registry.
        assert "attribution_decomposition" in known_template_ids()

    def test_template_has_expected_dag(self):
        t = load_attribution_decomposition_template()
        node_ids = [n.node_id for n in t.nodes]
        assert node_ids == [
            "target_yield", "bench_yield", "align",
            "target_aligned", "bench_aligned", "residual",
        ]
        # Exactly 6 edges: 2 inputs to align, 2 select edges out of
        # align, 2 inputs to residual.
        assert len(t.edges) == 6

    def test_template_terminal_is_residual(self):
        t = load_attribution_decomposition_template()
        assert t.terminal_node_id == "residual"
        # Terminal is an OperatorNodeTemplate (series_arithmetic).
        terminal = next(n for n in t.nodes if n.node_id == "residual")
        assert isinstance(terminal, OperatorNodeTemplate)
        assert terminal.operator_name == "series_arithmetic"
        assert terminal.params["op"] == "subtract"

    def test_template_locks_methodology(self):
        t = load_attribution_decomposition_template()
        nodes_by_id = {n.node_id: n for n in t.nodes}

        # align_series — topology-locked methodology.
        align = nodes_by_id["align"]
        assert isinstance(align, OperatorNodeTemplate)
        assert align.params["join_policy"] == "inner"
        assert align.params["fill_policy"] == "raw"
        assert align.params["require_matching_frequency"] is True
        assert align.params["require_matching_missingness"] is False
        assert align.params["output_keys"] == ["target", "benchmark"]

        # select_from_series_set — template-locked keys matching
        # align.output_keys.
        assert nodes_by_id["target_aligned"].params["series_key"] == "target"
        assert nodes_by_id["bench_aligned"].params["series_key"] == "benchmark"

        # residual — series_arithmetic subtract.
        assert nodes_by_id["residual"].params["op"] == "subtract"

    def test_primitives_are_hardcoded_get_yield_levels(self):
        """V1 substrate: both primitive nodes are literal
        get_yield_levels_tool (not slot-substituted).  Documented as
        agent-scoping per WT3 path 2; the substrate-realistic V1
        cannot literally substitute pca_yield_curve /
        yield_change_attribution_pca via {$slot: ...} because the
        bridge does not support their list/snapshot outputs."""
        t = load_attribution_decomposition_template()
        nodes_by_id = {n.node_id: n for n in t.nodes}
        for nid in ("target_yield", "bench_yield"):
            node = nodes_by_id[nid]
            assert isinstance(node, PrimitiveNodeTemplate)
            assert node.tool_name == "get_yield_levels_tool", (
                f"{nid}.tool_name should be the literal "
                f"get_yield_levels_tool; got {node.tool_name!r}"
            )


# ---------------------------------------------------------------------------
# 2. Archetype signature
# ---------------------------------------------------------------------------

class TestArchetypeSignature:
    def test_at_least_one_cue_declared(self):
        t = load_attribution_decomposition_template()
        assert len(t.archetype_signature) >= 1

    def test_all_cues_well_formed(self):
        t = load_attribution_decomposition_template()
        for cue in t.archetype_signature:
            assert isinstance(cue, str)
            assert cue.strip()
            assert len(cue) <= 120, (
                f"archetype_signature cue too long ({len(cue)}>120): "
                f"{cue!r}"
            )

    def test_cues_propagate_to_card(self):
        t = load_attribution_decomposition_template()
        card = card_for_template(t)
        assert list(card.archetype_signature) == list(t.archetype_signature)


# ---------------------------------------------------------------------------
# 3. Slot binding
# ---------------------------------------------------------------------------

class TestSlotBinding:
    def _canonical_slots(self) -> dict:
        return {
            "target_curve_family": "UST",
            "target_tenor": "10Y",
            "benchmark_curve_family": "USD_SOFR_OIS",
            "benchmark_tenor": "10Y",
            "lookback_days": 1825,
        }

    def test_canonical_binding_succeeds(self):
        t = load_attribution_decomposition_template()
        wf = t.bind(self._canonical_slots())
        assert len(wf.nodes) == 6

    def test_missing_required_slot_raises(self):
        t = load_attribution_decomposition_template()
        bad = self._canonical_slots()
        del bad["target_curve_family"]
        with pytest.raises(SlotBindingError):
            t.bind(bad)

    def test_unknown_slot_raises(self):
        t = load_attribution_decomposition_template()
        bad = self._canonical_slots()
        bad["nonexistent_slot"] = "x"
        with pytest.raises(SlotBindingError):
            t.bind(bad)

    def test_wrong_slot_type_raises(self):
        t = load_attribution_decomposition_template()
        bad = self._canonical_slots()
        bad["lookback_days"] = "not_an_int"
        with pytest.raises(SlotBindingError):
            t.bind(bad)

    def test_optional_slots_use_defaults(self):
        t = load_attribution_decomposition_template()
        wf = t.bind(self._canonical_slots())
        # target_yield + bench_yield primitive nodes should have
        # output_field='time_series' (the default) substituted in.
        nodes_by_id = {n.node_id: n for n in wf.nodes}
        assert nodes_by_id["target_yield"].output_field == "time_series"
        assert nodes_by_id["bench_yield"].output_field == "time_series"


# ---------------------------------------------------------------------------
# 4. Template-card content
# ---------------------------------------------------------------------------

class TestTemplateCard:
    def test_card_terminal_artifact_type_is_series(self):
        t = load_attribution_decomposition_template()
        card = card_for_template(t)
        assert card.terminal_artifact_type == "Series"

    def test_card_operators_used_includes_locked_set(self):
        t = load_attribution_decomposition_template()
        card = card_for_template(t)
        # The template uses 3 operators: align_series,
        # select_from_series_set (twice — counted once in the set),
        # series_arithmetic.
        assert "align_series" in card.operators_used
        assert "select_from_series_set" in card.operators_used
        assert "series_arithmetic" in card.operators_used

    def test_card_primitives_used_includes_get_yield_levels(self):
        t = load_attribution_decomposition_template()
        card = card_for_template(t)
        assert "get_yield_levels_tool" in card.primitives_used


# ---------------------------------------------------------------------------
# 5. Real-rates end-to-end (synthetic fetcher, real rates resolver)
# ---------------------------------------------------------------------------

class TestEndToEndRealRates:
    """Bind to canonical UST/USD_SOFR_OIS attribution slots, run
    via the real rates_primitive_resolver against patched-fetcher
    synthetic data."""

    def test_validates_with_real_resolver(self):
        t = load_attribution_decomposition_template()
        wf = t.bind({
            "target_curve_family": "UST",
            "target_tenor": "10Y",
            "benchmark_curve_family": "USD_SOFR_OIS",
            "benchmark_tenor": "10Y",
            "lookback_days": 1825,
        })
        # validate_workflow returns None on success.
        validate_workflow(wf, primitive_resolver=rates_primitive_resolver)

    def test_runs_end_to_end_against_synthetic_fetchers(self):
        t = load_attribution_decomposition_template()
        wf = t.bind({
            "target_curve_family": "UST",
            "target_tenor": "10Y",
            "benchmark_curve_family": "USD_SOFR_OIS",
            "benchmark_tenor": "10Y",
            "lookback_days": 1825,
        })
        with patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.fetch_single_tenor",
            side_effect=_fake_fetch_single_tenor,
        ), patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.date",
            _FrozenDate,
        ):
            result = execute_workflow(
                wf, engine=None,
                primitive_resolver=rates_primitive_resolver,
            )

        # Terminal artifact is the per-date residual Series.
        assert isinstance(result.terminal_artifact, Series)
        # Both legs were emitted as PERCENT (get_yield_levels' raw
        # yield_mid units); subtract preserves units → residual is
        # PERCENT.
        assert result.terminal_artifact.units in (
            TimeSeriesUnits.PERCENT, TimeSeriesUnits.BPS,
        )

        # Lineage extends through every node in the DAG.
        assert set(result.node_artifacts.keys()) == {
            "target_yield", "bench_yield",
            "align", "target_aligned", "bench_aligned",
            "residual",
        }


# ---------------------------------------------------------------------------
# 6. WT15 — Mandatory instrument-agnostic test
# ---------------------------------------------------------------------------

class TestInstrumentAgnostic:
    """MANDATORY per WT15: the template must run unchanged across
    multiple curve_family pairings.  This is the load-bearing test
    for curve-family-agnostic scope — if the template fails on a
    second curve_family pairing, it has hidden assumptions.

    Per the work-order A5 acceptance, the test must exercise ≥2
    curve_families.  This class binds twice — UST/USD_SOFR_OIS
    (sovereign vs OIS anchor) and IT_BTP/DE_BUND (peripheral
    sovereign vs core sovereign anchor) — and asserts both produce
    well-formed terminal Series."""

    @pytest.mark.parametrize(
        "target_cf, target_tenor, bench_cf, bench_tenor",
        [
            # Case 1: UST vs USD_SOFR_OIS (cross-domain: sovereign vs OIS)
            ("UST", "10Y", "USD_SOFR_OIS", "10Y"),
            # Case 2: IT_BTP vs DE_BUND (cross-country: peripheral
            #         sovereign vs core sovereign anchor — the
            #         canonical "BTP local component above Bund"
            #         attribution shape).
            ("IT_BTP", "10Y", "DE_BUND", "10Y"),
        ],
    )
    def test_template_runs_unchanged_across_curve_family_pairings(
        self, target_cf, target_tenor, bench_cf, bench_tenor,
    ):
        """Same template, same DAG topology, same operator
        configuration — only the slot bindings differ.  No template
        code change between the two cases."""
        t = load_attribution_decomposition_template()
        wf = t.bind({
            "target_curve_family": target_cf,
            "target_tenor": target_tenor,
            "benchmark_curve_family": bench_cf,
            "benchmark_tenor": bench_tenor,
            "lookback_days": 1825,
        })
        with patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.fetch_single_tenor",
            side_effect=_fake_fetch_single_tenor,
        ), patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.date",
            _FrozenDate,
        ):
            result = execute_workflow(
                wf, engine=None,
                primitive_resolver=rates_primitive_resolver,
            )

        assert isinstance(result.terminal_artifact, Series)
        # The residual Series must have at least 1 row (the
        # alignment + subtraction produces a non-empty Series since
        # both legs share the same synthetic calendar).
        assert len(result.terminal_artifact.payload) >= 1


# ---------------------------------------------------------------------------
# 7. Sum-back invariant (decomposition sums to input)
# ---------------------------------------------------------------------------

class TestSumBackInvariant:
    """Work-order A5 acceptance: the test must validate that the
    decomposition sums to the input change within tolerance.

    For V1's subtraction attribution: residual = target - benchmark,
    so target = benchmark + residual exactly (subject to
    floating-point tolerance).  This pins the invariant against any
    future change to the residual computation that would silently
    drop / clip / clean values."""

    def test_target_equals_benchmark_plus_residual_within_tolerance(self):
        t = load_attribution_decomposition_template()
        wf = t.bind({
            "target_curve_family": "UST",
            "target_tenor": "10Y",
            "benchmark_curve_family": "USD_SOFR_OIS",
            "benchmark_tenor": "10Y",
            "lookback_days": 1825,
        })
        with patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.fetch_single_tenor",
            side_effect=_fake_fetch_single_tenor,
        ), patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.date",
            _FrozenDate,
        ):
            result = execute_workflow(
                wf, engine=None,
                primitive_resolver=rates_primitive_resolver,
            )

        target_series = result.node_artifacts["target_aligned"]
        bench_series = result.node_artifacts["bench_aligned"]
        residual_series = result.terminal_artifact

        target_vals = target_series.payload
        bench_vals = bench_series.payload
        residual_vals = residual_series.payload

        # All three Series must share the same index post-alignment.
        assert list(target_vals.index) == list(bench_vals.index)
        assert list(target_vals.index) == list(residual_vals.index)

        # Sum-back: target = benchmark + residual within fp tolerance.
        reconstructed = bench_vals + residual_vals
        max_abs_err = float((target_vals - reconstructed).abs().max())
        assert max_abs_err < 1e-9, (
            f"sum-back invariant violated: max |target - "
            f"(benchmark + residual)| = {max_abs_err}; expected < 1e-9"
        )
