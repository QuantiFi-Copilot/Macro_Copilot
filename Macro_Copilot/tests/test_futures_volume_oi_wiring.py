"""
test_futures_volume_oi_wiring.py — Caller-wiring smoke tests for the
                                    bond-futures volume + OI monitor

Mirrors test_futures_price_level_wiring.py for the futures_volume_oi
tool. Caller surface in V1:

  1. rates_agent/bond_futures/mcp_server.py::get_futures_volume_oi_tool

Key load-bearing properties under test:

  - MCP wrapper passes config= explicitly (no auto-load fallback).
  - The LLM-facing response withholds the historical ``time_series``
    rows but PRESERVES the ``methodology_disclosure`` (so the
    rolling-generic-OI caveat AND the OI z-score lookback window
    are propagated upward per the catalog's methodology guardrail).
  - The MCP wrapper does NOT accept a field_name parameter (the
    primitive's concept is tied to PX_VOLUME + OPEN_INT; exposing
    field_name would be input-schema overreach — see schemas.py
    module docstring).
  - Workflow registration is honest: PrimitiveSpec is registered with
    the correct CONFIG_PATH and with ``output_field_units = {}``
    (because the contract-count unit is not a member of the
    closed-enum TimeSeriesUnits family in V1 — see schemas module
    docstring + workflows registration comment).
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.bond_futures.tools.futures_volume_oi import (
    CONFIG_PATH as FUTURES_VOLUME_OI_CONFIG_PATH,
    FuturesVolumeOIOutput,
)
from shared.config import ToolConfig, clear_tool_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_output() -> dict:
    """Mock matches the live wire shape — MUST validate against
    ``FuturesVolumeOIOutput``."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family": "UST_FUT",
            "contract_code": "TY1",
            "tenor": "10Y",
            "contract_size": 100000.0,
            "expiry_date": "2026-12-21",
            "security_name": "TYZ6 COMB",
            "current_volume": 612345.0,
            "current_open_interest": 3045678.0,
            "delta_open_interest_1d": 12345.0,
            "oi_z_score": -0.42,
            "oi_high_252d": 3500000.0,
            "oi_low_252d": 2500000.0,
            "oi_percentile_252d": 54.6,
            "volume_rolling_mean_22d": 590000.0,
            "volume_rolling_max_22d": 1200000.0,
            "observation_count": 252,
        },
        "time_series": [
            {"date": "2026-04-29", "volume": 600000.0, "open_interest": 3033333.0},
            {"date": "2026-04-30", "volume": 612345.0, "open_interest": 3045678.0},
        ],
        "methodology_disclosure": (
            "OI z-score lookback = 252 trading days. This is rolling-"
            "generic open interest; the front-back OI migration is the "
            "positioning signal but the per-contract underlying rolls "
            "quarterly so the OI series mixes contracts across rolls "
            "(ADR 0013 — bond_futures V1 ships monitors only). The CTD "
            "identification, gross/net basis, implied repo, and "
            "DV01-weighted RV stack are Phase-4 work gated on D-repo + "
            "D-deliverable data ingestion."
        ),
    }


def _assert_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, "calculate_futures_volume_oi called without `config=`"
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "get_futures_volume_oi_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'get_futures_volume_oi_tool' (catches imports of the wrong "
        "tool's CONFIG_PATH)"
    )
    assert "oi_z_score_window_days" in cfg.conventions
    assert "default_volume_field" in cfg.conventions
    assert "default_open_interest_field" in cfg.conventions
    assert "volume_avg_window_days" in cfg.conventions


# ===========================================================================
# 1. MCP wrapper
# ===========================================================================

class TestMcpFuturesVolumeOIWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.bond_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_volume_oi",
            return_value=_well_formed_output(),
        ) as spy:
            output_json = mcp_module.get_futures_volume_oi_tool(
                curve_family="UST_FUT",
                contract_code="TY1",
                lookback_days=365,
            )
        assert spy.call_count == 1
        _assert_config_passed(spy.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        # P5 / catalog guardrail disclosure must reach the LLM.
        md = parsed.get("methodology_disclosure", "")
        assert "252" in md, (
            "MCP wrapper must propagate the OI z-score lookback "
            "verbatim (catalog methodology guardrail)"
        )
        assert "OI z-score lookback" in md
        # time_series stripped from the LLM-facing payload (frontend
        # REST gets the full payload via the dict result).
        assert "time_series" not in parsed

    def test_mcp_wrapper_has_no_field_name_param(self):
        """The MCP wrapper MUST NOT accept ``field_name`` — the volume /
        OI field mnemonics are YAML-owned. Exposing them as LLM inputs
        would be input-schema overreach (PR9 / OPR8)."""
        from rates_agent.bond_futures import mcp_server as mcp_module
        sig = inspect.signature(mcp_module.get_futures_volume_oi_tool)
        assert "field_name" not in sig.parameters, (
            "field_name must NOT be an LLM-controlled parameter on the "
            "volume/OI primitive (the field mnemonics are owned by "
            "config.yaml)"
        )

    def test_validation_error_returns_controlled_envelope(self):
        from rates_agent.bond_futures import mcp_server as mcp_module
        output_json = mcp_module.get_futures_volume_oi_tool(
            curve_family="",
            contract_code="TY1",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]


# ===========================================================================
# 2. CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.bond_futures.tools.futures_volume_oi import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.bond_futures.tools.futures_volume_oi.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_futures_volume_oi_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(FUTURES_VOLUME_OI_CONFIG_PATH)
        assert cfg.tool.name == "get_futures_volume_oi_tool"
        assert cfg.tool.domain == "bond_futures"


# ===========================================================================
# 3. Response-model validation (mock parity)
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        validated = FuturesVolumeOIOutput.model_validate(_well_formed_output())
        assert "252" in validated.methodology_disclosure
        assert validated.current_metrics.current_open_interest == 3045678.0

    def test_response_model_requires_methodology_disclosure(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad.pop("methodology_disclosure")
        with pytest.raises(ValidationError):
            FuturesVolumeOIOutput.model_validate(bad)


# ===========================================================================
# 4. Workflow registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_registered_in_rates_primitive_resolver(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver("get_futures_volume_oi_tool")
        assert spec.tool_name == "get_futures_volume_oi_tool"
        assert spec.config_path == FUTURES_VOLUME_OI_CONFIG_PATH

    def test_output_field_units_is_empty_by_design(self):
        """P8 / P5: bond-futures volume + OI live in contract-count
        space, which has no honest member of the closed-enum
        ``TimeSeriesUnits`` family in V1. Declaring ``percent`` /
        ``bps`` / ``count`` would silently lie. ``{}`` is the
        documented exempt mode (see shared/workflow/validate.py:368)
        until a future ADR extends TimeSeriesUnits with a CONTRACTS
        member."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver("get_futures_volume_oi_tool")
        assert spec.output_field_units == {}

    def test_registered_input_and_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.bond_futures.tools.futures_volume_oi import (
            FuturesVolumeOIInput,
            FuturesVolumeOIOutput,
        )
        spec = rates_primitive_resolver("get_futures_volume_oi_tool")
        assert spec.input_class is FuturesVolumeOIInput
        assert spec.output_class is FuturesVolumeOIOutput
