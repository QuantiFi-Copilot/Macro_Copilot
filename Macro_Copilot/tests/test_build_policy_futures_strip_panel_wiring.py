"""
test_build_policy_futures_strip_panel_wiring.py — Caller-wiring
                                                  smoke tests for the
                                                  build_policy_
                                                  futures_strip_panel
                                                  primitive
                                                  (Plan §5 Group 3 #21)

Layer A offline wiring smoke tests for the new policy-futures strip
Panel substrate primitive.  Caller surface in V1:

  1. rates_agent/policy_futures/mcp_server.py::
       build_policy_futures_strip_panel_tool
  2. rates_agent/policy_futures/tools/schemas/__init__.py
  3. rates_agent/workflows/__init__.py::_PRIMITIVE_SPECS
  4. shared/analytics/panel_assembly.py (the additive
     fetch_policy_futures_strip_panel + fetch_policy_futures_strip_
     universe helpers, AND the byte-identical regression-protection
     of every pre-existing helper signature)

Key load-bearing properties under test:

  - MCP wrapper passes config= explicitly (no auto-load fallback;
    PR14).
  - MCP wrapper accepts comma-separated ``curve_families`` AND
    ``strip_positions`` strings (MCP exposes flat scalars) and splits
    them before constructing the Pydantic input.
  - Empty-string sentinels translate to None → YAML defaults
    (PR9 / PR10) for ``field_name``, ``calendar_policy``,
    ``missing_data_policy``, ``curve_families``, ``strip_positions``,
    ``end_date``.
  - ISO ``start_date`` / ``end_date`` strings are parsed; malformed
    values return the controlled-error envelope.
  - The MCP wrapper drops the typed Panel artifact (``panel``) before
    serialising for the LLM (full per-row payload blows the token
    budget).
  - The MCP wrapper preserves the methodology_card on the LLM-visible
    response.
  - PR14 frozen wire-field names are present on the well-formed
    output (including ``column_keys`` and ``panel_value_field``
    correctly resolved on the methodology card).
  - Schemas hub re-exports the input / output / closed-family Literal
    aliases under the canonical short names.
  - Workflow registration is honest: PrimitiveSpec is registered with
    the correct CONFIG_PATH, ``output_field_units = {"panel":
    "percent"}``, and ``output_artifact_type = "Panel"`` (same shape
    as build_sovereign_yield_panel + build_zcis_panel +
    build_linker_panel).
  - The additive panel_assembly helpers leave the pre-existing
    fetcher signatures byte-identical (regression-protects
    sovereign_yield_panel + build_zcis_panel + build_linker_panel
    AND every policy_futures / bond_futures sibling that reads
    through rates_fetch.py).
  - The canonical-set count in tests/test_workflow_event_study.py's
    TestResolverCompleteness is asserted via known_rates_primitives()
    membership (the test in test_workflow_event_study.py owns the
    strict count drift assertion — this wiring test only verifies
    the new entry IS registered).
"""

from __future__ import annotations

