"""tests/test_build_zcis_panel_compute.py

Offline deterministic compute tests for the ``build_zcis_panel``
primitive (Plan §5 Group 3 #19).  The shared fetcher
(``fetch_inflation_swap_panel_by_vendor_ticker``) and the
universe-metadata helper (``fetch_inflation_swap_universe``) are
monkeypatched at the compute module's import site so the tests do
not require a live DB; the SQL-parity validator
(``test_build_zcis_panel_sql_validation.py``) covers the DB-grounded
layer separately.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from rates_agent.inflation_swaps.tools.build_zcis_panel import (
    CONFIG_PATH,
    BuildZcisPanelInput,
    build_zcis_panel,
)
from rates_agent.inflation_swaps.tools.build_zcis_panel.schemas import (
    BuildZcisPanelOutput,
)
from shared.artifacts.types import Panel
from shared.config import load_tool_config


# ============================================================================
# Test fixtures — synthetic ZCIS universe (3 curves × 7 tenors = 21
# instruments, matching the live playbook universe)
# ============================================================================


_TENORS_FULL = ("1Y", "2Y", "3Y", "5Y", "10Y", "20Y", "30Y")

_TICKER_BY_CURVE_TENOR = {
    "USD_ZCIS": {tnr: f"USSWIT{tnr.rstrip('Y')} Curncy" for tnr in _TENORS_FULL},
    "EUR_ZCIS": {tnr: f"EUSWI{tnr.rstrip('Y')} Curncy" for tnr in _TENORS_FULL},
    "GBP_ZCIS": {tnr: f"BPSWIT{tnr.rstrip('Y')} Curncy" for tnr in _TENORS_FULL},
}

_INDEX_REF_BY_CURVE = {
    "USD_ZCIS": {
        "inflation_index_family": "US_CPI_URBAN",
        "index_lag": "3M",
        "interpolation": "Daily",
        "underlying_index": "CPURNSA Index",
    },
    "EUR_ZCIS": {
        "inflation_index_family": "EU_HICP",
        "index_lag": "3M",
        "interpolation": "Monthly",
        "underlying_index": "CPTFEMU Index",
    },
    "GBP_ZCIS": {
        "inflation_index_family": "UK_RPI",
        "index_lag": "2M",
        "interpolation": "Monthly",
        "underlying_index": "UKRPI Index",
    },
}


def _make_universe_frame(
    curve_families,
    tenors=_TENORS_FULL,
):
    """Build a synthetic ``fetch_inflation_swap_universe`` return."""
    rows = []
    for cf in curve_families:
        for tnr in tenors:
            ticker = _TICKER_BY_CURVE_TENOR[cf][tnr]
            ref = _INDEX_REF_BY_CURVE[cf]
            rows.append(
                {
                    "curve_family": cf,
                    "tenor": tnr,
                    "vendor_ticker": ticker,
                    "underlying_index": ref["underlying_index"],
                    "maturity_date": date(2030, 1, 1),
                    "pricing_type": "zero_coupon_breakeven",
                    "inflation_index_family": ref["inflation_index_family"],
                    "index_lag": ref["index_lag"],
                    "interpolation": ref["interpolation"],
                }
            )
    return pd.DataFrame(rows)


def _make_panel_frame(
    curve_families,
    tenors=_TENORS_FULL,
    *,
    start=date(2024, 1, 1),
    n_business_days=40,
    constant_per_column=None,
):
    """Build a synthetic deterministic wide ZCIS panel.

    Column order matches the (curve_family, tenor_year, vendor_ticker)
    sort the production fetcher produces.
    """
    idx = pd.bdate_range(start=start, periods=n_business_days)
    # Mirror the production fetcher's column-order rule.
    ordered_columns = []
    for cf in curve_families:
        for tnr in tenors:
            ordered_columns.append(_TICKER_BY_CURVE_TENOR[cf][tnr])
    data = {}
    for col_idx, col in enumerate(ordered_columns):
        if constant_per_column is not None:
            data[col] = [float(constant_per_column.get(col, col_idx))] * len(idx)
        else:
            # Deterministic spread so every column is distinguishable.
            data[col] = [
                float(col_idx) + 2.0 + 0.001 * row_idx
                for row_idx in range(len(idx))
            ]
    df = pd.DataFrame(data, index=idx, columns=ordered_columns)
    df.index = pd.DatetimeIndex(df.index)
    return df


# ============================================================================
# 1. Default-config happy path on the full universe
# ============================================================================


def test_build_zcis_panel_default_full_universe():
    """Full ZCIS universe with default conventions returns a Panel
    over all 21 columns with PERCENT units and a populated
    methodology card."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildZcisPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 2, 28),
    )

    expected_curve_families = ["USD_ZCIS", "EUR_ZCIS", "GBP_ZCIS"]
    universe_df = _make_universe_frame(expected_curve_families)
    panel_df = _make_panel_frame(expected_curve_families)

    with patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_panel_by_vendor_ticker",
        return_value=panel_df,
    ):
        result = build_zcis_panel(engine=None, params=params, config=cfg)

    assert "error" not in result, f"unexpected error envelope: {result}"

    # PR14 frozen wire fields present.
    for key in (
        "start_date",
        "end_date",
        "row_count",
        "column_count",
        "curve_families",
        "tenors",
        "vendor_tickers",
        "units_by_column",
        "methodology_card",
        "panel",
    ):
        assert key in result, f"missing wire field {key!r}"

    assert result["column_count"] == 21
    # Index has 40 business days but the input was [2024-01-02, 2024-02-28]
    # — the universe panel is 40 business days from 2024-01-01;
    # business_days policy filters to dayofweek<5 (passthrough here).
    assert result["row_count"] == len(panel_df)
    assert result["curve_families"] == expected_curve_families
    assert result["tenors"] == list(_TENORS_FULL)
    # First three columns are USD_ZCIS in tenor-year-fraction order.
    assert result["vendor_tickers"][:3] == [
        "USSWIT1 Curncy",
        "USSWIT2 Curncy",
        "USSWIT3 Curncy",
    ]
    assert result["units_by_column"]["USSWIT1 Curncy"] == "percent"

    card = result["methodology_card"]
    for key in (
        "field_name",
        "calendar_policy",
        "missing_data_policy",
        "ffill_limit_days",
        "ffill_source_tag",
        "curve_families",
        "tenors",
        "index_family_caveat",
        "security_name_caveat",
        "methodology_label",
        "curve_family_reference",
    ):
        assert key in card, f"methodology_card missing {key!r}"
    assert card["field_name"] == "PX_MID"
    assert card["calendar_policy"] == "business_days"
    assert card["missing_data_policy"] == "forward_fill_only"
    assert card["ffill_limit_days"] == 5
    assert card["ffill_source_tag"] == "team_judgment_pending_review"
    # USD reference metadata flows onto the methodology card.
    assert card["curve_family_reference"]["USD_ZCIS"][
        "inflation_index_family"
    ] == "US_CPI_URBAN"
    assert "vendor_ticker" in card["security_name_caveat"]
    assert "security_name" in card["security_name_caveat"]
    assert "NULL" in card["security_name_caveat"]

    # The Panel artifact round-trips through the schema.
    validated = BuildZcisPanelOutput.model_validate(result)
    assert isinstance(validated.panel, Panel)
    assert list(validated.panel.payload.columns) == list(
        panel_df.columns
    )


