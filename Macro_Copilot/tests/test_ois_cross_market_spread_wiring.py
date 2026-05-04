"""
test_ois_cross_market_spread_wiring.py — Caller-wiring smoke tests

Mirrors ``test_cross_market_spread_wiring.py`` (sovereign analog) and
``test_ois_curve_spread_wiring.py`` / ``test_ois_rate_level_wiring.py``
(prior OIS migrations), scoped to OIS cross_market_spread's external
callers.  Today there is exactly ONE external caller:

  rates_agent/ois/mcp_server.py::calculate_ois_cross_market_spread_tool

Future OIS REST endpoints + batch surfaces will be added behind the
same ``CONFIG_PATH`` import.

Key load-bearing properties under test:

  - The MCP wrapper passes config explicitly (no auto-load fallback).
  - The MCP wrapper uses the empty-string / None-sentinel pattern so
    the YAML ``default_swap_rate_field`` actually flows through.
  - The hub re-export and CONFIG_PATH public symbol are stable.
  - The bespoke + canonical TimeSeries fields survive a full
    round-trip through the response model.
  - ALL three time-series payloads are stripped from LLM-facing JSON.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.ois.tools.cross_market_spread import (
    CONFIG_PATH as OIS_CROSS_MARKET_SPREAD_CONFIG_PATH,
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


def _well_formed_ois_cms_output() -> dict:
    """Mock matches the post-migration wire shape: snapshot
    ``current_metrics`` PLUS the bespoke wire-frozen ``time_series``
    PLUS two canonical ``shared.schemas.time_series.TimeSeries``
    payloads (BPS spread + Z_SCORE rolling)."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family_1": "USD_SOFR_OIS",
            "curve_family_2": "EUR_ESTR_OIS",
            "tenor": "2Y",
            "spread_label": "USD_SOFR_OIS-EUR_ESTR_OIS 2Y",
            "current_spread_bps": 25.0,
            "daily_change_bps": 1.0,
            "weekly_change_bps": 4.0,
            "monthly_change_bps": -2.0,
            "current_z_score": 0.5,
            "rolling_window_days": 252,
            "high_252d_bps": 35.0,
            "low_252d_bps": 10.0,
            "percentile_252d": 60.0,
            "curve_family_1_rate": 4.30,
            "curve_family_2_rate": 4.05,
        },
        "time_series": [
            {"date": "2026-04-29", "spread_bps": 24.0, "z_score": 0.4},
            {"date": "2026-04-30", "spread_bps": 25.0, "z_score": 0.5},
        ],
        "time_series_spread": {
            "series_name": "usd_sofr_ois_eur_estr_ois_2y_ois_cross_spread",
            "units": "bps",
            "description": "Test OIS cross-market spread series.",
            "rows": [
                {"date": "2026-04-29", "value": 24.0},
                {"date": "2026-04-30", "value": 25.0},
            ],
        },
        "time_series_zscore": {
            "series_name": "usd_sofr_ois_eur_estr_ois_2y_ois_cross_zscore",
            "units": "z_score",
            "description": "Test OIS cross-market z-score series.",
            "rows": [
                {"date": "2026-04-29", "value": 0.4},
                {"date": "2026-04-30", "value": 0.5},
            ],
        },
    }


