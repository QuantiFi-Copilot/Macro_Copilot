"""
test_half_life_wiring.py — Caller-wiring smoke tests for half_life.

Per the v6 transport rule, half_life has nested union inputs
(``Optional[SeriesSpec] | Optional[PairSpec] | Optional[PastedTimeSeries]``)
so it ships **MCP only this sprint**.  No FastAPI route until the
UI-integration PR — pinned by an explicit absence test so a future
commit can't accidentally add a flat-Query route that wouldn't fit
the input shape.

This file ALSO carries forward the rolling_regression follow-up's
lesson on the nested-MCP risk: the smoke test in
``test_mcp_nested_wrapper_smoke.py`` already proves the pattern works
for one tool, but each subsequent nested-input tool must independently
verify its OWN schema introspects correctly + accept a ``call_tool``
invocation.  Both are pinned here.

Key load-bearing properties:
  - The MCP wrapper's signature uses three Optional Pydantic types
    (per the v6 plan Delta P), NOT a Python Union.
  - The wrapper passes config explicitly.
  - The wrapper's snapshot output is the ONLY surface (no time-series
    payload to withhold).
  - No FastAPI route exists for this tool in this sprint.
  - The cross-layer min_observations error maps to HTTP-422-class
    phrase shape (the route doesn't exist yet, but we pin the phrase
    so when the route is added it will surface as 422 by default).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.sovereign_bonds.tools.half_life import (
    CONFIG_PATH as HALF_LIFE_CONFIG_PATH,
)
from shared.config import ToolConfig, clear_tool_config_cache
from shared.schemas import (
    PairSpec,
    PastedTimeSeries,
    SeriesSpec,
    TimeSeriesRow,
    TimeSeriesUnits,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_hl_output() -> dict:
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "series_label": "UST_10Y",
            "series_units": "percent",
            "is_mean_reverting": True,
            "half_life_days": 14.5,
            "half_life_ci_lower_days": 12.0,
            "half_life_ci_upper_days": 17.0,
            "long_run_mean_native": 4.0,
            "current_value_native": 4.25,
            "current_deviation_native": 0.25,
            "beta": -0.0521,
            "beta_ci_lower": -0.0640,
            "beta_ci_upper": -0.0402,
            "r_squared": 0.0860,
            "observation_count": 999,
            "confidence_level_used": 0.95,
        },
    }


def _assert_hl_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "half_life_tool"
    assert "min_observations" in cfg.conventions
    # OU rounding knob is intentionally a SEPARATE convention name
    # from the regression-family beta_round_decimals.
    assert "ou_beta_round_decimals" in cfg.conventions
    assert "beta_round_decimals" not in cfg.conventions


# ===========================================================================
# rates_agent/sovereign_bonds/mcp_server.py — calculate_half_life_tool
# ===========================================================================

class TestMcpHalfLifeWrapper:
    def test_passes_config_to_tool(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_half_life",
            return_value=_well_formed_hl_output(),
        ) as mock_hl:
            output_json = mcp_module.calculate_half_life_tool(
                series_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
                lookback_days=1825,
            )
        assert mock_hl.call_count == 1
        _assert_hl_config_passed(mock_hl.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_signature_uses_three_optional_pydantic(self):
        """Per v6 Delta P: nested-union tools use three Optional
        Pydantic types in the signature, NOT Python ``Union``.  The
        ``model_validator`` enforces 'exactly one'.  This pin
        catches a future maintainer flattening to Union[...]."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        import typing

        fn = mcp_module.calculate_half_life_tool
        underlying = getattr(fn, "fn", fn)
        underlying = getattr(underlying, "__wrapped__", underlying)
        hints = typing.get_type_hints(underlying)
        hints.pop("return", None)

        # series_spec, pair_spec, pasted_series all Optional[...]
        for name, expected_inner in (
            ("series_spec", SeriesSpec),
            ("pair_spec", PairSpec),
            ("pasted_series", PastedTimeSeries),
        ):
            ann = hints[name]
            origin = typing.get_origin(ann)
            args = typing.get_args(ann)
            # Optional[X] is Union[X, None]
            assert origin is typing.Union, (
                f"{name!r} must be Optional[{expected_inner.__name__}]; "
                f"got origin {origin!r}"
            )
            assert expected_inner in args and type(None) in args, (
                f"{name!r} must be Optional[{expected_inner.__name__}]; "
                f"got args {args!r}"
            )
        # lookback_days is a flat int.
        assert hints["lookback_days"] is int

    def test_pair_spec_path_invocation(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_half_life",
            return_value=_well_formed_hl_output(),
        ) as mock_hl:
            mcp_module.calculate_half_life_tool(
                pair_spec=PairSpec(cf1="IT_BTP", cf2="DE_BUND", tenor="10Y"),
                lookback_days=1825,
            )
        params = mock_hl.call_args.kwargs["params"]
        assert params.pair_spec is not None
        assert params.series_spec is None
        assert params.pasted_series is None
        assert params.pair_spec.cf1 == "IT_BTP"

    def test_pasted_series_path_invocation(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        pasted = PastedTimeSeries(
            series_name="custom_residual_bps",
            units=TimeSeriesUnits.BPS,
            rows=[
                TimeSeriesRow(date="2026-04-29", value=78.5),
                TimeSeriesRow(date="2026-04-30", value=80.1),
            ],
        )
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_half_life",
            return_value=_well_formed_hl_output(),
        ) as mock_hl:
            mcp_module.calculate_half_life_tool(pasted_series=pasted)
        params = mock_hl.call_args.kwargs["params"]
        assert params.pasted_series is not None
        assert params.series_spec is None
        assert params.pair_spec is None
        assert params.pasted_series.units == TimeSeriesUnits.BPS

    def test_pasted_series_path_does_not_require_db_engine(self):
        """The pasted_series path advertises chain-from-prior-tool
        WITHOUT touching the database.  Pin that behaviour: when
        pasted_series is supplied, the wrapper must NOT call
        _get_engine() — even if the engine factory would raise.
        Codex caught this regression in the initial PR (the wrapper
        was unconditionally calling _get_engine() and breaking the
        DB-free contract for the pasted path)."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        pasted = PastedTimeSeries(
            series_name="custom_residual_bps",
            units=TimeSeriesUnits.BPS,
            rows=[
                TimeSeriesRow(date="2026-04-29", value=78.5),
                TimeSeriesRow(date="2026-04-30", value=80.1),
            ],
        )
        # Make _get_engine raise — if the wrapper calls it on the
        # pasted path the test will surface "Database connection
        # failed" in the response.  With the fix in place,
        # _get_engine is NEVER called.
        with patch.object(
            mcp_module,
            "_get_engine",
            side_effect=AssertionError(
                "_get_engine() must NOT be called on the pasted_series path"
            ),
        ), patch.object(
            mcp_module,
            "calculate_half_life",
            return_value=_well_formed_hl_output(),
        ) as mock_compute:
            output_json = mcp_module.calculate_half_life_tool(pasted_series=pasted)

        # No 'Database connection failed' in the response.
        parsed = json.loads(output_json)
        assert "error" not in parsed, (
            f"pasted_series path surfaced an error envelope: {parsed.get('error')!r}"
        )
        assert "current_metrics" in parsed
        # And compute() received engine=None (the wrapper passes None
        # explicitly on the pasted path).
        assert mock_compute.call_count == 1
        assert mock_compute.call_args.kwargs["engine"] is None

    def test_series_spec_path_still_acquires_db_engine(self):
        """Sibling check: the DB-backed paths must STILL acquire an
        engine.  Verifies the conditional gating doesn't accidentally
        starve the series_spec / pair_spec paths."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ) as mock_get_engine, patch.object(
            mcp_module,
            "calculate_half_life",
            return_value=_well_formed_hl_output(),
        ) as mock_compute:
            mcp_module.calculate_half_life_tool(
                series_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
                lookback_days=1825,
            )
        # _get_engine called exactly once on the DB-backed path.
        assert mock_get_engine.call_count == 1
        # compute() received the live mock engine (NOT None).
        assert mock_compute.call_args.kwargs["engine"] is mock_engine

    def test_db_failure_on_pasted_path_does_not_break_request(self):
        """Even when _get_engine would raise, the pasted_series path
        succeeds — proves the fix actually short-circuits the engine
        acquisition rather than just catching the exception."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        pasted = PastedTimeSeries(
            series_name="x",
            units=TimeSeriesUnits.BPS,
            rows=[
                TimeSeriesRow(date="2026-04-29", value=1.0),
                TimeSeriesRow(date="2026-04-30", value=2.0),
            ],
        )
        with patch.object(
            mcp_module,
            "_get_engine",
            side_effect=ConnectionError("DB unavailable"),
        ), patch.object(
            mcp_module,
            "calculate_half_life",
            return_value=_well_formed_hl_output(),
        ):
            output_json = mcp_module.calculate_half_life_tool(pasted_series=pasted)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        # Did NOT surface as a DB error.
        assert "Database connection failed" not in (parsed.get("error") or "")

    def test_zero_inputs_surfaces_validation_error_envelope(self):
        """Calling the wrapper with no input variant returns the
        controlled-error envelope (not a Python exception).  The
        model_validator on HalfLifeInput is the source of the error."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ):
            output_json = mcp_module.calculate_half_life_tool()
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "exactly one" in parsed["error"].lower()


