"""
test_policy_futures_futures_pack_average_simple_wiring.py — Caller-
wiring smoke tests for the policy-futures pack-average monitor.

Mirrors the sibling tools' wiring tests. Caller surface in V1:

  1. rates_agent/policy_futures/mcp_server.py::get_futures_pack_average_simple_tool
  2. rates_agent/workflows/__init__.py::rates_primitive_resolver
     (registered as policy_futures_get_futures_pack_average_simple_tool)

Key load-bearing properties under test:

  - MCP wrapper passes config= explicitly (no auto-load fallback).
  - MCP wrapper uses the empty-string / None-sentinel pattern so
    the YAML default_price_field actually flows through.
  - MCP wrapper parses as_of_date ISO-format; empty string ⇒ None.
  - The LLM-facing response withholds the historical
    ``time_series`` rows (bespoke + both canonical TimeSeries) but
    PRESERVES the P5 ``methodology_disclosure``.
  - Workflow registration is honest: PrimitiveSpec is registered
    with the correct CONFIG_PATH and with the canonical TimeSeries
    ``output_field_units`` declarations (percent / z_score).
  - The MCP wrapper catches the ADR 0013 V1 EUR_SHORT_RATE_FUT
    NotImplementedError refusal and surfaces a clean
    ``{"error": "..."}`` envelope naming the missing
    ``delivery_month_type`` metadata + ADR 0013.
  - The MCP server module surfaces exactly 7 ``@mcp.tool()``
    wrappers (the pack-average primitive is Tool #7).
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.policy_futures.tools.futures_pack_average_simple import (
    CONFIG_PATH as FUTURES_PACK_AVERAGE_SIMPLE_CONFIG_PATH,
    FuturesPackAverageSimpleOutput,
)
from shared.config import ToolConfig, clear_tool_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


_METHODOLOGY_DISCLOSURE_FIXTURE: str = (
    "Rolling-generic strip pack average at SOFR_FUT pack=whites "
    "(strip positions [1,2,3,4], master stems [SFR1,SFR2,SFR3,"
    "SFR4]). Weighting: SIMPLE ARITHMETIC MEAN (each pack member "
    "carries weight 1/4 = 0.25). The desk-recognised quantity is "
    "the pack-average implied rate in PERCENT. Underlying short-"
    "rate regime: RFR (RFR = compounded daily risk-free rate; "
    "IBOR = unsecured 3M term IBOR). Inverse-priced strip — per "
    "leg, implied_rate_pct = 100 - raw_price; the pack average on "
    "the implied-rate axis equals the simple arithmetic mean of "
    "the four legs' implied rates. Z-score lookback = 252 trading "
    "days on the PACK-AVERAGE series; trailing range window = 252 "
    "trading days. This is NOT a CTD-of-futures-of-OIS pack "
    "average; the CTD-implied-OIS curve is not yet a primitive in "
    "this build. This is also NOT a meeting-by-meeting policy-"
    "path decomposition. This is also NOT a duration-weighted / "
    "DV01-weighted / regression-fitted pack average — those "
    "weighting variants are planned-extension territory and ship "
    "as separate primitives (ADR 0013 V1 scope — policy_futures "
    "ships strip-position-keyed monitors only)."
)


def _well_formed_output() -> dict:
    """Mock matches the live wire shape — MUST validate against
    ``FuturesPackAverageSimpleOutput``."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family": "SOFR_FUT",
            "pack": "whites",
            "strip_positions": [1, 2, 3, 4],
            "pack_label": "SOFR_FUT whites (SFR1..SFR4)",
            "contract_codes": ["SFR1", "SFR2", "SFR3", "SFR4"],
            "underlying_contract_codes": [
                "SFRM26", "SFRU26", "SFRZ26", "SFRH27",
            ],
            "security_names": [
                "SFRM26 COMB", "SFRU26 COMB",
                "SFRZ26 COMB", "SFRH27 COMB",
            ],
            "expiry_dates": [
                "2026-06-16", "2026-09-15",
                "2026-12-15", "2027-03-15",
            ],
            "inverse_priced": True,
            "short_rate_regime": "RFR",
            "implied_rates_pct": [4.1234, 4.0000, 3.9000, 3.8000],
            "pack_average_implied_rate_pct": 3.9559,
            "daily_change_pack_average_implied_rate_pct": 0.0050,
            "z_score_pack_average": -0.4500,
            "high_252d_pack_average_implied_rate_pct": 4.5000,
            "low_252d_pack_average_implied_rate_pct": 3.5000,
            "mid_252d_pack_average_implied_rate_pct": 4.0000,
            "percentile_252d": 45.6,
            "rolling_window_days": 252,
            "observation_count": 252,
        },
        "time_series": [
            {
                "date": "2026-04-29",
                "pack_average_implied_rate_pct": 3.9509,
                "z_score": -0.4700,
            },
            {
                "date": "2026-04-30",
                "pack_average_implied_rate_pct": 3.9559,
                "z_score": -0.4500,
            },
        ],
        "time_series_pack_average": {
            "series_name": "sofr_fut_whites_pack_average",
            "units": "percent",
            "description": "SOFR_FUT whites pack average.",
            "rows": [
                {"date": "2026-04-29", "value": 3.9509},
                {"date": "2026-04-30", "value": 3.9559},
            ],
        },
        "time_series_zscore": {
            "series_name": "sofr_fut_whites_zscore",
            "units": "z_score",
            "description": "Pack-average z-score.",
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
        "calculate_futures_pack_average_simple called without "
        "`config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == (
        "policy_futures_get_futures_pack_average_simple_tool"
    ), (
        f"caller passed config for tool {cfg.tool.name!r}, "
        "expected 'policy_futures_get_futures_pack_average_simple_tool' "
        "(catches imports of the wrong tool's CONFIG_PATH)"
    )
    assert "whites_strip_positions" in cfg.conventions
    assert "reds_strip_positions" in cfg.conventions
    assert "z_score_window_days" in cfg.conventions
    assert "default_price_field" in cfg.conventions
    assert "pack_average_round_decimals" in cfg.conventions
    assert "short_rate_regime_map" in cfg.conventions
    assert "trailing_range_window_days" in cfg.conventions


