"""tests/test_build_linker_panel_compute.py

Offline deterministic compute tests for the ``build_linker_panel``
primitive (Plan §5 Group 3 #20).  The shared fetcher
(``fetch_linker_panel_by_vendor_ticker``) and the universe-metadata
helper (``fetch_linker_universe``) are monkeypatched at the compute
module's import site so the tests do not require a live DB; the
SQL-parity validator (``test_build_linker_panel_sql_validation.py``)
covers the DB-grounded layer separately.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from rates_agent.inflation_indexed_bonds.tools.build_linker_panel import (
    CONFIG_PATH,
    BuildLinkerPanelInput,
    build_linker_panel,
)
from rates_agent.inflation_indexed_bonds.tools.build_linker_panel.schemas import (
    BuildLinkerPanelOutput,
)
from shared.artifacts.types import Panel
from shared.config import load_tool_config


# ============================================================================
# Test fixtures — synthetic linker universe (4 curve_families ×
# bonds: USD_TIPS=4, GBP_LINKER=9, EUR_FR_LINKER=5, CAD_RRB=6 = 24
# bonds total, matching the live ingested playbook universe)
# ============================================================================


_BONDS_BY_CURVE = {
    "USD_TIPS": [
        # (vendor_ticker, tenor, maturity_date, country)
        ("GTII5 Govt", "5Y", date(2030, 10, 15), "US"),
        ("GTII10 Govt", "10Y", date(2036, 1, 15), "US"),
        ("GTII20 Govt", "20Y", date(2045, 2, 15), "US"),
        ("GTII30 Govt", "30Y", date(2056, 2, 15), "US"),
    ],
    "GBP_LINKER": [
        ("GTGBPII1Y Govt", "1Y", date(2027, 11, 22), "UK"),
        ("GTGBPII2Y Govt", "2Y", date(2028, 8, 10), "UK"),
        ("GTGBPII3Y Govt", "3Y", date(2029, 3, 22), "UK"),
        ("GTGBPII5Y Govt", "5Y", date(2031, 8, 10), "UK"),
        ("GTGBPII10Y Govt", "10Y", date(2035, 9, 22), "UK"),
        ("GTGBPII15Y Govt", "15Y", date(2041, 8, 10), "UK"),
        ("GTGBPII20Y Govt", "20Y", date(2045, 3, 22), "UK"),
        ("GTGBPII30Y Govt", "30Y", date(2054, 11, 22), "UK"),
        ("GTGBPII50Y Govt", "50Y", date(2073, 3, 22), "UK"),
    ],
    "EUR_FR_LINKER": [
        ("GTFRFII2Y Govt", "2Y", date(2028, 3, 1), "France"),
        ("GTFRFII5Y Govt", "5Y", date(2029, 7, 25), "France"),
        ("GTFRFII7Y Govt", "7Y", date(2032, 3, 1), "France"),
        ("GTFRFII10Y Govt", "10Y", date(2036, 3, 1), "France"),
        ("GTFRFII15Y Govt", "15Y", date(2039, 3, 1), "France"),
    ],
    "CAD_RRB": [
        ("GTCADII5Y Govt", "5Y", date(2031, 12, 1), "Canada"),
        ("GTCADII10Y Govt", "10Y", date(2036, 12, 1), "Canada"),
        ("GTCADII15Y Govt", "15Y", date(2041, 12, 1), "Canada"),
        ("GTCADII20Y Govt", "20Y", date(2047, 12, 1), "Canada"),
        ("GTCADII25Y Govt", "25Y", date(2050, 12, 1), "Canada"),
        ("GTCADII30Y Govt", "30Y", date(2054, 12, 1), "Canada"),
    ],
}

_INDEX_FAMILY_BY_CURVE = {
    "USD_TIPS": "US_CPI_URBAN",
    "GBP_LINKER": "UK_RPI",
    "EUR_FR_LINKER": "EU_HICP",
    "CAD_RRB": "CAN_CPI",
}


def _make_universe_frame(curve_families):
    """Build a synthetic ``fetch_linker_universe`` return."""
    rows = []
    for cf in curve_families:
        idx_family = _INDEX_FAMILY_BY_CURVE[cf]
        for ticker, tenor, maturity, country in _BONDS_BY_CURVE[cf]:
            rows.append(
                {
                    "curve_family": cf,
                    "vendor_ticker": ticker,
                    "tenor": tenor,
                    "country": country,
                    "maturity_date": maturity,
                    "underlying_index": None,
                    "pricing_type": "real_yield",
                    "inflation_index_family": idx_family,
                    "security_name_attr": None,
                }
            )
    return pd.DataFrame(rows)


def _ordered_tickers(curve_families):
    """Replicate the production fetcher's column-order rule:
    sort by (curve_family, maturity_date, vendor_ticker)."""
    ordered = []
    for cf in curve_families:
        bonds_sorted = sorted(
            _BONDS_BY_CURVE[cf],
            key=lambda b: (b[2].toordinal(), b[0]),
        )
        for ticker, _tenor, _mat, _country in bonds_sorted:
            ordered.append(ticker)
    return ordered


def _make_panel_frame(
    curve_families,
    *,
    start=date(2024, 1, 1),
    n_business_days=40,
    constant_per_column=None,
):
    """Build a synthetic deterministic wide linker panel.

    Column order matches the (curve_family, maturity_date,
    vendor_ticker) sort the production fetcher produces.
    """
    idx = pd.bdate_range(start=start, periods=n_business_days)
    ordered_columns = _ordered_tickers(curve_families)
    data = {}
    for col_idx, col in enumerate(ordered_columns):
        if constant_per_column is not None:
            data[col] = [float(constant_per_column.get(col, col_idx))] * len(idx)
        else:
            # Deterministic spread so every column is distinguishable.
            data[col] = [
                float(col_idx) + 1.5 + 0.001 * row_idx
                for row_idx in range(len(idx))
            ]
    df = pd.DataFrame(data, index=idx, columns=ordered_columns)
    df.index = pd.DatetimeIndex(df.index)
    return df


# ============================================================================
# 1. Default-config happy path on the full universe
# ============================================================================


def test_build_linker_panel_default_full_universe():
    """Full linker universe with default conventions returns a Panel
    over all 24 columns with PERCENT units and a populated
    methodology card."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildLinkerPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 2, 28),
    )

    expected_curve_families = [
        "USD_TIPS", "GBP_LINKER", "EUR_FR_LINKER", "CAD_RRB",
    ]
    universe_df = _make_universe_frame(expected_curve_families)
    panel_df = _make_panel_frame(expected_curve_families)

    with patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_panel_by_vendor_ticker",
        return_value=panel_df,
    ):
        result = build_linker_panel(engine=None, params=params, config=cfg)

    assert "error" not in result, f"unexpected error envelope: {result}"

    # PR14 frozen wire fields present.
    for key in (
        "start_date",
        "end_date",
        "row_count",
        "column_count",
        "curve_families",
        "vendor_tickers",
        "units_by_column",
        "methodology_card",
        "panel",
    ):
        assert key in result, f"missing wire field {key!r}"

    # PR8 — there is NO tenors wire field on the output (linkers are
    # specific-maturity bonds, not tenor-pillar swaps).
    assert "tenors" not in result, (
        "build_linker_panel must not surface a 'tenors' wire field — "
        "linkers are specific-maturity bonds, not tenor-pillar swaps."
    )

    # Universe is 24 bonds across 4 curve families.
    assert result["column_count"] == 24
    assert result["row_count"] == len(panel_df)
    assert result["curve_families"] == expected_curve_families
    # First USD_TIPS column should be the 5Y (earliest maturity 2030).
    assert result["vendor_tickers"][0] == "GTII5 Govt"
    assert result["units_by_column"]["GTII5 Govt"] == "percent"
    # Every column gets percent units.
    assert set(result["units_by_column"].values()) == {"percent"}

    card = result["methodology_card"]
    for key in (
        "field_name",
        "calendar_policy",
        "missing_data_policy",
        "ffill_limit_days",
        "ffill_source_tag",
        "curve_families",
        "vendor_ticker_column_key",
        "security_name_caveat",
        "index_family_caveat",
        "market_structure_caveat",
        "cross_region_business_days_caveat",
        "curve_family_reference",
        "methodology_label",
    ):
        assert key in card, f"methodology_card missing {key!r}"
    assert card["field_name"] == "YLD_YTM_MID"
    # V1 default — matches the sibling build_zcis_panel +
    # build_sovereign_yield_panel convention value
    # (``business_days``) so the cross-tool config lint stays
    # clean.  The multi-region calendar caveat is surfaced via
    # ``cross_region_business_days_caveat`` and the opt-in
    # ``instrument_native`` (covered by the per-policy tests
    # below).
    assert card["calendar_policy"] == "business_days"
    assert card["missing_data_policy"] == "forward_fill_only"
    assert card["ffill_limit_days"] == 5
    assert card["ffill_source_tag"] == "industry_standard_5d_ffill"

    # Per-curve_family index_family caveat coverage.
    cf_ref = card["curve_family_reference"]
    assert set(cf_ref.keys()) == set(expected_curve_families)
    assert cf_ref["USD_TIPS"]["inflation_index_family"] == "US_CPI_URBAN"
    assert cf_ref["GBP_LINKER"]["inflation_index_family"] == "UK_RPI"
    assert cf_ref["EUR_FR_LINKER"]["inflation_index_family"] == "EU_HICP"
    assert cf_ref["CAD_RRB"]["inflation_index_family"] == "CAN_CPI"

    # Per-curve_family bond-count check (matches live DB universe).
    assert cf_ref["USD_TIPS"]["bond_count"] == 4
    assert cf_ref["GBP_LINKER"]["bond_count"] == 9
    assert cf_ref["EUR_FR_LINKER"]["bond_count"] == 5
    assert cf_ref["CAD_RRB"]["bond_count"] == 6

    # Per-bond reference rows expose vendor_ticker / tenor / country
    # / maturity_date.
    usd_bonds = cf_ref["USD_TIPS"]["bonds"]
    assert {b["vendor_ticker"] for b in usd_bonds} == {
        "GTII5 Govt", "GTII10 Govt", "GTII20 Govt", "GTII30 Govt",
    }
    for b in usd_bonds:
        assert b["country"] == "US"
        assert b["tenor"] in {"5Y", "10Y", "20Y", "30Y"}
        assert b["maturity_date"] is not None

    # Security-name caveat surfaces the no-proxy substitution.
    assert "vendor_ticker" in card["security_name_caveat"]
    assert "security_name" in card["security_name_caveat"]
    assert "NULL" in card["security_name_caveat"]
    # Index-family caveat names all four index families.
    for tag in ("US_CPI_URBAN", "UK_RPI", "EU_HICP", "CAN_CPI"):
        assert tag in card["index_family_caveat"]

    # The Panel artifact round-trips through the schema.
    validated = BuildLinkerPanelOutput.model_validate(result)
    assert isinstance(validated.panel, Panel)
    assert list(validated.panel.payload.columns) == list(
        panel_df.columns
    )


