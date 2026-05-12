"""tests/test_pr19_primitives_unit.py — unit tests for the 3 new
primitives landed in Phase 1 PR 19.

Tests in scope:
  - sovereign_yield_panel: input validation, config loading, compute
    behaviour with mocked fetcher
  - financing_rate: per-method param validators, NotImplementedError
    paths, constant_rate compute (no DB), config loading
  - breakeven_inflation: curve-pair validation, config loading,
    convention enum guard

Live-DB tests for each tool land separately as ``_sql_validation``
tests in a follow-up PR (matching the established pattern from
curve_spread, swap_spread, etc.).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================================
# sovereign_yield_panel
# ============================================================================


class TestSovereignYieldPanelInput:
    """Pydantic input invariants."""

    def test_valid_sovereign_pair_accepts(self):
        from rates_agent.sovereign_bonds.tools.sovereign_yield_panel.schemas import (
            SovereignYieldPanelInput, SovereignYieldPanelLegSpec,
        )
        params = SovereignYieldPanelInput(
            legs=[
                SovereignYieldPanelLegSpec(curve_family="UST", tenor="2Y"),
                SovereignYieldPanelLegSpec(curve_family="USD_TIPS", tenor="10Y"),
            ],
            start_date=date(2024, 1, 1),
        )
        assert len(params.legs) == 2

    def test_ois_curve_family_rejected(self):
        from rates_agent.sovereign_bonds.tools.sovereign_yield_panel.schemas import (
            SovereignYieldPanelInput, SovereignYieldPanelLegSpec,
        )
        with pytest.raises(ValueError, match="not a recognised sovereign family"):
            SovereignYieldPanelInput(
                legs=[SovereignYieldPanelLegSpec(
                    curve_family="USD_SOFR_OIS", tenor="2Y",
                )],
                start_date=date(2024, 1, 1),
            )

    def test_duplicate_legs_rejected(self):
        from rates_agent.sovereign_bonds.tools.sovereign_yield_panel.schemas import (
            SovereignYieldPanelInput, SovereignYieldPanelLegSpec,
        )
        with pytest.raises(ValueError, match="Duplicate leg spec"):
            SovereignYieldPanelInput(
                legs=[
                    SovereignYieldPanelLegSpec(curve_family="UST", tenor="10Y"),
                    SovereignYieldPanelLegSpec(curve_family="UST", tenor="10Y"),
                ],
                start_date=date(2024, 1, 1),
            )

    def test_end_before_start_rejected(self):
        from rates_agent.sovereign_bonds.tools.sovereign_yield_panel.schemas import (
            SovereignYieldPanelInput, SovereignYieldPanelLegSpec,
        )
        with pytest.raises(ValueError, match="cannot be before start_date"):
            SovereignYieldPanelInput(
                legs=[SovereignYieldPanelLegSpec(curve_family="UST", tenor="10Y")],
                start_date=date(2024, 6, 1),
                end_date=date(2024, 1, 1),
            )


class TestSovereignYieldPanelConfig:
    def test_config_loads(self):
        from rates_agent.sovereign_bonds.tools.sovereign_yield_panel import CONFIG_PATH
        from shared.config import load_tool_config
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "build_sovereign_yield_panel_tool"
        # All required conventions present.
        for key in (
            "default_field_name",
            "ffill_limit_days",
            "default_missing_data_policy",
            "calendar_policy",
        ):
            assert key in cfg.conventions, (
                f"missing convention {key!r} in sovereign_yield_panel config"
            )


class TestSovereignYieldPanelCompute:
    """Compute behaviour with mocked fetcher."""

    def test_empty_fetch_returns_error_envelope(self):
        from rates_agent.sovereign_bonds.tools.sovereign_yield_panel import (
            CONFIG_PATH, build_sovereign_yield_panel,
        )
        from rates_agent.sovereign_bonds.tools.sovereign_yield_panel.schemas import (
            SovereignYieldPanelInput, SovereignYieldPanelLegSpec,
        )
        from shared.config import load_tool_config

        cfg = load_tool_config(CONFIG_PATH)
        params = SovereignYieldPanelInput(
            legs=[SovereignYieldPanelLegSpec(curve_family="UST", tenor="10Y")],
            start_date=date(2024, 1, 1),
        )
        with patch(
            "rates_agent.sovereign_bonds.tools.sovereign_yield_panel.compute."
            "fetch_instrument_panel",
            return_value=pd.DataFrame(),
        ):
            result = build_sovereign_yield_panel(
                engine=None, params=params, config=cfg,
            )
        assert "error" in result
        assert "No observations found" in result["error"]


# ============================================================================
# financing_rate
# ============================================================================


class TestFinancingRateInput:
    def test_constant_rate_requires_pct(self):
        from rates_agent.ois.tools.financing_rate.schemas import FinancingRateInput
        with pytest.raises(ValueError, match="constant_rate_pct"):
            FinancingRateInput(
                method="constant_rate",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 2, 1),
            )

    def test_overnight_index_proxy_requires_curve(self):
        from rates_agent.ois.tools.financing_rate.schemas import FinancingRateInput
        with pytest.raises(ValueError, match="proxy_curve"):
            FinancingRateInput(
                method="overnight_index_proxy",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 2, 1),
            )

    def test_overnight_index_proxy_rejects_unknown_curve(self):
        from rates_agent.ois.tools.financing_rate.schemas import FinancingRateInput
        with pytest.raises(ValueError, match="not a recognised OIS family"):
            FinancingRateInput(
                method="overnight_index_proxy",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 2, 1),
                proxy_curve="NOT_AN_OIS_CURVE",
            )

    def test_valid_constant_rate_parses(self):
        from rates_agent.ois.tools.financing_rate.schemas import FinancingRateInput
        params = FinancingRateInput(
            method="constant_rate",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 2, 1),
            constant_rate_pct=5.30,
        )
        assert params.method == "constant_rate"
        assert params.constant_rate_pct == 5.30

    def test_valid_overnight_index_proxy_parses(self):
        from rates_agent.ois.tools.financing_rate.schemas import FinancingRateInput
        params = FinancingRateInput(
            method="overnight_index_proxy",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 2, 1),
            proxy_curve="USD_SOFR_OIS",
        )
        assert params.proxy_curve == "USD_SOFR_OIS"


class TestFinancingRateConfig:
    def test_config_loads(self):
        from rates_agent.ois.tools.financing_rate import CONFIG_PATH
        from shared.config import load_tool_config
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "compute_financing_rate_tool"
        for key in (
            "default_method",
            "default_day_count_basis",
            "default_calendar",
        ):
            assert key in cfg.conventions


class TestFinancingRateCompute:
    """Compute behaviour for the data-free constant_rate method."""

    def test_constant_rate_builds_panel(self):
        from rates_agent.ois.tools.financing_rate import (
            CONFIG_PATH, compute_financing_rate,
        )
        from rates_agent.ois.tools.financing_rate.schemas import FinancingRateInput
        from shared.config import load_tool_config

        cfg = load_tool_config(CONFIG_PATH)
        params = FinancingRateInput(
            method="constant_rate",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 12),
            constant_rate_pct=5.30,
        )
        result = compute_financing_rate(engine=None, params=params, config=cfg)
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        assert result["method"] == "constant_rate"
        assert result["n_observations"] > 0
        # Mean of a constant should equal the constant.
        assert abs(result["mean_rate_pct"] - 5.30) < 1e-9
        # Typed Panel artifact is smuggled in under _panel.
        panel = result["_panel"]
        assert panel.payload.shape[1] == 1
        assert (panel.payload.iloc[:, 0] == 5.30).all()

    def test_term_repo_curve_raises(self):
        from rates_agent.ois.tools.financing_rate import (
            CONFIG_PATH, compute_financing_rate,
        )
        from rates_agent.ois.tools.financing_rate.schemas import FinancingRateInput
        from shared.config import load_tool_config

        cfg = load_tool_config(CONFIG_PATH)
        params = FinancingRateInput(
            method="term_repo_curve",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 2, 1),
        )
        result = compute_financing_rate(engine=None, params=params, config=cfg)
        assert "error" in result
        assert "term_repo_curve" in result["error"]

    def test_gc_special_blend_raises(self):
        from rates_agent.ois.tools.financing_rate import (
            CONFIG_PATH, compute_financing_rate,
        )
        from rates_agent.ois.tools.financing_rate.schemas import FinancingRateInput
        from shared.config import load_tool_config

        cfg = load_tool_config(CONFIG_PATH)
        params = FinancingRateInput(
            method="gc_special_blend",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 2, 1),
        )
        result = compute_financing_rate(engine=None, params=params, config=cfg)
        assert "error" in result
        assert "gc_special_blend" in result["error"]


# ============================================================================
# breakeven_inflation
# ============================================================================


class TestBreakevenInflationInput:
    def test_ust_tips_pair_accepts(self):
        from rates_agent.sovereign_bonds.tools.breakeven_inflation.schemas import (
            BreakevenInflationInput,
        )
        params = BreakevenInflationInput(
            nominal_curve_family="UST",
            real_curve_family="USD_TIPS",
            tenor="10Y",
        )
        assert params.nominal_curve_family == "UST"

    def test_cross_currency_pair_rejected(self):
        from rates_agent.sovereign_bonds.tools.breakeven_inflation.schemas import (
            BreakevenInflationInput,
        )
        with pytest.raises(ValueError, match="not a valid currency-matched"):
            BreakevenInflationInput(
                nominal_curve_family="UST",
                real_curve_family="UK_LINKER",
                tenor="10Y",
            )

    def test_unknown_nominal_family_rejected(self):
        from rates_agent.sovereign_bonds.tools.breakeven_inflation.schemas import (
            BreakevenInflationInput,
        )
        with pytest.raises(ValueError, match="not a recognised nominal family"):
            BreakevenInflationInput(
                nominal_curve_family="NOT_A_FAMILY",
                real_curve_family="USD_TIPS",
                tenor="10Y",
            )


class TestBreakevenInflationConfig:
    def test_config_loads(self):
        from rates_agent.sovereign_bonds.tools.breakeven_inflation import CONFIG_PATH
        from shared.config import load_tool_config
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "calculate_breakeven_inflation_tool"
        # All required conventions present.
        for key in (
            "z_score_window_days",
            "z_score_min_periods",
            "z_score_ddof",
            "ffill_limit_days",
            "breakeven_bps_round_decimals",
            "default_convention",
        ):
            assert key in cfg.conventions


class TestBreakevenInflationCompute:
    def test_inflation_swap_convention_raises(self):
        """V1 only implements nominal_breakeven; inflation_swap raises."""
        from rates_agent.sovereign_bonds.tools.breakeven_inflation import (
            CONFIG_PATH, calculate_breakeven_inflation,
        )
        from rates_agent.sovereign_bonds.tools.breakeven_inflation.schemas import (
            BreakevenInflationInput,
        )
        from shared.config import load_tool_config

        cfg = load_tool_config(CONFIG_PATH)
        params = BreakevenInflationInput(
            nominal_curve_family="UST",
            real_curve_family="USD_TIPS",
            tenor="10Y",
            convention="inflation_swap_breakeven",
        )
        result = calculate_breakeven_inflation(
            engine=None, params=params, config=cfg,
        )
        assert "error" in result
        assert "inflation_swap_breakeven" in result["error"]


# ============================================================================
# Cross-cutting: WorkflowArchetype closed-family invariant
# ============================================================================


def test_workflow_archetype_tuple_and_literal_in_sync():
    """The tuple and Literal must contain the same set."""
    from shared.workflow.template import WorkflowArchetype, WORKFLOW_ARCHETYPES

    assert set(WORKFLOW_ARCHETYPES) == set(WorkflowArchetype.__args__), (
        "WORKFLOW_ARCHETYPES tuple drifted from WorkflowArchetype Literal — "
        f"tuple={WORKFLOW_ARCHETYPES}, literal={WorkflowArchetype.__args__}."
    )
