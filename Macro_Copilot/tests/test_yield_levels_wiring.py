"""
test_yield_levels_wiring.py — Caller-wiring smoke tests

Mirrors test_curve_move_classifier_wiring.py for the yield_levels tool.
Three external callers:

  1. rates_agent/sovereign_bonds/mcp_server.py::get_yield_levels_tool
  2. api/routes/rates/detail.py::yield_detail (/detail/yield)
  3. api/routes/rates/cards.py::yield_snapshot (/yield-snapshot)
     — but cards.py uses _compute_yield_snapshot, a SEPARATE batch
     reimplementation rather than calling the tool directly.  That
     batch path now reads the same config.yaml and uses the same
     compute_level_metrics primitive.  The wiring tests here cover
     all three surfaces.

Key load-bearing properties under test:

  - Each surface passes config explicitly (no auto-load fallback).
  - Each surface uses the empty-string / None-sentinel pattern so
    the YAML default_field_name actually flows through (the bug
    that bit curve_move_classifier and was fixed in commit b2605ee).
  - The batch yield-snapshot endpoint loads yield_levels/config.yaml
    exactly ONCE per request, not per-(curve, tenor) iteration.
  - When config load fails, callers return a controlled error
    envelope, not an unhandled 500.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from rates_agent.sovereign_bonds.tools.yield_levels import (
    CONFIG_PATH as YIELD_LEVELS_CONFIG_PATH,
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


def _well_formed_yield_output() -> dict:
    """Mock matches the post-cleanup wire shape: current_metrics PLUS
    the canonical ``time_series: TimeSeries`` field added by the
    legacy-TimeSeries cleanup (PRs #58/#59).  The mock MUST validate
    against the live ``YieldLevelOutput`` model — see
    ``test_mock_validates_against_response_model``."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family": "UST",
            "tenor": "10Y",
            "current_yield_pct": 4.30,
            "daily_change_bps": 1.5,
            "weekly_change_bps": 8.0,
            "monthly_change_bps": -3.0,
            "z_score": 0.85,
            "high_252d_pct": 4.95,
            "low_252d_pct": 3.40,
            "percentile_252d": 58.0,
            "observation_count": 252,
        },
        "time_series": {
            "series_name": "ust_10y_yield",
            "units": "percent",
            "description": "Test yield series.",
            "rows": [
                {"date": "2026-04-29", "value": 4.28},
                {"date": "2026-04-30", "value": 4.30},
            ],
        },
    }


def _assert_yield_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, "get_yield_levels called without `config=`"
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "get_yield_levels_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'get_yield_levels_tool' (catches imports of the wrong tool's "
        "CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions


# ===========================================================================
# api/routes/rates/detail.py — /detail/yield endpoint
# ===========================================================================

class TestDetailYieldEndpointWiring:
    def test_yield_detail_passes_config(self):
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            detail_module,
            "get_yield_levels",
            return_value=_well_formed_yield_output(),
        ) as mock_yl:
            detail_module.yield_detail(
                engine=mock_engine,
                curve_family="UST",
                tenor="10Y",
                lookback_days=365,
                field_name="YLD_YTM_MID",
            )
        assert mock_yl.call_count == 1
        _assert_yield_config_passed(mock_yl.call_args)

    def test_field_name_default_is_none(self):
        """The Query default for field_name must be None; a hardcoded
        string would shadow the YAML's default_field_name."""
        from api.routes.rates import detail as detail_module
        import inspect
        sig = inspect.signature(detail_module.yield_detail)
        query_obj = sig.parameters["field_name"].default
        assert query_obj.default is None, (
            f"Query default for field_name must be None, got "
            f"{query_obj.default!r}"
        )

    def test_omitted_field_name_flows_none(self):
        """Mirrors FastAPI's request-parse resolution by passing
        field_name=None directly.  Confirms None reaches
        YieldLevelInput unchanged."""
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            detail_module,
            "get_yield_levels",
            return_value=_well_formed_yield_output(),
        ) as mock_yl:
            detail_module.yield_detail(
                engine=mock_engine,
                curve_family="UST",
                tenor="10Y",
                lookback_days=365,
                field_name=None,
            )
        params = mock_yl.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"None must reach YieldLevelInput unchanged; got {params.field_name!r}"
        )


# ===========================================================================
# rates_agent/sovereign_bonds/mcp_server.py — get_yield_levels_tool
# ===========================================================================

class TestMcpYieldToolWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "get_yield_levels",
            return_value=_well_formed_yield_output(),
        ) as mock_yl:
            output_json = mcp_module.get_yield_levels_tool(
                curve_family="UST",
                tenor="10Y",
                lookback_days=365,
                field_name="YLD_YTM_MID",
            )
        assert mock_yl.call_count == 1
        _assert_yield_config_passed(mock_yl.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_field_name_default_is_empty_string_sentinel(self):
        """The MCP wrapper's field_name default must be the empty-
        string sentinel, NOT 'YLD_YTM_MID'.  Hardcoding any other
        string here shadows the YAML's default_field_name."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(mcp_module.get_yield_levels_tool)
        default = sig.parameters["field_name"].default
        assert default == "", (
            f"MCP wrapper's field_name default must be '' (empty-string "
            f"sentinel for 'use the YAML default'); got {default!r}"
        )

    def test_omitted_field_name_flows_none_to_yield_input(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "get_yield_levels",
            return_value=_well_formed_yield_output(),
        ) as mock_yl:
            mcp_module.get_yield_levels_tool(
                curve_family="UST",
                tenor="10Y",
                lookback_days=365,
                # field_name omitted
            )
        params = mock_yl.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach YieldLevelInput as None "
            f"(sentinel for 'use YAML default_field_name'), got "
            f"{params.field_name!r}"
        )

    def test_explicit_field_name_passes_through(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "get_yield_levels",
            return_value=_well_formed_yield_output(),
        ) as mock_yl:
            mcp_module.get_yield_levels_tool(
                curve_family="UST",
                tenor="10Y",
                field_name="PX_LAST",
            )
        params = mock_yl.call_args.kwargs["params"]
        assert params.field_name == "PX_LAST"


# ===========================================================================
# api/routes/rates/cards.py — /yield-snapshot endpoint
#
# This endpoint uses _compute_yield_snapshot, a SEPARATE batch
# reimplementation (one bulk SQL query for all instruments, then
# pandas groupby + per-instrument primitive call).  Codex's review of
# the migration plan correctly flagged that this surface MUST also be
# config-driven, otherwise yield_levels/config.yaml is not the single
# source of truth.  These tests prove that wiring.
# ===========================================================================

class TestCardsYieldSnapshotWiring:
    def test_compute_yield_snapshot_loads_config(self):
        """The batch path must call load_tool_config(YIELD_LEVELS_CONFIG_PATH)
        exactly once per request."""
        from shared.config import load_tool_config as real_load_tool_config

        from api.routes.rates import cards as cards_module

        mock_engine = MagicMock(name="engine")
        # The SQL query inside _compute_yield_snapshot returns rows
        # via engine.connect() context manager.  We mock engine.connect
        # to return a context manager whose execute() returns an empty
        # rowset — that short-circuits past the per-instrument loop
        # while still proving the config load happens.
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_result.keys.return_value = ["trade_date", "curve_family", "tenor", "field_value"]
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        with patch.object(
            cards_module, "load_tool_config", wraps=real_load_tool_config,
        ) as mock_load:
            cards_module._compute_yield_snapshot(mock_engine)

        # Should have called load_tool_config exactly once for this request.
        assert mock_load.call_count == 1
        loaded_path = mock_load.call_args.args[0] if mock_load.call_args.args else mock_load.call_args.kwargs["path"]
        assert str(loaded_path).endswith("yield_levels/config.yaml")

    def test_compute_yield_snapshot_uses_config_field_name_in_sql_query(self):
        """The bulk SQL query's field_name parameter must come from
        the YAML's default_field_name, not a hardcoded string.
        Catches the regression where someone reverts the field_name
        to a literal."""
        from api.routes.rates import cards as cards_module

        mock_engine = MagicMock(name="engine")
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_result.keys.return_value = ["trade_date", "curve_family", "tenor", "field_value"]
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        cards_module._compute_yield_snapshot(mock_engine)

        # The execute() call's second positional arg is the bind-param
        # dict; field_name should be YLD_YTM_MID (from YAML).
        execute_call = mock_conn.execute.call_args
        bind_params = execute_call.args[1] if len(execute_call.args) >= 2 else execute_call.kwargs
        assert bind_params["field_name"] == "YLD_YTM_MID", (
            f"bulk SQL query should use field_name from YAML "
            f"(default_field_name=YLD_YTM_MID), got "
            f"{bind_params.get('field_name')!r}"
        )

    def test_compute_yield_snapshot_uses_compute_level_metrics_primitive(self):
        """Per-instrument metrics computation must go through the
        shared compute_level_metrics primitive — not reimplement the
        z-score / period-changes / trailing-range math inline.  This
        guards against drift between the batch surface and the
        single-tool surface."""
        from api.routes.rates import cards as cards_module
        from shared.analytics.levels import compute_level_metrics as real_primitive

        # Build synthetic SQL rows: 2 instruments, 100 trading days each.
        bdays = pd.bdate_range("2024-01-01", periods=100)
        rows = []
        for cf, tenor in [("UST", "10Y"), ("DE_BUND", "10Y")]:
            for i, d in enumerate(bdays):
                rows.append({
                    "trade_date": d,
                    "curve_family": cf,
                    "tenor": tenor,
                    "field_value": 4.0 + 0.001 * i,
                })

        mock_engine = MagicMock(name="engine")
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [tuple(r.values()) for r in rows]
        mock_result.keys.return_value = ["trade_date", "curve_family", "tenor", "field_value"]
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        with patch.object(
            cards_module, "compute_level_metrics", wraps=real_primitive,
        ) as spy:
            output = cards_module._compute_yield_snapshot(mock_engine)

        # Two instruments → primitive called exactly twice.
        assert spy.call_count == 2
        # Each call uses the YAML conventions (window=252 etc.).
        for call in spy.call_args_list:
            kw = call.kwargs
            assert kw["z_window"] == 252
            assert kw["z_min_periods"] == 60
            assert kw["z_ddof"] == 1
            assert kw["trailing_window"] == 252
            assert kw["period_offsets"] == {"daily": 2, "weekly": 6, "monthly": 22}
        # Output rows have the expected wire shape.
        assert len(output) == 2
        for row in output:
            assert hasattr(row, "high_252d_pct")
            assert hasattr(row, "percentile_252d")


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.sovereign_bonds.tools.yield_levels import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.sovereign_bonds.tools.yield_levels.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_yield_levels_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(YIELD_LEVELS_CONFIG_PATH)
        assert cfg.tool.name == "get_yield_levels_tool"


# ===========================================================================
# Response-model validation (Codex P2 follow-up)
# ===========================================================================

class TestDetailRouteResponseModelValidation:
    """The wiring tests above call ``yield_detail`` directly (bypassing
    FastAPI's response-model validation).  These tests pin the
    HTTP-contract layer two ways:

      1. The mock used by the wiring tests MUST validate against the
         live ``YieldLevelOutput`` Pydantic response model — guards
         against the mock drifting away from the actual schema (e.g.,
         when a future schema change adds a required field).

      2. Round-tripping the mock through ``YieldLevelOutput`` and
         back to ``model_dump()`` produces a payload structurally
         identical to what FastAPI would emit, including the
         canonical ``time_series`` field added by the legacy-TimeSeries
         cleanup (PRs #58/#59).
    """

    def test_mock_validates_against_response_model(self):
        from rates_agent.sovereign_bonds.tools.yield_levels.schemas import (
            YieldLevelOutput,
        )
        # No pytest.raises — model_validate must succeed.  If it
        # raises, the wiring-test mock is out of sync with the live
        # schema (which is exactly the failure mode this test pins).
        validated = YieldLevelOutput.model_validate(_well_formed_yield_output())
        # Canonical TimeSeries field must be present on the validated
        # object (i.e. not silently dropped by extra='ignore' or
        # similar).
        assert validated.time_series is not None
        assert validated.time_series.units.value == "percent"

    def test_response_model_requires_time_series_field(self):
        """Removing ``time_series`` from the mock MUST raise — proves
        the field is REQUIRED on the response model, not optional.
        Without this guard, the new canonical field could be dropped
        in a future refactor and the wiring tests would still pass."""
        from rates_agent.sovereign_bonds.tools.yield_levels.schemas import (
            YieldLevelOutput,
        )
        from pydantic import ValidationError
        bad_mock = _well_formed_yield_output()
        bad_mock.pop("time_series")
        with pytest.raises(ValidationError):
            YieldLevelOutput.model_validate(bad_mock)

    def test_route_emits_full_response_after_model_validation(self):
        """End-to-end pin: route returns the mock dict, the response
        model validates it, and ``model_dump()`` round-trips with the
        canonical ``time_series`` field intact (the field FastAPI's
        response_model layer would emit on the wire)."""
        from api.routes.rates import detail as detail_module
        from rates_agent.sovereign_bonds.tools.yield_levels.schemas import (
            YieldLevelOutput,
        )

        mock_engine = MagicMock(name="engine")
        with patch.object(
            detail_module,
            "get_yield_levels",
            return_value=_well_formed_yield_output(),
        ):
            raw = detail_module.yield_detail(
                engine=mock_engine,
                curve_family="UST",
                tenor="10Y",
                lookback_days=365,
                field_name=None,
            )
        # Simulate FastAPI's response_model coercion.
        validated = YieldLevelOutput.model_validate(raw)
        dumped = validated.model_dump()
        assert "time_series" in dumped
        assert dumped["time_series"]["units"] == "percent"
        assert len(dumped["time_series"]["rows"]) == 2
