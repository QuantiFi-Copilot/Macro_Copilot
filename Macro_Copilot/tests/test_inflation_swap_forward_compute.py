"""
test_inflation_swap_forward_compute.py — Unit tests for the
ZCIS forward primitive.

Mirrors ``test_inflation_swap_curve_spread_compute.py`` (the
closest same-domain sibling) and
``test_forward_breakeven_simple_compute.py`` (the closest forward-
shape sibling).  This module differs in that it composes the
ZCIS rate-level primitive (PERCENT-units endpoint reads,
four-conjunct SELECT guard) into a dual-compounding geometric
forward rather than a per-trade-date difference.

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly,
     including the forward_compounding_mode key.
  2. Convention defaults align with sibling rates tools.
  3. Convention overrides actually change behaviour (z-window,
     ddof, period offsets, bps rounding,
     default_zcis_rate_field).
  4. Honest-placeholder guards: trailing_range_window_days != 252
     and forward_compounding_mode != dual_compounding both raise
     NotImplementedError.
  5. Schema-layer behaviour: field_name defaults to None;
     compute() resolves the sentinel against the YAML.  Tenor
     validators reject structurally invalid pairs.
  6. Composition guard inheritance: missing endpoint legs surface
     a controlled error envelope from the inner level call,
     propagated with start-tenor / end-tenor attribution.
  7. Per-trade-date alignment: dates where one endpoint has no
     data (beyond the level's ffill window) are dropped from the
     forward series.
  8. Dual-compounding geometric forward formula correctness on
     synthetic input.
  9. Reference-metadata mismatch surfaces a controlled error
     envelope (no silently averaged snapshot).
 10. Boundary-rounding parity:
     ``current_metrics.forward_zcis_bps`` equals
     ``time_series[-1].forward_zcis_bps`` AND
     ``time_series_forward.rows[-1].value * 100`` (rounded to
     bps_round_decimals) bit-for-bit.
 11. Wire-honesty: ``methodology_label`` is sourced from the YAML.
 12. Reference metadata threads to current_metrics + canonical
     description.
 13. Three import paths still resolve to the same Pydantic class.
 14. Canonical TimeSeries outputs: PERCENT for the forward,
     Z_SCORE for the rolling z-score, with the documented
     series_name pattern.

Tests are fully offline (DB-backed grounding lives in the SQL-
validation runner — see
``tests/test_inflation_swap_forward_sql_validation.py``).
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.inflation_swaps.tools.inflation_swap_forward import (
    CONFIG_PATH,
    InflationSwapForwardInput,
    calculate_inflation_swap_forward,
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


def _synthetic_pillar_df(
    *,
    days: int = 800,
    frozen_today: date = date(2026, 4, 30),
    base_pct: float = 2.50,
    drift_pct: float = -0.30,
    vendor_ticker: str = "USSWIT5 Curncy",
    inflation_index_family: str = "US_CPI_URBAN",
    index_lag: str = "3M",
    interpolation: str = "Daily",
    underlying_index: str = "CPURNSA Index",
    pricing_type: str = "zero_coupon_breakeven",
) -> pd.DataFrame:
    """Build a long-format DataFrame matching the shape
    ``fetch_zcis_single_pillar`` returns for one (curve_family,
    tenor) leg.
    """
    bdays = pd.bdate_range(
        frozen_today - timedelta(days=days * 2), frozen_today,
    )
    bdays = bdays[-days:]
    n = len(bdays)
    series = np.linspace(base_pct, base_pct + drift_pct, n)
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": series,
        "vendor_ticker": [vendor_ticker] * n,
        "pricing_type": [pricing_type] * n,
        "inflation_index_family": [inflation_index_family] * n,
        "index_lag": [index_lag] * n,
        "interpolation": [interpolation] * n,
        "underlying_index": [underlying_index] * n,
    })


def _patched_fetch_factory(legs: dict):
    """Build a stand-in for ``fetch_zcis_single_pillar`` that
    dispatches by (curve_family, tenor).
    """
    def _stub(*, engine, curve_family, tenor, field_name, start_date):
        key = (curve_family, tenor)
        if key in legs:
            return legs[key]
        return pd.DataFrame(columns=[
            "trade_date", "field_value", "vendor_ticker",
            "pricing_type", "inflation_index_family", "index_lag",
            "interpolation", "underlying_index",
        ])
    return _stub


class _FrozenDate(date):
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


def _build_legs_for_usd_5y5y(
    *,
    short_base_pct: float = 2.30,
    long_base_pct: float = 2.55,
    short_drift_pct: float = -0.20,
    long_drift_pct: float = -0.10,
) -> dict:
    """Default canonical two-leg fixture for USD_ZCIS 5Y5Y (start=5Y,
    end=10Y).

    The two legs drift by different amounts so the resulting
    forward rate is NOT constant — necessary for the z-score /
    period-change override tests to actually surface a
    convention-driven difference (constant input → constant
    forward → undefined z-score and zero daily change).
    """
    days = 800
    return {
        ("USD_ZCIS", "5Y"): _synthetic_pillar_df(
            days=days, base_pct=short_base_pct,
            drift_pct=short_drift_pct,
            vendor_ticker="USSWIT5 Curncy",
        ),
        ("USD_ZCIS", "10Y"): _synthetic_pillar_df(
            days=days, base_pct=long_base_pct,
            drift_pct=long_drift_pct,
            vendor_ticker="USSWIT10 Curncy",
        ),
    }


# Default-convention dict shared across the override-style tests
# below.  Mirrors the bundled config.yaml exactly except per-test
# overrides applied via ``_build_config(**overrides)``.
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
    "bps_round_decimals": 2,
    "z_score_round_decimals": 4,
    "yield_round_decimals": 4,
    "high_low_round_decimals": 4,
    "window_years_round_decimals": 4,
    "default_zcis_rate_field": "PX_MID",
    "zcis_forward_compounding_mode": "dual_compounding",
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
        "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar",
        new=_patched_fetch_factory(legs),
    ), patch(
        "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date",
        _FrozenDate,
    ):
        return calculate_inflation_swap_forward(
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
        assert cfg.tool.name == "inflation_swap_forward"
        assert cfg.tool.domain == "inflation_swaps"
        assert cfg.tool.category == "desk_invariant_primitive"

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
            "bps_round_decimals",
            "z_score_round_decimals",
            "yield_round_decimals",
            "high_low_round_decimals",
            "window_years_round_decimals",
            "default_zcis_rate_field",
            "zcis_forward_compounding_mode",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults_align_with_sibling_tools(self):
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
        assert cfg.convention_value("bps_round_decimals") == 2
        assert cfg.convention_value("z_score_round_decimals") == 4
        assert cfg.convention_value("yield_round_decimals") == 4
        assert cfg.convention_value("high_low_round_decimals") == 4
        assert cfg.convention_value("window_years_round_decimals") == 4
        assert cfg.convention_value("default_zcis_rate_field") == "PX_MID"
        assert (
            cfg.convention_value("zcis_forward_compounding_mode")
            == "dual_compounding"
        )

    def test_uses_zcis_field_convention_name_not_sovereign(self):
        """ZCIS rates use ``default_zcis_rate_field`` so the cross-
        config lint stays clean across the four families.  This
        test pins that we did NOT reuse the sovereign / linker
        ``default_field_name`` (which would conflict on value)."""
        cfg = load_tool_config(CONFIG_PATH)
        assert "default_zcis_rate_field" in cfg.conventions
        assert "default_field_name" not in cfg.conventions
        assert "default_swap_rate_field" not in cfg.conventions

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        # Catalog requires at minimum: cross-market spread,
        # swap-breakeven basis, year-weighted-linear alternative,
        # date-window mode, trailing-range configurability.
        assert len(cfg.methodology.planned_extensions) >= 4
        joined = " | ".join(cfg.methodology.planned_extensions).lower()
        assert "cross-market" in joined or "cross_market" in joined
        assert "swap-breakeven basis" in joined or "swap_breakeven_basis" in joined
        assert "year_weighted_linear" in joined
        assert "trailing_range_window_days" in joined

    def test_methodology_what_it_does_carries_disclosure(self):
        cfg = load_tool_config(CONFIG_PATH)
        text = cfg.methodology.what_it_does.lower()
        assert "zcis" in text
        # Geometric forward formula MUST be visible on the wire-
        # disclosure (either by name or by the "(1 + r" pattern).
        assert "geometric" in text or "(1 +" in text or "(1 + r_long" in text
        # Same-curve invariant MUST be visible.
        assert "same-curve" in text or "single ``curve_family``" in text
        # Forward-inflation-compensation honesty caveat MUST be
        # present.
        assert "compensation" in text


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        legs = _build_legs_for_usd_5y5y()
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        for k in (
            "as_of_date",
            "curve_family",
            "start_tenor",
            "end_tenor",
            "forward_window_label",
            "forward_zcis_pct",
            "forward_zcis_bps",
            "change_1d_bps",
            "change_1w_bps",
            "change_1m_bps",
            "z_score_252d",
            "high_252d_bps",
            "low_252d_bps",
            "percentile_252d",
            "start_zcis_pct",
            "end_zcis_pct",
            "start_years",
            "end_years",
            "observation_count",
            "inflation_index_family",
            "index_lag",
            "interpolation",
            "underlying_index",
            "methodology_label",
        ):
            assert k in cm, f"missing {k}"

        assert cm["start_years"] == 5.0
        assert cm["end_years"] == 10.0
        assert cm["start_tenor"] == "5Y"
        assert cm["end_tenor"] == "10Y"
        assert cm["forward_window_label"] == "USD_ZCIS 5Y10Y"

        # Forward bps and pct decompose cleanly.
        assert isinstance(cm["forward_zcis_pct"], float)
        assert isinstance(cm["forward_zcis_bps"], float)
        assert (
            abs(cm["forward_zcis_bps"] - cm["forward_zcis_pct"] * 100)
            < 0.5
        )

    def test_dual_compounding_formula_is_correct(self):
        """Hard-pin the dual-compounding geometric forward formula
        on a synthetic fixture with constant endpoint ZCIS rates.

        With short=2.0%, long=3.0%, T_short=5, T_long=10:
            r_short_dec = 0.02, r_long_dec = 0.03
            (1.03)^10 / (1.02)^5 = 1.343916379 / 1.104080803 = 1.217225
            f = 1.217225 ^ (1/5) - 1 = 1.04007 - 1 = 0.04007 (decimal)
            forward_pct ≈ 4.0072%
        """
        days = 800
        legs = {
            ("USD_ZCIS", "5Y"): _synthetic_pillar_df(
                days=days, base_pct=2.0, drift_pct=0.0,
                vendor_ticker="USSWIT5 Curncy",
            ),
            ("USD_ZCIS", "10Y"): _synthetic_pillar_df(
                days=days, base_pct=3.0, drift_pct=0.0,
                vendor_ticker="USSWIT10 Curncy",
            ),
        }
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["start_zcis_pct"] == pytest.approx(2.0, abs=1e-6)
        assert cm["end_zcis_pct"] == pytest.approx(3.0, abs=1e-6)

        # Hand-compute expected forward.
        r_short_dec = 0.02
        r_long_dec = 0.03
        T_short = 5.0
        T_long = 10.0
        expected = (
            ((1.0 + r_long_dec) ** T_long)
            / ((1.0 + r_short_dec) ** T_short)
        ) ** (1.0 / (T_long - T_short)) - 1.0
        expected_pct = expected * 100.0

        assert cm["forward_zcis_pct"] == pytest.approx(expected_pct, abs=1e-3)
        # bps should match pct * 100 within bps_round_decimals.
        assert cm["forward_zcis_bps"] == pytest.approx(
            expected_pct * 100.0, abs=0.05,
        )

    def test_explicit_config_matches_auto_loaded(self):
        legs = _build_legs_for_usd_5y5y()
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out_auto = _run(params, legs, config=None)
        out_explicit = _run(
            params, legs, config=load_tool_config(CONFIG_PATH),
        )
        assert out_auto == out_explicit

    def test_non_integer_year_fractions_handled(self):
        """Boundary case: 1Y/5Y forward.  start_years=1.0,
        end_years=5.0, dt=4.0.  Verify the validator + display
        handles the smaller pillars."""
        days = 800
        legs = {
            ("USD_ZCIS", "1Y"): _synthetic_pillar_df(
                days=days, base_pct=2.0, drift_pct=0.0,
                vendor_ticker="USSWIT1 Curncy",
            ),
            ("USD_ZCIS", "5Y"): _synthetic_pillar_df(
                days=days, base_pct=2.5, drift_pct=0.0,
                vendor_ticker="USSWIT5 Curncy",
            ),
        }
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="1Y",
            end_tenor="5Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["start_years"] == pytest.approx(1.0, abs=1e-4)
        assert cm["end_years"] == 5.0
        # Hand-compute expected forward.
        expected = (
            ((1.025) ** 5.0)
            / ((1.02) ** 1.0)
        ) ** (1.0 / 4.0) - 1.0
        assert cm["forward_zcis_pct"] == pytest.approx(
            expected * 100.0, abs=1e-3,
        )


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def test_z_score_window_override_changes_z(self):
        legs = _build_legs_for_usd_5y5y()
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out_default = _run(params, legs, config=_build_config())
        out_short = _run(
            params, legs, config=_build_config(z_score_window_days=120),
        )
        assert (
            out_default["current_metrics"]["z_score_252d"]
            != out_short["current_metrics"]["z_score_252d"]
        )

    def test_z_score_ddof_override_changes_z(self):
        legs = _build_legs_for_usd_5y5y()
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out_sample = _run(params, legs, config=_build_config(z_score_ddof=1))
        out_pop = _run(params, legs, config=_build_config(z_score_ddof=0))
        assert (
            out_sample["current_metrics"]["z_score_252d"]
            != out_pop["current_metrics"]["z_score_252d"]
        )

    def test_period_offsets_override_changes_changes(self):
        legs = _build_legs_for_usd_5y5y()
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out_default = _run(params, legs, config=_build_config())
        out_wider = _run(
            params, legs,
            config=_build_config(daily_change_offset_rows=5),
        )
        assert (
            out_default["current_metrics"]["change_1d_bps"]
            != out_wider["current_metrics"]["change_1d_bps"]
        )

    def test_bps_round_decimals_override_changes_precision(self):
        legs = _build_legs_for_usd_5y5y(
            short_base_pct=2.345678, long_base_pct=2.567890,
        )
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out_2 = _run(
            params, legs, config=_build_config(bps_round_decimals=2),
        )
        out_4 = _run(
            params, legs, config=_build_config(bps_round_decimals=4),
        )
        v2 = out_2["current_metrics"]["forward_zcis_bps"]
        v4 = out_4["current_metrics"]["forward_zcis_bps"]
        assert v2 == round(v2, 2)
        assert v4 == round(v4, 4)


# ===========================================================================
# 4. Honest-placeholder guards
# ===========================================================================

class TestPlaceholderGuards:
    def test_unsupported_trailing_window_raises(self):
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            calculate_inflation_swap_forward(
                engine=None, params=params,
                config=_build_config(trailing_range_window_days=180),
            )
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_bps" in msg
        assert "planned_extensions" in msg

    def test_unsupported_forward_compounding_mode_raises(self):
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            calculate_inflation_swap_forward(
                engine=None, params=params,
                config=_build_config(
                    zcis_forward_compounding_mode="year_weighted_linear",
                ),
            )
        msg = str(exc_info.value)
        assert "year_weighted_linear" in msg
        assert "dual_compounding" in msg
        assert "planned_extensions" in msg


# ===========================================================================
# 5. Schema-layer behaviour
# ===========================================================================

class TestInputSchemaBehaviour:
    def test_field_name_default_is_none(self):
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
        )
        assert params.field_name is None

    def test_tenors_must_differ(self):
        with pytest.raises(Exception):
            InflationSwapForwardInput(
                curve_family="USD_ZCIS",
                start_tenor="5Y",
                end_tenor="5Y",
            )

    def test_start_must_precede_end_tenor(self):
        """Inverted forward window MUST be rejected at the schema
        layer with a precise error pointing at the year-fraction
        comparison."""
        with pytest.raises(Exception) as exc_info:
            InflationSwapForwardInput(
                curve_family="USD_ZCIS",
                start_tenor="10Y",
                end_tenor="2Y",
            )
        msg = str(exc_info.value).lower()
        assert "strictly larger" in msg or "must map" in msg

    def test_extra_fields_rejected(self):
        """``extra='forbid'`` ensures unknown kwargs surface as a
        ValidationError rather than being silently ignored — protects
        the wire from typo-pollution AND from cross-curve attempts
        sneaking in via a stray ``second_curve_family``."""
        with pytest.raises(Exception):
            InflationSwapForwardInput(
                curve_family="USD_ZCIS",
                start_tenor="5Y",
                end_tenor="10Y",
                # Surprise field — extra='forbid' should reject this.
                second_curve_family="EUR_ZCIS",
            )

    def test_curve_family_required(self):
        """A single ``curve_family`` is the only curve identifier on
        this primitive — cross-curve attempts cannot be expressed."""
        with pytest.raises(Exception):
            InflationSwapForwardInput(
                start_tenor="5Y",
                end_tenor="10Y",
            )


# ===========================================================================
# 6. field_name → YAML default fall-through
# ===========================================================================

class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_zcis_rate_field reaches
    BOTH inner level fetches (one per endpoint)."""

    def _capture_fetch_calls(self, params, config, legs):
        captured = []

        def _stub(*, engine, curve_family, tenor, field_name, start_date):
            captured.append({
                "curve_family": curve_family,
                "tenor": tenor,
                "field_name": field_name,
                "start_date": start_date,
            })
            return legs.get(
                (curve_family, tenor),
                pd.DataFrame(columns=[
                    "trade_date", "field_value", "vendor_ticker",
                    "pricing_type", "inflation_index_family",
                    "index_lag", "interpolation", "underlying_index",
                ]),
            )

        with patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar",
            new=_stub,
        ), patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date",
            _FrozenDate,
        ):
            calculate_inflation_swap_forward(
                engine=None, params=params, config=config,
            )
        return captured

    def test_omitted_field_name_uses_yaml_default(self):
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_zcis_rate_field="PX_MID"),
            _build_legs_for_usd_5y5y(),
        )
        # Two endpoints = 2 fetches.
        assert len(captured) == 2
        for call in captured:
            assert call["field_name"] == "PX_MID"

    def test_yaml_override_changes_resolved_field(self):
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_zcis_rate_field="PX_BID"),
            _build_legs_for_usd_5y5y(),
        )
        for call in captured:
            assert call["field_name"] == "PX_BID"

    def test_explicit_field_name_overrides_yaml(self):
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            field_name="PX_ASK",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_zcis_rate_field="PX_MID"),
            _build_legs_for_usd_5y5y(),
        )
        for call in captured:
            assert call["field_name"] == "PX_ASK"


