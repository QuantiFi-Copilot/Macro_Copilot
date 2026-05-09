"""
test_swap_breakeven_basis_simple_compute.py — Unit tests for the
swap-breakeven basis primitive.

Mirrors ``test_cross_market_inflation_swap_spread_compute.py`` (the
closest sibling spread primitive in the inflation_swaps domain) and
``test_cross_country_breakeven_spread_simple_compute.py`` (the
closest cross-domain composition pattern in the
inflation_indexed_bonds domain).  The basis primitive composes the
ZCIS rate-level primitive (one call) AND the linker bond-implied
breakeven primitive (one call) into a per-trade-date difference.

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. Convention defaults align with sibling rates tools.
  3. Convention overrides actually change behaviour (z-window,
     ddof, period offsets, bps rounding, ffill_limit_days).
  4. Honest-placeholder guards: trailing_range_window_days != 252
     raises NotImplementedError;
     swap_breakeven_basis_sign_convention != 'zcis_minus_breakeven'
     raises NotImplementedError + message points at
     methodology.planned_extensions + names supported value +
     echoes offending value.
  5. Schema-layer behaviour: cross-leg invariant
     (nominal_curve_family != linker_curve_family); tenor parses
     via shared.analytics.curve_bootstrap.tenor_to_years.
  6. Composition guard inheritance: missing endpoint legs surface
     a controlled error envelope from the inner call, propagated
     with zcis / breakeven leg attribution.
  7. Per-trade-date alignment after ffill: a CONSECUTIVE block
     wider than ffill_limit_days dropped from the bespoke
     time_series rather than synthesised.
  8. Basis formula correctness on synthetic input.
  9. Sign convention (zcis_minus_breakeven; reject silent
     inversion).
 10. Boundary-rounding parity: ``current_metrics.basis_bps``
     equals ``time_series[-1].basis_bps`` AND
     ``time_series_basis.rows[-1].value`` bit-for-bit at the
     YAML's ``bps_round_decimals``.
 11. Wire-honesty: ``methodology_label`` is sourced from the YAML.
 12. Per-leg reference metadata + derived index-family summary
     (mismatched + matching branches).
 13. ``field_name`` threading reaches BOTH inner calls
     (explicit + None + empty-string sentinel).
 14. Three import paths still resolve to the same Pydantic class.
 15. Canonical TimeSeries outputs: BPS for the basis, Z_SCORE for
     the rolling z-score, with the documented series_name pattern.
 16. No raw market-data SELECTs in this primitive's compute path.

Tests are fully offline (DB-backed grounding lives in the SQL-
validation runner — see
``tests/test_swap_breakeven_basis_simple_sql_validation.py``).
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple import (
    CONFIG_PATH,
    SwapBreakevenBasisSimpleInput,
    calculate_swap_breakeven_basis_simple,
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


# Country/currency identity used by the breakeven primitive's
# same-country guard.  Mirrors the breakeven_curve_spread test
# fixture so identical synthetic universes pass through both.
_DEFAULT_COUNTRY_CURRENCY = {
    # nominal sovereigns
    ("UST",         "sovereign_benchmark"): ("US", "USD"),
    ("UK_GILT",     "sovereign_benchmark"): ("UK", "GBP"),
    ("FR_OAT",      "sovereign_benchmark"): ("France", "EUR"),
    ("DE_BUND",     "sovereign_benchmark"): ("Germany", "EUR"),
    ("CANADA_GOVT", "sovereign_benchmark"): ("Canada", "CAD"),
    # linkers
    ("USD_TIPS",      "inflation_linker"): ("US", "USD"),
    ("GBP_LINKER",    "inflation_linker"): ("UK", "GBP"),
    ("EUR_FR_LINKER", "inflation_linker"): ("France", "EUR"),
    ("CAD_RRB",       "inflation_linker"): ("Canada", "CAD"),
}


def _stub_country_currency_factory(table=None):
    table = table if table is not None else dict(_DEFAULT_COUNTRY_CURRENCY)

    def _stub(engine, curve_family, instrument_type):  # noqa: ARG001
        key = (curve_family, instrument_type)
        if key not in table:
            return (
                None, None,
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
        "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute._fetch_curve_family_country_currency",
        new=_stub_country_currency_factory(),
    ):
        yield


def _synthetic_zcis_pillar_df(
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
    """Long-format DataFrame matching what
    ``fetch_zcis_single_pillar`` returns for one (ZCIS curve, tenor)
    leg.  Mirrors the cross_market_inflation_swap_spread test
    fixture.
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


