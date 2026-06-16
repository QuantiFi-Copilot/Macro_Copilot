"""tests/test_build_policy_futures_strip_panel_compute.py

Offline deterministic compute tests for the
``build_policy_futures_strip_panel`` primitive (Plan §5 Group 3
#21).  The shared fetcher helper
(``fetch_policy_futures_strip_panel``) and the universe-metadata
helper (``fetch_policy_futures_strip_universe``) are monkeypatched
at the compute module's import site so the tests do not require a
live DB; the SQL-parity validator
(``test_build_policy_futures_strip_panel_sql_validation.py``)
covers the DB-grounded layer separately.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, List, Optional
from unittest.mock import patch

import pandas as pd
import pytest

from rates_agent.policy_futures.tools.build_policy_futures_strip_panel import (
    CONFIG_PATH,
    BuildPolicyFuturesStripPanelInput,
    BuildPolicyFuturesStripPanelOutput,
    build_policy_futures_strip_panel,
)
from shared.analytics.panel_assembly import _strip_panel_column_key
from shared.artifacts.types import Panel
from shared.config import load_tool_config


_COMPUTE_PATCH_PATH = (
    "rates_agent.policy_futures.tools.build_policy_futures_strip_panel.compute"
)


_CURVE_FAMILY_DEFAULTS = [
    ("SOFR_FUT", "US", "USD", "SFR"),
    ("SONIA_FUT", "UK", "GBP", "SFI"),
    ("EUR_SHORT_RATE_FUT", "Euro area", "EUR", "ER"),
]


def _expected_column_keys(
    curve_families: Iterable[str],
    strip_positions: Iterable[int],
) -> List[str]:
    """Replicate the production helper's Cartesian column-axis
    ordering: caller-supplied curve_families order × strip_positions
    ascending."""
    positions = sorted(int(p) for p in strip_positions)
    return [
        _strip_panel_column_key(cf, pos)
        for cf in curve_families
        for pos in positions
    ]


def _make_universe_frame(
    curve_families: Iterable[str],
    strip_positions: Iterable[int],
    *,
    inverse_pricing: bool = True,
) -> pd.DataFrame:
    """Build a synthetic ``fetch_policy_futures_strip_universe``
    return.  Every cell carries ``inverse_pricing=True`` (matches the
    live V1 playbook universe) unless overridden."""
    rows = []
    for cf, country, currency, stem in [
        c for c in _CURVE_FAMILY_DEFAULTS if c[0] in set(curve_families)
    ]:
        for pos in sorted(int(p) for p in strip_positions):
            rows.append(
                {
                    "curve_family": cf,
                    "strip_position": pos,
                    "vendor_ticker": f"{stem}{pos} Comdty",
                    "contract_code": f"{stem}{pos}",
                    "country": country,
                    "currency": currency,
                    "inverse_pricing": inverse_pricing,
                }
            )
    return pd.DataFrame(rows)


def _make_panel_frame(
    curve_families: Iterable[str],
    strip_positions: Iterable[int],
    *,
    start: date = date(2024, 1, 2),
    n_business_days: int = 40,
    raw_price_base: float = 95.0,
) -> pd.DataFrame:
    """Build a synthetic deterministic wide policy-futures panel of
    RAW PRICES (compute applies the inverse-pricing conversion to
    implied_rate_pct).  Column order matches the production helper's
    Cartesian (curve_family × strip_position) sort."""
    idx = pd.bdate_range(start=start, periods=n_business_days)
    ordered_columns = _expected_column_keys(curve_families, strip_positions)
    data = {}
    for col_idx, col in enumerate(ordered_columns):
        # Deterministic spread so every column is distinguishable.
        # ``raw_price_base + col_idx * 0.1`` keeps every cell in the
        # 94..97 price range (typical SFR / ER / SFI quotes).
        data[col] = [
            raw_price_base + col_idx * 0.1 + 0.001 * row_idx
            for row_idx in range(len(idx))
        ]
    df = pd.DataFrame(data, index=idx, columns=ordered_columns)
    df.index = pd.DatetimeIndex(df.index)
    return df


# ============================================================================
# 1. Default-config happy path on the full universe
# ============================================================================


def test_build_policy_futures_strip_panel_default_full_universe():
    """Full policy-futures universe with default conventions returns
    a Panel over 24 columns (3 curve_families × 8 strip positions)
    with PERCENT units and a populated methodology card."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildPolicyFuturesStripPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 2, 28),
    )

    expected_curve_families = [
        "SOFR_FUT", "SONIA_FUT", "EUR_SHORT_RATE_FUT",
    ]
    expected_positions = [1, 2, 3, 4, 5, 6, 7, 8]

    universe_df = _make_universe_frame(
        expected_curve_families, expected_positions,
    )
    panel_df = _make_panel_frame(
        expected_curve_families, expected_positions,
    )

    with patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_panel",
        return_value=(panel_df, universe_df),
    ), patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_universe",
        return_value=universe_df,
    ):
        result = build_policy_futures_strip_panel(
            engine=None, params=params, config=cfg,
        )

    assert "error" not in result, f"unexpected error envelope: {result}"

    # PR14 frozen wire fields present.
    for key in (
        "start_date",
        "end_date",
        "row_count",
        "column_count",
        "curve_families",
        "strip_positions",
        "column_keys",
        "units_by_column",
        "methodology_card",
        "panel",
    ):
        assert key in result, f"missing wire field {key!r}"

    # 3 curve_families × 8 strip positions = 24 cells.
    assert result["column_count"] == 24
    assert result["row_count"] == len(panel_df)
    assert result["curve_families"] == expected_curve_families
    assert result["strip_positions"] == expected_positions

    # Cartesian column-axis ordering: caller-input curve_families ×
    # ascending strip_positions.
    expected_keys = _expected_column_keys(
        expected_curve_families, expected_positions,
    )
    assert result["column_keys"] == expected_keys
    # First column is SOFR_FUT|1 (front SOFR).
    assert result["column_keys"][0] == "SOFR_FUT|1"
    # Every column gets percent units.
    assert set(result["units_by_column"].values()) == {"percent"}
    for col in expected_keys:
        assert result["units_by_column"][col] == "percent"

    card = result["methodology_card"]
    for key in (
        "field_name",
        "panel_value_field",
        "calendar_policy",
        "missing_data_policy",
        "ffill_limit_days",
        "ffill_source_tag",
        "curve_families",
        "strip_positions",
        "column_axis_encoding",
        "column_axis_encoding_separator",
        "inverse_pricing_handling",
        "cross_region_business_days_caveat",
        "rolling_generic_strip_caveat",
        "curve_family_reference",
        "methodology_label",
    ):
        assert key in card, f"methodology_card missing {key!r}"
    assert card["field_name"] == "PX_LAST"
    assert card["panel_value_field"] == "implied_rate_pct"
    assert card["calendar_policy"] == "business_days"
    assert card["missing_data_policy"] == "forward_fill_only"
    assert card["ffill_limit_days"] == 5
    # ffill_source_tag is read straight from the config's
    # ``ffill_limit_days`` convention ``source`` (see compute.py).  That
    # tag is ``team_judgment_pending_review`` — the registered debt tag
    # carried IDENTICALLY across all rates tools for PR13 cross-config
    # consistency (the 5-day ffill is a code-review judgment, not an
    # industry standard).  The prior assertion against the never-
    # registered ``industry_standard_5d_ffill`` was a stale red (M14).
    assert card["ffill_source_tag"] == "team_judgment_pending_review"
    assert card["column_axis_encoding_separator"] == "|"
    assert "100 - raw_price" in card["inverse_pricing_handling"]

    # Per-curve-family reference block.
    cf_ref = card["curve_family_reference"]
    assert set(cf_ref.keys()) == set(expected_curve_families)
    assert cf_ref["SOFR_FUT"]["short_rate_regime"] == "RFR"
    assert cf_ref["SONIA_FUT"]["short_rate_regime"] == "RFR"
    assert cf_ref["EUR_SHORT_RATE_FUT"]["short_rate_regime"] == "IBOR"
    assert cf_ref["SOFR_FUT"]["inverse_pricing"] is True
    assert cf_ref["SONIA_FUT"]["inverse_pricing"] is True
    assert cf_ref["EUR_SHORT_RATE_FUT"]["inverse_pricing"] is True
    for cf in expected_curve_families:
        assert cf_ref[cf]["cell_count"] == 8

    # The Euribor (EUR_SHORT_RATE_FUT) block discloses the Buba mix
    # AND cites the futures_pack_average_simple PR11 unblock path.
    eur_block = cf_ref["EUR_SHORT_RATE_FUT"]
    assert "buba_mix_caveat" in eur_block
    assert "delivery_month_type" in eur_block["buba_mix_caveat"]
    assert "futures_pack_average_simple" in eur_block["buba_mix_caveat"]

    # The SOFR_FUT regime caveat names SOFR + RFR; the EUR caveat
    # names Euribor + IBOR.
    assert "SOFR" in cf_ref["SOFR_FUT"]["regime_caveat"]
    assert "IBOR" in cf_ref["EUR_SHORT_RATE_FUT"]["regime_caveat"]
    assert "RFR" in cf_ref["SONIA_FUT"]["regime_caveat"]

    # Per-cell decomposition rows expose column_key / curve_family /
    # strip_position / vendor_ticker / inverse_pricing.
    sfr_cells = cf_ref["SOFR_FUT"]["cells"]
    assert len(sfr_cells) == 8
    assert {c["strip_position"] for c in sfr_cells} == set(range(1, 9))
    for cell in sfr_cells:
        assert cell["column_key"].startswith("SOFR_FUT|")
        assert cell["curve_family"] == "SOFR_FUT"
        assert cell["country"] == "US"
        assert cell["currency"] == "USD"
        assert cell["inverse_pricing"] is True

    # The Panel artifact round-trips through the schema.
    validated = BuildPolicyFuturesStripPanelOutput.model_validate(result)
    assert isinstance(validated.panel, Panel)
    assert list(validated.panel.payload.columns) == expected_keys

    # Implied-rate conversion: every cell is in PERCENT space
    # (95.x raw → 4.x percent).  Inverse-priced flag is True
    # across the universe so implied_rate_pct = 100 - raw_price.
    payload = validated.panel.payload
    for col in payload.columns:
        last_implied = float(payload[col].iloc[-1])
        # Reasonable bound for implied rates in the synthetic
        # fixture (raw prices live in [94.0, 97.5] so implied
        # rates live in [2.5, 6.0]).
        assert 2.0 <= last_implied <= 6.5, (
            f"implied_rate_pct out of expected synthetic range on "
            f"col {col}: {last_implied}"
        )


