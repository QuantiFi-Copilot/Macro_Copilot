"""
test_swap_spread_wiring.py — Caller-wiring smoke tests for the
cross-domain swap-spread primitive.

Mirrors the prior wiring suites (``test_ois_cross_market_spread_wiring.py``,
``test_ois_curve_spread_wiring.py``, ``test_ois_rate_level_wiring.py``).

External callers today: exactly ONE — the OIS MCP wrapper at
``rates_agent/ois/mcp_server.py::calculate_swap_spread_tool``.

Key load-bearing properties under test:

  - The MCP wrapper passes config explicitly (no auto-load fallback).
  - The MCP wrapper uses the empty-string sentinel pattern PER LEG
    (sovereign + OIS field names independently) so the YAML
    per-leg defaults flow through.
  - The hub re-export and CONFIG_PATH public symbol are stable.
  - The bespoke + canonical TimeSeries fields survive a full
    round-trip through the response model.
  - ALL three time-series payloads are stripped from LLM-facing JSON.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.ois.tools.swap_spread import (
    CONFIG_PATH as SWAP_SPREAD_CONFIG_PATH,
)
from shared.config import ToolConfig, clear_tool_config_cache, load_tool_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_swap_spread_output() -> dict:
    """Mock matches the post-build wire shape."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "sovereign_curve_family": "UST",
            "ois_curve_family": "USD_SOFR_OIS",
            "tenor": "10Y",
            "spread_label": "UST-USD_SOFR_OIS 10Y",
            "current_spread_bps": 25.0,
            "daily_change_bps": 1.0,
            "weekly_change_bps": 4.0,
            "monthly_change_bps": -2.0,
            "current_z_score": 0.5,
            "rolling_window_days": 252,
            "high_252d_bps": 35.0,
            "low_252d_bps": 10.0,
            "percentile_252d": 60.0,
            "sovereign_yield_pct": 4.30,
            "ois_rate_pct": 4.05,
        },
        "time_series": [
            {"date": "2026-04-29", "spread_bps": 24.0, "z_score": 0.4},
            {"date": "2026-04-30", "spread_bps": 25.0, "z_score": 0.5},
        ],
        "time_series_spread": {
            "series_name": "ust_usd_sofr_ois_10y_swap_spread",
            "units": "bps",
            "description": "Test swap spread series.",
            "rows": [
                {"date": "2026-04-29", "value": 24.0},
                {"date": "2026-04-30", "value": 25.0},
            ],
        },
        "time_series_zscore": {
            "series_name": "ust_usd_sofr_ois_10y_swap_spread_zscore",
            "units": "z_score",
            "description": "Test swap spread z-score series.",
            "rows": [
                {"date": "2026-04-29", "value": 0.4},
                {"date": "2026-04-30", "value": 0.5},
            ],
        },
        # Consolidation update: SwapSpreadOutput gained a REQUIRED third
        # canonical series — time_series_change_zscore (rolling z-score
        # of the day-over-day spread CHANGE, the event-study "widened a
        # lot today" signal, distinct from the level z-score above).
        "time_series_change_zscore": {
            "series_name": "ust_usd_sofr_ois_10y_swap_spread_change_zscore",
            "units": "z_score",
            "description": "Test swap spread change z-score series.",
            "rows": [
                {"date": "2026-04-29", "value": 0.1},
                {"date": "2026-04-30", "value": 0.2},
            ],
        },
    }


