"""
test_inflation_swap_rate_level_wiring.py — Caller-wiring smoke tests
for the ZCIS rate-level primitive.

Mirrors ``test_real_yield_level_wiring.py`` (the structural linker
analog) and ``test_ois_rate_level_wiring.py`` (the OIS analog), scoped
to the inflation_swap_rate_level primitive's external callers.  Today
there are exactly two:

  1. rates_agent/inflation_swaps/mcp_server.py
     ::calculate_inflation_swap_rate_level_tool
  2. rates_agent/workflows/__init__.py PrimitiveSpec registration

Future REST endpoints + batch surfaces will be added behind the same
``CONFIG_PATH`` import.

Key load-bearing properties under test:
  - The MCP wrapper passes config explicitly (no auto-load fallback).
    ``config=`` argument is observable at the callsite.
  - The MCP wrapper uses the empty-string / None-sentinel pattern so
    the YAML ``default_zcis_rate_field`` actually flows through.
  - The hub re-export and CONFIG_PATH public symbol are stable.
  - The wire-frozen + canonical TimeSeries fields survive a full
    round-trip through the response model.
  - The workflow registry exposes the primitive with the correct
    ``output_field_units`` declaration (``percent``).
  - The orchestrator-domains test suite already covers the new
    ``inflation_swaps`` Domain entry — see
    ``tests/test_orchestrator_domains.py`` for the additional
    ``Domain.INFLATION_SWAPS`` enumeration coverage.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.inflation_swaps.tools.inflation_swap_rate_level import (
    CONFIG_PATH as INFLATION_SWAP_RATE_LEVEL_CONFIG_PATH,
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


def _well_formed_zcis_output() -> dict:
    """Mock matches the wire shape: snapshot ``current_metrics`` PLUS
    the canonical ``shared.schemas.time_series.TimeSeries`` payload."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-09",
            "curve_family": "USD_ZCIS",
            "tenor": "5Y",
            "zcis_rate_pct": 2.45,
            "daily_change_bps": 1.5,
            "weekly_change_bps": 8.0,
            "monthly_change_bps": -3.0,
            "z_score": 0.85,
            "high_252d_pct": 2.85,
            "low_252d_pct": 2.10,
            "percentile_252d": 47.0,
            "observation_count": 252,
            "inflation_index_family": "US_CPI_URBAN",
            "index_lag": "3M",
            "interpolation": "Daily",
            "underlying_index": "CPURNSA Index",
            "methodology_label": "Test ZCIS methodology label.",
        },
        "time_series": {
            "series_name": "usd_zcis_5y_zcis_rate",
            "units": "percent",
            "description": (
                "Test ZCIS series; "
                "inflation_index_family='US_CPI_URBAN', "
                "index_lag='3M', interpolation='Daily'."
            ),
            "rows": [
                {"date": "2026-04-08", "value": 2.4400},
                {"date": "2026-04-09", "value": 2.4500},
            ],
        },
    }