# ============================================================================
# 2. Curve-family scoping
# ============================================================================


def test_build_policy_futures_strip_panel_curve_family_scope_sofr_only():
    """SOFR_FUT-only scope returns 8 columns (one per strip
    position) and the methodology card surfaces only the SOFR_FUT
    curve-family reference."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildPolicyFuturesStripPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 2, 28),
        curve_families=["SOFR_FUT"],
    )

    universe_df = _make_universe_frame(["SOFR_FUT"], list(range(1, 9)))
    panel_df = _make_panel_frame(["SOFR_FUT"], list(range(1, 9)))

    with patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_panel",
        return_value=(panel_df, universe_df),
    ), patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_universe",
        return_value=universe_df,
    ):
        result = build_policy_futures_strip_panel(
            engine=None, params=params, config=cfg,
        )

    assert "error" not in result
    assert result["column_count"] == 8
    assert result["curve_families"] == ["SOFR_FUT"]
    assert all(c.startswith("SOFR_FUT|") for c in result["column_keys"])
    assert list(
        result["methodology_card"]["curve_family_reference"].keys()
    ) == ["SOFR_FUT"]


def test_build_policy_futures_strip_panel_curve_family_scope_sofr_eur():
    """SOFR + EUR scope (RFR + IBOR side-by-side) preserves the
    EUR_SHORT_RATE_FUT raw Buba mix without refusing or averaging."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildPolicyFuturesStripPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 2, 28),
        curve_families=["SOFR_FUT", "EUR_SHORT_RATE_FUT"],
    )

    universe_df = _make_universe_frame(
        ["SOFR_FUT", "EUR_SHORT_RATE_FUT"], list(range(1, 9)),
    )
    panel_df = _make_panel_frame(
        ["SOFR_FUT", "EUR_SHORT_RATE_FUT"], list(range(1, 9)),
    )

    with patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_panel",
        return_value=(panel_df, universe_df),
    ) as fetch_mock, patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_universe",
        return_value=universe_df,
    ):
        result = build_policy_futures_strip_panel(
            engine=None, params=params, config=cfg,
        )

    assert "error" not in result
    assert result["column_count"] == 16  # 8 + 8
    assert result["curve_families"] == ["SOFR_FUT", "EUR_SHORT_RATE_FUT"]

    # Production fetcher got the resolved curve_family scope.
    fetch_call = fetch_mock.call_args
    assert list(fetch_call.kwargs["curve_families"]) == [
        "SOFR_FUT", "EUR_SHORT_RATE_FUT",
    ]

    # EUR_SHORT_RATE_FUT raw rows are PRESERVED in the panel.
    cf_ref = result["methodology_card"]["curve_family_reference"]
    assert "EUR_SHORT_RATE_FUT" in cf_ref
    assert cf_ref["EUR_SHORT_RATE_FUT"]["cell_count"] == 8
    assert "buba_mix_caveat" in cf_ref["EUR_SHORT_RATE_FUT"]


