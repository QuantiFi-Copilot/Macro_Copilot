"""
test_cross_country_breakeven_spread_simple_wiring.py — Caller-wiring
smoke tests for the linker cross_country_breakeven_spread_simple
primitive.

Mirrors ``test_breakeven_curve_spread_wiring.py`` (the closest
sibling) scoped to cross_country_breakeven_spread_simple's external
callers:

  1. rates_agent/inflation_indexed_bonds/mcp_server.py
     ::calculate_cross_country_breakeven_spread_simple_tool
  2. rates_agent/workflows/__init__.py PrimitiveSpec registration

Key load-bearing properties under test:
  - The MCP wrapper passes config explicitly (no auto-load fallback).
  - The MCP wrapper uses the empty-string / None-sentinel pattern so
    the YAML ``default_field_name`` actually flows through.
  - The MCP wrapper surfaces the controlled error envelope for the
    cross-country / leg-pollution / same-country-input cases.
  - The hub re-export and CONFIG_PATH public symbol are stable.
  - The wire-frozen + canonical TimeSeries fields survive a full
    round-trip through the response model.
  - The workflow registry exposes the primitive with the correct
    ``output_field_units`` declarations (BPS for time_series and
    time_series_spread, Z_SCORE for time_series_zscore).
  - methodology_label sourced from YAML reaches
    ``current_metrics.methodology_label`` on the wire.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple import (
    CONFIG_PATH as CROSS_COUNTRY_BREAKEVEN_SPREAD_SIMPLE_CONFIG_PATH,
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


def _well_formed_xc_breakeven_spread_output() -> dict:
    """Mock matches the wire shape: snapshot ``current_metrics`` PLUS
    bespoke ``time_series`` row list PLUS the two canonical
    TimeSeries payloads."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-08",
            "country_a_nominal_pair": "UST",
            "country_a_linker_pair": "USD_TIPS",
            "country_b_nominal_pair": "FR_OAT",
            "country_b_linker_pair": "EUR_FR_LINKER",
            "tenor": "10Y",
            "spread_label": "UST/USD_TIPS - FR_OAT/EUR_FR_LINKER 10Y XC breakeven",
            "current_spread_bps": 30.00,
            "daily_change_bps": 1.50,
            "weekly_change_bps": 8.00,
            "monthly_change_bps": -3.00,
            "current_z_score": 0.85,
            "rolling_window_days": 252,
            "high_252d_bps": 110.00,
            "low_252d_bps": -10.00,
            "percentile_252d": 33.0,
            "breakeven_a_bps": 240.00,
            "breakeven_b_bps": 210.00,
            "tenor_years": 10.0,
            "methodology_label": (
                "Same-tenor cross-country breakeven differential; "
                "breakeven_a_bps - breakeven_b_bps; country_a minus "
                "country_b; cross-country inflation-compensation "
                "differential, not pure expected inflation; subject "
                "to index-family mismatch (CPI-U vs HICP, etc.)."
            ),
        },
        "time_series": [
            {"date": "2026-04-07", "spread_bps": 28.50, "z_score": 0.82},
            {"date": "2026-04-08", "spread_bps": 30.00, "z_score": 0.85},
        ],
        "time_series_spread": {
            "series_name": "ust_usd_tips_fr_oat_eur_fr_linker_10y_xc_breakeven_spread",
            "units": "bps",
            "description": "Test cross-country breakeven spread series.",
            "rows": [
                {"date": "2026-04-07", "value": 28.50},
                {"date": "2026-04-08", "value": 30.00},
            ],
        },
        "time_series_zscore": {
            "series_name": "ust_usd_tips_fr_oat_eur_fr_linker_10y_xc_breakeven_spread_zscore",
            "units": "z_score",
            "description": "Test cross-country breakeven z-score series.",
            "rows": [
                {"date": "2026-04-07", "value": 0.82},
                {"date": "2026-04-08", "value": 0.85},
            ],
        },
    }


