"""
test_swap_breakeven_basis_simple_wiring.py — Caller-wiring smoke
tests for the swap-breakeven basis primitive.

Mirrors ``test_cross_market_inflation_swap_spread_wiring.py`` (the
closest sibling spread primitive in the inflation_swaps domain).

Key load-bearing properties under test:
  - The MCP wrapper passes config explicitly (no auto-load
    fallback) and the config is the swap_breakeven_basis_simple
    bundled config (not the wrong tool's CONFIG_PATH).
  - The MCP wrapper exposes ``field_name: str = ""`` and forwards
    explicit / empty-string / default values correctly.
  - The MCP wrapper surfaces the controlled error envelope for
    missing-leg / unparseable-tenor / same-curve cases.
  - The hub re-export and CONFIG_PATH public symbol are stable.
  - The wire-frozen + canonical TimeSeries fields survive a full
    round-trip through the response model.
  - The workflow registry exposes the primitive with the correct
    ``output_field_units`` declarations (BPS for time_series and
    time_series_basis, Z_SCORE for time_series_zscore).
  - The response model requires the derived
    ``index_families_match`` boolean.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple import (
    CONFIG_PATH as SWAP_BREAKEVEN_BASIS_SIMPLE_CONFIG_PATH,
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


def _well_formed_basis_output() -> dict:
    """Mock matches the wire shape: snapshot ``current_metrics``
    PLUS bespoke ``time_series`` row list PLUS the two canonical
    TimeSeries payloads.
    """
    return {
        "current_metrics": {
            "as_of_date": "2026-04-08",
            "zcis_curve_family": "USD_ZCIS",
            "nominal_curve_family": "UST",
            "linker_curve_family": "USD_TIPS",
            "tenor": "10Y",
            "tenor_years": 10.0,
            "basis_label": (
                "USD_ZCIS - UST/USD_TIPS 10Y swap-breakeven basis"
            ),
            "basis_pct": 0.15,
            "basis_bps": 15.00,
            "zcis_pct": 2.5000,
            "breakeven_pct": 2.3500,
            "breakeven_bps": 235.00,
            "nominal_yield_pct": 4.2000,
            "real_yield_pct": 1.8500,
            "change_1d_bps": 0.50,
            "change_1w_bps": 1.00,
            "change_1m_bps": -2.00,
            "z_score_252d": 0.42,
            "high_252d_bps": 30.00,
            "low_252d_bps": -10.00,
            "percentile_252d": 50.0,
            "observation_count": 252,
            "zcis_inflation_index_family": "US_CPI_URBAN",
            "zcis_index_lag": "3M",
            "zcis_interpolation": "Daily",
            "zcis_underlying_index": "CPURNSA Index",
            "linker_inflation_index_family": None,
            "linker_index_lag": None,
            "index_families_match": False,
            "index_family_caveat": (
                "Linker leg's inflation_index_family is not "
                "surfaced by the breakeven primitive's current "
                "public wire — the basis primitive cannot directly "
                "verify whether the linker bond references the "
                "same inflation index as the ZCIS leg "
                "('US_CPI_URBAN').  This basis is NOT a clean "
                "liquidity-premium read regardless."
            ),
            "methodology_label": (
                "Swap-breakeven basis subtracts a generic linker-"
                "implied breakeven from the corresponding ZCIS rate "
                "at the same tenor; basis = zcis_pct - "
                "breakeven_pct; not a clean liquidity-premium read; "
                "reflects index-lag effects."
            ),
        },
        "time_series": [
            {
                "date": "2026-04-07",
                "basis_pct": 0.14,
                "basis_bps": 14.00,
                "zcis_pct": 2.4900,
                "breakeven_pct": 2.3500,
            },
            {
                "date": "2026-04-08",
                "basis_pct": 0.15,
                "basis_bps": 15.00,
                "zcis_pct": 2.5000,
                "breakeven_pct": 2.3500,
            },
        ],
        "time_series_basis": {
            "series_name": "usd_zcis_ust_usd_tips_10y_swap_breakeven_basis",
            "units": "bps",
            "description": "Test swap-breakeven basis series.",
            "rows": [
                {"date": "2026-04-07", "value": 14.00},
                {"date": "2026-04-08", "value": 15.00},
            ],
        },
        "time_series_zscore": {
            "series_name": "usd_zcis_ust_usd_tips_10y_swap_breakeven_basis_zscore",
            "units": "z_score",
            "description": "Test swap-breakeven basis z-score series.",
            "rows": [
                {"date": "2026-04-07", "value": 0.40},
                {"date": "2026-04-08", "value": 0.42},
            ],
        },
    }


def _assert_basis_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_swap_breakeven_basis_simple called without "
        "`config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "calculate_swap_breakeven_basis_simple_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'calculate_swap_breakeven_basis_simple_tool' (catches imports of the wrong "
        "tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_zcis_rate_field" in cfg.conventions
    assert "swap_breakeven_basis_sign_convention" in cfg.conventions


# ===========================================================================
# rates_agent/inflation_swaps/mcp_server.py
# ===========================================================================

class TestMcpSwapBreakevenBasisSimpleToolWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_swap_breakeven_basis_simple",
            return_value=_well_formed_basis_output(),
        ) as mock_basis:
            output_json = mcp_module.calculate_swap_breakeven_basis_simple_tool(
                zcis_curve_family="USD_ZCIS",
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                tenor="10Y",
                lookback_days=365,
            )
        assert mock_basis.call_count == 1
        _assert_basis_config_passed(mock_basis.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_field_name_parameter_present_on_mcp_wrapper(self):
        """The MCP wrapper exposes ``field_name: str = ""`` so
        callers can override per-call.  The empty-string default
        mirrors the sentinel pattern used by sibling rates tools.
        """
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(
            mcp_module.calculate_swap_breakeven_basis_simple_tool,
        )
        assert "field_name" in sig.parameters
        assert sig.parameters["field_name"].default == ""

    def test_explicit_field_name_forwarded_to_input_schema(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple.schemas import (
            SwapBreakevenBasisSimpleInput,
        )

        captured: dict = {}

        def _fake_calculate(*, engine, params, config):
            captured["params"] = params
            return _well_formed_basis_output()

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_swap_breakeven_basis_simple",
            side_effect=_fake_calculate,
        ):
            mcp_module.calculate_swap_breakeven_basis_simple_tool(
                zcis_curve_family="USD_ZCIS",
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                tenor="10Y",
                lookback_days=365,
                field_name="PX_LAST",
            )
        assert isinstance(
            captured["params"], SwapBreakevenBasisSimpleInput,
        )
        assert captured["params"].field_name == "PX_LAST"

    def test_empty_string_field_name_coerced_to_none(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        captured: dict = {}

        def _fake_calculate(*, engine, params, config):
            captured["params"] = params
            return _well_formed_basis_output()

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_swap_breakeven_basis_simple",
            side_effect=_fake_calculate,
        ):
            mcp_module.calculate_swap_breakeven_basis_simple_tool(
                zcis_curve_family="USD_ZCIS",
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                tenor="10Y",
                lookback_days=365,
                field_name="",
            )
        assert captured["params"].field_name is None

    def test_default_field_name_is_empty_string(self):
        """When the caller does not pass ``field_name``, the
        MCP wrapper's empty-string default reaches the schema's
        ``_coerce_empty_field_name`` validator which coerces it to
        None.
        """
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        captured: dict = {}

        def _fake_calculate(*, engine, params, config):
            captured["params"] = params
            return _well_formed_basis_output()

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_swap_breakeven_basis_simple",
            side_effect=_fake_calculate,
        ):
            mcp_module.calculate_swap_breakeven_basis_simple_tool(
                zcis_curve_family="USD_ZCIS",
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                tenor="10Y",
            )
        assert captured["params"].field_name is None

    def test_time_series_stripped_from_llm_response(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_swap_breakeven_basis_simple",
            return_value=_well_formed_basis_output(),
        ):
            output_json = mcp_module.calculate_swap_breakeven_basis_simple_tool(
                zcis_curve_family="USD_ZCIS",
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        assert "time_series" not in parsed
        assert "time_series_basis" not in parsed
        assert "time_series_zscore" not in parsed
        # methodology_label MUST survive — load-bearing basis caveat.
        assert "methodology_label" in parsed["current_metrics"]
        # ZCIS leg's reference metadata MUST survive — the desk
        # needs the index family / lag / interpolation to interpret
        # the basis.
        assert (
            parsed["current_metrics"]["zcis_inflation_index_family"]
            == "US_CPI_URBAN"
        )


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_swap_breakeven_basis_config(self):
        cfg = load_tool_config(SWAP_BREAKEVEN_BASIS_SIMPLE_CONFIG_PATH)
        assert cfg.tool.name == "calculate_swap_breakeven_basis_simple_tool"
        assert cfg.tool.domain == "inflation_swaps"


# ===========================================================================
# Response-model validation
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple.schemas import (
            SwapBreakevenBasisSimpleOutput,
        )
        validated = SwapBreakevenBasisSimpleOutput.model_validate(
            _well_formed_basis_output(),
        )
        assert validated.time_series_basis.units.value == "bps"
        assert validated.time_series_zscore.units.value == "z_score"
        assert (
            validated.current_metrics.zcis_inflation_index_family
            == "US_CPI_URBAN"
        )
        assert validated.current_metrics.zcis_index_lag == "3M"
        assert validated.current_metrics.zcis_interpolation == "Daily"

    def test_response_model_requires_canonical_series(self):
        from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple.schemas import (
            SwapBreakevenBasisSimpleOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_basis_output()
        bad.pop("time_series_basis")
        with pytest.raises(ValidationError):
            SwapBreakevenBasisSimpleOutput.model_validate(bad)

    def test_response_model_requires_methodology_label(self):
        from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple.schemas import (
            SwapBreakevenBasisSimpleOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_basis_output()
        bad["current_metrics"].pop("methodology_label")
        with pytest.raises(ValidationError):
            SwapBreakevenBasisSimpleOutput.model_validate(bad)

    def test_response_model_requires_derived_index_families_match(self):
        """``index_families_match`` is a required derived top-level
        summary field on current_metrics — making it optional would
        let a future regression silently drop the load-bearing
        basis caveat from the wire.
        """
        from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple.schemas import (
            SwapBreakevenBasisSimpleOutput,
        )
        from pydantic import ValidationError
        bad = _well_formed_basis_output()
        bad["current_metrics"].pop("index_families_match")
        with pytest.raises(ValidationError):
            SwapBreakevenBasisSimpleOutput.model_validate(bad)

    def test_response_model_requires_zcis_reference_metadata(self):
        """ZCIS leg's reference metadata is load-bearing on the
        wire — making it optional would let a future regression
        silently drop the index-family caveat.
        """
        from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple.schemas import (
            SwapBreakevenBasisSimpleOutput,
        )
        from pydantic import ValidationError
        for required_field in (
            "zcis_inflation_index_family",
            "zcis_index_lag",
            "zcis_interpolation",
        ):
            bad = _well_formed_basis_output()
            bad["current_metrics"].pop(required_field)
            with pytest.raises(ValidationError):
                SwapBreakevenBasisSimpleOutput.model_validate(bad)

    def test_round_trip_preserves_canonical_fields(self):
        from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple.schemas import (
            SwapBreakevenBasisSimpleOutput,
        )
        validated = SwapBreakevenBasisSimpleOutput.model_validate(
            _well_formed_basis_output(),
        )
        dumped = validated.model_dump()
        assert dumped["time_series_basis"]["units"] == "bps"
        assert dumped["time_series_zscore"]["units"] == "z_score"
        assert dumped["current_metrics"]["methodology_label"]
        assert dumped["current_metrics"]["tenor_years"] == 10.0


# ===========================================================================
# Workflow PrimitiveSpec registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_resolver_returns_basis_spec(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_swap_breakeven_basis_simple_tool",
        )
        assert (
            spec.tool_name
            == "calculate_swap_breakeven_basis_simple_tool"
        )
        assert (
            spec.config_path
            == SWAP_BREAKEVEN_BASIS_SIMPLE_CONFIG_PATH
        )

    def test_spec_uses_correct_input_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple import (
            SwapBreakevenBasisSimpleInput,
            SwapBreakevenBasisSimpleOutput,
        )
        spec = rates_primitive_resolver(
            "calculate_swap_breakeven_basis_simple_tool",
        )
        assert spec.input_class is SwapBreakevenBasisSimpleInput
        assert spec.output_class is SwapBreakevenBasisSimpleOutput

    def test_spec_uses_correct_callable(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple import (
            calculate_swap_breakeven_basis_simple,
        )
        spec = rates_primitive_resolver(
            "calculate_swap_breakeven_basis_simple_tool",
        )
        assert spec.callable is calculate_swap_breakeven_basis_simple

    def test_output_field_units_declared_honestly(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "calculate_swap_breakeven_basis_simple_tool",
        )
        # Bespoke time_series row list carries basis_bps, so BPS;
        # canonical basis series is BPS; canonical z-score series
        # is Z_SCORE.
        assert spec.output_field_units == {
            "time_series": "bps",
            "time_series_basis": "bps",
            "time_series_zscore": "z_score",
        }

    def test_known_rates_primitives_includes_basis(self):
        from rates_agent.workflows import known_rates_primitives
        assert (
            "calculate_swap_breakeven_basis_simple_tool"
            in known_rates_primitives()
        )


# ===========================================================================
# Controlled-error envelope surface
# ===========================================================================

class TestMcpControlledErrorSurface:
    """The MCP wrapper must surface controlled-error envelopes from
    the underlying tool — without this the LLM hitting the basis
    tool with a non-ZCIS curve_family / same-issuer linker /
    unparseable tenor would receive a fabricated value or a stack
    trace.
    """

    def test_missing_endpoint_surfaces_error_envelope(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        controlled_error = {
            "error": (
                "Swap-breakeven basis USD_ZCIS - UST/USD_TIPS 10Y "
                "could not be computed: the zcis (USD_ZCIS) leg "
                "failed.  Inner error: No ZCIS data found for "
                "curve_family='USD_ZCIS', tenor='10Y' "
                "(instrument_type='inflation_swap', "
                "pricing_type='zero_coupon_breakeven')."
            )
        }
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_swap_breakeven_basis_simple",
            return_value=controlled_error,
        ):
            output_json = mcp_module.calculate_swap_breakeven_basis_simple_tool(
                zcis_curve_family="USD_ZCIS",
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "zcis" in parsed["error"].lower()
        assert "inflation_swap" in parsed["error"]

    def test_same_curve_attempt_surfaces_validation_error(self):
        """nominal_curve_family == linker_curve_family must hit the
        Pydantic validator and emerge as a controlled error
        envelope.
        """
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.calculate_swap_breakeven_basis_simple_tool(
                zcis_curve_family="USD_ZCIS",
                nominal_curve_family="UST",
                linker_curve_family="UST",
                tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "current_metrics" not in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_unparseable_tenor_surfaces_validation_error(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.calculate_swap_breakeven_basis_simple_tool(
                zcis_curve_family="USD_ZCIS",
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
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
    hardcoded Python literal.
    """

    def test_bundled_yaml_methodology_what_it_does_is_nonempty(self):
        cfg = load_tool_config(SWAP_BREAKEVEN_BASIS_SIMPLE_CONFIG_PATH)
        assert cfg.methodology.what_it_does.strip()
        what = cfg.methodology.what_it_does.lower()
        assert "zcis_pct" in what
        assert "breakeven_pct" in what
        # Load-bearing basis caveat per the catalog's
        # methodology_guardrails.
        assert "not a clean liquidity-premium read" in what
        assert "index-lag" in what
        # Sign convention visible.
        assert "zcis_minus_breakeven" in what or "zcis - breakeven" in what
