"""
test_curve_spread_wiring.py — Caller-wiring smoke tests
=========================================================

Commit 4 of the tool-config pilot wires three external callers
(``api/routes/rates/cards.py``, ``api/routes/rates/detail.py``,
``rates_agent/sovereign_bonds/mcp_server.py``) to load
``curve_spread/config.yaml`` and pass it explicitly as the ``config``
kwarg to ``calculate_curve_spread``.  Before this commit they relied
on compute()'s auto-load fallback; the wiring change makes the config
dependency observable at every callsite.

These tests are **wiring proofs**, not full integration tests:

  - We mock ``calculate_curve_spread`` itself so no DB / no SQL.
  - We invoke each caller's endpoint / tool function directly and
    inspect ``mock.call_args.kwargs`` to confirm a non-None ``config``
    of type ``ToolConfig`` was supplied.
  - We assert the supplied config is the bundled curve_spread config
    by checking ``cfg.tool.name``, so a misconfigured caller (one
    that imports the wrong CONFIG_PATH or an alternate tool's config)
    fails the test loudly.

Things this file does NOT verify:

  - That the SQL query / DB engine / response serialisation works.
    Those are integration concerns covered by the parity test
    (commit 0) once live-DB fixtures are captured.
  - That the LLM tool description still parses (covered by the
    MCP server smoke run at deploy time).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from rates_agent.sovereign_bonds.tools.curve_spread import (
    CONFIG_PATH as CURVE_SPREAD_CONFIG_PATH,
    CurveSpreadInput,
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


def _well_formed_curve_spread_output() -> dict:
    """Return a minimal calculate_curve_spread output dict that the
    callers can serialise without choking on missing fields."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family": "UST",
            "spread_label": "2s10s",
            "current_spread_bps": 50.0,
            "daily_change_bps": 1.0,
            "current_z_score": 0.5,
            "rolling_window_days": 252,
            "short_tenor_yield": 4.0,
            "long_tenor_yield": 4.5,
        },
        "time_series": [
            {"date": "2026-04-29", "spread_bps": 49.0, "z_score": 0.4},
            {"date": "2026-04-30", "spread_bps": 50.0, "z_score": 0.5},
        ],
    }


def _assert_curve_spread_config_passed(call_args) -> None:
    """Common assertion: the call passed a ToolConfig whose ``tool.name``
    matches the curve_spread tool.  Catches both:
      - missing config (caller forgot to pass it),
      - wrong config (caller imported a different tool's path)."""
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, "calculate_curve_spread called without `config=`"
    assert isinstance(cfg, ToolConfig), (
        f"`config` must be a ToolConfig, got {type(cfg).__name__}"
    )
    assert cfg.tool.name == "calculate_curve_spread_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'calculate_curve_spread_tool'"
    )
    # Sanity: at least one expected convention is present.
    assert "z_score_window_days" in cfg.conventions, (
        "passed config is missing z_score_window_days — wrong file path?"
    )


# ===========================================================================
# api/routes/rates/cards.py — /curve-shapes endpoint
# ===========================================================================

