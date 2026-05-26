"""
test_cross_country_real_yield_spread_simple_wiring.py — Caller-
wiring smoke tests for the linker
cross_country_real_yield_spread_simple primitive.

Mirrors ``test_real_yield_curve_spread_wiring.py`` and
``test_cross_country_breakeven_spread_simple_wiring.py`` scoped to
this primitive's external callers:

  1. rates_agent/inflation_indexed_bonds/mcp_server.py
     ::calculate_cross_country_real_yield_spread_simple_tool
  2. rates_agent/workflows/__init__.py PrimitiveSpec registration

Key load-bearing properties under test:
  - The MCP wrapper passes config explicitly (no auto-load
    fallback).
  - The MCP wrapper uses the empty-string / None-sentinel pattern
    so the YAML ``default_field_name`` actually flows through.
  - The MCP wrapper surfaces the controlled error envelope for
    same-curve / non-linker-curve / unknown-pillar cases.
  - The MCP docstring advertises only LIVE cross-country linker
    pairs (no ghost markets).
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

from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple import (
    CONFIG_PATH as CROSS_COUNTRY_REAL_YIELD_SPREAD_SIMPLE_CONFIG_PATH,
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


def _well_formed_output() -> dict:
    """Mock matches the wire shape: snapshot ``current_metrics``
    PLUS bespoke ``time_series`` row list PLUS the two canonical
    TimeSeries payloads.
    """
    return {
        "current_metrics": {
            "as_of_date": "2026-04-08",
            "first_curve_family": "USD_TIPS",
            "second_curve_family": "GBP_LINKER",
            "tenor": "10Y",
            "spread_label": "USD_TIPS - GBP_LINKER 10Y XC real-yield",
            "current_spread_pct": 1.2500,
            "daily_change_bps": 1.50,
            "weekly_change_bps": 4.00,
            "monthly_change_bps": -2.00,
            "current_z_score": 0.65,
            "rolling_window_days": 252,
            "high_252d_pct": 1.6000,
            "low_252d_pct": 0.4000,
            "percentile_252d": 50.0,
            "first_curve_real_yield_pct": 2.1000,
            "second_curve_real_yield_pct": 0.8500,
            "tenor_years": 10.0,
            "first_curve_country": "US",
            "first_curve_currency": "USD",
            "second_curve_country": "UK",
            "second_curve_currency": "GBP",
            "methodology_label": (
                "Same-tenor cross-country linker real-yield "
                "differential; first_curve_real_yield_pct - "
                "second_curve_real_yield_pct; subject to index-"
                "family AND market-structure mismatch caveats."
            ),
        },
        "time_series": [
            {"date": "2026-04-07", "spread_pct": 1.2350, "z_score": 0.62},
            {"date": "2026-04-08", "spread_pct": 1.2500, "z_score": 0.65},
        ],
        "time_series_spread": {
            "series_name": (
                "usd_tips_gbp_linker_10y_xc_real_yield_spread"
            ),
            "units": "percent",
            "description": (
                "Cross-country linker real-yield spread "
                "(USD_TIPS - GBP_LINKER) at 10Y "
                "(first_curve_real_yield_pct - "
                "second_curve_real_yield_pct) over the displayed "
                "window."
            ),
            "rows": [
                {"date": "2026-04-07", "value": 1.2350},
                {"date": "2026-04-08", "value": 1.2500},
            ],
        },
        "time_series_zscore": {
            "series_name": (
                "usd_tips_gbp_linker_10y_xc_real_yield_spread_zscore"
            ),
            "units": "z_score",
            "description": "Test rolling z-score series.",
            "rows": [
                {"date": "2026-04-07", "value": 0.62},
                {"date": "2026-04-08", "value": 0.65},
            ],
        },
    }


def _assert_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_cross_country_real_yield_spread_simple called "
        "without `config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == (
        "calculate_cross_country_real_yield_spread_simple_tool"
    ), (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'calculate_cross_country_real_yield_spread_simple_tool' "
        "(catches imports of the wrong tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_field_name" in cfg.conventions


# ===========================================================================
# rates_agent/inflation_indexed_bonds/mcp_server.py
# ===========================================================================

class TestMcpCrossCountryRealYieldSpreadToolWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cross_country_real_yield_spread_simple",
            return_value=_well_formed_output(),
        ) as mock_fn:
            output_json = mcp_module.calculate_cross_country_real_yield_spread_simple_tool(
                first_curve_family="USD_TIPS",
                second_curve_family="GBP_LINKER",
                tenor="10Y",
                lookback_days=365,
                field_name="YLD_YTM_MID",
            )
        assert mock_fn.call_count == 1
        _assert_config_passed(mock_fn.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_field_name_default_is_empty_string_sentinel(self):
        """The MCP wrapper's field_name default must be the empty-
        string sentinel, NOT 'YLD_YTM_MID'.  Hardcoding any other
        string shadows the YAML's default_field_name."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(
            mcp_module.calculate_cross_country_real_yield_spread_simple_tool,
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
            "calculate_cross_country_real_yield_spread_simple",
            return_value=_well_formed_output(),
        ) as mock_fn:
            mcp_module.calculate_cross_country_real_yield_spread_simple_tool(
                first_curve_family="USD_TIPS",
                second_curve_family="GBP_LINKER",
                tenor="10Y",
                lookback_days=365,
                # field_name omitted → defaults to ""
            )
        params = mock_fn.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach "
            f"CrossCountryRealYieldSpreadSimpleInput as None "
            "(sentinel for 'use YAML default_field_name'), got "
            f"{params.field_name!r}"
        )

    def test_explicit_field_name_passes_through(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cross_country_real_yield_spread_simple",
            return_value=_well_formed_output(),
        ) as mock_fn:
            mcp_module.calculate_cross_country_real_yield_spread_simple_tool(
                first_curve_family="USD_TIPS",
                second_curve_family="GBP_LINKER",
                tenor="10Y",
                field_name="YLD_YTM_BID",
            )
        params = mock_fn.call_args.kwargs["params"]
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
            "calculate_cross_country_real_yield_spread_simple",
            return_value=_well_formed_output(),
        ):
            output_json = mcp_module.calculate_cross_country_real_yield_spread_simple_tool(
                first_curve_family="USD_TIPS",
                second_curve_family="GBP_LINKER",
                tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        assert "time_series" not in parsed
        assert "time_series_spread" not in parsed
        assert "time_series_zscore" not in parsed
        # methodology_label MUST survive the strip.
        assert "methodology_label" in parsed["current_metrics"]
        # Reference identities MUST survive.
        assert parsed["current_metrics"]["first_curve_country"] == "US"
        assert parsed["current_metrics"]["second_curve_country"] == "UK"

    def test_docstring_advertises_only_live_linker_pairs(self):
        """MCP docstring must advertise only live cross-country
        pairs in the playbook universe.  No ghost markets (e.g.
        DE_BUND, IT_BTP, JPY_LINKER) should appear."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        doc = (
            mcp_module
            .calculate_cross_country_real_yield_spread_simple_tool
            .__doc__
            or ""
        )
        # Live linker curves (per
        # rates_agent/playbooks/inflation_indexed_bonds.yml).
        for live in ("USD_TIPS", "GBP_LINKER", "EUR_FR_LINKER", "CAD_RRB"):
            assert live in doc, (
                f"Live linker curve_family {live!r} missing from MCP "
                "docstring — desk users won't know which pairs to "
                "call."
            )
        # Ghost markets that the playbook does NOT yet ingest as
        # inflation linkers must NOT be advertised.
        for ghost in (
            "EUR_DE_LINKER",   # Germany — not ingested as linker
            "EUR_IT_LINKER",   # Italy — not ingested as linker
            "JPY_LINKER",      # Japan — not ingested
            "AUD_LINKER",      # Australia — not ingested
        ):
            assert ghost not in doc, (
                f"Ghost market {ghost!r} mentioned in MCP docstring "
                "but it is NOT in "
                "rates_agent/playbooks/inflation_indexed_bonds.yml's "
                "linker universe."
            )


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_xcry_config(self):
        cfg = load_tool_config(
            CROSS_COUNTRY_REAL_YIELD_SPREAD_SIMPLE_CONFIG_PATH,
        )
        assert cfg.tool.name == (
            "calculate_cross_country_real_yield_spread_simple_tool"
        )
        assert cfg.tool.domain == "inflation_indexed_bonds"


# ===========================================================================
# Response-model validation
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple.schemas import (
            CrossCountryRealYieldSpreadSimpleOutput,
        )
        validated = (
            CrossCountryRealYieldSpreadSimpleOutput.model_validate(
                _well_formed_output(),
            )
        )
        assert validated.time_series_spread.units.value == "percent"
        assert validated.time_series_zscore.units.value == "z_score"
        assert validated.current_metrics.first_curve_country == "US"
        assert validated.current_metrics.second_curve_country == "UK"

    def test_response_model_requires_canonical_series(self):
        from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple.schemas import (
            CrossCountryRealYieldSpreadSimpleOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad.pop("time_series_spread")
        with pytest.raises(ValidationError):
            CrossCountryRealYieldSpreadSimpleOutput.model_validate(bad)

    def test_response_model_requires_methodology_label(self):
        from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple.schemas import (
            CrossCountryRealYieldSpreadSimpleOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad["current_metrics"].pop("methodology_label")
        with pytest.raises(ValidationError):
            CrossCountryRealYieldSpreadSimpleOutput.model_validate(bad)

    def test_response_model_requires_country_currency_both_legs(self):
        """Reference identities are load-bearing — making any of
        them optional would let a future regression silently drop
        the resolved linker identity from the wire."""
        from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple.schemas import (
            CrossCountryRealYieldSpreadSimpleOutput,
        )
        from pydantic import ValidationError
        for required_field in (
            "first_curve_country",
            "first_curve_currency",
            "second_curve_country",
            "second_curve_currency",
        ):
            bad = _well_formed_output()
            bad["current_metrics"].pop(required_field)
            with pytest.raises(ValidationError):
                CrossCountryRealYieldSpreadSimpleOutput.model_validate(bad)

    def test_round_trip_preserves_canonical_fields(self):
        from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple.schemas import (
            CrossCountryRealYieldSpreadSimpleOutput,
        )
        validated = (
            CrossCountryRealYieldSpreadSimpleOutput.model_validate(
                _well_formed_output(),
            )
        )
        dumped = validated.model_dump()
        assert dumped["time_series_spread"]["units"] == "percent"
        assert dumped["time_series_zscore"]["units"] == "z_score"
        assert dumped["current_metrics"]["methodology_label"]
        assert dumped["current_metrics"]["tenor_years"] == 10.0


# ===========================================================================
# Workflow PrimitiveSpec registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_resolver_returns_xcry_spec(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_cross_country_real_yield_spread_simple_tool",
        )
        assert (
            spec.tool_name
            == "calculate_cross_country_real_yield_spread_simple_tool"
        )
        assert (
            spec.config_path
            == CROSS_COUNTRY_REAL_YIELD_SPREAD_SIMPLE_CONFIG_PATH
        )

    def test_spec_uses_correct_input_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple import (
            CrossCountryRealYieldSpreadSimpleInput,
            CrossCountryRealYieldSpreadSimpleOutput,
        )
        spec = rates_primitive_resolver(
            "calculate_cross_country_real_yield_spread_simple_tool",
        )
        assert spec.input_class is CrossCountryRealYieldSpreadSimpleInput
        assert spec.output_class is CrossCountryRealYieldSpreadSimpleOutput

    def test_spec_uses_correct_callable(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple import (
            calculate_cross_country_real_yield_spread_simple,
        )
        spec = rates_primitive_resolver(
            "calculate_cross_country_real_yield_spread_simple_tool",
        )
        assert (
            spec.callable
            is calculate_cross_country_real_yield_spread_simple
        )

    def test_output_field_units_declared_honestly(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_cross_country_real_yield_spread_simple_tool",
        )
        # Bespoke time_series carries spread_pct → PERCENT;
        # canonical spread series is PERCENT (NOT bps, real yields
        # aren't multiplied by 100); canonical z-score is Z_SCORE.
        assert spec.output_field_units == {
            "time_series": "percent",
            "time_series_spread": "percent",
            "time_series_zscore": "z_score",
        }

    def test_known_rates_primitives_includes_xcry(self):
        from rates_agent.workflows import known_rates_primitives
        assert (
            "calculate_cross_country_real_yield_spread_simple_tool"
            in known_rates_primitives()
        )


# ===========================================================================
# Controlled-error envelope surface
# ===========================================================================

class TestMcpControlledErrorSurface:
    """The MCP wrapper must surface controlled-error envelopes from
    the underlying tool — without this the LLM hitting the tool
    with a non-linker curve_family / same-curve / unknown-pillar
    would receive a fabricated value or a stack trace.
    """

    def test_missing_leg_surfaces_error_envelope(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        controlled_error = {
            "error": (
                "Cross-country real-yield spread (USD_TIPS vs "
                "GBP_LINKER @ 10Y) could not be computed: "
                "first_curve leg failed: No linker real-yield "
                "data found for curve_family='USD_TIPS', "
                "tenor='10Y' "
                "(instrument_type='inflation_linker')."
            )
        }
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cross_country_real_yield_spread_simple",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_cross_country_real_yield_spread_simple_tool(
                first_curve_family="USD_TIPS",
                second_curve_family="GBP_LINKER",
                tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "first_curve leg failed" in parsed["error"]
        assert "inflation_linker" in parsed["error"]

    def test_non_linker_curve_family_surfaces_controlled_error_envelope(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        controlled_error = {
            "error": (
                "Could not resolve country/currency for the "
                "first_curve_family='UST' "
                "(instrument_type='inflation_linker').  No "
                "instrument_master rows found for "
                "curve_family='UST', "
                "instrument_type='inflation_linker'.  If you "
                "passed a nominal sovereign curve_family (e.g. "
                "'UST', 'DE_BUND'), use the sovereign_bonds "
                "calculate_cross_market_spread_tool instead — this "
                "tool only operates on inflation-linker rows on "
                "BOTH legs."
            )
        }
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cross_country_real_yield_spread_simple",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_cross_country_real_yield_spread_simple_tool(
                first_curve_family="UST",
                second_curve_family="USD_TIPS",
                tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "inflation_linker" in parsed["error"]
        assert "sovereign" in parsed["error"].lower()

    def test_same_curve_surfaces_validation_error(self):
        """Same curve_family on both legs must hit the Pydantic
        validator and emerge as a controlled error envelope."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.calculate_cross_country_real_yield_spread_simple_tool(
                first_curve_family="USD_TIPS",
                second_curve_family="USD_TIPS",
                tenor="10Y",
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
    hardcoded Python literal.
    """

    def test_bundled_yaml_methodology_what_it_does_is_nonempty(self):
        cfg = load_tool_config(
            CROSS_COUNTRY_REAL_YIELD_SPREAD_SIMPLE_CONFIG_PATH,
        )
        assert cfg.methodology.what_it_does.strip()
        what = cfg.methodology.what_it_does.lower()
        # Spread formula + caveats must be present.
        assert "first_curve_real_yield_pct" in what
        assert "second_curve_real_yield_pct" in what
        assert "get_real_yield_level" in what
        # Caveats from methodology_guardrails.
        assert "index-family" in what or "index family" in what
        assert "market-structure" in what or "market structure" in what