# ===========================================================================
# api/routes/rates/detail.py — /detail/half-life route exists
# ===========================================================================

class TestFastApiRouteExists:
    """Consolidation update: the old 'MCP-only this sprint' pin is
    retired.  Per the standalone-bridge standard (consolidation
    target #4) half_life deliberately gained a typed-detail GET
    route — the one-of-three source union flattens to query params
    (series mode = ``curve_family`` + ``tenor``; pair mode adds
    ``curve_family_2``; ``pasted_series`` stays on the generic run
    endpoint).  Pin the live route and its contract instead."""

    def test_route_for_half_life_exists(self):
        from api.routes.rates import detail as detail_module
        import inspect
        handlers = [
            name for name, obj in inspect.getmembers(detail_module)
            if inspect.isfunction(obj) and name.endswith("_detail")
        ]
        assert "half_life_detail" in handlers, (
            "GET /detail/half-life was added in the consolidation "
            "(standalone-bridge standard, target #4); the typed-"
            "detail handler must exist in detail.py."
        )

    def test_route_path_method_and_response_model(self):
        from api.routes.rates import detail as detail_module
        from rates_agent.sovereign_bonds.tools.half_life import (
            HalfLifeOutput,
        )
        routes = {
            route.path: route for route in detail_module.router.routes
        }
        assert "/detail/half-life" in routes
        route = routes["/detail/half-life"]
        assert "GET" in route.methods
        assert route.response_model is HalfLifeOutput