# ============================================================================
# 2. Curve-family scoping
# ============================================================================


def test_build_zcis_panel_curve_family_scope_usd_only():
    """USD-only scope returns 7 columns (1Y..30Y) and the methodology
    card surfaces only the USD curve-family reference."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildZcisPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 2, 28),
        curve_families=["USD_ZCIS"],
    )

    universe_df = _make_universe_frame(["USD_ZCIS"])
    panel_df = _make_panel_frame(["USD_ZCIS"])

    with patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_panel_by_vendor_ticker",
        return_value=panel_df,
    ):
        result = build_zcis_panel(engine=None, params=params, config=cfg)

    assert "error" not in result
    assert result["column_count"] == 7
    assert result["curve_families"] == ["USD_ZCIS"]
    assert all(t.startswith("USSWIT") for t in result["vendor_tickers"])
    assert list(result["methodology_card"]["curve_family_reference"].keys()) == [
        "USD_ZCIS"
    ]


# ============================================================================
# 3. Tenor scoping
# ============================================================================


def test_build_zcis_panel_tenor_scope_front_end():
    """Tenor scoping limits the panel to the requested tenors only."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildZcisPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 2, 28),
        curve_families=["USD_ZCIS", "EUR_ZCIS"],
        tenors=["1Y", "5Y", "10Y"],
    )

    universe_df = _make_universe_frame(["USD_ZCIS", "EUR_ZCIS"])

    # The production fetcher would have already filtered to the
    # tenors; reproduce that here by passing only the requested
    # tenors to ``_make_panel_frame``.
    panel_df = _make_panel_frame(
        ["USD_ZCIS", "EUR_ZCIS"],
        tenors=("1Y", "5Y", "10Y"),
    )

    with patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_panel_by_vendor_ticker",
        return_value=panel_df,
    ) as fetch_mock:
        result = build_zcis_panel(engine=None, params=params, config=cfg)

    assert "error" not in result
    assert result["column_count"] == 6  # 2 curves × 3 tenors
    assert result["tenors"] == ["1Y", "5Y", "10Y"]

    # Production fetcher invocation got the resolved tenor scope.
    fetch_call = fetch_mock.call_args
    assert fetch_call.kwargs["tenors"] == ["1Y", "5Y", "10Y"]
    assert sorted(fetch_call.kwargs["curve_families"]) == [
        "EUR_ZCIS",
        "USD_ZCIS",
    ]


