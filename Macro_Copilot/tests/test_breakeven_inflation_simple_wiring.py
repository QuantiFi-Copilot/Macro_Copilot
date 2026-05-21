"""
test_breakeven_inflation_simple_wiring.py — Caller-wiring smoke tests
for the linker breakeven_inflation_simple primitive.

Mirrors ``test_real_yield_level_wiring.py`` (the structural single-leg
linker analogue) and ``test_cross_market_spread_wiring.py`` (the
two-leg sovereign analogue), scoped to breakeven_inflation_simple's
external callers:

  1. rates_agent/inflation_indexed_bonds/mcp_server.py
     ::calculate_breakeven_inflation_simple_tool
  2. rates_agent/workflows/__init__.py PrimitiveSpec registration

Key load-bearing properties under test:
  - The MCP wrapper passes config explicitly (no auto-load fallback).
  - The MCP wrapper uses the empty-string / None-sentinel pattern so
    the YAML ``default_field_name`` actually flows through.
  - The MCP wrapper surfaces the controlled error envelope for the
    no-row cases (both pollution directions).
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

from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple import (
    CONFIG_PATH as BREAKEVEN_INFLATION_SIMPLE_CONFIG_PATH,
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


def _well_formed_breakeven_output() -> dict:
    """Mock matches the wire shape: snapshot ``current_metrics`` PLUS
    bespoke ``time_series`` row list PLUS the two canonical TimeSeries
    payloads."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-08",
            "nominal_curve_family": "UST",
            "linker_curve_family": "USD_TIPS",
            "tenor": "10Y",
            "breakeven_label": "UST-USD_TIPS 10Y breakeven",
            "breakeven_pct": 2.4500,
            "breakeven_bps": 245.00,
            "daily_change_bps": 1.50,
            "weekly_change_bps": 8.00,
            "monthly_change_bps": -3.00,
            "current_z_score": 0.85,
            "rolling_window_days": 252,
            "high_252d_bps": 280.00,
            "low_252d_bps": 215.00,
            "percentile_252d": 58.0,
            "nominal_yield_pct": 4.30,
            "real_yield_pct": 1.85,
            "methodology_label": (
                "Generic bond-implied breakeven inflation; not a "
                "clean expected-inflation read."
            ),
        },
        "time_series": [
            {"date": "2026-04-07", "breakeven_bps": 244.00, "z_score": 0.82},
            {"date": "2026-04-08", "breakeven_bps": 245.00, "z_score": 0.85},
        ],
        "time_series_breakeven": {
            "series_name": "ust_usd_tips_10y_breakeven",
            "units": "bps",
            "description": "Test breakeven series.",
            "rows": [
                {"date": "2026-04-07", "value": 244.00},
                {"date": "2026-04-08", "value": 245.00},
            ],
        },
        "time_series_zscore": {
            "series_name": "ust_usd_tips_10y_zscore",
            "units": "z_score",
            "description": "Test z-score series.",
            "rows": [
                {"date": "2026-04-07", "value": 0.82},
                {"date": "2026-04-08", "value": 0.85},
            ],
        },
    }


