"""Futures primitives → open-DAG Series-leaf bridging contract tests.

Locks ADR 0017: the five dated-history futures primitives carry
canonical ``TimeSeries`` companion fields (built 1-to-1 from the same
display slices as their bespoke wire rows) + matching
``output_field_units`` declarations, so the open-DAG lane can bind
them as typed Series leaves.  Pre-ADR they were registered with
``output_field_units={}`` → TERMINAL_ONLY_SNAPSHOT → dropped from the
Selector catalogue (campaign honest-refusals k03 / k04 / l01 / l03).

Three layers, all deterministic (no DB, no LLM):
  1. classification — the five primitives classify BRIDGEABLE_SERIES;
     the genuinely point-in-time ``futures_strip_snapshot`` (one row
     per strip position at a single as_of_date) and the scanners stay
     TERMINAL_ONLY_SNAPSHOT;
  2. catalogue rendering — ``render_tool_catalogue`` keeps the five
     with EXACTLY the canonical fields (the bespoke ``time_series``
     row lists are not bindable);
  3. the Series bridge — ``tool_output_to_artifact_series`` lifts each
     canonical field from a well-formed output dict into a typed
     ``Series`` artifact carrying the declared unit.

Mirrors tests/orchestrator/open_dag/test_multicomponent_output_fields.py
(the PCA multi-output contract tests) for the futures registrations.
"""
from __future__ import annotations

import pytest

from orchestrator.contracts import Domain
from orchestrator.open_dag.composability_audit import (
    Composability,
    classify_primitive,
)
from orchestrator.selectors import render_tool_catalogue
from rates_agent.workflows import rates_primitive_resolver
from shared.artifacts.adapters.from_time_series import (
    tool_output_to_artifact_series,
)
from shared.config import load_tool_config
from shared.schemas.time_series import TimeSeriesUnits


class _Tool:
    """Mimics the surface area of a LangChain MCP BaseTool that
    ``render_tool_catalogue`` reads (``.name`` + ``.description``)."""

    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description


# The five bridged primitives and their canonical declarations.
_BRIDGED = {
    "get_futures_price_level_tool": {"time_series_price": "price"},
    "get_futures_volume_oi_tool": {
        "time_series_volume": "contracts",
        "time_series_open_interest": "contracts",
    },
    "policy_futures_get_futures_price_level_tool": {
        "time_series_implied_rate": "percent",
    },
    "policy_futures_get_futures_calendar_spread_tool": {
        "time_series_spread_implied_rate": "percent",
    },
    "policy_futures_get_volume_open_interest_snapshot_tool": {
        "time_series_volume": "contracts",
        "time_series_open_interest": "contracts",
    },
}


# ---------------------------------------------------------------------------
# 0. The ADR 0017 enum members exist with the declared wire values
# ---------------------------------------------------------------------------

def test_adr_0017_units_members_exist():
    assert TimeSeriesUnits.PRICE.value == "price"
    assert TimeSeriesUnits.CONTRACTS.value == "contracts"


def test_all_declared_units_are_enum_members():
    """Every declared unit string on the five bridged primitives is a
    member of the closed ``TimeSeriesUnits`` family (the value the
    canonical TimeSeries actually carries — the declaration cannot
    drift outside the enum)."""
    valid = {u.value for u in TimeSeriesUnits}
    for tool_name in _BRIDGED:
        spec = rates_primitive_resolver(tool_name)
        for field, unit in spec.output_field_units.items():
            assert unit in valid, (
                f"{tool_name}.{field} declares non-enum unit {unit!r}"
            )


# ---------------------------------------------------------------------------
# 1. Classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool_name", sorted(_BRIDGED))
def test_bridged_primitive_classifies_bridgeable_series(tool_name):
    spec = rates_primitive_resolver(tool_name)
    assert spec.output_field_units == _BRIDGED[tool_name]
    entry = classify_primitive(spec)
    assert entry.classification is Composability.BRIDGEABLE_SERIES