def _assert_xc_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_cross_country_breakeven_spread_simple called "
        "without `config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == (
        "calculate_cross_country_breakeven_spread_simple_tool"
    ), (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'calculate_cross_country_breakeven_spread_simple_tool' "
        "(catches imports of the wrong tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_field_name" in cfg.conventions


# ===========================================================================
# rates_agent/inflation_indexed_bonds/mcp_server.py
# ===========================================================================

class TestMcpXcBreakevenSpreadWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cross_country_breakeven_spread_simple",
            return_value=_well_formed_xc_breakeven_spread_output(),
        ) as mock_xcbs:
            output_json = mcp_module.calculate_cross_country_breakeven_spread_simple_tool(
                country_a_nominal_pair="UST",
                country_a_linker_pair="USD_TIPS",
                country_b_nominal_pair="FR_OAT",
                country_b_linker_pair="EUR_FR_LINKER",
                tenor="10Y",
                lookback_days=365,
                field_name="YLD_YTM_MID",
            )
        assert mock_xcbs.call_count == 1
        _assert_xc_config_passed(mock_xcbs.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_field_name_default_is_empty_string_sentinel(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(
            mcp_module.calculate_cross_country_breakeven_spread_simple_tool,
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
            "calculate_cross_country_breakeven_spread_simple",
            return_value=_well_formed_xc_breakeven_spread_output(),
        ) as mock_xcbs:
            mcp_module.calculate_cross_country_breakeven_spread_simple_tool(
                country_a_nominal_pair="UST",
                country_a_linker_pair="USD_TIPS",
                country_b_nominal_pair="FR_OAT",
                country_b_linker_pair="EUR_FR_LINKER",
                tenor="10Y",
                lookback_days=365,
                # field_name omitted → defaults to ""
            )
        params = mock_xcbs.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach "
            "CrossCountryBreakevenSpreadSimpleInput as None (sentinel "
            f"for 'use YAML default_field_name'), got "
            f"{params.field_name!r}"
        )

    def test_explicit_field_name_passes_through(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cross_country_breakeven_spread_simple",
            return_value=_well_formed_xc_breakeven_spread_output(),
        ) as mock_xcbs:
            mcp_module.calculate_cross_country_breakeven_spread_simple_tool(
                country_a_nominal_pair="UST",
                country_a_linker_pair="USD_TIPS",
                country_b_nominal_pair="FR_OAT",
                country_b_linker_pair="EUR_FR_LINKER",
                tenor="10Y",
                field_name="YLD_YTM_BID",
            )
        params = mock_xcbs.call_args.kwargs["params"]
        assert params.field_name == "YLD_YTM_BID"

    def test_time_series_stripped_from_llm_response(self):
        """The MCP wrapper must NOT serialize the bespoke or canonical
        TimeSeries payloads back to the LLM."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cross_country_breakeven_spread_simple",
            return_value=_well_formed_xc_breakeven_spread_output(),
        ):
            output_json = mcp_module.calculate_cross_country_breakeven_spread_simple_tool(
                country_a_nominal_pair="UST",
                country_a_linker_pair="USD_TIPS",
                country_b_nominal_pair="FR_OAT",
                country_b_linker_pair="EUR_FR_LINKER",
                tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        assert "time_series" not in parsed
        assert "time_series_spread" not in parsed
        assert "time_series_zscore" not in parsed
        # methodology_label MUST survive the strip.
        assert "methodology_label" in parsed["current_metrics"]


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_xc_breakeven_spread_config(self):
        cfg = load_tool_config(
            CROSS_COUNTRY_BREAKEVEN_SPREAD_SIMPLE_CONFIG_PATH,
        )
        assert cfg.tool.name == (
            "calculate_cross_country_breakeven_spread_simple_tool"
        )
        assert cfg.tool.domain == "inflation_indexed_bonds"


# ===========================================================================
# Response-model validation
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple.schemas import (
            CrossCountryBreakevenSpreadSimpleOutput,
        )
        validated = CrossCountryBreakevenSpreadSimpleOutput.model_validate(
            _well_formed_xc_breakeven_spread_output(),
        )
        assert validated.time_series_spread.units.value == "bps"
        assert validated.time_series_zscore.units.value == "z_score"

    def test_response_model_requires_canonical_series(self):
        from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple.schemas import (
            CrossCountryBreakevenSpreadSimpleOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_xc_breakeven_spread_output()
        bad.pop("time_series_spread")
        with pytest.raises(ValidationError):
            CrossCountryBreakevenSpreadSimpleOutput.model_validate(bad)

    def test_round_trip_preserves_canonical_fields(self):
        from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple.schemas import (
            CrossCountryBreakevenSpreadSimpleOutput,
        )
        validated = CrossCountryBreakevenSpreadSimpleOutput.model_validate(
            _well_formed_xc_breakeven_spread_output(),
        )
        dumped = validated.model_dump()
        assert dumped["time_series_spread"]["units"] == "bps"
        assert dumped["time_series_zscore"]["units"] == "z_score"
        assert dumped["current_metrics"]["methodology_label"]
        assert dumped["current_metrics"]["tenor_years"] == 10.0


# ===========================================================================
# methodology_label sourced from YAML reaches current_metrics
# ===========================================================================

class TestMethodologyLabelOnWire:
    """End-to-end-ish proof that the wire's
    ``current_metrics.methodology_label`` is sourced from the YAML's
    ``methodology.what_it_does`` (the bundled config) — NOT a
    hardcoded Python literal."""

    def test_methodology_label_from_yaml_reaches_wire(self):
        cfg = load_tool_config(
            CROSS_COUNTRY_BREAKEVEN_SPREAD_SIMPLE_CONFIG_PATH,
        )
        expected = cfg.methodology.what_it_does.strip()
        # The bundled config's wire-honesty disclosure must include
        # the spread formula, sign convention, and index-family
        # caveat — exactly the same load-bearing claims the compute
        # threads to current_metrics.methodology_label.
        assert "breakeven_a_bps" in expected
        assert "breakeven_b_bps" in expected
        assert "country_a minus country_b" in expected
        assert "INDEX-FAMILY" in expected or "index-family" in expected.lower()
        # The wire label is sourced from this exact text by compute(),
        # confirmed in the compute test
        # ``test_methodology_label_sourced_from_yaml`` and
        # ``test_bundled_methodology_label_carries_index_family_caveat``.


# ===========================================================================
# Workflow PrimitiveSpec registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_resolver_returns_xc_breakeven_spread_spec(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_cross_country_breakeven_spread_simple_tool",
        )
        assert spec.tool_name == (
            "calculate_cross_country_breakeven_spread_simple_tool"
        )
        assert spec.config_path == (
            CROSS_COUNTRY_BREAKEVEN_SPREAD_SIMPLE_CONFIG_PATH
        )

    def test_spec_uses_correct_input_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple import (
            CrossCountryBreakevenSpreadSimpleInput,
            CrossCountryBreakevenSpreadSimpleOutput,
        )
        spec = rates_primitive_resolver(
            "calculate_cross_country_breakeven_spread_simple_tool",
        )
        assert spec.input_class is CrossCountryBreakevenSpreadSimpleInput
        assert spec.output_class is CrossCountryBreakevenSpreadSimpleOutput

    def test_spec_uses_correct_callable(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple import (
            calculate_cross_country_breakeven_spread_simple,
        )
        spec = rates_primitive_resolver(
            "calculate_cross_country_breakeven_spread_simple_tool",
        )
        assert spec.callable is calculate_cross_country_breakeven_spread_simple

    def test_output_field_units_declared_honestly(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_cross_country_breakeven_spread_simple_tool",
        )
        # Bespoke time_series row list carries spread_bps, so BPS;
        # canonical spread series is BPS; canonical z-score series
        # is Z_SCORE.
        assert spec.output_field_units == {
            "time_series": "bps",
            "time_series_spread": "bps",
            "time_series_zscore": "z_score",
        }

    def test_known_rates_primitives_includes_xc_breakeven_spread(self):
        from rates_agent.workflows import known_rates_primitives
        assert (
            "calculate_cross_country_breakeven_spread_simple_tool"
            in known_rates_primitives()
        )


# ===========================================================================
# Controlled-error envelope surface
# ===========================================================================

class TestMcpControlledErrorSurface:
    """The MCP wrapper must surface controlled-error envelopes from
    the underlying tool — without this the LLM hitting the cross-
    country tool with leg pollution / cross-country same-pair input
    would receive a fabricated value or a stack trace.
    """

    def test_country_a_leg_failure_surfaces_error_envelope(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        controlled_error = {
            "error": (
                "Cross-country breakeven spread "
                "(UST/USD_TIPS vs FR_OAT/EUR_FR_LINKER @ 10Y) "
                "could not be computed: country_a leg failed: "
                "No linker (instrument_type='inflation_linker') "
                "rows found for curve_family='USD_TIPS', "
                "tenor='10Y'."
            )
        }
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cross_country_breakeven_spread_simple",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_cross_country_breakeven_spread_simple_tool(
                country_a_nominal_pair="UST",
                country_a_linker_pair="USD_TIPS",
                country_b_nominal_pair="FR_OAT",
                country_b_linker_pair="EUR_FR_LINKER",
                tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "country_a leg failed" in parsed["error"]

    def test_leg_pollution_surfaces_same_country_error_envelope(self):
        """Leg-internal cross-country pollution (e.g.
        country_a_nominal=UST + country_a_linker=EUR_FR_LINKER) flows
        through the spot primitive's same-country invariant and
        emerges as a leg-attributed controlled error envelope."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        same_country_error = {
            "error": (
                "Cross-country breakeven spread "
                "(UST/EUR_FR_LINKER vs FR_OAT/GBP_LINKER @ 10Y) "
                "could not be computed: country_a leg failed: "
                "Cross-country pair refused: nominal 'UST' is "
                "('US', 'USD') while linker 'EUR_FR_LINKER' is "
                "('France', 'EUR').  This primitive is a "
                "*same-country* generic bond-implied breakeven "
                "inflation by construction..."
            )
        }
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cross_country_breakeven_spread_simple",
            return_value=same_country_error,
        ):
            output_json = mcp_module.calculate_cross_country_breakeven_spread_simple_tool(
                country_a_nominal_pair="UST",
                country_a_linker_pair="EUR_FR_LINKER",
                country_b_nominal_pair="FR_OAT",
                country_b_linker_pair="GBP_LINKER",
                tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert sorted(parsed.keys()) == ["error"]
        assert "country_a leg failed" in parsed["error"]
        assert "same-country" in parsed["error"].lower()

    def test_same_country_input_surfaces_validation_error(self):
        """Identical country_a / country_b pairs MUST hit the
        Pydantic validator and emerge as a controlled error envelope."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.calculate_cross_country_breakeven_spread_simple_tool(
                country_a_nominal_pair="UST",
                country_a_linker_pair="USD_TIPS",
                country_b_nominal_pair="UST",
                country_b_linker_pair="USD_TIPS",
                tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "Invalid parameters" in parsed["error"]
