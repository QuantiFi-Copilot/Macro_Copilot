"""
test_policy_futures_futures_cross_market_spread_wiring.py — Caller-
                                                             wiring
                                                             smoke
                                                             tests
                                                             for the
                                                             policy-
                                                             futures
                                                             cross-
                                                             market-
                                                             spread
                                                             monitor

Mirrors the sibling tools' wiring tests. Caller surface in V1:

  1. rates_agent/policy_futures/mcp_server.py::get_futures_cross_market_spread_tool
  2. rates_agent/workflows/__init__.py::rates_primitive_resolver
     (registered as policy_futures_get_futures_cross_market_spread_tool)

Key load-bearing properties under test:

  - MCP wrapper passes config= explicitly (no auto-load fallback).
  - MCP wrapper uses the empty-string / None-sentinel pattern so the
    YAML default_price_field actually flows through.
  - MCP wrapper parses as_of_date ISO-format → date.fromisoformat
    sentinel; empty string ⇒ None (post-fetch data-max anchor).
  - The LLM-facing response withholds the historical ``time_series``
    rows AND the canonical TimeSeries payloads but PRESERVES the P5
    ``methodology_disclosure`` (sign convention, regime labels,
    scope-limit + RAW-differential caveats).
  - Workflow registration is honest: PrimitiveSpec is registered
    with the correct CONFIG_PATH and with PERCENT / Z_SCORE
    ``output_field_units`` (the spread is single-unit PERCENT
    POINTS — mirrors butterfly_simple, NOT the calendar_spread
    two-unit shape).
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.policy_futures.tools.futures_cross_market_spread import (
    CONFIG_PATH as FUTURES_CROSS_MARKET_SPREAD_CONFIG_PATH,
    FuturesCrossMarketSpreadOutput,
)
from shared.config import ToolConfig, clear_tool_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


_METHODOLOGY_DISCLOSURE_FIXTURE: str = (
    "Rolling-generic matched-strip cross-market implied-rate "
    "differential at strip_position=1: leg A = SOFR_FUT (master "
    "stem=SFR1); leg B = SONIA_FUT (master stem=SFI1). Sign "
    "convention (WIRE-FROZEN): spread_value_pct = "
    "implied_rate_pct(SOFR_FUT) - implied_rate_pct(SONIA_FUT) — A "
    "minus B; swapping the inputs flips the sign by construction. "
    "The desk-recognised quantity is the cross-market spread in "
    "PERCENT POINTS. Underlying short-rate regimes: both legs = "
    "RFR (SOFR_FUT and SONIA_FUT share the same regime). RFR = "
    "compounded daily risk-free rate; IBOR = unsecured 3M term "
    "IBOR. Per-leg labels are surfaced on the snapshot even when "
    "they agree (the catalog guardrail requires the disclosure "
    "regardless of regime match). Leg A (SOFR_FUT): inverse-priced "
    "— implied_rate_pct = 100 - raw_price; Leg B (SONIA_FUT): "
    "inverse-priced — implied_rate_pct = 100 - raw_price. Z-score "
    "lookback = 252 trading days on the SPREAD series; trailing "
    "range window = 252 trading days. Output is a RAW cross-market "
    "implied-rate differential — NOT a basis-adjusted spread "
    "(cross-currency basis NOT netted) and NOT a beta-adjusted "
    "spread (regression residual NOT computed); basis-adjusted and "
    "beta-adjusted variants are planned_extension territory per "
    "PR11 and ship as separate primitives in a future build. This "
    "primitive does NOT collapse mixed RFR/IBOR pairs into a pack-"
    "average — both per-leg regime labels are preserved on the "
    "snapshot (catalog guardrail). This is NOT a CTD-of-futures-"
    "of-OIS cross-market spread; the CTD-implied-OIS curve is not "
    "yet a primitive in this build. This is also NOT a meeting-by-"
    "meeting policy-path cross-CB decomposition (ADR 0013 V1 "
    "scope — policy_futures ships strip-position-keyed monitors "
    "only)."
)


def _well_formed_output() -> dict:
    """Mock matches the live wire shape — MUST validate against
    ``FuturesCrossMarketSpreadOutput``."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family_a": "SOFR_FUT",
            "curve_family_b": "SONIA_FUT",
            "strip_position": 1,
            "spread_label": "SFR1-SFI1",
            "contract_code_a": "SFR1",
            "contract_code_b": "SFI1",
            "underlying_contract_code_a": "SFRM26",
            "underlying_contract_code_b": "SFIM26",
            "security_name_a": "SFRM26 COMB",
            "security_name_b": "SFIM26 Comdty",
            "expiry_date_a": "2026-06-16",
            "expiry_date_b": "2026-06-17",
            "inverse_priced_a": True,
            "inverse_priced_b": True,
            "short_rate_regime_a": "RFR",
            "short_rate_regime_b": "RFR",
            "implied_rate_pct_a": 4.3500,
            "implied_rate_pct_b": 4.2000,
            "spread_value_pct": 0.1500,
            "daily_change_spread_value_pct": 0.0050,
            "z_score_spread": -0.45,
            "high_252d_spread_value_pct": 0.2500,
            "low_252d_spread_value_pct": -0.0500,
            "mid_252d_spread_value_pct": 0.1000,
            "percentile_252d": 66.7,
            "rolling_window_days": 252,
            "observation_count": 252,
        },
        "time_series": [
            {
                "date": "2026-04-29",
                "spread_value_pct": 0.1450,
                "z_score": -0.4500,
            },
            {
                "date": "2026-04-30",
                "spread_value_pct": 0.1500,
                "z_score": -0.4500,
            },
        ],
        "time_series_spread": {
            "series_name": "sofr_fut_sonia_fut_1_spread",
            "units": "percent",
            "description": "Cross-market spread in percent points.",
            "rows": [
                {"date": "2026-04-29", "value": 0.1450},
                {"date": "2026-04-30", "value": 0.1500},
            ],
        },
        "time_series_zscore": {
            "series_name": "sofr_fut_sonia_fut_1_zscore",
            "units": "z_score",
            "description": "Rolling z-score of the cross-market spread.",
            "rows": [
                {"date": "2026-04-29", "value": -0.4500},
                {"date": "2026-04-30", "value": -0.4500},
            ],
        },
        "methodology_disclosure": _METHODOLOGY_DISCLOSURE_FIXTURE,
    }


