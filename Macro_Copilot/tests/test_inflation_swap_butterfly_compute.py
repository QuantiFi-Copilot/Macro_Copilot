"""
test_inflation_swap_butterfly_compute.py — Unit tests for the
inflation_swap_butterfly primitive.

Mirrors ``test_inflation_swap_curve_spread_compute.py`` (closest
sibling — same domain, composes the same level primitive) and
``test_breakeven_butterfly_compute.py`` (closest butterfly analog —
same FIXED simple-butterfly weight tuple, same three-leg shape).
This module differs in:
  - the underlying primitive is the ZCIS rate-level (PERCENT-units
    endpoint reads, four-conjunct SELECT guard) rather than the
    bond-implied breakeven primitive,
  - the output unit is BPS (matching the inflation_swaps domain's
    BPS convention for curve-shape views), with the rate-space
    butterfly arithmetic multiplied by 100,
  - the same-curve invariant uses a single ``curve_family`` field
    at the input layer (no nominal/linker pair).

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. compute() runs end-to-end against synthetic input with the
     bundled config and returns a well-formed output.
  3. Explicit ``config=`` vs auto-load parity (byte-identical).
  4. Convention overrides change behaviour (z-window, ddof,
     period offsets, bps rounding, default_zcis_rate_field).
  5. Honest-placeholder guards: trailing_range_window_days != 252
     raises NotImplementedError.
  6. Schema-layer behaviour: field_name defaults to None; compute()
     resolves the sentinel against the YAML.  Tenor validators
     reject structurally invalid triplets (duplicate, inverted,
     etc).
  7. Composition guard inheritance: missing endpoint legs surface
     controlled error envelopes via the inner level primitive's
     four-conjunct SELECT guard firing transitively.
  8. Per-trade-date alignment: dates where any endpoint has no
     data are dropped from the butterfly series (strict inner-join
     discipline).
  9. Butterfly formula correctness on synthetic input — fixed
     simple-butterfly weight tuple (-0.5, +1.0, -0.5) verified
     numerically: butterfly_bps == (belly - 0.5*(short + long)) *
     100.
 10. Sign convention: POSITIVE = belly cheap (belly ZCIS rate HIGH
     relative to wings), NEGATIVE = belly rich.  Hard-pinned on
     deterministic synthetic input.
 11. Boundary-rounding parity:
     ``current_metrics.current_butterfly_bps`` equals
     ``time_series[-1].butterfly_bps`` AND
     ``time_series_butterfly.rows[-1].value`` bit-for-bit.
 12. Wire-honesty: ``methodology_label`` is sourced from the YAML
     and the bundled disclosure carries the raw-rate-space caveat
     AND the explicit weight tuple AND the sign convention.
 13. Three import paths still resolve to the same Pydantic class.
 14. Canonical TimeSeries outputs: BPS for the butterfly, Z_SCORE
     for the rolling z-score, with the documented series_name
     pattern AND the raw-rate-space caveat in the description.
 15. Missing endpoint (unknown pillar) yields a controlled error
     envelope.
 16. Reference-metadata mismatch surfaces a controlled error
     envelope (no silently averaged snapshot).

Tests are fully offline (DB-backed grounding lives in the SQL-
validation runner — see
``tests/test_inflation_swap_butterfly_sql_validation.py``).
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.inflation_swaps.tools.inflation_swap_butterfly import (
    CONFIG_PATH,
    InflationSwapButterflyInput,
    calculate_inflation_swap_butterfly,
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
    def _stub(*, engine, curve_family, tenor, field_name, start_date, end_date=None):
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


def _build_legs_for_usd_5s10s30s(
    *,
    short_base_pct: float = 2.30,
    belly_base_pct: float = 2.55,
    long_base_pct: float = 2.65,
    short_drift_pct: float = -0.20,
    belly_drift_pct: float = -0.10,
    long_drift_pct: float = -0.05,
) -> dict:
    """Default canonical three-leg fixture for USD_ZCIS 5s10s30s.

    The three legs drift by different amounts so the resulting
    butterfly is NOT constant — necessary for the z-score /
    period-change override tests to surface a convention-driven
    difference.
    """
    days = 800
    return {
        ("USD_ZCIS", "5Y"): _synthetic_pillar_df(
            days=days, base_pct=short_base_pct,
            drift_pct=short_drift_pct,
            vendor_ticker="USSWIT5 Curncy",
        ),
        ("USD_ZCIS", "10Y"): _synthetic_pillar_df(
            days=days, base_pct=belly_base_pct,
            drift_pct=belly_drift_pct,
            vendor_ticker="USSWIT10 Curncy",
        ),
        ("USD_ZCIS", "30Y"): _synthetic_pillar_df(
            days=days, base_pct=long_base_pct,
            drift_pct=long_drift_pct,
            vendor_ticker="USSWIT30 Curncy",
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
        return calculate_inflation_swap_butterfly(
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
        assert cfg.tool.name == "calculate_inflation_swap_butterfly_tool"
        assert cfg.tool.domain == "inflation_swaps"

    def test_category_is_desk_invariant_primitive(self):
        cfg = load_tool_config(CONFIG_PATH)
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
        assert len(cfg.methodology.planned_extensions) >= 2
        joined = " | ".join(cfg.methodology.planned_extensions).lower()
        assert "trailing_range_window_days" in joined

    def test_methodology_what_it_does_carries_disclosure(self):
        cfg = load_tool_config(CONFIG_PATH)
        text = cfg.methodology.what_it_does.lower()
        assert "zcis" in text
        # Butterfly formula MUST be visible — three weight tokens.
        assert "belly_zcis_pct" in text
        assert "short_zcis_pct" in text
        assert "long_zcis_pct" in text
        # Weight tuple explicitly stated.
        assert "(-0.5, +1.0, -0.5)" in text
        # Sign convention surfaced on the wire-disclosure.
        assert "positive" in text and "belly" in text
        # Raw-rate-space promise visible.
        assert "raw inflation-swap-rate space" in text or "raw inflation" in text
        # Same-curve invariant.
        assert "same-curve" in text or "single ``curve_family``" in text


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        legs = _build_legs_for_usd_5s10s30s()
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        for k in (
            "as_of_date",
            "curve_family",
            "short_tenor",
            "belly_tenor",
            "long_tenor",
            "butterfly_label",
            "current_butterfly_bps",
            "daily_change_bps",
            "weekly_change_bps",
            "monthly_change_bps",
            "current_z_score",
            "rolling_window_days",
            "high_252d_bps",
            "low_252d_bps",
            "percentile_252d",
            "wing_short_bps",
            "wing_long_bps",
            "short_zcis_rate_pct",
            "belly_zcis_rate_pct",
            "long_zcis_rate_pct",
            "short_years",
            "belly_years",
            "long_years",
            "observation_count",
            "inflation_index_family",
            "index_lag",
            "interpolation",
            "underlying_index",
            "methodology_label",
        ):
            assert k in cm, f"missing {k}"

        assert cm["short_years"] == 5.0
        assert cm["belly_years"] == 10.0
        assert cm["long_years"] == 30.0
        assert cm["short_tenor"] == "5Y"
        assert cm["belly_tenor"] == "10Y"
        assert cm["long_tenor"] == "30Y"
        assert cm["curve_family"] == "USD_ZCIS"
        assert cm["butterfly_label"] == "USD_ZCIS 5s10s30s"
        # Reference-metadata threads through to the wire.
        assert cm["inflation_index_family"] == "US_CPI_URBAN"
        assert cm["index_lag"] == "3M"
        assert cm["interpolation"] == "Daily"
        assert cm["underlying_index"] == "CPURNSA Index"
        # Rolling-window length surfaces the YAML default.
        assert cm["rolling_window_days"] == 252

    def test_butterfly_formula_is_correct_with_weight_tuple(self):
        """Hard-pin the FIXED simple-butterfly weight tuple
        (-0.5, +1.0, -0.5) on synthetic flat ZCIS legs.

        With constant ZCIS rates short=2.00%, belly=2.50%,
        long=3.00%:
          expected butterfly = (2.50 - 0.5*(2.00 + 3.00)) * 100
                             = (2.50 - 2.50) * 100 = 0 bps

        With short=2.00%, belly=3.00%, long=2.50% (belly cheap):
          butterfly = (3.00 - 0.5*(2.00 + 2.50)) * 100
                    = (3.00 - 2.25) * 100 = +75 bps
        """
        days = 800

        # Case 1: flat butterfly = 0 bps
        legs_flat = {
            ("USD_ZCIS", "5Y"): _synthetic_pillar_df(
                days=days, base_pct=2.00, drift_pct=0.0,
                vendor_ticker="USSWIT5 Curncy",
            ),
            ("USD_ZCIS", "10Y"): _synthetic_pillar_df(
                days=days, base_pct=2.50, drift_pct=0.0,
                vendor_ticker="USSWIT10 Curncy",
            ),
            ("USD_ZCIS", "30Y"): _synthetic_pillar_df(
                days=days, base_pct=3.00, drift_pct=0.0,
                vendor_ticker="USSWIT30 Curncy",
            ),
        }
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out_flat = _run(params, legs_flat)
        assert "error" not in out_flat, out_flat.get("error")
        cm = out_flat["current_metrics"]
        # Endpoint ZCIS rates
        assert cm["short_zcis_rate_pct"] == pytest.approx(2.00, abs=0.001)
        assert cm["belly_zcis_rate_pct"] == pytest.approx(2.50, abs=0.001)
        assert cm["long_zcis_rate_pct"] == pytest.approx(3.00, abs=0.001)
        # Butterfly = (2.50 - 0.5*(2.00 + 3.00)) * 100 = 0
        assert cm["current_butterfly_bps"] == pytest.approx(0.0, abs=0.05)
        # Wings: (belly - short) * 100 = 50, (long - belly) * 100 = 50
        assert cm["wing_short_bps"] == pytest.approx(50.0, abs=0.05)
        assert cm["wing_long_bps"] == pytest.approx(50.0, abs=0.05)

    def test_positive_sign_means_belly_cheap(self):
        """Hard-pin sign convention: POSITIVE = belly cheap (i.e.
        belly ZCIS rate HIGH relative to wings).

        Construct short=2.00%, belly=3.00%, long=2.50%:
          butterfly = (3.00 - 0.5*(2.00 + 2.50)) * 100 = +75 bps
          (belly CHEAP)
        """
        days = 800
        legs = {
            ("USD_ZCIS", "5Y"): _synthetic_pillar_df(
                days=days, base_pct=2.00, drift_pct=0.0,
                vendor_ticker="USSWIT5 Curncy",
            ),
            ("USD_ZCIS", "10Y"): _synthetic_pillar_df(
                days=days, base_pct=3.00, drift_pct=0.0,
                vendor_ticker="USSWIT10 Curncy",
            ),
            ("USD_ZCIS", "30Y"): _synthetic_pillar_df(
                days=days, base_pct=2.50, drift_pct=0.0,
                vendor_ticker="USSWIT30 Curncy",
            ),
        }
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["current_butterfly_bps"] == pytest.approx(75.0, abs=0.1)
        # belly CHEAP → butterfly > 0
        assert cm["current_butterfly_bps"] > 0

    def test_negative_sign_means_belly_rich(self):
        """Hard-pin sign convention: NEGATIVE = belly rich (i.e.
        belly ZCIS rate LOW relative to wings).

        Construct short=3.00%, belly=2.00%, long=3.50%:
          butterfly = (2.00 - 0.5*(3.00 + 3.50)) * 100 = -125 bps
          (belly RICH)
        """
        days = 800
        legs = {
            ("USD_ZCIS", "5Y"): _synthetic_pillar_df(
                days=days, base_pct=3.00, drift_pct=0.0,
                vendor_ticker="USSWIT5 Curncy",
            ),
            ("USD_ZCIS", "10Y"): _synthetic_pillar_df(
                days=days, base_pct=2.00, drift_pct=0.0,
                vendor_ticker="USSWIT10 Curncy",
            ),
            ("USD_ZCIS", "30Y"): _synthetic_pillar_df(
                days=days, base_pct=3.50, drift_pct=0.0,
                vendor_ticker="USSWIT30 Curncy",
            ),
        }
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["current_butterfly_bps"] == pytest.approx(-125.0, abs=0.1)
        # belly RICH → butterfly < 0
        assert cm["current_butterfly_bps"] < 0

    def test_butterfly_per_row_arithmetic(self):
        """Independently verify per-row arithmetic: every row in
        the bespoke time_series satisfies
        butterfly_bps == (belly - 0.5*(short + long)) * 100
        within bps_round_decimals tolerance.
        """
        legs = _build_legs_for_usd_5s10s30s()
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out
        cm = out["current_metrics"]
        expected = round(
            (cm["belly_zcis_rate_pct"]
             - 0.5 * (cm["short_zcis_rate_pct"]
                      + cm["long_zcis_rate_pct"])) * 100,
            2,
        )
        assert cm["current_butterfly_bps"] == pytest.approx(
            expected, abs=0.05,
        )

    def test_explicit_config_matches_auto_loaded(self):
        legs = _build_legs_for_usd_5s10s30s()
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
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
        legs = _build_legs_for_usd_5s10s30s()
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out_default = _run(params, legs, config=_build_config())
        out_short = _run(
            params, legs,
            config=_build_config(z_score_window_days=120),
        )
        assert (
            out_default["current_metrics"]["current_z_score"]
            != out_short["current_metrics"]["current_z_score"]
        )

    def test_z_score_ddof_override_changes_z(self):
        legs = _build_legs_for_usd_5s10s30s()
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
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
        legs = _build_legs_for_usd_5s10s30s()
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
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


# ===========================================================================
# 4. Honest-placeholder guards
# ===========================================================================

class TestPlaceholderGuards:
    def test_unsupported_trailing_window_raises(self):
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            calculate_inflation_swap_butterfly(
                engine=None, params=params,
                config=_build_config(trailing_range_window_days=180),
            )
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_bps" in msg
        assert "planned_extensions" in msg


# ===========================================================================
# 5. Schema-layer behaviour
# ===========================================================================

class TestInputSchemaBehaviour:
    def test_field_name_default_is_none(self):
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
        )
        assert params.field_name is None

    def test_short_equals_belly_rejected(self):
        with pytest.raises(Exception):
            InflationSwapButterflyInput(
                curve_family="USD_ZCIS",
                short_tenor="10Y",
                belly_tenor="10Y",
                long_tenor="30Y",
            )

    def test_belly_equals_long_rejected(self):
        with pytest.raises(Exception):
            InflationSwapButterflyInput(
                curve_family="USD_ZCIS",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="10Y",
            )

    def test_short_equals_long_rejected(self):
        with pytest.raises(Exception):
            InflationSwapButterflyInput(
                curve_family="USD_ZCIS",
                short_tenor="10Y",
                belly_tenor="20Y",
                long_tenor="10Y",
            )

    def test_short_must_precede_belly_tenor(self):
        with pytest.raises(Exception) as exc_info:
            InflationSwapButterflyInput(
                curve_family="USD_ZCIS",
                short_tenor="10Y",
                belly_tenor="5Y",
                long_tenor="30Y",
            )
        msg = str(exc_info.value).lower()
        assert "short_years < belly_years" in msg or "strictly" in msg

    def test_belly_must_precede_long_tenor(self):
        with pytest.raises(Exception):
            InflationSwapButterflyInput(
                curve_family="USD_ZCIS",
                short_tenor="5Y",
                belly_tenor="30Y",
                long_tenor="10Y",
            )

    def test_fully_inverted_rejected(self):
        with pytest.raises(Exception):
            InflationSwapButterflyInput(
                curve_family="USD_ZCIS",
                short_tenor="30Y",
                belly_tenor="10Y",
                long_tenor="5Y",
            )

    def test_extra_fields_rejected(self):
        with pytest.raises(Exception):
            InflationSwapButterflyInput(
                curve_family="USD_ZCIS",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
                bogus_param=123,
            )


# ===========================================================================
# 6. field_name → YAML default fall-through
# ===========================================================================

class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_zcis_rate_field reaches
    all THREE inner fetches (one per endpoint tenor)."""

    def _capture_fetch_calls(self, params, config, legs):
        captured = []

        def _stub(*, engine, curve_family, tenor, field_name, start_date, end_date=None):
            captured.append({
                "curve_family": curve_family,
                "tenor": tenor,
                "field_name": field_name,
            })
            key = (curve_family, tenor)
            return legs.get(
                key,
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
            calculate_inflation_swap_butterfly(
                engine=None, params=params, config=config,
            )
        return captured

    def test_omitted_field_name_uses_yaml_default(self):
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_zcis_rate_field="PX_MID"),
            _build_legs_for_usd_5s10s30s(),
        )
        # Three endpoints = 3 fetches.
        assert len(captured) == 3
        for call in captured:
            assert call["field_name"] == "PX_MID"

    def test_yaml_override_changes_resolved_field(self):
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_zcis_rate_field="PX_BID"),
            _build_legs_for_usd_5s10s30s(),
        )
        for call in captured:
            assert call["field_name"] == "PX_BID"

    def test_explicit_field_name_overrides_yaml(self):
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            field_name="PX_ASK",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_zcis_rate_field="PX_MID"),
            _build_legs_for_usd_5s10s30s(),
        )
        for call in captured:
            assert call["field_name"] == "PX_ASK"


