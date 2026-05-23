"""
test_futures_strip_snapshot_wiring.py — Caller-wiring smoke tests for
                                         the policy-futures whole-
                                         strip snapshot monitor

Mirrors the sibling tools' wiring tests. Caller surface in V1:

  1. rates_agent/policy_futures/mcp_server.py::get_futures_strip_snapshot_tool
  2. rates_agent/workflows/__init__.py::rates_primitive_resolver
     (registered as policy_futures_get_futures_strip_snapshot_tool)

Key load-bearing properties under test:

  - MCP wrapper passes config= explicitly (no auto-load fallback).
  - MCP wrapper uses the empty-string / None-sentinel pattern so the
    YAML defaults (PX_LAST / OPEN_INT) actually flow through.
  - MCP wrapper parses as_of_date ISO-format; empty string ⇒ None.
  - MCP wrapper returns the controlled-error envelope on Pydantic
    ValidationError (unknown curve_family hits the closed Literal).
  - The LLM-facing response surfaces the snapshot AND the output-level
    methodology_disclosure verbatim — the row count is small (V1 = 8)
    so the snapshot itself is not withheld.
  - Workflow registration is honest: PrimitiveSpec is registered with
    the correct CONFIG_PATH AND the exempt ``output_field_units={}``
    declaration (rows mix raw_price / implied_rate_pct / z_score /
    open_interest in semantically distinct unit spaces — declaring
    a single unit would silently lie under P8 + P5).
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.policy_futures.tools.futures_strip_snapshot import (
    CONFIG_PATH as FUTURES_STRIP_SNAPSHOT_CONFIG_PATH,
    FuturesStripSnapshotOutput,
)
from shared.config import ToolConfig, clear_tool_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


_METHODOLOGY_DISCLOSURE_FIXTURE: str = (
    "Rolling-generic strip snapshot for SOFR_FUT across "
    "strip_positions=[1,2,3,4,5,6,7,8]; the desk-recognised reading "
    "is the WHOLE STRIP on the aligned ``as_of_date``. Underlying "
    "short-rate regime: RFR (RFR = compounded daily risk-free rate; "
    "IBOR = unsecured 3M term IBOR). Inverse-priced strip — per "
    "leg, implied_rate_pct = 100 - raw_price; the snapshot's daily-"
    "change column is the raw subtraction on the implied-rate axis "
    "(PERCENT POINTS). Per-leg z-score lookback = 252 trading days "
    "on the implied-rate level series. This is NOT a CTD-of-futures-"
    "of-OIS strip read; the CTD-implied-OIS curve is not yet a "
    "primitive in this build (ADR 0011 V1 scope — policy_futures "
    "ships strip-position-keyed monitors only). The per-contract "
    "underlying rolls quarterly so each strip slot mixes contracts "
    "across rolls; this is the canonical desk read but it does NOT "
    "equal the price of a single underlying contract over time."
)


_ROW_METHODOLOGY_CARD_FIXTURE: str = (
    "SOFR_FUT row — underlying short-rate regime: RFR (RFR = "
    "compounded daily risk-free rate; IBOR = unsecured 3M term IBOR). "
    "Implied-rate conversion: implied_rate_pct = 100 - raw_price "
    "(inverse-priced strip). Per-leg z-score lookback = 252 trading "
    "days on the implied-rate level series."
)


def _well_formed_output() -> dict:
    """Mock matches the live wire shape — MUST validate against
    ``FuturesStripSnapshotOutput``."""
    rows = []
    for p in range(1, 9):
        rows.append({
            "strip_position": p,
            "contract_code": f"SFR{p}",
            "underlying_contract_code": f"SFR{p}M26",
            "security_name": f"SFR{p}M26 COMB",
            "expiry_date": "2026-06-16",
            "contract_size": 2500.0,
            "raw_price": 95.5 - 0.05 * (p - 1),
            "implied_rate_pct": 4.5 + 0.05 * (p - 1),
            "daily_change_implied_rate_pct": 0.0025 * (-1) ** p,
            "z_score_implied_rate": -0.4 + 0.1 * p,
            "open_interest": 1_000_000 - 80_000 * (p - 1),
            "row_methodology_card": _ROW_METHODOLOGY_CARD_FIXTURE,
        })
    return {
        "as_of_date": "2026-04-30",
        "curve_family": "SOFR_FUT",
        "inverse_priced": True,
        "short_rate_regime": "RFR",
        "quote_units": "100 - rate",
        "strip_positions": [1, 2, 3, 4, 5, 6, 7, 8],
        "snapshot": rows,
        "observation_count": 252,
        "methodology_disclosure": _METHODOLOGY_DISCLOSURE_FIXTURE,
    }


def _assert_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_futures_strip_snapshot called without `config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == (
        "policy_futures_get_futures_strip_snapshot_tool"
    ), (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'policy_futures_get_futures_strip_snapshot_tool' (catches "
        "imports of the wrong tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_price_field" in cfg.conventions
    assert "default_open_interest_field" in cfg.conventions
    assert "strip_positions" in cfg.conventions
    assert "short_rate_regime_map" in cfg.conventions
    assert "raw_price_round_decimals" in cfg.conventions


# ===========================================================================
# 1. MCP wrapper
# ===========================================================================

class TestMcpFuturesStripSnapshotWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_strip_snapshot",
            return_value=_well_formed_output(),
        ) as spy:
            output_json = mcp_module.get_futures_strip_snapshot_tool(
                curve_family="SOFR_FUT",
                last_price_field_name="PX_LAST",
                open_interest_field_name="OPEN_INT",
            )
        assert spy.call_count == 1
        _assert_config_passed(spy.call_args)
        parsed = json.loads(output_json)
        # Snapshot surfaced verbatim — small row count (V1 = 8) so not
        # withheld from the LLM.
        assert "snapshot" in parsed
        assert len(parsed["snapshot"]) == 8
        # P5 disclosure must reach the LLM-facing payload.
        assert parsed.get("methodology_disclosure") == (
            _METHODOLOGY_DISCLOSURE_FIXTURE
        )

    def test_field_name_defaults_are_empty_string_sentinels(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(
            mcp_module.get_futures_strip_snapshot_tool
        )
        assert sig.parameters["last_price_field_name"].default == ""
        assert sig.parameters["open_interest_field_name"].default == ""

    def test_as_of_date_default_is_empty_string_sentinel(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(
            mcp_module.get_futures_strip_snapshot_tool
        )
        assert sig.parameters["as_of_date"].default == ""

    def test_omitted_field_names_flow_none_to_input(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_strip_snapshot",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_strip_snapshot_tool(
                curve_family="SOFR_FUT",
            )
        params = spy.call_args.kwargs["params"]
        assert params.last_price_field_name is None, (
            f"omitted last_price_field_name must reach "
            f"FuturesStripSnapshotInput as None, got "
            f"{params.last_price_field_name!r}"
        )
        assert params.open_interest_field_name is None
        assert params.as_of_date is None

    def test_explicit_field_names_pass_through(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_strip_snapshot",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_strip_snapshot_tool(
                curve_family="SOFR_FUT",
                last_price_field_name="PX_BID",
                open_interest_field_name="OPEN_INT",
            )
        params = spy.call_args.kwargs["params"]
        assert params.last_price_field_name == "PX_BID"
        assert params.open_interest_field_name == "OPEN_INT"

    def test_whitespace_only_field_name_coalesces_to_none(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_strip_snapshot",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_strip_snapshot_tool(
                curve_family="SOFR_FUT",
                last_price_field_name="",
                open_interest_field_name="",
            )
        params = spy.call_args.kwargs["params"]
        # Empty strings must coalesce to None.
        assert params.last_price_field_name is None
        assert params.open_interest_field_name is None

    def test_explicit_as_of_date_parses_iso(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_strip_snapshot",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_strip_snapshot_tool(
                curve_family="SOFR_FUT",
                as_of_date="2026-04-08",
            )
        params = spy.call_args.kwargs["params"]
        assert params.as_of_date == date(2026, 4, 8)

    def test_malformed_as_of_date_returns_envelope(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        output_json = mcp_module.get_futures_strip_snapshot_tool(
            curve_family="SOFR_FUT",
            as_of_date="not-a-date",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "as_of_date" in parsed["error"]

    def test_validation_error_returns_controlled_envelope(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        # Unknown curve_family fails the closed Literal.
        output_json = mcp_module.get_futures_strip_snapshot_tool(
            curve_family="USD_OIS",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_tool_count_in_mcp_module(self):
        """The MCP server must register all six policy_futures tools
        (futures_price_level, volume_open_interest_snapshot,
        futures_calendar_spread, futures_butterfly_simple,
        futures_cross_market_spread, futures_strip_snapshot). Catches
        accidental registration removals."""
        from rates_agent.policy_futures import mcp_server as mcp_module
        registered = [
            name for name in dir(mcp_module)
            if name.startswith("get_") or name.startswith("calculate_")
        ]
        registered_set = set(registered)
        # The tool functions are decorated by @mcp.tool() but stay
        # importable on the module.
        for expected in (
            "get_futures_price_level_tool",
            "get_volume_open_interest_snapshot_tool",
            "get_futures_calendar_spread_tool",
            "get_futures_butterfly_simple_tool",
            "get_futures_cross_market_spread_tool",
            "get_futures_strip_snapshot_tool",
        ):
            assert expected in registered_set, (
                f"MCP server missing tool registration {expected!r}; "
                f"registered tools: {sorted(registered_set)}"
            )


# ===========================================================================
# 2. CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.policy_futures.tools.futures_strip_snapshot import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.policy_futures.tools.futures_strip_snapshot.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_policy_futures_strip_snapshot_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(FUTURES_STRIP_SNAPSHOT_CONFIG_PATH)
        assert cfg.tool.name == (
            "policy_futures_get_futures_strip_snapshot_tool"
        )
        assert cfg.tool.domain == "policy_futures"


# ===========================================================================
# 3. Response-model validation (mock parity)
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        validated = FuturesStripSnapshotOutput.model_validate(
            _well_formed_output()
        )
        assert validated.methodology_disclosure == (
            _METHODOLOGY_DISCLOSURE_FIXTURE
        )
        assert validated.inverse_priced is True
        assert validated.short_rate_regime == "RFR"
        assert validated.quote_units == "100 - rate"
        assert validated.strip_positions == [1, 2, 3, 4, 5, 6, 7, 8]
        assert len(validated.snapshot) == 8
        assert validated.snapshot[0].strip_position == 1
        assert validated.snapshot[0].contract_code == "SFR1"
        assert validated.snapshot[0].row_methodology_card == (
            _ROW_METHODOLOGY_CARD_FIXTURE
        )

    def test_response_model_requires_methodology_disclosure(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad.pop("methodology_disclosure")
        with pytest.raises(ValidationError):
            FuturesStripSnapshotOutput.model_validate(bad)

    def test_response_model_requires_per_row_methodology_card(self):
        """The per-row methodology card is REQUIRED on every row;
        catalog standardness guardrail — a desk consumer copying a
        single row must still see the regime + conversion-rule
        disclosure on that row."""
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad["snapshot"][0].pop("row_methodology_card")
        with pytest.raises(ValidationError):
            FuturesStripSnapshotOutput.model_validate(bad)

    def test_response_model_forbids_extra_keys(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad["unexpected"] = "value"
        with pytest.raises(ValidationError):
            FuturesStripSnapshotOutput.model_validate(bad)


# ===========================================================================
# 4. Workflow registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_registered_in_rates_primitive_resolver(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "policy_futures_get_futures_strip_snapshot_tool"
        )
        assert spec.tool_name == (
            "policy_futures_get_futures_strip_snapshot_tool"
        )
        assert spec.config_path == FUTURES_STRIP_SNAPSHOT_CONFIG_PATH

    def test_output_field_units_empty_by_design(self):
        """Rows mix raw_price (contract quote space), implied_rate_pct
        (PERCENT), daily_change (PERCENT POINTS), z_score (unit-less),
        and open_interest (CONTRACTS). The closed-enum
        ``TimeSeriesUnits`` family has no PRICE / CONTRACTS members in
        V1; declaring a single unit would silently lie under P8 + P5.
        Same exempt-snapshot pattern the sibling
        scan_bond_futures_extremes_tool / sovereign_yield_panel use."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "policy_futures_get_futures_strip_snapshot_tool"
        )
        assert spec.output_field_units == {}

    def test_registered_input_and_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.policy_futures.tools.futures_strip_snapshot import (
            FuturesStripSnapshotInput,
            FuturesStripSnapshotOutput,
        )
        spec = rates_primitive_resolver(
            "policy_futures_get_futures_strip_snapshot_tool"
        )
        assert spec.input_class is FuturesStripSnapshotInput
        assert spec.output_class is FuturesStripSnapshotOutput