# ===========================================================================
# Nested MCP transport — call_tool() end-to-end (carry forward the
# rolling_regression follow-up's lesson)
# ===========================================================================

class TestNestedMcpTransportExecution:
    """The schema-introspection check (proves FastMCP can describe
    the nested-Optional union signature) is necessary but not
    sufficient.  Codex's review of rolling_regression's initial PR
    pointed out that schema-gen success ≠ transport success — only a
    real call_tool() invocation through FastMCP's transport layer
    proves the wrapper actually runs.  Pin both paths here for
    half_life since this is the second nested-input MCP tool."""

    def test_call_tool_with_pair_spec_succeeds(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_half_life",
            return_value=_well_formed_hl_output(),
        ) as mock_compute:
            args = {
                "pair_spec": {
                    "cf1": "IT_BTP",
                    "cf2": "DE_BUND",
                    "tenor": "10Y",
                },
                "lookback_days": 1825,
            }
            result = asyncio.run(
                mcp_module.mcp.call_tool("calculate_half_life_tool", args)
            )
        assert isinstance(result, tuple) and len(result) == 2
        contents, _structured = result
        text = contents[0].text
        parsed = json.loads(text)
        assert "current_metrics" in parsed
        # Underlying compute received a properly-constructed Pydantic
        # input with pair_spec set.
        assert mock_compute.call_count == 1
        params = mock_compute.call_args.kwargs["params"]
        assert params.pair_spec is not None
        assert params.pair_spec.cf1 == "IT_BTP"
        assert params.pair_spec.cf2 == "DE_BUND"

    def test_call_tool_with_pasted_series_succeeds(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        # The MCP wrapper unconditionally calls _get_engine() even
        # though the pasted_series path doesn't actually use the DB.
        # Mock it so the test doesn't need a live engine.
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_half_life",
            return_value=_well_formed_hl_output(),
        ) as mock_compute:
            args = {
                "pasted_series": {
                    "series_name": "custom_residual_bps",
                    "units": "bps",
                    "rows": [
                        {"date": "2026-04-29", "value": 78.5},
                        {"date": "2026-04-30", "value": 80.1},
                    ],
                },
            }
            result = asyncio.run(
                mcp_module.mcp.call_tool("calculate_half_life_tool", args)
            )
        assert isinstance(result, tuple) and len(result) == 2
        params = mock_compute.call_args.kwargs["params"]
        assert params.pasted_series is not None
        assert params.pasted_series.units == TimeSeriesUnits.BPS
        assert len(params.pasted_series.rows) == 2


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.sovereign_bonds.tools.half_life import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.sovereign_bonds.tools.half_life.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_half_life_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(HALF_LIFE_CONFIG_PATH)
        assert cfg.tool.name == "half_life_tool"
        assert cfg.tool.category == "desk_invariant_primitive"
