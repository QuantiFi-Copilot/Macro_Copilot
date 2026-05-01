"""
test_cross_market_spread_wiring.py — Caller-wiring smoke tests

Three external callers:

  1. rates_agent/sovereign_bonds/mcp_server.py::calculate_cross_market_spread_tool
  2. api/routes/rates/detail.py::cross_market_detail (/detail/cross-market)
  3. api/routes/rates/cards.py::cross_market (/cross-market) — a thin
     per-pair caller that loops settings.CROSS_MARKET_PAIRS.  Migrated
     to load config ONCE per request before the loop, with a defensive
     503 if the YAML load fails (so a config error doesn't masquerade
     as "All N cross-market queries failed" via the existing
     failures-counter path).

Key load-bearing properties under test:

  - Each surface passes config explicitly (no auto-load fallback).
  - Each surface uses the empty-string / None-sentinel pattern so the
    YAML default_field_name actually flows through.
  - The /cross-market batch endpoint loads config exactly ONCE per
    request, regardless of how many pairs it iterates.
  - When config load fails, /cross-market returns a clean 503 BEFORE
    attempting any per-pair work (Codex P3 from the cross_market plan
    review).
  - The hub re-export and CONFIG_PATH public symbol are stable.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.sovereign_bonds.tools.cross_market_spread import (
    CONFIG_PATH as CROSS_MARKET_CONFIG_PATH,
)
from shared.config import ToolConfig, clear_tool_config_cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_cross_market_output() -> dict:
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family_1": "IT_BTP",
            "curve_family_2": "DE_BUND",
            "tenor": "10Y",
            "spread_label": "IT_BTP-DE_BUND 10Y",
            "current_spread_bps": 142.50,
            "daily_change_bps": -1.2,
            "weekly_change_bps": 4.5,
            "monthly_change_bps": -8.3,
            "current_z_score": 0.65,
            "rolling_window_days": 252,
            "high_252d_bps": 198.40,
            "low_252d_bps": 92.10,
            "percentile_252d": 47.2,
            "curve_family_1_yield": 4.50,
            "curve_family_2_yield": 3.075,
        },
        "time_series": [],
    }


def _assert_cross_market_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, "calculate_cross_market_spread called without `config=`"
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "calculate_cross_market_spread_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'calculate_cross_market_spread_tool' (catches imports of the "
        "wrong tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "bps_round_decimals" in cfg.conventions


# ===========================================================================
# api/routes/rates/detail.py — /detail/cross-market endpoint
# ===========================================================================

class TestDetailCrossMarketEndpointWiring:
    def test_cross_market_detail_passes_config(self):
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            detail_module,
            "calculate_cross_market_spread",
            return_value=_well_formed_cross_market_output(),
        ) as mock_cm:
            detail_module.cross_market_detail(
                engine=mock_engine,
                curve_family_1="IT_BTP",
                curve_family_2="DE_BUND",
                tenor="10Y",
                lookback_days=365,
                field_name="YLD_YTM_MID",
            )
        assert mock_cm.call_count == 1
        _assert_cross_market_config_passed(mock_cm.call_args)

    def test_field_name_default_is_none(self):
        """The Query default for field_name must be None; a hardcoded
        string would shadow the YAML's default_field_name."""
        from api.routes.rates import detail as detail_module
        import inspect
        sig = inspect.signature(detail_module.cross_market_detail)
        query_obj = sig.parameters["field_name"].default
        assert query_obj.default is None, (
            f"Query default for field_name must be None, got "
            f"{query_obj.default!r}"
        )

    def test_omitted_field_name_flows_none(self):
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            detail_module,
            "calculate_cross_market_spread",
            return_value=_well_formed_cross_market_output(),
        ) as mock_cm:
            detail_module.cross_market_detail(
                engine=mock_engine,
                curve_family_1="IT_BTP",
                curve_family_2="DE_BUND",
                tenor="10Y",
                lookback_days=365,
                field_name=None,
            )
        params = mock_cm.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"None must reach CrossMarketSpreadInput unchanged; got "
            f"{params.field_name!r}"
        )


# ===========================================================================
# rates_agent/sovereign_bonds/mcp_server.py — calculate_cross_market_spread_tool
# ===========================================================================

class TestMcpCrossMarketToolWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cross_market_spread",
            return_value=_well_formed_cross_market_output(),
        ) as mock_cm:
            output_json = mcp_module.calculate_cross_market_spread_tool(
                curve_family_1="IT_BTP",
                curve_family_2="DE_BUND",
                tenor="10Y",
                lookback_days=365,
                field_name="YLD_YTM_MID",
            )
        assert mock_cm.call_count == 1
        _assert_cross_market_config_passed(mock_cm.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_field_name_default_is_empty_string_sentinel(self):
        """The MCP wrapper's field_name default must be the empty-
        string sentinel, NOT 'YLD_YTM_MID'.  Hardcoding any other
        string shadows the YAML's default_field_name."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(mcp_module.calculate_cross_market_spread_tool)
        default = sig.parameters["field_name"].default
        assert default == "", (
            f"MCP wrapper's field_name default must be '' (empty-string "
            f"sentinel for 'use the YAML default'); got {default!r}"
        )

    def test_omitted_field_name_flows_none_to_input(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cross_market_spread",
            return_value=_well_formed_cross_market_output(),
        ) as mock_cm:
            mcp_module.calculate_cross_market_spread_tool(
                curve_family_1="IT_BTP",
                curve_family_2="DE_BUND",
                tenor="10Y",
                lookback_days=365,
                # field_name omitted
            )
        params = mock_cm.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach CrossMarketSpreadInput as None "
            f"(sentinel for 'use YAML default_field_name'), got "
            f"{params.field_name!r}"
        )

    def test_explicit_field_name_passes_through(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cross_market_spread",
            return_value=_well_formed_cross_market_output(),
        ) as mock_cm:
            mcp_module.calculate_cross_market_spread_tool(
                curve_family_1="IT_BTP",
                curve_family_2="DE_BUND",
                tenor="10Y",
                field_name="PX_LAST",
            )
        params = mock_cm.call_args.kwargs["params"]
        assert params.field_name == "PX_LAST"


# ===========================================================================
# api/routes/rates/cards.py — /cross-market batch endpoint
#
# This endpoint loops settings.CROSS_MARKET_PAIRS and calls the tool
# per pair.  After the migration it MUST:
#   1. load_tool_config exactly ONCE per request (not per pair)
#   2. raise 503 BEFORE the loop if config load fails (Codex P3)
#   3. pass that config to every per-pair tool call
# ===========================================================================

class TestCardsCrossMarketBatchWiring:
    def test_loads_config_once_per_request_regardless_of_pair_count(self):
        """load_tool_config must be invoked exactly once before the
        loop, not once per pair.  Otherwise a 50-pair list would do 50
        unnecessary YAML loads (load_tool_config caches, but the lookup
        still costs a hash + dict-get per pair)."""
        from shared.config import load_tool_config as real_load_tool_config

        from api.routes.rates import cards as cards_module
        from api.routes.rates.cards import settings

        mock_engine = MagicMock(name="engine")
        with patch.object(
            cards_module, "load_tool_config", wraps=real_load_tool_config,
        ) as mock_load, patch.object(
            cards_module,
            "calculate_cross_market_spread",
            return_value=_well_formed_cross_market_output(),
        ):
            cards_module.cross_market(engine=mock_engine)

        # Should call load_tool_config exactly ONCE per request,
        # regardless of how many pairs are in CROSS_MARKET_PAIRS.
        assert mock_load.call_count == 1, (
            f"load_tool_config called {mock_load.call_count} times; "
            f"expected exactly 1 (was the loop accidentally calling it "
            f"per pair?).  Pair count: {len(settings.CROSS_MARKET_PAIRS)}."
        )
        loaded_path = (
            mock_load.call_args.args[0]
            if mock_load.call_args.args
            else mock_load.call_args.kwargs["path"]
        )
        assert str(loaded_path).endswith("cross_market_spread/config.yaml")

    def test_passes_config_to_every_pair(self):
        """Every per-pair call to calculate_cross_market_spread must
        receive the same config object loaded once at the top."""
        from api.routes.rates import cards as cards_module
        from api.routes.rates.cards import settings

        mock_engine = MagicMock(name="engine")
        with patch.object(
            cards_module,
            "calculate_cross_market_spread",
            return_value=_well_formed_cross_market_output(),
        ) as mock_cm:
            cards_module.cross_market(engine=mock_engine)

        # One call per pair
        n_pairs = len(settings.CROSS_MARKET_PAIRS)
        assert mock_cm.call_count == n_pairs, (
            f"expected {n_pairs} per-pair tool calls, got {mock_cm.call_count}"
        )
        # Every call passed the same ToolConfig instance, with the
        # right tool name (catches imports of the wrong CONFIG_PATH).
        configs_passed = [c.kwargs.get("config") for c in mock_cm.call_args_list]
        assert all(cfg is not None for cfg in configs_passed)
        assert all(isinstance(cfg, ToolConfig) for cfg in configs_passed)
        assert all(
            cfg.tool.name == "calculate_cross_market_spread_tool"
            for cfg in configs_passed
        )
        # All calls received the SAME object (loaded once, not reloaded
        # per pair).
        assert all(cfg is configs_passed[0] for cfg in configs_passed)

    def test_config_load_failure_raises_503_before_pair_loop(self):
        """If load_tool_config raises, the endpoint must return 503
        BEFORE attempting any per-pair work.  Otherwise every pair
        would fail and the existing failures-counter path would mask
        the real cause as 'All N cross-market queries failed' — same
        silent-total-failure class as the field_name shadowing bug."""
        from fastapi import HTTPException
        from api.routes.rates import cards as cards_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            cards_module,
            "load_tool_config",
            side_effect=RuntimeError("yaml busted"),
        ), patch.object(
            cards_module,
            "calculate_cross_market_spread",
        ) as mock_cm:
            with pytest.raises(HTTPException) as exc_info:
                cards_module.cross_market(engine=mock_engine)

        assert exc_info.value.status_code == 503
        assert "config" in str(exc_info.value.detail).lower()
        # Critical: the per-pair tool was NEVER called.  If this fires,
        # the defensive load-then-503 ordering has regressed.
        assert mock_cm.call_count == 0, (
            "calculate_cross_market_spread was called despite a config "
            "load failure — the defensive 503-before-loop ordering "
            "has regressed"
        )


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.sovereign_bonds.tools.cross_market_spread import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.sovereign_bonds.tools.cross_market_spread.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_cross_market_spread_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(CROSS_MARKET_CONFIG_PATH)
        assert cfg.tool.name == "calculate_cross_market_spread_tool"
