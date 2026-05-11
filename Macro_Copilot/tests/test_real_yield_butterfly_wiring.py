"""
test_real_yield_butterfly_wiring.py — Caller-wiring smoke tests for
the linker real_yield_butterfly primitive.

Mirrors ``test_real_yield_curve_spread_wiring.py`` (the closest
sibling composition pattern) scoped to real_yield_butterfly's
external callers:

  1. rates_agent/inflation_indexed_bonds/mcp_server.py
     ::calculate_real_yield_butterfly_tool
  2. rates_agent/workflows/__init__.py PrimitiveSpec registration

Key load-bearing properties under test:
  - The MCP wrapper passes config explicitly (no auto-load fallback).
  - The MCP wrapper uses the empty-string / None-sentinel pattern so
    the YAML ``default_field_name`` actually flows through.
  - The MCP wrapper surfaces the controlled error envelope for the
    no-row / non-linker-curve / duplicate-tenor / inverted-tenor
    cases.
  - The hub re-export and CONFIG_PATH public symbol are stable.
  - The wire-frozen + canonical TimeSeries fields survive a full
    round-trip through the response model.
  - The workflow registry exposes the primitive with the correct
    ``output_field_units`` declarations (PERCENT for time_series
    and time_series_butterfly, Z_SCORE for time_series_zscore).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly import (
    CONFIG_PATH as REAL_YIELD_BUTTERFLY_CONFIG_PATH,
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


def _well_formed_butterfly_output() -> dict:
    """Mock matches the wire shape: snapshot ``current_metrics``
    PLUS bespoke ``time_series`` row list PLUS the two canonical
    TimeSeries payloads.
    """
    return {
        "current_metrics": {
            "as_of_date": "2026-04-08",
            "curve_family": "USD_TIPS",
            "short_tenor": "5Y",
            "belly_tenor": "10Y",
            "long_tenor": "30Y",
            "butterfly_label": "USD_TIPS 5s10s30s real-yield",
            "current_butterfly_pct": 0.1500,
            "daily_change_bps": 1.50,
            "weekly_change_bps": 4.00,
            "monthly_change_bps": -2.00,
            "current_z_score": 0.65,
            "rolling_window_days": 252,
            "high_252d_pct": 0.4500,
            "low_252d_pct": -0.1000,
            "percentile_252d": 50.0,
            "wing_short_pct": 0.2000,
            "wing_long_pct": 0.2000,
            "short_real_yield_pct": 1.8500,
            "belly_real_yield_pct": 2.0500,
            "long_real_yield_pct": 2.2500,
            "short_years": 5.0,
            "belly_years": 10.0,
            "long_years": 30.0,
            "observation_count": 252,
            "country": "US",
            "currency": "USD",
            "methodology_label": (
                "Same-country linker real-yield butterfly; belly - "
                "0.5*(short+long); USD_TIPS 5s10s30s real-yield."
            ),
        },
        "time_series": [
            {"date": "2026-04-07", "butterfly_pct": 0.1450, "z_score": 0.62},
            {"date": "2026-04-08", "butterfly_pct": 0.1500, "z_score": 0.65},
        ],
        "time_series_butterfly": {
            "series_name": "usd_tips_5y_10y_30y_real_yield_butterfly",
            "units": "percent",
            "description": "Test real-yield butterfly series.",
            "rows": [
                {"date": "2026-04-07", "value": 0.1450},
                {"date": "2026-04-08", "value": 0.1500},
            ],
        },
        "time_series_zscore": {
            "series_name": (
                "usd_tips_5y_10y_30y_real_yield_butterfly_zscore"
            ),
            "units": "z_score",
            "description": "Test real-yield butterfly z-score series.",
            "rows": [
                {"date": "2026-04-07", "value": 0.62},
                {"date": "2026-04-08", "value": 0.65},
            ],
        },
    }


def _assert_butterfly_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_real_yield_butterfly called without `config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "calculate_real_yield_butterfly_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'calculate_real_yield_butterfly_tool' (catches imports of "
        "the wrong tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_field_name" in cfg.conventions


# ===========================================================================
# rates_agent/inflation_indexed_bonds/mcp_server.py
# ===========================================================================

class TestMcpRealYieldButterflyToolWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_real_yield_butterfly",
            return_value=_well_formed_butterfly_output(),
        ) as mock_ryb:
            output_json = mcp_module.calculate_real_yield_butterfly_tool(
                curve_family="USD_TIPS",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
                lookback_days=365,
                field_name="YLD_YTM_MID",
            )
        assert mock_ryb.call_count == 1
        _assert_butterfly_config_passed(mock_ryb.call_args)
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
            mcp_module.calculate_real_yield_butterfly_tool,
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
            "calculate_real_yield_butterfly",
            return_value=_well_formed_butterfly_output(),
        ) as mock_ryb:
            mcp_module.calculate_real_yield_butterfly_tool(
                curve_family="USD_TIPS",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
                lookback_days=365,
                # field_name omitted → defaults to ""
            )
        params = mock_ryb.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach "
            f"RealYieldButterflyInput as None (sentinel for "
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
            "calculate_real_yield_butterfly",
            return_value=_well_formed_butterfly_output(),
        ) as mock_ryb:
            mcp_module.calculate_real_yield_butterfly_tool(
                curve_family="USD_TIPS",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
                field_name="YLD_YTM_BID",
            )
        params = mock_ryb.call_args.kwargs["params"]
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
            "calculate_real_yield_butterfly",
            return_value=_well_formed_butterfly_output(),
        ):
            output_json = mcp_module.calculate_real_yield_butterfly_tool(
                curve_family="USD_TIPS",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
            )
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        assert "time_series" not in parsed
        assert "time_series_butterfly" not in parsed
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
        from rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_butterfly_config(self):
        cfg = load_tool_config(REAL_YIELD_BUTTERFLY_CONFIG_PATH)
        assert cfg.tool.name == "calculate_real_yield_butterfly_tool"
        assert cfg.tool.domain == "inflation_indexed_bonds"


# ===========================================================================
# Response-model validation
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        from rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly.schemas import (
            RealYieldButterflyOutput,
        )
        validated = RealYieldButterflyOutput.model_validate(
            _well_formed_butterfly_output(),
        )
        # The butterfly is in PERCENT — distinct from the BPS
        # convention the sovereign butterfly uses.
        assert validated.time_series_butterfly.units.value == "percent"
        assert validated.time_series_zscore.units.value == "z_score"
        # Reference metadata threads through validation cleanly.
        assert validated.current_metrics.country == "US"
        assert validated.current_metrics.currency == "USD"

    def test_response_model_requires_canonical_series(self):
        from rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly.schemas import (
            RealYieldButterflyOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_butterfly_output()
        bad.pop("time_series_butterfly")
        with pytest.raises(ValidationError):
            RealYieldButterflyOutput.model_validate(bad)

    def test_response_model_requires_methodology_label(self):
        from rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly.schemas import (
            RealYieldButterflyOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_butterfly_output()
        bad["current_metrics"].pop("methodology_label")
        with pytest.raises(ValidationError):
            RealYieldButterflyOutput.model_validate(bad)

    def test_response_model_requires_country_and_currency(self):
        """``country`` / ``currency`` are load-bearing wire fields —
        making them optional would let a future regression silently
        drop the resolved linker identity from the wire."""
        from rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly.schemas import (
            RealYieldButterflyOutput,
        )
        from pydantic import ValidationError
        for required_field in ("country", "currency"):
            bad = _well_formed_butterfly_output()
            bad["current_metrics"].pop(required_field)
            with pytest.raises(ValidationError):
                RealYieldButterflyOutput.model_validate(bad)

    def test_round_trip_preserves_canonical_fields(self):
        from rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly.schemas import (
            RealYieldButterflyOutput,
        )
        validated = RealYieldButterflyOutput.model_validate(
            _well_formed_butterfly_output(),
        )
        dumped = validated.model_dump()
        assert dumped["time_series_butterfly"]["units"] == "percent"
        assert dumped["time_series_zscore"]["units"] == "z_score"
        assert dumped["current_metrics"]["methodology_label"]
        assert dumped["current_metrics"]["short_years"] == 5.0
        assert dumped["current_metrics"]["belly_years"] == 10.0
        assert dumped["current_metrics"]["long_years"] == 30.0


# ===========================================================================
# Workflow PrimitiveSpec registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_resolver_returns_butterfly_spec(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_real_yield_butterfly_tool",
        )
        assert (
            spec.tool_name == "calculate_real_yield_butterfly_tool"
        )
        assert spec.config_path == REAL_YIELD_BUTTERFLY_CONFIG_PATH

    def test_spec_uses_correct_input_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly import (
            RealYieldButterflyInput,
            RealYieldButterflyOutput,
        )
        spec = rates_primitive_resolver(
            "calculate_real_yield_butterfly_tool",
        )
        assert spec.input_class is RealYieldButterflyInput
        assert spec.output_class is RealYieldButterflyOutput

    def test_spec_uses_correct_callable(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly import (
            calculate_real_yield_butterfly,
        )
        spec = rates_primitive_resolver(
            "calculate_real_yield_butterfly_tool",
        )
        assert spec.callable is calculate_real_yield_butterfly

    def test_output_field_units_declared_honestly(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_real_yield_butterfly_tool",
        )
        # Bespoke time_series row list carries butterfly_pct, so
        # PERCENT; canonical butterfly series is PERCENT (NOT bps —
        # real yields are in PERCENT and not multiplied by 100);
        # canonical z-score series is Z_SCORE.
        assert spec.output_field_units == {
            "time_series": "percent",
            "time_series_butterfly": "percent",
            "time_series_zscore": "z_score",
        }

    def test_known_rates_primitives_includes_real_yield_butterfly(self):
        from rates_agent.workflows import known_rates_primitives
        assert (
            "calculate_real_yield_butterfly_tool"
            in known_rates_primitives()
        )


# ===========================================================================
# Controlled-error envelope surface
# ===========================================================================

class TestMcpControlledErrorSurface:
    """The MCP wrapper must surface controlled-error envelopes from
    the underlying tool — without this the LLM hitting the butterfly
    tool with a non-linker curve_family / inverted tenor would
    receive a fabricated value or a stack trace.
    """

    def test_missing_endpoint_surfaces_error_envelope(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        controlled_error = {
            "error": (
                "Real-yield butterfly 5Y10Y30Y could not be "
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
            "calculate_real_yield_butterfly",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_real_yield_butterfly_tool(
                curve_family="USD_TIPS",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
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
                "use the sovereign_bonds calculate_butterfly_tool "
                "instead — this tool only operates on inflation-"
                "linker rows."
            )
        }
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_real_yield_butterfly",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_real_yield_butterfly_tool(
                curve_family="UST",
                short_tenor="2Y",
                belly_tenor="5Y",
                long_tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "inflation_linker" in parsed["error"]
        # Routing hint to sovereign tool must survive.
        assert "sovereign" in parsed["error"].lower()

    def test_inverted_tenor_pair_surfaces_validation_error(self):
        """Inverted triplet must hit the Pydantic validator and
        emerge as a controlled error envelope."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.calculate_real_yield_butterfly_tool(
                curve_family="USD_TIPS",
                short_tenor="30Y",
                belly_tenor="10Y",
                long_tenor="5Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_duplicate_tenor_surfaces_validation_error(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.calculate_real_yield_butterfly_tool(
                curve_family="USD_TIPS",
                short_tenor="10Y",
                belly_tenor="10Y",
                long_tenor="30Y",
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
    real_yield_curve_spread / inflation_swap_curve_spread.
    """

    def test_bundled_yaml_methodology_what_it_does_is_nonempty(self):
        cfg = load_tool_config(REAL_YIELD_BUTTERFLY_CONFIG_PATH)
        assert cfg.methodology.what_it_does.strip()
        # Pin the load-bearing detail: butterfly formula + sign
        # convention + composition pattern.
        what = cfg.methodology.what_it_does.lower()
        assert "belly_real_yield_pct" in what
        assert "short_real_yield_pct" in what
        assert "long_real_yield_pct" in what
        assert "get_real_yield_level" in what
        # Sign convention disclosure.
        assert "cheap" in what and "rich" in what
