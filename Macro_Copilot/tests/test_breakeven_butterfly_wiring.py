"""
test_breakeven_butterfly_wiring.py — Caller-wiring smoke tests for
the linker breakeven_butterfly primitive.

Mirrors ``test_breakeven_curve_spread_wiring.py`` and
``test_real_yield_butterfly_wiring.py`` (closest siblings) scoped to
breakeven_butterfly's external callers:

  1. rates_agent/inflation_indexed_bonds/mcp_server.py
     ::calculate_breakeven_butterfly_tool
  2. rates_agent/workflows/__init__.py PrimitiveSpec registration

Key load-bearing properties under test:
  - The MCP wrapper passes config explicitly (no auto-load
    fallback).
  - The MCP wrapper uses the empty-string / None-sentinel pattern
    so the YAML ``default_field_name`` actually flows through.
  - The MCP wrapper surfaces the controlled error envelope for the
    no-row / cross-country / leg-pollution / inverted-tenor cases.
  - The MCP docstring advertises only LIVE linker curve families
    and tenors (no ghost markets).
  - The methodology_label is preserved in the MCP-level output.
  - The hub re-export and CONFIG_PATH public symbol are stable.
  - The wire-frozen + canonical TimeSeries fields survive a full
    round-trip through the response model.
  - The workflow registry exposes the primitive with the correct
    ``output_field_units`` declarations (BPS for time_series and
    time_series_butterfly, Z_SCORE for time_series_zscore).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly import (
    CONFIG_PATH as BREAKEVEN_BUTTERFLY_CONFIG_PATH,
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


def _well_formed_butterfly_output() -> dict:
    """Mock matches the wire shape: snapshot ``current_metrics`` PLUS
    bespoke ``time_series`` row list PLUS the two canonical
    TimeSeries payloads."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-08",
            "nominal_curve_family": "UST",
            "linker_curve_family": "USD_TIPS",
            "short_tenor": "5Y",
            "belly_tenor": "10Y",
            "long_tenor": "30Y",
            "butterfly_label": "UST/USD_TIPS 5s10s30s breakeven",
            "current_butterfly_bps": -12.50,
            "daily_change_bps": 0.75,
            "weekly_change_bps": -3.00,
            "monthly_change_bps": 6.25,
            "current_z_score": -0.42,
            "rolling_window_days": 252,
            "high_252d_bps": 18.50,
            "low_252d_bps": -22.00,
            "percentile_252d": 23.5,
            "wing_short_bps": 35.00,
            "wing_long_bps": 10.00,
            "short_breakeven_bps": 220.00,
            "belly_breakeven_bps": 255.00,
            "long_breakeven_bps": 265.00,
            "short_years": 5.0,
            "belly_years": 10.0,
            "long_years": 30.0,
            "observation_count": 252,
            "methodology_label": (
                "Same-country bond-implied breakeven butterfly; "
                "belly_breakeven_bps - 0.5*(short_breakeven_bps + "
                "long_breakeven_bps); inflation compensation "
                "curvature, not pure expected inflation.  "
                "(-0.5, +1.0, -0.5) weight tuple."
            ),
        },
        "time_series": [
            {
                "date": "2026-04-07",
                "butterfly_bps": -13.25,
                "z_score": -0.45,
            },
            {
                "date": "2026-04-08",
                "butterfly_bps": -12.50,
                "z_score": -0.42,
            },
        ],
        "time_series_butterfly": {
            "series_name": (
                "ust_usd_tips_5y_10y_30y_breakeven_butterfly"
            ),
            "units": "bps",
            "description": "Test breakeven butterfly series.",
            "rows": [
                {"date": "2026-04-07", "value": -13.25},
                {"date": "2026-04-08", "value": -12.50},
            ],
        },
        "time_series_zscore": {
            "series_name": (
                "ust_usd_tips_5y_10y_30y_breakeven_butterfly_zscore"
            ),
            "units": "z_score",
            "description": "Test breakeven butterfly z-score series.",
            "rows": [
                {"date": "2026-04-07", "value": -0.45},
                {"date": "2026-04-08", "value": -0.42},
            ],
        },
    }