@pytest.mark.parametrize("tool_name", [
    # Genuinely point-in-time / ranked-snapshot primitives MUST stay
    # TERMINAL_ONLY_SNAPSHOT — the strip snapshot is one row per strip
    # position at a single as_of_date (no dated history; flat output
    # without current_metrics), the scanners are ranked snapshots.
    "policy_futures_get_futures_strip_snapshot_tool",
    "policy_futures_get_scan_policy_futures_extremes_tool",
    "scan_bond_futures_extremes_tool",
])
def test_snapshot_only_primitives_stay_terminal(tool_name):
    spec = rates_primitive_resolver(tool_name)
    assert spec.output_field_units == {}
    entry = classify_primitive(spec)
    assert entry.classification is Composability.TERMINAL_ONLY_SNAPSHOT


# ---------------------------------------------------------------------------
# 2. Catalogue rendering (orchestrator/selectors.py output_field plumbing)
# ---------------------------------------------------------------------------

def test_catalogue_exposes_bond_futures_canonical_fields():
    tools = [
        _Tool("get_futures_price_level_tool", "front-month price history"),
        _Tool("get_futures_volume_oi_tool", "front-month volume + OI history"),
    ]
    cat, dropped = render_tool_catalogue(
        Domain.BOND_FUTURES, tools, rates_primitive_resolver,
    )
    assert dropped == []
    fields = {e.mcp_tool_name: e.available_output_fields for e in cat}
    # available_output_fields is sorted (declare_primitive_output sorts
    # the declared keys for stable catalogue prompts).
    assert fields == {
        "get_futures_price_level_tool": ("time_series_price",),
        "get_futures_volume_oi_tool": (
            "time_series_open_interest", "time_series_volume",
        ),
    }


def test_catalogue_exposes_policy_futures_canonical_fields():
    tools = [
        _Tool("get_futures_price_level_tool", "strip-slot implied rate"),
        _Tool("get_futures_calendar_spread_tool", "strip calendar spread"),
        _Tool("get_volume_open_interest_snapshot_tool", "strip volume + OI"),
        # Point-in-time whole-strip snapshot — must STILL be dropped.
        _Tool("get_futures_strip_snapshot_tool", "whole-strip snapshot"),
    ]
    cat, dropped = render_tool_catalogue(
        Domain.POLICY_FUTURES, tools, rates_primitive_resolver,
    )
    fields = {e.mcp_tool_name: e.available_output_fields for e in cat}
    assert fields == {
        "get_futures_price_level_tool": ("time_series_implied_rate",),
        "get_futures_calendar_spread_tool": (
            "time_series_spread_implied_rate",
        ),
        "get_volume_open_interest_snapshot_tool": (
            "time_series_open_interest", "time_series_volume",
        ),
    }
    assert len(dropped) == 1
    assert dropped[0].mcp_tool_name == "get_futures_strip_snapshot_tool"
    assert dropped[0].composability is Composability.TERMINAL_ONLY_SNAPSHOT


def test_catalogue_never_exposes_bespoke_row_lists():
    """The bespoke ``time_series`` List[Row] wire fields must NOT be
    bindable — the Series bridge rejects them at runtime (the Codex
    Round-5 failure mode this filter exists for)."""
    tools = [
        _Tool("get_futures_price_level_tool", "strip-slot implied rate"),
        _Tool("get_futures_calendar_spread_tool", "strip calendar spread"),
        _Tool("get_volume_open_interest_snapshot_tool", "strip volume + OI"),
    ]
    cat, _ = render_tool_catalogue(
        Domain.POLICY_FUTURES, tools, rates_primitive_resolver,
    )
    for entry in cat:
        assert "time_series" not in entry.available_output_fields


# ---------------------------------------------------------------------------
# 3. The Series bridge lifts the canonical fields end-to-end
# ---------------------------------------------------------------------------

_DATES = ["2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30"]


def _rows(values):
    return [
        {"date": d, "value": v} for d, v in zip(_DATES, values)
    ]