def _assert_breakeven_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_breakeven_inflation_simple called without `config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "calculate_breakeven_inflation_simple_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'calculate_breakeven_inflation_simple_tool' (catches imports of "
        "the wrong tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_field_name" in cfg.conventions


# ===========================================================================
# rates_agent/inflation_indexed_bonds/mcp_server.py
# ===========================================================================

class TestMcpBreakevenWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_breakeven_inflation_simple",
            return_value=_well_formed_breakeven_output(),
        ) as mock_bei:
            output_json = mcp_module.calculate_breakeven_inflation_simple_tool(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                tenor="10Y",
                lookback_days=365,
                field_name="YLD_YTM_MID",
            )
        assert mock_bei.call_count == 1
        _assert_breakeven_config_passed(mock_bei.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_field_name_default_is_empty_string_sentinel(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(
            mcp_module.calculate_breakeven_inflation_simple_tool,
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
            "calculate_breakeven_inflation_simple",
            return_value=_well_formed_breakeven_output(),
        ) as mock_bei:
            mcp_module.calculate_breakeven_inflation_simple_tool(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                tenor="10Y",
                lookback_days=365,
                # field_name omitted → defaults to ""
            )
        params = mock_bei.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach BreakevenInflationSimpleInput "
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
            "calculate_breakeven_inflation_simple",
            return_value=_well_formed_breakeven_output(),
        ) as mock_bei:
            mcp_module.calculate_breakeven_inflation_simple_tool(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                tenor="10Y",
                field_name="YLD_YTM_BID",
            )
        params = mock_bei.call_args.kwargs["params"]
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
            "calculate_breakeven_inflation_simple",
            return_value=_well_formed_breakeven_output(),
        ):
            output_json = mcp_module.calculate_breakeven_inflation_simple_tool(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        assert "time_series" not in parsed
        assert "time_series_breakeven" not in parsed
        assert "time_series_zscore" not in parsed
        # The methodology_label MUST survive the strip — it is the
        # wire-honesty disclosure the LLM needs to relay.
        assert "methodology_label" in parsed["current_metrics"]


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_breakeven_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(BREAKEVEN_INFLATION_SIMPLE_CONFIG_PATH)
        assert cfg.tool.name == "calculate_breakeven_inflation_simple_tool"
        assert cfg.tool.domain == "inflation_indexed_bonds"


# ===========================================================================
# Response-model validation
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.schemas import (
            BreakevenInflationSimpleOutput,
        )
        validated = BreakevenInflationSimpleOutput.model_validate(
            _well_formed_breakeven_output(),
        )
        assert validated.time_series_breakeven.units.value == "bps"
        assert validated.time_series_zscore.units.value == "z_score"

    def test_response_model_requires_canonical_series(self):
        from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.schemas import (
            BreakevenInflationSimpleOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_breakeven_output()
        bad.pop("time_series_breakeven")
        with pytest.raises(ValidationError):
            BreakevenInflationSimpleOutput.model_validate(bad)

    def test_round_trip_preserves_canonical_fields(self):
        from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.schemas import (
            BreakevenInflationSimpleOutput,
        )
        validated = BreakevenInflationSimpleOutput.model_validate(
            _well_formed_breakeven_output(),
        )
        dumped = validated.model_dump()
        assert dumped["time_series_breakeven"]["units"] == "bps"
        assert dumped["time_series_zscore"]["units"] == "z_score"
        assert dumped["current_metrics"]["methodology_label"]


# ===========================================================================
# Workflow PrimitiveSpec registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_resolver_returns_breakeven_spec(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_breakeven_inflation_simple_tool",
        )
        assert spec.tool_name == "calculate_breakeven_inflation_simple_tool"
        assert spec.config_path == BREAKEVEN_INFLATION_SIMPLE_CONFIG_PATH

    def test_spec_uses_correct_input_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple import (
            BreakevenInflationSimpleInput,
            BreakevenInflationSimpleOutput,
        )
        spec = rates_primitive_resolver(
            "calculate_breakeven_inflation_simple_tool",
        )
        assert spec.input_class is BreakevenInflationSimpleInput
        assert spec.output_class is BreakevenInflationSimpleOutput

    def test_spec_uses_correct_callable(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple import (
            calculate_breakeven_inflation_simple,
        )
        spec = rates_primitive_resolver(
            "calculate_breakeven_inflation_simple_tool",
        )
        assert spec.callable is calculate_breakeven_inflation_simple

    def test_output_field_units_declared_honestly(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_breakeven_inflation_simple_tool",
        )
        # Bespoke time_series row list carries breakeven_bps so it is
        # in BPS units; canonical breakeven series is BPS; canonical
        # z-score series is Z_SCORE.
        assert spec.output_field_units == {
            "time_series": "bps",
            "time_series_breakeven": "bps",
            "time_series_zscore": "z_score",
        }

    def test_known_rates_primitives_includes_breakeven(self):
        from rates_agent.workflows import known_rates_primitives
        assert (
            "calculate_breakeven_inflation_simple_tool"
            in known_rates_primitives()
        )


# ===========================================================================
# No-proxy guard surface
# ===========================================================================

class TestMcpNoProxyGuard:
    """The MCP wrapper must surface the controlled-error envelope
    when the underlying tool refuses to compute against polluted
    inputs (linker in nominal slot or vice versa).  Without this
    guarantee, the LLM hitting the breakeven tool with curve_family
    pollution would receive a fabricated near-zero value — a proxy
    violation forbidden by DESIGN_PRINCIPLES.md §1, §3 and
    STANDARD_TOOL_AND_YAML_RULES.md §J.
    """

    def test_linker_in_nominal_slot_surfaces_error_envelope(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        controlled_error = {
            "error": (
                "No nominal sovereign "
                "(instrument_type='sovereign_benchmark') rows found "
                "for curve_family='USD_TIPS', tenor='10Y', "
                "field='YLD_YTM_MID' since 2025-01-01.  If you "
                "passed a linker curve_family in the nominal slot "
                "(e.g. 'USD_TIPS', 'GBP_LINKER'), correct the "
                "argument order — this tool's nominal leg is "
                "filtered honestly with "
                "instrument_type='sovereign_benchmark' and will not "
                "silently fall through to linker data."
            )
        }
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_breakeven_inflation_simple",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_breakeven_inflation_simple_tool(
                nominal_curve_family="USD_TIPS",
                linker_curve_family="GBP_LINKER",
                tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "sovereign_benchmark" in parsed["error"]

    def test_nominal_in_linker_slot_surfaces_error_envelope(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        controlled_error = {
            "error": (
                "No linker (instrument_type='inflation_linker') "
                "rows found for curve_family='DE_BUND', tenor='10Y', "
                "field='YLD_YTM_MID' since 2025-01-01.  If you "
                "passed a nominal sovereign curve_family in the "
                "linker slot (e.g. 'UST', 'DE_BUND'), correct the "
                "argument order."
            )
        }
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_breakeven_inflation_simple",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_breakeven_inflation_simple_tool(
                nominal_curve_family="UST",
                linker_curve_family="DE_BUND",
                tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "inflation_linker" in parsed["error"]

    def test_cross_country_pair_surfaces_same_country_error_envelope(self):
        """The MCP wrapper MUST surface the same-country invariant
        error envelope as a clean ``{"error": ...}`` JSON payload —
        not a stack trace, not a partial snapshot.  Mirrors the
        existing pollution-probe wiring tests but for the cross-
        country branch (DE_BUND vs EUR_FR_LINKER — same currency,
        different country)."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        same_country_error = {
            "error": (
                "Cross-country pair refused: nominal 'DE_BUND' is "
                "('Germany', 'EUR') while linker 'EUR_FR_LINKER' is "
                "('France', 'EUR').  This primitive is a "
                "*same-country* generic bond-implied breakeven "
                "inflation by construction (the nominal sovereign "
                "and linker legs must share both country and "
                "currency, e.g. UST + USD_TIPS, UK_GILT + "
                "GBP_LINKER, FR_OAT + EUR_FR_LINKER, CANADA_GOVT + "
                "CAD_RRB).  A cross-country differential is a "
                "sovereign-credit / inflation-regime hybrid, not a "
                "generic breakeven, and is refused at compute time "
                "by design."
            )
        }
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_breakeven_inflation_simple",
            return_value=same_country_error,
        ):
            output_json = mcp_module.calculate_breakeven_inflation_simple_tool(
                nominal_curve_family="DE_BUND",
                linker_curve_family="EUR_FR_LINKER",
                tenor="10Y",
            )
        parsed = json.loads(output_json)
        # Exactly the controlled-error envelope shape.
        assert sorted(parsed.keys()) == ["error"]
        assert "current_metrics" not in parsed
        assert "DE_BUND" in parsed["error"]
        assert "EUR_FR_LINKER" in parsed["error"]
        assert "same-country" in parsed["error"].lower()
