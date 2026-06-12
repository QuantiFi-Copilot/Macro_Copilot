"""
test_policy_futures_futures_price_level_wiring.py — Caller-wiring smoke
                                                     tests for the
                                                     policy-futures
                                                     price-level monitor

Mirrors test_futures_price_level_wiring.py for the policy_futures domain.
Caller surface in V1:

  1. rates_agent/policy_futures/mcp_server.py::get_futures_price_level_tool
  2. rates_agent/workflows/__init__.py::rates_primitive_resolver
     (registered as policy_futures_get_futures_price_level_tool)

Key load-bearing properties under test:

  - MCP wrapper passes config= explicitly (no auto-load fallback).
  - MCP wrapper uses the empty-string / None-sentinel pattern so the
    YAML default_price_field actually flows through (the bug that
    bit curve_move_classifier and was fixed in commit b2605ee).
  - MCP wrapper parses as_of_date ISO-format → date.fromisoformat
    sentinel; empty string ⇒ None (post-fetch data-max anchor).
  - The LLM-facing response withholds the historical ``time_series``
    rows but PRESERVES the P5 ``methodology_disclosure`` (so the
    rolling-generic-strip-read caveat is propagated upward).
  - Workflow registration is honest: PrimitiveSpec is registered with
    the correct CONFIG_PATH and with the ADR 0017 canonical
    declaration ``output_field_units = {"time_series_implied_rate":
    "percent"}`` (the canonical implied-rate companion the open-DAG
    Series bridge lifts; the bespoke two-unit-space ``time_series``
    row list stays undeclared).
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.policy_futures.tools.futures_price_level import (
    CONFIG_PATH as FUTURES_PRICE_LEVEL_CONFIG_PATH,
    FuturesPriceLevelOutput,
)
from shared.config import ToolConfig, clear_tool_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


_METHODOLOGY_DISCLOSURE_FIXTURE: str = (
    "Rolling-generic strip read at SOFR_FUT strip_position=1 (master "
    "stem=SFR1); the desk-recognised level is the implied rate in "
    "PERCENT. Underlying short-rate regime: RFR (RFR = compounded "
    "daily risk-free rate; IBOR = unsecured 3M term IBOR). "
    "Inverse-priced strip — implied_rate_pct = 100 - raw_price. "
    "Z-score lookback = 252 trading days on the IMPLIED-RATE level "
    "series; trailing range window = 252 trading days. This is NOT "
    "a CTD-of-futures-of-OIS read; the CTD-implied-OIS curve is not "
    "yet a primitive in this build (ADR 0013 V1 scope — "
    "policy_futures ships strip-position-keyed monitors only)."
)


def _well_formed_output() -> dict:
    """Mock matches the live wire shape — MUST validate against
    ``FuturesPriceLevelOutput``."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family": "SOFR_FUT",
            "strip_position": 1,
            "contract_code": "SFR1",
            "underlying_contract_code": "SFRM26",
            "security_name": "SFRM26 COMB",
            "expiry_date": "2026-06-16",
            "contract_size": 2500.0,
            "tick_size": 0.005,
            "tick_value": 12.5,
            "inverse_priced": True,
            "short_rate_regime": "RFR",
            "quote_units": "100 - rate",
            "raw_price": 96.12500,
            "implied_rate_pct": 3.8750,
            "daily_change_raw_price": -0.01000,
            "daily_change_implied_rate_pct": 0.0100,
            "z_score_implied_rate": -0.42,
            "high_252d_implied_rate_pct": 4.5000,
            "low_252d_implied_rate_pct": 3.5000,
            "mid_252d_implied_rate_pct": 4.0000,
            "high_252d_raw_price": 96.50000,
            "low_252d_raw_price": 95.50000,
            "mid_252d_raw_price": 96.00000,
            "percentile_252d": 37.5,
            "observation_count": 252,
        },
        "time_series": [
            {"date": "2026-04-29", "raw_price": 96.13500, "implied_rate_pct": 3.8650},
            {"date": "2026-04-30", "raw_price": 96.12500, "implied_rate_pct": 3.8750},
        ],
        "time_series_implied_rate": {
            "series_name": "sofr_fut_1_implied_rate",
            "units": "percent",
            "description": (
                "Desk-recognised implied rate for SOFR_FUT strip "
                "position 1 in PERCENT."
            ),
            "rows": [
                {"date": "2026-04-29", "value": 3.8650},
                {"date": "2026-04-30", "value": 3.8750},
            ],
        },
        "methodology_disclosure": _METHODOLOGY_DISCLOSURE_FIXTURE,
    }


def _assert_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, "calculate_futures_price_level called without `config=`"
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "policy_futures_get_futures_price_level_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'policy_futures_get_futures_price_level_tool' (catches "
        "imports of the wrong tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_price_field" in cfg.conventions
    assert "raw_price_round_decimals" in cfg.conventions
    assert "short_rate_regime_map" in cfg.conventions


# ===========================================================================
# 1. MCP wrapper
# ===========================================================================

class TestMcpFuturesPriceLevelWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_price_level",
            return_value=_well_formed_output(),
        ) as spy:
            output_json = mcp_module.get_futures_price_level_tool(
                curve_family="SOFR_FUT",
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
        # time_series + the canonical companion are stripped from the
        # LLM-facing payload (frontend REST surfaces get the full
        # payload via the dict result; the open-DAG Series bridge
        # reads the canonical field from the raw dict).
        assert "time_series" not in parsed
        assert "time_series_implied_rate" not in parsed

    def test_field_name_default_is_empty_string_sentinel(self):
        """The MCP wrapper's field_name default must be the empty-
        string sentinel, NOT 'PX_LAST'. Hardcoding any other string
        here shadows the YAML's default_price_field."""
        from rates_agent.policy_futures import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(mcp_module.get_futures_price_level_tool)
        default = sig.parameters["field_name"].default
        assert default == "", (
            f"MCP wrapper's field_name default must be '' (empty-string "
            f"sentinel for 'use the YAML default'); got {default!r}"
        )

    def test_as_of_date_default_is_empty_string_sentinel(self):
        """The MCP wrapper's as_of_date default must be the empty-
        string sentinel, NOT an ISO date. Hardcoding a date here
        defeats the post-fetch data-max anchor."""
        from rates_agent.policy_futures import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(mcp_module.get_futures_price_level_tool)
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
            "calculate_futures_price_level",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_price_level_tool(
                curve_family="SOFR_FUT",
                strip_position=1,
                lookback_days=365,
                # field_name omitted
                # as_of_date omitted
            )
        params = spy.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach FuturesPriceLevelInput as None "
            f"(sentinel for 'use YAML default_price_field'), got "
            f"{params.field_name!r}"
        )
        assert params.as_of_date is None, (
            f"omitted as_of_date must reach FuturesPriceLevelInput as None "
            f"(sentinel for 'anchor at data max'), got {params.as_of_date!r}"
        )

    def test_explicit_field_name_passes_through(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_price_level",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_price_level_tool(
                curve_family="SOFR_FUT",
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
            "calculate_futures_price_level",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_price_level_tool(
                curve_family="SOFR_FUT",
                strip_position=1,
                as_of_date="2026-04-08",
            )
        params = spy.call_args.kwargs["params"]
        assert params.as_of_date == date(2026, 4, 8)

    def test_malformed_as_of_date_returns_envelope(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        output_json = mcp_module.get_futures_price_level_tool(
            curve_family="SOFR_FUT",
            strip_position=1,
            as_of_date="not-a-date",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "as_of_date" in parsed["error"]

    def test_validation_error_returns_controlled_envelope(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        # Pass an empty curve_family (min_length=1 fails) — must
        # produce the controlled {"error": ...} envelope, not raise.
        output_json = mcp_module.get_futures_price_level_tool(
            curve_family="",
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
        from rates_agent.policy_futures.tools.futures_price_level import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.policy_futures.tools.futures_price_level.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_policy_futures_price_level_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(FUTURES_PRICE_LEVEL_CONFIG_PATH)
        assert cfg.tool.name == "policy_futures_get_futures_price_level_tool"
        assert cfg.tool.domain == "policy_futures"


# ===========================================================================
# 3. Response-model validation (mock parity)
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        # If this raises, the wiring-test mock is out of sync with the
        # live schema (which is exactly the failure mode this guard
        # pins).
        validated = FuturesPriceLevelOutput.model_validate(_well_formed_output())
        assert validated.methodology_disclosure == _METHODOLOGY_DISCLOSURE_FIXTURE
        assert validated.current_metrics.raw_price == 96.12500
        assert validated.current_metrics.implied_rate_pct == 3.8750
        assert validated.current_metrics.inverse_priced is True
        assert validated.current_metrics.short_rate_regime == "RFR"

    def test_response_model_requires_methodology_disclosure(self):
        """Removing methodology_disclosure from the mock MUST raise —
        proves the field is REQUIRED on the response model, not
        optional. Without this guard, the P5 caveat could be dropped
        in a future refactor and the wiring tests would still pass."""
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad.pop("methodology_disclosure")
        with pytest.raises(ValidationError):
            FuturesPriceLevelOutput.model_validate(bad)

    def test_response_model_forbids_extra_keys(self):
        """frozen=True + extra='forbid' per typed-boundary
        discipline."""
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad["unexpected"] = "value"
        with pytest.raises(ValidationError):
            FuturesPriceLevelOutput.model_validate(bad)


# ===========================================================================
# 4. Workflow registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_registered_in_rates_primitive_resolver(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "policy_futures_get_futures_price_level_tool"
        )
        assert spec.tool_name == "policy_futures_get_futures_price_level_tool"
        assert spec.config_path == FUTURES_PRICE_LEVEL_CONFIG_PATH

    def test_output_field_units_declares_canonical_implied_rate(self):
        """ADR 0017: the single-unit-space canonical companion
        ``time_series_implied_rate`` is declared in PERCENT (the
        desk-recognised facet).  The bespoke two-unit-space
        ``time_series`` row list stays UNDECLARED so the validator
        refuses a binding the Series bridge cannot lift, and the
        raw-price facet is NOT exported canonically (per-contract
        quote space)."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "policy_futures_get_futures_price_level_tool"
        )
        assert spec.output_field_units == {
            "time_series_implied_rate": "percent",
        }

    def test_classified_bridgeable_series(self):
        """The composability audit must classify this primitive
        BRIDGEABLE_SERIES post-ADR-0017 (it was TERMINAL_ONLY_SNAPSHOT
        while output_field_units was empty — campaign refusals k03 /
        k04)."""
        from orchestrator.open_dag.composability_audit import (
            Composability,
            classify_primitive,
        )
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "policy_futures_get_futures_price_level_tool"
        )
        entry = classify_primitive(spec)
        assert entry.classification is Composability.BRIDGEABLE_SERIES

    def test_registered_input_and_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.policy_futures.tools.futures_price_level import (
            FuturesPriceLevelInput,
            FuturesPriceLevelOutput,
        )
        spec = rates_primitive_resolver(
            "policy_futures_get_futures_price_level_tool"
        )
        assert spec.input_class is FuturesPriceLevelInput
        assert spec.output_class is FuturesPriceLevelOutput