# ============================================================================
# 2. Curve-family scoping
# ============================================================================


def test_build_linker_panel_curve_family_scope_usd_only():
    """USD_TIPS-only scope returns 4 columns (the four TIPS bonds)
    and the methodology card surfaces only the USD_TIPS curve-family
    reference."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildLinkerPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 2, 28),
        curve_families=["USD_TIPS"],
    )

    universe_df = _make_universe_frame(["USD_TIPS"])
    panel_df = _make_panel_frame(["USD_TIPS"])

    with patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_panel_by_vendor_ticker",
        return_value=panel_df,
    ):
        result = build_linker_panel(engine=None, params=params, config=cfg)

    assert "error" not in result
    assert result["column_count"] == 4
    assert result["curve_families"] == ["USD_TIPS"]
    assert all(t.startswith("GTII") for t in result["vendor_tickers"])
    assert list(result["methodology_card"]["curve_family_reference"].keys()) == [
        "USD_TIPS"
    ]


def test_build_linker_panel_curve_family_scope_us_uk():
    """US + UK scope returns 13 columns (4 TIPS + 9 GBP linkers)
    in the deterministic (curve_family, maturity_date) order."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildLinkerPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 2, 28),
        curve_families=["USD_TIPS", "GBP_LINKER"],
    )

    universe_df = _make_universe_frame(["USD_TIPS", "GBP_LINKER"])
    panel_df = _make_panel_frame(["USD_TIPS", "GBP_LINKER"])

    with patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_panel_by_vendor_ticker",
        return_value=panel_df,
    ) as fetch_mock:
        result = build_linker_panel(engine=None, params=params, config=cfg)

    assert "error" not in result
    assert result["column_count"] == 13  # 4 + 9

    # Production fetcher got the resolved curve_family scope.
    fetch_call = fetch_mock.call_args
    assert list(fetch_call.kwargs["curve_families"]) == [
        "USD_TIPS", "GBP_LINKER",
    ]