def _assert_swap_spread_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_swap_spread called without `config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "calculate_swap_spread_tool"
    assert "z_score_window_days" in cfg.conventions
    assert "sovereign_leg_default_field" in cfg.conventions
    assert "ois_leg_default_field" in cfg.conventions


# ===========================================================================
# rates_agent/ois/mcp_server.py — calculate_swap_spread_tool
# ===========================================================================

class TestMcpSwapSpreadToolWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_swap_spread",
            return_value=_well_formed_swap_spread_output(),
        ) as mock_ss:
            output_json = mcp_module.calculate_swap_spread_tool(
                sovereign_curve_family="UST",
                ois_curve_family="USD_SOFR_OIS",
                tenor="10Y", lookback_days=365,
                sovereign_field_name="YLD_YTM_MID",
                ois_field_name="PX_LAST",
            )
        assert mock_ss.call_count == 1
        _assert_swap_spread_config_passed(mock_ss.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_per_leg_field_name_defaults_are_empty_string_sentinels(self):
        """Both sovereign_field_name and ois_field_name MCP defaults
        must be the empty-string sentinel.  Hardcoding any other
        string here shadows the YAML's per-leg defaults — same
        shadowing pattern fixed for sovereign curve_move_classifier
        in commit b2605ee, applied per-leg here."""
        from rates_agent.ois import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(
            mcp_module.calculate_swap_spread_tool,
        )
        sov_default = sig.parameters["sovereign_field_name"].default
        ois_default = sig.parameters["ois_field_name"].default
        assert sov_default == "", (
            f"sovereign_field_name default must be ''; got {sov_default!r}"
        )
        assert ois_default == "", (
            f"ois_field_name default must be ''; got {ois_default!r}"
        )

    def test_omitted_per_leg_field_names_flow_none_to_input(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_swap_spread",
            return_value=_well_formed_swap_spread_output(),
        ) as mock_ss:
            mcp_module.calculate_swap_spread_tool(
                sovereign_curve_family="UST",
                ois_curve_family="USD_SOFR_OIS",
                tenor="10Y", lookback_days=365,
                # Both field names omitted → defaults to ""
            )
        params = mock_ss.call_args.kwargs["params"]
        assert params.sovereign_field_name is None
        assert params.ois_field_name is None

    def test_explicit_per_leg_field_names_pass_through(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_swap_spread",
            return_value=_well_formed_swap_spread_output(),
        ) as mock_ss:
            mcp_module.calculate_swap_spread_tool(
                sovereign_curve_family="UST",
                ois_curve_family="USD_SOFR_OIS",
                tenor="10Y",
                sovereign_field_name="YLD_BID",
                ois_field_name="PX_BID",
            )
        params = mock_ss.call_args.kwargs["params"]
        assert params.sovereign_field_name == "YLD_BID"
        assert params.ois_field_name == "PX_BID"

    def test_invalid_input_returns_error_envelope(self):
        """Curves-must-differ validator must trip a controlled error
        envelope, not an unhandled exception."""
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_swap_spread",
            return_value=_well_formed_swap_spread_output(),
        ) as mock_ss:
            output_json = mcp_module.calculate_swap_spread_tool(
                sovereign_curve_family="UST",
                ois_curve_family="UST",  # validator trips
                tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert mock_ss.call_count == 0

    def test_time_series_stripped_from_llm_response(self):
        """The MCP wrapper must NOT serialize ANY of the three
        time-series payloads (bespoke + canonical spread + canonical
        zscore) back to the LLM."""
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_swap_spread",
            return_value=_well_formed_swap_spread_output(),
        ):
            output_json = mcp_module.calculate_swap_spread_tool(
                sovereign_curve_family="UST",
                ois_curve_family="USD_SOFR_OIS",
                tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        assert "time_series" not in parsed
        assert "time_series_spread" not in parsed
        assert "time_series_zscore" not in parsed


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.ois.tools.swap_spread import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.ois.tools.swap_spread.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_swap_spread_config(self):
        cfg = load_tool_config(SWAP_SPREAD_CONFIG_PATH)
        assert cfg.tool.name == "calculate_swap_spread_tool"
        assert cfg.tool.domain == "ois"


# ===========================================================================
# Response-model validation
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        from rates_agent.ois.tools.swap_spread.schemas import (
            SwapSpreadOutput,
        )
        validated = SwapSpreadOutput.model_validate(
            _well_formed_swap_spread_output(),
        )
        assert validated.time_series_spread is not None
        assert validated.time_series_zscore is not None
        assert validated.time_series_change_zscore is not None
        assert validated.time_series_spread.units.value == "bps"
        assert validated.time_series_zscore.units.value == "z_score"
        assert validated.time_series_change_zscore.units.value == "z_score"

    def test_response_model_requires_canonical_time_series_fields(self):
        from rates_agent.ois.tools.swap_spread.schemas import (
            SwapSpreadOutput,
        )
        from pydantic import ValidationError
        # time_series_change_zscore joined the required canonical set
        # in the consolidation (event-study change-stretch signal).
        for field in (
            "time_series_spread",
            "time_series_zscore",
            "time_series_change_zscore",
        ):
            bad = _well_formed_swap_spread_output()
            bad.pop(field)
            with pytest.raises(ValidationError):
                SwapSpreadOutput.model_validate(bad)

    def test_round_trip_preserves_all_canonical_time_series_fields(self):
        from rates_agent.ois.tools.swap_spread.schemas import (
            SwapSpreadOutput,
        )
        validated = SwapSpreadOutput.model_validate(
            _well_formed_swap_spread_output(),
        )
        dumped = validated.model_dump()
        assert "current_metrics" in dumped
        assert "time_series" in dumped
        assert "time_series_spread" in dumped
        assert "time_series_zscore" in dumped
        # Consolidation update: the change-z-score canonical series
        # must round-trip too.
        assert "time_series_change_zscore" in dumped
        # Cross-domain-specific snapshot field names — explicit
        # check that sovereign_yield_pct + ois_rate_pct are on the
        # wire (not curve_family_1_yield etc).
        assert "sovereign_yield_pct" in dumped["current_metrics"]
        assert "ois_rate_pct" in dumped["current_metrics"]