# ===========================================================================
# 7. Composition guard inheritance
# ===========================================================================

class TestCompositionGuards:
    """Forward composes the level primitive twice; the level's
    no-proxy guard (instrument_type + pricing_type + curve_family
    + tenor SELECT filter) must fire on each endpoint and surface
    as a controlled error envelope when violated.
    """

    def test_missing_short_endpoint_returns_error_envelope(self):
        legs = _build_legs_for_usd_5y5y()
        # Drop the start-tenor leg entirely.
        del legs[("USD_ZCIS", "5Y")]
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "start-tenor" in out["error"].lower()
        assert "5Y" in out["error"]
        # Inner error context (instrument_type / pricing_type
        # rationale from the four-conjunct guard) is propagated.
        assert "inflation_swap" in out["error"]
        assert "zero_coupon_breakeven" in out["error"]

    def test_missing_long_endpoint_returns_error_envelope(self):
        legs = _build_legs_for_usd_5y5y()
        del legs[("USD_ZCIS", "10Y")]
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "end-tenor" in out["error"].lower()
        assert "10Y" in out["error"]
        assert "inflation_swap" in out["error"]

    def test_unknown_curve_family_returns_error_envelope(self):
        """A non-ZCIS curve_family (e.g. 'UST') yields zero rows
        through the four-conjunct SELECT guard; the inner level
        primitive surfaces the controlled error and we propagate
        it with start-tenor attribution (the start leg is tried
        first)."""
        legs: dict = {}
        params = InflationSwapForwardInput(
            curve_family="UST",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "inflation_swap" in out["error"]
        assert "zero_coupon_breakeven" in out["error"]


# ===========================================================================
# 8. Per-trade-date alignment
# ===========================================================================

class TestPerTradeDateAlignment:
    """Forward-layer inner-join discipline.

    Dates that are still missing on a leg AFTER the level
    primitive's ffill window are dropped from the displayed
    forward series rather than synthesised; brief gaps inside the
    level's ``ffill_limit_days`` window are bridged by the inner
    level clean step.  The forward layer never adds its own ffill
    across the join.
    """

    def test_missing_endpoint_date_is_dropped(self):
        """Drop a CONSECUTIVE 7-trading-day block from the long
        leg (strictly wider than ``ffill_limit_days`` = 5).  Even
        if the inner level clean step bridges the first 5 days of
        the gap, the remaining 2+ days CANNOT be carried forward,
        so the forward layer's strict inner-join MUST drop them
        from ``time_series`` rather than synthesise a value on
        those dates.
        """
        legs = _build_legs_for_usd_5y5y()

        ffill_limit = _BUNDLED_DEFAULTS["ffill_limit_days"]
        gap_size = 7
        # Sanity: gap is strictly wider than the level's ffill
        # window so the un-bridged tail is non-empty.
        assert gap_size > ffill_limit

        # Place the gap inside the displayed window but well
        # before the latest aligned trade_date so the snapshot row
        # falls outside the gap.
        all_dates = sorted(
            legs[("USD_ZCIS", "10Y")]["trade_date"].tolist(),
        )
        gap_start_idx = len(all_dates) - 130
        gap_dates = all_dates[gap_start_idx : gap_start_idx + gap_size]
        assert len(gap_dates) == gap_size

        df = legs[("USD_ZCIS", "10Y")]
        legs[("USD_ZCIS", "10Y")] = (
            df[~df["trade_date"].isin(gap_dates)].reset_index(drop=True)
        )

        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")

        bespoke_dates = [row["date"] for row in out["time_series"]]

        # Chronological order is preserved across the dropped block.
        assert bespoke_dates == sorted(bespoke_dates)

        # The un-bridged tail of the gap (anything past
        # ffill_limit_days) cannot be carried forward by the inner
        # level — the forward inner-join MUST drop those dates.
        # At least 2 dates fall past the limit (gap_size -
        # ffill_limit = 2), so a non-empty subset is checked.
        unbridged = gap_dates[ffill_limit:]
        assert len(unbridged) >= 2
        for d in unbridged:
            d_str = d.strftime("%Y-%m-%d")
            assert d_str not in bespoke_dates, (
                f"Forward time_series contains un-bridged gap "
                f"date {d_str} — strict inner-join was expected to "
                "drop it (no synthetic forward point)."
            )

        # Snapshot's latest displayed row falls outside the gap,
        # so forward_zcis_bps must still be populated.
        assert out["current_metrics"]["forward_zcis_bps"] is not None


# ===========================================================================
# 9. Reference-metadata equality guard
# ===========================================================================

class TestReferenceMetadataEqualityGuard:
    """Same-curve invariant re-asserted in code: if the two
    endpoint reads disagree on inflation_index_family /
    index_lag / interpolation / underlying_index, surface a
    controlled error envelope rather than silently picking one
    side (which would indicate a real instrument_master
    inconsistency).
    """

    def test_mismatched_index_lag_surfaces_controlled_error(self):
        days = 800
        legs = {
            ("USD_ZCIS", "5Y"): _synthetic_pillar_df(
                days=days, base_pct=2.0, drift_pct=0.0,
                vendor_ticker="USSWIT5 Curncy",
                index_lag="3M",
            ),
            ("USD_ZCIS", "10Y"): _synthetic_pillar_df(
                days=days, base_pct=3.0, drift_pct=0.0,
                vendor_ticker="USSWIT10 Curncy",
                index_lag="2M",  # mismatch — pathological
            ),
        }
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "Reference-metadata mismatch" in out["error"]
        assert "index_lag" in out["error"]
        # Both legs' values must be cited so the operator can
        # diagnose without a second tool call.
        assert "3M" in out["error"]
        assert "2M" in out["error"]

    def test_mismatched_inflation_index_family_surfaces_controlled_error(
        self,
    ):
        days = 800
        legs = {
            ("USD_ZCIS", "5Y"): _synthetic_pillar_df(
                days=days, base_pct=2.0, drift_pct=0.0,
                vendor_ticker="USSWIT5 Curncy",
                inflation_index_family="US_CPI_URBAN",
            ),
            ("USD_ZCIS", "10Y"): _synthetic_pillar_df(
                days=days, base_pct=3.0, drift_pct=0.0,
                vendor_ticker="USSWIT10 Curncy",
                inflation_index_family="US_CPI_OLD",  # mismatch
            ),
        }
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "Reference-metadata mismatch" in out["error"]
        assert "inflation_index_family" in out["error"]


# ===========================================================================
# 10. Boundary-rounding parity
# ===========================================================================

class TestBoundaryRoundingParity:
    def test_snapshot_bps_matches_bespoke_last_row_strictly(self):
        legs = _build_legs_for_usd_5y5y()
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        snap = out["current_metrics"]["forward_zcis_bps"]
        bespoke_last = out["time_series"][-1]["forward_zcis_bps"]
        assert snap == bespoke_last

    def test_snapshot_bps_matches_canonical_last_row_strictly(self):
        """time_series_forward ships in PERCENT, so the boundary
        comparison rounds (canonical PERCENT * 100) at
        bps_round_decimals before comparing with the snapshot."""
        legs = _build_legs_for_usd_5y5y()
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        snap = out["current_metrics"]["forward_zcis_bps"]
        canon_pct_last = out["time_series_forward"]["rows"][-1]["value"]
        bps_round = _BUNDLED_DEFAULTS["bps_round_decimals"]
        assert snap == round(canon_pct_last * 100, bps_round)

    def test_snapshot_pct_matches_canonical_last_row_strictly(self):
        legs = _build_legs_for_usd_5y5y()
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        snap_pct = out["current_metrics"]["forward_zcis_pct"]
        canon_pct_last = out["time_series_forward"]["rows"][-1]["value"]
        assert snap_pct == canon_pct_last

    def test_bespoke_z_score_matches_canonical_z_score_per_row(self):
        legs = _build_legs_for_usd_5y5y()
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
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
# 11. Wire-honesty disclosure threading
# ===========================================================================

class TestMethodologyLabelThreading:
    def test_methodology_label_sourced_from_yaml(self):
        legs = _build_legs_for_usd_5y5y()
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        custom_text = (
            "CUSTOM-ZCIS-FORWARD-DISCLOSURE-77 same-curve ZCIS "
            "forward via dual-compounding geometric formula"
        )
        out = _run(
            params, legs,
            config=_build_config(methodology_what_it_does=custom_text),
        )
        assert (
            out["current_metrics"]["methodology_label"]
            == custom_text.strip()
        )

    def test_bundled_methodology_label_carries_zcis_caveat(self):
        legs = _build_legs_for_usd_5y5y()
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs, config=None)
        label = out["current_metrics"]["methodology_label"].lower()
        assert "zcis" in label
        # Geometric formula and inflation-compensation caveat must
        # both be visible on the wire.
        assert "geometric" in label or "(1 +" in label
        assert "compensation" in label


