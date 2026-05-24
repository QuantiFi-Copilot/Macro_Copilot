"""
test_wirp_meeting_pricing_wiring.py — Caller-wiring smoke tests for WIRP
========================================================================

Pins the MCP wrapper's load-bearing properties + the workflow
registry's WORKFLOW_INCOMPATIBLE_TOOLS membership (the primitive is
deliberately NOT bridge-composable — list-shaped categorical output
per the get_otr_history / classify_curve_move precedent).

Wrapper contract:
  - Imports CONFIG_PATH + compute function from the package init.
  - Loads bundled config explicitly with
    ``load_tool_config(WIRP_MEETING_PRICING_CONFIG_PATH)`` and
    passes ``config=`` to the compute function (PR7 +
    DESIGN_PRINCIPLES §8).
  - Translates the two wire sentinels (``n_meetings=0`` → None;
    ``meeting_date=''`` → None) so the Pydantic
    ``@model_validator`` sees a clean shape.
  - Converts Pydantic ValidationError + DB / compute exceptions into
    controlled error envelopes (P6 — transport boundary).
  - Passes through the primitive's own error envelope unchanged.

Tests are fully offline; ``_get_engine`` + ``calculate_wirp_meeting_pricing``
are patched.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.ois.tools.wirp_meeting_pricing import (
    CONFIG_PATH as WIRP_MEETING_PRICING_CONFIG_PATH,
)
from shared.config import ToolConfig, clear_tool_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_output() -> dict:
    """Mock that validates against the live WirpMeetingPricingOutput shape.

    Field names + semantics reflect the Codex P0 fix on PR #190:
    ``cumulative_move_prob_pct`` (replacing the misnamed
    signed_move_prob_pct), and NO derived hike/cut/hold fields.
    """
    return {
        "current_metrics": {
            "central_bank": "FOMC",
            "selection_mode": "next_n_meetings",
            "n_meetings_returned": 1,
            "n_meetings_requested": 1,
            "requested_meeting_date": None,
            "next_meeting_date": "2026-06-17",
            "next_implied_policy_rate_pct": 3.637,
            "next_cumulative_move_prob_pct": 8.1,
            "next_num_25bp_moves_priced": 0.081,
            "next_rate_change_native": 0.02,
            "next_as_of_date": "2026-05-22",
        },
        "meetings": [
            {
                "central_bank": "FOMC",
                "meeting_date": "2026-06-17",
                "meeting_token": "JUN2026",
                "as_of_date": "2026-05-22",
                "implied_policy_rate_pct": 3.637,
                "cumulative_move_prob_pct": 8.1,
                "num_25bp_moves_priced": 0.081,
                "rate_change_native": 0.02,
                "vendor_ticker": "WIRP:FOMC:2026-06-17",
                "bloomberg_ticker_implied_rate": "US0BFR JUN2026 Index",
                "bloomberg_ticker_move_prob": "US0BPR JUN2026 Index",
                "bloomberg_ticker_num_moves": "US0BNM JUN2026 Index",
                "bloomberg_ticker_rate_change": "US0BCH JUN2026 Index",
            }
        ],
        "methodology_note": (
            "Source: Bloomberg WIRP screen as ingested per ADR 0009.  "
            "NOT recomputed from STIR futures.  cumulative_move_prob_pct "
            "is CUMULATIVE across multiple 25bp moves (range "
            "-360.1..548.0 — see wirp.yml Stage-B); DO NOT interpret as "
            "single-event probability.  rate_change_native in PERCENTAGE "
            "POINTS (Bloomberg native units).  P12."
        ),
    }


def _assert_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, "calculate_wirp_meeting_pricing called without `config=`"
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "calculate_wirp_meeting_pricing_tool"
    assert "default_n_meetings" in cfg.conventions
    assert "implied_rate_field" in cfg.conventions


# ===========================================================================
# MCP wrapper — rates_agent/ois/mcp_server.py
# ===========================================================================

class TestMcpWirpMeetingPricingToolWiring:
    def test_passes_config_to_compute(self):
        from rates_agent.ois import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_wirp_meeting_pricing",
            return_value=_well_formed_output(),
        ) as mock_calc:
            output_json = mcp_module.calculate_wirp_meeting_pricing_tool(
                central_bank="FOMC", selection_mode="next_n_meetings",
                n_meetings=3,
            )
        assert mock_calc.call_count == 1
        _assert_config_passed(mock_calc.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        assert parsed["current_metrics"]["central_bank"] == "FOMC"

    def test_n_meetings_zero_sentinel_resolves_to_none(self):
        """The MCP wrapper's ``n_meetings=0`` is the wire sentinel for
        'use YAML default'.  The wrapper MUST translate 0 → None so
        the YAML default flows through (curve_move_classifier
        wrapper-shadowing fix pattern).  The Pydantic Input has
        ge=1 so a literal 0 would fail validation — only the
        wrapper's sentinel translation makes it work."""
        from rates_agent.ois import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_wirp_meeting_pricing",
            return_value=_well_formed_output(),
        ) as mock_calc:
            mcp_module.calculate_wirp_meeting_pricing_tool(
                central_bank="FOMC", selection_mode="next_n_meetings",
                n_meetings=0,  # sentinel
            )
        params = mock_calc.call_args.kwargs["params"]
        assert params.n_meetings is None, (
            "n_meetings=0 wire sentinel was not translated to None"
        )

    def test_meeting_date_empty_sentinel_resolves_to_none(self):
        """For selection_mode=next_n_meetings, an empty
        meeting_date='' is the wire sentinel for 'not provided'.
        Must translate to None so the @model_validator passes."""
        from rates_agent.ois import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_wirp_meeting_pricing",
            return_value=_well_formed_output(),
        ):
            output_json = mcp_module.calculate_wirp_meeting_pricing_tool(
                central_bank="FOMC", selection_mode="next_n_meetings",
                n_meetings=3, meeting_date="",
            )
        parsed = json.loads(output_json)
        assert "error" not in parsed, parsed.get("error")

    def test_specific_meeting_date_passes_through(self):
        from rates_agent.ois import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_wirp_meeting_pricing",
            return_value=_well_formed_output(),
        ) as mock_calc:
            mcp_module.calculate_wirp_meeting_pricing_tool(
                central_bank="FOMC",
                selection_mode="specific_meeting_date",
                meeting_date="2026-06-17",
            )
        params = mock_calc.call_args.kwargs["params"]
        from datetime import date as _date
        assert params.meeting_date == _date(2026, 6, 17)
        assert params.n_meetings is None  # mode forbids it

    def test_invalid_params_return_envelope(self):
        """Pydantic validation errors → JSON error envelope."""
        from rates_agent.ois import mcp_server as mcp_module
        # n_meetings=999 violates le=24
        output_json = mcp_module.calculate_wirp_meeting_pricing_tool(
            central_bank="FOMC", selection_mode="next_n_meetings",
            n_meetings=999,
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_invalid_central_bank_returns_envelope(self):
        """Lowercase central_bank that doesn't match the regex
        pattern post-strip returns a Pydantic error envelope."""
        from rates_agent.ois import mcp_server as mcp_module
        output_json = mcp_module.calculate_wirp_meeting_pricing_tool(
            central_bank="12", selection_mode="next_n_meetings",
            n_meetings=3,
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_compute_exception_returns_envelope(self):
        from rates_agent.ois import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_wirp_meeting_pricing",
            side_effect=RuntimeError("synthetic DB failure"),
        ):
            output_json = mcp_module.calculate_wirp_meeting_pricing_tool(
                central_bank="FOMC", selection_mode="next_n_meetings",
                n_meetings=3,
            )
        parsed = json.loads(output_json)
        assert "error" in parsed

    def test_tool_returned_error_envelope_passes_through(self):
        """When the primitive returns its own error envelope (e.g.
        unsupported central_bank), the MCP wrapper passes it
        through unchanged."""
        from rates_agent.ois import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_wirp_meeting_pricing",
            return_value={"error": "Unsupported central_bank='SNB'..."},
        ):
            output_json = mcp_module.calculate_wirp_meeting_pricing_tool(
                central_bank="SNB", selection_mode="next_n_meetings",
                n_meetings=3,
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "SNB" in parsed["error"]


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.ois.tools.wirp_meeting_pricing import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.ois.tools.wirp_meeting_pricing.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_wirp_meeting_pricing_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(WIRP_MEETING_PRICING_CONFIG_PATH)
        assert cfg.tool.name == "calculate_wirp_meeting_pricing_tool"


# ===========================================================================
# Workflow registration — NOT bridge-composable (list-shaped output)
# ===========================================================================

class TestWorkflowIncompatibility:
    """Pin the deliberate WORKFLOW_INCOMPATIBLE_TOOLS membership.
    Adding the primitive to _PRIMITIVE_SPECS without first widening
    the bridge to handle the list-shaped categorical output is a
    contract violation — this test catches it."""

    def test_registered_in_workflow_incompatible(self):
        from rates_agent.workflows import WORKFLOW_INCOMPATIBLE_TOOLS
        assert "calculate_wirp_meeting_pricing_tool" in WORKFLOW_INCOMPATIBLE_TOOLS, (
            "calculate_wirp_meeting_pricing_tool should be in "
            "WORKFLOW_INCOMPATIBLE_TOOLS so the omission from "
            "_PRIMITIVE_SPECS is intentional, not absence-of-registration."
        )
        rationale = WORKFLOW_INCOMPATIBLE_TOOLS["calculate_wirp_meeting_pricing_tool"]
        # Rationale must name the list/categorical output shape.
        assert "list-shaped" in rationale.lower() or "list of" in rationale.lower()
        # AND name the planned Panel-shape path
        assert "Panel" in rationale or "panel" in rationale.lower()

    def test_not_in_primitive_specs(self):
        """Pin the absence — adding it to _PRIMITIVE_SPECS without
        removing from WORKFLOW_INCOMPATIBLE_TOOLS would be a
        contract violation (tool in both at once)."""
        from rates_agent.workflows import _PRIMITIVE_SPECS
        assert "calculate_wirp_meeting_pricing_tool" not in _PRIMITIVE_SPECS


# ===========================================================================
# Mock-vs-schema parity
# ===========================================================================

class TestMockValidatesAgainstSchema:
    def test_well_formed_mock_validates(self):
        from rates_agent.ois.tools.wirp_meeting_pricing import (
            WirpMeetingPricingOutput,
        )
        validated = WirpMeetingPricingOutput.model_validate(_well_formed_output())
        assert validated.current_metrics.central_bank == "FOMC"
        assert len(validated.meetings) == 1
        assert validated.meetings[0].vendor_ticker == "WIRP:FOMC:2026-06-17"
        assert validated.methodology_note  # non-empty

    def test_methodology_note_required(self):
        from pydantic import ValidationError
        from rates_agent.ois.tools.wirp_meeting_pricing import (
            WirpMeetingPricingOutput,
        )
        bad = _well_formed_output()
        bad.pop("methodology_note")
        with pytest.raises(ValidationError):
            WirpMeetingPricingOutput.model_validate(bad)
