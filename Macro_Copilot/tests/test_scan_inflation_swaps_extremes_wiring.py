"""
test_scan_inflation_swaps_extremes_wiring.py — Caller-wiring smoke
                                                tests for the ZCIS
                                                universe-wide
                                                extremes scan

Layer A offline wiring smoke tests for the new ZCIS scanner.
Mirrors test_scan_inflation_linkers_extremes_wiring.py.  Caller
surface in V1:

  1. rates_agent/inflation_swaps/mcp_server.py::
     get_scan_inflation_swaps_extremes_tool

Key load-bearing properties under test:

  - MCP wrapper passes config= explicitly (no auto-load fallback;
    PR14).
  - MCP wrapper accepts a comma-separated ``curve_families`` string
    (MCP exposes flat scalars) and splits it before constructing
    the Pydantic input — mirrors the linker scanner convention.
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
    pattern as the linker scanner).
  - The extended ``fetch_scan_universe_reference`` helper's
    projection includes ``underlying_index`` for BOTH the linker
    instrument_type AND the inflation_swap instrument_type — the
    additive extension preserves the linker scanner's call site.
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.inflation_swaps.tools.scan_inflation_swaps_extremes import (
    CONFIG_PATH as SCAN_INFLATION_SWAPS_EXTREMES_CONFIG_PATH,
    ScanInflationSwapsExtremesOutput,
)
from shared.config import ToolConfig, clear_tool_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_output() -> dict:
    """Mock matches the live wire shape — MUST validate against
    ``ScanInflationSwapsExtremesOutput``."""
    methodology = (
        "Universe-wide ZCIS quoted-rate LEVEL scan. Z-score lookback "
        "= 252 trading days. This is a ZCIS (zero-coupon inflation "
        "swap) QUOTED-RATE level read, NOT a breakeven scan, NOT a "
        "linker real-yield scan, NOT a curve-shape scan, NOT a "
        "forward-inflation scan. INDEX-FAMILY MISMATCH "
        "(load-bearing): the three ZCIS markets reference DIFFERENT "
        "underlying inflation indices — USD_ZCIS references US "
        "CPI-U non-seasonally adjusted (CPURNSA Index), EUR_ZCIS "
        "references Eurozone HICP ex-tobacco (CPTFEMU Index), "
        "GBP_ZCIS references UK RPI (UKRPI Index). The ranking "
        "spans this heterogeneity; each output row's "
        "underlying_index field names the exact reference index. "
        "MARKET-STRUCTURE MISMATCH (load-bearing): ZCIS conventions "
        "differ across currencies in index-publication-lag "
        "(USD_ZCIS 3M lag with daily interpolation, EUR_ZCIS 3M "
        "lag with monthly interpolation, GBP_ZCIS 2M lag with "
        "monthly interpolation), dealer-quote liquidity, and "
        "structural basis vs the bond-implied breakeven curve; an "
        "extreme may reflect liquidity / structural moves rather "
        "than pure inflation-expectations divergence. This is a "
        "morning screen, NOT a tactical trade signal — use the "
        "sibling inflation_swap_curve_spread / "
        "inflation_swap_forward / inflation_swap_butterfly / "
        "cross_market_inflation_swap_spread / "
        "swap_breakeven_basis_simple primitives for further "
        "decomposition."
    )
    return {
        "scan_summary": (
            "Scanned 21 ZCIS stems (21 scoreable). Stems with "
            "|z| >= 1.5: 5. Showing top 3 by absolute ZCIS-rate "
            "z-score. as_of 2026-04-08."
        ),
        "results": [
            {
                "rank": 1,
                "curve_family": "GBP_ZCIS",
                "tenor": "10Y",
                "as_of_date": "2026-04-08",
                "zcis_rate_pct": 3.95,
                "daily_change_zcis_rate_bps": 4.5,
                "monthly_change_zcis_rate_bps": 12.5,
                "z_score_zcis_rate": 2.45,
                "signal": "EXTREME_HIGH",
                "maturity_date": "2036-03-15",
                "underlying_index": "UKRPI Index",
                "vendor_ticker": "BPSWIT10 Curncy",
                "methodology_disclosure": methodology,
            },
            {
                "rank": 2,
                "curve_family": "USD_ZCIS",
                "tenor": "2Y",
                "as_of_date": "2026-04-08",
                "zcis_rate_pct": 2.55,
                "daily_change_zcis_rate_bps": -2.0,
                "monthly_change_zcis_rate_bps": -8.0,
                "z_score_zcis_rate": -2.10,
                "signal": "EXTREME_LOW",
                "maturity_date": "2028-03-15",
                "underlying_index": "CPURNSA Index",
                "vendor_ticker": "USSWIT2 Curncy",
                "methodology_disclosure": methodology,
            },
        ],
        "methodology_disclosure": methodology,
    }


def _assert_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_scan_inflation_swaps_extremes called without "
        "`config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "scan_inflation_swaps_extremes_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'scan_inflation_swaps_extremes_tool'"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_zcis_rate_field" in cfg.conventions
    assert "inflation_swap_curve_families" in cfg.conventions


# ===========================================================================
# 1. MCP wrapper
# ===========================================================================

class TestMcpScanInflationSwapsExtremesWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_inflation_swaps_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            output_json = (
                mcp_module.get_scan_inflation_swaps_extremes_tool(
                    curve_families="",
                    top_n=5,
                    min_abs_z_score=1.5,
                    as_of_date="",
                )
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
        # ZCIS scope must propagate to the LLM-visible payload.
        assert "zcis" in md.lower() or "inflation swap" in md.lower()
        # Per-row disclosure must also be present (catalog wording).
        for row in parsed["results"]:
            assert "methodology_disclosure" in row
            assert "252" in row["methodology_disclosure"]

    def test_empty_curve_families_string_becomes_none(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_inflation_swaps_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_inflation_swaps_extremes_tool(
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
            "calculate_scan_inflation_swaps_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_inflation_swaps_extremes_tool(
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
            "calculate_scan_inflation_swaps_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_inflation_swaps_extremes_tool(
                curve_families=" USD_ZCIS , EUR_ZCIS  ",
            )
        params = spy.call_args.kwargs["params"]
        assert params.curve_families == ["USD_ZCIS", "EUR_ZCIS"]

    def test_mcp_wrapper_has_no_field_name_param(self):
        """The MCP wrapper MUST NOT accept ``field_name`` — the field
        mnemonic is YAML-owned. Exposing it as an LLM input would be
        input-schema overreach (PR8 / OPR8)."""
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        sig = inspect.signature(
            mcp_module.get_scan_inflation_swaps_extremes_tool
        )
        assert "field_name" not in sig.parameters

    def test_mcp_wrapper_has_no_lookback_days_param(self):
        """PR8 input-schema discipline: ``lookback_days`` controls
        the fetch window — that is methodology, not a per-query
        knob."""
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        sig = inspect.signature(
            mcp_module.get_scan_inflation_swaps_extremes_tool
        )
        assert "lookback_days" not in sig.parameters

    def test_mcp_wrapper_has_no_metrics_param(self):
        """The single metric (ZCIS rate LEVEL z-score) IS the
        concept; exposing ``metrics`` as a per-query knob would be
        scope creep past the catalog wording."""
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        sig = inspect.signature(
            mcp_module.get_scan_inflation_swaps_extremes_tool
        )
        assert "metrics" not in sig.parameters

    def test_mcp_wrapper_has_as_of_date_param(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        sig = inspect.signature(
            mcp_module.get_scan_inflation_swaps_extremes_tool
        )
        assert "as_of_date" in sig.parameters
        # Default must be the empty-string sentinel.
        assert sig.parameters["as_of_date"].default == ""

    def test_top_n_sentinel_omitted_becomes_none(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_inflation_swaps_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_inflation_swaps_extremes_tool()
        params = spy.call_args.kwargs["params"]
        assert params.top_n is None
        assert params.min_abs_z_score is None
        assert params.as_of_date is None

    def test_top_n_explicit_value_passes_through(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_inflation_swaps_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_inflation_swaps_extremes_tool(top_n=10)
        params = spy.call_args.kwargs["params"]
        assert params.top_n == 10

    def test_min_abs_z_score_explicit_value_passes_through(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_inflation_swaps_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_inflation_swaps_extremes_tool(
                min_abs_z_score=0.5,
            )
        params = spy.call_args.kwargs["params"]
        assert params.min_abs_z_score == 0.5

    def test_as_of_date_iso_string_parsed_to_date(self):
        from datetime import date as _date
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_inflation_swaps_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_inflation_swaps_extremes_tool(
                as_of_date="2026-04-08",
            )
        params = spy.call_args.kwargs["params"]
        assert params.as_of_date == _date(2026, 4, 8)

    def test_as_of_date_malformed_returns_controlled_error(self):
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        output_json = mcp_module.get_scan_inflation_swaps_extremes_tool(
            as_of_date="not-a-date",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid as_of_date" in parsed["error"]

    def test_non_zcis_curve_family_returns_controlled_envelope(self):
        """If the user passes a linker curve family in the CSV, the
        schema validator rejects it and the wrapper returns a
        controlled-error envelope."""
        from rates_agent.inflation_swaps import mcp_server as mcp_module
        output_json = mcp_module.get_scan_inflation_swaps_extremes_tool(
            curve_families="USD_ZCIS,USD_TIPS",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]
        assert (
            "USD_TIPS" in parsed["error"]
            or "whitelist" in parsed["error"].lower()
        )


# ===========================================================================
# 2. CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.inflation_swaps.tools.scan_inflation_swaps_extremes import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.inflation_swaps.tools.scan_inflation_swaps_extremes.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_scan_inflation_swaps_extremes_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(
            SCAN_INFLATION_SWAPS_EXTREMES_CONFIG_PATH,
        )
        assert cfg.tool.name == "scan_inflation_swaps_extremes_tool"
        assert cfg.tool.domain == "inflation_swaps"


# ===========================================================================
# 3. Response-model validation (mock parity)
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        validated = ScanInflationSwapsExtremesOutput.model_validate(
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
            ScanInflationSwapsExtremesOutput.model_validate(bad)

    def test_result_row_requires_methodology_disclosure(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad["results"][0].pop("methodology_disclosure")
        with pytest.raises(ValidationError):
            ScanInflationSwapsExtremesOutput.model_validate(bad)


# ===========================================================================
# 4. Schemas hub re-export
# ===========================================================================

class TestSchemasHubReExport:
    def test_input_re_exported(self):
        from rates_agent.inflation_swaps.tools.schemas import (
            ScanInflationSwapsExtremesInput,
        )
        from rates_agent.inflation_swaps.tools.scan_inflation_swaps_extremes import (
            ScanInflationSwapsExtremesInput as direct_in,
        )
        assert ScanInflationSwapsExtremesInput is direct_in

    def test_output_re_exported(self):
        from rates_agent.inflation_swaps.tools.schemas import (
            ScanInflationSwapsExtremesOutput,
        )
        from rates_agent.inflation_swaps.tools.scan_inflation_swaps_extremes import (
            ScanInflationSwapsExtremesOutput as direct_out,
        )
        assert ScanInflationSwapsExtremesOutput is direct_out

    def test_result_row_re_exported(self):
        from rates_agent.inflation_swaps.tools.schemas import (
            ScanInflationSwapsExtremesResultRow,
        )
        from rates_agent.inflation_swaps.tools.scan_inflation_swaps_extremes import (
            ScanInflationSwapsExtremesResultRow as direct_row,
        )
        assert ScanInflationSwapsExtremesResultRow is direct_row


# ===========================================================================
# 5. Workflow registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_registered_in_rates_primitive_resolver(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "get_scan_inflation_swaps_extremes_tool"
        )
        assert spec.tool_name == "get_scan_inflation_swaps_extremes_tool"
        assert spec.config_path == (
            SCAN_INFLATION_SWAPS_EXTREMES_CONFIG_PATH
        )

    def test_output_field_units_is_empty_by_design(self):
        """P8 / P5: the scan is a SNAPSHOT primitive — no canonical
        TimeSeries on the wire.  Per-row snapshots mix PERCENT
        (zcis_rate_pct), BPS (daily/monthly_change_zcis_rate_bps),
        unit-less z-score, and plain-string reference columns; none
        of those map onto the closed-enum ``TimeSeriesUnits`` family
        cleanly. ``{}`` is the documented exempt mode — same pattern
        as the linker / bond_futures scanner."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "get_scan_inflation_swaps_extremes_tool"
        )
        assert spec.output_field_units == {}

    def test_registered_input_and_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_swaps.tools.scan_inflation_swaps_extremes import (
            ScanInflationSwapsExtremesInput,
            ScanInflationSwapsExtremesOutput,
        )
        spec = rates_primitive_resolver(
            "get_scan_inflation_swaps_extremes_tool"
        )
        assert spec.input_class is ScanInflationSwapsExtremesInput
        assert spec.output_class is ScanInflationSwapsExtremesOutput


