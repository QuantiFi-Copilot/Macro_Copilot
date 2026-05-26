"""
test_curve_move_classifier_wiring.py — Caller-wiring smoke tests
==================================================================

Mirrors ``tests/test_curve_spread_wiring.py`` for the
``classify_curve_move`` tool.  Three external callers
(``rates_agent/sovereign_bonds/mcp_server.py``,
``api/routes/rates/cards.py``'s ``/regimes`` endpoint, and
``api/routes/rates/detail.py``'s ``/detail/regime`` endpoint) load
``curve_move_classifier/config.yaml`` and pass it as the explicit
``config`` kwarg to ``classify_curve_move_compute``.  These tests
prove the wiring rather than full integration.

Things asserted:

  - ``calculate_curve_spread`` / ``classify_curve_move_compute`` is
    invoked with a ``config`` whose ``tool.name`` is the curve-move
    classifier (catches "imported the wrong tool's CONFIG_PATH").
  - The cards ``/regimes`` endpoint loads its config ONCE per
    request, not per-curve-or-period iteration.
  - The cards ``/regimes`` endpoint returns a 503 (not an unhandled
    500) when ``load_tool_config`` raises — same operational-error
    discipline as the other endpoints.
  - The detail ``/detail/regime`` endpoint translates the new
    ``classification`` / ``description`` field names to the legacy
    ``regime_tag`` / ``regime_description`` wire format the
    frontend expects.
  - The MCP wrapper invokes the renamed
    ``classify_curve_move_tool`` (not the legacy regime name).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.sovereign_bonds.tools.curve_move_classifier import (
    CONFIG_PATH as CURVE_MOVE_CONFIG_PATH,
    CurveMoveInput,
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


def _well_formed_curve_move_output(classification: str = "BEAR_FLATTENER") -> dict:
    """Mirror the new tool's output shape — uses ``classification`` /
    ``description`` (not ``regime_tag`` / ``regime_description``)."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "prior_date": "2026-04-29",
            "curve_family": "UST",
            "lookback_period": "1d",
            "spread_label": "2s10s",
            "classification": classification,
            "description": "Yields rose; curve flattened.",
            "front_tenor": "2Y",
            "back_tenor": "10Y",
            "front_level_current": 4.20,
            "back_level_current":  4.50,
            "front_level_prior":   4.16,
            "back_level_prior":    4.49,
            "front_change_bps":   +4.0,
            "back_change_bps":    +1.0,
            "spread_current_bps":  30.0,
            "spread_prior_bps":    33.0,
            "spread_change_bps":   -3.0,
        },
    }


def _assert_curve_move_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, "classify_curve_move_compute called without `config=`"
    assert isinstance(cfg, ToolConfig), (
        f"`config` must be a ToolConfig, got {type(cfg).__name__}"
    )
    assert cfg.tool.name == "classify_curve_move_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'classify_curve_move_tool' (catches imports of the wrong "
        "tool's CONFIG_PATH)"
    )
    assert "parallel_threshold_bps" in cfg.conventions, (
        "passed config missing parallel_threshold_bps — wrong file path?"
    )


# ===========================================================================
# api/routes/rates/cards.py — /regimes endpoint
# ===========================================================================

