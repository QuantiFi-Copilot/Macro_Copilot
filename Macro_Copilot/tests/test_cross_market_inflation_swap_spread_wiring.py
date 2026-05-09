"""
test_cross_market_inflation_swap_spread_wiring.py — Caller-wiring
smoke tests for the same-tenor cross-market ZCIS spread primitive.

Mirrors ``test_inflation_swap_curve_spread_wiring.py`` (the closest
sibling) scoped to cross_market_inflation_swap_spread's external
callers:

  1. rates_agent/inflation_swaps/mcp_server.py
     ::calculate_cross_market_inflation_swap_spread_tool
  2. rates_agent/workflows/__init__.py PrimitiveSpec registration

Key load-bearing properties under test:
  - The MCP wrapper passes config explicitly (no auto-load
    fallback).
  - The MCP wrapper surfaces the controlled error envelope for the
    no-row / non-ZCIS-curve / same-curve / unparseable-tenor cases.
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

from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread import (
    CONFIG_PATH as CROSS_MARKET_INFLATION_SWAP_SPREAD_CONFIG_PATH,
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


def _well_formed_cross_market_output() -> dict:
    """Mock matches the wire shape: snapshot ``current_metrics``
    PLUS bespoke ``time_series`` row list PLUS the two canonical
    TimeSeries payloads.
    """
    return {
        "current_metrics": {
            "as_of_date": "2026-04-08",
            "leg_a_curve_family": "USD_ZCIS",
            "leg_b_curve_family": "EUR_ZCIS",
            "tenor": "5Y",
            "tenor_years": 5.0,
            "spread_label": "USD_ZCIS-EUR_ZCIS 5Y",
            "spread_pct": 0.30,
            "spread_bps": 30.00,
            "change_1d_bps": 1.50,
            "change_1w_bps": 4.00,
            "change_1m_bps": -2.00,
            "z_score_252d": 0.65,
            "high_252d_bps": 60.00,
            "low_252d_bps": -10.00,
            "percentile_252d": 50.0,
            "leg_a_pct": 2.5000,
            "leg_b_pct": 2.2000,
            "observation_count": 252,
            "leg_a_inflation_index_family": "US_CPI_URBAN",
            "leg_b_inflation_index_family": "EU_HICP",
            "index_families_match": False,
            "index_family_caveat": (
                "Cross-market spread mixes inflation-compensation "
                "regimes: leg_a references 'US_CPI_URBAN' while "
                "leg_b references 'EU_HICP', so this spread captures "
                "BOTH inflation-expectation differentials AND "
                "structural index-family differences — it is NOT a "
                "clean expected-inflation divergence."
            ),
            "leg_a_index_lag": "3M",
            "leg_b_index_lag": "3M",
            "leg_a_interpolation": "Daily",
            "leg_b_interpolation": "Monthly",
            "leg_a_underlying_index": "CPURNSA Index",
            "leg_b_underlying_index": "CPTFEMU Index",
            "methodology_label": (
                "Same-tenor cross-market ZCIS spread; "
                "(leg_a_pct - leg_b_pct); "
                "USD_ZCIS-EUR_ZCIS 5Y."
            ),
        },
        "time_series": [
            {
                "date": "2026-04-07",
                "spread_pct": 0.28,
                "spread_bps": 28.00,
                "leg_a_pct": 2.4800,
                "leg_b_pct": 2.2000,
            },
            {
                "date": "2026-04-08",
                "spread_pct": 0.30,
                "spread_bps": 30.00,
                "leg_a_pct": 2.5000,
                "leg_b_pct": 2.2000,
            },
        ],
        "time_series_spread": {
            "series_name": "usd_zcis_eur_zcis_5y_zcis_cross_market_spread",
            "units": "bps",
            "description": "Test cross-market ZCIS spread series.",
            "rows": [
                {"date": "2026-04-07", "value": 28.00},
                {"date": "2026-04-08", "value": 30.00},
            ],
        },
        "time_series_zscore": {
            "series_name": "usd_zcis_eur_zcis_5y_zcis_cross_market_spread_zscore",
            "units": "z_score",
            "description": "Test cross-market ZCIS spread z-score series.",
            "rows": [
                {"date": "2026-04-07", "value": 0.62},
                {"date": "2026-04-08", "value": 0.65},
            ],
        },
    }


def _assert_cross_market_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_cross_market_inflation_swap_spread called without "
        "`config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "cross_market_inflation_swap_spread", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'cross_market_inflation_swap_spread' (catches imports of the "
        "wrong tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_zcis_rate_field" in cfg.conventions
    assert "cross_market_sign_convention" in cfg.conventions


# ===========================================================================
# rates_agent/inflation_swaps/mcp_server.py
# ===========================================================================

class TestMcpCrossMarketInflationSwapSpreadToolWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cross_market_inflation_swap_spread",
            return_value=_well_formed_cross_market_output(),
        ) as mock_cmiss:
            output_json = mcp_module.calculate_cross_market_inflation_swap_spread_tool(
                leg_a_curve_family="USD_ZCIS",
                leg_b_curve_family="EUR_ZCIS",
                tenor="5Y",
                lookback_days=365,
            )
        assert mock_cmiss.call_count == 1
        _assert_cross_market_config_passed(mock_cmiss.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_field_name_parameter_present_on_mcp_wrapper(self):
        """The MCP wrapper exposes ``field_name: str = ""`` so callers
        can override per-call.  The empty-string default mirrors the
        sentinel pattern used by sibling rates tools so the YAML's
        ``default_zcis_rate_field`` resolves through compute when the
        caller does not override.
        """
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(
            mcp_module.calculate_cross_market_inflation_swap_spread_tool,
        )
        assert "field_name" in sig.parameters
        assert sig.parameters["field_name"].default == ""

    def test_explicit_field_name_forwarded_to_input_schema(self):
        """An explicit ``field_name="PX_LAST"`` from the MCP wrapper
        must reach the schema and the underlying compute call.
        """
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread.schemas import (
            CrossMarketInflationSwapSpreadInput,
        )

        captured: dict = {}

        def _fake_calculate(*, engine, params, config):
            captured["params"] = params
            return _well_formed_cross_market_output()

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cross_market_inflation_swap_spread",
            side_effect=_fake_calculate,
        ):
            mcp_module.calculate_cross_market_inflation_swap_spread_tool(
                leg_a_curve_family="USD_ZCIS",
                leg_b_curve_family="EUR_ZCIS",
                tenor="5Y",
                lookback_days=365,
                field_name="PX_LAST",
            )
        assert isinstance(
            captured["params"], CrossMarketInflationSwapSpreadInput,
        )
        assert captured["params"].field_name == "PX_LAST"

    def test_empty_string_field_name_coerced_to_none(self):
        """The MCP wrapper translates the empty-string sentinel to
        None before constructing the input.  The schema's own
        validator would also coerce, but verifying at the wrapper
        layer pins the wire-level sentinel translation.
        """
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        captured: dict = {}

        def _fake_calculate(*, engine, params, config):
            captured["params"] = params
            return _well_formed_cross_market_output()

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cross_market_inflation_swap_spread",
            side_effect=_fake_calculate,
        ):
            mcp_module.calculate_cross_market_inflation_swap_spread_tool(
                leg_a_curve_family="USD_ZCIS",
                leg_b_curve_family="EUR_ZCIS",
                tenor="5Y",
                lookback_days=365,
                field_name="",
            )
        assert captured["params"].field_name is None

    def test_time_series_stripped_from_llm_response(self):
        """The MCP wrapper must NOT serialize the bespoke or canonical
        TimeSeries payloads back to the LLM."""
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cross_market_inflation_swap_spread",
            return_value=_well_formed_cross_market_output(),
        ):
            output_json = mcp_module.calculate_cross_market_inflation_swap_spread_tool(
                leg_a_curve_family="USD_ZCIS",
                leg_b_curve_family="EUR_ZCIS",
                tenor="5Y",
            )
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        assert "time_series" not in parsed
        assert "time_series_spread" not in parsed
        assert "time_series_zscore" not in parsed
        # methodology_label MUST survive the strip — it carries the
        # load-bearing index-family caveat.
        assert "methodology_label" in parsed["current_metrics"]
        # Per-leg reference metadata MUST survive — the desk needs
        # both legs' index_family / lag / interpolation to interpret
        # the spread.
        assert (
            parsed["current_metrics"]["leg_a_inflation_index_family"]
            == "US_CPI_URBAN"
        )
        assert (
            parsed["current_metrics"]["leg_b_inflation_index_family"]
            == "EU_HICP"
        )


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_cross_market_config(self):
        cfg = load_tool_config(
            CROSS_MARKET_INFLATION_SWAP_SPREAD_CONFIG_PATH,
        )
        assert cfg.tool.name == "cross_market_inflation_swap_spread"
        assert cfg.tool.domain == "inflation_swaps"


# ===========================================================================
# Response-model validation
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread.schemas import (
            CrossMarketInflationSwapSpreadOutput,
        )
        validated = CrossMarketInflationSwapSpreadOutput.model_validate(
            _well_formed_cross_market_output(),
        )
        assert validated.time_series_spread.units.value == "bps"
        assert validated.time_series_zscore.units.value == "z_score"
        # Per-leg reference metadata threads through validation
        # cleanly.
        assert (
            validated.current_metrics.leg_a_inflation_index_family
            == "US_CPI_URBAN"
        )
        assert (
            validated.current_metrics.leg_b_inflation_index_family
            == "EU_HICP"
        )
        assert validated.current_metrics.leg_a_index_lag == "3M"
        assert validated.current_metrics.leg_b_index_lag == "3M"
        assert validated.current_metrics.leg_a_interpolation == "Daily"
        assert validated.current_metrics.leg_b_interpolation == "Monthly"

    def test_response_model_requires_canonical_series(self):
        from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread.schemas import (
            CrossMarketInflationSwapSpreadOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_cross_market_output()
        bad.pop("time_series_spread")
        with pytest.raises(ValidationError):
            CrossMarketInflationSwapSpreadOutput.model_validate(bad)

    def test_response_model_requires_methodology_label(self):
        from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread.schemas import (
            CrossMarketInflationSwapSpreadOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_cross_market_output()
        bad["current_metrics"].pop("methodology_label")
        with pytest.raises(ValidationError):
            CrossMarketInflationSwapSpreadOutput.model_validate(bad)

    def test_response_model_requires_derived_index_family_summary(self):
        """``index_families_match`` is a required derived top-level
        summary field on current_metrics — making it optional would
        let a future regression silently drop the load-bearing
        index-family caveat from the wire.  ``index_family_caveat``
        is Optional (None when families match) and is therefore not
        required.
        """
        from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread.schemas import (
            CrossMarketInflationSwapSpreadOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_cross_market_output()
        bad["current_metrics"].pop("index_families_match")
        with pytest.raises(ValidationError):
            CrossMarketInflationSwapSpreadOutput.model_validate(bad)

    def test_response_model_requires_per_leg_reference_metadata(self):
        """Per-leg ``inflation_index_family`` / ``index_lag`` /
        ``interpolation`` are load-bearing wire fields — making them
        optional would let a future regression silently drop the
        index-family caveat from the wire.
        """
        from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread.schemas import (
            CrossMarketInflationSwapSpreadOutput,
        )
        from pydantic import ValidationError
        for required_field in (
            "leg_a_inflation_index_family",
            "leg_b_inflation_index_family",
            "leg_a_index_lag",
            "leg_b_index_lag",
            "leg_a_interpolation",
            "leg_b_interpolation",
        ):
            bad = _well_formed_cross_market_output()
            bad["current_metrics"].pop(required_field)
            with pytest.raises(ValidationError):
                CrossMarketInflationSwapSpreadOutput.model_validate(bad)

    def test_round_trip_preserves_canonical_fields(self):
        from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread.schemas import (
            CrossMarketInflationSwapSpreadOutput,
        )
        validated = CrossMarketInflationSwapSpreadOutput.model_validate(
            _well_formed_cross_market_output(),
        )
        dumped = validated.model_dump()
        assert dumped["time_series_spread"]["units"] == "bps"
        assert dumped["time_series_zscore"]["units"] == "z_score"
        assert dumped["current_metrics"]["methodology_label"]
        assert dumped["current_metrics"]["tenor_years"] == 5.0


# ===========================================================================
# Workflow PrimitiveSpec registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_resolver_returns_cross_market_spec(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_cross_market_inflation_swap_spread_tool",
        )
        assert (
            spec.tool_name
            == "calculate_cross_market_inflation_swap_spread_tool"
        )
        assert (
            spec.config_path
            == CROSS_MARKET_INFLATION_SWAP_SPREAD_CONFIG_PATH
        )

    def test_spec_uses_correct_input_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread import (
            CrossMarketInflationSwapSpreadInput,
            CrossMarketInflationSwapSpreadOutput,
        )
        spec = rates_primitive_resolver(
            "calculate_cross_market_inflation_swap_spread_tool",
        )
        assert spec.input_class is CrossMarketInflationSwapSpreadInput
        assert spec.output_class is CrossMarketInflationSwapSpreadOutput

    def test_spec_uses_correct_callable(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread import (
            calculate_cross_market_inflation_swap_spread,
        )
        spec = rates_primitive_resolver(
            "calculate_cross_market_inflation_swap_spread_tool",
        )
        assert spec.callable is calculate_cross_market_inflation_swap_spread

    def test_output_field_units_declared_honestly(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_cross_market_inflation_swap_spread_tool",
        )
        # Bespoke time_series row list carries spread_bps, so BPS;
        # canonical spread series is BPS; canonical z-score series
        # is Z_SCORE.
        assert spec.output_field_units == {
            "time_series": "bps",
            "time_series_spread": "bps",
            "time_series_zscore": "z_score",
        }

    def test_known_rates_primitives_includes_cross_market(self):
        from rates_agent.workflows import known_rates_primitives
        assert (
            "calculate_cross_market_inflation_swap_spread_tool"
            in known_rates_primitives()
        )


# ===========================================================================
# Controlled-error envelope surface
# ===========================================================================

class TestMcpControlledErrorSurface:
    """The MCP wrapper must surface controlled-error envelopes from
    the underlying tool — without this the LLM hitting the cross-
    market tool with a non-ZCIS curve_family / same-curve / unparseable
    tenor would receive a fabricated value or a stack trace.
    """

    def test_missing_endpoint_surfaces_error_envelope(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        controlled_error = {
            "error": (
                "Cross-market ZCIS spread USD_ZCIS-EUR_ZCIS 5Y could "
                "not be computed: the leg_a (USD_ZCIS) endpoint "
                "failed.  Inner error: No ZCIS data found for "
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
            "calculate_cross_market_inflation_swap_spread",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_cross_market_inflation_swap_spread_tool(
                leg_a_curve_family="USD_ZCIS",
                leg_b_curve_family="EUR_ZCIS",
                tenor="5Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "leg_a" in parsed["error"].lower()
        assert "USD_ZCIS" in parsed["error"]
        # Inner-error rationale must travel back to the LLM so it
        # can route non-ZCIS questions elsewhere.
        assert "inflation_swap" in parsed["error"]

    def test_non_zcis_curve_family_surfaces_controlled_error_envelope(
        self,
    ):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        controlled_error = {
            "error": (
                "Cross-market ZCIS spread UST-EUR_ZCIS 5Y could not "
                "be computed: the leg_a (UST) endpoint failed.  "
                "Inner error: No ZCIS data found for curve_family="
                "'UST', tenor='5Y' (instrument_type='inflation_swap', "
                "pricing_type='zero_coupon_breakeven')."
            )
        }
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cross_market_inflation_swap_spread",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_cross_market_inflation_swap_spread_tool(
                leg_a_curve_family="UST",
                leg_b_curve_family="EUR_ZCIS",
                tenor="5Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "inflation_swap" in parsed["error"]
        assert "zero_coupon_breakeven" in parsed["error"]

    def test_same_curve_attempt_surfaces_validation_error(self):
        """leg_a == leg_b must hit the Pydantic validator and emerge
        as a controlled error envelope with the re-route hint to
        ``inflation_swap_curve_spread``.
        """
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.calculate_cross_market_inflation_swap_spread_tool(
                leg_a_curve_family="USD_ZCIS",
                leg_b_curve_family="USD_ZCIS",
                tenor="5Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_unparseable_tenor_surfaces_validation_error(self):
        """Unknown / unparseable tenor must hit the Pydantic
        validator and emerge as a controlled error envelope.
        """
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.calculate_cross_market_inflation_swap_spread_tool(
                leg_a_curve_family="USD_ZCIS",
                leg_b_curve_family="EUR_ZCIS",
                tenor="ABC",
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
    inflation_swap_curve_spread / breakeven_curve_spread.
    """

    def test_bundled_yaml_methodology_what_it_does_is_nonempty(self):
        cfg = load_tool_config(
            CROSS_MARKET_INFLATION_SWAP_SPREAD_CONFIG_PATH,
        )
        assert cfg.methodology.what_it_does.strip()
        # Pin the load-bearing detail: spread formula + cross-market
        # invariant + composition pattern + INDEX-FAMILY CAVEAT.
        what = cfg.methodology.what_it_does.lower()
        assert "leg_a_pct" in what
        assert "leg_b_pct" in what
        assert "calculate_inflation_swap_rate_level" in what
        # INDEX-FAMILY CAVEAT — load-bearing per the catalog's
        # methodology_guardrails.
        assert "us cpi" in what or "us_cpi" in what or "cpi-u" in what
        assert "hicp" in what
        assert "rpi" in what