# ============================================================================
# 3. Field-name override
# ============================================================================


def test_build_linker_panel_field_name_override():
    """Explicit field_name override flows through to the fetcher."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildLinkerPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_TIPS"],
        field_name="YLD_YTM_BID",
    )

    universe_df = _make_universe_frame(["USD_TIPS"])
    panel_df = _make_panel_frame(["USD_TIPS"])

    with patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_panel_by_vendor_ticker",
        return_value=panel_df,
    ) as fetch_mock:
        result = build_linker_panel(engine=None, params=params, config=cfg)

    assert "error" not in result
    assert fetch_mock.call_args.kwargs["field_name"] == "YLD_YTM_BID"
    assert result["methodology_card"]["field_name"] == "YLD_YTM_BID"


# ============================================================================
# 4. Calendar policy
# ============================================================================


def test_build_linker_panel_calendar_policy_business_days_default():
    """The V1 default ``business_days`` calendar policy drops
    Saturday rows.  Matches the sibling build_zcis_panel +
    build_sovereign_yield_panel default value so the cross-tool
    config lint stays clean.  The multi-region caveat is surfaced
    on the methodology card via
    ``cross_region_business_days_caveat``."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildLinkerPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_TIPS"],
    )

    idx = pd.DatetimeIndex(["2024-01-02", "2024-01-06", "2024-01-08"])
    panel_df = pd.DataFrame(
        {"GTII5 Govt": [1.0, 2.0, 3.0]},
        index=idx,
    )

    universe_df = _make_universe_frame(["USD_TIPS"])
    # Restrict the universe to a single bond so the empty-column
    # detection branch does not fire on the other 3 USD_TIPS bonds.
    universe_df = universe_df[universe_df["vendor_ticker"] == "GTII5 Govt"]

    with patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_panel_by_vendor_ticker",
        return_value=panel_df,
    ):
        result = build_linker_panel(engine=None, params=params, config=cfg)

    assert "error" not in result
    # 2024-01-06 was a Saturday → dropped by the default
    # business_days policy.
    assert result["row_count"] == 2
    assert result["methodology_card"]["calendar_policy"] == "business_days"


