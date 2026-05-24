"""
test_get_otr_history_wiring.py — Caller-wiring smoke tests for get_otr_history
==============================================================================

Mirrors test_yield_levels_wiring.py for the new OTR-history primitive.  Pin
the MCP wrapper's load-bearing properties:

  - The wrapper imports CONFIG_PATH and the compute function from the
    primitive's package init.
  - The wrapper loads the bundled config explicitly with
    ``load_tool_config(GET_OTR_HISTORY_CONFIG_PATH)`` and passes
    ``config=`` to ``get_otr_history`` (PR7 + DESIGN_PRINCIPLES §8 —
    call-site config visibility).
  - The wrapper converts Pydantic validation errors into a controlled
    JSON error envelope (P6 — transport boundary).
  - The wrapper converts DB / compute exceptions into a controlled
    error envelope (P6).
  - The wrapper does NOT silently drop conventions; the resolver of
    truth for what conventions apply is the bundled config.yaml that
    the wrapper passes through.

This primitive does NOT have a ``field_name`` input (it reads
identifier rows from otr_history, not market_data_daily field
values), so the empty-string / None sentinel pattern that
yield_levels uses for field_name does NOT apply here.  The wiring
contract instead pins the country / tenor / lookback_days flow.

Tests are fully offline; the MCP wrapper's get_db_engine and the
primitive's get_otr_history are patched.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.sovereign_bonds.tools.get_otr_history import (
    CONFIG_PATH as GET_OTR_HISTORY_CONFIG_PATH,
)
from shared.config import ToolConfig, clear_tool_config_cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_otr_history_output() -> dict:
    """Mock matches the live OtrHistoryOutput shape — current_metrics +
    transitions + methodology_note.  Must validate against the live
    schema; see test_mock_validates_against_output_model."""
    return {
        "current_metrics": {
            "country": "US",
            "tenor": "10Y",
            "as_of_date": "2026-05-24",
            "otr_instrument_id": 102,
            "cusip": "91282CLB6",
            "isin": "US91282CLB60",
            "vendor_ticker": "/cusip/91282CLB6",
            "maturity_date": "2034-11-15",
            "current_effective_from": "2025-11-15",
            "transition_count_in_window": 2,
            "lookback_days": 252,
        },
        "transitions": [
            {
                "effective_from": "2025-08-15",
                "effective_to": "2025-11-14",
                "otr_instrument_id": 101,
                "cusip": "91282CKZ4",
                "isin": "US91282CKZ40",
                "vendor_ticker": "/cusip/91282CKZ4",
                "maturity_date": "2034-08-15",
            },
            {
                "effective_from": "2025-11-15",
                "effective_to": None,
                "otr_instrument_id": 102,
                "cusip": "91282CLB6",
                "isin": "US91282CLB60",
                "vendor_ticker": "/cusip/91282CLB6",
                "maturity_date": "2034-11-15",
            },
        ],
        "methodology_note": (
            "Source: macro_data.otr_history (ADR 0003), populated by the "
            "on-the-run resolver per ADR 0007. Forward-only ingest: pre-"
            "resolver OTR windows are NOT reconstructed — queries that "
            "pre-date the resolver's first run return honest absence "
            "(transitions=[], current_metrics identity fields None), never "
            "a fabricated mapping. Detection-date precision: a window's "
            "effective_from is the resolver's first confirmed observation, "
            "~1-2 days after the true auction date at daily incremental "
            "cadence (TD #27). Bloomberg has no historical OTR-window "
            "screen; this primitive does not recompute what the source of "
            "record provides (P12)."
        ),
    }


def _assert_otr_history_config_passed(call_args) -> None:
    """The wrapper must pass an explicit ``config=`` of the right tool's
    bundled ToolConfig — guards against importing the wrong tool's
    CONFIG_PATH at wiring time."""
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, "get_otr_history called without `config=`"
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "get_otr_history_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'get_otr_history_tool' (catches imports of the wrong tool's "
        "CONFIG_PATH)"
    )
    assert "default_lookback_days" in cfg.conventions


# ===========================================================================
# MCP wrapper — rates_agent/sovereign_bonds/mcp_server.py::get_otr_history_tool
# ===========================================================================

class TestMcpOtrHistoryToolWiring:
    def test_passes_config_to_compute(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "get_otr_history",
            return_value=_well_formed_otr_history_output(),
        ) as mock_otr:
            output_json = mcp_module.get_otr_history_tool(
                country="US",
                tenor="10Y",
                lookback_days=252,
            )
        assert mock_otr.call_count == 1
        _assert_otr_history_config_passed(mock_otr.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_default_lookback_days_matches_yaml(self):
        """The MCP wrapper's Python default for ``lookback_days`` MUST
        match the YAML's ``default_lookback_days`` convention so the
        two layers don't disagree (PR7 + P10).  The same shadowing
        anti-pattern that bit curve_move_classifier (commit b2605ee)
        is the failure mode this test pins."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        from shared.config import load_tool_config
        import inspect

        sig = inspect.signature(mcp_module.get_otr_history_tool)
        wrapper_default = sig.parameters["lookback_days"].default
        yaml_default = load_tool_config(GET_OTR_HISTORY_CONFIG_PATH).convention_value(
            "default_lookback_days"
        )
        assert wrapper_default == yaml_default, (
            f"MCP wrapper default ({wrapper_default}) shadows YAML "
            f"default ({yaml_default}); align the wrapper or the YAML."
        )

    def test_omitted_lookback_days_flows_yaml_default(self):
        """Omitting lookback_days at the MCP boundary lets the YAML
        default flow through to OtrHistoryInput."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "get_otr_history",
            return_value=_well_formed_otr_history_output(),
        ) as mock_otr:
            mcp_module.get_otr_history_tool(country="US", tenor="10Y")
        params = mock_otr.call_args.kwargs["params"]
        # 365 is the YAML default for default_lookback_days (calendar days, 1Y).
        assert params.lookback_days == 365

    def test_explicit_lookback_days_passes_through(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "get_otr_history",
            return_value=_well_formed_otr_history_output(),
        ) as mock_otr:
            mcp_module.get_otr_history_tool(
                country="DE",
                tenor="10Y",
                lookback_days=730,
            )
        params = mock_otr.call_args.kwargs["params"]
        assert params.country == "DE"
        assert params.tenor == "10Y"
        assert params.lookback_days == 730

    def test_invalid_params_return_envelope(self):
        """Pydantic ValidationError at the MCP boundary becomes a JSON
        error envelope, not a raised exception (P6 — transport
        boundary)."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        # lookback_days=10 violates ge=30 — schema-level validation.
        output_json = mcp_module.get_otr_history_tool(
            country="US",
            tenor="10Y",
            lookback_days=10,
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_compute_exception_returns_envelope(self):
        """A raise inside compute() at the MCP boundary becomes a JSON
        error envelope (P6)."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "get_otr_history",
            side_effect=RuntimeError("synthetic DB failure"),
        ):
            output_json = mcp_module.get_otr_history_tool(
                country="US",
                tenor="10Y",
                lookback_days=252,
            )
        parsed = json.loads(output_json)
        assert "error" in parsed

    def test_honest_absence_preserved_through_wrapper(self):
        """Empty transitions list passes through the MCP wrapper
        unchanged.  Empty absence is NOT an error envelope — the
        wrapper must NOT add ``error`` to such a response."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        empty_response = {
            "current_metrics": {
                "country": "ZA",
                "tenor": "10Y",
                "as_of_date": "2026-05-24",
                "otr_instrument_id": None,
                "cusip": None,
                "isin": None,
                "vendor_ticker": None,
                "maturity_date": None,
                "current_effective_from": None,
                "transition_count_in_window": 0,
                "lookback_days": 252,
            },
            "transitions": [],
            "methodology_note": "Source: macro_data.otr_history (ADR 0003)...",
        }
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "get_otr_history",
            return_value=empty_response,
        ):
            output_json = mcp_module.get_otr_history_tool(
                country="ZA", tenor="10Y", lookback_days=252,
            )
        parsed = json.loads(output_json)
        assert "error" not in parsed
        assert parsed["transitions"] == []


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.sovereign_bonds.tools.get_otr_history import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.sovereign_bonds.tools.get_otr_history.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_get_otr_history_config(self):
        from shared.config import load_tool_config

        cfg = load_tool_config(GET_OTR_HISTORY_CONFIG_PATH)
        assert cfg.tool.name == "get_otr_history_tool"


