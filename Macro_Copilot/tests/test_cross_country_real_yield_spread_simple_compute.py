"""
test_cross_country_real_yield_spread_simple_compute.py — Unit tests
for the linker cross_country_real_yield_spread_simple primitive.

Mirrors ``test_real_yield_curve_spread_compute.py`` and
``test_cross_country_breakeven_spread_simple_compute.py`` — this
primitive composes ``get_real_yield_level`` twice (one per
curve_family) at the SAME tenor and inherits the level primitive's
no-proxy guard (``instrument_type='inflation_linker'``)
transitively on BOTH legs.

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. compute() runs end-to-end against synthetic input with the
     bundled config and returns a well-formed output.
  3. Explicit ``config=`` vs auto-load parity (byte-identical).
  4. Convention overrides change behaviour (z-window, ddof,
     period offsets, yield rounding, default_field_name).
  5. Honest-placeholder guards: trailing_range_window_days != 252
     raises NotImplementedError.
  6. Schema-layer behaviour: field_name defaults to None;
     compute() resolves the sentinel against the YAML.  Cross-
     curve validators reject identical curve_family on both
     legs.
  7. Composition guard inheritance: non-linker curve_family on
     either leg refused at compute time BEFORE any inner level
     fetch fires (post-fetch identity guard on
     instrument_master).  Cross-product of a nominal sovereign
     with a linker is refused.
  8. Per-trade-date alignment: dates where one leg has no data
     are dropped from the spread series.
  9. Spread formula correctness AND sign convention on synthetic
     input (first minus second; flipping inputs flips the sign).
 10. Boundary-rounding parity: snapshot ``current_spread_pct``
     equals ``time_series[-1].spread_pct`` AND
     ``time_series_spread.rows[-1].value`` bit-for-bit.
 11. Wire-honesty: ``methodology_label`` is sourced from the YAML.
 12. Three import paths still resolve to the same Pydantic class.
 13. Canonical TimeSeries outputs: PERCENT for the spread,
     Z_SCORE for the rolling z-score, with the documented
     series_name pattern AND the sign-convention token in the
     spread series description.
 14. ffill semantics: gap > ffill_limit_days surfaces as dropped
     rows on the spread series.
 15. Z-score basics on deterministic synthetic series.

Tests are fully offline (DB-backed grounding lives in the SQL-
validation runner — see
``tests/test_cross_country_real_yield_spread_simple_sql_validation.py``).
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple import (
    CONFIG_PATH,
    CrossCountryRealYieldSpreadSimpleInput,
    calculate_cross_country_real_yield_spread_simple,
)
from shared.config import (
    Convention,
    MethodologyMeta,
    ToolConfig,
    ToolMeta,
    clear_tool_config_cache,
    load_tool_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


# DB-free country/currency lookup table.  The compute path issues a
# real instrument_master SELECT in
# ``_fetch_curve_family_country_currency`` to enforce the post-fetch
# identity guard on BOTH legs before either inner level call fires.
_DEFAULT_COUNTRY_CURRENCY = {
    # Linkers — the only valid identities for this primitive.
    ("USD_TIPS",      "inflation_linker"): ("US", "USD"),
    ("GBP_LINKER",    "inflation_linker"): ("UK", "GBP"),
    ("EUR_FR_LINKER", "inflation_linker"): ("France", "EUR"),
    ("CAD_RRB",       "inflation_linker"): ("Canada", "CAD"),
    # Nominal-sovereign curve families intentionally absent under
    # the inflation_linker key so the guard refuses them.  Even if
    # registered under sovereign_benchmark, the guard's lookup uses
    # inflation_linker and will therefore see "no rows".
}


def _stub_country_currency_factory(table=None):
    table = table if table is not None else dict(_DEFAULT_COUNTRY_CURRENCY)

    def _stub(engine, curve_family, instrument_type):  # noqa: ARG001
        key = (curve_family, instrument_type)
        if key not in table:
            return (
                None,
                None,
                (
                    f"No instrument_master rows found for "
                    f"curve_family='{curve_family}', "
                    f"instrument_type='{instrument_type}'."
                ),
            )
        value = table[key]
        if isinstance(value, tuple) and len(value) == 2:
            return value[0], value[1], None
        return None, None, str(value)

    return _stub


@pytest.fixture(autouse=True)
def _patch_country_currency():
    with patch(
        "rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple.compute._fetch_curve_family_country_currency",
        new=_stub_country_currency_factory(),
    ):
        yield


def _synthetic_single_leg(
    *,
    days: int = 800,
    frozen_today: date = date(2026, 4, 30),
    base_pct: float,
    drift_pct: float,
) -> pd.DataFrame:
    """Build a ``[trade_date, field_value]`` long-format DataFrame
    matching what ``fetch_single_tenor`` returns for one
    (curve_family, tenor) leg."""
    bdays = pd.bdate_range(
        frozen_today - timedelta(days=days * 2), frozen_today,
    )
    bdays = bdays[-days:]
    n = len(bdays)
    series = np.linspace(base_pct, base_pct + drift_pct, n)
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": series,
    })


def _patched_fetch_factory(legs: dict):
    """Build a stand-in for ``fetch_single_tenor`` that dispatches by
    (curve_family, tenor, instrument_type)."""
    def _stub(
        *,
        engine,
        curve_family,
        tenor,
        field_name,
        start_date,
        instrument_type=None,
    ):
        key = (curve_family, tenor, instrument_type)
        if key in legs:
            return legs[key]
        return pd.DataFrame(columns=["trade_date", "field_value"])
    return _stub


class _FrozenDate(date):
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


def _build_legs_for_usd_tips_vs_gbp_linker_10y(
    *,
    first_real: float = 2.05,
    second_real: float = 0.85,
    first_drift: float = -0.30,
    second_drift: float = -0.10,
) -> dict:
    """Default canonical two-leg fixture for USD_TIPS 10Y vs
    GBP_LINKER 10Y.  Differentiated drifts produce a spread series
    that varies through time — necessary for the z-score / period-
    change override tests to surface a convention-driven
    difference.
    """
    days = 800
    return {
        ("USD_TIPS", "10Y", "inflation_linker"): _synthetic_single_leg(
            days=days, base_pct=first_real, drift_pct=first_drift,
        ),
        ("GBP_LINKER", "10Y", "inflation_linker"): _synthetic_single_leg(
            days=days, base_pct=second_real, drift_pct=second_drift,
        ),
    }


_BUNDLED_DEFAULTS = {
    "z_score_window_days": 252,
    "z_score_min_periods": 60,
    "z_score_ddof": 1,
    "z_score_buffer_multiplier": 1.5,
    "daily_change_offset_rows": 2,
    "weekly_change_offset_rows": 6,
    "monthly_change_offset_rows": 22,
    "trailing_range_window_days": 252,
    "ffill_limit_days": 5,
    "yield_round_decimals": 4,
    "bps_round_decimals": 2,
    "z_score_round_decimals": 4,
    "window_years_round_decimals": 4,
    "high_low_round_decimals": 4,
    "default_field_name": "YLD_YTM_MID",
}


def _build_config(
    *,
    methodology_what_it_does: str = "test methodology label",
    **overrides,
) -> ToolConfig:
    defaults = dict(_BUNDLED_DEFAULTS)
    defaults.update(overrides)
    return ToolConfig(
        tool=ToolMeta(name="t", domain="d", description="x"),
        methodology=MethodologyMeta(what_it_does=methodology_what_it_does),
        conventions={
            k: Convention(value=v, source="test", rationale="test")
            for k, v in defaults.items()
        },
    )


def _run(params, legs, *, config=None):
    with patch(
        "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.fetch_single_tenor",
        new=_patched_fetch_factory(legs),
    ), patch(
        "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.date",
        _FrozenDate,
    ):
        return calculate_cross_country_real_yield_spread_simple(
            engine=None, params=params, config=config,
        )


# ===========================================================================
# 1. Bundled config.yaml
# ===========================================================================

class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == (
            "calculate_cross_country_real_yield_spread_simple_tool"
        )
        assert cfg.tool.domain == "inflation_indexed_bonds"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "z_score_window_days",
            "z_score_min_periods",
            "z_score_ddof",
            "z_score_buffer_multiplier",
            "daily_change_offset_rows",
            "weekly_change_offset_rows",
            "monthly_change_offset_rows",
            "trailing_range_window_days",
            "ffill_limit_days",
            "yield_round_decimals",
            "bps_round_decimals",
            "z_score_round_decimals",
            "window_years_round_decimals",
            "high_low_round_decimals",
            "default_field_name",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults_align_with_other_rates_tools(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("z_score_window_days") == 252
        assert cfg.convention_value("z_score_min_periods") == 60
        assert cfg.convention_value("z_score_ddof") == 1
        assert cfg.convention_value("z_score_buffer_multiplier") == 1.5
        assert cfg.convention_value("daily_change_offset_rows") == 2
        assert cfg.convention_value("weekly_change_offset_rows") == 6
        assert cfg.convention_value("monthly_change_offset_rows") == 22
        assert cfg.convention_value("trailing_range_window_days") == 252
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("yield_round_decimals") == 4
        assert cfg.convention_value("bps_round_decimals") == 2
        assert cfg.convention_value("z_score_round_decimals") == 4
        assert cfg.convention_value("window_years_round_decimals") == 4
        assert cfg.convention_value("high_low_round_decimals") == 4
        assert cfg.convention_value("default_field_name") == "YLD_YTM_MID"

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 2
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "trailing_range_window_days" in joined

    def test_methodology_what_it_does_carries_disclosure(self):
        cfg = load_tool_config(CONFIG_PATH)
        text = cfg.methodology.what_it_does.lower()
        # Spread formula MUST be visible on the wire-disclosure.
        assert "first_curve_real_yield_pct" in text
        assert "second_curve_real_yield_pct" in text
        # Index-family and market-structure caveats MUST be in the
        # methodology label per the catalog's methodology_guardrails.
        assert "index-family" in text or "index family" in text
        assert "market-structure" in text or "market structure" in text


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        for k in (
            "as_of_date",
            "first_curve_family",
            "second_curve_family",
            "tenor",
            "spread_label",
            "current_spread_pct",
            "daily_change_bps",
            "weekly_change_bps",
            "monthly_change_bps",
            "current_z_score",
            "rolling_window_days",
            "high_252d_pct",
            "low_252d_pct",
            "percentile_252d",
            "first_curve_real_yield_pct",
            "second_curve_real_yield_pct",
            "tenor_years",
            "first_curve_country",
            "first_curve_currency",
            "second_curve_country",
            "second_curve_currency",
            "methodology_label",
        ):
            assert k in cm, f"missing {k}"

        assert cm["tenor_years"] == 10.0
        assert cm["first_curve_family"] == "USD_TIPS"
        assert cm["second_curve_family"] == "GBP_LINKER"
        assert cm["spread_label"] == (
            "USD_TIPS - GBP_LINKER 10Y XC real-yield"
        )
        assert cm["first_curve_country"] == "US"
        assert cm["first_curve_currency"] == "USD"
        assert cm["second_curve_country"] == "UK"
        assert cm["second_curve_currency"] == "GBP"

    def test_spread_formula_is_correct(self):
        """Hard-pin the spread formula on a synthetic fixture with
        constant endpoint real yields.  With first=2.20%,
        second=0.80%, expected spread = +1.40 pct.
        """
        days = 800
        legs = {
            ("USD_TIPS", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=2.20, drift_pct=0.0,
            ),
            ("GBP_LINKER", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=0.80, drift_pct=0.0,
            ),
        }
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["first_curve_real_yield_pct"] == pytest.approx(2.20, abs=1e-4)
        assert cm["second_curve_real_yield_pct"] == pytest.approx(0.80, abs=1e-4)
        # spread = first - second = 1.40 pct
        assert cm["current_spread_pct"] == pytest.approx(1.40, abs=1e-4)

    def test_sign_convention_first_minus_second(self):
        """Flipping first ↔ second flips the displayed sign."""
        days = 800
        legs_ab = {
            ("USD_TIPS", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=2.00, drift_pct=0.0,
            ),
            ("GBP_LINKER", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=1.00, drift_pct=0.0,
            ),
        }
        out_ab = _run(
            CrossCountryRealYieldSpreadSimpleInput(
                first_curve_family="USD_TIPS",
                second_curve_family="GBP_LINKER",
                tenor="10Y",
                lookback_days=365,
            ),
            legs_ab,
        )
        out_ba = _run(
            CrossCountryRealYieldSpreadSimpleInput(
                first_curve_family="GBP_LINKER",
                second_curve_family="USD_TIPS",
                tenor="10Y",
                lookback_days=365,
            ),
            legs_ab,
        )
        assert "error" not in out_ab
        assert "error" not in out_ba
        assert out_ab["current_metrics"]["current_spread_pct"] == pytest.approx(
            1.00, abs=1e-4,
        )
        assert out_ba["current_metrics"]["current_spread_pct"] == pytest.approx(
            -1.00, abs=1e-4,
        )

    def test_negative_spread_handled(self):
        """Cross-country real-yield differentials can be negative."""
        days = 800
        legs = {
            ("USD_TIPS", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=0.30, drift_pct=0.0,
            ),
            ("GBP_LINKER", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=1.20, drift_pct=0.0,
            ),
        }
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")
        assert out["current_metrics"]["current_spread_pct"] < 0

    def test_explicit_config_matches_auto_loaded(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out_auto = _run(params, legs, config=None)
        out_explicit = _run(
            params, legs, config=load_tool_config(CONFIG_PATH),
        )
        assert out_auto == out_explicit


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def test_z_score_window_override_changes_z(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out_default = _run(params, legs, config=_build_config())
        out_short = _run(
            params, legs, config=_build_config(z_score_window_days=120),
        )
        assert (
            out_default["current_metrics"]["current_z_score"]
            != out_short["current_metrics"]["current_z_score"]
        )

    def test_z_score_ddof_override_changes_z(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out_sample = _run(
            params, legs, config=_build_config(z_score_ddof=1),
        )
        out_pop = _run(
            params, legs, config=_build_config(z_score_ddof=0),
        )
        assert (
            out_sample["current_metrics"]["current_z_score"]
            != out_pop["current_metrics"]["current_z_score"]
        )

    def test_period_offsets_override_changes_changes(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out_default = _run(params, legs, config=_build_config())
        out_wider = _run(
            params, legs,
            config=_build_config(daily_change_offset_rows=5),
        )
        assert (
            out_default["current_metrics"]["daily_change_bps"]
            != out_wider["current_metrics"]["daily_change_bps"]
        )

    def test_yield_round_decimals_override_changes_precision(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y(
            first_real=2.0876543, second_real=0.8765432,
        )
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out_4 = _run(
            params, legs, config=_build_config(yield_round_decimals=4),
        )
        out_6 = _run(
            params, legs, config=_build_config(yield_round_decimals=6),
        )
        v4 = out_4["current_metrics"]["current_spread_pct"]
        v6 = out_6["current_metrics"]["current_spread_pct"]
        assert v4 == round(v4, 4)
        assert v6 == round(v6, 6)


# ===========================================================================
# 4. Honest-placeholder guards
# ===========================================================================

class TestPlaceholderGuards:
    def test_unsupported_trailing_window_raises(self):
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            calculate_cross_country_real_yield_spread_simple(
                engine=None, params=params,
                config=_build_config(trailing_range_window_days=180),
            )
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_pct" in msg
        assert "planned_extensions" in msg


# ===========================================================================
# 5. Schema-layer behaviour
# ===========================================================================

class TestInputSchemaBehaviour:
    def test_field_name_default_is_none(self):
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
        )
        assert params.field_name is None

    def test_curves_must_differ(self):
        with pytest.raises(Exception):
            CrossCountryRealYieldSpreadSimpleInput(
                first_curve_family="USD_TIPS",
                second_curve_family="USD_TIPS",
                tenor="10Y",
            )

    def test_same_curve_error_message_routes_to_same_country_tool(self):
        with pytest.raises(Exception) as exc_info:
            CrossCountryRealYieldSpreadSimpleInput(
                first_curve_family="USD_TIPS",
                second_curve_family="USD_TIPS",
                tenor="10Y",
            )
        msg = str(exc_info.value).lower()
        # Caller should be routed back to the same-country tool.
        assert "calculate_real_yield_curve_spread_tool" in msg

    def test_extra_fields_rejected(self):
        with pytest.raises(Exception):
            CrossCountryRealYieldSpreadSimpleInput(
                first_curve_family="USD_TIPS",
                second_curve_family="GBP_LINKER",
                tenor="10Y",
                short_tenor="2Y",  # surprise field — extra='forbid'
            )

    def test_missing_first_curve_rejected(self):
        with pytest.raises(Exception):
            CrossCountryRealYieldSpreadSimpleInput(
                second_curve_family="GBP_LINKER",
                tenor="10Y",
            )

    def test_missing_second_curve_rejected(self):
        with pytest.raises(Exception):
            CrossCountryRealYieldSpreadSimpleInput(
                first_curve_family="USD_TIPS",
                tenor="10Y",
            )

    def test_missing_tenor_rejected(self):
        with pytest.raises(Exception):
            CrossCountryRealYieldSpreadSimpleInput(
                first_curve_family="USD_TIPS",
                second_curve_family="GBP_LINKER",
            )


# ===========================================================================
# 6. field_name → YAML default fall-through
# ===========================================================================

class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_field_name reaches BOTH
    inner level calls (one per curve)."""

    def _capture_fetch_calls(self, params, config, legs):
        captured = []

        def _stub(**kwargs):
            captured.append(kwargs)
            key = (kwargs["curve_family"], kwargs["tenor"], kwargs["instrument_type"])
            return legs.get(
                key, pd.DataFrame(columns=["trade_date", "field_value"]),
            )

        with patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.fetch_single_tenor",
            new=_stub,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.date",
            _FrozenDate,
        ):
            calculate_cross_country_real_yield_spread_simple(
                engine=None, params=params, config=config,
            )
        return captured

    def test_omitted_field_name_uses_yaml_default(self):
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_field_name="YLD_YTM_MID"),
            _build_legs_for_usd_tips_vs_gbp_linker_10y(),
        )
        # Two legs = 2 fetches.
        assert len(captured) == 2
        for call in captured:
            assert call["field_name"] == "YLD_YTM_MID"

    def test_yaml_override_changes_resolved_field(self):
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_field_name="YLD_YTM_BID"),
            _build_legs_for_usd_tips_vs_gbp_linker_10y(),
        )
        for call in captured:
            assert call["field_name"] == "YLD_YTM_BID"

    def test_explicit_field_name_overrides_yaml(self):
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            field_name="YLD_YTM_ASK",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_field_name="YLD_YTM_MID"),
            _build_legs_for_usd_tips_vs_gbp_linker_10y(),
        )
        for call in captured:
            assert call["field_name"] == "YLD_YTM_ASK"

    def test_instrument_type_filter_applied_on_every_fetch(self):
        """The level primitive's no-proxy guard is inherited
        transitively: every inner fetch is constrained to
        ``instrument_type='inflation_linker'``."""
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(),
            _build_legs_for_usd_tips_vs_gbp_linker_10y(),
        )
        assert len(captured) == 2
        for call in captured:
            assert call["instrument_type"] == "inflation_linker"