# ============================================================================
# 3. Strip-position scoping
# ============================================================================


def test_build_policy_futures_strip_panel_strip_position_scope_whites_only():
    """strip_positions=[1,2,3,4] (whites-only) returns 12 columns
    (3 curve_families × 4 positions)."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildPolicyFuturesStripPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 2, 28),
        strip_positions=[1, 2, 3, 4],
    )

    expected_cfs = ["SOFR_FUT", "SONIA_FUT", "EUR_SHORT_RATE_FUT"]
    universe_df = _make_universe_frame(expected_cfs, [1, 2, 3, 4])
    panel_df = _make_panel_frame(expected_cfs, [1, 2, 3, 4])

    with patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_panel",
        return_value=(panel_df, universe_df),
    ) as fetch_mock, patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_universe",
        return_value=universe_df,
    ):
        result = build_policy_futures_strip_panel(
            engine=None, params=params, config=cfg,
        )

    assert "error" not in result
    assert result["column_count"] == 12  # 3 × 4
    assert result["strip_positions"] == [1, 2, 3, 4]
    assert all(
        int(c.split("|", 1)[1]) in {1, 2, 3, 4}
        for c in result["column_keys"]
    )
    # Production fetcher got the resolved strip_positions scope.
    fetch_call = fetch_mock.call_args
    assert list(fetch_call.kwargs["strip_positions"]) == [1, 2, 3, 4]


# ============================================================================
# 4. Field-name override
# ============================================================================


def test_build_policy_futures_strip_panel_field_name_override():
    """Explicit field_name override flows through to the fetcher
    AND to the methodology card's ``field_name`` field."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildPolicyFuturesStripPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["SOFR_FUT"],
        field_name="PX_BID",
    )

    universe_df = _make_universe_frame(["SOFR_FUT"], list(range(1, 9)))
    panel_df = _make_panel_frame(["SOFR_FUT"], list(range(1, 9)))

    with patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_panel",
        return_value=(panel_df, universe_df),
    ) as fetch_mock, patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_universe",
        return_value=universe_df,
    ):
        result = build_policy_futures_strip_panel(
            engine=None, params=params, config=cfg,
        )

    assert "error" not in result
    assert fetch_mock.call_args.kwargs["field_name"] == "PX_BID"
    assert result["methodology_card"]["field_name"] == "PX_BID"


