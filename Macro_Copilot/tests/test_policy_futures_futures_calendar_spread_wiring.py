"""
test_policy_futures_futures_calendar_spread_wiring.py — Caller-wiring
                                                         smoke tests for
                                                         the policy-
                                                         futures calendar-
                                                         spread monitor

Mirrors the sibling tools' wiring tests. Caller surface in V1:

  1. rates_agent/policy_futures/mcp_server.py::get_futures_calendar_spread_tool
  2. rates_agent/workflows/__init__.py::rates_primitive_resolver
     (registered as policy_futures_get_futures_calendar_spread_tool)

Key load-bearing properties under test:

  - MCP wrapper passes config= explicitly (no auto-load fallback).
  - MCP wrapper uses the empty-string / None-sentinel pattern so the
    YAML default_price_field actually flows through (the bug that
    bit curve_move_classifier and was fixed in commit b2605ee).
  - MCP wrapper parses as_of_date ISO-format → date.fromisoformat
    sentinel; empty string ⇒ None (post-fetch data-max anchor).
  - The LLM-facing response withholds the historical ``time_series``
    rows but PRESERVES the P5 ``methodology_disclosure`` (sign
    convention, regime label, scope-limit caveats).
  - Workflow registration is honest: PrimitiveSpec is registered with
    the correct CONFIG_PATH and with the ADR 0017 canonical
    declaration ``output_field_units =
    {"time_series_spread_implied_rate": "percent"}`` (the canonical
    implied-rate-spread companion the open-DAG Series bridge lifts;
    the bespoke two-unit-space ``time_series`` row list stays
    undeclared).
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.policy_futures.tools.futures_calendar_spread import (
    CONFIG_PATH as FUTURES_CALENDAR_SPREAD_CONFIG_PATH,
    FuturesCalendarSpreadOutput,
)
from shared.config import ToolConfig, clear_tool_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


_METHODOLOGY_DISCLOSURE_FIXTURE: str = (
    "Rolling-generic strip calendar spread at SOFR_FUT "
    "strip_position_short=1 (master stem=SFR1) minus "
    "strip_position_long=2 (master stem=SFR2). Sign convention: "
    "short_leg minus long_leg (fronter minus backer). The desk-"
    "recognised quantity is the implied-rate spread in PERCENT "
    "POINTS. Underlying short-rate regime: RFR (RFR = compounded "
    "daily risk-free rate; IBOR = unsecured 3M term IBOR). "
    "Inverse-priced strip — per leg, implied_rate_pct = 100 - "
    "raw_price; the calendar spread on the implied-rate axis equals "
    "-(raw-price spread). Z-score lookback = 252 trading days on "
    "the IMPLIED-RATE-SPREAD series; trailing range window = 252 "
    "trading days. This is NOT a CTD-of-futures-of-OIS calendar "
    "spread; the CTD-implied-OIS curve is not yet a primitive in "
    "this build. This is also NOT a meeting-by-meeting policy-path "
    "decomposition — it is a same-curve strip-slot calendar spread "
    "on rolling-generic series (ADR 0013 V1 scope — policy_futures "
    "ships strip-position-keyed monitors only)."
)


def _well_formed_output() -> dict:
    """Mock matches the live wire shape — MUST validate against
    ``FuturesCalendarSpreadOutput``."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family": "SOFR_FUT",
            "strip_position_short": 1,
            "strip_position_long": 2,
            "spread_label": "SFR1-SFR2",
            "contract_code_short": "SFR1",
            "contract_code_long": "SFR2",
            "underlying_contract_code_short": "SFRM26",
            "underlying_contract_code_long": "SFRU26",
            "security_name_short": "SFRM26 COMB",
            "security_name_long": "SFRU26 COMB",
            "expiry_date_short": "2026-06-16",
            "expiry_date_long": "2026-09-15",
            "inverse_priced": True,
            "short_rate_regime": "RFR",
            "raw_price_spread": 0.12500,
            "spread_implied_rate_pct": -0.1250,
            "daily_change_raw_price_spread": -0.00500,
            "daily_change_spread_implied_rate_pct": 0.0050,
            "z_score_spread_implied_rate": -0.45,
            "high_252d_spread_implied_rate_pct": 0.2500,
            "low_252d_spread_implied_rate_pct": -0.4500,
            "mid_252d_spread_implied_rate_pct": -0.1000,
            "percentile_252d": 46.4,
            "rolling_window_days": 252,
            "observation_count": 252,
        },
        "time_series": [
            {
                "date": "2026-04-29",
                "raw_price_spread": 0.13000,
                "spread_implied_rate_pct": -0.1300,
            },
            {
                "date": "2026-04-30",
                "raw_price_spread": 0.12500,
                "spread_implied_rate_pct": -0.1250,
            },
        ],
        "time_series_spread_implied_rate": {
            "series_name": "sofr_fut_1_2_calendar_spread",
            "units": "percent",
            "description": (
                "Implied-rate calendar spread (fronter − backer) on "
                "SOFR_FUT strip positions (1, 2) in PERCENT POINTS."
            ),
            "rows": [
                {"date": "2026-04-29", "value": -0.1300},
                {"date": "2026-04-30", "value": -0.1250},
            ],
        },
        "methodology_disclosure": _METHODOLOGY_DISCLOSURE_FIXTURE,
    }


