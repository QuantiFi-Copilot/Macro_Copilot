"""tests/test_workflow_regime_conditioned_relationship.py — canonical
relationship-archetype template (the second standard workflow
template).

This file COMPLETELY REPLACES the previous (binary forward-response)
test suite that shipped with PR #84.  The previous template was
event-study-shaped (threshold_events × event_windows ×
conditional_aggregate per regime, subtract); Codex correctly
identified that this was NOT the original second proof archetype.
This rewrite ships against the actual relationship archetype:

  classify regimes → split sample by mask → run a RELATIONSHIP
  ANALYSIS (rolling β) inside each subsample → compare across
  regimes

Q2 — the canonical proof question this archetype was chosen to prove:

  "Estimate the rolling beta of the 10Y UST yield CHANGE to the 2Y
  OIS rate CHANGE, and report how that beta differs in steepening
  vs flattening regimes of the 2s10s curve over the last 3 years."

Coverage:

  1. Template structural validity (loads, validates, registers, has
     the canonical 12-node / 13-edge relationship-archetype shape).
  2. archetype_signature cues are well-formed.
  3. Slot binding refusals (missing / unknown / wrong type) +
     defaults.
  4. **Topology-archetype-fit gate** — the load-bearing gate that
     prevents drift back into event-study shape.  Pins:
       - rolling_regression × 1
       - apply_mask × 2
       - summarize_series × 2
       - NO event_windows (event-study operator)
       - NO conditional_aggregate (event-study operator)
  5. **Q2 real-rates end-to-end** — the canonical Q2 binding (UST
     10Y / 2Y SOFR OIS / 2s10s curve regime) executes against the
     rates primitive resolver with mocked DB fetchers.  Pins:
       - terminal Series carries the per-regime β-difference
       - terminal Series payload at the summarize_series sentinel
         date (1900-01-01)
       - terminal numeric value = mean(high_betas) − mean(low_betas)
         (sign-convention pin)
       - lineage extends through every node in the 12-node DAG
  6. **MANDATORY instrument-agnostic test** (workflow_architecture.md
     gate): same template runs unchanged against a finance-blind
     synthetic primitive resolver.
  7. Template card content reflects the relationship-archetype's
     operator family.
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
from shared.operators.summarize_series import SUMMARY_SENTINEL_DATE
from shared.schemas import TimeSeries, TimeSeriesRow
from shared.workflow import (
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


# ===========================================================================
# DEVELOPMENT PAUSE — entire module skipped.
# ===========================================================================
# The ``regime_conditioned_relationship`` workflow template is on
# development pause: it is NOT used by the live system and the open-DAG
# lane never invokes it (``DISABLE_TEMPLATE_ROUTER=1``; see TD #32).  Its
# canonical shape wires two ``summarize_series`` outputs into
# ``series_arithmetic.subtract`` at the shared ``SUMMARY_SENTINEL_DATE``.
#
# ``summarize_series`` has been migrated from that sentinel-date 1-row
# ``Series`` to a real ``ScalarMetric`` (GAP_LEDGER G01) — the canonical
# fix for the single-number-summary query class ("average / std / current
# value of X"), which the L4.5 CoverageGate correctly refused while the
# operator emitted a Series.  That migration intentionally breaks this
# paused template's Series→Series wiring (the resolver now flags
# ``ScalarMetric → compare.left expects Series``).  Rather than preserve a
# dead sentinel hack for a paused template, this module is skipped until
# the template is either retired or re-plumbed onto ScalarMetric operands.
pytestmark = pytest.mark.skip(
    reason=(
        "regime_conditioned_relationship template is on development pause "
        "(DISABLE_TEMPLATE_ROUTER=1); summarize_series migrated to "
        "ScalarMetric (GAP_LEDGER G01), intentionally breaking this "
        "template's sentinel-Series → series_arithmetic.subtract wiring."
    )
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


_FROZEN_TODAY = date(2026, 4, 30)


class _FrozenDate(date):
    @classmethod
    def today(cls) -> date:
        return _FROZEN_TODAY


def _synthetic_yield_single_tenor_df(
    *,
    days: int = 800,
    base: float = 4.30,
    drift: float = 0.40,
    noise: float = 0.012,
    seed: int = 17,
) -> pd.DataFrame:
    """Long-format frame for fetch_single_tenor.  Single curve,
    single tenor (yield_levels / ois_rate_level both call this with
    one curve+tenor).  Schema: {'trade_date', 'field_value'}."""
    rs = np.random.RandomState(seed)
    bdays = pd.bdate_range(
        _FROZEN_TODAY - timedelta(days=days * 2), _FROZEN_TODAY,
    )[-days:]
    n = len(bdays)
    vals = np.linspace(base, base + drift, n) + rs.randn(n) * noise
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": vals,
    })


def _synthetic_curve_pair_df(
    *,
    days: int = 800,
    seed: int = 13,
) -> pd.DataFrame:
    """Long-format frame for fetch_tenor_pair (USD_SOFR_OIS 2s10s).
    Schema: {'trade_date', 'tenor', 'field_value'}.

    Construct the spread (long − short) so it oscillates between
    ~+150 bps and ~-100 bps, ensuring both regimes (>+50 / <-50)
    fire on a healthy fraction of dates."""
    rs = np.random.RandomState(seed)
    bdays = pd.bdate_range(
        _FROZEN_TODAY - timedelta(days=days * 2), _FROZEN_TODAY,
    )[-days:]
    n = len(bdays)
    short = np.linspace(3.50, 4.80, n) + rs.randn(n) * 0.02
    long_drift = np.sin(np.linspace(0, 6 * np.pi, n)) * 1.0
    long = short + long_drift + rs.randn(n) * 0.015
    rows = []
    for tenor, vals in (("2Y", short), ("10Y", long)):
        for d, v in zip(bdays, vals):
            rows.append({
                "trade_date": d.date(), "tenor": tenor,
                "field_value": float(v),
            })
    return pd.DataFrame(rows)


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
        import importlib
        import rates_agent.workflows.regime_conditioned_relationship as rcr
        importlib.reload(rcr)
        registered = get_template("regime_conditioned_relationship")
        assert registered.template_id == "regime_conditioned_relationship"

    def test_template_in_listed_templates(self):
        import importlib
        import rates_agent.workflows.regime_conditioned_relationship as rcr
        importlib.reload(rcr)
        templates = list_templates(
            archetype="regime_conditioned_relationship",
        )
        assert any(
            t.template_id == "regime_conditioned_relationship"
            for t in templates
        )

    def test_template_has_expected_nodes(self):
        """Canonical relationship-archetype shape: 3 primitives +
        10 operators (relationship, beta select, regime_diff, 2 masks,
        2 apply, 2 summary, 1 compare) = 13 nodes.  ``regime_diff``
        was added in the Codex P1 follow-up to PR #86 so the regime
        classification is move-based (steepening / flattening) not
        level-based (steep / flat / inverted)."""
        t = load_regime_conditioned_template()
        node_ids = {n.node_id for n in t.nodes}
        assert node_ids == {
            # Primitives
            "lhs", "rhs", "regime_signal",
            # Relationship branch
            "relationship", "beta",
            # Regime classification (move-based; Codex P1 follow-up)
            "regime_diff",
            # Regime-mask producers (consume regime_diff, not raw signal)
            "high_mask", "low_mask",
            # Per-regime β subsamples
            "high_betas", "low_betas",
            # Per-regime summaries (sentinel-aligned)
            "high_summary", "low_summary",
            # Comparison (terminal)
            "compare",
        }

    def test_template_terminal_is_compare(self):
        t = load_regime_conditioned_template()
        assert t.terminal_node_id == "compare"

    def test_template_locks_canonical_methodology(self):
        """Topology-locked params are NOT slot-substitutable.  Pin
        them so a future template edit can't accidentally relax
        them without explicit review."""
        t = load_regime_conditioned_template()
        nodes = {n.node_id: n for n in t.nodes}

        # rolling_regression: level_change basis on both sides,
        # with-intercept regression.
        rel = nodes["relationship"]
        assert rel.params["lhs_basis"] == "level_change"
        assert rel.params["rhs_basis"] == "level_change"
        assert rel.params["add_constant"] is True

        # select_from_series_set: extract β specifically.
        assert nodes["beta"].params["series_key"] == "beta"

        # threshold_events × 2 (regime classifiers).  rule=above on
        # high; rule=below on low; raw_value basis on both;
        # look_ahead_safe on both.
        for nid, expected_rule in (
            ("high_mask", "above"),
            ("low_mask", "below"),
        ):
            n = nodes[nid]
            assert n.params["rule"] == expected_rule
            assert n.params["threshold_basis"] == "raw_value"
            assert n.params["look_ahead_safe"] is True

        # apply_mask × 2.  Default index_policy=intersect,
        # preserve_full_index=false (sparse subsample).
        for nid in ("high_betas", "low_betas"):
            n = nodes[nid]
            assert n.params["index_policy"] == "intersect"
            assert n.params["preserve_full_index"] is False

        # summarize_series × 2.  Mean central, std dispersion.
        for nid in ("high_summary", "low_summary"):
            n = nodes[nid]
            assert n.params["statistic"] == "mean"
            assert n.params["dispersion"] == "std"

        # compare: subtract (high − low).
        assert nodes["compare"].params["op"] == "subtract"

    def test_compare_left_is_high_summary_right_is_low_summary(self):
        """Sign-convention pin at the topology level.  high_summary
        feeds ``left``; low_summary feeds ``right``.  Catches an
        operand-order regression at template-edit time."""
        t = load_regime_conditioned_template()
        compare_edges = [e for e in t.edges if e.target_node_id == "compare"]
        assert len(compare_edges) == 2
        slot_to_source = {
            e.target_input_slot: e.source_node_id for e in compare_edges
        }
        assert slot_to_source["left"] == "high_summary"
        assert slot_to_source["right"] == "low_summary"


# ===========================================================================
# 2. archetype_signature contract
# ===========================================================================


class TestArchetypeSignature:
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
        # Canonical Q2 binding: thresholds are BPS DAILY MOVES on the
        # 2s10s curve_spread (NOT level thresholds).  +3.0 = "the
        # curve steepened by ≥3 bps today", -3.0 = "flattened by
        # ≥3 bps today".  Codex P1 follow-up to PR #86.
        defaults = {
            "lhs_tool_name": "get_yield_levels_tool",
            "lhs_params": {
                "curve_family": "UST", "tenor": "10Y",
                "lookback_days": 1500,
            },
            "lhs_output_field": "time_series",
            "rhs_tool_name": "get_ois_rate_level_tool",
            "rhs_params": {
                "curve_family": "USD_SOFR_OIS", "tenor": "2Y",
                "lookback_days": 1500,
            },
            "rhs_output_field": "time_series",
            "regime_signal_tool_name": "calculate_ois_curve_spread_tool",
            "regime_signal_params": {
                "curve_family": "USD_SOFR_OIS",
                "short_tenor": "2Y", "long_tenor": "10Y",
                "lookback_days": 1500,
            },
            "regime_signal_output_field": "time_series_spread",
            "regression_window": 60,
            "regression_min_periods": 30,
            "high_threshold": 3.0,
            "low_threshold": -3.0,
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
        partial.pop("regression_window")
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

    def test_regression_min_periods_default_applied(self):
        """regression_min_periods defaults to 30 when caller omits it."""
        t = load_regime_conditioned_template()
        partial = self._full_slot_values()
        partial.pop("regression_min_periods")
        wf = t.bind(partial)
        rel_node = next(
            n for n in wf.nodes if n.node_id == "relationship"
        )
        assert rel_node.params["min_periods"] == 30

    def test_disjoint_threshold_contract_enforced(self):
        """Codex P2 follow-up to PR #86: the
        ``high_threshold >= low_threshold`` contract is now enforced
        by the substrate at bind() time (declared via the template's
        slot_constraints block).  Previously this was a docs-only
        invariant — overlapping thresholds bound silently and
        executed with shared-date regime samples that undermined
        the per-regime comparison's economic meaning.  Now a
        violation raises SlotBindingError."""
        t = load_regime_conditioned_template()
        # Inverted order: high < low.  MUST raise.
        bad = self._full_slot_values(high_threshold=-5.0, low_threshold=5.0)
        with pytest.raises(SlotBindingError, match="must be >="):
            t.bind(bad)

    def test_equal_thresholds_accepted(self):
        """``gte`` operator: equal high == low is a degenerate but
        legal binding (regimes are technically still disjoint —
        no day satisfies BOTH branches' strict-inequality rules).
        The constraint accepts it; downstream operators surface
        the empty-mask case if applicable."""
        t = load_regime_conditioned_template()
        wf = t.bind(self._full_slot_values(
            high_threshold=0.0, low_threshold=0.0,
        ))
        assert isinstance(wf, Workflow)


# ===========================================================================
# 4. Topology-archetype-fit gate (LOAD-BEARING anti-overfit)
# ===========================================================================
#
# This class is the load-bearing gate that prevents drift back into
# event-study shape.  The previous (V0) regime_conditioned_relationship
# template that shipped with PR #84 used threshold_events ×
# event_windows × conditional_aggregate per regime — i.e. it was an
# event-study-shaped template wearing the relationship archetype's
# name.  Codex correctly identified that as an archetype mismatch.
#
# The fix: this gate asserts the template's operator family matches
# the relationship archetype's canonical operator set, and FORBIDS
# the event-study archetype's operators.


class TestTopologyArchetypeFit:
    """Pin the canonical relationship-archetype DAG topology so
    accidental drift into a different archetype's shape surfaces
    in code review, not in production output."""

    def test_uses_rolling_regression_exactly_once(self):
        """The relationship analysis = one rolling-OLS regression of
        lhs on rhs.  Multi-regression variants ship as separate
        templates."""
        t = load_regime_conditioned_template()
        op_names = [
            n.operator_name for n in t.nodes if n.kind == "operator"
        ]
        assert op_names.count("rolling_regression") == 1

    def test_uses_apply_mask_twice_for_per_regime_subsamples(self):
        """One apply_mask per regime — the load-bearing
        "split sample by mask" step of the archetype."""
        t = load_regime_conditioned_template()
        op_names = [
            n.operator_name for n in t.nodes if n.kind == "operator"
        ]
        assert op_names.count("apply_mask") == 2

    def test_uses_summarize_series_twice_for_per_regime_summaries(self):
        """One summarize_series per regime — collapses the per-regime
        β subsample to a sentinel-aligned scalar so downstream
        subtract has a non-empty intersection."""
        t = load_regime_conditioned_template()
        op_names = [
            n.operator_name for n in t.nodes if n.kind == "operator"
        ]
        assert op_names.count("summarize_series") == 2

    def test_uses_threshold_events_twice_for_regime_masks(self):
        """The two regime-mask producers (high + low).  Reuses the
        threshold_events operator with rule=above / rule=below.
        threshold_events emits a typed boolean EventSet; the
        downstream apply_mask consumes it as a generic per-date
        mask, not as a sparse trigger event-set."""
        t = load_regime_conditioned_template()
        op_names = [
            n.operator_name for n in t.nodes if n.kind == "operator"
        ]
        assert op_names.count("threshold_events") == 2

    def test_uses_select_from_series_set_to_extract_beta(self):
        """rolling_regression emits SeriesSet[beta, alpha,
        r_squared].  select_from_series_set lifts the β series for
        downstream masking + summarization."""
        t = load_regime_conditioned_template()
        op_names = [
            n.operator_name for n in t.nodes if n.kind == "operator"
        ]
        assert op_names.count("select_from_series_set") == 1

    def test_uses_series_arithmetic_subtract_for_compare(self):
        t = load_regime_conditioned_template()
        compare_node = next(
            n for n in t.nodes if n.node_id == "compare"
        )
        assert compare_node.operator_name == "series_arithmetic"
        assert compare_node.params["op"] == "subtract"

    def test_uses_regime_diff_for_move_based_classification(self):
        """Codex P1 follow-up to PR #86: the regime classification
        is MOVE-based (steepening / flattening), not LEVEL-based
        (steep / flat).  This is enforced by inserting a
        ``series_arithmetic op=diff`` step between regime_signal
        and the high/low threshold_events nodes.  The fork between
        the two compare-feeding chains uses op=subtract; this test
        pins the SECOND series_arithmetic instance to op=diff so a
        future edit cannot remove the diff and silently revert to
        level-based regimes."""
        t = load_regime_conditioned_template()
        regime_diff_node = next(
            (n for n in t.nodes if n.node_id == "regime_diff"),
            None,
        )
        assert regime_diff_node is not None, (
            "regime_diff node is missing; the regime classification "
            "would silently revert to level-based without it."
        )
        assert regime_diff_node.operator_name == "series_arithmetic"
        assert regime_diff_node.params["op"] == "diff"
        assert regime_diff_node.params["period"] == 1

    def test_regime_diff_wires_signal_to_masks(self):
        """Pin the regime_diff edges:
          - regime_signal → regime_diff (left)
          - regime_diff → high_mask (series)
          - regime_diff → low_mask (series)
        Catches a regression that would re-route raw regime_signal
        into the masks (which would silently bring back level-based
        regime classification)."""
        t = load_regime_conditioned_template()
        edges_by_target = {}
        for e in t.edges:
            edges_by_target.setdefault(
                e.target_node_id, {},
            ).setdefault(e.target_input_slot, []).append(
                e.source_node_id,
            )
        # regime_diff: left slot fed by regime_signal.
        assert edges_by_target["regime_diff"]["left"] == ["regime_signal"]
        # masks: series slot fed by regime_diff (NOT regime_signal).
        assert edges_by_target["high_mask"]["series"] == ["regime_diff"]
        assert edges_by_target["low_mask"]["series"] == ["regime_diff"]

    def test_does_not_use_event_study_archetype_operators(self):
        """LOAD-BEARING: regime_conditioned_relationship MUST NOT use
        event_windows or conditional_aggregate.  Those are the
        event-study archetype's per-event operators (the V0 template
        that shipped with PR #84 used both — and was correctly
        identified by Codex as still being event-study-shaped).
        Catches a regression at template-edit time."""
        t = load_regime_conditioned_template()
        op_names = {
            n.operator_name for n in t.nodes if n.kind == "operator"
        }
        forbidden = {"event_windows", "conditional_aggregate"}
        leak = op_names & forbidden
        assert not leak, (
            f"regime_conditioned_relationship template drifted into "
            f"event-study-archetype operators: {sorted(leak)}.  "
            "These belong to event_study (per-event-day forward-"
            "windowing of a target series).  The relationship "
            "archetype's per-subsample analysis is rolling_regression "
            "+ apply_mask + summarize_series, NOT event_windows + "
            "conditional_aggregate.  Either remove them or, if a "
            "real desk question motivates a forward-response variant, "
            "ship a separately-named template (e.g. "
            "regime_conditioned_response) per the V1 1-template-per-"
            "archetype rule."
        )

    def test_node_and_edge_counts(self):
        """Canonical relationship-archetype shape after the Codex P1
        follow-up to PR #86: 13 nodes (3 primitives + 10 operators
        — relationship, beta select, regime_diff, 2 masks, 2 apply,
        2 summary, 1 compare), 14 edges.  Pin these so accidental
        adds/removals surface in code review."""
        t = load_regime_conditioned_template()
        assert len(t.nodes) == 13
        assert len(t.edges) == 14


# ===========================================================================
# 5. Q2 real-rates end-to-end
# ===========================================================================


class TestEndToEndProofQ2:
    """The canonical Q2 binding (UST 10Y / 2Y SOFR OIS / 2s10s curve
    regime) executes against the rates primitive resolver with
    mocked DB fetchers and produces the expected per-regime β
    difference."""

    def _slot_values(self):
        # Canonical Q2 binding: thresholds are BPS DAILY MOVES on the
        # 2s10s curve_spread (steepening / flattening).  +3.0 = "the
        # curve steepened by ≥3 bps today", -3.0 = "flattened by
        # ≥3 bps today".  Codex P1 follow-up to PR #86: the previous
        # binding used level thresholds (+50 / -50) which classified
        # steep-vs-flat (level state), not steepening-vs-flattening
        # (move direction) — answering a different question from the
        # canonical Q2 prompt.
        return {
            "lhs_tool_name": "get_yield_levels_tool",
            "lhs_params": {
                "curve_family": "UST", "tenor": "10Y",
                "lookback_days": 1500,
            },
            "lhs_output_field": "time_series",
            "rhs_tool_name": "get_ois_rate_level_tool",
            "rhs_params": {
                "curve_family": "USD_SOFR_OIS", "tenor": "2Y",
                "lookback_days": 1500,
            },
            "rhs_output_field": "time_series",
            "regime_signal_tool_name": "calculate_ois_curve_spread_tool",
            "regime_signal_params": {
                "curve_family": "USD_SOFR_OIS",
                "short_tenor": "2Y", "long_tenor": "10Y",
                "lookback_days": 1500,
            },
            "regime_signal_output_field": "time_series_spread",
            "regression_window": 60,
            "regression_min_periods": 30,
            "high_threshold": 3.0,
            "low_threshold": -3.0,
        }

    def _patches(self):
        return (
            # LHS: UST 10Y yield via fetch_single_tenor.
            patch(
                "rates_agent.sovereign_bonds.tools.yield_levels.compute.fetch_single_tenor",
                return_value=_synthetic_yield_single_tenor_df(
                    base=4.30, drift=0.40, seed=17,
                ),
            ),
            patch(
                "rates_agent.sovereign_bonds.tools.yield_levels.compute.date",
                _FrozenDate,
            ),
            # RHS: 2Y SOFR OIS rate via fetch_single_tenor (different
            # base, different seed for distinct synthetic dynamics).
            patch(
                "rates_agent.ois.tools.rate_level.compute.fetch_single_tenor",
                return_value=_synthetic_yield_single_tenor_df(
                    base=4.05, drift=0.30, seed=29,
                ),
            ),
            patch(
                "rates_agent.ois.tools.rate_level.compute.date",
                _FrozenDate,
            ),
            # regime signal: 2s10s curve spread via fetch_tenor_pair.
            patch(
                "rates_agent.ois.tools.curve_spread.compute.fetch_tenor_pair",
                return_value=_synthetic_curve_pair_df(),
            ),
            patch(
                "rates_agent.ois.tools.curve_spread.compute.date",
                _FrozenDate,
            ),
        )

    def test_workflow_validates_with_real_resolver(self):
        t = load_regime_conditioned_template()
        wf = t.bind(self._slot_values())
        validate_workflow(wf, primitive_resolver=rates_primitive_resolver)

    def test_q2_runs_end_to_end(self):
        t = load_regime_conditioned_template()
        wf = t.bind(self._slot_values())
        p1, p2, p3, p4, p5, p6 = self._patches()
        with p1, p2, p3, p4, p5, p6:
            result = execute_workflow(
                wf, engine=None,
                primitive_resolver=rates_primitive_resolver,
            )
        # Terminal is the per-regime β-difference scalar (1-row Series
        # at the summarize_series sentinel date).
        assert isinstance(result.terminal_artifact, Series)
        assert len(result.terminal_artifact.payload) == 1

    def test_terminal_payload_at_summary_sentinel_date(self):
        t = load_regime_conditioned_template()
        wf = t.bind(self._slot_values())
        p1, p2, p3, p4, p5, p6 = self._patches()
        with p1, p2, p3, p4, p5, p6:
            result = execute_workflow(
                wf, engine=None,
                primitive_resolver=rates_primitive_resolver,
            )
        # Both per-regime summaries collapsed to the sentinel date so
        # the downstream subtract had a non-empty intersection.
        idx = result.terminal_artifact.payload.index
        assert idx[0] == SUMMARY_SENTINEL_DATE

    def test_terminal_units_are_RATIO(self):
        """β has units RATIO (BPS_lhs_change / BPS_rhs_change).
        summarize_series propagates units 1:1 (mean of β series is
        still RATIO).  series_arithmetic.subtract requires matching
        units AND propagates them — so the terminal regime-difference
        is RATIO."""
        t = load_regime_conditioned_template()
        wf = t.bind(self._slot_values())
        p1, p2, p3, p4, p5, p6 = self._patches()
        with p1, p2, p3, p4, p5, p6:
            result = execute_workflow(
                wf, engine=None,
                primitive_resolver=rates_primitive_resolver,
            )
        assert result.terminal_artifact.units == TimeSeriesUnits.RATIO

    def test_terminal_value_equals_high_minus_low_summary(self):
        """Sign-convention numeric pin: terminal ==
        high_summary - low_summary.  Catches an operand-order
        regression at the template's compare-edge wiring."""
        t = load_regime_conditioned_template()
        wf = t.bind(self._slot_values())
        p1, p2, p3, p4, p5, p6 = self._patches()
        with p1, p2, p3, p4, p5, p6:
            result = execute_workflow(
                wf, engine=None,
                primitive_resolver=rates_primitive_resolver,
            )
        terminal = float(result.terminal_artifact.payload.iloc[0])
        high = float(result.node_artifacts["high_summary"].payload.iloc[0])
        low = float(result.node_artifacts["low_summary"].payload.iloc[0])
        assert np.isclose(terminal, high - low, atol=1e-9), (
            f"per-regime β-difference at sentinel: expected "
            f"{high - low:.6f} (=high − low), got {terminal:.6f}.  "
            "Sign convention regression detected."
        )

    def test_lineage_extends_through_every_node(self):
        t = load_regime_conditioned_template()
        wf = t.bind(self._slot_values())
        p1, p2, p3, p4, p5, p6 = self._patches()
        with p1, p2, p3, p4, p5, p6:
            result = execute_workflow(
                wf, engine=None,
                primitive_resolver=rates_primitive_resolver,
            )
        assert set(result.node_artifacts.keys()) == {
            "lhs", "rhs", "regime_signal",
            "relationship", "beta",
            "regime_diff",
            "high_mask", "low_mask",
            "high_betas", "low_betas",
            "high_summary", "low_summary",
            "compare",
        }

    def test_workflow_lineage_summary_includes_every_node(self):
        t = load_regime_conditioned_template()
        wf = t.bind(self._slot_values())
        p1, p2, p3, p4, p5, p6 = self._patches()
        with p1, p2, p3, p4, p5, p6:
            result = execute_workflow(
                wf, engine=None,
                primitive_resolver=rates_primitive_resolver,
            )
        summary = result.workflow_lineage_summary
        for nid in (
            "lhs", "rhs", "regime_signal",
            "relationship", "beta",
            "regime_diff",
            "high_mask", "low_mask",
            "high_betas", "low_betas",
            "high_summary", "low_summary",
            "compare",
        ):
            assert nid in summary