# ============================================================================
# 5. Calendar policy
# ============================================================================


def test_build_policy_futures_strip_panel_calendar_policy_business_days_default():
    """The V1 default ``business_days`` calendar policy drops
    Saturday rows."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildPolicyFuturesStripPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["SOFR_FUT"],
        strip_positions=[1],
    )

    idx = pd.DatetimeIndex(["2024-01-02", "2024-01-06", "2024-01-08"])
    panel_df = pd.DataFrame(
        {"SOFR_FUT|1": [95.0, 95.1, 95.2]},
        index=idx,
    )
    universe_df = _make_universe_frame(["SOFR_FUT"], [1])

    with patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_panel",
        return_value=(panel_df, universe_df),
    ), patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_universe",
        return_value=universe_df,
    ):
        result = build_policy_futures_strip_panel(
            engine=None, params=params, config=cfg,
        )

    assert "error" not in result
    # 2024-01-06 was a Saturday → dropped by the default
    # business_days policy.
    assert result["row_count"] == 2
    assert result["methodology_card"]["calendar_policy"] == "business_days"


def test_build_policy_futures_strip_panel_calendar_policy_instrument_native_opt_in():
    """Explicit ``instrument_native`` opt-in preserves all observed
    dates verbatim (no business-day filter)."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildPolicyFuturesStripPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["SOFR_FUT"],
        strip_positions=[1],
        calendar_policy="instrument_native",
    )

    idx = pd.DatetimeIndex(["2024-01-02", "2024-01-03", "2024-01-06"])
    panel_df = pd.DataFrame(
        {"SOFR_FUT|1": [95.0, 95.1, 95.2]},
        index=idx,
    )
    universe_df = _make_universe_frame(["SOFR_FUT"], [1])

    with patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_panel",
        return_value=(panel_df, universe_df),
    ), patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_universe",
        return_value=universe_df,
    ):
        result = build_policy_futures_strip_panel(
            engine=None, params=params, config=cfg,
        )

    assert "error" not in result
    assert result["row_count"] == 3  # Saturday preserved by opt-in
    assert result["methodology_card"]["calendar_policy"] == "instrument_native"