def _assert_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_futures_calendar_spread called without `config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "policy_futures_get_futures_calendar_spread_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'policy_futures_get_futures_calendar_spread_tool' (catches "
        "imports of the wrong tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_price_field" in cfg.conventions
    assert "raw_price_round_decimals" in cfg.conventions
    assert "short_rate_regime_map" in cfg.conventions
    assert "trailing_range_window_days" in cfg.conventions


# ===========================================================================
# 1. MCP wrapper
# ===========================================================================

class TestMcpFuturesCalendarSpreadWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_calendar_spread",
            return_value=_well_formed_output(),
        ) as spy:
            output_json = mcp_module.get_futures_calendar_spread_tool(
                curve_family="SOFR_FUT",
                strip_position_short=1,
                strip_position_long=2,
                lookback_days=365,
                field_name="PX_LAST",
            )
        assert spy.call_count == 1
        _assert_config_passed(spy.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        # P5 disclosure must reach the LLM-facing payload.
        assert parsed.get("methodology_disclosure") == _METHODOLOGY_DISCLOSURE_FIXTURE
        # time_series + the canonical companion are stripped from the
        # LLM-facing payload (the open-DAG Series bridge reads the
        # canonical field from the raw dict).
        assert "time_series" not in parsed
        assert "time_series_spread_implied_rate" not in parsed

    def test_field_name_default_is_empty_string_sentinel(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(mcp_module.get_futures_calendar_spread_tool)
        default = sig.parameters["field_name"].default
        assert default == "", (
            f"MCP wrapper's field_name default must be '' (empty-string "
            f"sentinel for 'use the YAML default'); got {default!r}"
        )

    def test_as_of_date_default_is_empty_string_sentinel(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(mcp_module.get_futures_calendar_spread_tool)
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
            "calculate_futures_calendar_spread",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_calendar_spread_tool(
                curve_family="SOFR_FUT",
                strip_position_short=1,
                strip_position_long=2,
                lookback_days=365,
                # field_name omitted
                # as_of_date omitted
            )
        params = spy.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach FuturesCalendarSpreadInput as None "
            f"(sentinel for 'use YAML default_price_field'), got "
            f"{params.field_name!r}"
        )
        assert params.as_of_date is None, (
            f"omitted as_of_date must reach FuturesCalendarSpreadInput as None "
            f"(sentinel for 'anchor at data max'), got {params.as_of_date!r}"
        )

    def test_explicit_field_name_passes_through(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_calendar_spread",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_calendar_spread_tool(
                curve_family="SOFR_FUT",
                strip_position_short=1,
                strip_position_long=2,
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
            "calculate_futures_calendar_spread",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_calendar_spread_tool(
                curve_family="SOFR_FUT",
                strip_position_short=1,
                strip_position_long=2,
                as_of_date="2026-04-08",
            )
        params = spy.call_args.kwargs["params"]
        assert params.as_of_date == date(2026, 4, 8)

    def test_malformed_as_of_date_returns_envelope(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        output_json = mcp_module.get_futures_calendar_spread_tool(
            curve_family="SOFR_FUT",
            strip_position_short=1,
            strip_position_long=2,
            as_of_date="not-a-date",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "as_of_date" in parsed["error"]

    def test_validation_error_returns_controlled_envelope(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        # Pass an empty curve_family (min_length=1 fails) — must
        # produce the controlled {"error": ...} envelope, not raise.
        output_json = mcp_module.get_futures_calendar_spread_tool(
            curve_family="",
            strip_position_short=1,
            strip_position_long=2,
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_same_leg_input_returns_controlled_envelope(self):
        """The schema validator rejects same-leg spreads — the MCP
        wrapper must surface the resulting ValidationError as the
        controlled-error envelope."""
        from rates_agent.policy_futures import mcp_server as mcp_module

        output_json = mcp_module.get_futures_calendar_spread_tool(
            curve_family="SOFR_FUT",
            strip_position_short=2,
            strip_position_long=2,
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_short_greater_than_long_returns_controlled_envelope(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        output_json = mcp_module.get_futures_calendar_spread_tool(
            curve_family="SOFR_FUT",
            strip_position_short=4,
            strip_position_long=2,
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]


# ===========================================================================
# 2. CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.policy_futures.tools.futures_calendar_spread import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.policy_futures.tools.futures_calendar_spread.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_policy_futures_calendar_spread_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(FUTURES_CALENDAR_SPREAD_CONFIG_PATH)
        assert cfg.tool.name == "policy_futures_get_futures_calendar_spread_tool"
        assert cfg.tool.domain == "policy_futures"


# ===========================================================================
# 3. Response-model validation (mock parity)
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        # If this raises, the wiring-test mock is out of sync with the
        # live schema (which is exactly the failure mode this guard
        # pins).
        validated = FuturesCalendarSpreadOutput.model_validate(_well_formed_output())
        assert validated.methodology_disclosure == _METHODOLOGY_DISCLOSURE_FIXTURE
        assert validated.current_metrics.raw_price_spread == 0.12500
        assert validated.current_metrics.spread_implied_rate_pct == -0.1250
        assert validated.current_metrics.inverse_priced is True
        assert validated.current_metrics.short_rate_regime == "RFR"
        assert validated.current_metrics.rolling_window_days == 252

    def test_response_model_requires_methodology_disclosure(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad.pop("methodology_disclosure")
        with pytest.raises(ValidationError):
            FuturesCalendarSpreadOutput.model_validate(bad)

    def test_response_model_forbids_extra_keys(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad["unexpected"] = "value"
        with pytest.raises(ValidationError):
            FuturesCalendarSpreadOutput.model_validate(bad)


# ===========================================================================
# 4. Workflow registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_registered_in_rates_primitive_resolver(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "policy_futures_get_futures_calendar_spread_tool"
        )
        assert spec.tool_name == "policy_futures_get_futures_calendar_spread_tool"
        assert spec.config_path == FUTURES_CALENDAR_SPREAD_CONFIG_PATH

    def test_output_field_units_declares_canonical_spread(self):
        """ADR 0017: the single-unit-space canonical companion
        ``time_series_spread_implied_rate`` is declared in PERCENT
        (implied-rate spread in percent points — same declaration as
        the already-bridged cross_market_spread).  The bespoke
        two-unit-space ``time_series`` row list stays UNDECLARED so
        the validator refuses a binding the Series bridge cannot
        lift."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "policy_futures_get_futures_calendar_spread_tool"
        )
        assert spec.output_field_units == {
            "time_series_spread_implied_rate": "percent",
        }

    def test_classified_bridgeable_series(self):
        """The composability audit must classify this primitive
        BRIDGEABLE_SERIES post-ADR-0017 (it was TERMINAL_ONLY_SNAPSHOT
        while output_field_units was empty — campaign refusal k03)."""
        from orchestrator.open_dag.composability_audit import (
            Composability,
            classify_primitive,
        )
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "policy_futures_get_futures_calendar_spread_tool"
        )
        entry = classify_primitive(spec)
        assert entry.classification is Composability.BRIDGEABLE_SERIES

    def test_registered_input_and_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.policy_futures.tools.futures_calendar_spread import (
            FuturesCalendarSpreadInput,
            FuturesCalendarSpreadOutput,
        )
        spec = rates_primitive_resolver(
            "policy_futures_get_futures_calendar_spread_tool"
        )
        assert spec.input_class is FuturesCalendarSpreadInput
        assert spec.output_class is FuturesCalendarSpreadOutput
