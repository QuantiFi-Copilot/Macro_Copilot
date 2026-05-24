"""
test_cpi_surprise_wiring.py — Caller-wiring smoke tests for cpi_surprise
========================================================================

Mirrors test_otr_ofr_spread_wiring.py for the new CPI-surprise
primitive (primitive 3 of the easy-win batch).  Pins the MCP
wrapper's load-bearing properties:

  - Wrapper imports CONFIG_PATH and the compute function from the
    primitive's package init.
  - Wrapper loads the bundled config explicitly with
    ``load_tool_config(CPI_SURPRISE_CONFIG_PATH)`` and passes
    ``config=`` to ``calculate_cpi_surprise`` (PR7 +
    DESIGN_PRINCIPLES §8 — call-site config visibility).
  - Wrapper converts Pydantic validation errors into a controlled
    JSON error envelope (P6 — transport boundary).
  - Wrapper converts DB / compute exceptions into a controlled
    error envelope (P6).
  - Wrapper's Python default for ``lookback_releases`` matches the
    YAML's ``default_lookback_releases`` convention (no parallel
    "second source of truth").
  - The primitive's own ``{"error": "..."}`` envelope (unsupported
    country, empty result) passes through unchanged.

This primitive IS bridge-composable (Series shape via canonical
TimeSeries) — must be registered in ``_PRIMITIVE_SPECS`` and absent
from ``WORKFLOW_INCOMPATIBLE_TOOLS``.

Tests are fully offline; the MCP wrapper's get_db_engine and the
primitive's calculate_cpi_surprise are patched.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.inflation_swaps.tools.cpi_surprise import (
    CONFIG_PATH as CPI_SURPRISE_CONFIG_PATH,
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


def _well_formed_output() -> dict:
    """Mock matches the live CpiSurpriseOutput shape.  Must validate
    against the live schema; see test_mock_validates_against_schema."""
    return {
        "current_metrics": {
            "release_date": "2026-04-10",
            "country": "US",
            "event_type": "cpi_yoy",
            "period": "Mar 2026",
            "surprise_label": "US CPI YoY surprise",
            "current_surprise_pct": 0.1,
            "current_z_score": 0.5,
            "release_z_window_releases": 24,
            "current_actual_pct": 3.2,
            "current_consensus_median_pct": 3.1,
            "current_prior_pct": 3.15,
            "observation_count": 24,
        },
        "time_series": [
            {
                "date": "2026-04-10",
                "period": "Mar 2026",
                "surprise_pct": 0.1,
                "z_score": 0.5,
                "actual_pct": 3.2,
                "consensus_median_pct": 3.1,
            }
        ],
        "time_series_surprise": {
            "series_name": "us_cpi_surprise",
            "units": "percent",
            "description": (
                "Per-release CPI surprise for US "
                "(actual − consensus_median, in percentage points of "
                "YoY CPI) over the displayed release window."
            ),
            "rows": [{"date": "2026-04-10", "value": 0.1}],
        },
        "time_series_zscore": {
            "series_name": "us_cpi_surprise_zscore",
            "units": "z_score",
            "description": (
                "Rolling release-window z-score of the US CPI "
                "surprise series vs its own trailing window."
            ),
            "rows": [{"date": "2026-04-10", "value": 0.5}],
        },
        "methodology_note": (
            "Source: macro_data.event_calendar (ADR 0004) populated "
            "by the economic-releases playbook per ADR 0008.  Surprise "
            "is the EXACT IDENTITY ``actual − consensus_median``.  "
            "Pre-release REVISIONS of prior surprises (``revised_prior``) "
            "are NOT in this series.  Scope: bounded by ECO_RELEASE_DT_LIST "
            "(TD #28b).  P12."
        ),
    }


def _assert_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, "calculate_cpi_surprise called without `config=`"
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "calculate_cpi_surprise_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'calculate_cpi_surprise_tool'"
    )
    assert "release_z_window" in cfg.conventions
    assert "cpi_event_type_for_us" in cfg.conventions


# ===========================================================================
# MCP wrapper — rates_agent/inflation_swaps/mcp_server.py
# ===========================================================================

class TestMcpCpiSurpriseToolWiring:
    def test_passes_config_to_compute(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cpi_surprise",
            return_value=_well_formed_output(),
        ) as mock_calc:
            output_json = mcp_module.calculate_cpi_surprise_tool(
                country="US",
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
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        from shared.config import load_tool_config
        import inspect

        sig = inspect.signature(mcp_module.calculate_cpi_surprise_tool)
        wrapper_default = sig.parameters["lookback_releases"].default
        yaml_default = load_tool_config(CPI_SURPRISE_CONFIG_PATH).convention_value(
            "default_lookback_releases"
        )
        assert wrapper_default == yaml_default, (
            f"MCP wrapper default ({wrapper_default}) shadows YAML "
            f"default ({yaml_default}); align the wrapper or the YAML."
        )

    def test_omitted_lookback_releases_flows_yaml_default(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cpi_surprise",
            return_value=_well_formed_output(),
        ) as mock_calc:
            mcp_module.calculate_cpi_surprise_tool(country="US")
        params = mock_calc.call_args.kwargs["params"]
        assert params.lookback_releases == 24

    def test_explicit_lookback_passes_through(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cpi_surprise",
            return_value=_well_formed_output(),
        ) as mock_calc:
            mcp_module.calculate_cpi_surprise_tool(
                country="EU", lookback_releases=36,
            )
        params = mock_calc.call_args.kwargs["params"]
        assert params.country == "EU"
        assert params.lookback_releases == 36

    def test_invalid_params_return_envelope(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        # lookback_releases=2 violates ge=4 — schema-level validation.
        output_json = mcp_module.calculate_cpi_surprise_tool(
            country="US", lookback_releases=2,
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_invalid_country_returns_envelope(self):
        """alpha-3 / numeric / wrong-shape country → Pydantic validation
        error envelope."""
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        output_json = mcp_module.calculate_cpi_surprise_tool(
            country="USA", lookback_releases=12,
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_compute_exception_returns_envelope(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cpi_surprise",
            side_effect=RuntimeError("synthetic DB failure"),
        ):
            output_json = mcp_module.calculate_cpi_surprise_tool(
                country="US", lookback_releases=12,
            )
        parsed = json.loads(output_json)
        assert "error" in parsed

    def test_tool_returned_error_envelope_passes_through(self):
        """When the primitive returns its own error envelope (empty
        result, unsupported country), the MCP wrapper passes it
        through unchanged — does NOT crash, does NOT re-wrap."""
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_cpi_surprise",
            return_value={"error": "Unsupported country=CA: ..."},
        ):
            output_json = mcp_module.calculate_cpi_surprise_tool(
                country="JP", lookback_releases=12,
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "CA" in parsed["error"]


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.inflation_swaps.tools.cpi_surprise import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.inflation_swaps.tools.cpi_surprise.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_cpi_surprise_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(CPI_SURPRISE_CONFIG_PATH)
        assert cfg.tool.name == "calculate_cpi_surprise_tool"


# ===========================================================================
# Workflow registration — bridge-composable (Series shape)
# ===========================================================================

class TestWorkflowRegistration:
    def test_registered_in_primitive_specs(self):
        from rates_agent.workflows import _PRIMITIVE_SPECS
        assert "calculate_cpi_surprise_tool" in _PRIMITIVE_SPECS

    def test_not_in_workflow_incompatible(self):
        from rates_agent.workflows import WORKFLOW_INCOMPATIBLE_TOOLS
        assert "calculate_cpi_surprise_tool" not in WORKFLOW_INCOMPATIBLE_TOOLS

    def test_output_field_units(self):
        """Unit declarations match the schema's canonical TimeSeries
        units."""
        from rates_agent.workflows import _PRIMITIVE_SPECS
        spec = _PRIMITIVE_SPECS["calculate_cpi_surprise_tool"]
        assert spec.output_field_units["time_series_surprise"] == "percent"
        assert spec.output_field_units["time_series_zscore"] == "z_score"


# ===========================================================================
# Mock-vs-schema parity
# ===========================================================================

class TestMockValidatesAgainstSchema:
    def test_well_formed_mock_validates(self):
        from rates_agent.inflation_swaps.tools.cpi_surprise import (
            CpiSurpriseOutput,
        )
        validated = CpiSurpriseOutput.model_validate(_well_formed_output())
        assert validated.current_metrics.current_surprise_pct == 0.1
        assert validated.time_series_surprise.units.value == "percent"
        assert validated.methodology_note  # non-empty

    def test_methodology_note_required(self):
        from pydantic import ValidationError
        from rates_agent.inflation_swaps.tools.cpi_surprise import (
            CpiSurpriseOutput,
        )
        bad = _well_formed_output()
        bad.pop("methodology_note")
        with pytest.raises(ValidationError):
            CpiSurpriseOutput.model_validate(bad)
