"""
test_scan_policy_futures_extremes_wiring.py — Caller-wiring smoke
                                               tests for the
                                               policy-futures
                                               universe-wide
                                               extremes scan

Mirrors test_scan_bond_futures_extremes_wiring.py. Caller surface
in V1:

  1. rates_agent/policy_futures/mcp_server.py::
     get_scan_policy_futures_extremes_tool

Key load-bearing properties under test:

  - MCP wrapper passes config= explicitly (no auto-load fallback).
  - MCP wrapper accepts a comma-separated ``curve_families`` string
    AND a comma-separated ``metrics`` string (the metrics CSV is
    the legitimate metric-subset selector, NOT a methodology-knob
    overreach).
  - The MCP wrapper does NOT accept ``field_name`` /
    ``lookback_days`` parameters (the three field mnemonics are
    YAML-owned; ``lookback_days`` is methodology).
  - Empty ``curve_families`` / ``metrics`` strings translate to
    None ⇒ YAML's full-universe / full-metric defaults.
  - The MCP wrapper DOES preserve methodology_disclosure on the
    response AND on every result row (the catalog's per-row
    disclosure guardrail).
  - Workflow registration is honest: PrimitiveSpec is registered
    with the correct CONFIG_PATH and with ``output_field_units = {}``
    (snapshot primitive; no canonical TimeSeries; same exempt
    pattern as scan_bond_futures_extremes / futures_strip_snapshot).
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.policy_futures.tools.scan_policy_futures_extremes import (
    CONFIG_PATH as SCAN_POLICY_FUTURES_EXTREMES_CONFIG_PATH,
    ScanPolicyFuturesExtremesOutput,
)
from shared.config import ToolConfig, clear_tool_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_output() -> dict:
    """Mock matches the live wire shape — MUST validate against
    ``ScanPolicyFuturesExtremesOutput``."""
    methodology = (
        "Universe-wide policy-futures strip scan across "
        "curve_families=[SOFR_FUT,EUR_SHORT_RATE_FUT,SONIA_FUT], "
        "strip_positions 1..8. Ranks each (curve_family, "
        "strip_position) stem by absolute z-score across four "
        "metrics: implied_rate_level, implied_rate_change, "
        "volume_level, open_interest_level. Z-score lookback = 252 "
        "trading days. Underlying short-rate regime varies across "
        "rows: SOFR_FUT=RFR, EUR_SHORT_RATE_FUT=IBOR, SONIA_FUT=RFR "
        "(RFR = compounded daily risk-free rate; IBOR = unsecured "
        "3M term IBOR). The implied-rate conversion is metadata-"
        "driven from instrument_master.attributes->>"
        "'inverse_pricing': implied_rate_pct = 100 - raw_price for "
        "inverse-priced strips. Rolling-generic strip. NOT a "
        "tenor-anchored OIS scan, NOT a pack-average summary, NOT "
        "a curve-shape read. This is a morning screen, NOT a "
        "tactical trade signal."
    )
    return {
        "scan_summary": (
            "Scanned 24 policy-futures stems (24 scoreable). "
            "Stems with |z| >= 1.5 per metric: "
            "implied_rate_level=4, implied_rate_change=2, "
            "volume_level=3, open_interest_level=5. "
            "Showing top 5 per metric (14 rows). "
            "as_of 2026-04-08."
        ),
        "results": [
            {
                "rank": 1,
                "metric": "implied_rate_level",
                "curve_family": "SOFR_FUT",
                "strip_position": 1,
                "contract_code": "SFR1",
                "underlying_contract_code": "SFRH6 COMB",
                "security_name": "SFRH6 COMB sec",
                "expiry_date": "2026-06-16",
                "contract_size": 2500.0,
                "inverse_priced": True,
                "short_rate_regime": "RFR",
                "quote_units": "100 - rate",
                "as_of_date": "2026-04-08",
                "current_raw_price": 96.33250,
                "implied_rate_pct": 3.6675,
                "daily_change_implied_rate_bps": -0.50,
                "current_volume": 612345.0,
                "current_open_interest": 1045678.0,
                "delta_open_interest_1d": 12345.0,
                "z_score": 2.41,
                "signal": "EXTREME_HIGH",
                "methodology_disclosure": methodology,
            },
            {
                "rank": 1,
                "metric": "open_interest_level",
                "curve_family": "EUR_SHORT_RATE_FUT",
                "strip_position": 4,
                "contract_code": "ER4",
                "underlying_contract_code": "ERM7",
                "security_name": "ERM7 sec",
                "expiry_date": "2027-06-15",
                "contract_size": 1000000.0,
                "inverse_priced": True,
                "short_rate_regime": "IBOR",
                "quote_units": "100 - rate",
                "as_of_date": "2026-04-08",
                "current_raw_price": 97.4150,
                "implied_rate_pct": 2.5850,
                "daily_change_implied_rate_bps": 1.20,
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
        "calculate_scan_policy_futures_extremes called without "
        "`config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert (
        cfg.tool.name
        == "policy_futures_scan_policy_futures_extremes_tool"
    ), (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'policy_futures_scan_policy_futures_extremes_tool' "
        "(catches imports of the wrong tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_price_field" in cfg.conventions
    assert "default_volume_field" in cfg.conventions
    assert "default_open_interest_field" in cfg.conventions
    assert "policy_futures_curve_families" in cfg.conventions
    assert "short_rate_regime_map" in cfg.conventions
    assert "default_metrics" in cfg.conventions


# ===========================================================================
# 1. MCP wrapper
# ===========================================================================

class TestMcpScanPolicyFuturesExtremesWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.policy_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_policy_futures_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            output_json = (
                mcp_module.get_scan_policy_futures_extremes_tool(
                    curve_families="",
                    top_n=5,
                    min_abs_z_score=1.5,
                    metrics="",
                    as_of_date="",
                )
            )
        assert spy.call_count == 1
        _assert_config_passed(spy.call_args)
        parsed = json.loads(output_json)
        assert "scan_summary" in parsed
        assert "results" in parsed
        # P5 / catalog guardrail disclosure must reach the LLM.
        md = parsed.get("methodology_disclosure", "")
        assert "252" in md
        assert "universe-wide" in md.lower()
        assert "SOFR_FUT=RFR" in md
        assert "EUR_SHORT_RATE_FUT=IBOR" in md
        # Per-row disclosure must also be present.
        for row in parsed["results"]:
            assert "methodology_disclosure" in row
            assert "252" in row["methodology_disclosure"]
            assert row["short_rate_regime"] in ("RFR", "IBOR")

    def test_empty_curve_families_string_becomes_none(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_policy_futures_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_policy_futures_extremes_tool(
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
            "calculate_scan_policy_futures_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_policy_futures_extremes_tool(
                curve_families="SOFR_FUT,EUR_SHORT_RATE_FUT",
            )
        params = spy.call_args.kwargs["params"]
        assert params.curve_families == [
            "SOFR_FUT", "EUR_SHORT_RATE_FUT",
        ]

    def test_csv_curve_families_strips_whitespace(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_policy_futures_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_policy_futures_extremes_tool(
                curve_families=" SOFR_FUT , SONIA_FUT  ",
            )
        params = spy.call_args.kwargs["params"]
        assert params.curve_families == ["SOFR_FUT", "SONIA_FUT"]

    def test_csv_metrics_split_to_list(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_policy_futures_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_policy_futures_extremes_tool(
                metrics="implied_rate_level,implied_rate_change",
            )
        params = spy.call_args.kwargs["params"]
        assert params.metrics == [
            "implied_rate_level", "implied_rate_change",
        ]

    def test_empty_metrics_string_becomes_none(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_policy_futures_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_policy_futures_extremes_tool(metrics="")
        params = spy.call_args.kwargs["params"]
        assert params.metrics is None

    def test_mcp_wrapper_has_no_field_name_param(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        sig = inspect.signature(
            mcp_module.get_scan_policy_futures_extremes_tool,
        )
        assert "field_name" not in sig.parameters

    def test_mcp_wrapper_has_no_lookback_days_param(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        sig = inspect.signature(
            mcp_module.get_scan_policy_futures_extremes_tool,
        )
        assert "lookback_days" not in sig.parameters

    def test_mcp_wrapper_has_as_of_date_param(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        sig = inspect.signature(
            mcp_module.get_scan_policy_futures_extremes_tool,
        )
        assert "as_of_date" in sig.parameters
        assert sig.parameters["as_of_date"].default == ""

    def test_top_n_sentinel_omitted_becomes_none(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_policy_futures_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_policy_futures_extremes_tool()
        params = spy.call_args.kwargs["params"]
        assert params.top_n is None
        assert params.min_abs_z_score is None
        assert params.metrics is None
        assert params.as_of_date is None

    def test_top_n_explicit_value_passes_through(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_policy_futures_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_policy_futures_extremes_tool(top_n=10)
        params = spy.call_args.kwargs["params"]
        assert params.top_n == 10

    def test_min_abs_z_score_explicit_value_passes_through(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_policy_futures_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_policy_futures_extremes_tool(
                min_abs_z_score=0.5,
            )
        params = spy.call_args.kwargs["params"]
        assert params.min_abs_z_score == 0.5

    def test_as_of_date_iso_string_parsed_to_date(self):
        from datetime import date as _date
        from rates_agent.policy_futures import mcp_server as mcp_module
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_scan_policy_futures_extremes",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_scan_policy_futures_extremes_tool(
                as_of_date="2026-04-08",
            )
        params = spy.call_args.kwargs["params"]
        assert params.as_of_date == _date(2026, 4, 8)

    def test_as_of_date_malformed_returns_controlled_error(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        output_json = (
            mcp_module.get_scan_policy_futures_extremes_tool(
                as_of_date="not-a-date",
            )
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid as_of_date" in parsed["error"]

    def test_bond_futures_curve_family_returns_controlled_envelope(self):
        """If the user passes UST_FUT in the CSV, the schema
        validator rejects it and the wrapper returns a controlled
        error envelope (mirrors the per-strip monitor pattern)."""
        from rates_agent.policy_futures import mcp_server as mcp_module
        output_json = (
            mcp_module.get_scan_policy_futures_extremes_tool(
                curve_families="SOFR_FUT,UST_FUT",
            )
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]
        assert "UST_FUT" in parsed["error"]

    def test_invalid_metric_returns_controlled_envelope(self):
        from rates_agent.policy_futures import mcp_server as mcp_module
        output_json = (
            mcp_module.get_scan_policy_futures_extremes_tool(
                metrics="NOT_A_METRIC",
            )
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]


# ===========================================================================
# 2. CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.policy_futures.tools.scan_policy_futures_extremes import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.policy_futures.tools.scan_policy_futures_extremes.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_scan_policy_futures_extremes_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(
            SCAN_POLICY_FUTURES_EXTREMES_CONFIG_PATH,
        )
        assert (
            cfg.tool.name
            == "policy_futures_scan_policy_futures_extremes_tool"
        )
        assert cfg.tool.domain == "policy_futures"


# ===========================================================================
# 3. Schema hub re-exports
# ===========================================================================

class TestSchemaHubReExports:
    def test_schemas_hub_re_exports_scan_classes(self):
        from rates_agent.policy_futures.tools.schemas import (
            ScanMetric,
            PolicyFuturesScanCurveFamily,
            ScanPolicyFuturesExtremesInput,
            ScanPolicyFuturesExtremesOutput,
            ScanPolicyFuturesExtremesResultRow,
        )
        assert ScanMetric is not None
        assert PolicyFuturesScanCurveFamily is not None
        assert ScanPolicyFuturesExtremesInput is not None
        assert ScanPolicyFuturesExtremesOutput is not None
        assert ScanPolicyFuturesExtremesResultRow is not None


# ===========================================================================
# 4. Response-model validation (mock parity)
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        validated = ScanPolicyFuturesExtremesOutput.model_validate(
            _well_formed_output(),
        )
        assert "252" in validated.methodology_disclosure
        assert len(validated.results) == 2
        for row in validated.results:
            assert "252" in row.methodology_disclosure
            assert row.short_rate_regime in ("RFR", "IBOR")

    def test_response_model_requires_methodology_disclosure(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad.pop("methodology_disclosure")
        with pytest.raises(ValidationError):
            ScanPolicyFuturesExtremesOutput.model_validate(bad)

    def test_result_row_requires_methodology_disclosure(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad["results"][0].pop("methodology_disclosure")
        with pytest.raises(ValidationError):
            ScanPolicyFuturesExtremesOutput.model_validate(bad)

    def test_result_row_requires_short_rate_regime(self):
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad["results"][0].pop("short_rate_regime")
        with pytest.raises(ValidationError):
            ScanPolicyFuturesExtremesOutput.model_validate(bad)


# ===========================================================================
# 5. Workflow registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_registered_in_rates_primitive_resolver(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "get_scan_policy_futures_extremes_tool",
        )
        assert (
            spec.tool_name == "get_scan_policy_futures_extremes_tool"
        )
        assert (
            spec.config_path
            == SCAN_POLICY_FUTURES_EXTREMES_CONFIG_PATH
        )

    def test_output_field_units_is_empty_by_design(self):
        """P8 / P5: the scan is a SNAPSHOT primitive — no canonical
        TimeSeries on the wire. Per-row snapshots mix PERCENT
        (implied_rate_pct), BPS (daily_change_implied_rate_bps),
        the contract's native price units, CONTRACTS (volume /
        OI), and unit-less z-scores. ``{}`` is the documented
        exempt mode — same pattern as scan_bond_futures_extremes /
        futures_strip_snapshot."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver(
            "get_scan_policy_futures_extremes_tool",
        )
        assert spec.output_field_units == {}

    def test_registered_input_and_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.policy_futures.tools.scan_policy_futures_extremes import (
            ScanPolicyFuturesExtremesInput,
            ScanPolicyFuturesExtremesOutput,
        )
        spec = rates_primitive_resolver(
            "get_scan_policy_futures_extremes_tool",
        )
        assert spec.input_class is ScanPolicyFuturesExtremesInput
        assert spec.output_class is ScanPolicyFuturesExtremesOutput