# ===========================================================================
# 1. MCP wrapper
# ===========================================================================

class TestMcpFuturesPackAverageSimpleWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_pack_average_simple",
            return_value=_well_formed_output(),
        ) as spy:
            output_json = mcp_module.get_futures_pack_average_simple_tool(
                curve_family="SOFR_FUT",
                pack="whites",
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
        assert "time_series_pack_average" not in parsed
        assert "time_series_zscore" not in parsed

    def test_field_name_default_is_empty_string_sentinel(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(
            mcp_module.get_futures_pack_average_simple_tool,
        )
        default = sig.parameters["field_name"].default
        assert default == "", (
            f"MCP wrapper's field_name default must be '' (empty-"
            f"string sentinel); got {default!r}"
        )

    def test_as_of_date_default_is_empty_string_sentinel(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(
            mcp_module.get_futures_pack_average_simple_tool,
        )
        default = sig.parameters["as_of_date"].default
        assert default == "", (
            f"MCP wrapper's as_of_date default must be '' "
            f"(empty-string sentinel); got {default!r}"
        )

    def test_omitted_field_name_flows_none_to_input(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_pack_average_simple",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_pack_average_simple_tool(
                curve_family="SOFR_FUT",
                pack="whites",
                lookback_days=365,
            )
        params = spy.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach "
            f"FuturesPackAverageSimpleInput as None, got "
            f"{params.field_name!r}"
        )
        assert params.as_of_date is None, (
            f"omitted as_of_date must reach "
            f"FuturesPackAverageSimpleInput as None, got "
            f"{params.as_of_date!r}"
        )

    def test_explicit_field_name_passes_through(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_pack_average_simple",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_pack_average_simple_tool(
                curve_family="SOFR_FUT",
                pack="whites",
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
            "calculate_futures_pack_average_simple",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_pack_average_simple_tool(
                curve_family="SOFR_FUT",
                pack="whites",
                as_of_date="2026-04-08",
            )
        params = spy.call_args.kwargs["params"]
        assert params.as_of_date == date(2026, 4, 8)

    def test_malformed_as_of_date_returns_envelope(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        output_json = mcp_module.get_futures_pack_average_simple_tool(
            curve_family="SOFR_FUT",
            pack="whites",
            as_of_date="not-a-date",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "as_of_date" in parsed["error"]

    def test_validation_error_returns_controlled_envelope(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        # Unknown curve_family fails the closed Literal
        output_json = mcp_module.get_futures_pack_average_simple_tool(
            curve_family="UNKNOWN_FUT",
            pack="whites",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_invalid_pack_returns_controlled_envelope(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        # Unknown pack fails the closed Literal
        output_json = mcp_module.get_futures_pack_average_simple_tool(
            curve_family="SOFR_FUT",
            pack="greens",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]


# ===========================================================================
# 2. EUR_SHORT_RATE_FUT refusal at MCP boundary
# ===========================================================================

class TestEurShortRateFutRefusedAtMcp:
    """The MCP wrapper MUST catch the compute layer's
    NotImplementedError and serialise it as a clean
    ``{"error": "..."}`` envelope so the LLM sees a stable wire
    shape (NOT a stack trace)."""

    def test_eur_short_rate_fut_returns_clean_envelope_pr11(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.get_futures_pack_average_simple_tool(
                curve_family="EUR_SHORT_RATE_FUT",
                pack="whites",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        # Envelope must carry the playbook-metadata unblock reason
        # AND the ADR-0011 citation so the desk reader can follow
        # the disclosure trail without inspecting the source.
        assert "delivery_month_type" in parsed["error"]
        assert "ADR 0013" in parsed["error"]
        assert "EUR_SHORT_RATE_FUT" in parsed["error"]

    def test_eur_refusal_also_fires_on_reds(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.get_futures_pack_average_simple_tool(
                curve_family="EUR_SHORT_RATE_FUT",
                pack="reds",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "delivery_month_type" in parsed["error"]
        assert "ADR 0013" in parsed["error"]


# ===========================================================================
# 3. CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.policy_futures.tools.futures_pack_average_simple import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.policy_futures.tools.futures_pack_average_simple.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_policy_futures_pack_average_simple_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(FUTURES_PACK_AVERAGE_SIMPLE_CONFIG_PATH)
        assert cfg.tool.name == (
            "policy_futures_get_futures_pack_average_simple_tool"
        )
        assert cfg.tool.domain == "policy_futures"


# ===========================================================================
# 4. Response-model validation (mock parity)
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        validated = FuturesPackAverageSimpleOutput.model_validate(
            _well_formed_output()
        )
        assert validated.methodology_disclosure == (
            _METHODOLOGY_DISCLOSURE_FIXTURE
        )
        assert (
            validated.current_metrics.pack_average_implied_rate_pct
            == 3.9559
        )
        assert validated.current_metrics.inverse_priced is True
        assert validated.current_metrics.short_rate_regime == "RFR"
        assert validated.current_metrics.rolling_window_days == 252
        assert validated.time_series_pack_average.units.value == "percent"
        assert validated.time_series_zscore.units.value == "z_score"

    def test_response_model_requires_methodology_disclosure(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad.pop("methodology_disclosure")
        with pytest.raises(ValidationError):
            FuturesPackAverageSimpleOutput.model_validate(bad)

    def test_response_model_forbids_extra_keys(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad["unexpected"] = "value"
        with pytest.raises(ValidationError):
            FuturesPackAverageSimpleOutput.model_validate(bad)


# ===========================================================================
# 5. Workflow registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_registered_in_rates_primitive_resolver(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "policy_futures_get_futures_pack_average_simple_tool"
        )
        assert spec.tool_name == (
            "policy_futures_get_futures_pack_average_simple_tool"
        )
        assert spec.config_path == FUTURES_PACK_AVERAGE_SIMPLE_CONFIG_PATH

    def test_output_field_units_declared_honestly(self):
        """The pack-average series IS single-unit (PERCENT — simple
        arithmetic mean of four implied rates) so we DO declare the
        canonical-series unit tags. ``time_series`` (bespoke) /
        ``time_series_pack_average`` (canonical) both carry PERCENT;
        ``time_series_zscore`` carries Z_SCORE."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "policy_futures_get_futures_pack_average_simple_tool"
        )
        assert spec.output_field_units == {
            "time_series": "percent",
            "time_series_pack_average": "percent",
            "time_series_zscore": "z_score",
        }

    def test_registered_input_and_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.policy_futures.tools.futures_pack_average_simple import (
            FuturesPackAverageSimpleInput,
            FuturesPackAverageSimpleOutput,
        )
        spec = rates_primitive_resolver(
            "policy_futures_get_futures_pack_average_simple_tool"
        )
        assert spec.input_class is FuturesPackAverageSimpleInput
        assert spec.output_class is FuturesPackAverageSimpleOutput


# ===========================================================================
# 6. MCP module-level tool count
# ===========================================================================
# Pack-average was the 7th wrapper to land; the scan-extremes primitive
# (catalog id ``policy_futures__scan_policy_futures_extremes``,
# build_order 29) added the 8th; the build_policy_futures_strip_panel
# primitive (catalog id
# ``policy_futures__build_policy_futures_strip_panel``, build_order
# 32) added the 9th. The cross-domain count assertion tracks the
# policy_futures MCP surface as a whole — it lives in the pack-average
# wiring test for historical reasons (pack-average was the last of the
# per-strip / curve-shape primitives before the scanner landed), but
# the assertion is domain-wide rather than pack-average-specific.
# Bumping the expected set to 9 is the minimal wiring touchpoint the
# panel primitive's landing requires.

class TestMcpToolCount:
    def test_policy_futures_mcp_module_exposes_nine_tools(self):
        """After the build_policy_futures_strip_panel primitive
        lands (V1 substrate #9), the policy_futures MCP module must
        expose exactly 9 ``@mcp.tool()``-decorated wrappers
        (price_level, volume_oi, calendar_spread, butterfly_simple,
        cross_market_spread, strip_snapshot, pack_average_simple,
        scan_extremes, build_policy_futures_strip_panel)."""
        from rates_agent.policy_futures import mcp_server as mcp_module
        wrappers = sorted(
            name for name in dir(mcp_module)
            if name.endswith("_tool")
            and callable(getattr(mcp_module, name))
        )
        # Filter out any accidental private helpers
        assert wrappers == sorted([
            "get_futures_price_level_tool",
            "get_volume_open_interest_snapshot_tool",
            "get_futures_calendar_spread_tool",
            "get_futures_butterfly_simple_tool",
            "get_futures_cross_market_spread_tool",
            "get_futures_strip_snapshot_tool",
            "get_futures_pack_average_simple_tool",
            "get_scan_policy_futures_extremes_tool",
            "build_policy_futures_strip_panel_tool",
        ]), wrappers