def test_build_linker_panel_calendar_policy_instrument_native_opt_in():
    """Explicit ``instrument_native`` opt-in preserves all observed
    dates verbatim (no business-day filter).  The
    desk-recommended override for cross-region full-universe
    queries that want every native session date through."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildLinkerPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_TIPS"],
        calendar_policy="instrument_native",
    )

    # Build a panel that includes a Saturday observation so we can
    # detect the absence of the business-day filter.
    idx = pd.DatetimeIndex(["2024-01-02", "2024-01-03", "2024-01-06"])
    panel_df = pd.DataFrame(
        {"GTII5 Govt": [1.0, 2.0, 3.0]},
        index=idx,
    )

    universe_df = _make_universe_frame(["USD_TIPS"])
    universe_df = universe_df[universe_df["vendor_ticker"] == "GTII5 Govt"]

    with patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_panel_by_vendor_ticker",
        return_value=panel_df,
    ):
        result = build_linker_panel(engine=None, params=params, config=cfg)

    assert "error" not in result
    assert result["row_count"] == 3  # Saturday preserved by opt-in
    assert result["methodology_card"]["calendar_policy"] == "instrument_native"


# ============================================================================
# 5. Missing-data policies
# ============================================================================


def test_build_linker_panel_missing_data_policy_raise_errors():
    """``missing_data_policy='raise'`` surfaces a controlled error
    when the panel has NaN cells after ffill."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildLinkerPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_TIPS"],
        missing_data_policy="raise",
    )

    idx = pd.bdate_range(start="2024-01-02", periods=5)
    panel_df = pd.DataFrame(
        {
            "GTII5 Govt": [1.0, 1.1, 1.2, 1.3, 1.4],
            "GTII10 Govt": [2.0, 2.1, None, 2.3, 2.4],
        },
        index=idx,
    )

    universe_df = _make_universe_frame(["USD_TIPS"])
    universe_df = universe_df[
        universe_df["vendor_ticker"].isin({"GTII5 Govt", "GTII10 Govt"})
    ]

    with patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_panel_by_vendor_ticker",
        return_value=panel_df,
    ):
        result = build_linker_panel(engine=None, params=params, config=cfg)

    assert "error" in result
    assert "missing_data_policy='raise'" in result["error"]