def _assert_ois_cms_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_ois_cross_market_spread called without `config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "calculate_ois_cross_market_spread_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'calculate_ois_cross_market_spread_tool' (catches imports of "
        "the wrong tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_swap_rate_field" in cfg.conventions


# ===========================================================================
# rates_agent/ois/mcp_server.py — calculate_ois_cross_market_spread_tool
# ===========================================================================

class TestMcpOisCrossMarketSpreadToolWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_ois_cross_market_spread",
            return_value=_well_formed_ois_cms_output(),
        ) as mock_cms:
            output_json = mcp_module.calculate_ois_cross_market_spread_tool(
                curve_family_1="USD_SOFR_OIS",
                curve_family_2="EUR_ESTR_OIS",
                tenor="2Y", lookback_days=365,
                field_name="PX_LAST",
            )
        assert mock_cms.call_count == 1
        _assert_ois_cms_config_passed(mock_cms.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_field_name_default_is_empty_string_sentinel(self):
        """The MCP wrapper's field_name default must be the empty-
        string sentinel, NOT 'PX_LAST'.  Hardcoding any other string
        here shadows the YAML's default_swap_rate_field — same
        shadowing pattern fixed for sovereign curve_move_classifier
        in commit b2605ee."""
        from rates_agent.ois import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(
            mcp_module.calculate_ois_cross_market_spread_tool,
        )
        default = sig.parameters["field_name"].default
        assert default == "", (
            f"MCP wrapper's field_name default must be '' (empty-string "
            f"sentinel for 'use the YAML default'); got {default!r}"
        )

    def test_omitted_field_name_flows_none_to_input(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_ois_cross_market_spread",
            return_value=_well_formed_ois_cms_output(),
        ) as mock_cms:
            mcp_module.calculate_ois_cross_market_spread_tool(
                curve_family_1="USD_SOFR_OIS",
                curve_family_2="EUR_ESTR_OIS",
                tenor="2Y", lookback_days=365,
                # field_name omitted → defaults to ""
            )
        params = mock_cms.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach OISCrossMarketSpreadInput "
            f"as None (sentinel for 'use YAML "
            f"default_swap_rate_field'), got {params.field_name!r}"
        )

    def test_explicit_field_name_passes_through(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_ois_cross_market_spread",
            return_value=_well_formed_ois_cms_output(),
        ) as mock_cms:
            mcp_module.calculate_ois_cross_market_spread_tool(
                curve_family_1="USD_SOFR_OIS",
                curve_family_2="EUR_ESTR_OIS",
                tenor="2Y",
                field_name="PX_BID",
            )
        params = mock_cms.call_args.kwargs["params"]
        assert params.field_name == "PX_BID"

    def test_invalid_input_returns_error_envelope(self):
        """Curves-must-differ validator must trip a controlled error
        envelope, not an unhandled exception."""
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_ois_cross_market_spread",
            return_value=_well_formed_ois_cms_output(),
        ) as mock_cms:
            output_json = mcp_module.calculate_ois_cross_market_spread_tool(
                curve_family_1="USD_SOFR_OIS",
                curve_family_2="USD_SOFR_OIS",
                tenor="2Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert mock_cms.call_count == 0

    def test_time_series_stripped_from_llm_response(self):
        """The MCP wrapper must NOT serialize ANY of the three
        time-series payloads (bespoke list + two canonical TimeSeries)
        back to the LLM."""
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_ois_cross_market_spread",
            return_value=_well_formed_ois_cms_output(),
        ):
            output_json = mcp_module.calculate_ois_cross_market_spread_tool(
                curve_family_1="USD_SOFR_OIS",
                curve_family_2="EUR_ESTR_OIS",
                tenor="2Y",
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
        from rates_agent.ois.tools.cross_market_spread import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.ois.tools.cross_market_spread.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_ois_cross_market_spread_config(self):
        cfg = load_tool_config(OIS_CROSS_MARKET_SPREAD_CONFIG_PATH)
        assert cfg.tool.name == "calculate_ois_cross_market_spread_tool"
        assert cfg.tool.domain == "ois"


# ===========================================================================
# Response-model validation
# ===========================================================================

class TestResponseModelValidation:
    """Pin the wire contract for OIS cross_market_spread.  Mirrors the
    response-model validation pattern from prior OIS migrations + the
    sovereign cross_market_spread suite — guards against the canonical
    TimeSeries shapes silently regressing into bespoke dicts."""

    def test_mock_validates_against_response_model(self):
        from rates_agent.ois.tools.cross_market_spread.schemas import (
            OISCrossMarketSpreadOutput,
        )
        validated = OISCrossMarketSpreadOutput.model_validate(
            _well_formed_ois_cms_output(),
        )
        assert validated.time_series_spread is not None
        assert validated.time_series_zscore is not None
        assert validated.time_series_spread.units.value == "bps"
        assert validated.time_series_zscore.units.value == "z_score"

    def test_response_model_requires_canonical_time_series_fields(self):
        from rates_agent.ois.tools.cross_market_spread.schemas import (
            OISCrossMarketSpreadOutput,
        )
        from pydantic import ValidationError
        for field in ("time_series_spread", "time_series_zscore"):
            bad = _well_formed_ois_cms_output()
            bad.pop(field)
            with pytest.raises(ValidationError):
                OISCrossMarketSpreadOutput.model_validate(bad)

    def test_round_trip_preserves_all_three_time_series_fields(self):
        """``model_validate(...).model_dump()`` MUST preserve all three
        time-series payloads — bespoke list AND both canonical series.
        Wire-frozen names: rate (not yield) on each leg's snapshot
        field — OIS-specific."""
        from rates_agent.ois.tools.cross_market_spread.schemas import (
            OISCrossMarketSpreadOutput,
        )
        validated = OISCrossMarketSpreadOutput.model_validate(
            _well_formed_ois_cms_output(),
        )
        dumped = validated.model_dump()
        assert "current_metrics" in dumped
        assert "time_series" in dumped
        assert "time_series_spread" in dumped
        assert "time_series_zscore" in dumped
        assert dumped["time_series_spread"]["units"] == "bps"
        assert dumped["time_series_zscore"]["units"] == "z_score"
        assert "curve_family_1_rate" in dumped["current_metrics"]
        assert "curve_family_2_rate" in dumped["current_metrics"]