def test_bridge_lifts_bond_futures_price_series():
    from rates_agent.bond_futures.tools.futures_price_level import (
        CONFIG_PATH,
        FuturesPriceLevelInput,
        FuturesPriceLevelOutput,
    )

    prices = [110.703125, 110.640625, 110.578125, 110.453125]
    output = {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family": "UST_FUT",
            "contract_code": "TY1",
            "tenor": "10Y",
            "quote_units": "points",
            "contract_size": 100000.0,
            "expiry_date": "2026-12-21",
            "security_name": "TYZ6 COMB",
            "current_price": prices[-1],
            "daily_change_price": -0.125,
            "weekly_change_price": -0.5,
            "monthly_change_price": 0.75,
            "z_score": -0.42,
            "high_252d_price": 113.5,
            "low_252d_price": 108.25,
            "percentile_252d": 41.9,
            "observation_count": len(prices),
        },
        "time_series": [
            {"date": d, "price": p} for d, p in zip(_DATES, prices)
        ],
        "time_series_price": {
            "series_name": "ust_fut_ty1_price",
            "units": "price",
            "description": "TY1 price history (points).",
            "rows": _rows(prices),
        },
        "methodology_disclosure": "rolling-generic price caveat.",
    }
    series = tool_output_to_artifact_series(
        output,
        output_class=FuturesPriceLevelOutput,
        output_field="time_series_price",
        tool_name="get_futures_price_level_tool",
        tool_config=load_tool_config(CONFIG_PATH),
        params=FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="TY1",
        ),
    )
    assert series.units is TimeSeriesUnits.PRICE
    assert series.series_key == "ust_fut_ty1_price"
    assert list(series.payload.values) == prices


def test_bridge_lifts_policy_calendar_spread_series():
    from rates_agent.policy_futures.tools.futures_calendar_spread import (
        CONFIG_PATH,
        FuturesCalendarSpreadInput,
        FuturesCalendarSpreadOutput,
    )

    spreads = [-0.1350, -0.1325, -0.1300, -0.1250]
    output = {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family": "SOFR_FUT",
            "strip_position_short": 1,
            "strip_position_long": 2,
            "spread_label": "SFR1-SFR2",
            "contract_code_short": "SFR1",
            "contract_code_long": "SFR2",
            "underlying_contract_code_short": "SFRM26",
            "underlying_contract_code_long": "SFRU26",
            "security_name_short": "SFRM26 COMB",
            "security_name_long": "SFRU26 COMB",
            "expiry_date_short": "2026-06-16",
            "expiry_date_long": "2026-09-15",
            "inverse_priced": True,
            "short_rate_regime": "RFR",
            "raw_price_spread": 0.12500,
            "spread_implied_rate_pct": spreads[-1],
            "daily_change_raw_price_spread": -0.00500,
            "daily_change_spread_implied_rate_pct": 0.0050,
            "z_score_spread_implied_rate": -0.45,
            "high_252d_spread_implied_rate_pct": 0.2500,
            "low_252d_spread_implied_rate_pct": -0.4500,
            "mid_252d_spread_implied_rate_pct": -0.1000,
            "percentile_252d": 46.4,
            "rolling_window_days": 252,
            "observation_count": len(spreads),
        },
        "time_series": [
            {
                "date": d,
                "raw_price_spread": -s,
                "spread_implied_rate_pct": s,
            }
            for d, s in zip(_DATES, spreads)
        ],
        "time_series_spread_implied_rate": {
            "series_name": "sofr_fut_1_2_calendar_spread",
            "units": "percent",
            "description": "SFR1-SFR2 implied-rate spread (pct points).",
            "rows": _rows(spreads),
        },
        "methodology_disclosure": "sign convention + regime caveats.",
    }
    series = tool_output_to_artifact_series(
        output,
        output_class=FuturesCalendarSpreadOutput,
        output_field="time_series_spread_implied_rate",
        tool_name="policy_futures_get_futures_calendar_spread_tool",
        tool_config=load_tool_config(CONFIG_PATH),
        params=FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1,
            strip_position_long=2,
        ),
    )
    assert series.units is TimeSeriesUnits.PERCENT
    assert series.series_key == "sofr_fut_1_2_calendar_spread"
    assert list(series.payload.values) == spreads