# ============================================================================
# 6. Missing-data policies
# ============================================================================


def test_build_policy_futures_strip_panel_missing_data_policy_raise_errors():
    """``missing_data_policy='raise'`` surfaces a controlled error
    when the panel has NaN cells after ffill."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildPolicyFuturesStripPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["SOFR_FUT"],
        strip_positions=[1, 2],
        missing_data_policy="raise",
    )

    idx = pd.bdate_range(start="2024-01-02", periods=5)
    panel_df = pd.DataFrame(
        {
            "SOFR_FUT|1": [95.0, 95.1, 95.2, 95.3, 95.4],
            "SOFR_FUT|2": [94.0, 94.1, None, 94.3, 94.4],
        },
        index=idx,
    )
    universe_df = _make_universe_frame(["SOFR_FUT"], [1, 2])

    with patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_panel",
        return_value=(panel_df, universe_df),
    ), patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_universe",
        return_value=universe_df,
    ):
        result = build_policy_futures_strip_panel(
            engine=None, params=params, config=cfg,
        )

    assert "error" in result
    assert "missing_data_policy='raise'" in result["error"]


def test_build_policy_futures_strip_panel_missing_data_policy_drop_rows():
    """``drop_rows_any_missing`` shrinks the panel to fully-observed
    dates only."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildPolicyFuturesStripPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["SOFR_FUT"],
        strip_positions=[1, 2],
        missing_data_policy="drop_rows_any_missing",
    )

    idx = pd.bdate_range(start="2024-01-02", periods=5)
    panel_df = pd.DataFrame(
        {
            "SOFR_FUT|1": [95.0, 95.1, 95.2, 95.3, 95.4],
            "SOFR_FUT|2": [94.0, 94.1, None, 94.3, 94.4],
        },
        index=idx,
    )
    universe_df = _make_universe_frame(["SOFR_FUT"], [1, 2])

    with patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_panel",
        return_value=(panel_df, universe_df),
    ), patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_universe",
        return_value=universe_df,
    ):
        result = build_policy_futures_strip_panel(
            engine=None, params=params, config=cfg,
        )

    assert "error" not in result
    # 5 input rows; the 3rd (SFR2 NaN) drops.
    assert result["row_count"] == 4


# ============================================================================
# 7. ffill_limit honoured (config-driven)
# ============================================================================


