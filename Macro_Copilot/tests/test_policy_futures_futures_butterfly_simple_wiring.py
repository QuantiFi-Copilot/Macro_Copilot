"""
test_policy_futures_futures_butterfly_simple_wiring.py — Caller-wiring
                                                          smoke tests
                                                          for the
                                                          policy-futures
                                                          simple-
                                                          butterfly
                                                          monitor

Mirrors the sibling tools' wiring tests. Caller surface in V1:

  1. rates_agent/policy_futures/mcp_server.py::get_futures_butterfly_simple_tool
  2. rates_agent/workflows/__init__.py::rates_primitive_resolver
     (registered as policy_futures_get_futures_butterfly_simple_tool)

Key load-bearing properties under test:

  - MCP wrapper passes config= explicitly (no auto-load fallback).
  - MCP wrapper uses the empty-string / None-sentinel pattern so the
    YAML default_price_field actually flows through.
  - MCP wrapper parses as_of_date ISO-format; empty string ⇒ None.
  - The LLM-facing response withholds the historical ``time_series``
    rows (bespoke + both canonical TimeSeries) but PRESERVES the P5
    ``methodology_disclosure``.
  - Workflow registration is honest: PrimitiveSpec is registered with
    the correct CONFIG_PATH and with the canonical TimeSeries
    ``output_field_units`` declarations (percent / z_score).
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.policy_futures.tools.futures_butterfly_simple import (
    CONFIG_PATH as FUTURES_BUTTERFLY_SIMPLE_CONFIG_PATH,
    FuturesButterflySimpleOutput,
)
from shared.config import ToolConfig, clear_tool_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


_METHODOLOGY_DISCLOSURE_FIXTURE: str = (
    "Rolling-generic strip simple butterfly at SOFR_FUT "
    "strip_position_wing_short=1 (master stem=SFR1), "
    "strip_position_body=2 (master stem=SFR2), "
    "strip_position_wing_long=3 (master stem=SFR3). Sign convention: "
    "body minus wing average (formula: body - 0.5 * (wing_short + "
    "wing_long)). Weighting: FIXED 50-50 simple-butterfly weights "
    "(body=1.0, wing_short=-0.5, wing_long=-0.5). The desk-recognised "
    "quantity is the butterfly value in PERCENT POINTS. Underlying "
    "short-rate regime: RFR (RFR = compounded daily risk-free rate; "
    "IBOR = unsecured 3M term IBOR). Inverse-priced strip — per leg, "
    "implied_rate_pct = 100 - raw_price; the butterfly value on the "
    "implied-rate axis equals the body's implied rate minus the wing "
    "average. Z-score lookback = 252 trading days on the BUTTERFLY "
    "series; trailing range window = 252 trading days. This is NOT a "
    "CTD-of-futures-of-OIS butterfly; the CTD-implied-OIS curve is "
    "not yet a primitive in this build. This is also NOT a "
    "meeting-by-meeting policy-path decomposition. This is also NOT "
    "a DV01-neutral or regression-fitted butterfly — those weighting "
    "variants are planned-extension territory and ship as separate "
    "primitives (ADR 0013 V1 scope — policy_futures ships strip-"
    "position-keyed monitors only)."
)


def _well_formed_output() -> dict:
    """Mock matches the live wire shape — MUST validate against
    ``FuturesButterflySimpleOutput``."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family": "SOFR_FUT",
            "strip_position_wing_short": 1,
            "strip_position_body": 2,
            "strip_position_wing_long": 3,
            "butterfly_label": "SFR1-SFR2-SFR3",
            "contract_code_wing_short": "SFR1",
            "contract_code_body": "SFR2",
            "contract_code_wing_long": "SFR3",
            "underlying_contract_code_wing_short": "SFRM26",
            "underlying_contract_code_body": "SFRU26",
            "underlying_contract_code_wing_long": "SFRZ26",
            "security_name_wing_short": "SFRM26 COMB",
            "security_name_body": "SFRU26 COMB",
            "security_name_wing_long": "SFRZ26 COMB",
            "expiry_date_wing_short": "2026-06-16",
            "expiry_date_body": "2026-09-15",
            "expiry_date_wing_long": "2026-12-15",
            "inverse_priced": True,
            "short_rate_regime": "RFR",
            "implied_rate_pct_wing_short": 4.1234,
            "implied_rate_pct_body": 4.0000,
            "implied_rate_pct_wing_long": 3.9000,
            "butterfly_value_pct": -0.0117,
            "daily_change_butterfly_value_pct": 0.0025,
            "z_score_butterfly": -0.45,
            "high_252d_butterfly_value_pct": 0.2500,
            "low_252d_butterfly_value_pct": -0.4500,
            "mid_252d_butterfly_value_pct": -0.1000,
            "percentile_252d": 46.4,
            "rolling_window_days": 252,
            "observation_count": 252,
        },
        "time_series": [
            {
                "date": "2026-04-29",
                "butterfly_value_pct": -0.0140,
                "z_score": -0.4700,
            },
            {
                "date": "2026-04-30",
                "butterfly_value_pct": -0.0117,
                "z_score": -0.4500,
            },
        ],
        "time_series_butterfly": {
            "series_name": "sofr_fut_1_2_3_butterfly",
            "units": "percent",
            "description": "Simple butterfly value on SOFR_FUT.",
            "rows": [
                {"date": "2026-04-29", "value": -0.0140},
                {"date": "2026-04-30", "value": -0.0117},
            ],
        },
        "time_series_zscore": {
            "series_name": "sofr_fut_1_2_3_zscore",
            "units": "z_score",
            "description": "Rolling z-score of the butterfly.",
            "rows": [
                {"date": "2026-04-29", "value": -0.4700},
                {"date": "2026-04-30", "value": -0.4500},
            ],
        },
        "methodology_disclosure": _METHODOLOGY_DISCLOSURE_FIXTURE,
    }