def _assert_butterfly_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_breakeven_butterfly called without `config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "calculate_breakeven_butterfly_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'calculate_breakeven_butterfly_tool' (catches imports of "
        "the wrong tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_field_name" in cfg.conventions


# ===========================================================================
# rates_agent/inflation_indexed_bonds/mcp_server.py
# ===========================================================================

class TestMcpButterflyWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_breakeven_butterfly",
            return_value=_well_formed_butterfly_output(),
        ) as mock_bb:
            output_json = mcp_module.calculate_breakeven_butterfly_tool(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
                lookback_days=365,
                field_name="YLD_YTM_MID",
            )
        assert mock_bb.call_count == 1
        _assert_butterfly_config_passed(mock_bb.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_field_name_default_is_empty_string_sentinel(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(
            mcp_module.calculate_breakeven_butterfly_tool,
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
            "calculate_breakeven_butterfly",
            return_value=_well_formed_butterfly_output(),
        ) as mock_bb:
            mcp_module.calculate_breakeven_butterfly_tool(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
                lookback_days=365,
                # field_name omitted → defaults to ""
            )
        params = mock_bb.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach BreakevenButterflyInput "
            f"as None (sentinel for 'use YAML default_field_name'), "
            f"got {params.field_name!r}"
        )

    def test_empty_string_field_name_flows_none_to_input(self):
        """Explicit empty-string MUST translate to None (matches the
        sentinel pattern)."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_breakeven_butterfly",
            return_value=_well_formed_butterfly_output(),
        ) as mock_bb:
            mcp_module.calculate_breakeven_butterfly_tool(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
                field_name="",
            )
        params = mock_bb.call_args.kwargs["params"]
        assert params.field_name is None

    def test_explicit_field_name_passes_through(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_breakeven_butterfly",
            return_value=_well_formed_butterfly_output(),
        ) as mock_bb:
            mcp_module.calculate_breakeven_butterfly_tool(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
                field_name="YLD_YTM_BID",
            )
        params = mock_bb.call_args.kwargs["params"]
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
            "calculate_breakeven_butterfly",
            return_value=_well_formed_butterfly_output(),
        ):
            output_json = mcp_module.calculate_breakeven_butterfly_tool(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
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
        assert (
            "compensation"
            in parsed["current_metrics"]["methodology_label"].lower()
        )

    def test_docstring_advertises_only_live_curve_families(self):
        """MCP docstring must mention only the live linker
        curve_families (USD_TIPS, GBP_LINKER, EUR_FR_LINKER,
        CAD_RRB) and matched nominal pairs (UST, UK_GILT, FR_OAT,
        CANADA_GOVT).  No ghost markets like 'JPY_LINKER' or
        'IT_LINKER' — those aren't in the playbook."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        doc = mcp_module.calculate_breakeven_butterfly_tool.__doc__ or ""
        # Live linker curve_families
        for cf in ("USD_TIPS", "GBP_LINKER", "EUR_FR_LINKER", "CAD_RRB"):
            assert cf in doc, f"docstring missing live linker: {cf}"
        # Live nominal curve_families
        for cf in ("UST", "UK_GILT", "FR_OAT", "CANADA_GOVT"):
            assert cf in doc, f"docstring missing live nominal: {cf}"
        # Ghost markets — must NOT appear (catch copy-paste errors).
        for ghost in (
            "JPY_LINKER",
            "JGB_LINKER",
            "IT_LINKER",
            "AU_LINKER",
        ):
            assert ghost not in doc, (
                f"docstring advertises ghost curve_family {ghost!r} "
                "— remove it"
            )


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_butterfly_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(BREAKEVEN_BUTTERFLY_CONFIG_PATH)
        assert cfg.tool.name == "calculate_breakeven_butterfly_tool"
        assert cfg.tool.domain == "inflation_indexed_bonds"


# ===========================================================================
# Response-model validation
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly.schemas import (
            BreakevenButterflyOutput,
        )
        validated = BreakevenButterflyOutput.model_validate(
            _well_formed_butterfly_output(),
        )
        assert validated.time_series_butterfly.units.value == "bps"
        assert validated.time_series_zscore.units.value == "z_score"

    def test_response_model_requires_canonical_series(self):
        from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly.schemas import (
            BreakevenButterflyOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_butterfly_output()
        bad.pop("time_series_butterfly")
        with pytest.raises(ValidationError):
            BreakevenButterflyOutput.model_validate(bad)

    def test_round_trip_preserves_canonical_fields(self):
        from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly.schemas import (
            BreakevenButterflyOutput,
        )
        validated = BreakevenButterflyOutput.model_validate(
            _well_formed_butterfly_output(),
        )
        dumped = validated.model_dump()
        assert dumped["time_series_butterfly"]["units"] == "bps"
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
            "calculate_breakeven_butterfly_tool",
        )
        assert spec.tool_name == "calculate_breakeven_butterfly_tool"
        assert spec.config_path == BREAKEVEN_BUTTERFLY_CONFIG_PATH

    def test_spec_uses_correct_input_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly import (
            BreakevenButterflyInput,
            BreakevenButterflyOutput,
        )
        spec = rates_primitive_resolver(
            "calculate_breakeven_butterfly_tool",
        )
        assert spec.input_class is BreakevenButterflyInput
        assert spec.output_class is BreakevenButterflyOutput

    def test_spec_uses_correct_callable(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly import (
            calculate_breakeven_butterfly,
        )
        spec = rates_primitive_resolver(
            "calculate_breakeven_butterfly_tool",
        )
        assert spec.callable is calculate_breakeven_butterfly

    def test_output_field_units_declared_honestly(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_breakeven_butterfly_tool",
        )
        # Bespoke time_series row list carries butterfly_bps, so BPS;
        # canonical butterfly series is BPS; canonical z-score series
        # is Z_SCORE.  Distinct from real_yield_butterfly which uses
        # PERCENT for the butterfly + bespoke row list.
        assert spec.output_field_units == {
            "time_series": "bps",
            "time_series_butterfly": "bps",
            "time_series_zscore": "z_score",
        }

    def test_known_rates_primitives_includes_butterfly(self):
        from rates_agent.workflows import known_rates_primitives
        assert (
            "calculate_breakeven_butterfly_tool"
            in known_rates_primitives()
        )


# ===========================================================================
# Controlled-error envelope surface
# ===========================================================================

class TestMcpControlledErrorSurface:
    """The MCP wrapper must surface controlled-error envelopes from
    the underlying tool — without this the LLM hitting the butterfly
    tool with curve-family pollution / cross-country pairs would
    receive a fabricated value or a stack trace.
    """

    def test_missing_endpoint_surfaces_error_envelope(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        controlled_error = {
            "error": (
                "Breakeven butterfly 5Y10Y30Y could not be computed "
                "for UST vs USD_TIPS: the short-tenor endpoint (5Y) "
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
            "calculate_breakeven_butterfly",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_breakeven_butterfly_tool(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "short-tenor" in parsed["error"].lower()
        assert "5Y" in parsed["error"]

    def test_cross_country_pair_surfaces_same_country_error_envelope(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        same_country_error = {
            "error": (
                "Breakeven butterfly 5Y10Y30Y could not be computed "
                "for DE_BUND vs EUR_FR_LINKER: the short-tenor "
                "endpoint (5Y) failed.  Inner error: Cross-country "
                "pair refused: nominal 'DE_BUND' is "
                "('Germany', 'EUR') while linker 'EUR_FR_LINKER' "
                "is ('France', 'EUR').  This primitive is a "
                "*same-country* generic bond-implied breakeven "
                "inflation by construction..."
            )
        }
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_breakeven_butterfly",
            return_value=same_country_error,
        ):
            output_json = mcp_module.calculate_breakeven_butterfly_tool(
                nominal_curve_family="DE_BUND",
                linker_curve_family="EUR_FR_LINKER",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
            )
        parsed = json.loads(output_json)
        assert sorted(parsed.keys()) == ["error"]
        assert "current_metrics" not in parsed
        assert "DE_BUND" in parsed["error"]
        assert "EUR_FR_LINKER" in parsed["error"]
        assert "same-country" in parsed["error"].lower()

    def test_invalid_tenor_triplet_surfaces_validation_error(self):
        """Inverted triplet (long < belly < short) must hit the
        Pydantic validator and emerge as a controlled error
        envelope."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.calculate_breakeven_butterfly_tool(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                short_tenor="30Y",
                belly_tenor="10Y",
                long_tenor="5Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_duplicate_tenor_triplet_surfaces_validation_error(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.calculate_breakeven_butterfly_tool(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                short_tenor="10Y",
                belly_tenor="10Y",
                long_tenor="30Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "Invalid parameters" in parsed["error"]