def test_build_policy_futures_strip_panel_ffill_limit_threaded_to_fetcher():
    """The YAML's ``ffill_limit_days`` value is passed to the
    fetcher AND surfaced on the methodology card."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildPolicyFuturesStripPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["SOFR_FUT"],
    )

    universe_df = _make_universe_frame(["SOFR_FUT"], list(range(1, 9)))
    panel_df = _make_panel_frame(["SOFR_FUT"], list(range(1, 9)))

    with patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_panel",
        return_value=(panel_df, universe_df),
    ) as fetch_mock, patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_universe",
        return_value=universe_df,
    ):
        result = build_policy_futures_strip_panel(
            engine=None, params=params, config=cfg,
        )

    assert "error" not in result
    assert fetch_mock.call_args.kwargs["ffill_limit_days"] == 5
    assert result["methodology_card"]["ffill_limit_days"] == 5


# ============================================================================
# 8. Empty-result / fully-empty-column error envelopes
# ============================================================================


def test_build_policy_futures_strip_panel_empty_universe_returns_error():
    """Empty instrument_master surface yields a controlled error
    envelope, not an exception."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildPolicyFuturesStripPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["SOFR_FUT"],
    )

    with patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_panel",
        return_value=(pd.DataFrame(), pd.DataFrame()),
    ), patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_universe",
        return_value=pd.DataFrame(),
    ):
        result = build_policy_futures_strip_panel(
            engine=None, params=params, config=cfg,
        )

    assert "error" in result
    assert "instrument_master" in result["error"]


def test_build_policy_futures_strip_panel_empty_panel_returns_error():
    """Empty enriched-view fetch yields a controlled error
    envelope."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildPolicyFuturesStripPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["SOFR_FUT"],
    )

    universe_df = _make_universe_frame(["SOFR_FUT"], list(range(1, 9)))

    with patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_panel",
        return_value=(pd.DataFrame(), universe_df),
    ), patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_universe",
        return_value=universe_df,
    ):
        result = build_policy_futures_strip_panel(
            engine=None, params=params, config=cfg,
        )

    assert "error" in result
    assert "No policy-futures observations" in result["error"]


def test_build_policy_futures_strip_panel_fully_empty_column_fails():
    """A column populated only with NaN flags a structural bug, not
    a missing-data condition — surfaces the offending column."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildPolicyFuturesStripPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["SOFR_FUT"],
        strip_positions=[1, 2],
    )

    idx = pd.bdate_range(start="2024-01-02", periods=5)
    panel_df = pd.DataFrame(
        {
            "SOFR_FUT|1": [95.0, 95.1, 95.2, 95.3, 95.4],
            "SOFR_FUT|2": [None] * 5,
        },
        index=idx,
    )
    universe_df = _make_universe_frame(["SOFR_FUT"], [1, 2])

    with patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_panel",
        return_value=(panel_df, universe_df),
    ), patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_universe",
        return_value=universe_df,
    ):
        result = build_policy_futures_strip_panel(
            engine=None, params=params, config=cfg,
        )

    assert "error" in result
    assert "SOFR_FUT|2" in result["error"]


# ============================================================================
# 9. Mixed inverse_pricing flag within ONE curve_family → refuses
# ============================================================================


def test_build_policy_futures_strip_panel_mixed_inverse_pricing_within_cf_refuses():
    """Two strip positions on the SAME curve_family with disagreeing
    inverse_pricing flags ⇒ refuse with the controlled-error
    envelope (P5 — refusing to silently mix conventions)."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildPolicyFuturesStripPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["SOFR_FUT"],
        strip_positions=[1, 2],
    )

    # Hand-craft a universe frame with conflicting inverse_pricing
    # flags on the SOFR_FUT curve.
    universe_df = pd.DataFrame(
        [
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
                "inverse_pricing": False,
            },
        ]
    )
    idx = pd.bdate_range(start="2024-01-02", periods=5)
    panel_df = pd.DataFrame(
        {
            "SOFR_FUT|1": [95.0, 95.1, 95.2, 95.3, 95.4],
            "SOFR_FUT|2": [94.0, 94.1, 94.2, 94.3, 94.4],
        },
        index=idx,
    )

    with patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_panel",
        return_value=(panel_df, universe_df),
    ), patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_universe",
        return_value=universe_df,
    ):
        result = build_policy_futures_strip_panel(
            engine=None, params=params, config=cfg,
        )

    assert "error" in result
    assert "inverse_pricing" in result["error"]
    assert "SOFR_FUT" in result["error"]


# ============================================================================
# 10. Schema validation — closed-family curve_families
# ============================================================================


def test_build_policy_futures_strip_panel_rejects_non_policy_futures_curve_family():
    """Non-policy-futures curve family is refused at schema
    validation."""
    with pytest.raises(ValueError):
        BuildPolicyFuturesStripPanelInput(
            start_date=date(2024, 1, 2),
            curve_families=["UST"],  # sovereign — not a policy-futures family
        )


def test_build_policy_futures_strip_panel_rejects_zcis_curve_family():
    """ZCIS curve families are refused at schema validation —
    use build_zcis_panel for those."""
    with pytest.raises(ValueError):
        BuildPolicyFuturesStripPanelInput(
            start_date=date(2024, 1, 2),
            curve_families=["USD_ZCIS"],
        )


def test_build_policy_futures_strip_panel_rejects_bond_futures_curve_family():
    """Bond-futures curve_family (UST_FUT) is refused — use the
    bond_futures domain for those."""
    with pytest.raises(ValueError):
        BuildPolicyFuturesStripPanelInput(
            start_date=date(2024, 1, 2),
            curve_families=["UST_FUT"],
        )


def test_build_policy_futures_strip_panel_rejects_empty_curve_families_list():
    """Explicitly-empty ``curve_families=[]`` is refused at
    validation — caller passes ``None`` instead to mean "full
    universe"."""
    with pytest.raises(ValueError):
        BuildPolicyFuturesStripPanelInput(
            start_date=date(2024, 1, 2),
            curve_families=[],
        )


