"""
test_real_yield_curve_spread_wiring.py — Caller-wiring smoke tests
for the linker real_yield_curve_spread primitive.

Mirrors ``test_breakeven_curve_spread_wiring.py`` and
``test_inflation_swap_curve_spread_wiring.py`` (the closest
sibling composition patterns) scoped to
real_yield_curve_spread's external callers:

  1. rates_agent/inflation_indexed_bonds/mcp_server.py
     ::calculate_real_yield_curve_spread_tool
  2. rates_agent/workflows/__init__.py PrimitiveSpec registration

Key load-bearing properties under test:
  - The MCP wrapper passes config explicitly (no auto-load fallback).
  - The MCP wrapper uses the empty-string / None-sentinel pattern
    so the YAML ``default_field_name`` actually flows through.
  - The MCP wrapper surfaces the controlled error envelope for the
    no-row / non-linker-curve / inverted-tenor cases.
  - The hub re-export and CONFIG_PATH public symbol are stable.
  - The wire-frozen + canonical TimeSeries fields survive a full
    round-trip through the response model.
  - The workflow registry exposes the primitive with the correct
    ``output_field_units`` declarations (PERCENT for time_series
    and time_series_spread, Z_SCORE for time_series_zscore).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread import (
    CONFIG_PATH as REAL_YIELD_CURVE_SPREAD_CONFIG_PATH,
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
            "curve_family": "USD_TIPS",
            "short_tenor": "5Y",
            "long_tenor": "10Y",
            "spread_label": "USD_TIPS 5s10s real-yield",
            "current_spread_pct": 0.2500,
            "daily_change_bps": 1.50,
            "weekly_change_bps": 4.00,
            "monthly_change_bps": -2.00,
            "current_z_score": 0.65,
            "rolling_window_days": 252,
            "high_252d_pct": 0.6000,
            "low_252d_pct": -0.1000,
            "percentile_252d": 50.0,
            "short_real_yield_pct": 1.8500,
            "long_real_yield_pct": 2.1000,
            "short_years": 5.0,
            "long_years": 10.0,
            "observation_count": 252,
            "country": "US",
            "currency": "USD",
            "methodology_label": (
                "Same-country linker real-yield curve spread; "
                "long_real_yield_pct - short_real_yield_pct; "
                "USD_TIPS 5s10s real-yield."
            ),
        },
        "time_series": [
            {"date": "2026-04-07", "spread_pct": 0.2350, "z_score": 0.62},
            {"date": "2026-04-08", "spread_pct": 0.2500, "z_score": 0.65},
        ],
        "time_series_spread": {
            "series_name": "usd_tips_5y_10y_real_yield_curve_spread",
            "units": "percent",
            "description": "Test real-yield curve spread series.",
            "rows": [
                {"date": "2026-04-07", "value": 0.2350},
                {"date": "2026-04-08", "value": 0.2500},
            ],
        },
        "time_series_zscore": {
            "series_name": (
                "usd_tips_5y_10y_real_yield_curve_spread_zscore"
            ),
            "units": "z_score",
            "description": "Test real-yield curve spread z-score series.",
            "rows": [
                {"date": "2026-04-07", "value": 0.62},
                {"date": "2026-04-08", "value": 0.65},
            ],
        },
    }


def _assert_curve_spread_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_real_yield_curve_spread called without "
        "`config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "calculate_real_yield_curve_spread_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, "
        "expected 'calculate_real_yield_curve_spread_tool' "
        "(catches imports of the wrong tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_field_name" in cfg.conventions


# ===========================================================================
# rates_agent/inflation_indexed_bonds/mcp_server.py
# ===========================================================================

class TestMcpRealYieldCurveSpreadToolWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_real_yield_curve_spread",
            return_value=_well_formed_curve_spread_output(),
        ) as mock_rycs:
            output_json = mcp_module.calculate_real_yield_curve_spread_tool(
                curve_family="USD_TIPS",
                short_tenor="5Y",
                long_tenor="10Y",
                lookback_days=365,
                field_name="YLD_YTM_MID",
            )
        assert mock_rycs.call_count == 1
        _assert_curve_spread_config_passed(mock_rycs.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_field_name_default_is_empty_string_sentinel(self):
        """The MCP wrapper's field_name default must be the empty-
        string sentinel, NOT 'YLD_YTM_MID'.  Hardcoding any other
        string here shadows the YAML's default_field_name — same
        shadowing pattern fixed for sovereign curve_move_classifier
        in commit b2605ee."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(
            mcp_module.calculate_real_yield_curve_spread_tool,
        )
        default = sig.parameters["field_name"].default
        assert default == "", (
            f"MCP wrapper's field_name default must be '' (empty-"
            f"string sentinel for 'use the YAML default'); got "
            f"{default!r}"
        )

    def test_omitted_field_name_flows_none_to_input(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_real_yield_curve_spread",
            return_value=_well_formed_curve_spread_output(),
        ) as mock_rycs:
            mcp_module.calculate_real_yield_curve_spread_tool(
                curve_family="USD_TIPS",
                short_tenor="5Y",
                long_tenor="10Y",
                lookback_days=365,
                # field_name omitted → defaults to ""
            )
        params = mock_rycs.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach "
            f"RealYieldCurveSpreadInput as None (sentinel for "
            f"'use YAML default_field_name'), got "
            f"{params.field_name!r}"
        )

    def test_explicit_field_name_passes_through(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_real_yield_curve_spread",
            return_value=_well_formed_curve_spread_output(),
        ) as mock_rycs:
            mcp_module.calculate_real_yield_curve_spread_tool(
                curve_family="USD_TIPS",
                short_tenor="5Y",
                long_tenor="10Y",
                field_name="YLD_YTM_BID",
            )
        params = mock_rycs.call_args.kwargs["params"]
        assert params.field_name == "YLD_YTM_BID"

    def test_time_series_stripped_from_llm_response(self):
        """The MCP wrapper must NOT serialize the bespoke or
        canonical TimeSeries payloads back to the LLM."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_real_yield_curve_spread",
            return_value=_well_formed_curve_spread_output(),
        ):
            output_json = mcp_module.calculate_real_yield_curve_spread_tool(
                curve_family="USD_TIPS",
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
        # Reference metadata MUST survive — desk needs it to
        # confirm the resolved linker identity.
        assert parsed["current_metrics"]["country"] == "US"
        assert parsed["current_metrics"]["currency"] == "USD"


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_curve_spread_config(self):
        cfg = load_tool_config(REAL_YIELD_CURVE_SPREAD_CONFIG_PATH)
        assert cfg.tool.name == "calculate_real_yield_curve_spread_tool"
        assert cfg.tool.domain == "inflation_indexed_bonds"


# ===========================================================================
# Response-model validation
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread.schemas import (
            RealYieldCurveSpreadOutput,
        )
        validated = RealYieldCurveSpreadOutput.model_validate(
            _well_formed_curve_spread_output(),
        )
        # The spread is in PERCENT — distinct from the BPS
        # convention sibling curve-spread primitives use.
        assert validated.time_series_spread.units.value == "percent"
        assert validated.time_series_zscore.units.value == "z_score"
        # Reference metadata threads through validation cleanly.
        assert validated.current_metrics.country == "US"
        assert validated.current_metrics.currency == "USD"

    def test_response_model_requires_canonical_series(self):
        from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread.schemas import (
            RealYieldCurveSpreadOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_curve_spread_output()
        bad.pop("time_series_spread")
        with pytest.raises(ValidationError):
            RealYieldCurveSpreadOutput.model_validate(bad)

    def test_response_model_requires_methodology_label(self):
        from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread.schemas import (
            RealYieldCurveSpreadOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_curve_spread_output()
        bad["current_metrics"].pop("methodology_label")
        with pytest.raises(ValidationError):
            RealYieldCurveSpreadOutput.model_validate(bad)

    def test_response_model_requires_country_and_currency(self):
        """``country`` / ``currency`` are load-bearing wire fields —
        making them optional would let a future regression silently
        drop the resolved linker identity from the wire."""
        from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread.schemas import (
            RealYieldCurveSpreadOutput,
        )
        from pydantic import ValidationError
        for required_field in ("country", "currency"):
            bad = _well_formed_curve_spread_output()
            bad["current_metrics"].pop(required_field)
            with pytest.raises(ValidationError):
                RealYieldCurveSpreadOutput.model_validate(bad)

    def test_round_trip_preserves_canonical_fields(self):
        from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread.schemas import (
            RealYieldCurveSpreadOutput,
        )
        validated = RealYieldCurveSpreadOutput.model_validate(
            _well_formed_curve_spread_output(),
        )
        dumped = validated.model_dump()
        assert dumped["time_series_spread"]["units"] == "percent"
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
            "calculate_real_yield_curve_spread_tool",
        )
        assert (
            spec.tool_name
            == "calculate_real_yield_curve_spread_tool"
        )
        assert (
            spec.config_path
            == REAL_YIELD_CURVE_SPREAD_CONFIG_PATH
        )

    def test_spec_uses_correct_input_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread import (
            RealYieldCurveSpreadInput,
            RealYieldCurveSpreadOutput,
        )
        spec = rates_primitive_resolver(
            "calculate_real_yield_curve_spread_tool",
        )
        assert spec.input_class is RealYieldCurveSpreadInput
        assert spec.output_class is RealYieldCurveSpreadOutput

    def test_spec_uses_correct_callable(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread import (
            calculate_real_yield_curve_spread,
        )
        spec = rates_primitive_resolver(
            "calculate_real_yield_curve_spread_tool",
        )
        assert spec.callable is calculate_real_yield_curve_spread

    def test_output_field_units_declared_honestly(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_real_yield_curve_spread_tool",
        )
        # Bespoke time_series row list carries spread_pct, so
        # PERCENT; canonical spread series is PERCENT (NOT bps —
        # real yields are in PERCENT and not multiplied by 100);
        # canonical z-score series is Z_SCORE.
        assert spec.output_field_units == {
            "time_series": "percent",
            "time_series_spread": "percent",
            "time_series_zscore": "z_score",
        }

    def test_known_rates_primitives_includes_real_yield_curve_spread(self):
        from rates_agent.workflows import known_rates_primitives
        assert (
            "calculate_real_yield_curve_spread_tool"
            in known_rates_primitives()
        )


# ===========================================================================
# Controlled-error envelope surface
# ===========================================================================

class TestMcpControlledErrorSurface:
    """The MCP wrapper must surface controlled-error envelopes from
    the underlying tool — without this the LLM hitting the curve-
    spread tool with a non-linker curve_family / inverted tenor
    would receive a fabricated value or a stack trace.
    """

    def test_missing_endpoint_surfaces_error_envelope(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        controlled_error = {
            "error": (
                "Real-yield curve spread 5Y10Y could not be "
                "computed for USD_TIPS: the short-tenor endpoint "
                "(5Y) failed.  Inner error: No linker real-yield "
                "data found for curve_family='USD_TIPS', "
                "tenor='5Y' (instrument_type='inflation_linker')."
            )
        }
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_real_yield_curve_spread",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_real_yield_curve_spread_tool(
                curve_family="USD_TIPS",
                short_tenor="5Y",
                long_tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "short-tenor" in parsed["error"].lower()
        assert "5Y" in parsed["error"]
        # Inner-error rationale must travel back to the LLM so it
        # can route non-linker questions elsewhere.
        assert "inflation_linker" in parsed["error"]

    def test_non_linker_curve_family_surfaces_controlled_error_envelope(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        controlled_error = {
            "error": (
                "Could not resolve country/currency for the linker "
                "curve_family='UST' "
                "(instrument_type='inflation_linker').  No "
                "instrument_master rows found for "
                "curve_family='UST', "
                "instrument_type='inflation_linker'.  This tool's "
                "same-country / same-curve-family invariant cannot "
                "be evaluated without the (country, currency) "
                "identity for the curve.  If you passed a nominal "
                "sovereign curve_family (e.g. 'UST', 'DE_BUND'), "
                "use the sovereign_bonds calculate_curve_spread_tool "
                "instead — this tool only operates on inflation-"
                "linker rows."
            )
        }
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_real_yield_curve_spread",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_real_yield_curve_spread_tool(
                curve_family="UST",
                short_tenor="2Y",
                long_tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "inflation_linker" in parsed["error"]
        # Routing hint to sovereign tool must survive.
        assert "sovereign" in parsed["error"].lower()

    def test_invalid_tenor_pair_surfaces_validation_error(self):
        """Inverted curve (long_tenor before short_tenor) must hit
        the Pydantic validator and emerge as a controlled error
        envelope."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.calculate_real_yield_curve_spread_tool(
                curve_family="USD_TIPS",
                short_tenor="10Y",
                long_tenor="5Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_equal_tenor_surfaces_validation_error(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.calculate_real_yield_curve_spread_tool(
                curve_family="USD_TIPS",
                short_tenor="10Y",
                long_tenor="10Y",
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
    breakeven_curve_spread / inflation_swap_curve_spread.
    """

    def test_bundled_yaml_methodology_what_it_does_is_nonempty(self):
        cfg = load_tool_config(REAL_YIELD_CURVE_SPREAD_CONFIG_PATH)
        assert cfg.methodology.what_it_does.strip()
        # Pin the load-bearing detail: spread formula + composition
        # pattern.
        what = cfg.methodology.what_it_does.lower()
        assert "long_real_yield_pct" in what
        assert "short_real_yield_pct" in what
        assert "get_real_yield_level" in what
