"""
test_ois_forward_rate_wiring.py — Caller-wiring smoke tests

Mirrors the prior OIS migration wiring suites
(``test_ois_rate_level_wiring.py``, ``test_ois_curve_spread_wiring.py``,
``test_ois_cross_market_spread_wiring.py``), scoped to OIS
forward_rate's external callers.  Today there is exactly ONE
external caller:

  rates_agent/ois/mcp_server.py::calculate_ois_forward_rate_tool

Future OIS REST endpoints + batch surfaces will be added behind the
same ``CONFIG_PATH`` import.

Key load-bearing properties under test:

  - The MCP wrapper passes config explicitly (no auto-load fallback).
  - The MCP wrapper uses the empty-string / None-sentinel pattern so
    the YAML ``default_swap_rate_field`` actually flows through.
  - The MCP wrapper handles BOTH input modes (tenor + date) via the
    "" → None translation for start/end_tenor / start/end_date.
  - The hub re-export and CONFIG_PATH public symbol are stable.
  - The bespoke + canonical TimeSeries fields survive a full
    round-trip through the response model.
  - ALL three time-series payloads are stripped from LLM-facing JSON.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.ois.tools.forward_rate import (
    CONFIG_PATH as OIS_FORWARD_RATE_CONFIG_PATH,
)
from shared.config import ToolConfig, clear_tool_config_cache, load_tool_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_ois_fwd_output() -> dict:
    """Mock matches the post-migration wire shape: snapshot
    ``current_metrics`` PLUS the bespoke wire-frozen ``time_series``
    PLUS two canonical ``shared.schemas.time_series.TimeSeries``
    payloads (PERCENT forward + Z_SCORE rolling)."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family": "USD_SOFR_OIS",
            "forward_label": "USD SOFR 1Y1Y",
            "start_years": 1.0,
            "end_years": 2.0,
            "forward_rate_pct": 4.10,
            "daily_change_bps": 0.5,
            "current_z_score": 0.4,
            "rolling_window_days": 252,
            "high_252d_pct": 4.45,
            "low_252d_pct": 3.85,
            "percentile_252d": 50.0,
            "start_spot_rate_pct": 4.30,
            "end_spot_rate_pct": 4.20,
        },
        "time_series": [
            {"date": "2026-04-29", "forward_rate_pct": 4.09, "z_score": 0.35},
            {"date": "2026-04-30", "forward_rate_pct": 4.10, "z_score": 0.40},
        ],
        "time_series_forward": {
            "series_name": "usd_sofr_1y1y_ois_forward",
            "units": "percent",
            "description": "Test OIS forward series.",
            "rows": [
                {"date": "2026-04-29", "value": 4.09},
                {"date": "2026-04-30", "value": 4.10},
            ],
        },
        "time_series_zscore": {
            "series_name": "usd_sofr_1y1y_ois_forward_zscore",
            "units": "z_score",
            "description": "Test OIS forward z-score series.",
            "rows": [
                {"date": "2026-04-29", "value": 0.35},
                {"date": "2026-04-30", "value": 0.40},
            ],
        },
    }