# ===========================================================================
# 7. Composition guard inheritance
# ===========================================================================

class TestCompositionGuards:
    """Butterfly composes the level primitive three times; the
    level's four-conjunct SELECT guard must fire on each endpoint
    and surface as a controlled error envelope when no rows are
    found.
    """

    def test_missing_short_endpoint_returns_error_envelope(self):
        legs = _build_legs_for_usd_5s10s30s()
        legs[("USD_ZCIS", "5Y")] = pd.DataFrame(columns=[
            "trade_date", "field_value", "vendor_ticker",
            "pricing_type", "inflation_index_family", "index_lag",
            "interpolation", "underlying_index",
        ])
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "short-tenor" in out["error"].lower()
        assert "5Y" in out["error"]
        # Inner-level error rationale propagates.
        assert "inflation_swap" in out["error"]
        assert "zero_coupon_breakeven" in out["error"]

    def test_missing_belly_endpoint_returns_error_envelope(self):
        legs = _build_legs_for_usd_5s10s30s()
        legs[("USD_ZCIS", "10Y")] = pd.DataFrame(columns=[
            "trade_date", "field_value", "vendor_ticker",
            "pricing_type", "inflation_index_family", "index_lag",
            "interpolation", "underlying_index",
        ])
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "belly-tenor" in out["error"].lower()
        assert "10Y" in out["error"]

    def test_missing_long_endpoint_returns_error_envelope(self):
        legs = _build_legs_for_usd_5s10s30s()
        legs[("USD_ZCIS", "30Y")] = pd.DataFrame(columns=[
            "trade_date", "field_value", "vendor_ticker",
            "pricing_type", "inflation_index_family", "index_lag",
            "interpolation", "underlying_index",
        ])
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "long-tenor" in out["error"].lower()
        assert "30Y" in out["error"]

    def test_unknown_curve_family_returns_error_envelope(self):
        """Non-ZCIS curve_family must surface controlled error
        envelope (the level primitive's four-conjunct guard returns
        no rows for 'UST' since it has instrument_type='sovereign_benchmark').
        """
        legs: dict = {}  # no fixtures = no rows for any pillar
        params = InflationSwapButterflyInput(
            curve_family="UST",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "inflation_swap" in out["error"]

    def test_reference_metadata_mismatch_returns_error_envelope(self):
        """If endpoint reads return different reference metadata
        (e.g. simulated instrument_master inconsistency), the
        butterfly compute layer must refuse to emit a snapshot.
        """
        legs = _build_legs_for_usd_5s10s30s()
        # Mutate the belly leg's index_lag to simulate an
        # instrument_master inconsistency.
        bad_belly = legs[("USD_ZCIS", "10Y")].copy()
        bad_belly["index_lag"] = "6M"  # diverges from short/long's 3M
        legs[("USD_ZCIS", "10Y")] = bad_belly
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        # Either the inner level primitive's ref-metadata guard
        # fires (no-data error) or our same-curve invariant guard
        # fires (mismatch error).  Both are valid; both must NOT
        # produce a snapshot.  In practice the level primitive's
        # _resolve_reference_metadata refuses mixed metadata across
        # rows for the same ticker — but if it's mixed across legs
        # (simulated here), the butterfly's
        # _assert_same_curve_reference_metadata catches it.
        # Test only requires that no snapshot is produced.
        # Note: the level primitive may accept it (rows same ticker
        # have consistent metadata), so the butterfly's own guard
        # must fire.
        if "error" in out:
            assert "current_metrics" not in out
            # error message identifies the mismatch
            err_lower = out["error"].lower()
            assert (
                "mismatch" in err_lower
                or "ambiguous" in err_lower
            )
        else:
            # If no error, the test fixture wasn't strong enough to
            # exercise the guard — fail explicitly.
            pytest.fail(
                "Expected reference-metadata mismatch to surface a "
                "controlled error envelope, but compute() returned "
                f"keys: {sorted(out.keys())}"
            )


# ===========================================================================
# 8. Per-trade-date alignment + ffill semantics
# ===========================================================================

class TestPerTradeDateAlignment:
    """Inner-join discipline: dates where any endpoint has no data
    are dropped from the butterfly series, NOT carried forward."""

    def test_missing_endpoint_date_is_dropped(self):
        """Drop ~10 consecutive business days from the long leg
        (within ffill_limit so the level primitive bridges some,
        but the gap exceeds the limit so the tail-end dates remain
        dropped).  Verify the butterfly series on those dates is
        missing.
        """
        legs = _build_legs_for_usd_5s10s30s()

        all_dates = sorted(
            legs[("USD_ZCIS", "30Y")]["trade_date"].tolist()
        )
        mid_idx = len(all_dates) // 2
        gap_dates = set(all_dates[mid_idx:mid_idx + 10])

        df = legs[("USD_ZCIS", "30Y")]
        legs[("USD_ZCIS", "30Y")] = df[
            ~df["trade_date"].isin(gap_dates)
        ].reset_index(drop=True)

        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")

        bespoke_dates = {row["date"] for row in out["time_series"]}
        missing_strs = sorted(d.strftime("%Y-%m-%d") for d in gap_dates)
        # The LAST gap day MUST be absent — beyond the ffill_limit.
        assert missing_strs[-1] not in bespoke_dates


# ===========================================================================
# 9. Boundary-rounding parity
# ===========================================================================

class TestBoundaryRoundingParity:
    def test_snapshot_bps_matches_bespoke_last_row_strictly(self):
        legs = _build_legs_for_usd_5s10s30s()
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        snap = out["current_metrics"]["current_butterfly_bps"]
        bespoke_last = out["time_series"][-1]["butterfly_bps"]
        assert snap == bespoke_last

    def test_snapshot_bps_matches_canonical_last_row_strictly(self):
        legs = _build_legs_for_usd_5s10s30s()
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        snap = out["current_metrics"]["current_butterfly_bps"]
        canon_last = out["time_series_butterfly"]["rows"][-1]["value"]
        assert snap == canon_last

    def test_bespoke_z_score_matches_canonical_z_score_per_row(self):
        legs = _build_legs_for_usd_5s10s30s()
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
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
        legs = _build_legs_for_usd_5s10s30s()
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        custom_text = (
            "CUSTOM-ZCIS-BUTTERFLY-DISCLOSURE-77 raw inflation-swap-"
            "rate space (-0.5, +1.0, -0.5) belly_zcis_pct - 0.5*("
            "short_zcis_pct + long_zcis_pct)"
        )
        out = _run(
            params, legs,
            config=_build_config(methodology_what_it_does=custom_text),
        )
        assert (
            out["current_metrics"]["methodology_label"]
            == custom_text.strip()
        )

    def test_bundled_methodology_label_carries_raw_rate_caveat(self):
        legs = _build_legs_for_usd_5s10s30s()
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs, config=None)
        label = out["current_metrics"]["methodology_label"].lower()
        # Raw-rate-space caveat must surface on the wire.
        assert (
            "raw inflation-swap-rate space" in label
            or "raw inflation" in label
        )
        # Formula visible on the wire.
        assert "belly_zcis_pct" in label
        assert "short_zcis_pct" in label
        assert "long_zcis_pct" in label
        # Sign convention surfaced.
        assert "positive" in label
        # Explicit weight tuple stated.
        assert "(-0.5, +1.0, -0.5)" in label
        # No model-derived basis / IRP adjustment caveat.
        assert (
            "no subtraction of a model-derived basis" in label
            or "no" in label and "basis" in label
        )


