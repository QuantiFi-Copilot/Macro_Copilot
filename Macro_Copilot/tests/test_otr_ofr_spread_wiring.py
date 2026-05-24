"""
test_otr_ofr_spread_wiring.py — Caller-wiring smoke tests for otr_ofr_spread
============================================================================

Mirrors test_yield_levels_wiring.py / test_curve_spread_wiring.py for the
new OTR/OFR spread primitive.  Pins the MCP wrapper's load-bearing
properties:

  - Wrapper imports CONFIG_PATH and the compute function from the
    primitive's package init.
  - Wrapper loads the bundled config explicitly with
    ``load_tool_config(OTR_OFR_SPREAD_CONFIG_PATH)`` and passes
    ``config=`` to ``calculate_otr_ofr_spread`` (PR7 +
    DESIGN_PRINCIPLES §8 — call-site config visibility).
  - Wrapper converts Pydantic validation errors into a controlled JSON
    error envelope (P6 — transport boundary).
  - Wrapper converts DB / compute exceptions into a controlled error
    envelope (P6).
  - Wrapper translates the empty-string ``field_name`` wire sentinel to
    None so the YAML default flows through (curve_move_classifier
    wrapper-shadowing fix b2605ee).
  - Wrapper's Python defaults match the YAML defaults — no parallel
    "second source of truth".

This primitive is bridge-composable (Series shape), so it must be
present in ``_PRIMITIVE_SPECS`` and absent from
``WORKFLOW_INCOMPATIBLE_TOOLS``.  The reverse — being in BOTH or
NEITHER — is a registration bug; the tests below pin the correct
state.

Tests are fully offline; the MCP wrapper's get_db_engine and the
primitive's calculate_otr_ofr_spread are patched.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.sovereign_bonds.tools.otr_ofr_spread import (
    CONFIG_PATH as OTR_OFR_SPREAD_CONFIG_PATH,
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


def _well_formed_otr_ofr_output() -> dict:
    """Mock matches the live OtrOfrSpreadOutput shape — current_metrics +
    time_series + canonical TimeSeries fields + methodology_note.  Must
    validate against the live schema; see
    test_mock_validates_against_output_model."""
    return {
        "current_metrics": {
            "as_of_date": "2026-05-22",
            "country": "US",
            "tenor": "10Y",
            "slot_label": "US 10Y OTR/OFR",
            "current_spread_bps": -3.25,
            "daily_change_bps": -0.5,
            "current_z_score": -1.2,
            "rolling_window_days": 252,
            "otr_yield_pct": 4.25,
            "ofr_yield_pct": 4.2825,
            "otr_instrument_id": 102,
            "otr_cusip": None,
            "otr_isin": None,
            "ofr_instrument_id": 101,
            "ofr_cusip": None,
            "ofr_isin": None,
            "observation_count": 180,
        },
        "time_series": [
            {
                "date": "2026-05-22",
                "spread_bps": -3.25,
                "z_score": -1.2,
                "otr_yield_pct": 4.25,
                "ofr_yield_pct": 4.2825,
            }
        ],
        "time_series_spread": {
            "series_name": "us_10y_otr_ofr_spread",
            "units": "bps",
            "description": "OTR/OFR yield spread for the US 10Y sovereign cash-bond slot (otr − ofr, in bps) over the displayed window.",
            "rows": [{"date": "2026-05-22", "value": -3.25}],
        },
        "time_series_zscore": {
            "series_name": "us_10y_otr_ofr_zscore",
            "units": "z_score",
            "description": "Rolling z-score of the US 10Y OTR/OFR yield spread vs its own trailing 252-trading-day window.",
            "rows": [{"date": "2026-05-22", "value": -1.2}],
        },
        "methodology_note": (
            "Source: macro_data.otr_history (ADR 0003) JOIN macro_data."
            "market_data_daily; OTR/OFR pair resolution per the on-the-run "
            "resolver (ADR 0007).  Forward-only ingest: pre-resolver OTR "
            "windows are NOT reconstructed — queries that pre-date the "
            "resolver's first run return an error envelope, never a "
            "fabricated spread.  Detection-date precision: a window's "
            "effective_from is the resolver's first confirmed observation, "
            "~1-2 days after the true auction date at daily incremental "
            "cadence (TD #27); roll-day spread datapoints may correspondingly "
            "lag the auction.  OFR is defined as the bond from the SCD2 "
            "window IMMEDIATELY PRIOR to the window covering the trade date "
            "(LAG over effective_from); the slot's first-ever observed "
            "window has no prior bond and emits ofr_yield=None / spread=None "
            "(honest absence, not zero).  Bloomberg has no historical OTR-"
            "window screen; this primitive does not recompute what the "
            "source of record provides (P12)."
        ),
    }


def _assert_otr_ofr_config_passed(call_args) -> None:
    """The wrapper must pass an explicit ``config=`` of the right tool's
    bundled ToolConfig — guards against importing the wrong tool's
    CONFIG_PATH at wiring time."""
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, "calculate_otr_ofr_spread called without `config=`"
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "calculate_otr_ofr_spread_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'calculate_otr_ofr_spread_tool' (catches imports of the wrong "
        "tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_field_name" in cfg.conventions


# ===========================================================================
# MCP wrapper — rates_agent/sovereign_bonds/mcp_server.py
# ===========================================================================

class TestMcpOtrOfrSpreadToolWiring:
    def test_passes_config_to_compute(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_otr_ofr_spread",
            return_value=_well_formed_otr_ofr_output(),
        ) as mock_calc:
            output_json = mcp_module.calculate_otr_ofr_spread_tool(
                country="US",
                tenor="10Y",
                lookback_days=180,
            )
        assert mock_calc.call_count == 1
        _assert_otr_ofr_config_passed(mock_calc.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        assert parsed["current_metrics"]["country"] == "US"

    def test_default_lookback_days_matches_yaml(self):
        """The MCP wrapper's Python default for ``lookback_days`` MUST
        match the YAML's documented default (365).  Same shadowing
        anti-pattern the curve_move_classifier wrapper hit (b2605ee)."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        import inspect

        sig = inspect.signature(mcp_module.calculate_otr_ofr_spread_tool)
        wrapper_default = sig.parameters["lookback_days"].default
        # The YAML's Pydantic-default is 365; the wrapper must echo it.
        from rates_agent.sovereign_bonds.tools.otr_ofr_spread import (
            OtrOfrSpreadInput,
        )
        pydantic_default = OtrOfrSpreadInput.model_fields["lookback_days"].default
        assert wrapper_default == pydantic_default, (
            f"MCP wrapper default ({wrapper_default}) shadows Pydantic "
            f"default ({pydantic_default}); align the wrapper or the schema."
        )

    def test_empty_field_name_sentinel_resolves_to_none(self):
        """The MCP wrapper's ``field_name`` parameter uses the empty-
        string wire sentinel.  The wrapper MUST translate '' to None
        so the YAML default flows through (curve_move_classifier
        wrapper-shadowing fix b2605ee)."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_otr_ofr_spread",
            return_value=_well_formed_otr_ofr_output(),
        ) as mock_calc:
            mcp_module.calculate_otr_ofr_spread_tool(
                country="US",
                tenor="10Y",
                lookback_days=180,
                field_name="",  # wire-level sentinel
            )
        params = mock_calc.call_args.kwargs["params"]
        # The wrapper translated '' → None so the YAML default applies.
        assert params.field_name is None

    def test_explicit_field_name_passes_through(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_otr_ofr_spread",
            return_value=_well_formed_otr_ofr_output(),
        ) as mock_calc:
            mcp_module.calculate_otr_ofr_spread_tool(
                country="DE",
                tenor="10Y",
                lookback_days=730,
                field_name="PX_DIRTY_MID",
            )
        params = mock_calc.call_args.kwargs["params"]
        assert params.country == "DE"
        assert params.tenor == "10Y"
        assert params.lookback_days == 730
        assert params.field_name == "PX_DIRTY_MID"

    def test_invalid_params_return_envelope(self):
        """Pydantic ValidationError at the MCP boundary becomes a JSON
        error envelope, not a raised exception (P6 — transport
        boundary)."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        # lookback_days=10 violates ge=30
        output_json = mcp_module.calculate_otr_ofr_spread_tool(
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
            "calculate_otr_ofr_spread",
            side_effect=RuntimeError("synthetic DB failure"),
        ):
            output_json = mcp_module.calculate_otr_ofr_spread_tool(
                country="US",
                tenor="10Y",
                lookback_days=180,
            )
        parsed = json.loads(output_json)
        assert "error" in parsed

    def test_tool_returned_error_envelope_passes_through(self):
        """When the primitive itself returns an ``{"error": "..."}``
        envelope (e.g. no OTR window in lookback), the MCP wrapper
        passes it through unchanged.  Does NOT crash, does NOT
        re-wrap."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_otr_ofr_spread",
            return_value={"error": "No OTR/OFR data found for country='ZA'..."},
        ):
            output_json = mcp_module.calculate_otr_ofr_spread_tool(
                country="ZA",
                tenor="10Y",
                lookback_days=180,
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "ZA" in parsed["error"]


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.sovereign_bonds.tools.otr_ofr_spread import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.sovereign_bonds.tools.otr_ofr_spread.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_otr_ofr_spread_config(self):
        from shared.config import load_tool_config

        cfg = load_tool_config(OTR_OFR_SPREAD_CONFIG_PATH)
        assert cfg.tool.name == "calculate_otr_ofr_spread_tool"


# ===========================================================================
# Workflow registration — bridge-composable (Series shape)
# ===========================================================================

class TestWorkflowRegistration:
    """Unlike get_otr_history (categorical, not bridge-composable),
    otr_ofr_spread emits canonical TimeSeries fields and must be
    registered in ``_PRIMITIVE_SPECS`` with the correct
    ``output_field_units``."""

    def test_registered_in_primitive_specs(self):
        from rates_agent.workflows import _PRIMITIVE_SPECS

        assert "calculate_otr_ofr_spread_tool" in _PRIMITIVE_SPECS

    def test_not_in_workflow_incompatible(self):
        """Pin the absence — adding otr_ofr_spread to
        WORKFLOW_INCOMPATIBLE_TOOLS would be a contract violation."""
        from rates_agent.workflows import WORKFLOW_INCOMPATIBLE_TOOLS

        assert "calculate_otr_ofr_spread_tool" not in WORKFLOW_INCOMPATIBLE_TOOLS

    def test_output_field_units(self):
        """``output_field_units`` declarations must match the schema's
        canonical TimeSeries unit declarations.  Without this, the
        substrate's unit-compatibility check (P2 follow-up) cannot
        validate."""
        from rates_agent.workflows import _PRIMITIVE_SPECS

        spec = _PRIMITIVE_SPECS["calculate_otr_ofr_spread_tool"]
        assert spec.output_field_units["time_series_spread"] == "bps"
        assert spec.output_field_units["time_series_zscore"] == "z_score"


# ===========================================================================
# Mock-vs-schema parity
# ===========================================================================

class TestMockValidatesAgainstSchema:
    def test_well_formed_mock_validates(self):
        """The wiring mock MUST validate against the live
        OtrOfrSpreadOutput model — guards against the mock drifting
        away from the actual schema."""
        from rates_agent.sovereign_bonds.tools.otr_ofr_spread import (
            OtrOfrSpreadOutput,
        )
        validated = OtrOfrSpreadOutput.model_validate(_well_formed_otr_ofr_output())
        assert validated.current_metrics.current_spread_bps == -3.25
        assert validated.time_series_spread.units.value == "bps"
        assert validated.methodology_note  # non-empty

    def test_methodology_note_required(self):
        """methodology_note is a required field — removing it MUST
        raise.  Pin the PR10 requirement that the user-facing TD #27
        disclosure cannot be silently dropped by a future refactor."""
        from pydantic import ValidationError

        from rates_agent.sovereign_bonds.tools.otr_ofr_spread import (
            OtrOfrSpreadOutput,
        )
        bad = _well_formed_otr_ofr_output()
        bad.pop("methodology_note")
        with pytest.raises(ValidationError):
            OtrOfrSpreadOutput.model_validate(bad)