import inspect
import json
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from rates_agent.policy_futures.tools.build_policy_futures_strip_panel import (
    CONFIG_PATH as BUILD_POLICY_FUTURES_STRIP_PANEL_CONFIG_PATH,
    BuildPolicyFuturesStripPanelInput,
    BuildPolicyFuturesStripPanelOutput,
)
from shared.config import ToolConfig, clear_tool_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_output() -> dict:
    """Mock return for ``build_policy_futures_strip_panel`` that
    round-trips through
    ``BuildPolicyFuturesStripPanelOutput.model_validate``.

    The Panel artifact is left at ``None`` (the schema's
    ``Optional[Panel]``) — wiring tests don't exercise the typed
    artifact, the compute tests + SQL validator do.  The MCP-layer
    drop-``panel`` smoke test below double-checks that the wire-side
    behaviour is correct regardless of the artifact's presence.
    """
    return {
        "start_date": "2024-01-02",
        "end_date": "2024-02-28",
        "row_count": 40,
        "column_count": 8,
        "curve_families": ["SOFR_FUT"],
        "strip_positions": [1, 2, 3, 4, 5, 6, 7, 8],
        "column_keys": [
            "SOFR_FUT|1", "SOFR_FUT|2", "SOFR_FUT|3", "SOFR_FUT|4",
            "SOFR_FUT|5", "SOFR_FUT|6", "SOFR_FUT|7", "SOFR_FUT|8",
        ],
        "units_by_column": {
            f"SOFR_FUT|{i}": "percent" for i in range(1, 9)
        },
        "methodology_card": {
            "field_name": "PX_LAST",
            "panel_value_field": "implied_rate_pct",
            "calendar_policy": "business_days",
            "missing_data_policy": "forward_fill_only",
            "ffill_limit_days": 5,
            "ffill_source_tag": "industry_standard_5d_ffill",
            "curve_families": ["SOFR_FUT"],
            "strip_positions": [1, 2, 3, 4, 5, 6, 7, 8],
            "column_axis_encoding": (
                "<CURVE_FAMILY>|<STRIP_POSITION> (flat string)."
            ),
            "column_axis_encoding_separator": "|",
            "inverse_pricing_handling": (
                "implied_rate_pct = 100 - raw_price for inverse-"
                "priced cells."
            ),
            "cross_region_business_days_caveat": (
                "Mon-Fri unioned across three regions is an "
                "approximation."
            ),
            "rolling_generic_strip_caveat": (
                "Rolling-generic strip; NOT a CTD-of-futures-of-"
                "OIS read."
            ),
            "methodology_label": "Policy-futures strip panel test.",
            "curve_family_reference": {
                "SOFR_FUT": {
                    "short_rate_regime": "RFR",
                    "inverse_pricing": True,
                    "cell_count": 8,
                    "cells": [],
                    "regime_caveat": "SOFR_FUT short-rate regime: RFR.",
                }
            },
        },
        "panel": None,
    }


def _assert_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "build_policy_futures_strip_panel called without `config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "build_policy_futures_strip_panel_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'build_policy_futures_strip_panel_tool'"
    )
    assert "default_price_field" in cfg.conventions
    assert "ffill_limit_days" in cfg.conventions
    assert "default_missing_data_policy" in cfg.conventions
    assert "calendar_policy" in cfg.conventions


# ===========================================================================
# 1. MCP wrapper
# ===========================================================================