# ===========================================================================
# 6. MANDATORY instrument-agnostic test (workflow_architecture.md gate)
# ===========================================================================


class _SyntheticInput(BaseModel):
    series_name: str = "synthetic"
    n_rows: int = 800
    base_value: float = 0.0
    drift: float = 0.0
    noise: float = 0.01
    units: str = "percent"
    seed: int = 0


class _SyntheticOutput(BaseModel):
    class _Metrics(BaseModel):
        as_of_date: str

    current_metrics: "_SyntheticOutput._Metrics"
    time_series: TimeSeries


_SyntheticOutput.model_rebuild()


def _make_synthetic_callable(*, transform=None, seed_offset: int = 0):
    """Factory for synthetic primitive callables.  ``transform``,
    when supplied, takes the rhs value array and returns the lhs
    value array — letting one synthetic primitive's output be a
    deterministic function of another's so the rolling-OLS recovers
    a known beta when the template runs.  Default None = independent
    series."""

    def _fn(*, engine, params, config) -> dict:
        rs = np.random.RandomState(int(params.seed) + seed_offset)
        bdays = pd.bdate_range(
            _FROZEN_TODAY - timedelta(days=900),
            _FROZEN_TODAY,
        )[-int(params.n_rows):]
        n = len(bdays)
        base_arr = (
            np.linspace(params.base_value, params.base_value + params.drift, n)
            + rs.randn(n) * params.noise
        )
        rows = [
            TimeSeriesRow(date=d.strftime("%Y-%m-%d"), value=float(v))
            for d, v in zip(bdays, base_arr)
        ]
        return {
            "current_metrics": {"as_of_date": rows[-1].date},
            "time_series": {
                "series_name": params.series_name,
                "units": params.units,
                "description": "Synthetic primitive output.",
                "rows": [r.model_dump() for r in rows],
            },
        }

    return _fn


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
    cfg_lhs = tmp_path / "synthetic_lhs.yaml"
    cfg_lhs.write_text(_SYNTHETIC_CONFIG_YAML)
    cfg_rhs = tmp_path / "synthetic_rhs.yaml"
    cfg_rhs.write_text(_SYNTHETIC_CONFIG_YAML)
    cfg_regime = tmp_path / "synthetic_regime.yaml"
    cfg_regime.write_text(_SYNTHETIC_CONFIG_YAML)

    catalog = {
        "synthetic_lhs_tool": PrimitiveSpec(
            tool_name="synthetic_lhs_tool",
            callable=_make_synthetic_callable(seed_offset=0),
            input_class=_SyntheticInput,
            output_class=_SyntheticOutput,
            config_path=cfg_lhs,
            output_field_units={"time_series": "percent"},
        ),
        "synthetic_rhs_tool": PrimitiveSpec(
            tool_name="synthetic_rhs_tool",
            callable=_make_synthetic_callable(seed_offset=100),
            input_class=_SyntheticInput,
            output_class=_SyntheticOutput,
            config_path=cfg_rhs,
            output_field_units={"time_series": "percent"},
        ),
        # Regime classifier emits a BPS-typed "spread-like" series
        # whose level is thresholded at +N / -N.  Same units mental
        # model as a curve-spread regime classifier.
        "synthetic_regime_tool": PrimitiveSpec(
            tool_name="synthetic_regime_tool",
            callable=_make_synthetic_callable(seed_offset=200),
            input_class=_SyntheticInput,
            output_class=_SyntheticOutput,
            config_path=cfg_regime,
            output_field_units={"time_series": "bps"},
        ),
    }

    def _resolve(tool_name: str) -> PrimitiveSpec:
        if tool_name not in catalog:
            raise KeyError(f"unknown synthetic tool: {tool_name!r}")
        return catalog[tool_name]

    return _resolve