class TestCardsRegimesEndpointWiring:

    def test_regimes_passes_config_to_classify_curve_move(self):
        from api.routes.rates import cards as cards_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            cards_module,
            "classify_curve_move_compute",
            return_value=_well_formed_curve_move_output(),
        ) as mock_cm:
            cards_module.regimes(
                engine=mock_engine,
                curves="UST",
                front_tenor="2Y",
                back_tenor="10Y",
            )

        assert mock_cm.call_count >= 1
        for call in mock_cm.call_args_list:
            _assert_curve_move_config_passed(call)
            assert call.kwargs["engine"] is mock_engine

    def test_regimes_loads_config_only_once_per_request(self):
        """``load_tool_config`` must be called exactly ONCE per
        request, regardless of how many (curve × period) pairs the
        endpoint iterates over.  If a future refactor moves the
        load inside the loop, ``call_count`` scales with N×M and
        this test fails with a clear message."""
        from shared.config import load_tool_config as real_load_tool_config

        from api.routes.rates import cards as cards_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            cards_module,
            "classify_curve_move_compute",
            return_value=_well_formed_curve_move_output(),
        ), patch.object(
            cards_module,
            "load_tool_config",
            wraps=real_load_tool_config,
        ) as mock_load:
            # 2 curves × N periods → if load is per-iteration, count
            # explodes; if per-request, count == 1.
            cards_module.regimes(
                engine=mock_engine,
                curves="UST,DE_BUND",
                front_tenor="2Y",
                back_tenor="10Y",
            )

        # Exactly one load — for the curve-move tool's config.
        assert mock_load.call_count == 1, (
            f"load_tool_config was called {mock_load.call_count} times for "
            "a 2-curve regimes request; expected exactly 1 (load must "
            "live outside the per-(curve, period) loop)."
        )

    def test_regimes_returns_503_when_config_load_fails(self):
        from fastapi import HTTPException

        from api.routes.rates import cards as cards_module
        from shared.config import ToolConfigError

        def _broken_load(*_args, **_kwargs):
            raise ToolConfigError(
                "Tool config not found: /nope/config.yaml"
            )

        mock_engine = MagicMock(name="engine")
        with patch.object(
            cards_module, "load_tool_config", side_effect=_broken_load,
        ), patch.object(
            cards_module,
            "classify_curve_move_compute",
            return_value=_well_formed_curve_move_output(),
        ) as mock_cm:
            with pytest.raises(HTTPException) as exc_info:
                cards_module.regimes(
                    engine=mock_engine,
                    curves="UST",
                    front_tenor="2Y",
                    back_tenor="10Y",
                )

        assert exc_info.value.status_code == 503
        assert "configuration" in exc_info.value.detail.lower()
        # Short-circuit: tool was never reached.
        assert mock_cm.call_count == 0

    def test_regimes_translates_classification_to_regime_tag(self):
        """The /regimes wire format keeps ``regime_tag`` /
        ``regime_description`` field names for frontend
        backward-compat.  The translation happens at the API
        boundary."""
        from api.routes.rates import cards as cards_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            cards_module,
            "classify_curve_move_compute",
            return_value=_well_formed_curve_move_output("BULL_STEEPENER"),
        ):
            response = cards_module.regimes(
                engine=mock_engine,
                curves="UST",
                front_tenor="2Y",
                back_tenor="10Y",
            )

        assert len(response.regimes) >= 1
        # Wire shape uses regime_tag / regime_description, NOT the
        # tool's internal classification / description names.
        row = response.regimes[0]
        assert row.regime_tag == "BULL_STEEPENER"
        assert "Yields rose" in row.regime_description


# ===========================================================================
# api/routes/rates/detail.py — /detail/regime endpoint
# ===========================================================================

class TestDetailRegimeEndpointWiring:

    def test_regime_detail_passes_config_to_classify_curve_move(self):
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            detail_module,
            "classify_curve_move_compute",
            return_value=_well_formed_curve_move_output(),
        ) as mock_cm:
            detail_module.regime_detail(
                engine=mock_engine,
                curve_family="UST",
                front_tenor="2Y",
                back_tenor="10Y",
                lookback_period="22d",
                field_name="YLD_YTM_MID",
            )

        assert mock_cm.call_count == 1
        _assert_curve_move_config_passed(mock_cm.call_args)
        assert mock_cm.call_args.kwargs["engine"] is mock_engine

    def test_regime_detail_translates_to_legacy_wire_format(self):
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            detail_module,
            "classify_curve_move_compute",
            return_value=_well_formed_curve_move_output("BEAR_STEEPENER"),
        ):
            response = detail_module.regime_detail(
                engine=mock_engine,
                curve_family="UST",
                front_tenor="2Y",
                back_tenor="10Y",
                lookback_period="22d",
                field_name="YLD_YTM_MID",
            )

        # Endpoint returns a dict (no response_model) so the wire
        # format intentionally diverges from the tool's new schema.
        cm = response["current_metrics"]
        assert cm["regime_tag"] == "BEAR_STEEPENER"
        assert "regime_description" in cm
        # Legacy field names ONLY — translation must strip the new ones.
        assert "classification" not in cm
        assert "description" not in cm


