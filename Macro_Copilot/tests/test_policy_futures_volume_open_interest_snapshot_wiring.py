"""
test_policy_futures_volume_open_interest_snapshot_wiring.py — Caller-
wiring smoke tests for the policy-futures volume + OI snapshot monitor

Mirrors test_policy_futures_futures_price_level_wiring.py for the
volume_open_interest_snapshot tool. Caller surface in V1:

  1. rates_agent/policy_futures/mcp_server.py
     ::get_volume_open_interest_snapshot_tool
  2. rates_agent/workflows/__init__.py::rates_primitive_resolver
     (registered as
      policy_futures_get_volume_open_interest_snapshot_tool)

Key load-bearing properties under test:

  - MCP wrapper passes config= explicitly (no auto-load fallback).
  - The LLM-facing response withholds the historical ``time_series``
    rows but PRESERVES the ``methodology_disclosure`` (so the
    rolling-generic-strip OI caveat AND the OI z-score lookback
    window are propagated upward per the catalog's standardness
    guardrail).
  - The MCP wrapper does NOT accept a field_name parameter (the
    primitive's concept is tied to PX_VOLUME + OPEN_INT; exposing
    field_name would be input-schema overreach — see schemas.py
    module docstring).
  - The MCP wrapper parses as_of_date ISO-format via
    date.fromisoformat; empty string ⇒ None (post-fetch data-max
    anchor); malformed ⇒ controlled error envelope.
  - Workflow registration is honest: PrimitiveSpec is registered with
    the correct CONFIG_PATH and with ``output_field_units = {}``
    (because the contract-count unit is not a member of the closed-
    enum TimeSeriesUnits family in V1 — see schemas module docstring
    + workflows registration comment).
"""

from __future__ import annotations

import inspect
import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.policy_futures.tools.volume_open_interest_snapshot import (
    CONFIG_PATH as VOLUME_OPEN_INTEREST_SNAPSHOT_CONFIG_PATH,
    VolumeOpenInterestSnapshotOutput,
)
from shared.config import ToolConfig, clear_tool_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


_METHODOLOGY_DISCLOSURE_FIXTURE: str = (
    "OI z-score lookback = 252 trading days on the strip-slot "
    "open-interest series. This is the rolling-generic strip slot's "
    "open interest; the per-contract underlying rolls quarterly so "
    "the OI series mixes contracts across rolls (ADR 0013 — "
    "policy_futures V1 ships strip-position-keyed monitors only). "
    "Front-back OI migration as a positioning signal cannot be "
    "expressed as a single-strip-slot primitive — that is future "
    "cross-strip work."
)


def _well_formed_output() -> dict:
    """Mock matches the live wire shape — MUST validate against
    ``VolumeOpenInterestSnapshotOutput``."""
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
            "current_volume": 612345.0,
            "current_open_interest": 1045678.0,
            "delta_open_interest_1d": 12345.0,
            "oi_z_score": -0.42,
            "oi_high_252d": 1500000.0,
            "oi_low_252d": 800000.0,
            "oi_percentile_252d": 54.6,
            "volume_rolling_mean_22d": 590000.0,
            "volume_rolling_max_22d": 1200000.0,
            "observation_count": 252,
        },
        "time_series": [
            {"date": "2026-04-29", "volume": 600000.0, "open_interest": 1033333.0},
            {"date": "2026-04-30", "volume": 612345.0, "open_interest": 1045678.0},
        ],
        "methodology_disclosure": _METHODOLOGY_DISCLOSURE_FIXTURE,
    }