def test_build_policy_futures_strip_panel_rejects_duplicate_curve_families():
    """Duplicate curve_families are refused at schema validation."""
    with pytest.raises(ValueError):
        BuildPolicyFuturesStripPanelInput(
            start_date=date(2024, 1, 2),
            curve_families=["SOFR_FUT", "SOFR_FUT"],
        )


def test_build_policy_futures_strip_panel_rejects_end_before_start():
    """``end_date < start_date`` is refused at schema validation."""
    with pytest.raises(ValueError):
        BuildPolicyFuturesStripPanelInput(
            start_date=date(2024, 6, 1),
            end_date=date(2024, 1, 1),
        )


def test_build_policy_futures_strip_panel_rejects_out_of_range_strip_position():
    """strip_position=9 is out of the V1 playbook range (1..8) and
    refused at schema validation."""
    with pytest.raises(ValueError):
        BuildPolicyFuturesStripPanelInput(
            start_date=date(2024, 1, 2),
            strip_positions=[1, 9],
        )


def test_build_policy_futures_strip_panel_rejects_zero_strip_position():
    """strip_position=0 is below the V1 playbook range and refused at
    schema validation."""
    with pytest.raises(ValueError):
        BuildPolicyFuturesStripPanelInput(
            start_date=date(2024, 1, 2),
            strip_positions=[0],
        )


def test_build_policy_futures_strip_panel_rejects_duplicate_strip_positions():
    """Duplicate strip_positions are refused at schema validation."""
    with pytest.raises(ValueError):
        BuildPolicyFuturesStripPanelInput(
            start_date=date(2024, 1, 2),
            strip_positions=[1, 1],
        )


def test_build_policy_futures_strip_panel_rejects_empty_strip_positions_list():
    """Explicitly-empty ``strip_positions=[]`` is refused."""
    with pytest.raises(ValueError):
        BuildPolicyFuturesStripPanelInput(
            start_date=date(2024, 1, 2),
            strip_positions=[],
        )


def test_build_policy_futures_strip_panel_input_forbids_aspirational_knob():
    """PR8 wire-shape guard — no aspirational methodology knobs
    (e.g. ``delivery_month_type``, ``inverse_pricing_override``,
    ``ddof``) on the input schema."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        BuildPolicyFuturesStripPanelInput(
            start_date=date(2024, 1, 2),
            delivery_month_type="serial",  # not an accepted input
        )
    with pytest.raises(ValidationError):
        BuildPolicyFuturesStripPanelInput(
            start_date=date(2024, 1, 2),
            inverse_pricing_override=True,  # not an accepted input
        )
    with pytest.raises(ValidationError):
        BuildPolicyFuturesStripPanelInput(
            start_date=date(2024, 1, 2),
            ddof=1,  # not an accepted input
        )


# ============================================================================
# 11. None universe scoping → full live universe
# ============================================================================


def test_build_policy_futures_strip_panel_none_scoping_resolved_to_full():
    """``curve_families=None`` + ``strip_positions=None`` resolves to
    the full live universe (3 curve_families × 8 strip positions)."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildPolicyFuturesStripPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
    )

    expected_curve_families = [
        "SOFR_FUT", "SONIA_FUT", "EUR_SHORT_RATE_FUT",
    ]
    expected_positions = list(range(1, 9))
    universe_df = _make_universe_frame(
        expected_curve_families, expected_positions,
    )
    panel_df = _make_panel_frame(
        expected_curve_families, expected_positions,
    )

    with patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_panel",
        return_value=(panel_df, universe_df),
    ) as fetch_mock, patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_universe",
        return_value=universe_df,
    ):
        result = build_policy_futures_strip_panel(
            engine=None, params=params, config=cfg,
        )

    assert "error" not in result
    assert fetch_mock.call_args.kwargs["curve_families"] == expected_curve_families
    assert fetch_mock.call_args.kwargs["strip_positions"] == expected_positions


