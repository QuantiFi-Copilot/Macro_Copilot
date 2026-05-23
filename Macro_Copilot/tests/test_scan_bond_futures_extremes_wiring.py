"""
test_scan_bond_futures_extremes_wiring.py — Caller-wiring smoke tests
                                              for the bond-futures
                                              universe-wide extremes
                                              scan

Mirrors test_futures_volume_oi_wiring.py for the
``scan_bond_futures_extremes`` tool. Caller surface in V1:

  1. rates_agent/bond_futures/mcp_server.py::scan_bond_futures_extremes_tool

Key load-bearing properties under test:

  - MCP wrapper passes config= explicitly (no auto-load fallback).
  - MCP wrapper accepts a comma-separated ``curve_families`` string
    (MCP exposes flat scalars) and splits it before constructing the
    Pydantic input — mirrors the sovereign ``scan_extremes_tool``
    convention.
  - The MCP wrapper does NOT accept ``field_name`` / ``metrics``
    parameters (the four metrics ARE the concept; the four field
    mnemonics are YAML-owned). Exposing them would be input-schema
    overreach (PR9 / OPR8).
  - Empty ``curve_families`` string ("") translates to None →
    YAML's full-universe whitelist (default behaviour).
  - The MCP wrapper DOES preserve methodology_disclosure on the
    response AND on every result row (the catalog's per-row
    disclosure guardrail).
  - Workflow registration is honest: PrimitiveSpec is registered with
    the correct CONFIG_PATH and with ``output_field_units = {}``
    (snapshot primitive; no canonical TimeSeries; same exempt
    pattern as futures_price_level / futures_volume_oi).
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.bond_futures.tools.scan_bond_futures_extremes import (
    CONFIG_PATH as SCAN_BOND_FUTURES_EXTREMES_CONFIG_PATH,
    ScanBondFuturesExtremesOutput,
)
from shared.config import ToolConfig, clear_tool_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_output() -> dict:
    """Mock matches the live wire shape — MUST validate against
    ``ScanBondFuturesExtremesOutput``."""
    methodology = (
        "Universe-wide front-month bond-futures sweep. Z-score lookback "
        "= 252 trading days. This is rolling-generic price; the CTD-"
        "implied yield is not yet a primitive in this build, and this "
        "is NOT an inter-commodity DV01-weighted spread, NOT a basis "
        "trade, and NOT a tenor-anchored yield call. The CTD "
        "identification, gross/net basis, implied repo, and DV01-"
        "weighted inter-commodity RV stack are Phase-4 work gated on "
        "D-repo + D-deliverable data ingestion (ADR 0011 — bond_"
        "futures V1 ships monitors only)."
    )
    return {
        "scan_summary": (
            "Scanned 6 bond-futures stems (6 scoreable). "
            "Stems with |z| >= 1.5 per metric: "
            "price=2, price_change=1, volume=3, open_interest=2. "
            "Showing top 5 per metric (8 rows). "
            "as_of_dates span 2026-04-29 to 2026-04-30."
        ),
        "results": [
            {
                "rank": 1,
                "metric": "price",
                "curve_family": "UST_FUT",
                "contract_code": "TY1",
                "tenor": "10Y",
                "as_of_date": "2026-04-30",
                "current_price": 110.453125,
                "daily_price_change": 0.09375,
                "current_volume": 612345.0,
                "current_open_interest": 3045678.0,
                "delta_open_interest_1d": 12345.0,
                "z_score": 2.41,
                "signal": "EXTREME_HIGH",
                "methodology_disclosure": methodology,
            },
            {
                "rank": 1,
                "metric": "open_interest",
                "curve_family": "UST_FUT",
                "contract_code": "US1",
                "tenor": "30Y",
                "as_of_date": "2026-04-30",
                "current_price": 145.78125,
                "daily_price_change": -0.0625,
                "current_volume": 412000.0,
                "current_open_interest": 1850000.0,
                "delta_open_interest_1d": 25000.0,
                "z_score": 3.12,
                "signal": "EXTREME_HIGH",
                "methodology_disclosure": methodology,
            },
        ],
        "methodology_disclosure": methodology,
    }


def _assert_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_scan_bond_futures_extremes called without `config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "scan_bond_futures_extremes_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'scan_bond_futures_extremes_tool' (catches imports of the "
        "wrong tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_price_field" in cfg.conventions
    assert "default_volume_field" in cfg.conventions
    assert "default_open_interest_field" in cfg.conventions
    assert "bond_futures_curve_families" in cfg.conventions


# ===========================================================================
# 1. MCP wrapper
# ===========================================================================

class TestMcpScanBondFuturesExtremesWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.bond_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_bond_futures_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            output_json = mcp_module.scan_bond_futures_extremes_tool(
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
        # P5 / catalog guardrail disclosure must reach the LLM.
        md = parsed.get("methodology_disclosure", "")
        assert "252" in md, (
            "MCP wrapper must propagate the z-score lookback "
            "verbatim (catalog methodology guardrail)"
        )
        assert "universe-wide" in md.lower()
        # Per-row disclosure must also be present (catalog wording).
        for row in parsed["results"]:
            assert "methodology_disclosure" in row
            assert "252" in row["methodology_disclosure"]

    def test_empty_curve_families_string_becomes_none(self):
        """The MCP wrapper accepts an empty-string ``curve_families``
        sentinel and translates it to None so the YAML whitelist's
        full-universe default flows through."""
        from rates_agent.bond_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_bond_futures_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.scan_bond_futures_extremes_tool(curve_families="")
        params = spy.call_args.kwargs["params"]
        assert params.curve_families is None

    def test_csv_curve_families_split_to_list(self):
        """The MCP wrapper splits a comma-separated string into a list
        before constructing the Pydantic input."""
        from rates_agent.bond_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_bond_futures_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.scan_bond_futures_extremes_tool(
                curve_families="UST_FUT,DE_FUT",
            )
        params = spy.call_args.kwargs["params"]
        assert params.curve_families == ["UST_FUT", "DE_FUT"]

    def test_csv_curve_families_strips_whitespace(self):
        from rates_agent.bond_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_bond_futures_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.scan_bond_futures_extremes_tool(
                curve_families=" UST_FUT , DE_FUT  ",
            )
        params = spy.call_args.kwargs["params"]
        assert params.curve_families == ["UST_FUT", "DE_FUT"]

    def test_mcp_wrapper_has_no_field_name_param(self):
        """The MCP wrapper MUST NOT accept ``field_name`` — the price /
        volume / OI mnemonics are YAML-owned. Exposing them as LLM
        inputs would be input-schema overreach (PR9 / OPR8)."""
        from rates_agent.bond_futures import mcp_server as mcp_module
        sig = inspect.signature(mcp_module.scan_bond_futures_extremes_tool)
        assert "field_name" not in sig.parameters

    def test_mcp_wrapper_has_no_metrics_param(self):
        """The MCP wrapper MUST NOT accept ``metrics`` — the four
        metrics ARE the concept (PR2 / PR8 / OPR8)."""
        from rates_agent.bond_futures import mcp_server as mcp_module
        sig = inspect.signature(mcp_module.scan_bond_futures_extremes_tool)
        assert "metrics" not in sig.parameters

    def test_mcp_wrapper_has_no_lookback_days_param(self):
        """Reviewer round-1 mandatory-fix #2 (PR8 input-schema
        discipline): ``lookback_days`` controls the fetch window —
        that is methodology, not a per-query knob. The MCP wrapper
        MUST NOT accept it."""
        from rates_agent.bond_futures import mcp_server as mcp_module
        sig = inspect.signature(mcp_module.scan_bond_futures_extremes_tool)
        assert "lookback_days" not in sig.parameters

    def test_mcp_wrapper_has_as_of_date_param(self):
        """Reviewer round-1 mandatory-fix #2: ``as_of_date`` is the
        legitimate per-query anchor input that replaced the previous
        ``date.today()`` non-determinism."""
        from rates_agent.bond_futures import mcp_server as mcp_module
        sig = inspect.signature(mcp_module.scan_bond_futures_extremes_tool)
        assert "as_of_date" in sig.parameters
        # Default must be the empty-string sentinel (translated to
        # None before constructing the Pydantic input).
        assert sig.parameters["as_of_date"].default == ""

    def test_top_n_sentinel_omitted_becomes_none(self):
        """The MCP boundary uses ``0`` as the omit-sentinel for
        ``top_n`` because the wire layer cannot carry None for an
        int parameter. ``0`` ⇒ None ⇒ compute resolves to the
        YAML's ``default_top_n``."""
        from rates_agent.bond_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_bond_futures_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            # Use the wrapper signature's defaults (top_n=0,
            # min_abs_z_score=-1.0, as_of_date="").
            mcp_module.scan_bond_futures_extremes_tool()
        params = spy.call_args.kwargs["params"]
        assert params.top_n is None
        assert params.min_abs_z_score is None
        assert params.as_of_date is None

    def test_top_n_explicit_value_passes_through(self):
        from rates_agent.bond_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_bond_futures_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.scan_bond_futures_extremes_tool(top_n=10)
        params = spy.call_args.kwargs["params"]
        assert params.top_n == 10

    def test_min_abs_z_score_explicit_value_passes_through(self):
        from rates_agent.bond_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_bond_futures_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.scan_bond_futures_extremes_tool(min_abs_z_score=0.5)
        params = spy.call_args.kwargs["params"]
        assert params.min_abs_z_score == 0.5

    def test_as_of_date_iso_string_parsed_to_date(self):
        """ISO-format ``as_of_date`` string at the MCP boundary must
        parse to a ``datetime.date`` before constructing the
        Pydantic input."""
        from datetime import date as _date
        from rates_agent.bond_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_bond_futures_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.scan_bond_futures_extremes_tool(
                as_of_date="2026-04-08",
            )
        params = spy.call_args.kwargs["params"]
        assert params.as_of_date == _date(2026, 4, 8)

    def test_as_of_date_malformed_returns_controlled_error(self):
        """An unparseable as_of_date returns the controlled-error
        envelope (no exception)."""
        from rates_agent.bond_futures import mcp_server as mcp_module
        output_json = mcp_module.scan_bond_futures_extremes_tool(
            as_of_date="not-a-date",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid as_of_date" in parsed["error"]

    def test_policy_futures_curve_family_returns_controlled_envelope(self):
        """If the user passes SOFR_FUT in the CSV, the schema
        validator rejects it and the wrapper returns a controlled
        error envelope (mirrors the futures_price_level pattern)."""
        from rates_agent.bond_futures import mcp_server as mcp_module
        output_json = mcp_module.scan_bond_futures_extremes_tool(
            curve_families="UST_FUT,SOFR_FUT",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]
        assert "SOFR_FUT" in parsed["error"] or "policy_futures" in parsed["error"].lower()


# ===========================================================================
# 2. CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.bond_futures.tools.scan_bond_futures_extremes import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.bond_futures.tools.scan_bond_futures_extremes.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_scan_bond_futures_extremes_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(SCAN_BOND_FUTURES_EXTREMES_CONFIG_PATH)
        assert cfg.tool.name == "scan_bond_futures_extremes_tool"
        assert cfg.tool.domain == "bond_futures"


# ===========================================================================
# 3. Response-model validation (mock parity)
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        validated = ScanBondFuturesExtremesOutput.model_validate(
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
            ScanBondFuturesExtremesOutput.model_validate(bad)

    def test_result_row_requires_methodology_disclosure(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad["results"][0].pop("methodology_disclosure")
        with pytest.raises(ValidationError):
            ScanBondFuturesExtremesOutput.model_validate(bad)


# ===========================================================================
# 4. Workflow registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_registered_in_rates_primitive_resolver(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver("scan_bond_futures_extremes_tool")
        assert spec.tool_name == "scan_bond_futures_extremes_tool"
        assert spec.config_path == SCAN_BOND_FUTURES_EXTREMES_CONFIG_PATH

    def test_output_field_units_is_empty_by_design(self):
        """P8 / P5: the scan is a SNAPSHOT primitive — no canonical
        TimeSeries on the wire. Even if it had one, per-row snapshots
        mix contract-native price units, contract-count volume + OI,
        and unit-less z-scores; none of those map onto the closed-
        enum ``TimeSeriesUnits`` family in V1 (no ``PRICE`` member,
        no ``CONTRACTS`` member). ``{}`` is the documented exempt
        mode (see shared/workflow/validate.py:368) — same pattern as
        futures_price_level / futures_volume_oi."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver("scan_bond_futures_extremes_tool")
        assert spec.output_field_units == {}

    def test_registered_input_and_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.bond_futures.tools.scan_bond_futures_extremes import (
            ScanBondFuturesExtremesInput,
            ScanBondFuturesExtremesOutput,
        )
        spec = rates_primitive_resolver("scan_bond_futures_extremes_tool")
        assert spec.input_class is ScanBondFuturesExtremesInput
        assert spec.output_class is ScanBondFuturesExtremesOutput
