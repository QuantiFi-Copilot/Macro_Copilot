"""
test_build_zcis_panel_wiring.py — Caller-wiring smoke tests for the
                                  build_zcis_panel primitive
                                  (Plan §5 Group 3 #19)

Layer A offline wiring smoke tests for the new ZCIS Panel substrate
primitive.  Caller surface in V1:

  1. rates_agent/inflation_swaps/mcp_server.py::build_zcis_panel_tool
  2. rates_agent/inflation_swaps/tools/schemas/__init__.py
  3. rates_agent/workflows/__init__.py::_PRIMITIVE_SPECS

Key load-bearing properties under test:

  - MCP wrapper passes config= explicitly (no auto-load fallback;
    PR14).
  - MCP wrapper accepts comma-separated ``curve_families`` / ``tenors``
    strings (MCP exposes flat scalars) and splits them before
    constructing the Pydantic input.
  - Empty-string sentinels translate to None → YAML defaults
    (PR9 / PR10) for ``field_name``, ``calendar_policy``,
    ``missing_data_policy``, ``curve_families``, ``tenors``,
    ``end_date``.
  - ISO ``start_date`` / ``end_date`` strings are parsed; malformed
    values return the controlled-error envelope.
  - The MCP wrapper drops the typed Panel artifact (``panel``) before
    serialising for the LLM (full per-row payload blows the token
    budget).
  - The MCP wrapper preserves the methodology_card on the LLM-visible
    response.
  - Schemas hub re-exports the input / output / closed-family Literal
    aliases under the canonical short names.
  - Workflow registration is honest: PrimitiveSpec is registered with
    the correct CONFIG_PATH, ``output_field_units = {"panel": "percent"}``,
    and ``output_artifact_type = "Panel"`` (same shape as
    build_sovereign_yield_panel).
"""

from __future__ import annotations

