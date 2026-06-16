"""
test_build_linker_panel_wiring.py — Caller-wiring smoke tests for the
                                    build_linker_panel primitive
                                    (Plan §5 Group 3 #20)

Layer A offline wiring smoke tests for the new inflation-linker Panel
substrate primitive.  Caller surface in V1:

  1. rates_agent/inflation_indexed_bonds/mcp_server.py::build_linker_panel_tool
  2. rates_agent/inflation_indexed_bonds/tools/schemas/__init__.py
  3. rates_agent/workflows/__init__.py::_PRIMITIVE_SPECS

Key load-bearing properties under test:

  - MCP wrapper passes config= explicitly (no auto-load fallback;
    PR14).
  - MCP wrapper accepts comma-separated ``curve_families`` strings
    (MCP exposes flat scalars) and splits them before constructing
    the Pydantic input.
  - Empty-string sentinels translate to None → YAML defaults
    (PR9 / PR10) for ``field_name``, ``calendar_policy``,
    ``missing_data_policy``, ``curve_families``, ``end_date``.
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
    build_sovereign_yield_panel + build_zcis_panel).
  - The additive panel_assembly helpers leave the pre-existing
    fetcher signatures unchanged (regression-protects
    sovereign_yield_panel + build_zcis_panel).
"""

from __future__ import annotations

