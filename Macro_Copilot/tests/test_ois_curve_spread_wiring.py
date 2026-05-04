"""
test_ois_curve_spread_wiring.py — Caller-wiring smoke tests

Mirrors ``test_curve_spread_wiring.py`` (the sovereign analog) and
``test_ois_rate_level_wiring.py`` (the first OIS migration), scoped to
OIS curve_spread's external callers.  Today there is exactly ONE
external caller:

  rates_agent/ois/mcp_server.py::calculate_ois_curve_spread_tool

Future OIS REST endpoints + batch surfaces will be added behind the
same ``CONFIG_PATH`` import, at which point new caller-classes mirror
the sovereign curve_spread wiring file's structure.

Key load-bearing properties under test:

  - The MCP wrapper passes config explicitly (no auto-load fallback).
  - The MCP wrapper uses the empty-string / None-sentinel pattern so
    the YAML ``default_swap_rate_field`` actually flows through.
  - The hub re-export and CONFIG_PATH public symbol are stable.
  - The wire-frozen + canonical TimeSeries fields survive a full
    round-trip through the response model.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.ois.tools.curve_spread import (
    CONFIG_PATH as OIS_CURVE_SPREAD_CONFIG_PATH,
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


def _well_formed_ois_curve_spread_output() -> dict:
    """Mock matches the post-migration wire shape: snapshot
    ``current_metrics`` PLUS the bespoke wire-frozen ``time_series``
    PLUS two canonical ``shared.schemas.time_series.TimeSeries``
    payloads (BPS spread + Z_SCORE rolling)."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family": "USD_SOFR_OIS",
            "spread_label": "2s10s",
            "current_spread_bps": 25.0,
            "daily_change_bps": 1.0,
            "current_z_score": 0.5,
            "rolling_window_days": 252,
            "short_tenor_rate": 4.30,
            "long_tenor_rate": 4.55,
        },
        "time_series": [
            {"date": "2026-04-29", "spread_bps": 24.0, "z_score": 0.4},
            {"date": "2026-04-30", "spread_bps": 25.0, "z_score": 0.5},
        ],
        "time_series_spread": {
            "series_name": "usd_sofr_ois_2y_10y_ois_spread",
            "units": "bps",
            "description": "Test OIS spread series.",
            "rows": [
                {"date": "2026-04-29", "value": 24.0},
                {"date": "2026-04-30", "value": 25.0},
            ],
        },
        "time_series_zscore": {
            "series_name": "usd_sofr_ois_2y_10y_ois_zscore",
            "units": "z_score",
            "description": "Test OIS z-score series.",
            "rows": [
                {"date": "2026-04-29", "value": 0.4},
                {"date": "2026-04-30", "value": 0.5},
            ],
        },
    }


def _assert_ois_curve_spread_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_ois_curve_spread called without `config=`"
    )
    assert isinstance(cfg, ToolConfig), (
        f"`config` must be a ToolConfig, got {type(cfg).__name__}"
    )
    assert cfg.tool.name == "calculate_ois_curve_spread_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'calculate_ois_curve_spread_tool' (catches imports of the "
        "wrong tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_swap_rate_field" in cfg.conventions


# ===========================================================================
# rates_agent/ois/mcp_server.py — calculate_ois_curve_spread_tool
# ===========================================================================

class TestMcpOisCurveSpreadToolWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_ois_curve_spread",
            return_value=_well_formed_ois_curve_spread_output(),
        ) as mock_cs:
            output_json = mcp_module.calculate_ois_curve_spread_tool(
                curve_family="USD_SOFR_OIS",
                short_tenor="2Y", long_tenor="10Y",
                lookback_days=365,
                field_name="PX_LAST",
            )
        assert mock_cs.call_count == 1
        _assert_ois_curve_spread_config_passed(mock_cs.call_args)
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
        sig = inspect.signature(mcp_module.calculate_ois_curve_spread_tool)
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
            "calculate_ois_curve_spread",
            return_value=_well_formed_ois_curve_spread_output(),
        ) as mock_cs:
            mcp_module.calculate_ois_curve_spread_tool(
                curve_family="USD_SOFR_OIS",
                short_tenor="2Y", long_tenor="10Y",
                lookback_days=365,
                # field_name omitted → defaults to ""
            )
        params = mock_cs.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach OISCurveSpreadInput as None "
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
            "calculate_ois_curve_spread",
            return_value=_well_formed_ois_curve_spread_output(),
        ) as mock_cs:
            mcp_module.calculate_ois_curve_spread_tool(
                curve_family="USD_SOFR_OIS",
                short_tenor="2Y", long_tenor="10Y",
                field_name="PX_BID",
            )
        params = mock_cs.call_args.kwargs["params"]
        assert params.field_name == "PX_BID"

    def test_invalid_input_returns_error_envelope(self):
        """Tenor-equality validator must trip a controlled error
        envelope, not an unhandled exception."""
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_ois_curve_spread",
            return_value=_well_formed_ois_curve_spread_output(),
        ) as mock_cs:
            output_json = mcp_module.calculate_ois_curve_spread_tool(
                curve_family="USD_SOFR_OIS",
                short_tenor="5Y", long_tenor="5Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        # Tool function must NOT have been invoked because the
        # ValidationError fires before the engine acquisition.
        assert mock_cs.call_count == 0

    def test_time_series_stripped_from_llm_response(self):
        """The MCP wrapper must NOT serialize ANY of the three
        time-series payloads (bespoke list + two canonical TimeSeries)
        back to the LLM — frontend / future REST surfaces consume the
        full dict directly, the LLM context shouldn't carry every
        historical row."""
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_ois_curve_spread",
            return_value=_well_formed_ois_curve_spread_output(),
        ):
            output_json = mcp_module.calculate_ois_curve_spread_tool(
                curve_family="USD_SOFR_OIS",
                short_tenor="2Y", long_tenor="10Y",
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
        from rates_agent.ois.tools.curve_spread import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.ois.tools.curve_spread.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_ois_curve_spread_config(self):
        cfg = load_tool_config(OIS_CURVE_SPREAD_CONFIG_PATH)
        assert cfg.tool.name == "calculate_ois_curve_spread_tool"
        assert cfg.tool.domain == "ois"


# ===========================================================================
# Response-model validation
# ===========================================================================

class TestResponseModelValidation:
    """Pin the wire contract for OIS curve_spread.  Mirrors the
    response-model validation pattern added in PR #60 to the sovereign
    wiring suites + the OIS rate_level wiring suite — guards against
    the canonical TimeSeries shapes silently regressing into bespoke
    dicts."""

    def test_mock_validates_against_response_model(self):
        from rates_agent.ois.tools.curve_spread.schemas import (
            OISCurveSpreadOutput,
        )
        validated = OISCurveSpreadOutput.model_validate(
            _well_formed_ois_curve_spread_output(),
        )
        assert validated.time_series_spread is not None
        assert validated.time_series_zscore is not None
        assert validated.time_series_spread.units.value == "bps"
        assert validated.time_series_zscore.units.value == "z_score"

    def test_response_model_requires_canonical_time_series_fields(self):
        from rates_agent.ois.tools.curve_spread.schemas import (
            OISCurveSpreadOutput,
        )
        from pydantic import ValidationError
        for field in ("time_series_spread", "time_series_zscore"):
            bad = _well_formed_ois_curve_spread_output()
            bad.pop(field)
            with pytest.raises(ValidationError):
                OISCurveSpreadOutput.model_validate(bad)

    def test_round_trip_preserves_all_three_time_series_fields(self):
        """``model_validate(...).model_dump()`` MUST preserve all three
        time-series payloads — the bespoke list AND both canonical
        series.  Guards against a future schema edit that drops fields
        silently on serialization."""
        from rates_agent.ois.tools.curve_spread.schemas import (
            OISCurveSpreadOutput,
        )
        validated = OISCurveSpreadOutput.model_validate(
            _well_formed_ois_curve_spread_output(),
        )
        dumped = validated.model_dump()
        assert "current_metrics" in dumped
        assert "time_series" in dumped          # bespoke wire-frozen
        assert "time_series_spread" in dumped   # canonical BPS
        assert "time_series_zscore" in dumped   # canonical Z_SCORE
        assert dumped["time_series_spread"]["units"] == "bps"
        assert dumped["time_series_zscore"]["units"] == "z_score"
        # Wire-frozen names: rate (not yield) — OIS-specific.
        assert "short_tenor_rate" in dumped["current_metrics"]
        assert "long_tenor_rate" in dumped["current_metrics"]