def _assert_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_futures_butterfly_simple called without `config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == (
        "policy_futures_get_futures_butterfly_simple_tool"
    ), (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'policy_futures_get_futures_butterfly_simple_tool' (catches "
        "imports of the wrong tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_price_field" in cfg.conventions
    assert "butterfly_value_round_decimals" in cfg.conventions
    assert "short_rate_regime_map" in cfg.conventions
    assert "trailing_range_window_days" in cfg.conventions
    assert "butterfly_weighting" in cfg.conventions


# ===========================================================================
# 1. MCP wrapper
# ===========================================================================

class TestMcpFuturesButterflySimpleWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_butterfly_simple",
            return_value=_well_formed_output(),
        ) as spy:
            output_json = mcp_module.get_futures_butterfly_simple_tool(
                curve_family="SOFR_FUT",
                strip_position_wing_short=1,
                strip_position_body=2,
                strip_position_wing_long=3,
                lookback_days=365,
                field_name="PX_LAST",
            )
        assert spy.call_count == 1
        _assert_config_passed(spy.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        # P5 disclosure must reach the LLM-facing payload.
        assert parsed.get("methodology_disclosure") == (
            _METHODOLOGY_DISCLOSURE_FIXTURE
        )
        # time_series + canonical series are stripped from the LLM
        # payload.
        assert "time_series" not in parsed
        assert "time_series_butterfly" not in parsed
        assert "time_series_zscore" not in parsed

    def test_field_name_default_is_empty_string_sentinel(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(mcp_module.get_futures_butterfly_simple_tool)
        default = sig.parameters["field_name"].default
        assert default == "", (
            f"MCP wrapper's field_name default must be '' (empty-string "
            f"sentinel); got {default!r}"
        )

    def test_as_of_date_default_is_empty_string_sentinel(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(mcp_module.get_futures_butterfly_simple_tool)
        default = sig.parameters["as_of_date"].default
        assert default == "", (
            f"MCP wrapper's as_of_date default must be '' (empty-string "
            f"sentinel); got {default!r}"
        )

    def test_omitted_field_name_flows_none_to_input(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_butterfly_simple",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_butterfly_simple_tool(
                curve_family="SOFR_FUT",
                strip_position_wing_short=1,
                strip_position_body=2,
                strip_position_wing_long=3,
                lookback_days=365,
            )
        params = spy.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach FuturesButterflySimpleInput as "
            f"None, got {params.field_name!r}"
        )
        assert params.as_of_date is None, (
            f"omitted as_of_date must reach FuturesButterflySimpleInput as "
            f"None, got {params.as_of_date!r}"
        )

    def test_explicit_field_name_passes_through(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_butterfly_simple",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_butterfly_simple_tool(
                curve_family="SOFR_FUT",
                strip_position_wing_short=1,
                strip_position_body=2,
                strip_position_wing_long=3,
                field_name="PX_BID",
            )
        params = spy.call_args.kwargs["params"]
        assert params.field_name == "PX_BID"

    def test_explicit_as_of_date_parses_iso(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_butterfly_simple",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_butterfly_simple_tool(
                curve_family="SOFR_FUT",
                strip_position_wing_short=1,
                strip_position_body=2,
                strip_position_wing_long=3,
                as_of_date="2026-04-08",
            )
        params = spy.call_args.kwargs["params"]
        assert params.as_of_date == date(2026, 4, 8)

    def test_malformed_as_of_date_returns_envelope(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        output_json = mcp_module.get_futures_butterfly_simple_tool(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            as_of_date="not-a-date",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "as_of_date" in parsed["error"]

    def test_validation_error_returns_controlled_envelope(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        # Empty curve_family fails min_length=1
        output_json = mcp_module.get_futures_butterfly_simple_tool(
            curve_family="",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_repeated_leg_returns_controlled_envelope(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        output_json = mcp_module.get_futures_butterfly_simple_tool(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=2,
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_unordered_legs_returns_controlled_envelope(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        output_json = mcp_module.get_futures_butterfly_simple_tool(
            curve_family="SOFR_FUT",
            strip_position_wing_short=3,
            strip_position_body=2,
            strip_position_wing_long=4,
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]


# ===========================================================================
# 2. CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.policy_futures.tools.futures_butterfly_simple import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.policy_futures.tools.futures_butterfly_simple.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_policy_futures_butterfly_simple_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(FUTURES_BUTTERFLY_SIMPLE_CONFIG_PATH)
        assert cfg.tool.name == (
            "policy_futures_get_futures_butterfly_simple_tool"
        )
        assert cfg.tool.domain == "policy_futures"


# ===========================================================================
# 3. Response-model validation (mock parity)
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        validated = FuturesButterflySimpleOutput.model_validate(
            _well_formed_output()
        )
        assert validated.methodology_disclosure == (
            _METHODOLOGY_DISCLOSURE_FIXTURE
        )
        assert validated.current_metrics.butterfly_value_pct == -0.0117
        assert validated.current_metrics.inverse_priced is True
        assert validated.current_metrics.short_rate_regime == "RFR"
        assert validated.current_metrics.rolling_window_days == 252
        assert validated.time_series_butterfly.units.value == "percent"
        assert validated.time_series_zscore.units.value == "z_score"

    def test_response_model_requires_methodology_disclosure(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad.pop("methodology_disclosure")
        with pytest.raises(ValidationError):
            FuturesButterflySimpleOutput.model_validate(bad)

    def test_response_model_forbids_extra_keys(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad["unexpected"] = "value"
        with pytest.raises(ValidationError):
            FuturesButterflySimpleOutput.model_validate(bad)


# ===========================================================================
# 4. Workflow registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_registered_in_rates_primitive_resolver(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "policy_futures_get_futures_butterfly_simple_tool"
        )
        assert spec.tool_name == (
            "policy_futures_get_futures_butterfly_simple_tool"
        )
        assert spec.config_path == FUTURES_BUTTERFLY_SIMPLE_CONFIG_PATH

    def test_output_field_units_declared_honestly(self):
        """Unlike the calendar_spread sibling (two units per row,
        empty output_field_units), this primitive's butterfly series
        is single-unit (PERCENT POINTS) so we DO declare the
        canonical-series unit tags. ``time_series`` (bespoke) /
        ``time_series_butterfly`` (canonical) both carry PERCENT;
        ``time_series_zscore`` carries Z_SCORE."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "policy_futures_get_futures_butterfly_simple_tool"
        )
        assert spec.output_field_units == {
            "time_series": "percent",
            "time_series_butterfly": "percent",
            "time_series_zscore": "z_score",
        }

    def test_registered_input_and_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.policy_futures.tools.futures_butterfly_simple import (
            FuturesButterflySimpleInput,
            FuturesButterflySimpleOutput,
        )
        spec = rates_primitive_resolver(
            "policy_futures_get_futures_butterfly_simple_tool"
        )
        assert spec.input_class is FuturesButterflySimpleInput
        assert spec.output_class is FuturesButterflySimpleOutput