import inspect
import json
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from rates_agent.inflation_indexed_bonds.tools.build_linker_panel import (
    CONFIG_PATH as BUILD_LINKER_PANEL_CONFIG_PATH,
    BuildLinkerPanelInput,
    BuildLinkerPanelOutput,
)
from shared.config import ToolConfig, clear_tool_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_output() -> dict:
    """Mock return for ``build_linker_panel`` that round-trips through
    ``BuildLinkerPanelOutput.model_validate``.

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
        "column_count": 4,
        "curve_families": ["USD_TIPS"],
        "vendor_tickers": [
            "GTII5 Govt",
            "GTII10 Govt",
            "GTII20 Govt",
            "GTII30 Govt",
        ],
        "units_by_column": {
            "GTII5 Govt": "percent",
            "GTII10 Govt": "percent",
            "GTII20 Govt": "percent",
            "GTII30 Govt": "percent",
        },
        "methodology_card": {
            "field_name": "YLD_YTM_MID",
            "calendar_policy": "business_days",
            "missing_data_policy": "forward_fill_only",
            "ffill_limit_days": 5,
            # Read from the config's ffill_limit_days.source — the
            # registered debt tag carried identically across all rates
            # tools for PR13 (not the never-registered
            # ``industry_standard_5d_ffill``; M14).
            "ffill_source_tag": "team_judgment_pending_review",
            "curve_families": ["USD_TIPS"],
            "vendor_ticker_column_key": True,
            "security_name_caveat": (
                "Panel columns are keyed by vendor_ticker; "
                "security_name is universally NULL across the linker "
                "universe."
            ),
            "index_family_caveat": (
                "Linker curves reference different inflation indices "
                "across USD_TIPS / GBP_LINKER / EUR_FR_LINKER / "
                "CAD_RRB (US_CPI_URBAN / UK_RPI / EU_HICP / CAN_CPI)."
            ),
            "market_structure_caveat": (
                "Cross-country linker markets differ in benchmark "
                "availability and issuance size."
            ),
            "cross_region_business_days_caveat": (
                "business_days unioned across four-region universe "
                "is a cross-region approximation."
            ),
            "methodology_label": "Inflation-linker panel test.",
            "curve_family_reference": {
                "USD_TIPS": {
                    "inflation_index_family": "US_CPI_URBAN",
                    "pricing_type": "real_yield",
                    "bond_count": 4,
                    "bonds": [],
                }
            },
        },
        "panel": None,
    }


def _assert_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, "build_linker_panel called without `config=`"
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "build_linker_panel_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'build_linker_panel_tool'"
    )
    assert "default_field_name" in cfg.conventions
    assert "ffill_limit_days" in cfg.conventions
    assert "default_missing_data_policy" in cfg.conventions
    assert "calendar_policy" in cfg.conventions


# ===========================================================================
# 1. MCP wrapper
# ===========================================================================


class TestMcpBuildLinkerPanelWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_linker_panel",
            return_value=_well_formed_output(),
        ) as spy:
            output_json = mcp_module.build_linker_panel_tool(
                start_date="2024-01-02",
                end_date="2024-02-28",
                curve_families="USD_TIPS",
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
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        result_with_panel = _well_formed_output()
        result_with_panel["panel"] = {"opaque": "object"}

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_linker_panel",
            return_value=result_with_panel,
        ):
            output_json = mcp_module.build_linker_panel_tool(
                start_date="2024-01-02",
                end_date="2024-02-28",
            )
        parsed = json.loads(output_json)
        assert "panel" not in parsed

    def test_empty_curve_families_string_becomes_none(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_linker_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_linker_panel_tool(
                start_date="2024-01-02",
                curve_families="",
            )
        params = spy.call_args.kwargs["params"]
        assert params.curve_families is None

    def test_csv_curve_families_split_to_list(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_linker_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_linker_panel_tool(
                start_date="2024-01-02",
                curve_families="USD_TIPS,GBP_LINKER",
            )
        params = spy.call_args.kwargs["params"]
        assert params.curve_families == ["USD_TIPS", "GBP_LINKER"]

    def test_csv_curve_families_strips_whitespace(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_linker_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_linker_panel_tool(
                start_date="2024-01-02",
                curve_families=" USD_TIPS , GBP_LINKER  ",
            )
        params = spy.call_args.kwargs["params"]
        assert params.curve_families == ["USD_TIPS", "GBP_LINKER"]

    def test_empty_field_name_string_becomes_none(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_linker_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_linker_panel_tool(
                start_date="2024-01-02",
                field_name="",
            )
        params = spy.call_args.kwargs["params"]
        assert params.field_name is None

    def test_empty_calendar_policy_string_becomes_none(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_linker_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_linker_panel_tool(
                start_date="2024-01-02",
                calendar_policy="",
            )
        params = spy.call_args.kwargs["params"]
        assert params.calendar_policy is None

    def test_empty_missing_data_policy_string_becomes_none(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_linker_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_linker_panel_tool(
                start_date="2024-01-02",
                missing_data_policy="",
            )
        params = spy.call_args.kwargs["params"]
        assert params.missing_data_policy is None

    def test_end_date_sentinel_becomes_none(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_linker_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_linker_panel_tool(
                start_date="2024-01-02",
                end_date="",
            )
        params = spy.call_args.kwargs["params"]
        assert params.end_date is None

    def test_start_date_iso_parsed(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "build_linker_panel",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.build_linker_panel_tool(
                start_date="2024-01-02",
                end_date="2024-02-28",
            )
        params = spy.call_args.kwargs["params"]
        assert params.start_date == date(2024, 1, 2)
        assert params.end_date == date(2024, 2, 28)

    def test_malformed_start_date_returns_controlled_error(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        output_json = mcp_module.build_linker_panel_tool(
            start_date="not-a-date",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid start_date" in parsed["error"]

    def test_malformed_end_date_returns_controlled_error(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        output_json = mcp_module.build_linker_panel_tool(
            start_date="2024-01-02",
            end_date="bad-date",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid end_date" in parsed["error"]

    def test_non_linker_curve_family_returns_controlled_envelope(self):
        """A non-linker curve family in the CSV fails the closed-family
        Literal at Pydantic validation; the wrapper surfaces the
        controlled-error envelope."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        output_json = mcp_module.build_linker_panel_tool(
            start_date="2024-01-02",
            curve_families="USD_TIPS,UST",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_zcis_curve_family_rejected(self):
        """ZCIS curve_families (USD_ZCIS / EUR_ZCIS / GBP_ZCIS) are
        rejected — use build_zcis_panel for those."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        output_json = mcp_module.build_linker_panel_tool(
            start_date="2024-01-02",
            curve_families="USD_ZCIS",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed

    def test_mcp_wrapper_has_no_ffill_limit_param(self):
        """PR8 input-schema discipline: ``ffill_limit_days`` is a
        YAML-owned methodology knob.  The MCP wrapper must not
        expose it as a per-query input."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        sig = inspect.signature(mcp_module.build_linker_panel_tool)
        assert "ffill_limit_days" not in sig.parameters
        assert "ffill_limit" not in sig.parameters

    def test_mcp_wrapper_has_no_tenors_param(self):
        """PR8 — linkers are specific-maturity bonds, not tenor-
        pillar swaps; no per-query ``tenors`` knob is exposed."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        sig = inspect.signature(mcp_module.build_linker_panel_tool)
        assert "tenors" not in sig.parameters

    def test_mcp_wrapper_has_no_ddof_param(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        sig = inspect.signature(mcp_module.build_linker_panel_tool)
        assert "ddof" not in sig.parameters

    def test_mcp_wrapper_has_all_per_query_knobs(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        sig = inspect.signature(mcp_module.build_linker_panel_tool)
        for required in (
            "start_date",
            "end_date",
            "curve_families",
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
        from rates_agent.inflation_indexed_bonds.tools.build_linker_panel import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_build_linker_panel_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(BUILD_LINKER_PANEL_CONFIG_PATH)
        assert cfg.tool.name == "build_linker_panel_tool"
        assert cfg.tool.domain == "inflation_indexed_bonds"
        assert cfg.tool.category == "desk_invariant_primitive"


# ===========================================================================
# 3. Response-model validation
# ===========================================================================


class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        validated = BuildLinkerPanelOutput.model_validate(
            _well_formed_output(),
        )
        assert validated.column_count == 4
        assert validated.curve_families == ["USD_TIPS"]
        assert validated.methodology_card["field_name"] == "YLD_YTM_MID"

    def test_extra_fields_forbidden(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad["surprise_field"] = 42
        with pytest.raises(ValidationError):
            BuildLinkerPanelOutput.model_validate(bad)


# ===========================================================================
# 4. Schemas hub re-export
# ===========================================================================


class TestSchemasHubReExport:
    def test_input_re_exported(self):
        from rates_agent.inflation_indexed_bonds.tools.schemas import (
            BuildLinkerPanelInput as via_hub,
        )
        from rates_agent.inflation_indexed_bonds.tools.build_linker_panel import (
            BuildLinkerPanelInput as via_direct,
        )
        assert via_hub is via_direct

    def test_output_re_exported(self):
        from rates_agent.inflation_indexed_bonds.tools.schemas import (
            BuildLinkerPanelOutput as via_hub,
        )
        from rates_agent.inflation_indexed_bonds.tools.build_linker_panel import (
            BuildLinkerPanelOutput as via_direct,
        )
        assert via_hub is via_direct

    def test_curve_family_literal_re_exported(self):
        from rates_agent.inflation_indexed_bonds.tools.schemas import (
            LinkerCurveFamily as via_hub,
        )
        from rates_agent.inflation_indexed_bonds.tools.build_linker_panel import (
            LinkerCurveFamily as via_direct,
        )
        assert via_hub is via_direct

    def test_policy_literals_re_exported(self):
        from rates_agent.inflation_indexed_bonds.tools.schemas import (
            LinkerPanelCalendarPolicy,
            LinkerPanelMissingDataPolicy,
        )
        assert LinkerPanelCalendarPolicy is not None
        assert LinkerPanelMissingDataPolicy is not None


# ===========================================================================
# 5. Workflow registration
# ===========================================================================


class TestWorkflowRegistration:
    def test_registered_in_rates_primitive_resolver(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver("build_linker_panel_tool")
        assert spec.tool_name == "build_linker_panel_tool"
        assert spec.config_path == BUILD_LINKER_PANEL_CONFIG_PATH

    def test_output_field_units_declares_panel_percent(self):
        """``build_linker_panel`` emits a Panel artifact under the
        ``panel`` field; the per-column ``units_by_column`` carries
        the per-leg unit tags.  The workflow registration's
        ``output_field_units`` declares the single field as
        PERCENT (every linker column is PERCENT)."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver("build_linker_panel_tool")
        assert spec.output_field_units == {"panel": "percent"}

    def test_output_artifact_type_is_panel(self):
        """The executor's bridge dispatches on
        ``output_artifact_type`` — Panel-emitting primitives MUST
        declare ``"Panel"`` so the bridge picks
        ``tool_output_to_artifact_panel`` over the default Series
        bridge."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver("build_linker_panel_tool")
        assert spec.output_artifact_type == "Panel"

    def test_registered_input_and_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_indexed_bonds.tools.build_linker_panel import (
            BuildLinkerPanelInput,
            BuildLinkerPanelOutput,
        )
        spec = rates_primitive_resolver("build_linker_panel_tool")
        assert spec.input_class is BuildLinkerPanelInput
        assert spec.output_class is BuildLinkerPanelOutput


# ===========================================================================
# 6. Additive panel_assembly extension — new linker helpers
# ===========================================================================


class TestPanelAssemblyLinkerFetcher:
    """The additive ``fetch_linker_panel_by_vendor_ticker``
    helper must:
      - bind every filter as a parameter (no SQL string-interpolation
        of user input)
      - constrain on instrument_type='inflation_linker'
      - sort column order by (curve_family, maturity_date,
        vendor_ticker)
      - leave the pre-existing helpers (``fetch_instrument_panel``,
        ``fetch_inflation_swap_panel_by_vendor_ticker``,
        ``fetch_inflation_swap_universe``) untouched."""

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

    def test_bind_parameters_constrain_instrument_type(self):
        from shared.analytics import panel_assembly
        mock_engine, mock_conn = self._build_mock_engine([], [])
        panel_assembly.fetch_linker_panel_by_vendor_ticker(
            engine=mock_engine,
            curve_families=["USD_TIPS"],
            field_name="YLD_YTM_MID",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 2, 28),
            ffill_limit_days=5,
        )
        _sql_obj, bind_params = mock_conn.execute.call_args.args
        assert bind_params["instrument_type"] == "inflation_linker"
        assert bind_params["field_name"] == "YLD_YTM_MID"
        assert bind_params["curve_families"] == ["USD_TIPS"]
        assert bind_params["start_date"] == "2024-01-02"
        assert bind_params["end_date"] == "2024-02-28"
        # CRITICAL: linkers do NOT carry a pricing_type bound param
        # (no ZCIS-style no-proxy guard at the linker fetcher layer).
        assert "pricing_type" not in bind_params

    def test_sql_text_filters_instrument_type_and_vendor_ticker(self):
        from shared.analytics import panel_assembly
        mock_engine, mock_conn = self._build_mock_engine([], [])
        panel_assembly.fetch_linker_panel_by_vendor_ticker(
            engine=mock_engine,
            curve_families=["USD_TIPS"],
            field_name="YLD_YTM_MID",
            start_date=date(2024, 1, 2),
        )
        sql_obj, _bind_params = mock_conn.execute.call_args.args
        sql_text = str(sql_obj)
        assert "instrument_type" in sql_text
        assert "vendor_ticker" in sql_text
        # Linker fetcher does NOT inject a pricing_type filter.
        assert "pricing_type" not in sql_text

    def test_empty_curve_families_raises(self):
        from shared.analytics import panel_assembly
        with pytest.raises(ValueError):
            panel_assembly.fetch_linker_panel_by_vendor_ticker(
                engine=MagicMock(),
                curve_families=[],
                field_name="YLD_YTM_MID",
                start_date=date(2024, 1, 2),
            )

    def test_column_order_sorted_by_curve_then_maturity(self):
        """The helper sorts columns deterministically — verify the
        sort key is (curve_family, maturity_date, vendor_ticker).
        """
        from shared.analytics import panel_assembly
        # Synthesise rows out of order so we can prove the sort.
        # USD_TIPS comes before GBP_LINKER alphabetically; within
        # USD_TIPS the 5Y (mat 2030) precedes the 10Y (mat 2036).
        rows = [
            ("2024-01-02", "USD_TIPS", "GTII10 Govt", date(2036, 1, 15), 1.2),
            ("2024-01-02", "USD_TIPS", "GTII5 Govt", date(2030, 10, 15), 0.9),
            ("2024-01-02", "GBP_LINKER", "GTGBPII10Y Govt", date(2035, 9, 22), 2.1),
            ("2024-01-02", "GBP_LINKER", "GTGBPII5Y Govt", date(2031, 8, 10), 2.0),
        ]
        cols = [
            "trade_date", "curve_family", "vendor_ticker",
            "maturity_date", "field_value",
        ]
        mock_engine, _mock_conn = self._build_mock_engine(rows, cols)
        wide = panel_assembly.fetch_linker_panel_by_vendor_ticker(
            engine=mock_engine,
            curve_families=["USD_TIPS", "GBP_LINKER"],
            field_name="YLD_YTM_MID",
            start_date=date(2024, 1, 2),
        )
        # Expected order: GBP_LINKER comes before USD_TIPS
        # alphabetically; within each, earliest maturity first.
        assert list(wide.columns) == [
            "GTGBPII5Y Govt",
            "GTGBPII10Y Govt",
            "GTII5 Govt",
            "GTII10 Govt",
        ]

    def test_returns_empty_frame_when_no_rows(self):
        from shared.analytics import panel_assembly
        mock_engine, _mock_conn = self._build_mock_engine([], [])
        wide = panel_assembly.fetch_linker_panel_by_vendor_ticker(
            engine=mock_engine,
            curve_families=["USD_TIPS"],
            field_name="YLD_YTM_MID",
            start_date=date(2024, 1, 2),
        )
        assert wide.empty


class TestFetchLinkerUniverse:
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

        df = panel_assembly.fetch_linker_universe(
            engine=mock_engine,
            curve_families=["USD_TIPS", "GBP_LINKER"],
        )
        assert isinstance(df, pd.DataFrame)
        _sql_obj, bind_params = mock_conn.execute.call_args.args
        assert bind_params == {
            "instrument_type": "inflation_linker",
            "curve_families": ["USD_TIPS", "GBP_LINKER"],
        }
        # Linker universe helper does NOT carry a pricing_type
        # bind param (unlike fetch_inflation_swap_universe).
        assert "pricing_type" not in bind_params

    def test_empty_curve_families_raises(self):
        from shared.analytics import panel_assembly
        with pytest.raises(ValueError):
            panel_assembly.fetch_linker_universe(
                engine=MagicMock(),
                curve_families=[],
            )


# ===========================================================================
# 7. Pre-existing panel_assembly helpers — signature regression
# ===========================================================================


class TestPreExistingHelpersUnchanged:
    """The additive linker helpers must leave the pre-existing
    fetcher signatures byte-identical so sovereign_yield_panel +
    build_zcis_panel keep working.  This is the discipline that
    the build_zcis_panel review established at commit 32c386f.
    """

    def test_sovereign_fetch_instrument_panel_signature_unchanged(self):
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

    def test_zcis_fetch_inflation_swap_panel_signature_unchanged(self):
        from shared.analytics import panel_assembly
        sig = inspect.signature(
            panel_assembly.fetch_inflation_swap_panel_by_vendor_ticker,
        )
        params = list(sig.parameters.keys())
        # The V1 contract established by build_zcis_panel
        # (commit 32c386f): all kwargs after engine.
        assert params == [
            "engine", "curve_families", "tenors", "field_name",
            "start_date", "end_date", "ffill_limit_days",
        ]

    def test_zcis_fetch_inflation_swap_universe_signature_unchanged(self):
        from shared.analytics import panel_assembly
        sig = inspect.signature(panel_assembly.fetch_inflation_swap_universe)
        params = list(sig.parameters.keys())
        # The V1 contract established by build_zcis_panel:
        # (engine, *, curve_families).
        assert params == ["engine", "curve_families"]