# ============================================================================
# 4. Field-name override
# ============================================================================


def test_build_zcis_panel_field_name_override():
    """Explicit field_name override flows through to the fetcher."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildZcisPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_ZCIS"],
        tenors=["5Y", "10Y"],
        field_name="PX_BID",
    )

    universe_df = _make_universe_frame(["USD_ZCIS"], tenors=("5Y", "10Y"))
    panel_df = _make_panel_frame(["USD_ZCIS"], tenors=("5Y", "10Y"))

    with patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_panel_by_vendor_ticker",
        return_value=panel_df,
    ) as fetch_mock:
        result = build_zcis_panel(engine=None, params=params, config=cfg)

    assert "error" not in result
    assert fetch_mock.call_args.kwargs["field_name"] == "PX_BID"
    assert result["methodology_card"]["field_name"] == "PX_BID"


# ============================================================================
# 5. Calendar policy
# ============================================================================


def test_build_zcis_panel_calendar_policy_instrument_native():
    """``instrument_native`` calendar policy preserves all observed
    dates (no business-day filter)."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildZcisPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_ZCIS"],
        tenors=["5Y"],
        calendar_policy="instrument_native",
    )

    # Build a panel that includes a Saturday observation so we can
    # detect the absence of the business-day filter.
    idx = pd.DatetimeIndex(["2024-01-02", "2024-01-03", "2024-01-06"])
    panel_df = pd.DataFrame(
        {"USSWIT5 Curncy": [1.0, 2.0, 3.0]},
        index=idx,
    )

    universe_df = _make_universe_frame(["USD_ZCIS"], tenors=("5Y",))

    with patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_panel_by_vendor_ticker",
        return_value=panel_df,
    ):
        result = build_zcis_panel(engine=None, params=params, config=cfg)

    assert "error" not in result
    assert result["row_count"] == 3  # Saturday preserved
    assert result["methodology_card"]["calendar_policy"] == "instrument_native"


def test_build_zcis_panel_calendar_policy_business_days_filters_weekends():
    """``business_days`` calendar policy drops Saturday rows."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildZcisPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_ZCIS"],
        tenors=["5Y"],
        calendar_policy="business_days",
    )

    idx = pd.DatetimeIndex(["2024-01-02", "2024-01-06", "2024-01-08"])
    panel_df = pd.DataFrame(
        {"USSWIT5 Curncy": [1.0, 2.0, 3.0]},
        index=idx,
    )

    universe_df = _make_universe_frame(["USD_ZCIS"], tenors=("5Y",))

    with patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_panel_by_vendor_ticker",
        return_value=panel_df,
    ):
        result = build_zcis_panel(engine=None, params=params, config=cfg)

    assert "error" not in result
    # 2024-01-06 was a Saturday → dropped.
    assert result["row_count"] == 2


# ============================================================================
# 6. Missing-data policies
# ============================================================================


def test_build_zcis_panel_missing_data_policy_raise_errors():
    """``missing_data_policy='raise'`` surfaces a controlled error
    when the panel has NaN cells after ffill."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildZcisPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_ZCIS"],
        tenors=["5Y", "10Y"],
        missing_data_policy="raise",
    )

    idx = pd.bdate_range(start="2024-01-02", periods=5)
    panel_df = pd.DataFrame(
        {
            "USSWIT5 Curncy": [1.0, 1.1, 1.2, 1.3, 1.4],
            "USSWIT10 Curncy": [2.0, 2.1, None, 2.3, 2.4],
        },
        index=idx,
    )

    universe_df = _make_universe_frame(["USD_ZCIS"], tenors=("5Y", "10Y"))

    with patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_panel_by_vendor_ticker",
        return_value=panel_df,
    ):
        result = build_zcis_panel(engine=None, params=params, config=cfg)

    assert "error" in result
    assert "missing_data_policy='raise'" in result["error"]