class TestCardsEndpointWiring:
    def test_curve_shapes_passes_config_to_curve_spread(self):
        from api.routes.rates import cards as cards_module

        mock_engine = MagicMock(name="engine")
        # Replace calculate_curve_spread inside the cards module with a
        # mock that returns a well-formed output, so the endpoint's
        # post-processing runs successfully.
        with patch.object(
            cards_module,
            "calculate_curve_spread",
            return_value=_well_formed_curve_spread_output(),
        ) as mock_cs:
            cards_module.curve_shapes(
                engine=mock_engine,
                curves="UST",  # one curve only — keeps the loop tight
                short_tenor="2Y",
                long_tenor="10Y",
            )

        assert mock_cs.call_count == 1
        _assert_curve_spread_config_passed(mock_cs.call_args)
        # And sanity-check the rest of the call
        assert mock_cs.call_args.kwargs["engine"] is mock_engine

    def test_curve_shapes_passes_same_config_instance_to_every_curve(self):
        """Across N curves, every call must receive the same ToolConfig
        instance.  This proves the endpoint isn't reconstructing
        Configs per iteration (e.g. via a custom ToolConfig() built
        from a dict — which would be a different object identity even
        if the values matched).

        NOTE: this test does NOT prove that load_tool_config is called
        only once — load_tool_config is process-cached by path, so
        repeated in-loop calls would still return the same instance
        and this `is` check would pass.  See
        ``test_curve_shapes_loads_config_only_once_per_request`` for
        the call-count assertion that catches that regression.
        """
        from api.routes.rates import cards as cards_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            cards_module,
            "calculate_curve_spread",
            return_value=_well_formed_curve_spread_output(),
        ) as mock_cs:
            cards_module.curve_shapes(
                engine=mock_engine,
                curves="UST,DE_BUND,IT_BTP",
                short_tenor="2Y",
                long_tenor="10Y",
            )

        assert mock_cs.call_count == 3
        configs = [c.kwargs["config"] for c in mock_cs.call_args_list]
        assert all(c is configs[0] for c in configs), (
            "every curve received a different ToolConfig instance — the "
            "endpoint must reuse one Config across the loop"
        )
        for ca in mock_cs.call_args_list:
            _assert_curve_spread_config_passed(ca)

    def test_curve_shapes_loads_config_only_once_per_request(self):
        """``load_tool_config`` must be invoked exactly once per request,
        regardless of how many curves are in the per-curve loop.  If a
        future refactor moves the load into the loop, ``call_count``
        scales with curve count and this test fails.

        We spy on ``load_tool_config`` via ``wraps=`` so the real
        function still runs (the cache returns a real ToolConfig);
        only the call count is what we assert on.  Cache-state is
        normalised at the top of every test by the autouse
        ``_clear_cache`` fixture, so this is robust to test ordering.
        """
        from shared.config import load_tool_config as real_load_tool_config

        from api.routes.rates import cards as cards_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            cards_module,
            "calculate_curve_spread",
            return_value=_well_formed_curve_spread_output(),
        ), patch.object(
            cards_module,
            "load_tool_config",
            wraps=real_load_tool_config,
        ) as mock_load:
            cards_module.curve_shapes(
                engine=mock_engine,
                curves="UST,DE_BUND,IT_BTP",  # three curves
                short_tenor="2Y",
                long_tenor="10Y",
            )

        # If the load is correctly outside the loop: 1 call.
        # If accidentally inside the loop: 3 calls.
        assert mock_load.call_count == 1, (
            f"load_tool_config was called {mock_load.call_count} times for "
            "a 3-curve request; expected exactly 1 (load must live outside "
            "the per-curve loop).  Likely cause: the load was moved inside "
            "the for-loop in curve_shapes."
        )

    def test_curve_shapes_returns_503_when_config_load_fails(self):
        """When ``load_tool_config`` raises (missing YAML, invalid
        schema, etc.), the endpoint must return a clean 503 rather
        than letting the exception escape as an unhandled 500.  This
        matches the failure-mode contract used by detail.py and
        mcp_server.py."""
        from fastapi import HTTPException

        from api.routes.rates import cards as cards_module
        from shared.config import ToolConfigError

        mock_engine = MagicMock(name="engine")

        def _broken_load(*_args, **_kwargs):
            raise ToolConfigError(
                "Tool config not found: /nope/config.yaml"
            )

        with patch.object(
            cards_module, "load_tool_config", side_effect=_broken_load,
        ), patch.object(
            cards_module,
            "calculate_curve_spread",
            return_value=_well_formed_curve_spread_output(),
        ) as mock_cs:
            with pytest.raises(HTTPException) as exc_info:
                cards_module.curve_shapes(
                    engine=mock_engine,
                    curves="UST",
                    short_tenor="2Y",
                    long_tenor="10Y",
                )

        # 503, not 500 — config-loading is treated as a controlled
        # operational error, same shape as the per-curve failure path.
        assert exc_info.value.status_code == 503
        assert "config" in exc_info.value.detail.lower()
        # And we never reached calculate_curve_spread, because the
        # config load failed first.
        assert mock_cs.call_count == 0


