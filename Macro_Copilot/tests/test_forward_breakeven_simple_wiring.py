"""
test_forward_breakeven_simple_wiring.py — Caller-wiring smoke tests
for the linker forward_breakeven_simple primitive.

Mirrors ``test_breakeven_inflation_simple_wiring.py`` (the closest
spot analog), scoped to forward_breakeven_simple's external callers:

  1. rates_agent/inflation_indexed_bonds/mcp_server.py
     ::calculate_forward_breakeven_simple_tool
  2. rates_agent/workflows/__init__.py PrimitiveSpec registration

Key load-bearing properties under test:
  - The MCP wrapper passes config explicitly (no auto-load fallback).
  - The MCP wrapper uses the empty-string / None-sentinel pattern so
    the YAML ``default_field_name`` actually flows through.
  - The MCP wrapper surfaces the controlled error envelope for the
    no-row / cross-country / leg-pollution cases.
  - The hub re-export and CONFIG_PATH public symbol are stable.
  - The wire-frozen + canonical TimeSeries fields survive a full
    round-trip through the response model.
  - The workflow registry exposes the primitive with the correct
    ``output_field_units`` declarations (BPS for time series, Z_SCORE
    for z-score series).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.inflation_indexed_bonds.tools.forward_breakeven_simple import (
    CONFIG_PATH as FORWARD_BREAKEVEN_SIMPLE_CONFIG_PATH,
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


def _well_formed_forward_output() -> dict:
    """Mock matches the wire shape: snapshot ``current_metrics`` PLUS
    bespoke ``time_series`` row list PLUS the two canonical TimeSeries
    payloads."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-08",
            "nominal_curve_family": "UST",
            "linker_curve_family": "USD_TIPS",
            "start_tenor": "5Y",
            "end_tenor": "10Y",
            "forward_window_label": "UST/USD_TIPS 5Y10Y",
            "forward_breakeven_pct": 2.7500,
            "forward_breakeven_bps": 275.00,
            "daily_change_bps": 1.50,
            "weekly_change_bps": 8.00,
            "monthly_change_bps": -3.00,
            "current_z_score": 0.85,
            "rolling_window_days": 252,
            "high_252d_bps": 310.00,
            "low_252d_bps": 235.00,
            "percentile_252d": 58.0,
            "start_breakeven_bps": 245.00,
            "end_breakeven_bps": 260.00,
            "start_years": 5.0,
            "end_years": 10.0,
            "methodology_label": (
                "Forward bond-implied breakeven inflation; year-"
                "weighted linear; not a clean forward expected-"
                "inflation read."
            ),
        },
        "time_series": [
            {"date": "2026-04-07", "forward_breakeven_bps": 274.00, "z_score": 0.82},
            {"date": "2026-04-08", "forward_breakeven_bps": 275.00, "z_score": 0.85},
        ],
        "time_series_forward": {
            "series_name": "ust_usd_tips_5y_10y_forward_breakeven",
            "units": "bps",
            "description": "Test forward breakeven series.",
            "rows": [
                {"date": "2026-04-07", "value": 274.00},
                {"date": "2026-04-08", "value": 275.00},
            ],
        },
        "time_series_zscore": {
            "series_name": "ust_usd_tips_5y_10y_forward_breakeven_zscore",
            "units": "z_score",
            "description": "Test forward z-score series.",
            "rows": [
                {"date": "2026-04-07", "value": 0.82},
                {"date": "2026-04-08", "value": 0.85},
            ],
        },
    }


