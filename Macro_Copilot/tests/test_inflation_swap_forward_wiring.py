"""
test_inflation_swap_forward_wiring.py — Caller-wiring smoke tests
for the ZCIS forward primitive.

Mirrors ``test_inflation_swap_curve_spread_wiring.py`` (the closest
sibling) scoped to inflation_swap_forward's external callers:

  1. rates_agent/inflation_swaps/mcp_server.py
     ::calculate_inflation_swap_forward_tool
  2. rates_agent/workflows/__init__.py PrimitiveSpec registration

Key load-bearing properties under test:
  - The MCP wrapper passes config explicitly (no auto-load fallback).
  - The MCP wrapper uses the empty-string / None-sentinel pattern
    so the YAML ``default_zcis_rate_field`` actually flows through.
  - The MCP wrapper surfaces the controlled error envelope for the
    no-row / non-ZCIS-curve / inverted-tenor cases.
  - The hub re-export and CONFIG_PATH public symbol are stable.
  - The wire-frozen + canonical TimeSeries fields survive a full
    round-trip through the response model.
  - The workflow registry exposes the primitive with the correct
    ``output_field_units`` declarations (PERCENT for the forward
    TimeSeries, Z_SCORE for the z-score TimeSeries).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.inflation_swaps.tools.inflation_swap_forward import (
    CONFIG_PATH as INFLATION_SWAP_FORWARD_CONFIG_PATH,
)
from shared.config import (
    ToolConfig,
    clear_tool_config_cache,
    load_tool_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_forward_output() -> dict:
    """Mock matches the wire shape: snapshot ``current_metrics``
    PLUS bespoke ``time_series`` row list PLUS the two canonical
    TimeSeries payloads (forward in PERCENT, z-score in Z_SCORE).
    """
    return {
        "current_metrics": {
            "as_of_date": "2026-04-08",
            "curve_family": "USD_ZCIS",
            "start_tenor": "5Y",
            "end_tenor": "10Y",
            "forward_window_label": "USD_ZCIS 5Y10Y",
            "forward_zcis_pct": 2.7500,
            "forward_zcis_bps": 275.00,
            "change_1d_bps": 1.50,
            "change_1w_bps": 4.00,
            "change_1m_bps": -2.00,
            "z_score_252d": 0.65,
            "high_252d_bps": 320.00,
            "low_252d_bps": 240.00,
            "percentile_252d": 50.0,
            "start_zcis_pct": 2.4500,
            "end_zcis_pct": 2.7000,
            "start_years": 5.0,
            "end_years": 10.0,
            "observation_count": 252,
            "inflation_index_family": "US_CPI_URBAN",
            "index_lag": "3M",
            "interpolation": "Daily",
            "underlying_index": "CPURNSA Index",
            "methodology_label": (
                "Same-curve ZCIS forward via dual-compounding "
                "geometric formula on (1 + r_long)^T_long / "
                "(1 + r_short)^T_short; USD_ZCIS 5Y5Y."
            ),
        },
        "time_series": [
            {
                "date": "2026-04-07",
                "forward_zcis_pct": 2.7350,
                "forward_zcis_bps": 273.50,
                "z_score": 0.62,
            },
            {
                "date": "2026-04-08",
                "forward_zcis_pct": 2.7500,
                "forward_zcis_bps": 275.00,
                "z_score": 0.65,
            },
        ],
        "time_series_forward": {
            "series_name": "usd_zcis_5y_10y_zcis_forward",
            "units": "percent",
            "description": "Test ZCIS forward series.",
            "rows": [
                {"date": "2026-04-07", "value": 2.7350},
                {"date": "2026-04-08", "value": 2.7500},
            ],
        },
        "time_series_zscore": {
            "series_name": "usd_zcis_5y_10y_zcis_forward_zscore",
            "units": "z_score",
            "description": "Test ZCIS forward z-score series.",
            "rows": [
                {"date": "2026-04-07", "value": 0.62},
                {"date": "2026-04-08", "value": 0.65},
            ],
        },
    }


def _assert_forward_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_inflation_swap_forward called without "
        "`config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "inflation_swap_forward", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'inflation_swap_forward' (catches imports of the wrong "
        "tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_zcis_rate_field" in cfg.conventions
    assert "zcis_forward_compounding_mode" in cfg.conventions


# ===========================================================================
# rates_agent/inflation_swaps/mcp_server.py
# ===========================================================================

class TestMcpInflationSwapForwardToolWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_inflation_swap_forward",
            return_value=_well_formed_forward_output(),
        ) as mock_isf:
            output_json = mcp_module.calculate_inflation_swap_forward_tool(
                curve_family="USD_ZCIS",
                start_tenor="5Y",
                end_tenor="10Y",
                lookback_days=365,
                field_name="PX_MID",
            )
        assert mock_isf.call_count == 1
        _assert_forward_config_passed(mock_isf.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_field_name_default_is_empty_string_sentinel(self):
        """The MCP wrapper's field_name default must be the empty-
        string sentinel, NOT 'PX_MID'.  Hardcoding any other string
        here shadows the YAML's default_zcis_rate_field — same
        shadowing pattern fixed for sovereign curve_move_classifier
        in commit b2605ee."""
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(
            mcp_module.calculate_inflation_swap_forward_tool,
        )
        default = sig.parameters["field_name"].default
        assert default == "", (
            f"MCP wrapper's field_name default must be '' (empty-"
            f"string sentinel for 'use the YAML default'); got "
            f"{default!r}"
        )

    def test_omitted_field_name_flows_none_to_input(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_inflation_swap_forward",
            return_value=_well_formed_forward_output(),
        ) as mock_isf:
            mcp_module.calculate_inflation_swap_forward_tool(
                curve_family="USD_ZCIS",
                start_tenor="5Y",
                end_tenor="10Y",
                lookback_days=365,
                # field_name omitted → defaults to ""
            )
        params = mock_isf.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach "
            f"InflationSwapForwardInput as None (sentinel for "
            f"'use YAML default_zcis_rate_field'), got "
            f"{params.field_name!r}"
        )

    def test_explicit_field_name_passes_through(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_inflation_swap_forward",
            return_value=_well_formed_forward_output(),
        ) as mock_isf:
            mcp_module.calculate_inflation_swap_forward_tool(
                curve_family="USD_ZCIS",
                start_tenor="5Y",
                end_tenor="10Y",
                field_name="PX_BID",
            )
        params = mock_isf.call_args.kwargs["params"]
        assert params.field_name == "PX_BID"

    def test_time_series_stripped_from_llm_response(self):
        """The MCP wrapper must NOT serialize the bespoke or canonical
        TimeSeries payloads back to the LLM."""
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_inflation_swap_forward",
            return_value=_well_formed_forward_output(),
        ):
            output_json = mcp_module.calculate_inflation_swap_forward_tool(
                curve_family="USD_ZCIS",
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
        # Reference metadata MUST survive — the desk needs it to
        # interpret the forward.
        assert (
            parsed["current_metrics"]["inflation_index_family"]
            == "US_CPI_URBAN"
        )


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_forward import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.inflation_swaps.tools.inflation_swap_forward.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_forward_config(self):
        cfg = load_tool_config(INFLATION_SWAP_FORWARD_CONFIG_PATH)
        assert cfg.tool.name == "inflation_swap_forward"
        assert cfg.tool.domain == "inflation_swaps"


# ===========================================================================
# Response-model validation
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_forward.schemas import (
            InflationSwapForwardOutput,
        )
        validated = InflationSwapForwardOutput.model_validate(
            _well_formed_forward_output(),
        )
        assert validated.time_series_forward.units.value == "percent"
        assert validated.time_series_zscore.units.value == "z_score"
        # Reference metadata threads through validation cleanly.
        assert (
            validated.current_metrics.inflation_index_family
            == "US_CPI_URBAN"
        )
        assert validated.current_metrics.index_lag == "3M"
        assert validated.current_metrics.interpolation == "Daily"
        assert (
            validated.current_metrics.underlying_index
            == "CPURNSA Index"
        )

    def test_response_model_requires_canonical_series(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_forward.schemas import (
            InflationSwapForwardOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_forward_output()
        bad.pop("time_series_forward")
        with pytest.raises(ValidationError):
            InflationSwapForwardOutput.model_validate(bad)

    def test_response_model_requires_methodology_label(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_forward.schemas import (
            InflationSwapForwardOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_forward_output()
        bad["current_metrics"].pop("methodology_label")
        with pytest.raises(ValidationError):
            InflationSwapForwardOutput.model_validate(bad)

    def test_response_model_requires_reference_metadata(self):
        """inflation_index_family / index_lag / interpolation are
        load-bearing wire fields — making them optional would let a
        future regression silently drop the cross-curve
        comparability caveat from the wire.
        """
        from rates_agent.inflation_swaps.tools.inflation_swap_forward.schemas import (
            InflationSwapForwardOutput,
        )
        from pydantic import ValidationError
        for required_field in (
            "inflation_index_family", "index_lag", "interpolation",
        ):
            bad = _well_formed_forward_output()
            bad["current_metrics"].pop(required_field)
            with pytest.raises(ValidationError):
                InflationSwapForwardOutput.model_validate(bad)

    def test_round_trip_preserves_canonical_fields(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_forward.schemas import (
            InflationSwapForwardOutput,
        )
        validated = InflationSwapForwardOutput.model_validate(
            _well_formed_forward_output(),
        )
        dumped = validated.model_dump()
        assert dumped["time_series_forward"]["units"] == "percent"
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
            "calculate_inflation_swap_forward_tool",
        )
        assert (
            spec.tool_name
            == "calculate_inflation_swap_forward_tool"
        )
        assert spec.config_path == INFLATION_SWAP_FORWARD_CONFIG_PATH

    def test_spec_uses_correct_input_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_swaps.tools.inflation_swap_forward import (
            InflationSwapForwardInput,
            InflationSwapForwardOutput,
        )
        spec = rates_primitive_resolver(
            "calculate_inflation_swap_forward_tool",
        )
        assert spec.input_class is InflationSwapForwardInput
        assert spec.output_class is InflationSwapForwardOutput

    def test_spec_uses_correct_callable(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_swaps.tools.inflation_swap_forward import (
            calculate_inflation_swap_forward,
        )
        spec = rates_primitive_resolver(
            "calculate_inflation_swap_forward_tool",
        )
        assert spec.callable is calculate_inflation_swap_forward

    def test_output_field_units_declared_honestly(self):
        """Forward ZCIS is a *level*, not a spread.  The canonical
        time_series_forward ships in PERCENT (mirrors OIS
        forward_rate); the bespoke time_series row list also
        carries the percent column as primary; the rolling z-score
        canonical TimeSeries is in Z_SCORE units.  Declare those
        honestly so operator-layer unit-compat checks key off the
        right unit.
        """
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_inflation_swap_forward_tool",
        )
        assert spec.output_field_units == {
            "time_series": "percent",
            "time_series_forward": "percent",
            "time_series_zscore": "z_score",
        }

    def test_known_rates_primitives_includes_forward(self):
        from rates_agent.workflows import known_rates_primitives
        assert (
            "calculate_inflation_swap_forward_tool"
            in known_rates_primitives()
        )


# ===========================================================================
# Controlled-error envelope surface
# ===========================================================================

class TestMcpControlledErrorSurface:
    """The MCP wrapper must surface controlled-error envelopes from
    the underlying tool — without this the LLM hitting the forward
    tool with a non-ZCIS curve_family / inverted tenor would
    receive a fabricated value or a stack trace.
    """

    def test_missing_endpoint_surfaces_error_envelope(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        controlled_error = {
            "error": (
                "ZCIS forward 5Y10Y could not be computed for "
                "USD_ZCIS: the start-tenor endpoint (5Y) failed.  "
                "Inner error: No ZCIS data found for "
                "curve_family='USD_ZCIS', tenor='5Y' "
                "(instrument_type='inflation_swap', "
                "pricing_type='zero_coupon_breakeven')."
            )
        }
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_inflation_swap_forward",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_inflation_swap_forward_tool(
                curve_family="USD_ZCIS",
                start_tenor="5Y",
                end_tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "start-tenor" in parsed["error"].lower()
        assert "5Y" in parsed["error"]
        # Inner-error rationale must travel back to the LLM so it
        # can route non-ZCIS questions elsewhere.
        assert "inflation_swap" in parsed["error"]

    def test_non_zcis_curve_family_surfaces_controlled_error_envelope(
        self,
    ):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        controlled_error = {
            "error": (
                "ZCIS forward 5Y10Y could not be computed for "
                "UST: the start-tenor endpoint (5Y) failed.  Inner "
                "error: No ZCIS data found for curve_family='UST', "
                "tenor='5Y' (instrument_type='inflation_swap', "
                "pricing_type='zero_coupon_breakeven')."
            )
        }
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_inflation_swap_forward",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_inflation_swap_forward_tool(
                curve_family="UST",
                start_tenor="5Y",
                end_tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "inflation_swap" in parsed["error"]
        assert "zero_coupon_breakeven" in parsed["error"]

    def test_invalid_tenor_pair_surfaces_validation_error(self):
        """Inverted forward window (end_tenor before start_tenor)
        must hit the Pydantic validator and emerge as a controlled
        error envelope."""
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.calculate_inflation_swap_forward_tool(
                curve_family="USD_ZCIS",
                start_tenor="10Y",
                end_tenor="5Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "Invalid parameters" in parsed["error"]


# ===========================================================================
# Methodology label sourced from YAML (not hardcoded)
# ===========================================================================

class TestMethodologyLabelSourcedFromYaml:
    """The methodology_label that reaches current_metrics must be
    sourced from the YAML's ``methodology.what_it_does`` — NOT a
    hardcoded Python literal.  Same threading pattern as
    forward_breakeven_simple / inflation_swap_curve_spread.
    """

    def test_bundled_yaml_methodology_what_it_does_is_nonempty(self):
        cfg = load_tool_config(INFLATION_SWAP_FORWARD_CONFIG_PATH)
        assert cfg.methodology.what_it_does.strip()
        # Pin the load-bearing detail: dual-compounding geometric
        # formula + same-curve invariant + composition pattern.
        what = cfg.methodology.what_it_does.lower()
        assert "calculate_inflation_swap_rate_level" in what
        assert "geometric" in what or "(1 +" in what