def test_build_linker_panel_missing_data_policy_drop_rows():
    """``drop_rows_any_missing`` shrinks the panel to fully-observed
    dates only."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildLinkerPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_TIPS"],
        missing_data_policy="drop_rows_any_missing",
    )

    idx = pd.bdate_range(start="2024-01-02", periods=5)
    panel_df = pd.DataFrame(
        {
            "GTII5 Govt": [1.0, 1.1, 1.2, 1.3, 1.4],
            "GTII10 Govt": [2.0, 2.1, None, 2.3, 2.4],
        },
        index=idx,
    )

    universe_df = _make_universe_frame(["USD_TIPS"])
    universe_df = universe_df[
        universe_df["vendor_ticker"].isin({"GTII5 Govt", "GTII10 Govt"})
    ]

    with patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_panel_by_vendor_ticker",
        return_value=panel_df,
    ):
        result = build_linker_panel(engine=None, params=params, config=cfg)

    assert "error" not in result
    # 5 input rows; the 3rd (10Y NaN) drops.
    assert result["row_count"] == 4


# ============================================================================
# 6. ffill_limit honoured (config-driven)
# ============================================================================


def test_build_linker_panel_ffill_limit_threaded_to_fetcher():
    """The YAML's ``ffill_limit_days`` value is passed to the
    fetcher AND surfaced on the methodology card."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildLinkerPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_TIPS"],
    )

    universe_df = _make_universe_frame(["USD_TIPS"])
    panel_df = _make_panel_frame(["USD_TIPS"])

    with patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_panel_by_vendor_ticker",
        return_value=panel_df,
    ) as fetch_mock:
        result = build_linker_panel(engine=None, params=params, config=cfg)

    assert "error" not in result
    assert fetch_mock.call_args.kwargs["ffill_limit_days"] == 5
    assert result["methodology_card"]["ffill_limit_days"] == 5


# ============================================================================
# 7. Empty-result error envelope
# ============================================================================


def test_build_linker_panel_empty_universe_returns_error():
    """Empty instrument_master surface yields a controlled error
    envelope, not an exception."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildLinkerPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_TIPS"],
    )

    with patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_universe",
        return_value=pd.DataFrame(),
    ):
        result = build_linker_panel(engine=None, params=params, config=cfg)

    assert "error" in result
    assert "instrument_master" in result["error"]


def test_build_linker_panel_empty_panel_returns_error():
    """Empty enriched-view fetch yields a controlled error
    envelope."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildLinkerPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_TIPS"],
    )

    universe_df = _make_universe_frame(["USD_TIPS"])

    with patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_panel_by_vendor_ticker",
        return_value=pd.DataFrame(),
    ):
        result = build_linker_panel(engine=None, params=params, config=cfg)

    assert "error" in result
    assert "No inflation-linker observations" in result["error"]


def test_build_linker_panel_fully_empty_column_fails():
    """A column populated only with NaN flags a structural bug, not
    a missing-data condition — surfaces the offending column."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildLinkerPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_TIPS"],
    )

    idx = pd.bdate_range(start="2024-01-02", periods=5)
    panel_df = pd.DataFrame(
        {
            "GTII5 Govt": [1.0, 1.1, 1.2, 1.3, 1.4],
            "GTII10 Govt": [None] * 5,
        },
        index=idx,
    )

    universe_df = _make_universe_frame(["USD_TIPS"])
    universe_df = universe_df[
        universe_df["vendor_ticker"].isin({"GTII5 Govt", "GTII10 Govt"})
    ]

    with patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_panel_by_vendor_ticker",
        return_value=panel_df,
    ):
        result = build_linker_panel(engine=None, params=params, config=cfg)

    assert "error" in result
    assert "GTII10 Govt" in result["error"]


# ============================================================================
# 8. Schema validation — closed-family curve_families
# ============================================================================


def test_build_linker_panel_rejects_non_linker_curve_family():
    """Non-linker curve family is refused at schema validation."""
    with pytest.raises(ValueError):
        BuildLinkerPanelInput(
            start_date=date(2024, 1, 2),
            curve_families=["UST"],  # sovereign — not a linker family
        )


def test_build_linker_panel_rejects_zcis_curve_family():
    """ZCIS curve families are refused at schema validation —
    use build_zcis_panel for those."""
    with pytest.raises(ValueError):
        BuildLinkerPanelInput(
            start_date=date(2024, 1, 2),
            curve_families=["USD_ZCIS"],
        )


def test_build_linker_panel_rejects_empty_curve_families_list():
    """Explicitly-empty ``curve_families=[]`` is refused at
    validation — caller passes ``None`` instead to mean "full
    universe"."""
    with pytest.raises(ValueError):
        BuildLinkerPanelInput(
            start_date=date(2024, 1, 2),
            curve_families=[],
        )