def _assert_forward_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_forward_breakeven_simple called without `config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "calculate_forward_breakeven_simple_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'calculate_forward_breakeven_simple_tool' (catches imports of "
        "the wrong tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_field_name" in cfg.conventions
    assert "forward_compounding_mode" in cfg.conventions


# ===========================================================================
# rates_agent/inflation_indexed_bonds/mcp_server.py
# ===========================================================================

class TestMcpForwardWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_forward_breakeven_simple",
            return_value=_well_formed_forward_output(),
        ) as mock_fb:
            output_json = mcp_module.calculate_forward_breakeven_simple_tool(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                start_tenor="5Y",
                end_tenor="10Y",
                lookback_days=365,
                field_name="YLD_YTM_MID",
            )
        assert mock_fb.call_count == 1
        _assert_forward_config_passed(mock_fb.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_field_name_default_is_empty_string_sentinel(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(
            mcp_module.calculate_forward_breakeven_simple_tool,
        )
        default = sig.parameters["field_name"].default
        assert default == "", (
            f"MCP wrapper's field_name default must be '' (empty-string "
            f"sentinel for 'use the YAML default'); got {default!r}"
        )

    def test_omitted_field_name_flows_none_to_input(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_forward_breakeven_simple",
            return_value=_well_formed_forward_output(),
        ) as mock_fb:
            mcp_module.calculate_forward_breakeven_simple_tool(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                start_tenor="5Y",
                end_tenor="10Y",
                lookback_days=365,
                # field_name omitted → defaults to ""
            )
        params = mock_fb.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach ForwardBreakevenSimpleInput "
            f"as None (sentinel for 'use YAML default_field_name'), got "
            f"{params.field_name!r}"
        )

    def test_explicit_field_name_passes_through(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_forward_breakeven_simple",
            return_value=_well_formed_forward_output(),
        ) as mock_fb:
            mcp_module.calculate_forward_breakeven_simple_tool(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                start_tenor="5Y",
                end_tenor="10Y",
                field_name="YLD_YTM_BID",
            )
        params = mock_fb.call_args.kwargs["params"]
        assert params.field_name == "YLD_YTM_BID"

    def test_time_series_stripped_from_llm_response(self):
        """The MCP wrapper must NOT serialize the bespoke or canonical
        TimeSeries payloads back to the LLM — frontend REST surfaces
        get the full dict directly from the tool, but the LLM context
        shouldn't consume thousands of historical rows."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_forward_breakeven_simple",
            return_value=_well_formed_forward_output(),
        ):
            output_json = mcp_module.calculate_forward_breakeven_simple_tool(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                start_tenor="5Y",
                end_tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        assert "time_series" not in parsed
        assert "time_series_forward" not in parsed
        assert "time_series_zscore" not in parsed
        # methodology_label MUST survive the strip.
        assert "methodology_label" in parsed["current_metrics"]


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.inflation_indexed_bonds.tools.forward_breakeven_simple import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.forward_breakeven_simple.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_forward_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(FORWARD_BREAKEVEN_SIMPLE_CONFIG_PATH)
        assert cfg.tool.name == "calculate_forward_breakeven_simple_tool"
        assert cfg.tool.domain == "inflation_indexed_bonds"


# ===========================================================================
# Response-model validation
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        from rates_agent.inflation_indexed_bonds.tools.forward_breakeven_simple.schemas import (
            ForwardBreakevenSimpleOutput,
        )
        validated = ForwardBreakevenSimpleOutput.model_validate(
            _well_formed_forward_output(),
        )
        assert validated.time_series_forward.units.value == "bps"
        assert validated.time_series_zscore.units.value == "z_score"

    def test_response_model_requires_canonical_series(self):
        from rates_agent.inflation_indexed_bonds.tools.forward_breakeven_simple.schemas import (
            ForwardBreakevenSimpleOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_forward_output()
        bad.pop("time_series_forward")
        with pytest.raises(ValidationError):
            ForwardBreakevenSimpleOutput.model_validate(bad)

    def test_round_trip_preserves_canonical_fields(self):
        from rates_agent.inflation_indexed_bonds.tools.forward_breakeven_simple.schemas import (
            ForwardBreakevenSimpleOutput,
        )
        validated = ForwardBreakevenSimpleOutput.model_validate(
            _well_formed_forward_output(),
        )
        dumped = validated.model_dump()
        assert dumped["time_series_forward"]["units"] == "bps"
        assert dumped["time_series_zscore"]["units"] == "z_score"
        assert dumped["current_metrics"]["methodology_label"]
        assert dumped["current_metrics"]["start_years"] == 5.0
        assert dumped["current_metrics"]["end_years"] == 10.0


# ===========================================================================
# Workflow PrimitiveSpec registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_resolver_returns_forward_spec(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_forward_breakeven_simple_tool",
        )
        assert spec.tool_name == "calculate_forward_breakeven_simple_tool"
        assert spec.config_path == FORWARD_BREAKEVEN_SIMPLE_CONFIG_PATH

    def test_spec_uses_correct_input_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_indexed_bonds.tools.forward_breakeven_simple import (
            ForwardBreakevenSimpleInput,
            ForwardBreakevenSimpleOutput,
        )
        spec = rates_primitive_resolver(
            "calculate_forward_breakeven_simple_tool",
        )
        assert spec.input_class is ForwardBreakevenSimpleInput
        assert spec.output_class is ForwardBreakevenSimpleOutput

    def test_spec_uses_correct_callable(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_indexed_bonds.tools.forward_breakeven_simple import (
            calculate_forward_breakeven_simple,
        )
        spec = rates_primitive_resolver(
            "calculate_forward_breakeven_simple_tool",
        )
        assert spec.callable is calculate_forward_breakeven_simple

    def test_output_field_units_declared_honestly(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_forward_breakeven_simple_tool",
        )
        # Bespoke time_series row list carries forward_breakeven_bps
        # so it is in BPS units; canonical forward series is BPS;
        # canonical z-score series is Z_SCORE.
        assert spec.output_field_units == {
            "time_series": "bps",
            "time_series_forward": "bps",
            "time_series_zscore": "z_score",
        }

    def test_known_rates_primitives_includes_forward(self):
        from rates_agent.workflows import known_rates_primitives
        assert (
            "calculate_forward_breakeven_simple_tool"
            in known_rates_primitives()
        )


# ===========================================================================
# Controlled-error envelope surface
# ===========================================================================

class TestMcpControlledErrorSurface:
    """The MCP wrapper must surface controlled-error envelopes from
    the underlying tool.  Without this guarantee the LLM hitting the
    forward tool with curve-family pollution / cross-country pairs
    would receive a fabricated value or a stack trace.
    """

    def test_missing_endpoint_surfaces_error_envelope(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        controlled_error = {
            "error": (
                "Forward breakeven 5Y10Y could not be computed for "
                "UST vs USD_TIPS: the start-tenor endpoint (5Y) "
                "failed.  Inner error: No linker "
                "(instrument_type='inflation_linker') rows found "
                "for curve_family='USD_TIPS', tenor='5Y'."
            )
        }
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_forward_breakeven_simple",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_forward_breakeven_simple_tool(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                start_tenor="5Y",
                end_tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "start-tenor" in parsed["error"].lower()
        assert "5Y" in parsed["error"]

    def test_cross_country_pair_surfaces_same_country_error_envelope(self):
        """The MCP wrapper MUST surface the same-country invariant
        error envelope (inherited from the spot primitive's guard)
        as a clean ``{"error": ...}`` JSON payload — not a stack
        trace, not a partial snapshot.  Mirrors the
        breakeven_inflation_simple wiring test's cross-country probe
        but for the forward shape."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        same_country_error = {
            "error": (
                "Forward breakeven 5Y10Y could not be computed for "
                "DE_BUND vs EUR_FR_LINKER: the start-tenor endpoint "
                "(5Y) failed.  Inner error: Cross-country pair "
                "refused: nominal 'DE_BUND' is ('Germany', 'EUR') "
                "while linker 'EUR_FR_LINKER' is ('France', 'EUR').  "
                "This primitive is a *same-country* generic bond-"
                "implied breakeven inflation by construction..."
            )
        }
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_forward_breakeven_simple",
            return_value=same_country_error,
        ):
            output_json = mcp_module.calculate_forward_breakeven_simple_tool(
                nominal_curve_family="DE_BUND",
                linker_curve_family="EUR_FR_LINKER",
                start_tenor="5Y",
                end_tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert sorted(parsed.keys()) == ["error"]
        assert "current_metrics" not in parsed
        assert "DE_BUND" in parsed["error"]
        assert "EUR_FR_LINKER" in parsed["error"]
        assert "same-country" in parsed["error"].lower()

    def test_invalid_tenor_pair_surfaces_validation_error(self):
        """Inverted forward window (end_tenor before start_tenor)
        must hit the Pydantic validator and emerge as a controlled
        ``{"error": ...}`` envelope from the wrapper."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.calculate_forward_breakeven_simple_tool(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                start_tenor="10Y",
                end_tenor="5Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "Invalid parameters" in parsed["error"]