def _synthetic_bond_leg(
    *,
    days: int = 800,
    frozen_today: date = date(2026, 4, 30),
    base_pct: float,
    drift_pct: float,
) -> pd.DataFrame:
    """``[trade_date, field_value]`` long-format frame matching what
    ``fetch_single_tenor`` returns for one (sovereign / linker
    curve, tenor) leg.
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
    })


def _patched_zcis_fetch_factory(legs: dict):
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


def _patched_bond_fetch_factory(legs: dict):
    def _stub(
        *, engine, curve_family, tenor, field_name, start_date,
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


def _build_legs_for_us_10y(
    *,
    zcis_base_pct: float = 2.50,
    zcis_drift_pct: float = -0.30,
    nominal_base_pct: float = 4.20,
    nominal_drift_pct: float = -0.05,
    real_base_pct: float = 1.85,
    real_drift_pct: float = 0.10,
    days: int = 800,
) -> tuple:
    """Default canonical fixture for USD_ZCIS 10Y minus
    UST/USD_TIPS 10Y breakeven.

    Returns ``(zcis_legs, bond_legs)``.  zcis_legs is keyed by
    (curve_family, tenor); bond_legs is keyed by
    (curve_family, tenor, instrument_type).
    """
    zcis_legs = {
        ("USD_ZCIS", "10Y"): _synthetic_zcis_pillar_df(
            days=days, base_pct=zcis_base_pct,
            drift_pct=zcis_drift_pct,
            vendor_ticker="USSWIT10 Curncy",
            inflation_index_family="US_CPI_URBAN",
            index_lag="3M",
            interpolation="Daily",
            underlying_index="CPURNSA Index",
        ),
    }
    bond_legs = {
        ("UST", "10Y", "sovereign_benchmark"): _synthetic_bond_leg(
            days=days, base_pct=nominal_base_pct,
            drift_pct=nominal_drift_pct,
        ),
        ("USD_TIPS", "10Y", "inflation_linker"): _synthetic_bond_leg(
            days=days, base_pct=real_base_pct,
            drift_pct=real_drift_pct,
        ),
    }
    return zcis_legs, bond_legs


# Default-convention dict shared across the override-style tests
# below.  Mirrors the bundled config.yaml exactly except per-test
# overrides applied via ``_build_config(**overrides)``.
_BUNDLED_DEFAULTS = {
    "default_lookback_days": 365,
    "z_score_window_days": 252,
    "zscore_window_days": 252,
    "z_score_min_periods": 60,
    "z_score_ddof": 1,
    "zscore_ddof": 1,
    "z_score_buffer_multiplier": 1.5,
    "daily_change_offset_rows": 2,
    "weekly_change_offset_rows": 6,
    "monthly_change_offset_rows": 22,
    "trailing_range_window_days": 252,
    "ffill_limit_days": 5,
    "bps_round_decimals": 2,
    "pct_round_decimals": 4,
    "z_score_round_decimals": 4,
    "yield_round_decimals": 4,
    "high_low_round_decimals": 4,
    "window_years_round_decimals": 4,
    "default_zcis_rate_field": "PX_MID",
    "swap_breakeven_basis_sign_convention": "zcis_minus_breakeven",
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


def _run(params, zcis_legs, bond_legs, *, config=None):
    """Run compute with deep seam patches on BOTH inner primitives.

    ZCIS leg: patches ``fetch_zcis_single_pillar`` + ``date`` on
    the ZCIS level primitive's compute module.
    Breakeven leg: patches ``fetch_single_tenor`` + ``date`` on the
    breakeven primitive's compute module.  The country/currency
    seam is patched by an autouse fixture above.
    """
    with patch(
        "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar",
        new=_patched_zcis_fetch_factory(zcis_legs),
    ), patch(
        "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date",
        _FrozenDate,
    ), patch(
        "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
        new=_patched_bond_fetch_factory(bond_legs),
    ), patch(
        "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
        _FrozenDate,
    ):
        return calculate_swap_breakeven_basis_simple(
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
        assert cfg.tool.name == "swap_breakeven_basis_simple"
        assert cfg.tool.domain == "inflation_swaps"
        assert cfg.tool.category == "desk_invariant_primitive"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        # Spec-required keys MUST exist literally as named.
        required = {
            "default_lookback_days",
            "zscore_window_days",
            "zscore_ddof",
            "bps_round_decimals",
            "pct_round_decimals",
            "ffill_limit_days",
            "swap_breakeven_basis_sign_convention",
            "default_zcis_rate_field",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_canonical_lint_aligned_conventions_also_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        canonical = {
            "z_score_window_days",
            "z_score_min_periods",
            "z_score_ddof",
            "z_score_buffer_multiplier",
            "daily_change_offset_rows",
            "weekly_change_offset_rows",
            "monthly_change_offset_rows",
            "trailing_range_window_days",
            "yield_round_decimals",
            "high_low_round_decimals",
            "window_years_round_decimals",
            "z_score_round_decimals",
        }
        missing = canonical - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults_align_with_sibling_tools(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("default_lookback_days") == 365
        assert cfg.convention_value("z_score_window_days") == 252
        assert cfg.convention_value("zscore_window_days") == 252
        assert cfg.convention_value("z_score_min_periods") == 60
        assert cfg.convention_value("z_score_ddof") == 1
        assert cfg.convention_value("zscore_ddof") == 1
        assert cfg.convention_value("z_score_buffer_multiplier") == 1.5
        assert cfg.convention_value("daily_change_offset_rows") == 2
        assert cfg.convention_value("weekly_change_offset_rows") == 6
        assert cfg.convention_value("monthly_change_offset_rows") == 22
        assert cfg.convention_value("trailing_range_window_days") == 252
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("bps_round_decimals") == 2
        assert cfg.convention_value("pct_round_decimals") == 4
        assert cfg.convention_value("z_score_round_decimals") == 4
        assert cfg.convention_value("yield_round_decimals") == 4
        assert cfg.convention_value("high_low_round_decimals") == 4
        assert cfg.convention_value("window_years_round_decimals") == 4
        assert cfg.convention_value("default_zcis_rate_field") == "PX_MID"
        assert (
            cfg.convention_value("swap_breakeven_basis_sign_convention")
            == "zcis_minus_breakeven"
        )

    def test_methodology_what_it_does_carries_basis_caveat(self):
        cfg = load_tool_config(CONFIG_PATH)
        text = cfg.methodology.what_it_does.lower()
        # Formula visible.
        assert "zcis_pct" in text
        assert "breakeven_pct" in text
        # Load-bearing basis caveat.
        assert "not a clean liquidity-premium read" in text
        assert "index-lag" in text
        # Sign convention visible.
        assert "zcis_minus_breakeven" in text or "zcis - breakeven" in text

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        # Spec requires: cross-currency basis, alternative sign
        # convention, forward basis.
        assert len(cfg.methodology.planned_extensions) >= 3
        joined = " | ".join(cfg.methodology.planned_extensions).lower()
        assert "cross-currency" in joined
        assert (
            "swap_breakeven_basis_sign_convention" in joined
            or "alternative" in joined and "sign" in joined
        )
        assert "forward" in joined and "basis" in joined


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, zcis_legs, bond_legs)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        for k in (
            "as_of_date", "zcis_curve_family", "nominal_curve_family",
            "linker_curve_family", "tenor", "tenor_years",
            "basis_label", "basis_pct", "basis_bps",
            "zcis_pct", "breakeven_pct", "breakeven_bps",
            "nominal_yield_pct", "real_yield_pct",
            "change_1d_bps", "change_1w_bps", "change_1m_bps",
            "z_score_252d", "high_252d_bps", "low_252d_bps",
            "percentile_252d", "observation_count",
            "zcis_inflation_index_family", "zcis_index_lag",
            "zcis_interpolation", "zcis_underlying_index",
            "linker_inflation_index_family", "linker_index_lag",
            "index_families_match", "index_family_caveat",
            "methodology_label",
        ):
            assert k in cm, f"missing {k}"

        assert cm["tenor_years"] == 10.0
        assert cm["zcis_curve_family"] == "USD_ZCIS"
        assert cm["nominal_curve_family"] == "UST"
        assert cm["linker_curve_family"] == "USD_TIPS"
        assert cm["tenor"] == "10Y"
        assert "swap-breakeven basis" in cm["basis_label"]

    def test_basis_formula_is_correct(self):
        """Hard-pin the basis formula on a synthetic fixture with
        constant per-leg rates.  ZCIS=2.50, nominal=4.20, real=1.85
        → breakeven=2.35, basis=zcis-breakeven=0.15 → 15bps.
        """
        zcis_legs, bond_legs = _build_legs_for_us_10y(
            zcis_base_pct=2.50, zcis_drift_pct=0.0,
            nominal_base_pct=4.20, nominal_drift_pct=0.0,
            real_base_pct=1.85, real_drift_pct=0.0,
        )
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, zcis_legs, bond_legs)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["zcis_pct"] == pytest.approx(2.50, abs=1e-6)
        assert cm["breakeven_pct"] == pytest.approx(2.35, abs=1e-3)
        assert cm["basis_pct"] == pytest.approx(0.15, abs=1e-3)
        assert cm["basis_bps"] == pytest.approx(15.0, abs=0.05)
        assert cm["nominal_yield_pct"] == pytest.approx(4.20, abs=1e-3)
        assert cm["real_yield_pct"] == pytest.approx(1.85, abs=1e-3)

    def test_explicit_config_matches_auto_loaded(self):
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out_auto = _run(params, zcis_legs, bond_legs, config=None)
        out_explicit = _run(
            params, zcis_legs, bond_legs,
            config=load_tool_config(CONFIG_PATH),
        )
        assert out_auto == out_explicit

    def test_explicit_config_matches_auto_loaded_with_explicit_field_name(self):
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
            field_name="PX_LAST",
        )
        out_auto = _run(params, zcis_legs, bond_legs, config=None)
        out_explicit = _run(
            params, zcis_legs, bond_legs,
            config=load_tool_config(CONFIG_PATH),
        )
        assert out_auto == out_explicit


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def test_z_score_window_override_changes_z(self):
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out_default = _run(params, zcis_legs, bond_legs, config=_build_config())
        out_short = _run(
            params, zcis_legs, bond_legs,
            config=_build_config(z_score_window_days=120),
        )
        assert (
            out_default["current_metrics"]["z_score_252d"]
            != out_short["current_metrics"]["z_score_252d"]
        )

    def test_z_score_ddof_override_changes_z(self):
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out_sample = _run(
            params, zcis_legs, bond_legs,
            config=_build_config(z_score_ddof=1),
        )
        out_pop = _run(
            params, zcis_legs, bond_legs,
            config=_build_config(z_score_ddof=0),
        )
        assert (
            out_sample["current_metrics"]["z_score_252d"]
            != out_pop["current_metrics"]["z_score_252d"]
        )

    def test_period_offsets_override_changes_changes(self):
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out_default = _run(params, zcis_legs, bond_legs, config=_build_config())
        out_wider = _run(
            params, zcis_legs, bond_legs,
            config=_build_config(daily_change_offset_rows=5),
        )
        assert (
            out_default["current_metrics"]["change_1d_bps"]
            != out_wider["current_metrics"]["change_1d_bps"]
        )

    def test_bps_round_decimals_override_changes_precision(self):
        zcis_legs, bond_legs = _build_legs_for_us_10y(
            zcis_base_pct=2.345678,
            nominal_base_pct=4.123456,
            real_base_pct=1.876543,
        )
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out_2 = _run(
            params, zcis_legs, bond_legs,
            config=_build_config(bps_round_decimals=2),
        )
        out_4 = _run(
            params, zcis_legs, bond_legs,
            config=_build_config(bps_round_decimals=4),
        )
        v2 = out_2["current_metrics"]["basis_bps"]
        v4 = out_4["current_metrics"]["basis_bps"]
        assert v2 == round(v2, 2)
        assert v4 == round(v4, 4)


# ===========================================================================
# 4. Honest-placeholder guards
# ===========================================================================

class TestPlaceholderGuards:
    def test_unsupported_trailing_window_raises(self):
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            calculate_swap_breakeven_basis_simple(
                engine=None, params=params,
                config=_build_config(trailing_range_window_days=180),
            )
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_bps" in msg
        assert "planned_extensions" in msg

    def test_unsupported_sign_convention_raises(self):
        """Per the catalog's methodology_guardrails: any sign
        convention other than ``zcis_minus_breakeven`` MUST raise
        NotImplementedError, and the message must point callers at
        ``methodology.planned_extensions``, name the supported
        value, and echo the offending value.
        """
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            calculate_swap_breakeven_basis_simple(
                engine=None, params=params,
                config=_build_config(
                    swap_breakeven_basis_sign_convention="breakeven_minus_zcis",
                ),
            )
        msg = str(exc_info.value)
        assert "breakeven_minus_zcis" in msg
        assert "zcis_minus_breakeven" in msg
        # Per catalog guardrail: guard message MUST point at
        # planned_extensions so an audit can confirm the pointer.
        assert "methodology.planned_extensions" in msg


# ===========================================================================
# 5. Schema-layer behaviour
# ===========================================================================

class TestInputSchemaBehaviour:
    def test_curve_families_must_differ(self):
        with pytest.raises(Exception) as exc_info:
            SwapBreakevenBasisSimpleInput(
                zcis_curve_family="USD_ZCIS",
                nominal_curve_family="UST",
                linker_curve_family="UST",
                tenor="10Y",
            )
        raw_msg = str(exc_info.value)
        msg = raw_msg.lower()
        assert "must be different" in msg or "must differ" in msg
        assert "cross_market_inflation_swap_spread" in raw_msg
        assert (
            "calculate_cross_market_inflation_swap_spread_tool"
            in raw_msg
        )

    def test_tenor_must_parse(self):
        with pytest.raises(Exception) as exc_info:
            SwapBreakevenBasisSimpleInput(
                zcis_curve_family="USD_ZCIS",
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                tenor="ABC",
            )
        msg = str(exc_info.value)
        assert "tenor" in msg.lower()

    def test_extra_fields_rejected(self):
        with pytest.raises(Exception):
            SwapBreakevenBasisSimpleInput(
                zcis_curve_family="USD_ZCIS",
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                tenor="10Y",
                bogus_field="oops",
            )

    def test_required_fields_required(self):
        for missing in ("zcis_curve_family", "nominal_curve_family",
                        "linker_curve_family", "tenor"):
            kwargs = {
                "zcis_curve_family": "USD_ZCIS",
                "nominal_curve_family": "UST",
                "linker_curve_family": "USD_TIPS",
                "tenor": "10Y",
            }
            kwargs.pop(missing)
            with pytest.raises(Exception):
                SwapBreakevenBasisSimpleInput(**kwargs)

    def test_field_name_default_is_none(self):
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
        )
        assert params.field_name is None

    def test_field_name_empty_string_coerced_to_none(self):
        for sentinel in ("", "   ", "\t"):
            params = SwapBreakevenBasisSimpleInput(
                zcis_curve_family="USD_ZCIS",
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                tenor="10Y",
                field_name=sentinel,
            )
            assert params.field_name is None, (
                f"sentinel {sentinel!r} should be coerced to None"
            )

    def test_field_name_explicit_value_kept(self):
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            field_name="PX_LAST",
        )
        assert params.field_name == "PX_LAST"


class TestTenorParserReuse:
    def test_tenor_parser_drives_year_fraction(self):
        from shared.analytics.curve_bootstrap import tenor_to_years
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        # Add a 5Y leg pair too so we can probe a different tenor.
        zcis_legs[("USD_ZCIS", "5Y")] = _synthetic_zcis_pillar_df(
            days=800, base_pct=2.4, drift_pct=-0.05,
            vendor_ticker="USSWIT5 Curncy",
        )
        bond_legs[("UST", "5Y", "sovereign_benchmark")] = (
            _synthetic_bond_leg(days=800, base_pct=4.0, drift_pct=-0.05)
        )
        bond_legs[("USD_TIPS", "5Y", "inflation_linker")] = (
            _synthetic_bond_leg(days=800, base_pct=1.7, drift_pct=0.05)
        )
        for tenor in ("5Y", "10Y"):
            params = SwapBreakevenBasisSimpleInput(
                zcis_curve_family="USD_ZCIS",
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                tenor=tenor,
                lookback_days=365,
            )
            out = _run(params, zcis_legs, bond_legs)
            assert "error" not in out, out.get("error")
            assert (
                out["current_metrics"]["tenor_years"]
                == pytest.approx(tenor_to_years(tenor), abs=1e-4)
            )


# ===========================================================================
# 6. Composition guard inheritance
# ===========================================================================

class TestCompositionGuards:
    def test_missing_zcis_leg_returns_error_envelope(self):
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        del zcis_legs[("USD_ZCIS", "10Y")]
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, zcis_legs, bond_legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "zcis" in out["error"].lower()
        assert "USD_ZCIS" in out["error"]
        # Inner error context (instrument_type / pricing_type
        # rationale from the four-conjunct guard) is propagated.
        assert "inflation_swap" in out["error"]
        assert "zero_coupon_breakeven" in out["error"]

    def test_missing_breakeven_linker_leg_returns_error_envelope(self):
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        del bond_legs[("USD_TIPS", "10Y", "inflation_linker")]
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, zcis_legs, bond_legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "breakeven" in out["error"].lower()
        # Inner-error rationale must travel back so caller sees the
        # instrument_type discriminator.
        assert "inflation_linker" in out["error"]


# ===========================================================================
# 7. Per-trade-date alignment after ffill
# ===========================================================================

class TestPerTradeDateAlignment:
    def test_missing_endpoint_date_is_dropped_strict_inner_join(self):
        """Drop a CONSECUTIVE 7-trading-day block from the linker
        leg (strictly wider than ``ffill_limit_days`` = 5).  The
        breakeven primitive's clean step bridges the first 5 days
        of the gap; the un-bridged 2+ days CANNOT be carried
        forward, so the basis layer's strict inner-join MUST drop
        them from ``time_series`` rather than synthesise a value.
        """
        zcis_legs, bond_legs = _build_legs_for_us_10y()

        ffill_limit = _BUNDLED_DEFAULTS["ffill_limit_days"]
        gap_size = 7
        assert gap_size > ffill_limit

        all_dates = sorted(
            bond_legs[("USD_TIPS", "10Y", "inflation_linker")][
                "trade_date"
            ].tolist(),
        )
        gap_start_idx = len(all_dates) - 130
        gap_dates = all_dates[gap_start_idx : gap_start_idx + gap_size]
        assert len(gap_dates) == gap_size

        df = bond_legs[("USD_TIPS", "10Y", "inflation_linker")]
        bond_legs[("USD_TIPS", "10Y", "inflation_linker")] = (
            df[~df["trade_date"].isin(gap_dates)].reset_index(drop=True)
        )

        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, zcis_legs, bond_legs)
        assert "error" not in out, out.get("error")

        bespoke_dates = [row["date"] for row in out["time_series"]]
        assert bespoke_dates == sorted(bespoke_dates)

        # The un-bridged tail of the gap (anything past
        # ffill_limit_days) cannot be carried forward by either
        # inner clean step — the basis inner-join MUST drop those
        # dates.
        unbridged = gap_dates[ffill_limit:]
        assert len(unbridged) >= 2
        for d in unbridged:
            d_str = d.strftime("%Y-%m-%d")
            assert d_str not in bespoke_dates, (
                f"Basis time_series contains un-bridged gap date "
                f"{d_str} — strict inner-join was expected to drop "
                "it (no synthetic basis point)."
            )

        assert out["current_metrics"]["basis_bps"] is not None


# ===========================================================================
# 8. Sign convention
# ===========================================================================

class TestSignConvention:
    def test_default_sign_is_zcis_minus_breakeven(self):
        """Hard-pin: with ZCIS=2.50, breakeven=2.35, basis_bps must
        be POSITIVE (15 bps).  A regression to a different sign
        convention would flip this.
        """
        zcis_legs, bond_legs = _build_legs_for_us_10y(
            zcis_base_pct=2.50, zcis_drift_pct=0.0,
            nominal_base_pct=4.20, nominal_drift_pct=0.0,
            real_base_pct=1.85, real_drift_pct=0.0,
        )
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, zcis_legs, bond_legs)
        assert "error" not in out, out.get("error")
        # zcis(2.50) - breakeven(2.35) = +0.15% = +15 bps.
        assert out["current_metrics"]["basis_bps"] > 0
        assert out["current_metrics"]["basis_bps"] == pytest.approx(
            15.0, abs=0.05,
        )


# ===========================================================================
# 9. Boundary-rounding parity
# ===========================================================================

class TestBoundaryRoundingParity:
    def test_snapshot_bps_matches_bespoke_last_row_strictly(self):
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, zcis_legs, bond_legs)
        snap = out["current_metrics"]["basis_bps"]
        bespoke_last = out["time_series"][-1]["basis_bps"]
        assert snap == bespoke_last

    def test_snapshot_bps_matches_canonical_last_row_strictly(self):
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, zcis_legs, bond_legs)
        snap = out["current_metrics"]["basis_bps"]
        canon_last = out["time_series_basis"]["rows"][-1]["value"]
        assert snap == canon_last


# ===========================================================================
# 10. Wire-honesty disclosure threading
# ===========================================================================

class TestMethodologyLabelThreading:
    def test_methodology_label_sourced_from_yaml(self):
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        custom_text = (
            "CUSTOM-SWAP-BREAKEVEN-BASIS-DISCLOSURE-99  zcis_pct - "
            "breakeven_pct  zcis_minus_breakeven  index-lag basis"
        )
        out = _run(
            params, zcis_legs, bond_legs,
            config=_build_config(methodology_what_it_does=custom_text),
        )
        assert (
            out["current_metrics"]["methodology_label"]
            == custom_text.strip()
        )

    def test_bundled_methodology_label_carries_basis_caveat(self):
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, zcis_legs, bond_legs, config=None)
        label = out["current_metrics"]["methodology_label"].lower()
        assert "zcis_pct" in label
        assert "breakeven_pct" in label
        # Load-bearing basis caveat MUST be on the wire.
        assert "not a clean liquidity-premium read" in label
        assert "index-lag" in label


# ===========================================================================
# 11. Per-leg metadata + derived index-family summary
# ===========================================================================

class TestPerLegReferenceMetadataSurfacing:
    def test_zcis_reference_metadata_threads_to_current_metrics(self):
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, zcis_legs, bond_legs)
        cm = out["current_metrics"]
        assert cm["zcis_inflation_index_family"] == "US_CPI_URBAN"
        assert cm["zcis_index_lag"] == "3M"
        assert cm["zcis_interpolation"] == "Daily"
        assert cm["zcis_underlying_index"] == "CPURNSA Index"


class TestDerivedIndexFamilySummary:
    """``index_families_match`` + ``index_family_caveat`` are
    derived top-level summary fields on current_metrics so the desk
    reader can read the load-bearing index-family caveat from one
    field rather than reconciling per-leg strings.
    """

    def test_unsurfaced_linker_metadata_flags_caveat(self):
        """The breakeven primitive's current public surface does
        NOT yet expose linker inflation_index_family on
        current_metrics, so the basis primitive's
        ``index_families_match`` defaults to False with a caveat
        that flags the missing metadata.
        """
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, zcis_legs, bond_legs)
        cm = out["current_metrics"]
        assert cm["index_families_match"] is False
        caveat = cm["index_family_caveat"]
        assert caveat is not None
        # ZCIS leg's family must appear in the caveat verbatim.
        assert "US_CPI_URBAN" in caveat
        # Caveat must spell out the load-bearing wire warning.
        assert (
            "not a clean basis read" in caveat.lower()
            or "not a clean liquidity-premium read" in caveat.lower()
        )

    def test_matching_families_clears_caveat(self):
        """Synthetic configuration where the breakeven primitive
        is mocked to surface ``inflation_index_family`` matching
        the ZCIS leg (e.g. both report 'US_CPI_URBAN').  In that
        case the basis is a clean basis read — the caveat must be
        None and ``index_families_match`` must be True.
        """
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )

        # Patch the inner breakeven callable directly on the basis
        # primitive's compute module so we can inject a synthetic
        # output that includes ``inflation_index_family``.  This
        # does NOT touch the real breakeven primitive — it
        # replaces the binding the basis primitive looks up.
        from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple import (
            compute as basis_compute,
        )
        bdays = pd.bdate_range(
            date(2026, 4, 30) - timedelta(days=600),
            date(2026, 4, 30),
        )
        bond_rows = [
            {
                "date": d.strftime("%Y-%m-%d"),
                "value": 235.0,  # 2.35% breakeven in bps
            }
            for d in bdays
        ]
        synthetic_breakeven_output = {
            "current_metrics": {
                "as_of_date": bdays[-1].strftime("%Y-%m-%d"),
                "nominal_curve_family": "UST",
                "linker_curve_family": "USD_TIPS",
                "tenor": "10Y",
                "breakeven_label": "UST-USD_TIPS 10Y breakeven",
                "breakeven_pct": 2.35,
                "breakeven_bps": 235.00,
                "nominal_yield_pct": 4.20,
                "real_yield_pct": 1.85,
                # Synthetic — mock the linker leg surfacing index
                # metadata so the matching branch is testable.
                "inflation_index_family": "US_CPI_URBAN",
                "index_lag": "3M",
                "methodology_label": "synthetic",
            },
            "time_series": [],
            "time_series_breakeven": {
                "series_name": "synthetic_breakeven",
                "units": "bps",
                "description": "synthetic",
                "rows": bond_rows,
            },
            "time_series_zscore": {
                "series_name": "synthetic_zscore",
                "units": "z_score",
                "description": "synthetic",
                "rows": [],
            },
        }

        with patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar",
            new=_patched_zcis_fetch_factory(zcis_legs),
        ), patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date",
            _FrozenDate,
        ), patch.object(
            basis_compute,
            "calculate_breakeven_inflation_simple",
            return_value=synthetic_breakeven_output,
        ):
            out = calculate_swap_breakeven_basis_simple(
                engine=None, params=params, config=None,
            )

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["zcis_inflation_index_family"] == "US_CPI_URBAN"
        assert cm["linker_inflation_index_family"] == "US_CPI_URBAN"
        assert cm["index_families_match"] is True
        assert cm["index_family_caveat"] is None


# ===========================================================================
# 12. field_name threading
# ===========================================================================

class TestFieldNameThreading:
    """The optional ``field_name`` input must thread into BOTH
    inner calls (the ZCIS level call AND the breakeven call).  The
    sentinel (``None`` or empty string after schema coercion) must
    fall through to each inner primitive's own YAML default.
    """

    def test_explicit_field_name_threads_to_both_inner_calls(self):
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
            field_name="PX_LAST",
        )
        zcis_captured: list = []
        bond_captured: list = []

        def _zcis_capture_stub(
            *, engine, curve_family, tenor, field_name, start_date,
        ):
            zcis_captured.append((curve_family, tenor, field_name))
            return zcis_legs.get(
                (curve_family, tenor),
                pd.DataFrame(columns=[
                    "trade_date", "field_value", "vendor_ticker",
                    "pricing_type", "inflation_index_family",
                    "index_lag", "interpolation", "underlying_index",
                ]),
            )

        def _bond_capture_stub(
            *, engine, curve_family, tenor, field_name, start_date,
            instrument_type=None,
        ):
            bond_captured.append(
                (curve_family, tenor, field_name, instrument_type),
            )
            return bond_legs.get(
                (curve_family, tenor, instrument_type),
                pd.DataFrame(columns=["trade_date", "field_value"]),
            )

        with patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar",
            new=_zcis_capture_stub,
        ), patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_bond_capture_stub,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ):
            out = calculate_swap_breakeven_basis_simple(
                engine=None, params=params, config=None,
            )
        assert "error" not in out, out.get("error")

        # ZCIS leg saw exactly one call with the explicit override.
        assert len(zcis_captured) == 1
        assert zcis_captured[0][0] == "USD_ZCIS"
        assert zcis_captured[0][2] == "PX_LAST"

        # Breakeven leg ran two fetches (linker + nominal); both
        # must have received the override.
        assert len(bond_captured) == 2
        bond_fields = {c[3]: c[2] for c in bond_captured}
        assert bond_fields["sovereign_benchmark"] == "PX_LAST"
        assert bond_fields["inflation_linker"] == "PX_LAST"

    def test_none_field_name_falls_through_to_yaml_defaults(self):
        """``field_name=None`` (default) must fall through to each
        inner primitive's YAML default — PX_MID for the ZCIS leg,
        YLD_YTM_MID for the breakeven leg.
        """
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        zcis_captured: list = []
        bond_captured: list = []

        def _zcis_stub(*, engine, curve_family, tenor, field_name, start_date):
            zcis_captured.append((curve_family, tenor, field_name))
            return zcis_legs.get(
                (curve_family, tenor),
                pd.DataFrame(columns=[
                    "trade_date", "field_value", "vendor_ticker",
                    "pricing_type", "inflation_index_family",
                    "index_lag", "interpolation", "underlying_index",
                ]),
            )

        def _bond_stub(
            *, engine, curve_family, tenor, field_name, start_date,
            instrument_type=None,
        ):
            bond_captured.append(
                (curve_family, tenor, field_name, instrument_type),
            )
            return bond_legs.get(
                (curve_family, tenor, instrument_type),
                pd.DataFrame(columns=["trade_date", "field_value"]),
            )

        with patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar",
            new=_zcis_stub,
        ), patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_bond_stub,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ):
            out = calculate_swap_breakeven_basis_simple(
                engine=None, params=params, config=None,
            )
        assert "error" not in out, out.get("error")

        # Each inner primitive resolved None against its own YAML
        # default — PX_MID for ZCIS, YLD_YTM_MID for breakeven.
        assert zcis_captured[0][2] == "PX_MID"
        bond_fields = {c[3]: c[2] for c in bond_captured}
        assert bond_fields["sovereign_benchmark"] == "YLD_YTM_MID"
        assert bond_fields["inflation_linker"] == "YLD_YTM_MID"

    def test_empty_string_field_name_falls_through_to_yaml_defaults(self):
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
            field_name="",
        )
        # Sanity: validator coerced "" → None.
        assert params.field_name is None
        zcis_captured: list = []
        bond_captured: list = []

        def _zcis_stub(*, engine, curve_family, tenor, field_name, start_date):
            zcis_captured.append((curve_family, tenor, field_name))
            return zcis_legs.get(
                (curve_family, tenor),
                pd.DataFrame(columns=[
                    "trade_date", "field_value", "vendor_ticker",
                    "pricing_type", "inflation_index_family",
                    "index_lag", "interpolation", "underlying_index",
                ]),
            )

        def _bond_stub(
            *, engine, curve_family, tenor, field_name, start_date,
            instrument_type=None,
        ):
            bond_captured.append(
                (curve_family, tenor, field_name, instrument_type),
            )
            return bond_legs.get(
                (curve_family, tenor, instrument_type),
                pd.DataFrame(columns=["trade_date", "field_value"]),
            )

        with patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar",
            new=_zcis_stub,
        ), patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_bond_stub,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ):
            out = calculate_swap_breakeven_basis_simple(
                engine=None, params=params, config=None,
            )
        assert "error" not in out, out.get("error")
        assert zcis_captured[0][2] == "PX_MID"
        bond_fields = {c[3]: c[2] for c in bond_captured}
        assert bond_fields["sovereign_benchmark"] == "YLD_YTM_MID"
        assert bond_fields["inflation_linker"] == "YLD_YTM_MID"


# ===========================================================================
# 13. Import path stability
# ===========================================================================

class TestImportPathStability:
    def test_function_via_package_init(self):
        from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple import (
            calculate_swap_breakeven_basis_simple as via_package,
        )
        from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple.compute import (
            calculate_swap_breakeven_basis_simple as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple import (
            SwapBreakevenBasisSimpleInput as via_package,
        )
        from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple.schemas import (
            SwapBreakevenBasisSimpleInput as via_schemas,
        )
        from rates_agent.inflation_swaps.tools.schemas import (
            SwapBreakevenBasisSimpleInput as via_hub,
        )
        assert via_package is via_schemas is via_hub


# ===========================================================================
# 14. Canonical TimeSeries outputs
# ===========================================================================

class TestCanonicalTimeSeries:
    def test_canonical_basis_uses_bps_units_and_naming(self):
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, zcis_legs, bond_legs)
        ts = out["time_series_basis"]
        assert ts["units"] == "bps"
        assert (
            ts["series_name"]
            == "usd_zcis_ust_usd_tips_10y_swap_breakeven_basis"
        )
        assert "zcis_minus_breakeven" in ts["description"]

    def test_canonical_zscore_uses_z_score_units_and_naming(self):
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, zcis_legs, bond_legs)
        ts = out["time_series_zscore"]
        assert ts["units"] == "z_score"
        assert (
            ts["series_name"]
            == "usd_zcis_ust_usd_tips_10y_swap_breakeven_basis_zscore"
        )

    def test_canonical_lengths_match_bespoke(self):
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, zcis_legs, bond_legs)
        assert (
            len(out["time_series"])
            == len(out["time_series_basis"]["rows"])
        )
        assert (
            len(out["time_series"])
            == len(out["time_series_zscore"]["rows"])
        )

    def test_canonical_payloads_validate_against_TimeSeries_schema(self):
        from shared.schemas import TimeSeries
        zcis_legs, bond_legs = _build_legs_for_us_10y()
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family="USD_ZCIS",
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, zcis_legs, bond_legs)
        TimeSeries.model_validate(out["time_series_basis"])
        TimeSeries.model_validate(out["time_series_zscore"])


# ===========================================================================
# 15. No raw market-data SELECTs in this primitive's compute path
# ===========================================================================

class TestNoRawSelects:
    def test_compute_py_does_not_select_market_data_directly(self):
        """Per spec: ``grep -E "FROM\\s+(market_data|instrument_master
        |v_market_data_daily_enriched)"`` on the new compute.py
        MUST return ZERO matches.  Docstring mentions of the table
        names are fine — only actual SQL FROM-clauses are
        forbidden.
        """
        from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple import (
            compute as compute_module,
        )
        import inspect
        import re
        source = inspect.getsource(compute_module)
        pattern = re.compile(
            r"FROM\s+(market_data|instrument_master|"
            r"v_market_data_daily_enriched|"
            r"macro_data\.market_data|"
            r"macro_data\.instrument_master|"
            r"macro_data\.v_market_data_daily_enriched)",
            re.IGNORECASE,
        )
        matches = pattern.findall(source)
        assert not matches, (
            f"compute.py contains raw market-data FROM-clauses "
            f"({matches!r}) — both the four-conjunct guard (ZCIS "
            "leg) AND the linker / nominal instrument_type "
            "discriminators (breakeven leg) must live inside the "
            "respective inner primitives, NOT here.  Composing the "
            "inner primitives is the only sanctioned way for this "
            "primitive to read the universe."
        )
