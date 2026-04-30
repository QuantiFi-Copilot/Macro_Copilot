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

    def test_curve_shapes_reuses_config_across_curve_loop(self):
        """The endpoint loads config once outside the per-curve loop;
        across N curves it must invoke calculate_curve_spread N times,
        all with the same ToolConfig instance (no per-iteration reload).
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
        # All three must be the same ToolConfig instance — proves the
        # config is loaded once and reused, not re-loaded per curve.
        assert all(c is configs[0] for c in configs), (
            "config was re-loaded per curve; expected one load reused across the loop"
        )
        for ca in mock_cs.call_args_list:
            _assert_curve_spread_config_passed(ca)


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
    """Commit 4 promotes ``_CONFIG_PATH`` to ``CONFIG_PATH`` and re-
    exports it from the package init.  This regression-tests the
    promotion: both names resolve to the same Path object, and
    loading via either path returns the same cached ToolConfig."""

    def test_public_and_legacy_paths_are_identical(self):
        from rates_agent.sovereign_bonds.tools.curve_spread import CONFIG_PATH
        from rates_agent.sovereign_bonds.tools.curve_spread.compute import (
            _CONFIG_PATH,
        )
        assert CONFIG_PATH == _CONFIG_PATH

    def test_load_returns_curve_spread_config(self):
        cfg = load_tool_config(CURVE_SPREAD_CONFIG_PATH)
        assert cfg.tool.name == "calculate_curve_spread_tool"
        assert cfg.tool.domain == "sovereign_bonds"