def test_build_zcis_panel_missing_data_policy_drop_rows():
    """``drop_rows_any_missing`` shrinks the panel to fully-observed
    dates only."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildZcisPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_ZCIS"],
        tenors=["5Y", "10Y"],
        missing_data_policy="drop_rows_any_missing",
    )

    idx = pd.bdate_range(start="2024-01-02", periods=5)
    panel_df = pd.DataFrame(
        {
            "USSWIT5 Curncy": [1.0, 1.1, 1.2, 1.3, 1.4],
            "USSWIT10 Curncy": [2.0, 2.1, None, 2.3, 2.4],
        },
        index=idx,
    )

    universe_df = _make_universe_frame(["USD_ZCIS"], tenors=("5Y", "10Y"))

    with patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_panel_by_vendor_ticker",
        return_value=panel_df,
    ):
        result = build_zcis_panel(engine=None, params=params, config=cfg)

    assert "error" not in result
    # 5 input rows; the 3rd (10Y NaN) drops.
    assert result["row_count"] == 4


# ============================================================================
# 7. ffill_limit honoured (config-driven)
# ============================================================================


def test_build_zcis_panel_ffill_limit_threaded_to_fetcher():
    """The YAML's ``ffill_limit_days`` value is passed to the
    fetcher AND surfaced on the methodology card."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildZcisPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_ZCIS"],
        tenors=["5Y"],
    )

    universe_df = _make_universe_frame(["USD_ZCIS"], tenors=("5Y",))
    panel_df = _make_panel_frame(["USD_ZCIS"], tenors=("5Y",))

    with patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_panel_by_vendor_ticker",
        return_value=panel_df,
    ) as fetch_mock:
        result = build_zcis_panel(engine=None, params=params, config=cfg)

    assert "error" not in result
    assert fetch_mock.call_args.kwargs["ffill_limit_days"] == 5
    assert result["methodology_card"]["ffill_limit_days"] == 5


# ============================================================================
# 8. Empty-result error envelope
# ============================================================================


def test_build_zcis_panel_empty_universe_returns_error():
    """Empty instrument_master surface yields a controlled error
    envelope, not an exception."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildZcisPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_ZCIS"],
    )

    with patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_universe",
        return_value=pd.DataFrame(),
    ):
        result = build_zcis_panel(engine=None, params=params, config=cfg)

    assert "error" in result
    assert "instrument_master" in result["error"]


def test_build_zcis_panel_empty_panel_returns_error():
    """Empty enriched-view fetch yields a controlled error
    envelope."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildZcisPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_ZCIS"],
    )

    universe_df = _make_universe_frame(["USD_ZCIS"])

    with patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_panel_by_vendor_ticker",
        return_value=pd.DataFrame(),
    ):
        result = build_zcis_panel(engine=None, params=params, config=cfg)

    assert "error" in result
    assert "No ZCIS observations" in result["error"]


def test_build_zcis_panel_fully_empty_column_fails():
    """A column populated only with NaN flags a structural bug, not
    a missing-data condition — surfaces the offending column."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildZcisPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_ZCIS"],
        tenors=["5Y", "10Y"],
    )

    idx = pd.bdate_range(start="2024-01-02", periods=5)
    panel_df = pd.DataFrame(
        {
            "USSWIT5 Curncy": [1.0, 1.1, 1.2, 1.3, 1.4],
            "USSWIT10 Curncy": [None] * 5,
        },
        index=idx,
    )

    universe_df = _make_universe_frame(["USD_ZCIS"], tenors=("5Y", "10Y"))

    with patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_panel_by_vendor_ticker",
        return_value=panel_df,
    ):
        result = build_zcis_panel(engine=None, params=params, config=cfg)

    assert "error" in result
    assert "USSWIT10 Curncy" in result["error"]


# ============================================================================
# 9. Schema validation — closed-family curve_families
# ============================================================================


def test_build_zcis_panel_rejects_non_zcis_curve_family():
    """Non-ZCIS curve family is refused at schema validation."""
    with pytest.raises(ValueError):
        BuildZcisPanelInput(
            start_date=date(2024, 1, 2),
            curve_families=["UST"],  # sovereign — not a ZCIS family
        )


def test_build_zcis_panel_rejects_empty_curve_families_list():
    """Explicitly-empty ``curve_families=[]`` is refused at
    validation — caller passes ``None`` instead to mean "full
    universe"."""
    with pytest.raises(ValueError):
        BuildZcisPanelInput(
            start_date=date(2024, 1, 2),
            curve_families=[],
        )