# ===========================================================================
# 6. fetch_scan_universe_reference Layer-A unit test (extension)
# ===========================================================================
#
# The fetch_scan_universe_reference helper was EXTENDED in this PR
# with ``underlying_index`` in its SELECT projection (additive
# change). Layer-A unit test: assert BOTH the new (ZCIS) projection
# AND the existing (linker) projection paths return the expected
# columns. The SQL change is non-breaking because existing call
# sites read only the columns they need.
class TestFetchScanUniverseReferenceUnit:
    def _build_mock_engine(self, cols):
        mock_engine = MagicMock(name="engine")
        mock_conn = MagicMock(name="conn")
        mock_result = MagicMock(name="result")
        mock_result.fetchall.return_value = []
        mock_result.keys.return_value = cols
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__.return_value = (
            mock_conn
        )
        return mock_engine, mock_conn

    def test_helper_inflation_swap_filtered_uses_filtered_sql(self):
        """ZCIS-side projection path: filtered call binds curve_families
        and the result-key path exposes underlying_index."""
        from shared.analytics import rates_fetch
        cols = [
            "curve_family", "tenor", "contract_code",
            "maturity_date", "country", "vendor_ticker",
            "underlying_index",
        ]
        mock_engine, mock_conn = self._build_mock_engine(cols)
        rates_fetch.fetch_scan_universe_reference(
            engine=mock_engine,
            instrument_type="inflation_swap",
            curve_families=["USD_ZCIS", "EUR_ZCIS"],
        )
        _sql_obj, bind_params = mock_conn.execute.call_args.args
        assert bind_params == {
            "instrument_type": "inflation_swap",
            "curve_families": ["USD_ZCIS", "EUR_ZCIS"],
            "end_date": None,
        }

    def test_helper_inflation_swap_unfiltered_uses_all_sql(self):
        """ZCIS-side projection path: unfiltered call does NOT bind
        curve_families."""
        from shared.analytics import rates_fetch
        cols = [
            "curve_family", "tenor", "contract_code",
            "maturity_date", "country", "vendor_ticker",
            "underlying_index",
        ]
        mock_engine, mock_conn = self._build_mock_engine(cols)
        rates_fetch.fetch_scan_universe_reference(
            engine=mock_engine,
            instrument_type="inflation_swap",
            curve_families=None,
        )
        _sql_obj, bind_params = mock_conn.execute.call_args.args
        assert bind_params == {
            "instrument_type": "inflation_swap",
            "end_date": None,
        }

    def test_helper_inflation_linker_existing_path_still_works(self):
        """Linker-side projection path (the existing call site): the
        extension is additive — existing callers continue to work.
        Sanity-check that the helper invokes the same SQL surface
        and accepts the linker instrument_type."""
        from shared.analytics import rates_fetch
        cols = [
            "curve_family", "tenor", "contract_code",
            "maturity_date", "country", "vendor_ticker",
            "underlying_index",
        ]
        mock_engine, mock_conn = self._build_mock_engine(cols)
        df = rates_fetch.fetch_scan_universe_reference(
            engine=mock_engine,
            instrument_type="inflation_linker",
            curve_families=None,
        )
        assert df.empty
        _sql_obj, bind_params = mock_conn.execute.call_args.args
        assert bind_params == {
            "instrument_type": "inflation_linker",
            "end_date": None,
        }

    def test_helper_sql_projects_underlying_index(self):
        """The helper's compiled SQL text must reference
        ``underlying_index`` in the SELECT — the additive column
        the ZCIS scanner relies on."""
        from shared.analytics import rates_fetch
        sql_all = str(
            rates_fetch._FETCH_SCAN_UNIVERSE_REFERENCE_ALL_SQL,
        )
        sql_filtered = str(
            rates_fetch._FETCH_SCAN_UNIVERSE_REFERENCE_FILTERED_SQL,
        )
        assert "underlying_index" in sql_all
        assert "underlying_index" in sql_filtered
