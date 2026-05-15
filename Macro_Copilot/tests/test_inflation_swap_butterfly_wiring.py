"""
test_inflation_swap_butterfly_wiring.py — Caller-wiring smoke tests
for the inflation_swap_butterfly primitive.

Mirrors ``test_inflation_swap_curve_spread_wiring.py`` (the closest
sibling — same domain, same MCP shape) scoped to
inflation_swap_butterfly's external callers:

  1. rates_agent/inflation_swaps/mcp_server.py
     ::calculate_inflation_swap_butterfly_tool
  2. rates_agent/workflows/__init__.py PrimitiveSpec registration

Key load-bearing properties under test:
  - The MCP wrapper passes config explicitly (no auto-load fallback).
  - The MCP wrapper uses the empty-string / None-sentinel pattern
    so the YAML ``default_zcis_rate_field`` actually flows through.
  - The MCP wrapper surfaces the controlled error envelope for the
    no-row / non-ZCIS-curve / inverted-tenor / duplicate-tenor cases.
  - The hub re-export and CONFIG_PATH public symbol are stable.
  - The wire-frozen + canonical TimeSeries fields survive a full
    round-trip through the response model.
  - The workflow registry exposes the primitive with the correct
    ``output_field_units`` declarations (BPS for time_series and
    time_series_butterfly, Z_SCORE for time_series_zscore).
  - The methodology_label is present on the MCP-level output.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.inflation_swaps.tools.inflation_swap_butterfly import (
    CONFIG_PATH as INFLATION_SWAP_BUTTERFLY_CONFIG_PATH,
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
            "curve_family": "USD_ZCIS",
            "short_tenor": "5Y",
            "belly_tenor": "10Y",
            "long_tenor": "30Y",
            "butterfly_label": "USD_ZCIS 5s10s30s",
            "current_butterfly_bps": -7.50,
            "daily_change_bps": 0.50,
            "weekly_change_bps": 2.00,
            "monthly_change_bps": -1.00,
            "current_z_score": -0.45,
            "rolling_window_days": 252,
            "high_252d_bps": 25.00,
            "low_252d_bps": -30.00,
            "percentile_252d": 45.0,
            "wing_short_bps": 25.00,
            "wing_long_bps": 10.00,
            "short_zcis_rate_pct": 2.4500,
            "belly_zcis_rate_pct": 2.7000,
            "long_zcis_rate_pct": 2.8000,
            "short_years": 5.0,
            "belly_years": 10.0,
            "long_years": 30.0,
            "observation_count": 252,
            "inflation_index_family": "US_CPI_URBAN",
            "index_lag": "3M",
            "interpolation": "Daily",
            "underlying_index": "CPURNSA Index",
            "methodology_label": (
                "Same-curve ZCIS butterfly; (belly_zcis_pct - "
                "0.5*(short_zcis_pct + long_zcis_pct)) * 100; "
                "raw inflation-swap-rate space; weight tuple "
                "(-0.5, +1.0, -0.5)."
            ),
        },
        "time_series": [
            {"date": "2026-04-07", "butterfly_bps": -8.00, "z_score": -0.50},
            {"date": "2026-04-08", "butterfly_bps": -7.50, "z_score": -0.45},
        ],
        "time_series_butterfly": {
            "series_name": "usd_zcis_5y_10y_30y_inflation_swap_butterfly",
            "units": "bps",
            "description": "Test ZCIS butterfly series.",
            "rows": [
                {"date": "2026-04-07", "value": -8.00},
                {"date": "2026-04-08", "value": -7.50},
            ],
        },
        "time_series_zscore": {
            "series_name": (
                "usd_zcis_5y_10y_30y_inflation_swap_butterfly_zscore"
            ),
            "units": "z_score",
            "description": "Test ZCIS butterfly z-score series.",
            "rows": [
                {"date": "2026-04-07", "value": -0.50},
                {"date": "2026-04-08", "value": -0.45},
            ],
        },
    }


def _assert_butterfly_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_inflation_swap_butterfly called without `config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "inflation_swap_butterfly", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'inflation_swap_butterfly' (catches imports of the wrong "
        "tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_zcis_rate_field" in cfg.conventions


# ===========================================================================
# rates_agent/inflation_swaps/mcp_server.py
# ===========================================================================

class TestMcpInflationSwapButterflyToolWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_inflation_swap_butterfly",
            return_value=_well_formed_butterfly_output(),
        ) as mock_isb:
            output_json = mcp_module.calculate_inflation_swap_butterfly_tool(
                curve_family="USD_ZCIS",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
                lookback_days=365,
                field_name="PX_MID",
            )
        assert mock_isb.call_count == 1
        _assert_butterfly_config_passed(mock_isb.call_args)
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
            mcp_module.calculate_inflation_swap_butterfly_tool,
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
            "calculate_inflation_swap_butterfly",
            return_value=_well_formed_butterfly_output(),
        ) as mock_isb:
            mcp_module.calculate_inflation_swap_butterfly_tool(
                curve_family="USD_ZCIS",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
                lookback_days=365,
                # field_name omitted → defaults to ""
            )
        params = mock_isb.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach "
            f"InflationSwapButterflyInput as None (sentinel for "
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
            "calculate_inflation_swap_butterfly",
            return_value=_well_formed_butterfly_output(),
        ) as mock_isb:
            mcp_module.calculate_inflation_swap_butterfly_tool(
                curve_family="USD_ZCIS",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
                field_name="PX_BID",
            )
        params = mock_isb.call_args.kwargs["params"]
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
            "calculate_inflation_swap_butterfly",
            return_value=_well_formed_butterfly_output(),
        ):
            output_json = mcp_module.calculate_inflation_swap_butterfly_tool(
                curve_family="USD_ZCIS",
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
        # Reference metadata MUST survive — the desk needs it to
        # interpret the butterfly.
        assert (
            parsed["current_metrics"]["inflation_index_family"]
            == "US_CPI_URBAN"
        )

    def test_mcp_docstring_advertises_live_curve_families_only(self):
        """The MCP wrapper's docstring must only mention live ZCIS
        curve families and tenors from the inflation_swaps playbook
        (USD_ZCIS / EUR_ZCIS / GBP_ZCIS at 1Y / 2Y / 3Y / 5Y / 10Y /
        20Y / 30Y).  No ghost markets."""
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        doc = mcp_module.calculate_inflation_swap_butterfly_tool.__doc__
        assert doc is not None
        # Live curve families
        assert "USD_ZCIS" in doc
        assert "EUR_ZCIS" in doc
        assert "GBP_ZCIS" in doc
        # No ghost curve families (e.g. CAD_ZCIS, AUD_ZCIS, JPY_ZCIS)
        for ghost in ("CAD_ZCIS", "AUD_ZCIS", "JPY_ZCIS"):
            assert ghost not in doc, (
                f"MCP docstring advertises ghost market {ghost!r} — "
                "remove or implement coverage in the playbook."
            )
        # Methodology disclosure present
        assert "FIXED simple-butterfly" in doc or "weight tuple" in doc
        assert "(-0.5, +1.0, -0.5)" in doc

    def test_methodology_label_present_in_mcp_output(self):
        """The MCP wrapper must return current_metrics including
        methodology_label so the LLM can quote the disclosure."""
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_inflation_swap_butterfly",
            return_value=_well_formed_butterfly_output(),
        ):
            output_json = mcp_module.calculate_inflation_swap_butterfly_tool(
                curve_family="USD_ZCIS",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
            )
        parsed = json.loads(output_json)
        label = parsed["current_metrics"]["methodology_label"]
        assert label
        assert "raw inflation-swap-rate space" in label.lower() or "raw" in label.lower()


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_butterfly import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.inflation_swaps.tools.inflation_swap_butterfly.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_butterfly_config(self):
        cfg = load_tool_config(INFLATION_SWAP_BUTTERFLY_CONFIG_PATH)
        assert cfg.tool.name == "inflation_swap_butterfly"
        assert cfg.tool.domain == "inflation_swaps"


# ===========================================================================
# Response-model validation
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_butterfly.schemas import (
            InflationSwapButterflyOutput,
        )
        validated = InflationSwapButterflyOutput.model_validate(
            _well_formed_butterfly_output(),
        )
        assert validated.time_series_butterfly.units.value == "bps"
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

    def test_response_model_requires_canonical_butterfly_series(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_butterfly.schemas import (
            InflationSwapButterflyOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_butterfly_output()
        bad.pop("time_series_butterfly")
        with pytest.raises(ValidationError):
            InflationSwapButterflyOutput.model_validate(bad)

    def test_response_model_requires_methodology_label(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_butterfly.schemas import (
            InflationSwapButterflyOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_butterfly_output()
        bad["current_metrics"].pop("methodology_label")
        with pytest.raises(ValidationError):
            InflationSwapButterflyOutput.model_validate(bad)

    def test_response_model_requires_reference_metadata(self):
        """inflation_index_family / index_lag / interpolation are
        load-bearing wire fields — making them optional would let a
        future regression silently drop the cross-curve
        comparability caveat from the wire.
        """
        from rates_agent.inflation_swaps.tools.inflation_swap_butterfly.schemas import (
            InflationSwapButterflyOutput,
        )
        from pydantic import ValidationError
        for required_field in (
            "inflation_index_family", "index_lag", "interpolation",
        ):
            bad = _well_formed_butterfly_output()
            bad["current_metrics"].pop(required_field)
            with pytest.raises(ValidationError):
                InflationSwapButterflyOutput.model_validate(bad)

    def test_round_trip_preserves_canonical_fields(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_butterfly.schemas import (
            InflationSwapButterflyOutput,
        )
        validated = InflationSwapButterflyOutput.model_validate(
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
            "calculate_inflation_swap_butterfly_tool",
        )
        assert (
            spec.tool_name
            == "calculate_inflation_swap_butterfly_tool"
        )
        assert (
            spec.config_path
            == INFLATION_SWAP_BUTTERFLY_CONFIG_PATH
        )

    def test_spec_uses_correct_input_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_swaps.tools.inflation_swap_butterfly import (
            InflationSwapButterflyInput,
            InflationSwapButterflyOutput,
        )
        spec = rates_primitive_resolver(
            "calculate_inflation_swap_butterfly_tool",
        )
        assert spec.input_class is InflationSwapButterflyInput
        assert spec.output_class is InflationSwapButterflyOutput

    def test_spec_uses_correct_callable(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_swaps.tools.inflation_swap_butterfly import (
            calculate_inflation_swap_butterfly,
        )
        spec = rates_primitive_resolver(
            "calculate_inflation_swap_butterfly_tool",
        )
        assert spec.callable is calculate_inflation_swap_butterfly

    def test_output_field_units_declared_honestly(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_inflation_swap_butterfly_tool",
        )
        # Bespoke time_series row list carries butterfly_bps, so
        # BPS; canonical butterfly series is BPS; canonical z-score
        # series is Z_SCORE.
        assert spec.output_field_units == {
            "time_series": "bps",
            "time_series_butterfly": "bps",
            "time_series_zscore": "z_score",
        }

    def test_known_rates_primitives_includes_butterfly(self):
        from rates_agent.workflows import known_rates_primitives
        assert (
            "calculate_inflation_swap_butterfly_tool"
            in known_rates_primitives()
        )


# ===========================================================================
# Controlled-error envelope surface
# ===========================================================================

class TestMcpControlledErrorSurface:
    """The MCP wrapper must surface controlled-error envelopes from
    the underlying tool — without this the LLM hitting the
    butterfly tool with a non-ZCIS curve_family / inverted tenor
    would receive a fabricated value or a stack trace.
    """

    def test_missing_endpoint_surfaces_error_envelope(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        controlled_error = {
            "error": (
                "ZCIS butterfly 5Y10Y30Y could not be computed for "
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
            "calculate_inflation_swap_butterfly",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_inflation_swap_butterfly_tool(
                curve_family="USD_ZCIS",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "short-tenor" in parsed["error"].lower()
        assert "5Y" in parsed["error"]
        # Inner-error rationale must travel back to the LLM.
        assert "inflation_swap" in parsed["error"]

    def test_non_zcis_curve_family_surfaces_controlled_error_envelope(
        self,
    ):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        controlled_error = {
            "error": (
                "ZCIS butterfly 5Y10Y30Y could not be computed for "
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
            "calculate_inflation_swap_butterfly",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_inflation_swap_butterfly_tool(
                curve_family="UST",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "inflation_swap" in parsed["error"]
        assert "zero_coupon_breakeven" in parsed["error"]

    def test_inverted_tenor_triplet_surfaces_validation_error(self):
        """Inverted curve (long_tenor < belly_tenor < short_tenor)
        must hit the Pydantic validator and emerge as a controlled
        error envelope."""
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.calculate_inflation_swap_butterfly_tool(
                curve_family="USD_ZCIS",
                short_tenor="30Y",
                belly_tenor="10Y",
                long_tenor="5Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_duplicate_tenor_surfaces_validation_error(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.calculate_inflation_swap_butterfly_tool(
                curve_family="USD_ZCIS",
                short_tenor="5Y",
                belly_tenor="5Y",
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
    hardcoded Python literal.
    """

    def test_bundled_yaml_methodology_what_it_does_is_nonempty(self):
        cfg = load_tool_config(INFLATION_SWAP_BUTTERFLY_CONFIG_PATH)
        assert cfg.methodology.what_it_does.strip()
        # Pin the load-bearing detail: butterfly formula + raw-rate
        # caveat + weight tuple.
        what = cfg.methodology.what_it_does.lower()
        assert "belly_zcis_pct" in what
        assert "short_zcis_pct" in what
        assert "long_zcis_pct" in what
        assert "(-0.5, +1.0, -0.5)" in what
        assert "calculate_inflation_swap_rate_level" in what
        # Raw-rate-space promise.
        assert "raw inflation-swap-rate space" in what or "raw inflation" in what
