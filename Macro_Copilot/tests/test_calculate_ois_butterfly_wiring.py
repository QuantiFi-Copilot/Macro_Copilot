"""
test_calculate_ois_butterfly_wiring.py — Caller-wiring smoke tests
=====================================================================

Pins the wiring of ``calculate_ois_butterfly`` into the surfaces that
matter for V1:

  1. ``rates_agent/ois/mcp_server.py::calculate_ois_butterfly_tool``
     - passes config=ToolConfig explicitly (no auto-load fallback).
     - uses the empty-string / None-sentinel pattern so the YAML
       default_swap_rate_field actually flows through.
     - returns a controlled JSON error envelope on validation failure.

  2. The schemas hub (``rates_agent/ois/tools/schemas/__init__.py``)
     re-exports the input/output classes and they resolve to the same
     Pydantic class as the package init.

  3. ``rates_agent/workflows/__init__.py::_PRIMITIVE_SPECS`` registers
     the tool with the correct callable + config + output_field_units
     (BPS for time_series_butterfly, Z_SCORE for time_series_zscore).

  4. The bundled ``CONFIG_PATH`` public symbol resolves to the
     primitive's config.yaml (same surface every other rates tool
     exposes).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.ois.tools.calculate_ois_butterfly import (
    CONFIG_PATH as OIS_BUTTERFLY_CONFIG_PATH,
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


def _well_formed_output() -> dict:
    """Mock matches the post-cleanup wire shape: bespoke wire-frozen
    ``time_series`` PLUS the canonical ``time_series_butterfly`` and
    ``time_series_zscore`` fields."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family": "USD_SOFR_OIS",
            "butterfly_label": "2s5s10s",
            "current_butterfly_bps": -12.50,
            "daily_change_bps": 0.75,
            "current_z_score": -0.42,
            "rolling_window_days": 252,
            "high_252d_bps": 18.40,
            "low_252d_bps": -45.10,
            "percentile_252d": 51.6,
            "wing_short_bps": -25.30,
            "wing_long_bps": -12.80,
            "short_tenor_rate": 4.10,
            "belly_tenor_rate": 3.98,
            "long_tenor_rate": 4.12,
        },
        "time_series": [
            {"date": "2026-04-29", "butterfly_bps": -13.25, "z_score": -0.50},
            {"date": "2026-04-30", "butterfly_bps": -12.50, "z_score": -0.42},
        ],
        "time_series_butterfly": {
            "series_name": "usd_sofr_ois_2y_5y_10y_ois_butterfly",
            "units": "bps",
            "description": "Test OIS butterfly series.",
            "rows": [
                {"date": "2026-04-29", "value": -13.25},
                {"date": "2026-04-30", "value": -12.50},
            ],
        },
        "time_series_zscore": {
            "series_name": "usd_sofr_ois_2y_5y_10y_ois_zscore",
            "units": "z_score",
            "description": "Test OIS butterfly z-score series.",
            "rows": [
                {"date": "2026-04-29", "value": -0.50},
                {"date": "2026-04-30", "value": -0.42},
            ],
        },
    }


def _assert_butterfly_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, "calculate_ois_butterfly called without `config=`"
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "calculate_ois_butterfly_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'calculate_ois_butterfly_tool' (catches imports of the wrong "
        "tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_swap_rate_field" in cfg.conventions


# ===========================================================================
# 1. MCP wrapper — rates_agent/ois/mcp_server.py
# ===========================================================================

class TestMcpOISButterflyToolWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_ois_butterfly",
            return_value=_well_formed_output(),
        ) as mock_bf:
            output_json = mcp_module.calculate_ois_butterfly_tool(
                curve_family="USD_SOFR_OIS",
                short_tenor="2Y",
                belly_tenor="5Y",
                long_tenor="10Y",
                lookback_days=365,
                field_name="PX_LAST",
            )
        assert mock_bf.call_count == 1
        _assert_butterfly_config_passed(mock_bf.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_field_name_default_is_empty_string_sentinel(self):
        """The MCP wrapper's field_name default must be the empty-
        string sentinel, NOT 'PX_LAST'.  Hardcoding any other string
        here shadows the YAML's default_swap_rate_field."""
        from rates_agent.ois import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(mcp_module.calculate_ois_butterfly_tool)
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
            "calculate_ois_butterfly",
            return_value=_well_formed_output(),
        ) as mock_bf:
            mcp_module.calculate_ois_butterfly_tool(
                curve_family="USD_SOFR_OIS",
                short_tenor="2Y",
                belly_tenor="5Y",
                long_tenor="10Y",
                lookback_days=365,
                # field_name omitted
            )
        params = mock_bf.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach OISButterflyInput as None "
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
            "calculate_ois_butterfly",
            return_value=_well_formed_output(),
        ) as mock_bf:
            mcp_module.calculate_ois_butterfly_tool(
                curve_family="USD_SOFR_OIS",
                short_tenor="2Y",
                belly_tenor="5Y",
                long_tenor="10Y",
                field_name="PX_BID",
            )
        params = mock_bf.call_args.kwargs["params"]
        assert params.field_name == "PX_BID"

    def test_invalid_curve_family_returns_controlled_error_envelope(self):
        """Non-OIS curve_family must produce a JSON error envelope
        (not an unhandled exception) — closed-enum P8 refusal at the
        schema layer is caught by the wrapper's ValidationError
        branch."""
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.calculate_ois_butterfly_tool(
                curve_family="UST",  # sovereign — not an OIS curve
                short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_strips_time_series_from_llm_response(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_ois_butterfly",
            return_value=_well_formed_output(),
        ):
            output_json = mcp_module.calculate_ois_butterfly_tool(
                curve_family="USD_SOFR_OIS",
                short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
            )
        parsed = json.loads(output_json)
        # The LLM-facing payload must not include time_series rows.
        assert "time_series" not in parsed
        assert "time_series_butterfly" not in parsed
        assert "time_series_zscore" not in parsed
        # But current_metrics must still be there.
        assert "current_metrics" in parsed


# ===========================================================================
# 2. CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.ois.tools.calculate_ois_butterfly import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.ois.tools.calculate_ois_butterfly.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_ois_butterfly_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(OIS_BUTTERFLY_CONFIG_PATH)
        assert cfg.tool.name == "calculate_ois_butterfly_tool"
        assert cfg.tool.domain == "ois"


# ===========================================================================
# 3. Schemas hub re-export
# ===========================================================================

class TestSchemasHubReExport:
    def test_input_schema_re_exported(self):
        from rates_agent.ois.tools.schemas import OISButterflyInput as via_hub
        from rates_agent.ois.tools.calculate_ois_butterfly import (
            OISButterflyInput as via_package,
        )
        assert via_hub is via_package

    def test_output_schema_re_exported(self):
        from rates_agent.ois.tools.schemas import OISButterflyOutput as via_hub
        from rates_agent.ois.tools.calculate_ois_butterfly import (
            OISButterflyOutput as via_package,
        )
        assert via_hub is via_package

    def test_metrics_schema_re_exported(self):
        from rates_agent.ois.tools.schemas import (
            OISButterflyCurrentMetrics as via_hub,
        )
        from rates_agent.ois.tools.calculate_ois_butterfly import (
            OISButterflyCurrentMetrics as via_package,
        )
        assert via_hub is via_package


# ===========================================================================
# 4. workflows PrimitiveSpec registration
# ===========================================================================

class TestWorkflowsPrimitiveRegistration:
    def test_primitive_registered_in_resolver(self):
        from rates_agent.workflows import (
            known_rates_primitives,
            rates_primitive_resolver,
        )
        assert "calculate_ois_butterfly_tool" in known_rates_primitives()
        spec = rates_primitive_resolver("calculate_ois_butterfly_tool")
        assert spec.tool_name == "calculate_ois_butterfly_tool"

    def test_primitive_spec_uses_correct_callable_and_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.ois.tools.calculate_ois_butterfly import (
            OISButterflyInput,
            OISButterflyOutput,
            calculate_ois_butterfly,
        )
        spec = rates_primitive_resolver("calculate_ois_butterfly_tool")
        assert spec.callable is calculate_ois_butterfly
        assert spec.input_class is OISButterflyInput
        assert spec.output_class is OISButterflyOutput
        assert spec.config_path == OIS_BUTTERFLY_CONFIG_PATH

    def test_primitive_spec_output_field_units_canonical_series(self):
        """``time_series_butterfly`` must declare BPS units;
        ``time_series_zscore`` must declare Z_SCORE units.  Operator-
        layer unit-compat checks rely on these declarations."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver("calculate_ois_butterfly_tool")
        units = spec.output_field_units
        assert units["time_series_butterfly"] == "bps"
        assert units["time_series_zscore"] == "z_score"
        # Bespoke list inherits the butterfly unit (BPS).
        assert units["time_series"] == "bps"


# ===========================================================================
# 5. OutputModel validation
# ===========================================================================

class TestOutputModelValidation:
    def test_mock_validates_against_response_model(self):
        from rates_agent.ois.tools.calculate_ois_butterfly.schemas import (
            OISButterflyOutput,
        )
        validated = OISButterflyOutput.model_validate(_well_formed_output())
        assert validated.time_series_butterfly is not None
        assert validated.time_series_zscore is not None
        assert validated.time_series_butterfly.units.value == "bps"
        assert validated.time_series_zscore.units.value == "z_score"

    def test_response_model_requires_canonical_time_series_fields(self):
        from rates_agent.ois.tools.calculate_ois_butterfly.schemas import (
            OISButterflyOutput,
        )
        from pydantic import ValidationError
        for field in ("time_series_butterfly", "time_series_zscore"):
            bad = _well_formed_output()
            bad.pop(field)
            with pytest.raises(ValidationError):
                OISButterflyOutput.model_validate(bad)
