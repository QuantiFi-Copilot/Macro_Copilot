"""
test_inflation_swap_curve_spread_wiring.py — Caller-wiring smoke
tests for the ZCIS curve-spread primitive.

Mirrors ``test_breakeven_curve_spread_wiring.py`` (the closest
sibling) scoped to inflation_swap_curve_spread's external callers:

  1. rates_agent/inflation_swaps/mcp_server.py
     ::calculate_inflation_swap_curve_spread_tool
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
    ``output_field_units`` declarations (BPS for time_series and
    time_series_spread, Z_SCORE for time_series_zscore).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.inflation_swaps.tools.inflation_swap_curve_spread import (
    CONFIG_PATH as INFLATION_SWAP_CURVE_SPREAD_CONFIG_PATH,
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


def _well_formed_curve_spread_output() -> dict:
    """Mock matches the wire shape: snapshot ``current_metrics``
    PLUS bespoke ``time_series`` row list PLUS the two canonical
    TimeSeries payloads.
    """
    return {
        "current_metrics": {
            "as_of_date": "2026-04-08",
            "curve_family": "USD_ZCIS",
            "short_tenor": "5Y",
            "long_tenor": "10Y",
            "spread_label": "USD_ZCIS 5s10s",
            "spread_bps": 25.00,
            "change_1d_bps": 1.50,
            "change_1w_bps": 4.00,
            "change_1m_bps": -2.00,
            "z_score_252d": 0.65,
            "high_252d_bps": 60.00,
            "low_252d_bps": -10.00,
            "percentile_252d": 50.0,
            "short_zcis_rate_pct": 2.4500,
            "long_zcis_rate_pct": 2.7000,
            "short_years": 5.0,
            "long_years": 10.0,
            "observation_count": 252,
            "inflation_index_family": "US_CPI_URBAN",
            "index_lag": "3M",
            "interpolation": "Daily",
            "underlying_index": "CPURNSA Index",
            "methodology_label": (
                "Same-curve ZCIS tenor spread; "
                "(long_zcis_pct - short_zcis_pct) * 100; "
                "USD_ZCIS 5s10s."
            ),
        },
        "time_series": [
            {"date": "2026-04-07", "spread_bps": 23.50, "z_score": 0.62},
            {"date": "2026-04-08", "spread_bps": 25.00, "z_score": 0.65},
        ],
        "time_series_spread": {
            "series_name": "usd_zcis_5y_10y_zcis_curve_spread",
            "units": "bps",
            "description": "Test ZCIS curve spread series.",
            "rows": [
                {"date": "2026-04-07", "value": 23.50},
                {"date": "2026-04-08", "value": 25.00},
            ],
        },
        "time_series_zscore": {
            "series_name": "usd_zcis_5y_10y_zcis_curve_spread_zscore",
            "units": "z_score",
            "description": "Test ZCIS curve spread z-score series.",
            "rows": [
                {"date": "2026-04-07", "value": 0.62},
                {"date": "2026-04-08", "value": 0.65},
            ],
        },
    }


def _assert_curve_spread_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_inflation_swap_curve_spread called without "
        "`config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "calculate_inflation_swap_curve_spread_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'calculate_inflation_swap_curve_spread_tool' (catches imports of the wrong "
        "tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_zcis_rate_field" in cfg.conventions


# ===========================================================================
# rates_agent/inflation_swaps/mcp_server.py
# ===========================================================================

class TestMcpInflationSwapCurveSpreadToolWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_inflation_swap_curve_spread",
            return_value=_well_formed_curve_spread_output(),
        ) as mock_iscs:
            output_json = mcp_module.calculate_inflation_swap_curve_spread_tool(
                curve_family="USD_ZCIS",
                short_tenor="5Y",
                long_tenor="10Y",
                lookback_days=365,
                field_name="PX_MID",
            )
        assert mock_iscs.call_count == 1
        _assert_curve_spread_config_passed(mock_iscs.call_args)
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
            mcp_module.calculate_inflation_swap_curve_spread_tool,
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
            "calculate_inflation_swap_curve_spread",
            return_value=_well_formed_curve_spread_output(),
        ) as mock_iscs:
            mcp_module.calculate_inflation_swap_curve_spread_tool(
                curve_family="USD_ZCIS",
                short_tenor="5Y",
                long_tenor="10Y",
                lookback_days=365,
                # field_name omitted → defaults to ""
            )
        params = mock_iscs.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach "
            f"InflationSwapCurveSpreadInput as None (sentinel for "
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
            "calculate_inflation_swap_curve_spread",
            return_value=_well_formed_curve_spread_output(),
        ) as mock_iscs:
            mcp_module.calculate_inflation_swap_curve_spread_tool(
                curve_family="USD_ZCIS",
                short_tenor="5Y",
                long_tenor="10Y",
                field_name="PX_BID",
            )
        params = mock_iscs.call_args.kwargs["params"]
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
            "calculate_inflation_swap_curve_spread",
            return_value=_well_formed_curve_spread_output(),
        ):
            output_json = mcp_module.calculate_inflation_swap_curve_spread_tool(
                curve_family="USD_ZCIS",
                short_tenor="5Y",
                long_tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        assert "time_series" not in parsed
        assert "time_series_spread" not in parsed
        assert "time_series_zscore" not in parsed
        # methodology_label MUST survive the strip.
        assert "methodology_label" in parsed["current_metrics"]
        # Reference metadata MUST survive — the desk needs it to
        # interpret the spread.
        assert (
            parsed["current_metrics"]["inflation_index_family"]
            == "US_CPI_URBAN"
        )


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_curve_spread import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.inflation_swaps.tools.inflation_swap_curve_spread.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_curve_spread_config(self):
        cfg = load_tool_config(INFLATION_SWAP_CURVE_SPREAD_CONFIG_PATH)
        assert cfg.tool.name == "calculate_inflation_swap_curve_spread_tool"
        assert cfg.tool.domain == "inflation_swaps"


# ===========================================================================
# Response-model validation
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_curve_spread.schemas import (
            InflationSwapCurveSpreadOutput,
        )
        validated = InflationSwapCurveSpreadOutput.model_validate(
            _well_formed_curve_spread_output(),
        )
        assert validated.time_series_spread.units.value == "bps"
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
        from rates_agent.inflation_swaps.tools.inflation_swap_curve_spread.schemas import (
            InflationSwapCurveSpreadOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_curve_spread_output()
        bad.pop("time_series_spread")
        with pytest.raises(ValidationError):
            InflationSwapCurveSpreadOutput.model_validate(bad)

    def test_response_model_requires_methodology_label(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_curve_spread.schemas import (
            InflationSwapCurveSpreadOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_curve_spread_output()
        bad["current_metrics"].pop("methodology_label")
        with pytest.raises(ValidationError):
            InflationSwapCurveSpreadOutput.model_validate(bad)

    def test_response_model_requires_reference_metadata(self):
        """inflation_index_family / index_lag / interpolation are
        load-bearing wire fields — making them optional would let a
        future regression silently drop the cross-curve
        comparability caveat from the wire.
        """
        from rates_agent.inflation_swaps.tools.inflation_swap_curve_spread.schemas import (
            InflationSwapCurveSpreadOutput,
        )
        from pydantic import ValidationError
        for required_field in (
            "inflation_index_family", "index_lag", "interpolation",
        ):
            bad = _well_formed_curve_spread_output()
            bad["current_metrics"].pop(required_field)
            with pytest.raises(ValidationError):
                InflationSwapCurveSpreadOutput.model_validate(bad)

    def test_round_trip_preserves_canonical_fields(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_curve_spread.schemas import (
            InflationSwapCurveSpreadOutput,
        )
        validated = InflationSwapCurveSpreadOutput.model_validate(
            _well_formed_curve_spread_output(),
        )
        dumped = validated.model_dump()
        assert dumped["time_series_spread"]["units"] == "bps"
        assert dumped["time_series_zscore"]["units"] == "z_score"
        assert dumped["current_metrics"]["methodology_label"]
        assert dumped["current_metrics"]["short_years"] == 5.0
        assert dumped["current_metrics"]["long_years"] == 10.0


# ===========================================================================
# Workflow PrimitiveSpec registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_resolver_returns_curve_spread_spec(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_inflation_swap_curve_spread_tool",
        )
        assert (
            spec.tool_name
            == "calculate_inflation_swap_curve_spread_tool"
        )
        assert spec.config_path == INFLATION_SWAP_CURVE_SPREAD_CONFIG_PATH

    def test_spec_uses_correct_input_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_swaps.tools.inflation_swap_curve_spread import (
            InflationSwapCurveSpreadInput,
            InflationSwapCurveSpreadOutput,
        )
        spec = rates_primitive_resolver(
            "calculate_inflation_swap_curve_spread_tool",
        )
        assert spec.input_class is InflationSwapCurveSpreadInput
        assert spec.output_class is InflationSwapCurveSpreadOutput

    def test_spec_uses_correct_callable(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_swaps.tools.inflation_swap_curve_spread import (
            calculate_inflation_swap_curve_spread,
        )
        spec = rates_primitive_resolver(
            "calculate_inflation_swap_curve_spread_tool",
        )
        assert spec.callable is calculate_inflation_swap_curve_spread

    def test_output_field_units_declared_honestly(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_inflation_swap_curve_spread_tool",
        )
        # Bespoke time_series row list carries spread_bps, so BPS;
        # canonical spread series is BPS; canonical z-score series
        # is Z_SCORE.
        assert spec.output_field_units == {
            "time_series": "bps",
            "time_series_spread": "bps",
            "time_series_zscore": "z_score",
        }

    def test_known_rates_primitives_includes_curve_spread(self):
        from rates_agent.workflows import known_rates_primitives
        assert (
            "calculate_inflation_swap_curve_spread_tool"
            in known_rates_primitives()
        )


# ===========================================================================
# Controlled-error envelope surface
# ===========================================================================

class TestMcpControlledErrorSurface:
    """The MCP wrapper must surface controlled-error envelopes from
    the underlying tool — without this the LLM hitting the curve-
    spread tool with a non-ZCIS curve_family / inverted tenor
    would receive a fabricated value or a stack trace.
    """

    def test_missing_endpoint_surfaces_error_envelope(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        controlled_error = {
            "error": (
                "ZCIS curve spread 5Y10Y could not be computed for "
                "USD_ZCIS: the short-tenor endpoint (5Y) failed.  "
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
            "calculate_inflation_swap_curve_spread",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_inflation_swap_curve_spread_tool(
                curve_family="USD_ZCIS",
                short_tenor="5Y",
                long_tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "short-tenor" in parsed["error"].lower()
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
                "ZCIS curve spread 5Y10Y could not be computed for "
                "UST: the short-tenor endpoint (5Y) failed.  Inner "
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
            "calculate_inflation_swap_curve_spread",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_inflation_swap_curve_spread_tool(
                curve_family="UST",
                short_tenor="5Y",
                long_tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "inflation_swap" in parsed["error"]
        assert "zero_coupon_breakeven" in parsed["error"]

    def test_invalid_tenor_pair_surfaces_validation_error(self):
        """Inverted curve (long_tenor before short_tenor) must hit
        the Pydantic validator and emerge as a controlled error
        envelope."""
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.calculate_inflation_swap_curve_spread_tool(
                curve_family="USD_ZCIS",
                short_tenor="10Y",
                long_tenor="5Y",
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
    breakeven_curve_spread / cross_country_breakeven_spread_simple.
    """

    def test_bundled_yaml_methodology_what_it_does_is_nonempty(self):
        cfg = load_tool_config(INFLATION_SWAP_CURVE_SPREAD_CONFIG_PATH)
        assert cfg.methodology.what_it_does.strip()
        # Pin the load-bearing detail: spread formula + same-curve
        # invariant + composition pattern.
        what = cfg.methodology.what_it_does.lower()
        assert "long_zcis_pct" in what
        assert "short_zcis_pct" in what
        assert "calculate_inflation_swap_rate_level" in what