class TestMcpBuildPolicyFuturesStripPanelWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_policy_futures_strip_panel",
            return_value=_well_formed_output(),
        ) as spy:
            output_json = mcp_module.build_policy_futures_strip_panel_tool(
                start_date="2024-01-02",
                end_date="2024-02-28",
                curve_families="SOFR_FUT",
            )
        assert spy.call_count == 1
        _assert_config_passed(spy.call_args)
        parsed = json.loads(output_json)
        assert "row_count" in parsed
        assert "column_count" in parsed
        assert "column_keys" in parsed
        assert "strip_positions" in parsed
        assert "methodology_card" in parsed
        # The wrapper drops the typed Panel artifact before the JSON.
        assert "panel" not in parsed

    def test_drops_panel_field_even_when_present(self):
        """If compute returns a populated ``panel`` field (the
        production path under the workflow executor's bridge), the
        MCP wrapper still drops it before JSON-encoding for the
        LLM."""
        from rates_agent.policy_futures import mcp_server as mcp_module
        result_with_panel = _well_formed_output()
        result_with_panel["panel"] = {"opaque": "object"}

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_policy_futures_strip_panel",
            return_value=result_with_panel,
        ):
            output_json = mcp_module.build_policy_futures_strip_panel_tool(
                start_date="2024-01-02",
                end_date="2024-02-28",
            )
        parsed = json.loads(output_json)
        assert "panel" not in parsed

    def test_empty_curve_families_string_becomes_none(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_policy_futures_strip_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_policy_futures_strip_panel_tool(
                start_date="2024-01-02",
                curve_families="",
            )
        params = spy.call_args.kwargs["params"]
        assert params.curve_families is None

    def test_csv_curve_families_split_to_list(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_policy_futures_strip_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_policy_futures_strip_panel_tool(
                start_date="2024-01-02",
                curve_families="SOFR_FUT,SONIA_FUT",
            )
        params = spy.call_args.kwargs["params"]
        assert params.curve_families == ["SOFR_FUT", "SONIA_FUT"]

    def test_csv_curve_families_strips_whitespace(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_policy_futures_strip_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_policy_futures_strip_panel_tool(
                start_date="2024-01-02",
                curve_families=" SOFR_FUT , EUR_SHORT_RATE_FUT  ",
            )
        params = spy.call_args.kwargs["params"]
        assert params.curve_families == ["SOFR_FUT", "EUR_SHORT_RATE_FUT"]

    def test_empty_strip_positions_string_becomes_none(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_policy_futures_strip_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_policy_futures_strip_panel_tool(
                start_date="2024-01-02",
                strip_positions="",
            )
        params = spy.call_args.kwargs["params"]
        assert params.strip_positions is None

    def test_csv_strip_positions_split_to_int_list(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_policy_futures_strip_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_policy_futures_strip_panel_tool(
                start_date="2024-01-02",
                strip_positions="1,2,3,4",
            )
        params = spy.call_args.kwargs["params"]
        assert params.strip_positions == [1, 2, 3, 4]

    def test_malformed_strip_positions_returns_controlled_envelope(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        output_json = mcp_module.build_policy_futures_strip_panel_tool(
            start_date="2024-01-02",
            strip_positions="1,abc",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid strip_positions" in parsed["error"]

    def test_empty_field_name_string_becomes_none(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_policy_futures_strip_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_policy_futures_strip_panel_tool(
                start_date="2024-01-02",
                field_name="",
            )
        params = spy.call_args.kwargs["params"]
        assert params.field_name is None

    def test_empty_calendar_policy_string_becomes_none(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_policy_futures_strip_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_policy_futures_strip_panel_tool(
                start_date="2024-01-02",
                calendar_policy="",
            )
        params = spy.call_args.kwargs["params"]
        assert params.calendar_policy is None

    def test_empty_missing_data_policy_string_becomes_none(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_policy_futures_strip_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_policy_futures_strip_panel_tool(
                start_date="2024-01-02",
                missing_data_policy="",
            )
        params = spy.call_args.kwargs["params"]
        assert params.missing_data_policy is None

    def test_end_date_sentinel_becomes_none(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_policy_futures_strip_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_policy_futures_strip_panel_tool(
                start_date="2024-01-02",
                end_date="",
            )
        params = spy.call_args.kwargs["params"]
        assert params.end_date is None

    def test_start_date_iso_parsed(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_policy_futures_strip_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_policy_futures_strip_panel_tool(
                start_date="2024-01-02",
                end_date="2024-02-28",
            )
        params = spy.call_args.kwargs["params"]
        assert params.start_date == date(2024, 1, 2)
        assert params.end_date == date(2024, 2, 28)

    def test_malformed_start_date_returns_controlled_error(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        output_json = mcp_module.build_policy_futures_strip_panel_tool(
            start_date="not-a-date",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid start_date" in parsed["error"]

    def test_malformed_end_date_returns_controlled_error(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        output_json = mcp_module.build_policy_futures_strip_panel_tool(
            start_date="2024-01-02",
            end_date="bad-date",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid end_date" in parsed["error"]

    def test_non_policy_futures_curve_family_returns_controlled_envelope(self):
        """A non-policy-futures curve family in the CSV fails the
        closed-family Literal at Pydantic validation; the wrapper
        surfaces the controlled-error envelope."""
        from rates_agent.policy_futures import mcp_server as mcp_module
        output_json = mcp_module.build_policy_futures_strip_panel_tool(
            start_date="2024-01-02",
            curve_families="SOFR_FUT,UST",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_bond_futures_curve_family_rejected(self):
        """Bond-futures curve_family (UST_FUT) is rejected — use the
        bond_futures domain for those."""
        from rates_agent.policy_futures import mcp_server as mcp_module
        output_json = mcp_module.build_policy_futures_strip_panel_tool(
            start_date="2024-01-02",
            curve_families="UST_FUT",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed

    def test_out_of_range_strip_position_returns_controlled_envelope(self):
        """strip_position=9 is out of V1 range; the closed-family
        Literal rejects it at Pydantic validation."""
        from rates_agent.policy_futures import mcp_server as mcp_module
        output_json = mcp_module.build_policy_futures_strip_panel_tool(
            start_date="2024-01-02",
            strip_positions="1,9",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_mcp_wrapper_has_no_ffill_limit_param(self):
        """PR8 input-schema discipline: ``ffill_limit_days`` is a
        YAML-owned methodology knob.  The MCP wrapper must not
        expose it as a per-query input."""
        from rates_agent.policy_futures import mcp_server as mcp_module
        sig = inspect.signature(
            mcp_module.build_policy_futures_strip_panel_tool,
        )
        assert "ffill_limit_days" not in sig.parameters
        assert "ffill_limit" not in sig.parameters

    def test_mcp_wrapper_has_no_ddof_param(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        sig = inspect.signature(
            mcp_module.build_policy_futures_strip_panel_tool,
        )
        assert "ddof" not in sig.parameters

    def test_mcp_wrapper_has_no_delivery_month_type_param(self):
        """PR8 — the Panel substrate preserves Buba mix raw; no
        ``delivery_month_type`` knob is exposed on this primitive."""
        from rates_agent.policy_futures import mcp_server as mcp_module
        sig = inspect.signature(
            mcp_module.build_policy_futures_strip_panel_tool,
        )
        assert "delivery_month_type" not in sig.parameters

    def test_mcp_wrapper_has_no_inverse_pricing_override_param(self):
        """PR8 — the inverse-pricing conversion is metadata-driven;
        no per-query override is exposed."""
        from rates_agent.policy_futures import mcp_server as mcp_module
        sig = inspect.signature(
            mcp_module.build_policy_futures_strip_panel_tool,
        )
        assert "inverse_pricing_override" not in sig.parameters

    def test_mcp_wrapper_has_all_per_query_knobs(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        sig = inspect.signature(
            mcp_module.build_policy_futures_strip_panel_tool,
        )
        for required in (
            "start_date",
            "end_date",
            "curve_families",
            "strip_positions",
            "field_name",
            "calendar_policy",
            "missing_data_policy",
        ):
            assert required in sig.parameters, (
                f"MCP wrapper missing per-query knob {required!r}"
            )

    def test_mcp_module_tool_registration_visible(self):
        """The MCP server module exposes the new tool as a
        ``build_*_tool``-prefixed attribute, catching accidental
        registration removals."""
        from rates_agent.policy_futures import mcp_server as mcp_module
        assert hasattr(
            mcp_module, "build_policy_futures_strip_panel_tool",
        )


# ===========================================================================
# 2. CONFIG_PATH public symbol
# ===========================================================================


class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.policy_futures.tools.build_policy_futures_strip_panel import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.policy_futures.tools.build_policy_futures_strip_panel.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_build_policy_futures_strip_panel_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(BUILD_POLICY_FUTURES_STRIP_PANEL_CONFIG_PATH)
        assert cfg.tool.name == "build_policy_futures_strip_panel_tool"
        assert cfg.tool.domain == "policy_futures"
        assert cfg.tool.category == "desk_invariant_primitive"


# ===========================================================================
# 3. Response-model validation
# ===========================================================================


class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        validated = BuildPolicyFuturesStripPanelOutput.model_validate(
            _well_formed_output(),
        )
        assert validated.column_count == 8
        assert validated.curve_families == ["SOFR_FUT"]
        assert validated.strip_positions == [1, 2, 3, 4, 5, 6, 7, 8]
        assert validated.column_keys == [
            f"SOFR_FUT|{i}" for i in range(1, 9)
        ]
        assert validated.methodology_card["panel_value_field"] == (
            "implied_rate_pct"
        )

    def test_extra_fields_forbidden(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad["surprise_field"] = 42
        with pytest.raises(ValidationError):
            BuildPolicyFuturesStripPanelOutput.model_validate(bad)


# ===========================================================================
# 4. Schemas hub re-export
# ===========================================================================


class TestSchemasHubReExport:
    def test_input_re_exported(self):
        from rates_agent.policy_futures.tools.schemas import (
            BuildPolicyFuturesStripPanelInput as via_hub,
        )
        from rates_agent.policy_futures.tools.build_policy_futures_strip_panel import (
            BuildPolicyFuturesStripPanelInput as via_direct,
        )
        assert via_hub is via_direct

    def test_output_re_exported(self):
        from rates_agent.policy_futures.tools.schemas import (
            BuildPolicyFuturesStripPanelOutput as via_hub,
        )
        from rates_agent.policy_futures.tools.build_policy_futures_strip_panel import (
            BuildPolicyFuturesStripPanelOutput as via_direct,
        )
        assert via_hub is via_direct

    def test_curve_family_literal_re_exported(self):
        from rates_agent.policy_futures.tools.schemas import (
            PolicyFuturesStripCurveFamily as via_hub,
        )
        from rates_agent.policy_futures.tools.build_policy_futures_strip_panel import (
            PolicyFuturesStripCurveFamily as via_direct,
        )
        assert via_hub is via_direct

    def test_policy_literals_re_exported(self):
        from rates_agent.policy_futures.tools.schemas import (
            PolicyFuturesStripPanelCalendarPolicy,
            PolicyFuturesStripPanelMissingDataPolicy,
            PolicyFuturesStripPosition,
        )
        assert PolicyFuturesStripPanelCalendarPolicy is not None
        assert PolicyFuturesStripPanelMissingDataPolicy is not None
        assert PolicyFuturesStripPosition is not None


# ===========================================================================
# 5. Workflow registration
# ===========================================================================


class TestWorkflowRegistration:
    def test_registered_in_rates_primitive_resolver(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "policy_futures_build_policy_futures_strip_panel_tool",
        )
        assert spec.tool_name == "policy_futures_build_policy_futures_strip_panel_tool"
        assert spec.config_path == (
            BUILD_POLICY_FUTURES_STRIP_PANEL_CONFIG_PATH
        )

    def test_output_field_units_declares_panel_percent(self):
        """``build_policy_futures_strip_panel`` emits a Panel
        artifact under the ``panel`` field; the per-column
        ``units_by_column`` carries the per-cell unit tags.  The
        workflow registration's ``output_field_units`` declares the
        single field as PERCENT (every cell is the
        implied_rate_pct in PERCENT)."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "policy_futures_build_policy_futures_strip_panel_tool",
        )
        assert spec.output_field_units == {"panel": "percent"}

    def test_output_artifact_type_is_panel(self):
        """The executor's bridge dispatches on
        ``output_artifact_type`` — Panel-emitting primitives MUST
        declare ``"Panel"`` so the bridge picks
        ``tool_output_to_artifact_panel`` over the default Series
        bridge."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "policy_futures_build_policy_futures_strip_panel_tool",
        )
        assert spec.output_artifact_type == "Panel"

    def test_registered_input_and_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.policy_futures.tools.build_policy_futures_strip_panel import (
            BuildPolicyFuturesStripPanelInput,
            BuildPolicyFuturesStripPanelOutput,
        )
        spec = rates_primitive_resolver(
            "policy_futures_build_policy_futures_strip_panel_tool",
        )
        assert spec.input_class is BuildPolicyFuturesStripPanelInput
        assert spec.output_class is BuildPolicyFuturesStripPanelOutput

    def test_tool_registered_in_known_primitives(self):
        """The new tool is enumerated by
        ``known_rates_primitives`` so the
        ``test_workflow_event_study.py::TestResolverCompleteness``
        canonical-set check picks it up."""
        from rates_agent.workflows import known_rates_primitives
        assert "policy_futures_build_policy_futures_strip_panel_tool" in (
            set(known_rates_primitives())
        )


# ===========================================================================
# 6. Additive panel_assembly extension — new policy_futures helpers
# ===========================================================================


class TestPanelAssemblyPolicyFuturesStripFetcher:
    """The additive ``fetch_policy_futures_strip_panel`` helper
    must:
      - bind every filter as a parameter (no SQL string-interpolation
        of user input)
      - constrain on instrument_type='policy_future' AND
        is_rolling_contract=TRUE
      - return the wide panel keyed by the flat
        "<CURVE_FAMILY>|<STRIP_POSITION>" encoding
      - return the universe metadata via the sibling
        fetch_policy_futures_strip_universe helper
      - leave the pre-existing helpers
        (``fetch_instrument_panel``,
        ``fetch_inflation_swap_panel_by_vendor_ticker``,
        ``fetch_inflation_swap_universe``,
        ``fetch_linker_panel_by_vendor_ticker``,
        ``fetch_linker_universe``) untouched.
    """

    def _build_mock_engine(self, rows, cols, *, universe_rows=None):
        mock_engine = MagicMock(name="engine")
        mock_conn = MagicMock(name="conn")

        mock_panel_result = MagicMock(name="panel_result")
        mock_panel_result.fetchall.return_value = rows
        mock_panel_result.keys.return_value = cols

        mock_universe_result = MagicMock(name="universe_result")
        mock_universe_result.mappings.return_value.all.return_value = (
            universe_rows or []
        )

        # The fetcher executes two SQL statements per call: the
        # panel pivot SELECT, then the universe helper's SELECT.
        # We return them in order.
        mock_conn.execute.side_effect = [
            mock_panel_result,
            mock_universe_result,
        ]
        mock_engine.connect.return_value.__enter__.return_value = (
            mock_conn
        )
        return mock_engine, mock_conn

    def test_bind_parameters_constrain_instrument_type(self):
        from shared.analytics import panel_assembly
        mock_engine, mock_conn = self._build_mock_engine([], [])
        panel_assembly.fetch_policy_futures_strip_panel(
            engine=mock_engine,
            curve_families=["SOFR_FUT"],
            strip_positions=[1, 2, 3, 4, 5, 6, 7, 8],
            field_name="PX_LAST",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 2, 28),
            ffill_limit_days=5,
        )
        # First call is the panel pivot SELECT — check its binds.
        first_call = mock_conn.execute.call_args_list[0]
        _sql_obj, bind_params = first_call.args
        assert bind_params["instrument_type"] == "policy_future"
        assert bind_params["field_name"] == "PX_LAST"
        assert bind_params["curve_families"] == ["SOFR_FUT"]
        assert bind_params["strip_positions"] == [1, 2, 3, 4, 5, 6, 7, 8]
        assert bind_params["start_date"] == "2024-01-02"
        assert bind_params["end_date"] == "2024-02-28"

    def test_sql_text_filters_instrument_type_and_is_rolling_contract(self):
        from shared.analytics import panel_assembly
        mock_engine, mock_conn = self._build_mock_engine([], [])
        panel_assembly.fetch_policy_futures_strip_panel(
            engine=mock_engine,
            curve_families=["SOFR_FUT"],
            strip_positions=[1],
            field_name="PX_LAST",
            start_date=date(2024, 1, 2),
        )
        # First call is the panel pivot SELECT.
        first_call = mock_conn.execute.call_args_list[0]
        sql_obj, _bind_params = first_call.args
        sql_text = str(sql_obj)
        assert "instrument_type" in sql_text
        assert "strip_position" in sql_text
        # Second call is the universe helper — uses the SCD2
        # is_rolling_contract guard on instrument_master.
        second_call = mock_conn.execute.call_args_list[1]
        sql_obj_u, _bind_params_u = second_call.args
        assert "is_rolling_contract" in str(sql_obj_u)

    def test_empty_curve_families_raises(self):
        from shared.analytics import panel_assembly
        with pytest.raises(ValueError):
            panel_assembly.fetch_policy_futures_strip_panel(
                engine=MagicMock(),
                curve_families=[],
                strip_positions=[1, 2],
                field_name="PX_LAST",
                start_date=date(2024, 1, 2),
            )

    def test_empty_strip_positions_raises(self):
        from shared.analytics import panel_assembly
        with pytest.raises(ValueError):
            panel_assembly.fetch_policy_futures_strip_panel(
                engine=MagicMock(),
                curve_families=["SOFR_FUT"],
                strip_positions=[],
                field_name="PX_LAST",
                start_date=date(2024, 1, 2),
            )

    def test_column_order_follows_cartesian_product(self):
        """The helper sorts columns deterministically — verify the
        order is (caller-supplied curve_family order) ×
        (strip_position ascending).
        """
        from shared.analytics import panel_assembly
        # Synthesise rows out of (curve_family, position) order so
        # we can prove the deterministic sort.
        rows = [
            ("2024-01-02", "SOFR_FUT", 2, 95.5),
            ("2024-01-02", "SOFR_FUT", 1, 96.0),
            ("2024-01-02", "SONIA_FUT", 1, 95.8),
            ("2024-01-02", "SONIA_FUT", 2, 95.3),
        ]
        cols = [
            "trade_date", "curve_family", "strip_position",
            "field_value",
        ]
        # Provide a non-empty universe so the helper returns the
        # universe frame from the SCD2 SELECT.
        universe_rows = [
            {
                "curve_family": "SOFR_FUT",
                "strip_position": 1,
                "vendor_ticker": "SFR1 Comdty",
                "contract_code": "SFR1",
                "country": "US",
                "currency": "USD",
                "inverse_pricing": True,
            },
            {
                "curve_family": "SOFR_FUT",
                "strip_position": 2,
                "vendor_ticker": "SFR2 Comdty",
                "contract_code": "SFR2",
                "country": "US",
                "currency": "USD",
                "inverse_pricing": True,
            },
            {
                "curve_family": "SONIA_FUT",
                "strip_position": 1,
                "vendor_ticker": "SFI1 Comdty",
                "contract_code": "SFI1",
                "country": "UK",
                "currency": "GBP",
                "inverse_pricing": True,
            },
            {
                "curve_family": "SONIA_FUT",
                "strip_position": 2,
                "vendor_ticker": "SFI2 Comdty",
                "contract_code": "SFI2",
                "country": "UK",
                "currency": "GBP",
                "inverse_pricing": True,
            },
        ]
        mock_engine, _mock_conn = self._build_mock_engine(
            rows, cols, universe_rows=universe_rows,
        )
        wide, universe = panel_assembly.fetch_policy_futures_strip_panel(
            engine=mock_engine,
            curve_families=["SOFR_FUT", "SONIA_FUT"],
            strip_positions=[1, 2],
            field_name="PX_LAST",
            start_date=date(2024, 1, 2),
        )
        # Expected: caller-input curve_family order × ascending
        # strip_position.
        assert list(wide.columns) == [
            "SOFR_FUT|1", "SOFR_FUT|2",
            "SONIA_FUT|1", "SONIA_FUT|2",
        ]
        assert len(universe) == 4

    def test_returns_empty_frame_when_no_rows(self):
        from shared.analytics import panel_assembly
        mock_engine, _mock_conn = self._build_mock_engine([], [])
        wide, universe = panel_assembly.fetch_policy_futures_strip_panel(
            engine=mock_engine,
            curve_families=["SOFR_FUT"],
            strip_positions=[1],
            field_name="PX_LAST",
            start_date=date(2024, 1, 2),
        )
        assert wide.empty
        # universe_meta is also empty because the mocked universe
        # SELECT returned no rows.
        assert universe.empty


class TestFetchPolicyFuturesStripUniverse:
    def test_binds_curve_families_and_instrument_type_filter(self):
        from shared.analytics import panel_assembly
        mock_engine = MagicMock(name="engine")
        mock_conn = MagicMock(name="conn")
        mock_result = MagicMock(name="result")
        mock_result.mappings.return_value.all.return_value = []
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__.return_value = (
            mock_conn
        )

        df = panel_assembly.fetch_policy_futures_strip_universe(
            engine=mock_engine,
            curve_families=["SOFR_FUT", "SONIA_FUT"],
            strip_positions=[1, 2, 3, 4, 5, 6, 7, 8],
        )
        assert isinstance(df, pd.DataFrame)
        _sql_obj, bind_params = mock_conn.execute.call_args.args
        assert bind_params == {
            "instrument_type": "policy_future",
            "curve_families": ["SOFR_FUT", "SONIA_FUT"],
            "strip_positions": [1, 2, 3, 4, 5, 6, 7, 8],
        }

    def test_empty_curve_families_raises(self):
        from shared.analytics import panel_assembly
        with pytest.raises(ValueError):
            panel_assembly.fetch_policy_futures_strip_universe(
                engine=MagicMock(),
                curve_families=[],
                strip_positions=[1],
            )

    def test_empty_strip_positions_raises(self):
        from shared.analytics import panel_assembly
        with pytest.raises(ValueError):
            panel_assembly.fetch_policy_futures_strip_universe(
                engine=MagicMock(),
                curve_families=["SOFR_FUT"],
                strip_positions=[],
            )


# ===========================================================================
# 7. Pre-existing helper signatures unchanged
# ===========================================================================


class TestPreExistingHelpersUnchanged:
    """The additive policy_futures helpers must leave every
    pre-existing fetcher signature byte-identical so sovereign_
    yield_panel / build_zcis_panel / build_linker_panel AND every
    sibling policy_futures / bond_futures tool keep working.  This
    is the regression discipline established at commits 32c386f
    (build_zcis_panel) and 95f0e0f (build_linker_panel).
    """

    def test_panel_assembly_fetch_instrument_panel_signature_unchanged(self):
        from shared.analytics import panel_assembly
        sig = inspect.signature(panel_assembly.fetch_instrument_panel)
        params = list(sig.parameters.keys())
        # The V1 contract for the sovereign fetcher:
        # (engine, leg_specs, start_date, end_date=None,
        #  ffill_limit_days=5).
        assert params == [
            "engine", "leg_specs", "start_date", "end_date",
            "ffill_limit_days",
        ]

    def test_panel_assembly_zcis_panel_signature_unchanged(self):
        from shared.analytics import panel_assembly
        sig = inspect.signature(
            panel_assembly.fetch_inflation_swap_panel_by_vendor_ticker,
        )
        params = list(sig.parameters.keys())
        assert params == [
            "engine", "curve_families", "tenors", "field_name",
            "start_date", "end_date", "ffill_limit_days",
        ]

    def test_panel_assembly_zcis_universe_signature_unchanged(self):
        from shared.analytics import panel_assembly
        sig = inspect.signature(
            panel_assembly.fetch_inflation_swap_universe,
        )
        params = list(sig.parameters.keys())
        assert params == ["engine", "curve_families"]

    def test_panel_assembly_linker_panel_signature_unchanged(self):
        from shared.analytics import panel_assembly
        sig = inspect.signature(
            panel_assembly.fetch_linker_panel_by_vendor_ticker,
        )
        params = list(sig.parameters.keys())
        # V1 contract established by build_linker_panel
        # (commit 95f0e0f).
        assert params == [
            "engine", "curve_families", "field_name", "start_date",
            "end_date", "ffill_limit_days",
        ]

    def test_panel_assembly_linker_universe_signature_unchanged(self):
        from shared.analytics import panel_assembly
        sig = inspect.signature(panel_assembly.fetch_linker_universe)
        params = list(sig.parameters.keys())
        assert params == ["engine", "curve_families"]

    def test_rates_fetch_strip_position_signature_unchanged(self):
        from shared.analytics import rates_fetch
        sig = inspect.signature(rates_fetch.fetch_strip_position)
        params = list(sig.parameters.keys())
        assert params == [
            "engine", "curve_family", "strip_position", "field_name",
            "start_date", "end_date",
        ]

    def test_rates_fetch_strip_group_signature_unchanged(self):
        from shared.analytics import rates_fetch
        sig = inspect.signature(rates_fetch.fetch_strip_group)
        params = list(sig.parameters.keys())
        assert params == [
            "engine", "curve_family", "strip_positions", "field_name",
            "start_date",
        ]

    def test_rates_fetch_cross_market_strip_signature_unchanged(self):
        from shared.analytics import rates_fetch
        sig = inspect.signature(rates_fetch.fetch_cross_market_strip)
        params = list(sig.parameters.keys())
        assert params == [
            "engine", "curve_family_1", "curve_family_2",
            "strip_position", "field_name", "start_date",
        ]