# ============================================================================
# 12. Explicit config vs auto-load parity
# ============================================================================


def test_build_policy_futures_strip_panel_explicit_config_vs_auto_load_parity():
    """Passing ``config=None`` and passing
    ``config=load_tool_config(CONFIG_PATH)`` produce identical
    results — exercises the auto-load fallback in
    ``build_policy_futures_strip_panel``."""
    explicit = load_tool_config(CONFIG_PATH)
    params = BuildPolicyFuturesStripPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        curve_families=["SOFR_FUT"],
        strip_positions=[1, 2],
    )

    universe_df = _make_universe_frame(["SOFR_FUT"], [1, 2])
    panel_df = _make_panel_frame(["SOFR_FUT"], [1, 2])

    with patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_panel",
        return_value=(panel_df, universe_df),
    ), patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_universe",
        return_value=universe_df,
    ):
        result_explicit = build_policy_futures_strip_panel(
            engine=None, params=params, config=explicit,
        )
        result_auto = build_policy_futures_strip_panel(
            engine=None, params=params, config=None,
        )

    # Drop the typed Panel artifact for the comparison — pandas
    # equality on the embedded DataFrame is awkward via ==.
    for r in (result_explicit, result_auto):
        r.pop("panel", None)
    assert result_explicit == result_auto


# ============================================================================
# 13. Calendar-policy invalid value rejected at schema layer
# ============================================================================


def test_build_policy_futures_strip_panel_rejects_unknown_calendar_policy_via_schema():
    """Calendar-policy override outside the closed-enum is refused
    at schema validation."""
    with pytest.raises(ValueError):
        BuildPolicyFuturesStripPanelInput(
            start_date=date(2024, 1, 2),
            calendar_policy="weird_calendar",  # not in the Literal
        )


# ============================================================================
# 14. Implied-rate value-field contract (PR14 frozen)
# ============================================================================


def test_build_policy_futures_strip_panel_panel_value_field_is_implied_rate_pct():
    """The methodology card declares ``panel_value_field`` as
    ``implied_rate_pct`` (PR14 frozen) AND every cell's value lives
    in PERCENT space after the inverse-pricing conversion."""
    cfg = load_tool_config(CONFIG_PATH)
    params = BuildPolicyFuturesStripPanelInput(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 5),
        curve_families=["SOFR_FUT"],
        strip_positions=[1],
    )

    idx = pd.bdate_range(start="2024-01-02", periods=4)
    panel_df = pd.DataFrame({"SOFR_FUT|1": [95.0, 95.5, 96.0, 96.5]}, index=idx)
    universe_df = _make_universe_frame(["SOFR_FUT"], [1])

    with patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_panel",
        return_value=(panel_df, universe_df),
    ), patch(
        f"{_COMPUTE_PATCH_PATH}.fetch_policy_futures_strip_universe",
        return_value=universe_df,
    ):
        result = build_policy_futures_strip_panel(
            engine=None, params=params, config=cfg,
        )

    assert "error" not in result
    assert result["methodology_card"]["panel_value_field"] == "implied_rate_pct"

    validated = BuildPolicyFuturesStripPanelOutput.model_validate(result)
    payload = validated.panel.payload
    # Inverse-priced strip ⇒ implied_rate_pct = 100 - raw_price.
    expected = [5.0, 4.5, 4.0, 3.5]
    actual = payload["SOFR_FUT|1"].tolist()
    for got, want in zip(actual, expected):
        assert abs(got - want) < 1e-9, f"expected {want}, got {got}"