def _assert_ois_fwd_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_ois_forward_rate called without `config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "calculate_ois_forward_rate_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'calculate_ois_forward_rate_tool' (catches imports of the "
        "wrong tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_swap_rate_field" in cfg.conventions


# ===========================================================================
# rates_agent/ois/mcp_server.py — calculate_ois_forward_rate_tool
# ===========================================================================

class TestMcpOisForwardRateToolWiring:
    def test_passes_config_to_tool_tenor_mode(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_ois_forward_rate",
            return_value=_well_formed_ois_fwd_output(),
        ) as mock_fr:
            output_json = mcp_module.calculate_ois_forward_rate_tool(
                curve_family="USD_SOFR_OIS",
                start_tenor="1Y", end_tenor="2Y",
                lookback_days=365,
                field_name="PX_LAST",
            )
        assert mock_fr.call_count == 1
        _assert_ois_fwd_config_passed(mock_fr.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_passes_config_to_tool_date_mode(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_ois_forward_rate",
            return_value=_well_formed_ois_fwd_output(),
        ) as mock_fr:
            output_json = mcp_module.calculate_ois_forward_rate_tool(
                curve_family="USD_SOFR_OIS",
                start_date="2026-07-01",
                end_date="2027-01-01",
                lookback_days=365,
            )
        assert mock_fr.call_count == 1
        _assert_ois_fwd_config_passed(mock_fr.call_args)
        # Tenor params translated to None when the caller passes "".
        params = mock_fr.call_args.kwargs["params"]
        assert params.start_tenor is None
        assert params.end_tenor is None
        assert params.start_date == "2026-07-01"
        assert params.end_date == "2027-01-01"

    def test_field_name_default_is_empty_string_sentinel(self):
        """The MCP wrapper's field_name default must be the empty-
        string sentinel, NOT 'PX_LAST'.  Hardcoding any other string
        here shadows the YAML's default_swap_rate_field — same
        shadowing pattern fixed for sovereign curve_move_classifier
        in commit b2605ee."""
        from rates_agent.ois import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(
            mcp_module.calculate_ois_forward_rate_tool,
        )
        default = sig.parameters["field_name"].default
        assert default == "", (
            f"MCP wrapper's field_name default must be '' (empty-string "
            f"sentinel for 'use the YAML default'); got {default!r}"
        )

    def test_omitted_field_name_flows_none_to_input(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_ois_forward_rate",
            return_value=_well_formed_ois_fwd_output(),
        ) as mock_fr:
            mcp_module.calculate_ois_forward_rate_tool(
                curve_family="USD_SOFR_OIS",
                start_tenor="1Y", end_tenor="2Y",
                lookback_days=365,
                # field_name omitted → defaults to ""
            )
        params = mock_fr.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach OISForwardRateInput as "
            f"None (sentinel for 'use YAML default_swap_rate_field'), "
            f"got {params.field_name!r}"
        )

    def test_explicit_field_name_passes_through(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_ois_forward_rate",
            return_value=_well_formed_ois_fwd_output(),
        ) as mock_fr:
            mcp_module.calculate_ois_forward_rate_tool(
                curve_family="USD_SOFR_OIS",
                start_tenor="1Y", end_tenor="2Y",
                field_name="PX_BID",
            )
        params = mock_fr.call_args.kwargs["params"]
        assert params.field_name == "PX_BID"

    def test_invalid_input_returns_error_envelope(self):
        """Window-mode validator must trip a controlled error
        envelope, not an unhandled exception."""
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_ois_forward_rate",
            return_value=_well_formed_ois_fwd_output(),
        ) as mock_fr:
            # Both modes supplied — must trip the validator.
            output_json = mcp_module.calculate_ois_forward_rate_tool(
                curve_family="USD_SOFR_OIS",
                start_tenor="1Y", end_tenor="2Y",
                start_date="2026-07-01", end_date="2027-01-01",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert mock_fr.call_count == 0

    def test_no_mode_returns_error_envelope(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_ois_forward_rate",
            return_value=_well_formed_ois_fwd_output(),
        ) as mock_fr:
            # Neither mode supplied (all "") — must trip the validator.
            output_json = mcp_module.calculate_ois_forward_rate_tool(
                curve_family="USD_SOFR_OIS",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert mock_fr.call_count == 0

    def test_time_series_stripped_from_llm_response(self):
        """The MCP wrapper must NOT serialize ANY of the three
        time-series payloads (bespoke list + two canonical
        TimeSeries) back to the LLM."""
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_ois_forward_rate",
            return_value=_well_formed_ois_fwd_output(),
        ):
            output_json = mcp_module.calculate_ois_forward_rate_tool(
                curve_family="USD_SOFR_OIS",
                start_tenor="1Y", end_tenor="2Y",
            )
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        assert "time_series" not in parsed
        assert "time_series_forward" not in parsed
        assert "time_series_zscore" not in parsed


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.ois.tools.forward_rate import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.ois.tools.forward_rate.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_ois_forward_rate_config(self):
        cfg = load_tool_config(OIS_FORWARD_RATE_CONFIG_PATH)
        assert cfg.tool.name == "calculate_ois_forward_rate_tool"
        assert cfg.tool.domain == "ois"


# ===========================================================================
# Response-model validation
# ===========================================================================

class TestResponseModelValidation:
    """Pin the wire contract for OIS forward_rate.  Mirrors the
    response-model validation pattern from prior OIS migrations."""

    def test_mock_validates_against_response_model(self):
        from rates_agent.ois.tools.forward_rate.schemas import (
            OISForwardRateOutput,
        )
        validated = OISForwardRateOutput.model_validate(
            _well_formed_ois_fwd_output(),
        )
        assert validated.time_series_forward is not None
        assert validated.time_series_zscore is not None
        assert validated.time_series_forward.units.value == "percent"
        assert validated.time_series_zscore.units.value == "z_score"

    def test_response_model_requires_canonical_time_series_fields(self):
        from rates_agent.ois.tools.forward_rate.schemas import (
            OISForwardRateOutput,
        )
        from pydantic import ValidationError
        for field in ("time_series_forward", "time_series_zscore"):
            bad = _well_formed_ois_fwd_output()
            bad.pop(field)
            with pytest.raises(ValidationError):
                OISForwardRateOutput.model_validate(bad)

    def test_round_trip_preserves_all_three_time_series_fields(self):
        from rates_agent.ois.tools.forward_rate.schemas import (
            OISForwardRateOutput,
        )
        validated = OISForwardRateOutput.model_validate(
            _well_formed_ois_fwd_output(),
        )
        dumped = validated.model_dump()
        assert "current_metrics" in dumped
        assert "time_series" in dumped
        assert "time_series_forward" in dumped
        assert "time_series_zscore" in dumped
        assert dumped["time_series_forward"]["units"] == "percent"
        assert dumped["time_series_zscore"]["units"] == "z_score"
        # OIS-specific snapshot field names — start/end_spot_rate_pct
        # use "rate" not "yield".
        assert "start_spot_rate_pct" in dumped["current_metrics"]
        assert "end_spot_rate_pct" in dumped["current_metrics"]