def test_bridge_lifts_volume_oi_contract_count_series():
    from rates_agent.bond_futures.tools.futures_volume_oi import (
        CONFIG_PATH,
        FuturesVolumeOIInput,
        FuturesVolumeOIOutput,
    )

    volumes = [580000.0, 595000.0, 600000.0, 612345.0]
    ois = [3020000.0, 3026000.0, 3033333.0, 3045678.0]
    output = {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family": "UST_FUT",
            "contract_code": "TY1",
            "tenor": "10Y",
            "contract_size": 100000.0,
            "expiry_date": "2026-12-21",
            "security_name": "TYZ6 COMB",
            "current_volume": volumes[-1],
            "current_open_interest": ois[-1],
            "delta_open_interest_1d": 12345.0,
            "oi_z_score": -0.42,
            "oi_high_252d": 3500000.0,
            "oi_low_252d": 2500000.0,
            "oi_percentile_252d": 54.6,
            "volume_rolling_mean_22d": 590000.0,
            "volume_rolling_max_22d": 1200000.0,
            "observation_count": len(volumes),
        },
        "time_series": [
            {"date": d, "volume": v, "open_interest": o}
            for d, v, o in zip(_DATES, volumes, ois)
        ],
        "time_series_volume": {
            "series_name": "ust_fut_ty1_volume",
            "units": "contracts",
            "description": "TY1 daily traded volume in CONTRACTS.",
            "rows": _rows(volumes),
        },
        "time_series_open_interest": {
            "series_name": "ust_fut_ty1_open_interest",
            "units": "contracts",
            "description": "TY1 end-of-day open interest in CONTRACTS.",
            "rows": _rows(ois),
        },
        "methodology_disclosure": "rolling-generic OI caveat (252d z).",
    }
    cfg = load_tool_config(CONFIG_PATH)
    params = FuturesVolumeOIInput(
        curve_family="UST_FUT", contract_code="TY1",
    )
    for field, values, key in (
        ("time_series_volume", volumes, "ust_fut_ty1_volume"),
        ("time_series_open_interest", ois, "ust_fut_ty1_open_interest"),
    ):
        series = tool_output_to_artifact_series(
            output,
            output_class=FuturesVolumeOIOutput,
            output_field=field,
            tool_name="get_futures_volume_oi_tool",
            tool_config=cfg,
            params=params,
        )
        assert series.units is TimeSeriesUnits.CONTRACTS
        assert series.series_key == key
        assert list(series.payload.values) == values


def test_bridge_refuses_bespoke_row_list_field():
    """Binding the bespoke ``time_series`` row list must raise — it is
    not a canonical TimeSeries.  The catalogue never offers it, and
    the bridge backstops the contract."""
    from rates_agent.bond_futures.tools.futures_price_level import (
        CONFIG_PATH,
        FuturesPriceLevelInput,
        FuturesPriceLevelOutput,
    )

    prices = [110.703125, 110.640625, 110.578125, 110.453125]
    output = {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family": "UST_FUT",
            "contract_code": "TY1",
            "tenor": "10Y",
            "quote_units": "points",
            "contract_size": 100000.0,
            "expiry_date": "2026-12-21",
            "security_name": "TYZ6 COMB",
            "current_price": prices[-1],
            "daily_change_price": -0.125,
            "weekly_change_price": -0.5,
            "monthly_change_price": 0.75,
            "z_score": -0.42,
            "high_252d_price": 113.5,
            "low_252d_price": 108.25,
            "percentile_252d": 41.9,
            "observation_count": len(prices),
        },
        "time_series": [
            {"date": d, "price": p} for d, p in zip(_DATES, prices)
        ],
        "time_series_price": {
            "series_name": "ust_fut_ty1_price",
            "units": "price",
            "description": "TY1 price history (points).",
            "rows": _rows(prices),
        },
        "methodology_disclosure": "rolling-generic price caveat.",
    }
    with pytest.raises(ValueError, match="did not resolve"):
        tool_output_to_artifact_series(
            output,
            output_class=FuturesPriceLevelOutput,
            output_field="time_series",
            tool_name="get_futures_price_level_tool",
            tool_config=load_tool_config(CONFIG_PATH),
            params=FuturesPriceLevelInput(
                curve_family="UST_FUT", contract_code="TY1",
            ),
        )