# ===========================================================================
# 7. Composition guard inheritance
# ===========================================================================

class TestCompositionGuards:
    """The post-fetch instrument_master identity guard on BOTH legs
    must fire BEFORE any inner level fetch when EITHER curve_family
    is non-linker or unknown.
    """

    def test_non_linker_first_curve_returns_error_envelope(self):
        legs: dict = {}
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="UST",  # nominal sovereign — not in table
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        err = out["error"]
        assert "first_curve_family" in err
        assert "UST" in err
        assert "inflation_linker" in err
        # Routing hint to sovereign tool should survive.
        assert "sovereign_bonds" in err.lower() or "cross_market_spread" in err

    def test_non_linker_second_curve_returns_error_envelope(self):
        legs: dict = {}
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="DE_BUND",  # nominal sovereign
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        err = out["error"]
        assert "second_curve_family" in err
        assert "DE_BUND" in err
        assert "inflation_linker" in err

    def test_cross_product_nominal_linker_refused(self):
        """A cross-product of (nominal sovereign, linker) — e.g.
        UST first leg, USD_TIPS second leg — must be refused at the
        identity-guard layer BEFORE any inner level fetch fires.
        The first leg's guard fires first."""
        fetch_calls = []

        def _record_fetch(**kwargs):
            fetch_calls.append(kwargs)
            return pd.DataFrame(columns=["trade_date", "field_value"])

        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="UST",  # nominal sovereign
            second_curve_family="USD_TIPS",  # linker
            tenor="10Y",
            lookback_days=365,
        )
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.fetch_single_tenor",
            new=_record_fetch,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.date",
            _FrozenDate,
        ):
            out = calculate_cross_country_real_yield_spread_simple(
                engine=None, params=params,
            )
        assert "error" in out
        assert fetch_calls == [], (
            "Non-linker first_curve_family must be refused BEFORE "
            "any market-data fetch fires.  Got "
            f"{len(fetch_calls)} fetch call(s)."
        )

    def test_identity_guard_runs_before_any_fetch(self):
        """A non-linker curve_family on either leg must be refused
        BEFORE any market-data SELECT (verified by recording fetch
        calls)."""
        fetch_calls = []

        def _record_fetch(**kwargs):
            fetch_calls.append(kwargs)
            return pd.DataFrame(columns=["trade_date", "field_value"])

        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="DE_BUND",
            tenor="10Y",
            lookback_days=365,
        )
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.fetch_single_tenor",
            new=_record_fetch,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.date",
            _FrozenDate,
        ):
            out = calculate_cross_country_real_yield_spread_simple(
                engine=None, params=params,
            )
        assert "error" in out
        assert fetch_calls == [], (
            "Non-linker second_curve_family must be refused BEFORE "
            "any market-data fetch fires.  Got "
            f"{len(fetch_calls)} fetch call(s)."
        )

    def test_unknown_pillar_yields_controlled_error_envelope(self):
        """A pillar that doesn't exist on a leg must surface a
        controlled error envelope, NOT a Python exception."""
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        # Use a tenor that has no entries for either leg
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="40Y",  # unsupported tenor for USD_TIPS
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        err = out["error"]
        # Either "first_curve leg failed" or "second_curve leg
        # failed" — depends on which inner call fires first.
        assert (
            "first_curve leg failed" in err
            or "second_curve leg failed" in err
        )

    def test_missing_first_leg_returns_error_envelope(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        legs[("USD_TIPS", "10Y", "inflation_linker")] = pd.DataFrame(
            columns=["trade_date", "field_value"],
        )
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "first_curve leg failed" in out["error"]

    def test_missing_second_leg_returns_error_envelope(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        legs[("GBP_LINKER", "10Y", "inflation_linker")] = pd.DataFrame(
            columns=["trade_date", "field_value"],
        )
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "second_curve leg failed" in out["error"]


# ===========================================================================
# 8. Per-trade-date alignment
# ===========================================================================

class TestPerTradeDateAlignment:
    """Inner-join discipline: dates where one leg has no data are
    dropped from the spread series, NOT carried forward."""

    def test_missing_endpoint_date_is_dropped(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()

        # Drop all dates from the second leg before a cutoff so
        # those dates can't be aligned; the inner level primitive's
        # 5-day ffill cannot bridge a multi-month gap.
        second_df = legs[("GBP_LINKER", "10Y", "inflation_linker")]
        cutoff_date = second_df["trade_date"].iloc[100]
        legs[("GBP_LINKER", "10Y", "inflation_linker")] = second_df[
            second_df["trade_date"] >= cutoff_date
        ].reset_index(drop=True)

        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")

        ts_dates = sorted(row["date"] for row in out["time_series"])
        first_second_date = cutoff_date.strftime("%Y-%m-%d")
        for d in ts_dates:
            assert d >= first_second_date, (
                f"Spread series contains date {d!r} predating the "
                f"second leg's first observation ({first_second_date!r})."
            )


# ===========================================================================
# 9. Boundary-rounding parity
# ===========================================================================

class TestBoundaryRoundingParity:
    def test_snapshot_pct_matches_bespoke_last_row_strictly(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        snap = out["current_metrics"]["current_spread_pct"]
        bespoke_last = out["time_series"][-1]["spread_pct"]
        assert snap == bespoke_last

    def test_snapshot_pct_matches_canonical_last_row_strictly(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        snap = out["current_metrics"]["current_spread_pct"]
        canon_last = out["time_series_spread"]["rows"][-1]["value"]
        assert snap == canon_last

    def test_bespoke_z_score_matches_canonical_z_score_per_row(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        bespoke = out["time_series"]
        canonical = out["time_series_zscore"]["rows"]
        assert len(bespoke) == len(canonical)
        for b, c in zip(bespoke, canonical):
            assert b["date"] == c["date"]
            assert b["z_score"] == c["value"]


# ===========================================================================
# 10. Wire-honesty disclosure threading
# ===========================================================================

class TestMethodologyLabelThreading:
    def test_methodology_label_sourced_from_yaml(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        custom_text = (
            "CUSTOM-XC-REAL-YIELD-DISCLOSURE-77 "
            "first_curve_real_yield_pct - second_curve_real_yield_pct "
            "cross-country real-rate divergence object"
        )
        out = _run(
            params, legs,
            config=_build_config(methodology_what_it_does=custom_text),
        )
        assert (
            out["current_metrics"]["methodology_label"]
            == custom_text.strip()
        )

    def test_bundled_methodology_label_carries_formula_and_caveats(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs, config=None)
        label = out["current_metrics"]["methodology_label"].lower()
        # Spread formula MUST be visible on the wire.
        assert "first_curve_real_yield_pct" in label
        assert "second_curve_real_yield_pct" in label
        # Caveats MUST be threaded onto the wire per
        # methodology_guardrails in the catalog.
        assert "index-family" in label or "index family" in label
        assert "market-structure" in label or "market structure" in label


# ===========================================================================
# 11. Import path stability
# ===========================================================================

class TestImportPathStability:
    def test_function_via_package_init(self):
        from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple import (
            calculate_cross_country_real_yield_spread_simple as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple.compute import (
            calculate_cross_country_real_yield_spread_simple as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple import (
            CrossCountryRealYieldSpreadSimpleInput as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple.schemas import (
            CrossCountryRealYieldSpreadSimpleInput as via_schemas,
        )
        from rates_agent.inflation_indexed_bonds.tools.schemas import (
            CrossCountryRealYieldSpreadSimpleInput as via_hub,
        )
        assert via_package is via_schemas is via_hub


# ===========================================================================
# 12. Canonical TimeSeries outputs
# ===========================================================================

class TestCanonicalTimeSeries:
    def test_canonical_spread_uses_percent_units_and_naming(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        ts = out["time_series_spread"]
        # PERCENT — same units as the underlying real yields, NOT BPS.
        assert ts["units"] == "percent"
        assert (
            ts["series_name"]
            == "usd_tips_gbp_linker_10y_xc_real_yield_spread"
        )

    def test_canonical_spread_description_carries_sign_convention(self):
        """Sign-convention token must be present in the canonical
        TimeSeries description so downstream consumers cannot
        misread the sign."""
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        desc = out["time_series_spread"]["description"]
        assert (
            "first_curve_real_yield_pct - second_curve_real_yield_pct"
            in desc
        ), (
            "Canonical TimeSeries description must embed the literal "
            "sign-convention token so downstream consumers cannot "
            f"reinterpret the sign.  Got: {desc!r}"
        )

    def test_canonical_spread_description_carries_caveats(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        desc = out["time_series_spread"]["description"].lower()
        assert "index-family" in desc or "index family" in desc
        assert "market-structure" in desc or "market structure" in desc

    def test_canonical_zscore_uses_z_score_units_and_naming(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        ts = out["time_series_zscore"]
        assert ts["units"] == "z_score"
        assert (
            ts["series_name"]
            == "usd_tips_gbp_linker_10y_xc_real_yield_spread_zscore"
        )

    def test_canonical_lengths_match_bespoke(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert (
            len(out["time_series"])
            == len(out["time_series_spread"]["rows"])
        )
        assert (
            len(out["time_series"])
            == len(out["time_series_zscore"]["rows"])
        )

    def test_canonical_payloads_validate_against_TimeSeries_schema(self):
        from shared.schemas import TimeSeries
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        TimeSeries.model_validate(out["time_series_spread"])
        TimeSeries.model_validate(out["time_series_zscore"])

    def test_canonical_dates_chronological(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        dates = [r["date"] for r in out["time_series_spread"]["rows"]]
        assert dates == sorted(dates)


# ===========================================================================
# 13. ffill semantics: gap > ffill_limit_days surfaces as dropped row
# ===========================================================================

class TestFfillSemantics:
    """Inner level primitive applies an ffill within
    ffill_limit_days; a synthetic gap exceeding that limit must
    surface as a dropped row on the spread series (no synthetic
    carry-forward).
    """

    def test_long_gap_drops_spread_rows(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()

        # Carve out a ~20-business-day hole in the second leg (well
        # beyond the 5-day ffill_limit).
        second_df = legs[("GBP_LINKER", "10Y", "inflation_linker")]
        gap_start_idx = 300
        gap_end_idx = 320
        gap_dates = second_df["trade_date"].iloc[
            gap_start_idx:gap_end_idx
        ].tolist()
        legs[("GBP_LINKER", "10Y", "inflation_linker")] = second_df[
            ~second_df["trade_date"].isin(gap_dates)
        ].reset_index(drop=True)

        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")

        ts_dates = {row["date"] for row in out["time_series"]}
        observable_window_min = (
            date(2026, 4, 30) - timedelta(days=365)
        )
        missing_dates_in_window = [
            d for d in gap_dates
            if d >= observable_window_min
        ]
        # Skip first 5 (ffill_limit bridges those).
        for d in missing_dates_in_window[5:]:
            assert d.strftime("%Y-%m-%d") not in ts_dates, (
                f"Long-gap date {d!r} survived in the spread series — "
                "inner-join discipline must have broken."
            )


# ===========================================================================
# 14. Z-score basics on deterministic synthetic series
# ===========================================================================

class TestZScoreBasics:
    def test_z_score_finite_after_warmup(self):
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        z = out["current_metrics"]["current_z_score"]
        assert z is not None
        assert isinstance(z, float)
        assert not (z != z)  # NaN check

    def test_z_score_none_when_spread_constant(self):
        days = 800
        legs = {
            ("USD_TIPS", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=2.00, drift_pct=0.0,
            ),
            ("GBP_LINKER", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=1.50, drift_pct=0.0,
            ),
        }
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        # spread = 0.50 every day → z-score undefined (None).
        assert out["current_metrics"]["current_z_score"] is None


# ===========================================================================
# 15. Identity-guard ambiguous metadata
# ===========================================================================

class TestIdentityGuardAmbiguity:
    """If instrument_master returned multiple distinct (country,
    currency) tuples for a curve_family, the helper would surface
    a controlled error rather than silently picking one."""

    def test_ambiguous_first_curve_metadata_refused(self):
        ambiguous_table = dict(_DEFAULT_COUNTRY_CURRENCY)
        ambiguous_table[("USD_TIPS", "inflation_linker")] = (
            "Ambiguous instrument_master metadata for "
            "curve_family='USD_TIPS', "
            "instrument_type='inflation_linker': "
            "multiple distinct (country, currency) rows observed: "
            "[('US', 'USD'), ('PR', 'USD')]."
        )
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family="USD_TIPS",
            second_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        legs = _build_legs_for_usd_tips_vs_gbp_linker_10y()
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple.compute._fetch_curve_family_country_currency",
            new=_stub_country_currency_factory(ambiguous_table),
        ):
            out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "Ambiguous" in out["error"] or "ambiguous" in out["error"].lower()
        assert "first_curve_family" in out["error"]