# ===========================================================================
# rates_agent/sovereign_bonds/mcp_server.py — classify_curve_move_tool
# ===========================================================================

class TestMcpToolWiring:

    def test_classify_curve_move_tool_passes_config(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "classify_curve_move_compute",
            return_value=_well_formed_curve_move_output(),
        ) as mock_cm:
            output_json = mcp_module.classify_curve_move_tool(
                curve_family="UST",
                front_tenor="2Y",
                back_tenor="10Y",
                lookback_period="22d",
                field_name="YLD_YTM_MID",
            )

        assert mock_cm.call_count == 1
        _assert_curve_move_config_passed(mock_cm.call_args)

        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        # MCP server returns the tool's output shape directly (no
        # boundary translation); LLM sees the new field names.
        assert "classification" in parsed["current_metrics"]

    def test_legacy_tool_name_no_longer_exists(self):
        """The previous tool name was ``classify_curve_regime_tool``.
        Restoring it would silently shadow the new tool from the
        LLM's tool-discovery perspective.  This test ensures the
        rename is load-bearing."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        assert hasattr(mcp_module, "classify_curve_move_tool")
        assert not hasattr(mcp_module, "classify_curve_regime_tool"), (
            "classify_curve_regime_tool was retired in the curve_move_classifier "
            "migration.  If restoring is intentional, also delete this test."
        )


# ===========================================================================
# Wrapper-layer field_name shadowing — regression coverage for the
# Codex P1 follow-up.  These tests prove that omitting field_name
# through the SURFACE wrappers (MCP tool + FastAPI route) flows the
# YAML's default_field_name convention all the way to the SQL
# fetcher, instead of silently being shadowed by a hardcoded
# wrapper-layer default.
#
# The earlier wiring tests above pass field_name="YLD_YTM_MID"
# explicitly, so they don't catch wrapper shadowing.  These tests
# are the load-bearing safety net against that regression.
# ===========================================================================

class TestWrapperDoesNotShadowYamlDefault:

    def _capture_field_name_seen_by_compute(
        self, mock_cm: MagicMock,
    ) -> object:
        """Pull the field_name on the CurveMoveInput passed to
        classify_curve_move_compute.  None means "the wrapper
        correctly forwarded the omission" → compute() will then
        resolve it against the YAML's default_field_name."""
        params = mock_cm.call_args.kwargs["params"]
        return params.field_name

    def test_mcp_wrapper_field_name_default_is_empty_string_sentinel(self):
        """The MCP wrapper's ``field_name`` parameter must default to
        the empty-string sentinel "", NOT to "YLD_YTM_MID".  An
        explicit hardcoded "YLD_YTM_MID" here would shadow the YAML
        convention (the LLM omitting field_name would still hit the
        legacy default regardless of what the YAML says)."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(mcp_module.classify_curve_move_tool)
        default = sig.parameters["field_name"].default
        assert default == "", (
            f"MCP wrapper's field_name default must be '' (the "
            f"empty-string sentinel for 'use the YAML default'), "
            f"got {default!r}.  Hardcoding any other value here "
            f"shadows config.yaml's default_field_name convention."
        )

    def test_mcp_wrapper_omitted_field_name_flows_none_to_curve_move_input(self):
        """When the LLM doesn't pass field_name, the MCP wrapper
        translates the empty-string sentinel into None and hands
        that to CurveMoveInput.  compute() then resolves None
        against the YAML's default_field_name."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "classify_curve_move_compute",
            return_value=_well_formed_curve_move_output(),
        ) as mock_cm:
            # Crucially: omit field_name.
            mcp_module.classify_curve_move_tool(
                curve_family="UST",
                front_tenor="2Y",
                back_tenor="10Y",
                lookback_period="22d",
            )

        assert mock_cm.call_count == 1
        seen = self._capture_field_name_seen_by_compute(mock_cm)
        assert seen is None, (
            f"omitted field_name must reach CurveMoveInput as None "
            f"(sentinel for 'use YAML default_field_name'), "
            f"got {seen!r}.  Wrapper hardcoded a default before "
            f"the schema layer could see the omission."
        )

    def test_mcp_wrapper_explicit_field_name_passes_through(self):
        """An explicitly-passed field_name reaches CurveMoveInput
        unchanged — the empty-string sentinel translation must not
        clobber explicit overrides."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "classify_curve_move_compute",
            return_value=_well_formed_curve_move_output(),
        ) as mock_cm:
            mcp_module.classify_curve_move_tool(
                curve_family="UST",
                front_tenor="2Y",
                back_tenor="10Y",
                lookback_period="22d",
                field_name="YLD_BID",  # explicit override
            )

        assert mock_cm.call_count == 1
        assert self._capture_field_name_seen_by_compute(mock_cm) == "YLD_BID"

    def test_detail_route_field_name_default_is_none(self):
        """The /detail/regime endpoint's ``field_name`` Query default
        must be None (FastAPI's natural Optional support).  A
        hardcoded ``Query(default="YLD_YTM_MID")`` would shadow the
        YAML's default_field_name."""
        from api.routes.rates import detail as detail_module
        import inspect
        sig = inspect.signature(detail_module.regime_detail)
        # FastAPI Query objects have a ``.default`` attribute.
        query_obj = sig.parameters["field_name"].default
        assert query_obj.default is None, (
            f"Query default for field_name must be None, got "
            f"{query_obj.default!r}.  A hardcoded string here "
            f"shadows config.yaml's default_field_name convention."
        )

    def test_detail_route_omitted_field_name_flows_none(self):
        """When an HTTP client omits ``?field_name=``, FastAPI resolves
        the ``Query(default=None)`` to None at request-parse time and
        invokes ``regime_detail(field_name=None, ...)``.  We simulate
        that path here (calling the endpoint as a plain function
        bypasses FastAPI's request lifecycle, so the test must
        explicitly pass None to mirror what FastAPI would have done).

        This test proves that ONCE the route receives None, the value
        flows unchanged into CurveMoveInput — and from there
        compute() resolves it against the YAML's default_field_name.
        Combined with ``test_detail_route_field_name_default_is_none``
        (which checks the Query default itself), the pair covers the
        full omitted-field path."""
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            detail_module,
            "classify_curve_move_compute",
            return_value=_well_formed_curve_move_output(),
        ) as mock_cm:
            detail_module.regime_detail(
                engine=mock_engine,
                curve_family="UST",
                front_tenor="2Y",
                back_tenor="10Y",
                lookback_period="22d",
                field_name=None,  # mirrors FastAPI's request-parse resolution
            )

        assert mock_cm.call_count == 1
        seen = self._capture_field_name_seen_by_compute(mock_cm)
        assert seen is None, (
            f"None must reach CurveMoveInput unchanged, got {seen!r}. "
            f"Route shadowed the schema's None sentinel with a "
            f"hardcoded default before constructing the input model."
        )

    def test_detail_route_explicit_field_name_passes_through(self):
        """An explicitly-passed ?field_name=PX_LAST reaches
        CurveMoveInput unchanged."""
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            detail_module,
            "classify_curve_move_compute",
            return_value=_well_formed_curve_move_output(),
        ) as mock_cm:
            detail_module.regime_detail(
                engine=mock_engine,
                curve_family="UST",
                front_tenor="2Y",
                back_tenor="10Y",
                lookback_period="22d",
                field_name="PX_LAST",
            )

        assert mock_cm.call_count == 1
        assert self._capture_field_name_seen_by_compute(mock_cm) == "PX_LAST"


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:

    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.sovereign_bonds.tools.curve_move_classifier import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.sovereign_bonds.tools.curve_move_classifier.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_curve_move_classifier_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(CURVE_MOVE_CONFIG_PATH)
        assert cfg.tool.name == "classify_curve_move_tool"
        assert cfg.tool.domain == "sovereign_bonds"