def _assert_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_futures_cross_market_spread called without `config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == (
        "policy_futures_get_futures_cross_market_spread_tool"
    ), (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'policy_futures_get_futures_cross_market_spread_tool' (catches "
        "imports of the wrong tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_price_field" in cfg.conventions
    assert "spread_value_round_decimals" in cfg.conventions
    assert "short_rate_regime_map" in cfg.conventions
    assert "trailing_range_window_days" in cfg.conventions
    assert "cross_market_pair_orientation" in cfg.conventions


# ===========================================================================
# 1. MCP wrapper
# ===========================================================================

class TestMcpFuturesCrossMarketSpreadWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_cross_market_spread",
            return_value=_well_formed_output(),
        ) as spy:
            output_json = mcp_module.get_futures_cross_market_spread_tool(
                curve_family_a="SOFR_FUT",
                curve_family_b="SONIA_FUT",
                strip_position=1,
                lookback_days=365,
                field_name="PX_LAST",
            )
        assert spy.call_count == 1
        _assert_config_passed(spy.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        # P5 disclosure must reach the LLM-facing payload.
        assert parsed.get("methodology_disclosure") == _METHODOLOGY_DISCLOSURE_FIXTURE
        # time_series + canonical TimeSeries are stripped from the
        # LLM-facing payload.
        assert "time_series" not in parsed
        assert "time_series_spread" not in parsed
        assert "time_series_zscore" not in parsed

    def test_field_name_default_is_empty_string_sentinel(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(
            mcp_module.get_futures_cross_market_spread_tool,
        )
        default = sig.parameters["field_name"].default
        assert default == "", (
            f"MCP wrapper's field_name default must be '' (empty-string "
            f"sentinel for 'use the YAML default'); got {default!r}"
        )

    def test_as_of_date_default_is_empty_string_sentinel(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(
            mcp_module.get_futures_cross_market_spread_tool,
        )
        default = sig.parameters["as_of_date"].default
        assert default == "", (
            f"MCP wrapper's as_of_date default must be '' (empty-string "
            f"sentinel for 'anchor at data max'); got {default!r}"
        )

    def test_omitted_field_name_flows_none_to_input(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_cross_market_spread",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_cross_market_spread_tool(
                curve_family_a="SOFR_FUT",
                curve_family_b="SONIA_FUT",
                strip_position=1,
                lookback_days=365,
                # field_name omitted
                # as_of_date omitted
            )
        params = spy.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach FuturesCrossMarketSpreadInput "
            f"as None (sentinel for 'use YAML default_price_field'), got "
            f"{params.field_name!r}"
        )
        assert params.as_of_date is None, (
            f"omitted as_of_date must reach FuturesCrossMarketSpreadInput "
            f"as None (sentinel for 'anchor at data max'), got "
            f"{params.as_of_date!r}"
        )

    def test_explicit_field_name_passes_through(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_cross_market_spread",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_cross_market_spread_tool(
                curve_family_a="SOFR_FUT",
                curve_family_b="SONIA_FUT",
                strip_position=1,
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
            "calculate_futures_cross_market_spread",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_cross_market_spread_tool(
                curve_family_a="SOFR_FUT",
                curve_family_b="SONIA_FUT",
                strip_position=1,
                as_of_date="2026-04-08",
            )
        params = spy.call_args.kwargs["params"]
        assert params.as_of_date == date(2026, 4, 8)

    def test_malformed_as_of_date_returns_envelope(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        output_json = mcp_module.get_futures_cross_market_spread_tool(
            curve_family_a="SOFR_FUT",
            curve_family_b="SONIA_FUT",
            strip_position=1,
            as_of_date="not-a-date",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "as_of_date" in parsed["error"]

    def test_validation_error_returns_controlled_envelope(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        # Pass an empty curve_family_a (min_length=1 fails) — must
        # produce the controlled {"error": ...} envelope, not raise.
        output_json = mcp_module.get_futures_cross_market_spread_tool(
            curve_family_a="",
            curve_family_b="SONIA_FUT",
            strip_position=1,
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_same_curve_input_returns_controlled_envelope(self):
        """The schema validator rejects same-curve cross-market
        spreads — the MCP wrapper must surface the resulting
        ValidationError as the controlled-error envelope."""
        from rates_agent.policy_futures import mcp_server as mcp_module

        output_json = mcp_module.get_futures_cross_market_spread_tool(
            curve_family_a="SOFR_FUT",
            curve_family_b="SOFR_FUT",
            strip_position=1,
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]


# ===========================================================================
# 2. CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.policy_futures.tools.futures_cross_market_spread import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.policy_futures.tools.futures_cross_market_spread.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_policy_futures_cross_market_spread_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(FUTURES_CROSS_MARKET_SPREAD_CONFIG_PATH)
        assert cfg.tool.name == (
            "policy_futures_get_futures_cross_market_spread_tool"
        )
        assert cfg.tool.domain == "policy_futures"


# ===========================================================================
# 3. Response-model validation (mock parity)
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        # If this raises, the wiring-test mock is out of sync with
        # the live schema (which is exactly the failure mode this
        # guard pins).
        validated = FuturesCrossMarketSpreadOutput.model_validate(
            _well_formed_output(),
        )
        assert (
            validated.methodology_disclosure
            == _METHODOLOGY_DISCLOSURE_FIXTURE
        )
        assert validated.current_metrics.spread_value_pct == 0.1500
        assert validated.current_metrics.implied_rate_pct_a == 4.3500
        assert validated.current_metrics.implied_rate_pct_b == 4.2000
        assert validated.current_metrics.inverse_priced_a is True
        assert validated.current_metrics.inverse_priced_b is True
        assert validated.current_metrics.short_rate_regime_a == "RFR"
        assert validated.current_metrics.short_rate_regime_b == "RFR"
        assert validated.current_metrics.rolling_window_days == 252

    def test_response_model_requires_methodology_disclosure(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad.pop("methodology_disclosure")
        with pytest.raises(ValidationError):
            FuturesCrossMarketSpreadOutput.model_validate(bad)

    def test_response_model_forbids_extra_keys(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad["unexpected"] = "value"
        with pytest.raises(ValidationError):
            FuturesCrossMarketSpreadOutput.model_validate(bad)


# ===========================================================================
# 4. Workflow registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_registered_in_rates_primitive_resolver(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "policy_futures_get_futures_cross_market_spread_tool"
        )
        assert spec.tool_name == (
            "policy_futures_get_futures_cross_market_spread_tool"
        )
        assert spec.config_path == FUTURES_CROSS_MARKET_SPREAD_CONFIG_PATH

    def test_output_field_units_declared(self):
        """The cross-market spread is single-unit PERCENT POINTS on
        the spread axis (per-leg implied rates are also PERCENT) — we
        declare canonical TimeSeries with TimeSeriesUnits.PERCENT
        (spread) + TimeSeriesUnits.Z_SCORE (z-score). Mirrors the
        butterfly_simple shape (single-unit), NOT the calendar_spread
        empty-dict shape (two-unit raw_price + implied_rate)."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "policy_futures_get_futures_cross_market_spread_tool"
        )
        assert spec.output_field_units == {
            "time_series": "percent",
            "time_series_spread": "percent",
            "time_series_zscore": "z_score",
        }

    def test_registered_input_and_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.policy_futures.tools.futures_cross_market_spread import (
            FuturesCrossMarketSpreadInput,
            FuturesCrossMarketSpreadOutput,
        )
        spec = rates_primitive_resolver(
            "policy_futures_get_futures_cross_market_spread_tool"
        )
        assert spec.input_class is FuturesCrossMarketSpreadInput
        assert spec.output_class is FuturesCrossMarketSpreadOutput
