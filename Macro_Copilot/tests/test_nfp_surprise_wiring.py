"""
test_nfp_surprise_wiring.py — Caller-wiring smoke tests for nfp_surprise
========================================================================

Mirrors test_cpi_surprise_wiring.py for the US-NFP-surprise primitive
(primitive 4 of the easy-win batch).  Pins the MCP wrapper's load-
bearing properties:

  - Wrapper imports CONFIG_PATH and the compute function from the
    primitive's package init.
  - Wrapper loads the bundled config explicitly with
    ``load_tool_config(NFP_SURPRISE_CONFIG_PATH)`` and passes
    ``config=`` to ``calculate_nfp_surprise`` (PR7 +
    DESIGN_PRINCIPLES §8 — call-site config visibility).
  - Wrapper converts Pydantic validation errors into a controlled
    JSON error envelope (P6 — transport boundary).
  - Wrapper converts DB / compute exceptions into a controlled
    error envelope (P6).
  - Wrapper's Python default for ``lookback_releases`` matches the
    YAML's ``default_lookback_releases`` convention.
  - The primitive's own ``{"error": "..."}`` envelope (empty result)
    passes through unchanged.

This primitive IS bridge-composable (Series shape via canonical
TimeSeries) — must be registered in ``_PRIMITIVE_SPECS`` and absent
from ``WORKFLOW_INCOMPATIBLE_TOOLS``.

Tests are fully offline.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.sovereign_bonds.tools.nfp_surprise import (
    CONFIG_PATH as NFP_SURPRISE_CONFIG_PATH,
)
from shared.config import ToolConfig, clear_tool_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_output() -> dict:
    """Mock matches the live NfpSurpriseOutput shape."""
    return {
        "current_metrics": {
            "release_date": "2026-05-08",
            "country": "US",
            "event_type": "nfp",
            "period": "Apr 2026",
            "surprise_label": "US NFP surprise",
            "current_surprise_k_jobs": 50.0,
            "current_z_score": 1.1,
            "release_z_window_releases": 24,
            "current_actual_k_jobs": 115.0,
            "current_consensus_median_k_jobs": 65.0,
            "current_prior_k_jobs": 185.0,
            "observation_count": 16,
        },
        "time_series": [
            {
                "date": "2026-05-08",
                "period": "Apr 2026",
                "surprise_k_jobs": 50.0,
                "z_score": 1.1,
                "actual_k_jobs": 115.0,
                "consensus_median_k_jobs": 65.0,
            }
        ],
        "time_series_surprise": {
            "series_name": "us_nfp_surprise",
            "units": "count",
            "description": (
                "Per-release nonfarm payrolls surprise for US "
                "(actual − consensus_median, in THOUSANDS of jobs)."
            ),
            "rows": [{"date": "2026-05-08", "value": 50.0}],
        },
        "time_series_zscore": {
            "series_name": "us_nfp_surprise_zscore",
            "units": "z_score",
            "description": (
                "Rolling release-window z-score of the US NFP "
                "surprise series."
            ),
            "rows": [{"date": "2026-05-08", "value": 1.1}],
        },
        "methodology_note": (
            "Source: macro_data.event_calendar (ADR 0004).  "
            "Surprise = actual − consensus_median.  PRIOR-MONTH "
            "REVISIONS via ``revised_prior`` are NOT retroactively "
            "applied (TD #28b).  P12."
        ),
    }


def _assert_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, "calculate_nfp_surprise called without `config=`"
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "calculate_nfp_surprise_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'calculate_nfp_surprise_tool'"
    )
    assert "release_z_window" in cfg.conventions
    assert cfg.convention_value("country") == "US"
    assert cfg.convention_value("event_type") == "nfp"


# ===========================================================================
# MCP wrapper — rates_agent/sovereign_bonds/mcp_server.py
# ===========================================================================

class TestMcpNfpSurpriseToolWiring:
    def test_passes_config_to_compute(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_nfp_surprise",
            return_value=_well_formed_output(),
        ) as mock_calc:
            output_json = mcp_module.calculate_nfp_surprise_tool(
                lookback_releases=12,
            )
        assert mock_calc.call_count == 1
        _assert_config_passed(mock_calc.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        assert parsed["current_metrics"]["country"] == "US"

    def test_default_lookback_releases_matches_yaml(self):
        """MCP wrapper's Python default for ``lookback_releases`` MUST
        match the YAML's ``default_lookback_releases`` convention so
        the two layers don't disagree (PR7 + P10)."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        from shared.config import load_tool_config
        import inspect

        sig = inspect.signature(mcp_module.calculate_nfp_surprise_tool)
        wrapper_default = sig.parameters["lookback_releases"].default
        yaml_default = load_tool_config(NFP_SURPRISE_CONFIG_PATH).convention_value(
            "default_lookback_releases"
        )
        assert wrapper_default == yaml_default

    def test_omitted_lookback_releases_flows_yaml_default(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_nfp_surprise",
            return_value=_well_formed_output(),
        ) as mock_calc:
            mcp_module.calculate_nfp_surprise_tool()
        params = mock_calc.call_args.kwargs["params"]
        assert params.lookback_releases == 24

    def test_explicit_lookback_passes_through(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_nfp_surprise",
            return_value=_well_formed_output(),
        ) as mock_calc:
            mcp_module.calculate_nfp_surprise_tool(lookback_releases=36)
        params = mock_calc.call_args.kwargs["params"]
        assert params.lookback_releases == 36

    def test_invalid_params_return_envelope(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        output_json = mcp_module.calculate_nfp_surprise_tool(
            lookback_releases=2,  # violates ge=4
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_compute_exception_returns_envelope(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_nfp_surprise",
            side_effect=RuntimeError("synthetic DB failure"),
        ):
            output_json = mcp_module.calculate_nfp_surprise_tool(
                lookback_releases=12,
            )
        parsed = json.loads(output_json)
        assert "error" in parsed

    def test_tool_returned_error_envelope_passes_through(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_nfp_surprise",
            return_value={"error": "No event_calendar rows found..."},
        ):
            output_json = mcp_module.calculate_nfp_surprise_tool(
                lookback_releases=12,
            )
        parsed = json.loads(output_json)
        assert "error" in parsed


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.sovereign_bonds.tools.nfp_surprise import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.sovereign_bonds.tools.nfp_surprise.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_nfp_surprise_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(NFP_SURPRISE_CONFIG_PATH)
        assert cfg.tool.name == "calculate_nfp_surprise_tool"


# ===========================================================================
# Workflow registration — bridge-composable (Series shape)
# ===========================================================================

class TestWorkflowRegistration:
    def test_registered_in_primitive_specs(self):
        from rates_agent.workflows import _PRIMITIVE_SPECS
        assert "calculate_nfp_surprise_tool" in _PRIMITIVE_SPECS

    def test_not_in_workflow_incompatible(self):
        from rates_agent.workflows import WORKFLOW_INCOMPATIBLE_TOOLS
        assert "calculate_nfp_surprise_tool" not in WORKFLOW_INCOMPATIBLE_TOOLS

    def test_output_field_units(self):
        from rates_agent.workflows import _PRIMITIVE_SPECS
        spec = _PRIMITIVE_SPECS["calculate_nfp_surprise_tool"]
        # COUNT, not PERCENT — NFP is thousands of jobs, not percentage points.
        assert spec.output_field_units["time_series_surprise"] == "count"
        assert spec.output_field_units["time_series_zscore"] == "z_score"


# ===========================================================================
# Mock-vs-schema parity
# ===========================================================================

class TestMockValidatesAgainstSchema:
    def test_well_formed_mock_validates(self):
        from rates_agent.sovereign_bonds.tools.nfp_surprise import (
            NfpSurpriseOutput,
        )
        validated = NfpSurpriseOutput.model_validate(_well_formed_output())
        assert validated.current_metrics.current_surprise_k_jobs == 50.0
        assert validated.time_series_surprise.units.value == "count"
        assert validated.methodology_note  # non-empty

    def test_methodology_note_required(self):
        from pydantic import ValidationError
        from rates_agent.sovereign_bonds.tools.nfp_surprise import (
            NfpSurpriseOutput,
        )
        bad = _well_formed_output()
        bad.pop("methodology_note")
        with pytest.raises(ValidationError):
            NfpSurpriseOutput.model_validate(bad)