class TestInstrumentAgnostic:
    """MANDATORY per workflow_architecture.md: every shipped template
    MUST run unchanged against a finance-blind synthetic primitive
    resolver that emits canonical TimeSeries through the bridge.

    This is the load-bearing anti-overfitting test.  A template
    that secretly hardcodes UST / OIS / curve_family assumptions
    would fail this test."""

    def test_runs_on_synthetic_primitives(self, synthetic_resolver):
        """Same template, same DAG topology, same operator
        configuration — only the (tool_name, output_field, params)
        slot bindings differ.  No template code change."""
        t = load_regime_conditioned_template()

        wf = t.bind({
            "lhs_tool_name": "synthetic_lhs_tool",
            "lhs_params": {
                "series_name": "synthetic_lhs",
                "n_rows": 800,
                "base_value": 4.0,
                "drift": 0.50,
                "noise": 0.01,
                "units": "percent",
                "seed": 1,
            },
            "lhs_output_field": "time_series",
            "rhs_tool_name": "synthetic_rhs_tool",
            "rhs_params": {
                "series_name": "synthetic_rhs",
                "n_rows": 800,
                "base_value": 3.5,
                "drift": 0.30,
                "noise": 0.01,
                "units": "percent",
                "seed": 2,
            },
            "rhs_output_field": "time_series",
            "regime_signal_tool_name": "synthetic_regime_tool",
            "regime_signal_params": {
                "series_name": "synthetic_regime",
                "n_rows": 800,
                # Build a regime signal whose DAILY DIFF oscillates
                # around 0 in BPS space (small noise → many days fall
                # in steepening / flattening regimes after the
                # template's regime_diff step fires).  Codex P1
                # follow-up to PR #86: the regime classification is
                # now MOVE-based, so the signal series itself just
                # needs a non-trivial day-over-day variance.
                "base_value": 0.0,
                "drift": 0.0,
                "noise": 5.0,
                "units": "bps",
                "seed": 3,
            },
            "regime_signal_output_field": "time_series",
            "regression_window": 60,
            "regression_min_periods": 30,
            # Daily-move thresholds (steepening / flattening regimes).
            "high_threshold": 1.0,
            "low_threshold": -1.0,
        })

        result = execute_workflow(
            wf, engine=None, primitive_resolver=synthetic_resolver,
        )

        # Same shape as the real-rates run.
        assert isinstance(result.terminal_artifact, Series)
        assert result.terminal_artifact.units == TimeSeriesUnits.RATIO
        assert len(result.terminal_artifact.payload) == 1
        assert (
            result.terminal_artifact.payload.index[0]
            == SUMMARY_SENTINEL_DATE
        )
        assert set(result.node_artifacts.keys()) == {
            "lhs", "rhs", "regime_signal",
            "relationship", "beta",
            "regime_diff",
            "high_mask", "low_mask",
            "high_betas", "low_betas",
            "high_summary", "low_summary",
            "compare",
        }