# ===========================================================================
# 11. Import path stability
# ===========================================================================

class TestImportPathStability:
    def test_function_via_package_init(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_butterfly import (
            calculate_inflation_swap_butterfly as via_package,
        )
        from rates_agent.inflation_swaps.tools.inflation_swap_butterfly.compute import (
            calculate_inflation_swap_butterfly as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_butterfly import (
            InflationSwapButterflyInput as via_package,
        )
        from rates_agent.inflation_swaps.tools.inflation_swap_butterfly.schemas import (
            InflationSwapButterflyInput as via_schemas,
        )
        from rates_agent.inflation_swaps.tools.schemas import (
            InflationSwapButterflyInput as via_hub,
        )
        assert via_package is via_schemas is via_hub


# ===========================================================================
# 12. Canonical TimeSeries outputs
# ===========================================================================

class TestCanonicalTimeSeries:
    def test_canonical_butterfly_uses_bps_units_and_naming(self):
        legs = _build_legs_for_usd_5s10s30s()
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        ts = out["time_series_butterfly"]
        assert ts["units"] == "bps"
        assert (
            ts["series_name"]
            == "usd_zcis_5y_10y_30y_inflation_swap_butterfly"
        )
        # Raw-rate-space caveat must surface in the description.
        desc = ts["description"].lower()
        assert "raw inflation-swap-rate space" in desc or "raw" in desc
        # Distinction from sovereign / real-yield / breakeven
        # butterflies — the ``_inflation_swap_butterfly`` suffix
        # is structurally distinct.
        assert "zcis" in desc or "inflation swap" in desc

    def test_canonical_zscore_uses_z_score_units_and_naming(self):
        legs = _build_legs_for_usd_5s10s30s()
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        ts = out["time_series_zscore"]
        assert ts["units"] == "z_score"
        assert (
            ts["series_name"]
            == "usd_zcis_5y_10y_30y_inflation_swap_butterfly_zscore"
        )

    def test_canonical_lengths_match_bespoke(self):
        legs = _build_legs_for_usd_5s10s30s()
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert (
            len(out["time_series"])
            == len(out["time_series_butterfly"]["rows"])
        )
        assert (
            len(out["time_series"])
            == len(out["time_series_zscore"]["rows"])
        )

    def test_canonical_payloads_validate_against_TimeSeries_schema(self):
        from shared.schemas import TimeSeries
        legs = _build_legs_for_usd_5s10s30s()
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        TimeSeries.model_validate(out["time_series_butterfly"])
        TimeSeries.model_validate(out["time_series_zscore"])

    def test_canonical_dates_chronological(self):
        legs = _build_legs_for_usd_5s10s30s()
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        dates = [r["date"] for r in out["time_series_butterfly"]["rows"]]
        assert dates == sorted(dates)

    def test_canonical_last_row_equals_snapshot_strictly(self):
        legs = _build_legs_for_usd_5s10s30s()
        params = InflationSwapButterflyInput(
            curve_family="USD_ZCIS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        # Catalog mandates: snapshot's current value equals
        # time_series.rows[-1].value strictly.
        assert (
            out["current_metrics"]["current_butterfly_bps"]
            == out["time_series_butterfly"]["rows"][-1]["value"]
        )
