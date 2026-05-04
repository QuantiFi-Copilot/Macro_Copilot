"""
test_ois_rate_level_wiring.py — Caller-wiring smoke tests

Mirrors ``test_yield_levels_wiring.py`` (the sovereign analog), scoped
to OIS rate_level's external callers.  Today there is exactly ONE
external caller:

  rates_agent/ois/mcp_server.py::calculate_ois_rate_level_tool

Future OIS REST endpoints + batch surfaces will be added behind the
same ``CONFIG_PATH`` import, at which point new caller-classes mirror
the sovereign yield_levels wiring file's structure.

Key load-bearing properties under test:

  - The MCP wrapper passes config explicitly (no auto-load
    fallback).  ``config=`` argument is observable at the callsite.
  - The MCP wrapper uses the empty-string / None-sentinel pattern so
    the YAML ``default_swap_rate_field`` actually flows through
    (the bug that bit sovereign curve_move_classifier and was fixed
    in commit b2605ee).
  - The hub re-export (``rates_agent.ois.tools.schemas``) and
    CONFIG_PATH public symbol are stable.
  - The wire-frozen + canonical TimeSeries fields survive a full
    round-trip through the response model (regression guard against
    silent shape changes).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.ois.tools.rate_level import (
    CONFIG_PATH as OIS_RATE_LEVEL_CONFIG_PATH,
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


def _well_formed_ois_rate_level_output() -> dict:
    """Mock matches the post-migration wire shape: snapshot
    ``current_metrics`` PLUS the canonical
    ``shared.schemas.time_series.TimeSeries`` payload."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family": "USD_SOFR_OIS",
            "tenor": "2Y",
            "current_rate_pct": 4.30,
            "daily_change_bps": 1.5,
            "weekly_change_bps": 8.0,
            "monthly_change_bps": -3.0,
            "z_score": 0.85,
            "high_252d_pct": 4.95,
            "low_252d_pct": 3.40,
            "percentile_252d": 58.0,
            "observation_count": 252,
        },
        "time_series": {
            "series_name": "usd_sofr_ois_2y_ois_rate",
            "units": "percent",
            "description": "Test OIS rate series.",
            "rows": [
                {"date": "2026-04-29", "value": 4.29},
                {"date": "2026-04-30", "value": 4.30},
            ],
        },
    }


def _assert_ois_rate_level_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, "get_ois_rate_level called without `config=`"
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "get_ois_rate_level_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'get_ois_rate_level_tool' (catches imports of the wrong tool's "
        "CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_swap_rate_field" in cfg.conventions


# ===========================================================================
# rates_agent/ois/mcp_server.py — calculate_ois_rate_level_tool
# ===========================================================================

class TestMcpOisRateLevelToolWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "get_ois_rate_level",
            return_value=_well_formed_ois_rate_level_output(),
        ) as mock_rl:
            output_json = mcp_module.calculate_ois_rate_level_tool(
                curve_family="USD_SOFR_OIS",
                tenor="2Y",
                lookback_days=365,
                field_name="PX_LAST",
            )
        assert mock_rl.call_count == 1
        _assert_ois_rate_level_config_passed(mock_rl.call_args)
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
        sig = inspect.signature(mcp_module.calculate_ois_rate_level_tool)
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
            "get_ois_rate_level",
            return_value=_well_formed_ois_rate_level_output(),
        ) as mock_rl:
            mcp_module.calculate_ois_rate_level_tool(
                curve_family="USD_SOFR_OIS",
                tenor="2Y",
                lookback_days=365,
                # field_name omitted → defaults to ""
            )
        params = mock_rl.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach OISRateLevelInput as None "
            f"(sentinel for 'use YAML default_swap_rate_field'), got "
            f"{params.field_name!r}"
        )

    def test_explicit_field_name_passes_through(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "get_ois_rate_level",
            return_value=_well_formed_ois_rate_level_output(),
        ) as mock_rl:
            mcp_module.calculate_ois_rate_level_tool(
                curve_family="USD_SOFR_OIS",
                tenor="2Y",
                field_name="PX_BID",
            )
        params = mock_rl.call_args.kwargs["params"]
        assert params.field_name == "PX_BID"

    def test_time_series_stripped_from_llm_response(self):
        """The MCP wrapper must NOT serialize the canonical TimeSeries
        payload back to the LLM — frontend REST surfaces get the full
        dict directly from the tool, but the LLM context shouldn't
        consume thousands of historical rows just to answer
        'where's SOFR 2Y?'.  Mirrors sovereign get_yield_levels_tool's
        behaviour."""
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "get_ois_rate_level",
            return_value=_well_formed_ois_rate_level_output(),
        ):
            output_json = mcp_module.calculate_ois_rate_level_tool(
                curve_family="USD_SOFR_OIS",
                tenor="2Y",
            )
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        assert "time_series" not in parsed, (
            "calculate_ois_rate_level_tool must strip the canonical "
            "time_series before returning to the LLM"
        )


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.ois.tools.rate_level import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.ois.tools.rate_level.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_ois_rate_level_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(OIS_RATE_LEVEL_CONFIG_PATH)
        assert cfg.tool.name == "get_ois_rate_level_tool"
        assert cfg.tool.domain == "ois"


# ===========================================================================
# Response-model validation
# ===========================================================================

class TestResponseModelValidation:
    """Pin the wire contract for OIS rate_level.  Mirrors the
    response-model validation pattern added in PR #60 to the sovereign
    wiring suites — guards against the canonical TimeSeries shape
    silently regressing into a bespoke dict."""

    def test_mock_validates_against_response_model(self):
        from rates_agent.ois.tools.rate_level.schemas import (
            OISRateLevelOutput,
        )
        validated = OISRateLevelOutput.model_validate(
            _well_formed_ois_rate_level_output(),
        )
        assert validated.time_series is not None
        assert validated.time_series.units.value == "percent"

    def test_response_model_requires_time_series_field(self):
        from rates_agent.ois.tools.rate_level.schemas import (
            OISRateLevelOutput,
        )
        from pydantic import ValidationError
        bad_mock = _well_formed_ois_rate_level_output()
        bad_mock.pop("time_series")
        with pytest.raises(ValidationError):
            OISRateLevelOutput.model_validate(bad_mock)

    def test_round_trip_preserves_canonical_fields(self):
        """``model_validate(...).model_dump()`` MUST preserve the
        canonical TimeSeries shape — guards against a future schema
        edit that drops fields silently on serialization."""
        from rates_agent.ois.tools.rate_level.schemas import (
            OISRateLevelOutput,
        )
        validated = OISRateLevelOutput.model_validate(
            _well_formed_ois_rate_level_output(),
        )
        dumped = validated.model_dump()
        assert "current_metrics" in dumped
        assert "time_series" in dumped
        assert dumped["time_series"]["units"] == "percent"
        assert dumped["time_series"]["series_name"] == "usd_sofr_ois_2y_ois_rate"