# ===========================================================================
# api/routes/rates/detail.py — /detail/spread endpoint
# ===========================================================================

class TestDetailEndpointWiring:
    def test_spread_detail_passes_config_to_curve_spread(self):
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            detail_module,
            "calculate_curve_spread",
            return_value=_well_formed_curve_spread_output(),
        ) as mock_cs:
            result = detail_module.spread_detail(
                engine=mock_engine,
                curve_family="UST",
                short_tenor="2Y",
                long_tenor="10Y",
                lookback_days=365,
                field_name="YLD_YTM_MID",
            )

        assert mock_cs.call_count == 1
        _assert_curve_spread_config_passed(mock_cs.call_args)
        assert mock_cs.call_args.kwargs["engine"] is mock_engine
        # The endpoint returns the (mocked) output as-is.
        assert result == _well_formed_curve_spread_output()


# ===========================================================================
# rates_agent/sovereign_bonds/mcp_server.py — calculate_curve_spread_tool
# ===========================================================================

class TestMcpToolWiring:
    def test_calculate_curve_spread_tool_passes_config(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        # Mock the engine factory (avoid hitting the DB) and the math
        # function (capture call args).
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module,
            "_get_engine",
            return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_curve_spread",
            return_value=_well_formed_curve_spread_output(),
        ) as mock_cs:
            # FastMCP's ``@mcp.tool()`` decorator registers the tool
            # with the server but returns the original function
            # unchanged — so we invoke it as a plain Python callable.
            output_json = mcp_module.calculate_curve_spread_tool(
                curve_family="UST",
                short_tenor="2Y",
                long_tenor="10Y",
                lookback_days=365,
                field_name="YLD_YTM_MID",
            )

        assert mock_cs.call_count == 1
        _assert_curve_spread_config_passed(mock_cs.call_args)
        # MCP serialises the result to JSON; smoke-check it parsed
        # cleanly and includes current_metrics.
        import json
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        # MCP server strips time_series before returning to LLM.
        assert "time_series" not in parsed


# ===========================================================================
# CONFIG_PATH public symbol — backward-compat sanity
# ===========================================================================

class TestConfigPathPublicSymbol:
    """Commit 4 promoted ``_CONFIG_PATH`` to ``CONFIG_PATH`` and re-
    exported it from the package init; commit 6 removed the underscore
    alias entirely.  These tests lock in the post-removal contract:

      - ``CONFIG_PATH`` is exposed by both the package init and the
        compute submodule (and they resolve to the same Path);
      - loading via either path returns the curve_spread ToolConfig;
      - the underscore alias no longer exists.
    """

    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.sovereign_bonds.tools.curve_spread import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.sovereign_bonds.tools.curve_spread.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_underscore_alias_no_longer_exists(self):
        """``_CONFIG_PATH`` was kept for one migration step and has now
        been removed.  If anyone restores it (out of habit or
        copy-paste from older docs), this test fails — restoring the
        alias should be a deliberate decision, not silent."""
        from rates_agent.sovereign_bonds.tools.curve_spread import compute
        assert not hasattr(compute, "_CONFIG_PATH"), (
            "`_CONFIG_PATH` underscore alias was retired in commit 6 "
            "of the tool-config pilot.  Use `CONFIG_PATH` (public) "
            "instead.  If restoring the alias is intentional, also "
            "delete this test in the same change so the rationale is "
            "recorded together."
        )

    def test_load_returns_curve_spread_config(self):
        cfg = load_tool_config(CURVE_SPREAD_CONFIG_PATH)
        assert cfg.tool.name == "calculate_curve_spread_tool"
        assert cfg.tool.domain == "sovereign_bonds"