def _assert_zcis_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_inflation_swap_rate_level called without `config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "inflation_swap_rate_level", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'inflation_swap_rate_level' (catches imports of the wrong "
        "tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_zcis_rate_field" in cfg.conventions


# ===========================================================================
# rates_agent/inflation_swaps/mcp_server.py — calculate_inflation_swap_rate_level_tool
# ===========================================================================

class TestMcpInflationSwapRateLevelToolWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_inflation_swap_rate_level",
            return_value=_well_formed_zcis_output(),
        ) as mock_isrl:
            output_json = mcp_module.calculate_inflation_swap_rate_level_tool(
                curve_family="USD_ZCIS",
                tenor="5Y",
                lookback_days=365,
                field_name="PX_MID",
            )
        assert mock_isrl.call_count == 1
        _assert_zcis_config_passed(mock_isrl.call_args)
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
            mcp_module.calculate_inflation_swap_rate_level_tool,
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
            "calculate_inflation_swap_rate_level",
            return_value=_well_formed_zcis_output(),
        ) as mock_isrl:
            mcp_module.calculate_inflation_swap_rate_level_tool(
                curve_family="USD_ZCIS",
                tenor="5Y",
                lookback_days=365,
                # field_name omitted → defaults to ""
            )
        params = mock_isrl.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach "
            f"InflationSwapRateLevelInput as None (sentinel for 'use "
            f"YAML default_zcis_rate_field'), got "
            f"{params.field_name!r}"
        )

    def test_explicit_field_name_passes_through(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_inflation_swap_rate_level",
            return_value=_well_formed_zcis_output(),
        ) as mock_isrl:
            mcp_module.calculate_inflation_swap_rate_level_tool(
                curve_family="USD_ZCIS",
                tenor="5Y",
                field_name="PX_BID",
            )
        params = mock_isrl.call_args.kwargs["params"]
        assert params.field_name == "PX_BID"

    def test_time_series_stripped_from_llm_response(self):
        """The MCP wrapper must NOT serialize the canonical TimeSeries
        payload back to the LLM — frontend REST surfaces get the full
        dict directly from the tool, but the LLM context shouldn't
        consume thousands of historical rows just to answer 'where's
        USD 5Y ZCIS?'.  Mirrors sibling rate-level tools."""
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_inflation_swap_rate_level",
            return_value=_well_formed_zcis_output(),
        ):
            output_json = mcp_module.calculate_inflation_swap_rate_level_tool(
                curve_family="USD_ZCIS",
                tenor="5Y",
            )
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        assert "time_series" not in parsed, (
            "calculate_inflation_swap_rate_level_tool must strip the "
            "canonical time_series before returning to the LLM"
        )

    def test_methodology_label_survives_to_llm_response(self):
        """The methodology_label is the wire-honesty disclosure — it
        MUST reach the LLM context.  The time_series is stripped, but
        current_metrics including methodology_label must travel
        intact."""
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_inflation_swap_rate_level",
            return_value=_well_formed_zcis_output(),
        ):
            output_json = mcp_module.calculate_inflation_swap_rate_level_tool(
                curve_family="USD_ZCIS",
                tenor="5Y",
            )
        parsed = json.loads(output_json)
        assert "methodology_label" in parsed["current_metrics"]
        assert (
            parsed["current_metrics"]["inflation_index_family"]
            == "US_CPI_URBAN"
        )


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_rate_level import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_inflation_swap_rate_level_config(self):
        cfg = load_tool_config(INFLATION_SWAP_RATE_LEVEL_CONFIG_PATH)
        assert cfg.tool.name == "inflation_swap_rate_level"
        assert cfg.tool.domain == "inflation_swaps"


# ===========================================================================
# Response-model validation
# ===========================================================================

class TestResponseModelValidation:
    """Pin the wire contract for inflation_swap_rate_level.  Mirrors
    the response-model validation pattern added in PR #60 to the
    sovereign wiring suites — guards against the canonical TimeSeries
    shape silently regressing into a bespoke dict.
    """

    def test_mock_validates_against_response_model(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_rate_level.schemas import (
            InflationSwapRateLevelOutput,
        )
        validated = InflationSwapRateLevelOutput.model_validate(
            _well_formed_zcis_output(),
        )
        assert validated.time_series is not None
        assert validated.time_series.units.value == "percent"
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

    def test_response_model_requires_time_series_field(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_rate_level.schemas import (
            InflationSwapRateLevelOutput,
        )
        from pydantic import ValidationError
        bad_mock = _well_formed_zcis_output()
        bad_mock.pop("time_series")
        with pytest.raises(ValidationError):
            InflationSwapRateLevelOutput.model_validate(bad_mock)

    def test_response_model_requires_methodology_label(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_rate_level.schemas import (
            InflationSwapRateLevelOutput,
        )
        from pydantic import ValidationError
        bad_mock = _well_formed_zcis_output()
        bad_mock["current_metrics"].pop("methodology_label")
        with pytest.raises(ValidationError):
            InflationSwapRateLevelOutput.model_validate(bad_mock)

    def test_response_model_requires_reference_metadata(self):
        """inflation_index_family / index_lag / interpolation are
        load-bearing wire fields — making them optional would let a
        future regression silently drop the cross-curve comparability
        caveat from the wire."""
        from rates_agent.inflation_swaps.tools.inflation_swap_rate_level.schemas import (
            InflationSwapRateLevelOutput,
        )
        from pydantic import ValidationError
        for required_field in (
            "inflation_index_family", "index_lag", "interpolation",
        ):
            bad_mock = _well_formed_zcis_output()
            bad_mock["current_metrics"].pop(required_field)
            with pytest.raises(ValidationError):
                InflationSwapRateLevelOutput.model_validate(bad_mock)

    def test_round_trip_preserves_canonical_fields(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_rate_level.schemas import (
            InflationSwapRateLevelOutput,
        )
        validated = InflationSwapRateLevelOutput.model_validate(
            _well_formed_zcis_output(),
        )
        dumped = validated.model_dump()
        assert "current_metrics" in dumped
        assert "time_series" in dumped
        assert dumped["time_series"]["units"] == "percent"
        assert (
            dumped["time_series"]["series_name"]
            == "usd_zcis_5y_zcis_rate"
        )
        assert "methodology_label" in dumped["current_metrics"]
        assert (
            dumped["current_metrics"]["inflation_index_family"]
            == "US_CPI_URBAN"
        )


# ===========================================================================
# Workflow PrimitiveSpec registration
# ===========================================================================

class TestWorkflowRegistration:
    """Registration in ``rates_agent.workflows`` is part of the
    primitive contract.  These guard against an accidental miswiring
    that would break operator-template composition.
    """

    def test_resolver_returns_inflation_swap_rate_level_spec(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_inflation_swap_rate_level_tool",
        )
        assert (
            spec.tool_name == "calculate_inflation_swap_rate_level_tool"
        )
        assert (
            spec.config_path == INFLATION_SWAP_RATE_LEVEL_CONFIG_PATH
        )

    def test_spec_uses_correct_input_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_swaps.tools.inflation_swap_rate_level import (
            InflationSwapRateLevelInput,
            InflationSwapRateLevelOutput,
        )
        spec = rates_primitive_resolver(
            "calculate_inflation_swap_rate_level_tool",
        )
        assert spec.input_class is InflationSwapRateLevelInput
        assert spec.output_class is InflationSwapRateLevelOutput

    def test_spec_uses_correct_callable(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_swaps.tools.inflation_swap_rate_level import (
            calculate_inflation_swap_rate_level,
        )
        spec = rates_primitive_resolver(
            "calculate_inflation_swap_rate_level_tool",
        )
        assert spec.callable is calculate_inflation_swap_rate_level

    def test_output_field_units_declares_percent_for_time_series(self):
        """The canonical ``time_series`` field is in PERCENT units
        (ZCIS par rate).  The substrate's validate-time unit-compat
        checks rely on this declaration to refuse downstream operators
        that would misinterpret the units."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_inflation_swap_rate_level_tool",
        )
        assert spec.output_field_units == {"time_series": "percent"}

    def test_known_rates_primitives_includes_inflation_swap_rate_level(self):
        from rates_agent.workflows import known_rates_primitives
        assert (
            "calculate_inflation_swap_rate_level_tool"
            in known_rates_primitives()
        )


# ===========================================================================
# ZCIS instrument_type guard — MCP wrapper surface
# ===========================================================================

class TestMcpZcisInstrumentTypeGuard:
    """The MCP wrapper must surface the controlled-error envelope
    when the underlying tool refuses to compute against a non-ZCIS
    curve family.  Without this guarantee, an LLM hitting the ZCIS
    tool with curve_family='UST' would receive nominal yields
    presented under ``zcis_rate_pct`` semantics — a proxy violation
    forbidden by DESIGN_PRINCIPLES.md §1, §3 and
    STANDARD_TOOL_AND_YAML_RULES.md §J.
    """

    def test_non_zcis_curve_family_surfaces_controlled_error_envelope(
        self,
    ):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        controlled_error = {
            "error": (
                "No ZCIS data found for curve_family='UST', "
                "tenor='10Y', field='PX_MID' since 2025-01-01 "
                "(instrument_type='inflation_swap', "
                "pricing_type='zero_coupon_breakeven').  Verify the "
                "inflation-swap curve family and tenor exist in the "
                "database (see "
                "rates_agent/playbooks/inflation_swaps.yml)."
            )
        }
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_inflation_swap_rate_level",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_inflation_swap_rate_level_tool(
                curve_family="UST",
                tenor="10Y",
                lookback_days=365,
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        # No fabricated metrics may leak through.
        assert "current_metrics" not in parsed
        assert "zcis_rate_pct" not in parsed
        # The instrument_type / pricing_type rationale must travel
        # back to the LLM so it can route non-ZCIS questions
        # elsewhere.
        assert "inflation_swap" in parsed["error"]
        assert "zero_coupon_breakeven" in parsed["error"]


# ===========================================================================
# Methodology label sourced from YAML (not hardcoded)
# ===========================================================================

class TestMethodologyLabelSourcedFromYaml:
    """The methodology_label that reaches current_metrics must be
    sourced from the YAML's ``methodology.what_it_does`` — NOT a
    hardcoded Python literal.  Same threading pattern as
    cross_country_breakeven_spread_simple.
    """

    def test_bundled_yaml_methodology_what_it_does_is_nonempty(self):
        cfg = load_tool_config(INFLATION_SWAP_RATE_LEVEL_CONFIG_PATH)
        assert cfg.methodology.what_it_does.strip()
        # Pin the load-bearing detail: filters + reference-metadata
        # surface.
        what = cfg.methodology.what_it_does
        assert "instrument_type='inflation_swap'" in what
        assert "pricing_type='zero_coupon_breakeven'" in what
        assert "inflation_index_family" in what