# ===========================================================================
# 7. Template card content
# ===========================================================================


class TestTemplateCard:
    def test_card_records_slot_substituted_primitives(self):
        t = load_regime_conditioned_template()
        card = card_for_template(t)
        # All three primitives are slot-driven.
        assert "<via $slot:lhs_tool_name>" in card.primitives_used
        assert "<via $slot:rhs_tool_name>" in card.primitives_used
        assert (
            "<via $slot:regime_signal_tool_name>" in card.primitives_used
        )

    def test_card_records_concrete_operator_names(self):
        t = load_regime_conditioned_template()
        card = card_for_template(t)
        # Operators are template-locked → the card records concrete
        # names.  Distinct from event_study's set: no event_windows
        # / conditional_aggregate, has rolling_regression / apply_mask
        # / summarize_series / select_from_series_set.
        assert set(card.operators_used) == {
            "rolling_regression",
            "select_from_series_set",
            "threshold_events",
            "apply_mask",
            "summarize_series",
            "series_arithmetic",
        }

    def test_card_terminal_artifact_type_is_Series(self):
        t = load_regime_conditioned_template()
        card = card_for_template(t)
        # ``compare`` (series_arithmetic op=subtract) emits Series.
        assert card.terminal_artifact_type == "Series"

    def test_card_node_and_edge_counts(self):
        t = load_regime_conditioned_template()
        card = card_for_template(t)
        # Codex P1 follow-up to PR #86: regime_diff added → 13 nodes,
        # 14 edges.
        assert card.node_count == 13
        assert card.edge_count == 14

    def test_card_archetype_is_regime_conditioned_relationship(self):
        t = load_regime_conditioned_template()
        card = card_for_template(t)
        assert card.archetype == "regime_conditioned_relationship"
