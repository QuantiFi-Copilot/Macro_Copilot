"""
test_scan_inflation_linkers_extremes_wiring.py — Caller-wiring smoke
                                                  tests for the linker
                                                  universe-wide
                                                  extremes scan

Layer A offline wiring smoke tests for the new linker scanner.
Mirrors test_scan_bond_futures_extremes_wiring.py.  Caller surface
in V1:

  1. rates_agent/inflation_indexed_bonds/mcp_server.py::
     get_scan_inflation_linkers_extremes_tool

Key load-bearing properties under test:

  - MCP wrapper passes config= explicitly (no auto-load fallback;
    PR14).
  - MCP wrapper accepts a comma-separated ``curve_families`` string
    (MCP exposes flat scalars) and splits it before constructing
    the Pydantic input — mirrors the bond_futures scanner
    convention.
  - The MCP wrapper does NOT accept ``field_name`` / ``metrics`` /
    ``lookback_days`` parameters (input-schema overreach guard).
  - Empty ``curve_families`` string translates to None → YAML's
    full-universe whitelist (default behaviour).
  - ``top_n=0`` / ``min_abs_z_score=-1.0`` sentinels translate to
    None → YAML defaults (PR9 / PR10).
  - ISO ``as_of_date`` string is parsed; malformed values return
    the controlled-error envelope.
  - The MCP wrapper preserves methodology_disclosure on the
    response AND on every result row (catalog guardrail).
  - Schemas hub re-exports the input / output / row classes under
    the canonical short names.
  - Workflow registration is honest: PrimitiveSpec is registered
    with the correct CONFIG_PATH and with
    ``output_field_units = {}`` (snapshot primitive; same exempt
    pattern as the bond_futures scanner).
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.inflation_indexed_bonds.tools.scan_inflation_linkers_extremes import (
    CONFIG_PATH as SCAN_INFLATION_LINKERS_EXTREMES_CONFIG_PATH,
    ScanInflationLinkersExtremesOutput,
)
from shared.config import ToolConfig, clear_tool_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_output() -> dict:
    """Mock matches the live wire shape — MUST validate against
    ``ScanInflationLinkersExtremesOutput``."""
    methodology = (
        "Universe-wide linker real-yield LEVEL scan. Z-score lookback "
        "= 252 trading days. This is a REAL-YIELD level read, NOT a "
        "breakeven scan, NOT a nominal-yield scan, NOT an "
        "inflation-compensation scan. INDEX-FAMILY MISMATCH "
        "(load-bearing): different countries' linkers reference "
        "different inflation indices — USD TIPS reference CPI-U "
        "non-seasonally adjusted, EUR-area linkers reference HICP "
        "ex-tobacco, UK linkers reference RPI (legacy) or CPIH "
        "(newer issues), Canadian RRBs reference Canada CPI. The "
        "ranking spans this heterogeneity. MARKET-STRUCTURE "
        "MISMATCH (load-bearing): linker markets differ in "
        "benchmark availability at the same tenor pillar, issuance "
        "size, liquidity premium, and deflation-floor treatment; "
        "an extreme may reflect liquidity / structural moves "
        "rather than pure real-rate divergence. This is a morning "
        "screen, NOT a tactical trade signal — use the sibling "
        "per-bond / curve-shape / cross-country / breakeven "
        "primitives for further decomposition."
    )
    return {
        "scan_summary": (
            "Scanned 24 linker stems (23 scoreable). Stems with "
            "|z| >= 1.5: 5. Showing top 3 by absolute real-yield "
            "z-score. as_of 2026-04-08."
        ),
        "results": [
            {
                "rank": 1,
                "curve_family": "GBP_LINKER",
                "tenor": "5Y",
                "as_of_date": "2026-04-08",
                "real_yield_pct": 0.483,
                "daily_change_bps": -2.6,
                "monthly_change_bps": -7.5,
                "z_score_real_yield": -2.455,
                "signal": "EXTREME_LOW",
                "maturity_date": "2031-08-10",
                "country": "UK",
                "vendor_ticker": "GTGBPII5Y Govt",
                "methodology_disclosure": methodology,
            },
            {
                "rank": 2,
                "curve_family": "USD_TIPS",
                "tenor": "30Y",
                "as_of_date": "2026-04-08",
                "real_yield_pct": 2.612,
                "daily_change_bps": 1.0,
                "monthly_change_bps": -5.0,
                "z_score_real_yield": 2.0,
                "signal": "EXTREME_HIGH",
                "maturity_date": "2056-02-15",
                "country": "US",
                "vendor_ticker": "GTII30 Govt",
                "methodology_disclosure": methodology,
            },
        ],
        "methodology_disclosure": methodology,
    }


def _assert_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_scan_inflation_linkers_extremes called without "
        "`config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "scan_inflation_linkers_extremes_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'scan_inflation_linkers_extremes_tool'"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_field_name" in cfg.conventions
    assert "inflation_linker_curve_families" in cfg.conventions


# ===========================================================================
# 1. MCP wrapper
# ===========================================================================

class TestMcpScanInflationLinkersExtremesWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_inflation_linkers_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            output_json = mcp_module.get_scan_inflation_linkers_extremes_tool(
                curve_families="",
                top_n=5,
                min_abs_z_score=1.5,
                as_of_date="",
            )
        assert spy.call_count == 1
        _assert_config_passed(spy.call_args)
        parsed = json.loads(output_json)
        assert "scan_summary" in parsed
        assert "results" in parsed
        md = parsed.get("methodology_disclosure", "")
        assert "252" in md, (
            "MCP wrapper must propagate the z-score lookback "
            "verbatim (catalog methodology guardrail)"
        )
        # Real-yield scope must propagate to the LLM-visible payload.
        assert "real-yield" in md.lower() or "real yield" in md.lower()
        # Per-row disclosure must also be present (catalog wording).
        for row in parsed["results"]:
            assert "methodology_disclosure" in row
            assert "252" in row["methodology_disclosure"]

    def test_empty_curve_families_string_becomes_none(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_inflation_linkers_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_inflation_linkers_extremes_tool(
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
            "calculate_scan_inflation_linkers_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_inflation_linkers_extremes_tool(
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
            "calculate_scan_inflation_linkers_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_inflation_linkers_extremes_tool(
                curve_families=" USD_TIPS , GBP_LINKER  ",
            )
        params = spy.call_args.kwargs["params"]
        assert params.curve_families == ["USD_TIPS", "GBP_LINKER"]

    def test_mcp_wrapper_has_no_field_name_param(self):
        """The MCP wrapper MUST NOT accept ``field_name`` — the field
        mnemonic is YAML-owned. Exposing it as an LLM input would be
        input-schema overreach (PR8 / OPR8)."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        sig = inspect.signature(
            mcp_module.get_scan_inflation_linkers_extremes_tool
        )
        assert "field_name" not in sig.parameters

    def test_mcp_wrapper_has_no_lookback_days_param(self):
        """PR8 input-schema discipline: ``lookback_days`` controls
        the fetch window — that is methodology, not a per-query
        knob."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        sig = inspect.signature(
            mcp_module.get_scan_inflation_linkers_extremes_tool
        )
        assert "lookback_days" not in sig.parameters

    def test_mcp_wrapper_has_no_metrics_param(self):
        """The single metric (real-yield LEVEL z-score) IS the
        concept; exposing ``metrics`` as a per-query knob would be
        scope creep past the catalog wording."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        sig = inspect.signature(
            mcp_module.get_scan_inflation_linkers_extremes_tool
        )
        assert "metrics" not in sig.parameters

    def test_mcp_wrapper_has_as_of_date_param(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        sig = inspect.signature(
            mcp_module.get_scan_inflation_linkers_extremes_tool
        )
        assert "as_of_date" in sig.parameters
        # Default must be the empty-string sentinel.
        assert sig.parameters["as_of_date"].default == ""

    def test_top_n_sentinel_omitted_becomes_none(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_inflation_linkers_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_inflation_linkers_extremes_tool()
        params = spy.call_args.kwargs["params"]
        assert params.top_n is None
        assert params.min_abs_z_score is None
        assert params.as_of_date is None

    def test_top_n_explicit_value_passes_through(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_inflation_linkers_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_inflation_linkers_extremes_tool(top_n=10)
        params = spy.call_args.kwargs["params"]
        assert params.top_n == 10

    def test_min_abs_z_score_explicit_value_passes_through(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_inflation_linkers_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_inflation_linkers_extremes_tool(
                min_abs_z_score=0.5,
            )
        params = spy.call_args.kwargs["params"]
        assert params.min_abs_z_score == 0.5

    def test_as_of_date_iso_string_parsed_to_date(self):
        from datetime import date as _date
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_inflation_linkers_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_inflation_linkers_extremes_tool(
                as_of_date="2026-04-08",
            )
        params = spy.call_args.kwargs["params"]
        assert params.as_of_date == _date(2026, 4, 8)

    def test_as_of_date_malformed_returns_controlled_error(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        output_json = mcp_module.get_scan_inflation_linkers_extremes_tool(
            as_of_date="not-a-date",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid as_of_date" in parsed["error"]

    def test_nominal_curve_family_returns_controlled_envelope(self):
        """If the user passes a nominal sovereign curve family in the
        CSV, the schema validator rejects it and the wrapper returns
        a controlled-error envelope."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        output_json = mcp_module.get_scan_inflation_linkers_extremes_tool(
            curve_families="USD_TIPS,UST",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]
        assert (
            "UST" in parsed["error"]
            or "whitelist" in parsed["error"].lower()
        )


# ===========================================================================
# 2. CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.inflation_indexed_bonds.tools.scan_inflation_linkers_extremes import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.scan_inflation_linkers_extremes.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_scan_inflation_linkers_extremes_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(SCAN_INFLATION_LINKERS_EXTREMES_CONFIG_PATH)
        assert cfg.tool.name == "scan_inflation_linkers_extremes_tool"
        assert cfg.tool.domain == "inflation_indexed_bonds"


# ===========================================================================
# 3. Response-model validation (mock parity)
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        validated = ScanInflationLinkersExtremesOutput.model_validate(
            _well_formed_output(),
        )
        assert "252" in validated.methodology_disclosure
        assert len(validated.results) == 2
        for row in validated.results:
            assert "252" in row.methodology_disclosure

    def test_response_model_requires_methodology_disclosure(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad.pop("methodology_disclosure")
        with pytest.raises(ValidationError):
            ScanInflationLinkersExtremesOutput.model_validate(bad)

    def test_result_row_requires_methodology_disclosure(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad["results"][0].pop("methodology_disclosure")
        with pytest.raises(ValidationError):
            ScanInflationLinkersExtremesOutput.model_validate(bad)


# ===========================================================================
# 4. Schemas hub re-export
# ===========================================================================

class TestSchemasHubReExport:
    def test_input_re_exported(self):
        from rates_agent.inflation_indexed_bonds.tools.schemas import (
            ScanInflationLinkersExtremesInput,
        )
        from rates_agent.inflation_indexed_bonds.tools.scan_inflation_linkers_extremes import (
            ScanInflationLinkersExtremesInput as direct_in,
        )
        assert ScanInflationLinkersExtremesInput is direct_in

    def test_output_re_exported(self):
        from rates_agent.inflation_indexed_bonds.tools.schemas import (
            ScanInflationLinkersExtremesOutput,
        )
        from rates_agent.inflation_indexed_bonds.tools.scan_inflation_linkers_extremes import (
            ScanInflationLinkersExtremesOutput as direct_out,
        )
        assert ScanInflationLinkersExtremesOutput is direct_out

    def test_result_row_re_exported(self):
        from rates_agent.inflation_indexed_bonds.tools.schemas import (
            ScanInflationLinkersExtremesResultRow,
        )
        from rates_agent.inflation_indexed_bonds.tools.scan_inflation_linkers_extremes import (
            ScanInflationLinkersExtremesResultRow as direct_row,
        )
        assert ScanInflationLinkersExtremesResultRow is direct_row


# ===========================================================================
# 5. Workflow registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_registered_in_rates_primitive_resolver(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "scan_inflation_linkers_extremes_tool"
        )
        assert spec.tool_name == "scan_inflation_linkers_extremes_tool"
        assert spec.config_path == (
            SCAN_INFLATION_LINKERS_EXTREMES_CONFIG_PATH
        )

    def test_output_field_units_is_empty_by_design(self):
        """P8 / P5: the scan is a SNAPSHOT primitive — no canonical
        TimeSeries on the wire.  Per-row snapshots mix PERCENT
        (real_yield_pct), BPS (daily/monthly_change_bps), unit-less
        z-score, and plain-string reference columns; none of those
        map onto the closed-enum ``TimeSeriesUnits`` family
        cleanly. ``{}`` is the documented exempt mode — same pattern
        as the bond_futures scanner."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "scan_inflation_linkers_extremes_tool"
        )
        assert spec.output_field_units == {}

    def test_registered_input_and_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_indexed_bonds.tools.scan_inflation_linkers_extremes import (
            ScanInflationLinkersExtremesInput,
            ScanInflationLinkersExtremesOutput,
        )
        spec = rates_primitive_resolver(
            "scan_inflation_linkers_extremes_tool"
        )
        assert spec.input_class is ScanInflationLinkersExtremesInput
        assert spec.output_class is ScanInflationLinkersExtremesOutput


# ===========================================================================
# 6. fetch_scan_universe_reference Layer-A unit test
# ===========================================================================

class TestFetchScanUniverseReferenceUnit:
    """Layer-A unit test for the new scoped helper added to
    ``shared/analytics/rates_fetch.py``.  Mocks the engine connect
    + execute path and asserts the helper builds the right bind
    params + SQL shape across the filtered / unfiltered branches."""

    def test_helper_full_universe_uses_all_sql(self):
        from shared.analytics import rates_fetch
        mock_engine = MagicMock(name="engine")
        mock_conn = MagicMock(name="conn")
        mock_result = MagicMock(name="result")
        mock_result.fetchall.return_value = []
        mock_result.keys.return_value = [
            "curve_family", "tenor", "contract_code",
            "maturity_date", "country", "vendor_ticker",
        ]
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        df = rates_fetch.fetch_scan_universe_reference(
            engine=mock_engine,
            instrument_type="inflation_linker",
            curve_families=None,
        )
        assert df.empty
        # The full-universe SQL must NOT bind curve_families.
        sql_obj, bind_params = mock_conn.execute.call_args.args
        assert bind_params == {"instrument_type": "inflation_linker"}

    def test_helper_filtered_universe_uses_filtered_sql(self):
        from shared.analytics import rates_fetch
        mock_engine = MagicMock(name="engine")
        mock_conn = MagicMock(name="conn")
        mock_result = MagicMock(name="result")
        mock_result.fetchall.return_value = []
        mock_result.keys.return_value = [
            "curve_family", "tenor", "contract_code",
            "maturity_date", "country", "vendor_ticker",
        ]
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        rates_fetch.fetch_scan_universe_reference(
            engine=mock_engine,
            instrument_type="inflation_linker",
            curve_families=["USD_TIPS", "GBP_LINKER"],
        )
        _sql_obj, bind_params = mock_conn.execute.call_args.args
        assert bind_params == {
            "instrument_type": "inflation_linker",
            "curve_families": ["USD_TIPS", "GBP_LINKER"],
        }