import inspect
import json
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from rates_agent.inflation_swaps.tools.build_zcis_panel import (
    CONFIG_PATH as BUILD_ZCIS_PANEL_CONFIG_PATH,
    BuildZcisPanelInput,
    BuildZcisPanelOutput,
)
from shared.config import ToolConfig, clear_tool_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_output() -> dict:
    """Mock return for ``build_zcis_panel`` that round-trips through
    ``BuildZcisPanelOutput.model_validate``.

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
        "column_count": 3,
        "curve_families": ["USD_ZCIS"],
        "tenors": ["1Y", "5Y", "10Y"],
        "vendor_tickers": [
            "USSWIT1 Curncy",
            "USSWIT5 Curncy",
            "USSWIT10 Curncy",
        ],
        "units_by_column": {
            "USSWIT1 Curncy": "percent",
            "USSWIT5 Curncy": "percent",
            "USSWIT10 Curncy": "percent",
        },
        "methodology_card": {
            "field_name": "PX_MID",
            "calendar_policy": "business_days",
            "missing_data_policy": "forward_fill_only",
            "ffill_limit_days": 5,
            "ffill_source_tag": "team_judgment_pending_review",
            "curve_families": ["USD_ZCIS"],
            "tenors": ["1Y", "5Y", "10Y"],
            "index_family_caveat": (
                "ZCIS curves reference different inflation indices "
                "across USD_ZCIS / EUR_ZCIS / GBP_ZCIS."
            ),
            "security_name_caveat": (
                "Panel columns are keyed by vendor_ticker; "
                "security_name is universally NULL across the ZCIS "
                "universe."
            ),
            "vendor_ticker_column_key": True,
            "methodology_label": "ZCIS panel test.",
            "curve_family_reference": {
                "USD_ZCIS": {
                    "inflation_index_family": "US_CPI_URBAN",
                    "index_lag": "3M",
                    "interpolation": "Daily",
                    "underlying_index": "CPURNSA Index",
                }
            },
        },
        "panel": None,
    }


def _assert_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, "build_zcis_panel called without `config=`"
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "build_zcis_panel_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'build_zcis_panel_tool'"
    )
    assert "default_zcis_rate_field" in cfg.conventions
    assert "ffill_limit_days" in cfg.conventions
    assert "default_missing_data_policy" in cfg.conventions
    assert "calendar_policy" in cfg.conventions


# ===========================================================================
# 1. MCP wrapper
# ===========================================================================


class TestMcpBuildZcisPanelWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_zcis_panel",
            return_value=_well_formed_output(),
        ) as spy:
            output_json = mcp_module.build_zcis_panel_tool(
                start_date="2024-01-02",
                end_date="2024-02-28",
                curve_families="USD_ZCIS",
                tenors="1Y,5Y,10Y",
            )
        assert spy.call_count == 1
        _assert_config_passed(spy.call_args)
        parsed = json.loads(output_json)
        assert "row_count" in parsed
        assert "methodology_card" in parsed
        # The wrapper drops the typed Panel artifact before the JSON.
        assert "panel" not in parsed

    def test_drops_panel_field_even_when_present(self):
        """If compute returns a populated ``panel`` field (the
        production path under the workflow executor's bridge), the
        MCP wrapper still drops it before JSON-encoding for the
        LLM."""
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        result_with_panel = _well_formed_output()
        result_with_panel["panel"] = {"opaque": "object"}

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_zcis_panel",
            return_value=result_with_panel,
        ):
            output_json = mcp_module.build_zcis_panel_tool(
                start_date="2024-01-02",
                end_date="2024-02-28",
            )
        parsed = json.loads(output_json)
        assert "panel" not in parsed

    def test_empty_curve_families_string_becomes_none(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_zcis_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_zcis_panel_tool(
                start_date="2024-01-02",
                curve_families="",
            )
        params = spy.call_args.kwargs["params"]
        assert params.curve_families is None

    def test_csv_curve_families_split_to_list(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_zcis_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_zcis_panel_tool(
                start_date="2024-01-02",
                curve_families="USD_ZCIS,EUR_ZCIS",
            )
        params = spy.call_args.kwargs["params"]
        assert params.curve_families == ["USD_ZCIS", "EUR_ZCIS"]

    def test_csv_curve_families_strips_whitespace(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_zcis_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_zcis_panel_tool(
                start_date="2024-01-02",
                curve_families=" USD_ZCIS , EUR_ZCIS  ",
            )
        params = spy.call_args.kwargs["params"]
        assert params.curve_families == ["USD_ZCIS", "EUR_ZCIS"]

    def test_csv_tenors_split_to_list(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_zcis_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_zcis_panel_tool(
                start_date="2024-01-02",
                tenors="1Y, 5Y, 10Y",
            )
        params = spy.call_args.kwargs["params"]
        assert params.tenors == ["1Y", "5Y", "10Y"]

    def test_empty_tenors_string_becomes_none(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_zcis_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_zcis_panel_tool(
                start_date="2024-01-02",
                tenors="",
            )
        params = spy.call_args.kwargs["params"]
        assert params.tenors is None

    def test_empty_field_name_string_becomes_none(self):
        """Empty-string sentinel preserves YAML default (PR9 / PR10)."""
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_zcis_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_zcis_panel_tool(
                start_date="2024-01-02",
                field_name="",
            )
        params = spy.call_args.kwargs["params"]
        assert params.field_name is None

    def test_empty_calendar_policy_string_becomes_none(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_zcis_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_zcis_panel_tool(
                start_date="2024-01-02",
                calendar_policy="",
            )
        params = spy.call_args.kwargs["params"]
        assert params.calendar_policy is None

    def test_empty_missing_data_policy_string_becomes_none(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_zcis_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_zcis_panel_tool(
                start_date="2024-01-02",
                missing_data_policy="",
            )
        params = spy.call_args.kwargs["params"]
        assert params.missing_data_policy is None

    def test_end_date_sentinel_becomes_none(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_zcis_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_zcis_panel_tool(
                start_date="2024-01-02",
                end_date="",
            )
        params = spy.call_args.kwargs["params"]
        assert params.end_date is None

    def test_start_date_iso_parsed(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_zcis_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_zcis_panel_tool(
                start_date="2024-01-02",
                end_date="2024-02-28",
            )
        params = spy.call_args.kwargs["params"]
        assert params.start_date == date(2024, 1, 2)
        assert params.end_date == date(2024, 2, 28)

    def test_malformed_start_date_returns_controlled_error(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        output_json = mcp_module.build_zcis_panel_tool(
            start_date="not-a-date",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid start_date" in parsed["error"]

    def test_malformed_end_date_returns_controlled_error(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        output_json = mcp_module.build_zcis_panel_tool(
            start_date="2024-01-02",
            end_date="bad-date",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid end_date" in parsed["error"]

    def test_non_zcis_curve_family_returns_controlled_envelope(self):
        """A non-ZCIS curve family in the CSV fails the closed-family
        Literal at Pydantic validation; the wrapper surfaces the
        controlled-error envelope."""
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        output_json = mcp_module.build_zcis_panel_tool(
            start_date="2024-01-02",
            curve_families="USD_ZCIS,USD_TIPS",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_mcp_wrapper_has_no_ffill_limit_param(self):
        """PR8 input-schema discipline: ``ffill_limit_days`` is a
        YAML-owned methodology knob.  The MCP wrapper must not
        expose it as a per-query input."""
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        sig = inspect.signature(mcp_module.build_zcis_panel_tool)
        assert "ffill_limit_days" not in sig.parameters
        assert "ffill_limit" not in sig.parameters

    def test_mcp_wrapper_has_no_ddof_param(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        sig = inspect.signature(mcp_module.build_zcis_panel_tool)
        assert "ddof" not in sig.parameters

    def test_mcp_wrapper_has_all_per_query_knobs(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        sig = inspect.signature(mcp_module.build_zcis_panel_tool)
        for required in (
            "start_date",
            "end_date",
            "curve_families",
            "tenors",
            "field_name",
            "calendar_policy",
            "missing_data_policy",
        ):
            assert required in sig.parameters, (
                f"MCP wrapper missing per-query knob {required!r}"
            )


# ===========================================================================
# 2. CONFIG_PATH public symbol
# ===========================================================================


class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.inflation_swaps.tools.build_zcis_panel import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.inflation_swaps.tools.build_zcis_panel.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_build_zcis_panel_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(BUILD_ZCIS_PANEL_CONFIG_PATH)
        assert cfg.tool.name == "build_zcis_panel_tool"
        assert cfg.tool.domain == "inflation_swaps"
        assert cfg.tool.category == "desk_invariant_primitive"


# ===========================================================================
# 3. Response-model validation
# ===========================================================================


class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        validated = BuildZcisPanelOutput.model_validate(
            _well_formed_output(),
        )
        assert validated.column_count == 3
        assert validated.curve_families == ["USD_ZCIS"]
        assert validated.methodology_card["field_name"] == "PX_MID"

    def test_extra_fields_forbidden(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad["surprise_field"] = 42
        with pytest.raises(ValidationError):
            BuildZcisPanelOutput.model_validate(bad)


# ===========================================================================
# 4. Schemas hub re-export
# ===========================================================================


class TestSchemasHubReExport:
    def test_input_re_exported(self):
        from rates_agent.inflation_swaps.tools.schemas import (
            BuildZcisPanelInput as via_hub,
        )
        from rates_agent.inflation_swaps.tools.build_zcis_panel import (
            BuildZcisPanelInput as via_direct,
        )
        assert via_hub is via_direct

    def test_output_re_exported(self):
        from rates_agent.inflation_swaps.tools.schemas import (
            BuildZcisPanelOutput as via_hub,
        )
        from rates_agent.inflation_swaps.tools.build_zcis_panel import (
            BuildZcisPanelOutput as via_direct,
        )
        assert via_hub is via_direct

    def test_curve_family_literal_re_exported(self):
        from rates_agent.inflation_swaps.tools.schemas import (
            ZcisCurveFamily as via_hub,
        )
        from rates_agent.inflation_swaps.tools.build_zcis_panel import (
            ZcisCurveFamily as via_direct,
        )
        assert via_hub is via_direct

    def test_policy_literals_re_exported(self):
        from rates_agent.inflation_swaps.tools.schemas import (
            ZcisPanelCalendarPolicy,
            ZcisPanelMissingDataPolicy,
        )
        assert ZcisPanelCalendarPolicy is not None
        assert ZcisPanelMissingDataPolicy is not None


# ===========================================================================
# 5. Workflow registration
# ===========================================================================


class TestWorkflowRegistration:
    def test_registered_in_rates_primitive_resolver(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver("build_zcis_panel_tool")
        assert spec.tool_name == "build_zcis_panel_tool"
        assert spec.config_path == BUILD_ZCIS_PANEL_CONFIG_PATH

    def test_output_field_units_declares_panel_percent(self):
        """``build_zcis_panel`` emits a Panel artifact under the
        ``panel`` field; the per-column ``units_by_column`` carries
        the per-leg unit tags.  The workflow registration's
        ``output_field_units`` declares the single field as
        PERCENT (every ZCIS column is PERCENT)."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver("build_zcis_panel_tool")
        assert spec.output_field_units == {"panel": "percent"}

    def test_output_artifact_type_is_panel(self):
        """The executor's bridge dispatches on
        ``output_artifact_type`` — Panel-emitting primitives MUST
        declare ``"Panel"`` so the bridge picks
        ``tool_output_to_artifact_panel`` over the default Series
        bridge."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver("build_zcis_panel_tool")
        assert spec.output_artifact_type == "Panel"

    def test_registered_input_and_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_swaps.tools.build_zcis_panel import (
            BuildZcisPanelInput,
            BuildZcisPanelOutput,
        )
        spec = rates_primitive_resolver("build_zcis_panel_tool")
        assert spec.input_class is BuildZcisPanelInput
        assert spec.output_class is BuildZcisPanelOutput


# ===========================================================================
# 6. Sibling fetcher (Layer A unit smoke — additive panel_assembly extension)
# ===========================================================================


class TestPanelAssemblySiblingFetcher:
    """The additive ``fetch_inflation_swap_panel_by_vendor_ticker``
    helper must:
      - bind every filter as a parameter (no SQL string-interpolation
        of user input)
      - constrain on instrument_type + pricing_type (no-proxy guard)
      - sort column order by (curve_family, tenor_year_fraction,
        vendor_ticker)
      - leave ``fetch_instrument_panel`` (the sovereign caller's
        backend) untouched."""

    def _build_mock_engine(self, rows, cols):
        mock_engine = MagicMock(name="engine")
        mock_conn = MagicMock(name="conn")
        mock_result = MagicMock(name="result")
        mock_result.fetchall.return_value = rows
        mock_result.keys.return_value = cols
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__.return_value = (
            mock_conn
        )
        return mock_engine, mock_conn

    def test_bind_parameters_constrain_instrument_type_and_pricing_type(self):
        from shared.analytics import panel_assembly
        mock_engine, mock_conn = self._build_mock_engine([], [])
        panel_assembly.fetch_inflation_swap_panel_by_vendor_ticker(
            engine=mock_engine,
            curve_families=["USD_ZCIS"],
            tenors=["1Y", "5Y"],
            field_name="PX_MID",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 2, 28),
            ffill_limit_days=5,
        )
        _sql_obj, bind_params = mock_conn.execute.call_args.args
        assert bind_params["instrument_type"] == "inflation_swap"
        assert bind_params["pricing_type"] == "zero_coupon_breakeven"
        assert bind_params["field_name"] == "PX_MID"
        assert bind_params["curve_families"] == ["USD_ZCIS"]
        assert bind_params["tenors"] == ["1Y", "5Y"]
        assert bind_params["start_date"] == "2024-01-02"
        assert bind_params["end_date"] == "2024-02-28"

    def test_sql_text_filters_pricing_type_and_instrument_type(self):
        from shared.analytics import panel_assembly
        mock_engine, mock_conn = self._build_mock_engine([], [])
        panel_assembly.fetch_inflation_swap_panel_by_vendor_ticker(
            engine=mock_engine,
            curve_families=["USD_ZCIS"],
            tenors=None,
            field_name="PX_MID",
            start_date=date(2024, 1, 2),
        )
        sql_obj, _bind_params = mock_conn.execute.call_args.args
        sql_text = str(sql_obj)
        assert "instrument_type" in sql_text
        assert "pricing_type" in sql_text
        assert "vendor_ticker" in sql_text

    def test_empty_curve_families_raises(self):
        from shared.analytics import panel_assembly
        with pytest.raises(ValueError):
            panel_assembly.fetch_inflation_swap_panel_by_vendor_ticker(
                engine=MagicMock(),
                curve_families=[],
                tenors=None,
                field_name="PX_MID",
                start_date=date(2024, 1, 2),
            )

    def test_column_order_sorted_by_curve_then_tenor_year(self):
        """The helper sorts columns deterministically — verify the
        sort key is (curve_family, tenor_year_fraction, vendor_ticker).
        """
        from shared.analytics import panel_assembly
        # Synthesise rows out of order so we can prove the sort.
        rows = [
            ("2024-01-02", "USD_ZCIS", "10Y", "USSWIT10 Curncy", 1.2),
            ("2024-01-02", "USD_ZCIS", "1Y", "USSWIT1 Curncy", 0.9),
            ("2024-01-02", "USD_ZCIS", "5Y", "USSWIT5 Curncy", 1.1),
            ("2024-01-02", "EUR_ZCIS", "10Y", "EUSWI10 Curncy", 2.1),
            ("2024-01-02", "EUR_ZCIS", "5Y", "EUSWI5 Curncy", 2.0),
        ]
        cols = [
            "trade_date", "curve_family", "tenor", "vendor_ticker",
            "field_value",
        ]
        mock_engine, _mock_conn = self._build_mock_engine(rows, cols)
        wide = panel_assembly.fetch_inflation_swap_panel_by_vendor_ticker(
            engine=mock_engine,
            curve_families=["USD_ZCIS", "EUR_ZCIS"],
            tenors=None,
            field_name="PX_MID",
            start_date=date(2024, 1, 2),
        )
        assert list(wide.columns) == [
            "EUSWI5 Curncy",
            "EUSWI10 Curncy",
            "USSWIT1 Curncy",
            "USSWIT5 Curncy",
            "USSWIT10 Curncy",
        ]

    def test_returns_empty_frame_when_no_rows(self):
        from shared.analytics import panel_assembly
        mock_engine, _mock_conn = self._build_mock_engine([], [])
        wide = panel_assembly.fetch_inflation_swap_panel_by_vendor_ticker(
            engine=mock_engine,
            curve_families=["USD_ZCIS"],
            tenors=None,
            field_name="PX_MID",
            start_date=date(2024, 1, 2),
        )
        assert wide.empty

    def test_sovereign_fetch_instrument_panel_signature_unchanged(self):
        """The additive sibling helper must NOT alter the existing
        sovereign fetcher's signature — sovereign_yield_panel
        regression sweep depends on this."""
        from shared.analytics import panel_assembly
        sig = inspect.signature(panel_assembly.fetch_instrument_panel)
        # The signature this test enforces is the V1 contract:
        # (engine, leg_specs, start_date, end_date=None,
        #  ffill_limit_days=5).
        params = list(sig.parameters.keys())
        assert params == [
            "engine", "leg_specs", "start_date", "end_date",
            "ffill_limit_days",
        ]


# ===========================================================================
# 7. Universe helper (additive)
# ===========================================================================


class TestFetchInflationSwapUniverse:
    def test_binds_curve_families_and_no_proxy_filters(self):
        from shared.analytics import panel_assembly
        mock_engine = MagicMock(name="engine")
        mock_conn = MagicMock(name="conn")
        mock_result = MagicMock(name="result")
        mock_result.mappings.return_value.all.return_value = []
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__.return_value = (
            mock_conn
        )

        df = panel_assembly.fetch_inflation_swap_universe(
            engine=mock_engine,
            curve_families=["USD_ZCIS", "GBP_ZCIS"],
        )
        assert isinstance(df, pd.DataFrame)
        _sql_obj, bind_params = mock_conn.execute.call_args.args
        assert bind_params == {
            "instrument_type": "inflation_swap",
            "pricing_type": "zero_coupon_breakeven",
            "curve_families": ["USD_ZCIS", "GBP_ZCIS"],
        }

    def test_empty_curve_families_raises(self):
        from shared.analytics import panel_assembly
        with pytest.raises(ValueError):
            panel_assembly.fetch_inflation_swap_universe(
                engine=MagicMock(),
                curve_families=[],
            )