def test_build_linker_panel_rejects_end_before_start():
    """``end_date < start_date`` is refused at schema validation."""
    with pytest.raises(ValueError):
        BuildLinkerPanelInput(
            start_date=date(2024, 6, 1),
            end_date=date(2024, 1, 1),
        )


def test_build_linker_panel_rejects_duplicate_curve_families():
    """Duplicate curve_families are refused at schema validation."""
    with pytest.raises(ValueError):
        BuildLinkerPanelInput(
            start_date=date(2024, 1, 2),
            curve_families=["USD_TIPS", "USD_TIPS"],
        )


def test_build_linker_panel_input_forbids_tenors_knob():
    """PR8 — there is no per-query ``tenors`` knob on the input
    schema (linkers are specific-maturity bonds, not tenor-pillar
    swaps)."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        BuildLinkerPanelInput(
            start_date=date(2024, 1, 2),
            tenors=["5Y", "10Y"],  # not an accepted input
        )


# ============================================================================
# 9. None curve_families → resolved to full universe
# ============================================================================


def test_build_linker_panel_none_curve_families_resolved_to_full():
    """``curve_families=None`` resolves to the full live universe
    (USD_TIPS, GBP_LINKER, EUR_FR_LINKER, CAD_RRB)."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildLinkerPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
    )

    expected_curve_families = [
        "USD_TIPS", "GBP_LINKER", "EUR_FR_LINKER", "CAD_RRB",
    ]
    universe_df = _make_universe_frame(expected_curve_families)
    panel_df = _make_panel_frame(expected_curve_families)

    with patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_universe",
        return_value=universe_df,
    ) as universe_mock, patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_panel_by_vendor_ticker",
        return_value=panel_df,
    ) as fetch_mock:
        result = build_linker_panel(engine=None, params=params, config=cfg)

    assert "error" not in result
    assert universe_mock.call_args.kwargs["curve_families"] == expected_curve_families
    assert fetch_mock.call_args.kwargs["curve_families"] == expected_curve_families


# ============================================================================
# 10. Explicit config vs auto-load parity
# ============================================================================


def test_build_linker_panel_explicit_config_vs_auto_load_parity():
    """Passing ``config=None`` and passing
    ``config=load_tool_config(CONFIG_PATH)`` produce identical
    results — exercises the auto-load fallback in
    ``build_linker_panel``."""
    explicit = load_tool_config(CONFIG_PATH)
    params = BuildLinkerPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_TIPS"],
    )

    universe_df = _make_universe_frame(["USD_TIPS"])
    panel_df = _make_panel_frame(["USD_TIPS"])

    with patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_indexed_bonds.tools.build_linker_panel.compute.fetch_linker_panel_by_vendor_ticker",
        return_value=panel_df,
    ):
        result_explicit = build_linker_panel(
            engine=None, params=params, config=explicit,
        )
        result_auto = build_linker_panel(
            engine=None, params=params, config=None,
        )

    # Drop the typed Panel artifact for the comparison — pandas
    # equality on the embedded DataFrame is awkward via ==.
    for r in (result_explicit, result_auto):
        r.pop("panel", None)
    assert result_explicit == result_auto


# ============================================================================
# 11. Calendar-policy invalid value
# ============================================================================


def test_build_linker_panel_rejects_unknown_calendar_policy_via_schema():
    """Calendar-policy override outside the closed-enum is refused
    at schema validation (the runtime branch in compute.py is a
    defensive belt + braces on top)."""
    with pytest.raises(ValueError):
        BuildLinkerPanelInput(
            start_date=date(2024, 1, 2),
            calendar_policy="weird_calendar",  # not in the Literal
        )