def test_build_zcis_panel_rejects_end_before_start():
    """``end_date < start_date`` is refused at schema validation."""
    with pytest.raises(ValueError):
        BuildZcisPanelInput(
            start_date=date(2024, 6, 1),
            end_date=date(2024, 1, 1),
        )


def test_build_zcis_panel_rejects_duplicate_tenors():
    """Duplicate tenors are refused at schema validation."""
    with pytest.raises(ValueError):
        BuildZcisPanelInput(
            start_date=date(2024, 1, 2),
            tenors=["5Y", "5Y"],
        )


# ============================================================================
# 10. None tenors → resolved from live universe
# ============================================================================


def test_build_zcis_panel_none_tenors_resolved_from_universe():
    """``tenors=None`` resolves to every tenor present in the live
    universe (here: 1Y / 5Y / 10Y in the mocked universe meta)."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildZcisPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_ZCIS"],
    )

    # Synthesise a universe that only carries 1Y / 5Y / 10Y so we
    # can prove the resolution comes from the live frame.
    universe_df = _make_universe_frame(["USD_ZCIS"], tenors=("1Y", "5Y", "10Y"))
    panel_df = _make_panel_frame(["USD_ZCIS"], tenors=("1Y", "5Y", "10Y"))

    with patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_panel_by_vendor_ticker",
        return_value=panel_df,
    ):
        result = build_zcis_panel(engine=None, params=params, config=cfg)

    assert "error" not in result
    assert result["tenors"] == ["1Y", "5Y", "10Y"]
    assert result["column_count"] == 3


# ============================================================================
# 11. Tenor mismatch — none of the requested tenors exist
# ============================================================================


def test_build_zcis_panel_no_matching_tenors_returns_error():
    """When the requested tenors do not exist on the resolved
    universe, a controlled error envelope surfaces the offending
    set."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildZcisPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_ZCIS"],
        tenors=["7Y", "15Y"],  # not in the V1 grid
    )

    universe_df = _make_universe_frame(["USD_ZCIS"])

    with patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_universe",
        return_value=universe_df,
    ):
        result = build_zcis_panel(engine=None, params=params, config=cfg)

    assert "error" in result
    assert "7Y" in result["error"] and "15Y" in result["error"]


# ============================================================================
# 12. Explicit config vs auto-load parity
# ============================================================================


def test_build_zcis_panel_explicit_config_vs_auto_load_parity():
    """Passing ``config=None`` and passing ``config=load_tool_config(CONFIG_PATH)``
    produce identical results — exercises the auto-load fallback in
    ``build_zcis_panel``."""
    explicit = load_tool_config(CONFIG_PATH)
    params = BuildZcisPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["USD_ZCIS"],
        tenors=["5Y"],
    )

    universe_df = _make_universe_frame(["USD_ZCIS"], tenors=("5Y",))
    panel_df = _make_panel_frame(["USD_ZCIS"], tenors=("5Y",))

    with patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_universe",
        return_value=universe_df,
    ), patch(
        "rates_agent.inflation_swaps.tools.build_zcis_panel.compute.fetch_inflation_swap_panel_by_vendor_ticker",
        return_value=panel_df,
    ):
        result_explicit = build_zcis_panel(
            engine=None, params=params, config=explicit,
        )
        result_auto = build_zcis_panel(
            engine=None, params=params, config=None,
        )

    # Drop the typed Panel artifact for the comparison — pandas
    # equality on the embedded DataFrame is awkward via ==.
    for r in (result_explicit, result_auto):
        r.pop("panel", None)
    assert result_explicit == result_auto