# ===========================================================================
# 12. Reference-metadata surfacing
# ===========================================================================

class TestReferenceMetadataSurfacing:
    def test_reference_metadata_threads_to_current_metrics(self):
        days = 800
        legs = {
            ("EUR_ZCIS", "5Y"): _synthetic_pillar_df(
                days=days, base_pct=2.0, drift_pct=-0.10,
                vendor_ticker="EUSWI5 Curncy",
                inflation_index_family="EU_HICP",
                index_lag="3M",
                interpolation="Monthly",
                underlying_index="CPTFEMU Index",
            ),
            ("EUR_ZCIS", "10Y"): _synthetic_pillar_df(
                days=days, base_pct=2.5, drift_pct=-0.05,
                vendor_ticker="EUSWI10 Curncy",
                inflation_index_family="EU_HICP",
                index_lag="3M",
                interpolation="Monthly",
                underlying_index="CPTFEMU Index",
            ),
        }
        params = InflationSwapForwardInput(
            curve_family="EUR_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        cm = out["current_metrics"]
        assert cm["inflation_index_family"] == "EU_HICP"
        assert cm["index_lag"] == "3M"
        assert cm["interpolation"] == "Monthly"
        assert cm["underlying_index"] == "CPTFEMU Index"

    def test_reference_metadata_threads_to_canonical_description(self):
        days = 800
        legs = {
            ("EUR_ZCIS", "5Y"): _synthetic_pillar_df(
                days=days, base_pct=2.0, drift_pct=-0.10,
                vendor_ticker="EUSWI5 Curncy",
                inflation_index_family="EU_HICP",
                index_lag="3M",
                interpolation="Monthly",
            ),
            ("EUR_ZCIS", "10Y"): _synthetic_pillar_df(
                days=days, base_pct=2.5, drift_pct=-0.05,
                vendor_ticker="EUSWI10 Curncy",
                inflation_index_family="EU_HICP",
                index_lag="3M",
                interpolation="Monthly",
            ),
        }
        params = InflationSwapForwardInput(
            curve_family="EUR_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        desc = out["time_series_forward"]["description"]
        assert "EU_HICP" in desc
        assert "3M" in desc
        assert "Monthly" in desc


# ===========================================================================
# 13. Import path stability
# ===========================================================================

class TestImportPathStability:
    def test_function_via_package_init(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_forward import (
            calculate_inflation_swap_forward as via_package,
        )
        from rates_agent.inflation_swaps.tools.inflation_swap_forward.compute import (
            calculate_inflation_swap_forward as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_forward import (
            InflationSwapForwardInput as via_package,
        )
        from rates_agent.inflation_swaps.tools.inflation_swap_forward.schemas import (
            InflationSwapForwardInput as via_schemas,
        )
        from rates_agent.inflation_swaps.tools.schemas import (
            InflationSwapForwardInput as via_hub,
        )
        assert via_package is via_schemas is via_hub


# ===========================================================================
# 14. Canonical TimeSeries outputs
# ===========================================================================

class TestCanonicalTimeSeries:
    def test_canonical_forward_uses_percent_units_and_naming(self):
        legs = _build_legs_for_usd_5y5y()
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        ts = out["time_series_forward"]
        # Forward ZCIS is a *level* (percent), not a spread (bps).
        assert ts["units"] == "percent"
        assert ts["series_name"] == "usd_zcis_5y_10y_zcis_forward"

    def test_canonical_zscore_uses_z_score_units_and_naming(self):
        legs = _build_legs_for_usd_5y5y()
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        ts = out["time_series_zscore"]
        assert ts["units"] == "z_score"
        assert (
            ts["series_name"]
            == "usd_zcis_5y_10y_zcis_forward_zscore"
        )

    def test_canonical_lengths_match_bespoke(self):
        legs = _build_legs_for_usd_5y5y()
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert (
            len(out["time_series"])
            == len(out["time_series_forward"]["rows"])
        )
        assert (
            len(out["time_series"])
            == len(out["time_series_zscore"]["rows"])
        )

    def test_canonical_payloads_validate_against_TimeSeries_schema(self):
        from shared.schemas import TimeSeries
        legs = _build_legs_for_usd_5y5y()
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        TimeSeries.model_validate(out["time_series_forward"])
        TimeSeries.model_validate(out["time_series_zscore"])

    def test_canonical_dates_chronological(self):
        legs = _build_legs_for_usd_5y5y()
        params = InflationSwapForwardInput(
            curve_family="USD_ZCIS",
            start_tenor="5Y",
            end_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        dates = [r["date"] for r in out["time_series_forward"]["rows"]]
        assert dates == sorted(dates)