def _assert_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_volume_open_interest_snapshot called without `config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == (
        "policy_futures_get_volume_open_interest_snapshot_tool"
    ), (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'policy_futures_get_volume_open_interest_snapshot_tool' "
        "(catches imports of the wrong tool's CONFIG_PATH)"
    )
    assert "oi_z_score_window_days" in cfg.conventions
    assert "default_volume_field" in cfg.conventions
    assert "default_open_interest_field" in cfg.conventions
    assert "volume_avg_window_days" in cfg.conventions


# ===========================================================================
# 1. MCP wrapper
# ===========================================================================

class TestMcpVolumeOpenInterestSnapshotWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_volume_open_interest_snapshot",
            return_value=_well_formed_output(),
        ) as spy:
            output_json = mcp_module.get_volume_open_interest_snapshot_tool(
                curve_family="SOFR_FUT",
                strip_position=1,
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
            "verbatim (catalog standardness guardrail)"
        )
        assert "OI z-score lookback" in md
        # time_series stripped from the LLM-facing payload (frontend
        # REST gets the full payload via the dict result).
        assert "time_series" not in parsed

    def test_mcp_wrapper_has_no_field_name_param(self):
        """The MCP wrapper MUST NOT accept ``field_name`` — the volume /
        OI field mnemonics are YAML-owned. Exposing them as LLM inputs
        would be input-schema overreach (PR9 / OPR8)."""
        from rates_agent.policy_futures import mcp_server as mcp_module
        sig = inspect.signature(
            mcp_module.get_volume_open_interest_snapshot_tool
        )
        assert "field_name" not in sig.parameters, (
            "field_name must NOT be an LLM-controlled parameter on the "
            "volume/OI primitive (the field mnemonics are owned by "
            "config.yaml)"
        )

    def test_as_of_date_default_is_empty_string_sentinel(self):
        """The MCP wrapper's as_of_date default must be the empty-
        string sentinel, NOT an ISO date. Hardcoding a date here
        defeats the post-fetch data-max anchor."""
        from rates_agent.policy_futures import mcp_server as mcp_module
        sig = inspect.signature(
            mcp_module.get_volume_open_interest_snapshot_tool
        )
        default = sig.parameters["as_of_date"].default
        assert default == "", (
            f"MCP wrapper's as_of_date default must be '' (empty-string "
            f"sentinel for 'anchor at data max'); got {default!r}"
        )

    def test_omitted_as_of_date_flows_none_to_input(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_volume_open_interest_snapshot",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_volume_open_interest_snapshot_tool(
                curve_family="SOFR_FUT",
                strip_position=1,
                lookback_days=365,
            )
        params = spy.call_args.kwargs["params"]
        assert params.as_of_date is None, (
            f"omitted as_of_date must reach the input as None "
            f"(sentinel for 'anchor at data max'), got "
            f"{params.as_of_date!r}"
        )

    def test_explicit_as_of_date_parses_iso(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_volume_open_interest_snapshot",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_volume_open_interest_snapshot_tool(
                curve_family="SOFR_FUT",
                strip_position=1,
                as_of_date="2026-04-08",
            )
        params = spy.call_args.kwargs["params"]
        assert params.as_of_date == date(2026, 4, 8)

    def test_malformed_as_of_date_returns_envelope(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        output_json = mcp_module.get_volume_open_interest_snapshot_tool(
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
        output_json = mcp_module.get_volume_open_interest_snapshot_tool(
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
        from rates_agent.policy_futures.tools.volume_open_interest_snapshot import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.policy_futures.tools.volume_open_interest_snapshot.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_volume_open_interest_snapshot_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(VOLUME_OPEN_INTEREST_SNAPSHOT_CONFIG_PATH)
        assert cfg.tool.name == (
            "policy_futures_get_volume_open_interest_snapshot_tool"
        )
        assert cfg.tool.domain == "policy_futures"


# ===========================================================================
# 3. Response-model validation (mock parity)
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        # If this raises, the wiring-test mock is out of sync with the
        # live schema (which is exactly the failure mode this guard
        # pins).
        validated = VolumeOpenInterestSnapshotOutput.model_validate(
            _well_formed_output()
        )
        assert "252" in validated.methodology_disclosure
        assert validated.current_metrics.current_open_interest == 1045678.0
        assert validated.current_metrics.current_volume == 612345.0
        assert validated.current_metrics.strip_position == 1

    def test_response_model_requires_methodology_disclosure(self):
        """Removing methodology_disclosure from the mock MUST raise —
        proves the field is REQUIRED on the response model, not
        optional. Without this guard, the P5 caveat could be dropped
        in a future refactor and the wiring tests would still pass."""
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad.pop("methodology_disclosure")
        with pytest.raises(ValidationError):
            VolumeOpenInterestSnapshotOutput.model_validate(bad)

    def test_response_model_forbids_extra_keys(self):
        """frozen=True + extra='forbid' per typed-boundary
        discipline."""
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad["unexpected"] = "value"
        with pytest.raises(ValidationError):
            VolumeOpenInterestSnapshotOutput.model_validate(bad)


# ===========================================================================
# 4. Workflow registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_registered_in_rates_primitive_resolver(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "policy_futures_get_volume_open_interest_snapshot_tool"
        )
        assert spec.tool_name == (
            "policy_futures_get_volume_open_interest_snapshot_tool"
        )
        assert spec.config_path == VOLUME_OPEN_INTEREST_SNAPSHOT_CONFIG_PATH

    def test_output_field_units_is_empty_by_design(self):
        """P8 / P5: policy-futures volume + OI live in contract-count
        space, which has no honest member of the closed-enum
        ``TimeSeriesUnits`` family in V1. Declaring ``percent`` /
        ``bps`` / ``count`` would silently lie. ``{}`` is the
        documented exempt mode (see shared/workflow/validate.py:368)
        until a future ADR extends TimeSeriesUnits with a CONTRACTS
        member."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "policy_futures_get_volume_open_interest_snapshot_tool"
        )
        assert spec.output_field_units == {}

    def test_registered_input_and_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.policy_futures.tools.volume_open_interest_snapshot import (
            VolumeOpenInterestSnapshotInput,
            VolumeOpenInterestSnapshotOutput,
        )
        spec = rates_primitive_resolver(
            "policy_futures_get_volume_open_interest_snapshot_tool"
        )
        assert spec.input_class is VolumeOpenInterestSnapshotInput
        assert spec.output_class is VolumeOpenInterestSnapshotOutput