# ===========================================================================
# Workflow-incompatibility — explicit registry marker (Codex AC8 follow-up)
# ===========================================================================

class TestWorkflowIncompatibilityRegistry:
    """The primitive is intentionally NOT bridge-composable in V1
    (categorical / SCD2-shaped output, not Series/Panel).  Per the
    workflow contract, this omission is made EXPLICIT via
    ``rates_agent.workflows.WORKFLOW_INCOMPATIBLE_TOOLS`` so the
    distinction between 'intentionally omitted' and 'forgotten' is
    legible at the registry layer."""

    def test_listed_in_workflow_incompatible_registry(self):
        from rates_agent.workflows import WORKFLOW_INCOMPATIBLE_TOOLS

        assert "get_otr_history_tool" in WORKFLOW_INCOMPATIBLE_TOOLS, (
            "get_otr_history_tool should be in WORKFLOW_INCOMPATIBLE_TOOLS "
            "so the omission from _PRIMITIVE_SPECS is intentional, not "
            "absence-of-registration."
        )
        rationale = WORKFLOW_INCOMPATIBLE_TOOLS["get_otr_history_tool"]
        # Rationale must name the output shape — categorical / SCD2
        # transition log — to keep the disclosure honest.
        assert "SCD2" in rationale or "categorical" in rationale.lower()

    def test_not_in_primitive_specs(self):
        """Pin the absence — adding get_otr_history to _PRIMITIVE_SPECS
        without removing it from WORKFLOW_INCOMPATIBLE_TOOLS would be
        a contract violation (the tool would be in both at once)."""
        from rates_agent.workflows import _PRIMITIVE_SPECS

        assert "get_otr_history_tool" not in _PRIMITIVE_SPECS


# ===========================================================================
# Mock-vs-schema parity
# ===========================================================================

class TestMockValidatesAgainstSchema:
    def test_well_formed_mock_validates(self):
        """The wiring mock MUST validate against the live
        OtrHistoryOutput model — guards against the mock drifting away
        from the actual schema."""
        from rates_agent.sovereign_bonds.tools.get_otr_history import (
            OtrHistoryOutput,
        )
        validated = OtrHistoryOutput.model_validate(_well_formed_otr_history_output())
        assert validated.transitions[1].effective_to is None
        assert validated.current_metrics.cusip == "91282CLB6"
        assert validated.methodology_note  # non-empty

    def test_methodology_note_required(self):
        """methodology_note is a required field — removing it MUST
        raise.  Pin the PR10 requirement that the user-facing TD #27
        disclosure cannot be silently dropped by a future refactor."""
        from pydantic import ValidationError

        from rates_agent.sovereign_bonds.tools.get_otr_history import (
            OtrHistoryOutput,
        )
        bad = _well_formed_otr_history_output()
        bad.pop("methodology_note")
        with pytest.raises(ValidationError):
            OtrHistoryOutput.model_validate(bad)
