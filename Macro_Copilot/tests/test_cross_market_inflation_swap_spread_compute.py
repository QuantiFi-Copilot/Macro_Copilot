"""
test_cross_market_inflation_swap_spread_compute.py — Unit tests for
the same-tenor cross-market ZCIS spread primitive.

Mirrors ``test_inflation_swap_curve_spread_compute.py`` (the closest
sibling) — both primitives compose the ZCIS rate-level primitive
twice and inherit the inner four-conjunct SELECT guard
transitively.  This module differs in that it composes across two
DISTINCT curve families (cross-market) rather than two pillars on
the same curve (curve spread).

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. Convention defaults align with sibling rates tools.
  3. Convention overrides actually change behaviour (z-window,
     ddof, period offsets, bps rounding).
  4. Honest-placeholder guards: trailing_range_window_days != 252
     raises NotImplementedError; cross_market_sign_convention !=
     'leg_a_minus_leg_b' raises NotImplementedError.
  5. Schema-layer behaviour: cross-curve invariant
     (leg_a_curve_family != leg_b_curve_family); tenor parses via
     shared.analytics.curve_bootstrap.tenor_to_years.
  6. Composition guard inheritance: missing endpoint legs surface
     a controlled error envelope from the inner level call,
     propagated with leg_a / leg_b attribution.
  7. Per-trade-date alignment: dates where one leg has no data
     after the level primitive's ffill window are dropped from
     the spread series.
  8. Spread formula correctness on synthetic input.
  9. Sign convention: USD - EUR vs EUR - USD inversion check.
 10. Boundary-rounding parity:
     ``current_metrics.spread_bps`` equals
     ``time_series[-1].spread_bps`` AND
     ``time_series_spread.rows[-1].value`` bit-for-bit at the
     YAML's ``bps_round_decimals``.
 11. Wire-honesty: ``methodology_label`` is sourced from the YAML.
 12. Per-leg reference metadata threads to current_metrics +
     canonical description.
 13. Three import paths still resolve to the same Pydantic class.
 14. Canonical TimeSeries outputs: BPS for the spread, Z_SCORE for
     the rolling z-score, with the documented series_name pattern.

Tests are fully offline (DB-backed grounding lives in the SQL-
validation runner — see
``tests/test_cross_market_inflation_swap_spread_sql_validation.py``).
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread import (
    CONFIG_PATH,
    CrossMarketInflationSwapSpreadInput,
    calculate_cross_market_inflation_swap_spread,
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


def _build_legs_for_usd_vs_eur_5y(
    *,
    leg_a_base_pct: float = 2.50,
    leg_b_base_pct: float = 2.20,
    leg_a_drift_pct: float = -0.20,
    leg_b_drift_pct: float = -0.05,
    days: int = 800,
) -> dict:
    """Default canonical two-leg fixture for USD_ZCIS 5Y minus
    EUR_ZCIS 5Y.

    The two legs drift by different amounts so the resulting
    cross-market spread is NOT constant — necessary for the
    z-score / period-change override tests to actually surface a
    convention-driven difference.
    """
    return {
        ("USD_ZCIS", "5Y"): _synthetic_pillar_df(
            days=days, base_pct=leg_a_base_pct,
            drift_pct=leg_a_drift_pct,
            vendor_ticker="USSWIT5 Curncy",
            inflation_index_family="US_CPI_URBAN",
            index_lag="3M",
            interpolation="Daily",
            underlying_index="CPURNSA Index",
        ),
        ("EUR_ZCIS", "5Y"): _synthetic_pillar_df(
            days=days, base_pct=leg_b_base_pct,
            drift_pct=leg_b_drift_pct,
            vendor_ticker="EUSWI5 Curncy",
            inflation_index_family="EU_HICP",
            index_lag="3M",
            interpolation="Monthly",
            underlying_index="CPTFEMU Index",
        ),
    }


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
    "cross_market_sign_convention": "leg_a_minus_leg_b",
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
        return calculate_cross_market_inflation_swap_spread(
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
        assert cfg.tool.name == "cross_market_inflation_swap_spread"
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
            "cross_market_sign_convention",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_canonical_lint_aligned_conventions_also_present(self):
        """The cross-config lint enforces value-agreement on the
        canonical underscored convention names; those must also be
        declared so the lint can do its job.
        """
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
            "default_zcis_rate_field",
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
            cfg.convention_value("cross_market_sign_convention")
            == "leg_a_minus_leg_b"
        )

    def test_methodology_what_it_does_carries_index_family_caveat(self):
        """The load-bearing INDEX-FAMILY CAVEAT MUST be on the wire-
        disclosure exactly per the catalog's methodology_guardrails.
        """
        cfg = load_tool_config(CONFIG_PATH)
        text = cfg.methodology.what_it_does.lower()
        assert "zcis" in text
        # Spread formula MUST be visible on the wire-disclosure.
        assert "leg_a_pct" in text and "leg_b_pct" in text
        # Index-family caveat — load-bearing per the catalog.
        assert "us cpi" in text or "us_cpi" in text or "cpi-u" in text
        assert "hicp" in text
        assert "rpi" in text
        # The "NOT a clean expected-inflation divergence" disclaimer
        # MUST be present on the wire.
        assert "not" in text and (
            "clean expected-inflation" in text
            or "expected-inflation divergence" in text
        )

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        # Per Finding 3: planned_extensions must include the original
        # entries (swap-breakeven basis, trailing-window
        # configurability, date-window mode, forward-window
        # cross-market) PLUS the cross-tenor cross-market and
        # alternative sign-convention entries.
        assert len(cfg.methodology.planned_extensions) >= 5
        joined = " | ".join(cfg.methodology.planned_extensions).lower()
        assert (
            "swap_breakeven_basis" in joined
            or "swap-breakeven basis" in joined
        )
        assert "trailing_range_window_days" in joined
        # Cross-tenor cross-market deferral.
        assert "cross-tenor" in joined
        # Alternative sign-convention deferral.
        assert "cross_market_sign_convention" in joined
        assert "dv01" in joined or "duration" in joined


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        legs = _build_legs_for_usd_vs_eur_5y()
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out = _run(params, legs)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        for k in (
            "as_of_date",
            "leg_a_curve_family",
            "leg_b_curve_family",
            "tenor",
            "tenor_years",
            "spread_label",
            "spread_pct",
            "spread_bps",
            "change_1d_bps",
            "change_1w_bps",
            "change_1m_bps",
            "z_score_252d",
            "high_252d_bps",
            "low_252d_bps",
            "percentile_252d",
            "leg_a_pct",
            "leg_b_pct",
            "observation_count",
            "leg_a_inflation_index_family",
            "leg_b_inflation_index_family",
            "index_families_match",
            "index_family_caveat",
            "leg_a_index_lag",
            "leg_b_index_lag",
            "leg_a_interpolation",
            "leg_b_interpolation",
            "leg_a_underlying_index",
            "leg_b_underlying_index",
            "methodology_label",
        ):
            assert k in cm, f"missing {k}"

        assert cm["tenor_years"] == 5.0
        assert cm["leg_a_curve_family"] == "USD_ZCIS"
        assert cm["leg_b_curve_family"] == "EUR_ZCIS"
        assert cm["tenor"] == "5Y"
        assert cm["spread_label"] == "USD_ZCIS-EUR_ZCIS 5Y"

    def test_spread_formula_is_correct(self):
        """Hard-pin the spread formula on a synthetic fixture with
        constant per-leg ZCIS rates.  With leg_a=2.50%, leg_b=2.20%,
        expected spread_pct = 0.30, spread_bps = 30bps.
        """
        legs = _build_legs_for_usd_vs_eur_5y(
            leg_a_base_pct=2.50, leg_b_base_pct=2.20,
            leg_a_drift_pct=0.0, leg_b_drift_pct=0.0,
        )
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["leg_a_pct"] == pytest.approx(2.50, abs=1e-6)
        assert cm["leg_b_pct"] == pytest.approx(2.20, abs=1e-6)
        assert cm["spread_pct"] == pytest.approx(0.30, abs=1e-3)
        assert cm["spread_bps"] == pytest.approx(30.0, abs=0.05)

    def test_explicit_config_matches_auto_loaded(self):
        legs = _build_legs_for_usd_vs_eur_5y()
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out_auto = _run(params, legs, config=None)
        out_explicit = _run(
            params, legs, config=load_tool_config(CONFIG_PATH),
        )
        assert out_auto == out_explicit

    def test_explicit_config_matches_auto_loaded_with_explicit_field_name(self):
        """Parity between the auto-load fallback and an explicit
        ToolConfig must hold whether ``field_name`` is the YAML
        sentinel (None) or an explicit override.  Pins that the
        field_name sentinel resolution does not introduce a
        divergence between the two config-loading paths.
        """
        legs = _build_legs_for_usd_vs_eur_5y()
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
            field_name="PX_LAST",
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
        legs = _build_legs_for_usd_vs_eur_5y()
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
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
        legs = _build_legs_for_usd_vs_eur_5y()
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out_sample = _run(params, legs, config=_build_config(z_score_ddof=1))
        out_pop = _run(params, legs, config=_build_config(z_score_ddof=0))
        assert (
            out_sample["current_metrics"]["z_score_252d"]
            != out_pop["current_metrics"]["z_score_252d"]
        )

    def test_period_offsets_override_changes_changes(self):
        legs = _build_legs_for_usd_vs_eur_5y()
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
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
        legs = _build_legs_for_usd_vs_eur_5y(
            leg_a_base_pct=2.345678, leg_b_base_pct=2.123456,
        )
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out_2 = _run(
            params, legs, config=_build_config(bps_round_decimals=2),
        )
        out_4 = _run(
            params, legs, config=_build_config(bps_round_decimals=4),
        )
        v2 = out_2["current_metrics"]["spread_bps"]
        v4 = out_4["current_metrics"]["spread_bps"]
        assert v2 == round(v2, 2)
        assert v4 == round(v4, 4)


# ===========================================================================
# 4. Honest-placeholder guards
# ===========================================================================

class TestPlaceholderGuards:
    def test_unsupported_trailing_window_raises(self):
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            calculate_cross_market_inflation_swap_spread(
                engine=None, params=params,
                config=_build_config(trailing_range_window_days=180),
            )
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_bps" in msg
        assert "planned_extensions" in msg

    def test_unsupported_sign_convention_raises(self):
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            calculate_cross_market_inflation_swap_spread(
                engine=None, params=params,
                config=_build_config(
                    cross_market_sign_convention="leg_b_minus_leg_a",
                ),
            )
        msg = str(exc_info.value)
        assert "leg_b_minus_leg_a" in msg
        assert "leg_a_minus_leg_b" in msg
        # Per Finding 3: the guard message MUST point callers at
        # methodology.planned_extensions so a ripgrep-style audit
        # can confirm the pointer.
        assert "methodology.planned_extensions" in msg


# ===========================================================================
# 5. Schema-layer behaviour
# ===========================================================================

class TestInputSchemaBehaviour:
    def test_curves_must_differ(self):
        with pytest.raises(Exception) as exc_info:
            CrossMarketInflationSwapSpreadInput(
                leg_a_curve_family="USD_ZCIS",
                leg_b_curve_family="USD_ZCIS",
                tenor="5Y",
            )
        msg = str(exc_info.value).lower()
        assert "must be different" in msg or "must differ" in msg
        # Re-route hint to the same-curve sibling primitive must be
        # present in the validation error.
        assert "inflation_swap_curve_spread" in str(exc_info.value)

    def test_tenor_must_parse(self):
        """Unknown / unparseable tenor MUST be rejected at the
        schema layer with a precise error pointing at
        tenor_to_years.
        """
        with pytest.raises(Exception) as exc_info:
            CrossMarketInflationSwapSpreadInput(
                leg_a_curve_family="USD_ZCIS",
                leg_b_curve_family="EUR_ZCIS",
                tenor="ABC",
            )
        msg = str(exc_info.value)
        assert "tenor" in msg.lower()

    def test_extra_fields_rejected(self):
        """``extra='forbid'`` ensures unknown kwargs surface as a
        ValidationError rather than being silently ignored — protects
        the wire from typo-pollution.  ``field_name`` IS a legitimate
        optional input on this primitive (mirrors the standard
        sentinel pattern); the surprise-field probe uses an
        unambiguously-unknown key instead.
        """
        with pytest.raises(Exception):
            CrossMarketInflationSwapSpreadInput(
                leg_a_curve_family="USD_ZCIS",
                leg_b_curve_family="EUR_ZCIS",
                tenor="5Y",
                # Surprise field — extra='forbid' should reject this.
                bogus_field="oops",
            )

    def test_leg_a_curve_family_required(self):
        with pytest.raises(Exception):
            CrossMarketInflationSwapSpreadInput(
                leg_b_curve_family="EUR_ZCIS",
                tenor="5Y",
            )

    def test_leg_b_curve_family_required(self):
        with pytest.raises(Exception):
            CrossMarketInflationSwapSpreadInput(
                leg_a_curve_family="USD_ZCIS",
                tenor="5Y",
            )

    def test_tenor_required(self):
        with pytest.raises(Exception):
            CrossMarketInflationSwapSpreadInput(
                leg_a_curve_family="USD_ZCIS",
                leg_b_curve_family="EUR_ZCIS",
            )

    def test_field_name_default_is_none(self):
        """The standard sibling sentinel: ``field_name`` defaults to
        ``None`` so the YAML's ``default_zcis_rate_field`` resolves
        through compute.  Mirrors every other inflation_swaps tool.
        """
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
        )
        assert hasattr(params, "field_name")
        assert params.field_name is None

    def test_field_name_empty_string_coerced_to_none(self):
        """The schema's ``_coerce_empty_field_name`` validator
        coerces empty / whitespace-only strings to None so MCP/HTTP
        wrappers can forward their wire-level sentinel directly into
        the schema without silently shadowing the YAML default.
        """
        for sentinel in ("", "   ", "\t"):
            params = CrossMarketInflationSwapSpreadInput(
                leg_a_curve_family="USD_ZCIS",
                leg_b_curve_family="EUR_ZCIS",
                tenor="5Y",
                field_name=sentinel,
            )
            assert params.field_name is None, (
                f"sentinel {sentinel!r} should be coerced to None"
            )

    def test_field_name_explicit_value_kept(self):
        """A non-empty explicit ``field_name`` survives the schema
        unchanged and is threaded through compute into both inner
        level calls.
        """
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            field_name="PX_LAST",
        )
        assert params.field_name == "PX_LAST"


class TestTenorParserReuse:
    def test_tenor_parser_drives_year_fraction(self):
        """The ``tenor_years`` field on current_metrics must come
        from ``shared.analytics.curve_bootstrap.tenor_to_years``,
        not from a bespoke parser.
        """
        from shared.analytics.curve_bootstrap import tenor_to_years
        legs = _build_legs_for_usd_vs_eur_5y(days=800)
        # Add a 10Y leg pair too so we can probe a different tenor.
        legs[("USD_ZCIS", "10Y")] = _synthetic_pillar_df(
            days=800, base_pct=2.7, drift_pct=-0.05,
            vendor_ticker="USSWIT10 Curncy",
        )
        legs[("EUR_ZCIS", "10Y")] = _synthetic_pillar_df(
            days=800, base_pct=2.4, drift_pct=-0.02,
            vendor_ticker="EUSWI10 Curncy",
            inflation_index_family="EU_HICP",
            interpolation="Monthly",
        )
        for tenor in ("5Y", "10Y"):
            params = CrossMarketInflationSwapSpreadInput(
                leg_a_curve_family="USD_ZCIS",
                leg_b_curve_family="EUR_ZCIS",
                tenor=tenor,
                lookback_days=365,
            )
            out = _run(params, legs)
            assert "error" not in out, out.get("error")
            assert (
                out["current_metrics"]["tenor_years"]
                == pytest.approx(tenor_to_years(tenor), abs=1e-4)
            )


# ===========================================================================
# 6. Composition guard inheritance
# ===========================================================================

class TestCompositionGuards:
    """Cross-market spread composes the level primitive twice; the
    level's no-proxy guard (instrument_type + pricing_type +
    curve_family + tenor SELECT filter) must fire on each leg and
    surface as a controlled error envelope when violated.
    """

    def test_missing_leg_a_returns_error_envelope(self):
        legs = _build_legs_for_usd_vs_eur_5y()
        del legs[("USD_ZCIS", "5Y")]
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "leg_a" in out["error"].lower()
        assert "USD_ZCIS" in out["error"]
        # Inner error context (instrument_type / pricing_type
        # rationale from the four-conjunct guard) is propagated.
        assert "inflation_swap" in out["error"]
        assert "zero_coupon_breakeven" in out["error"]

    def test_missing_leg_b_returns_error_envelope(self):
        legs = _build_legs_for_usd_vs_eur_5y()
        del legs[("EUR_ZCIS", "5Y")]
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "leg_b" in out["error"].lower()
        assert "EUR_ZCIS" in out["error"]
        assert "inflation_swap" in out["error"]

    def test_unknown_curve_family_returns_error_envelope(self):
        """A non-ZCIS curve_family on one leg yields zero rows
        through the four-conjunct SELECT guard; the inner level
        primitive surfaces the controlled error and we propagate
        it with leg_a / leg_b attribution.
        """
        legs: dict = {}
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="UST",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "inflation_swap" in out["error"]
        assert "zero_coupon_breakeven" in out["error"]


# ===========================================================================
# 7. Per-trade-date alignment
# ===========================================================================

class TestPerTradeDateAlignment:
    """Spread-layer inner-join discipline.

    Dates that are still missing on a leg AFTER the level
    primitive's ffill window are dropped from the displayed spread
    series rather than synthesised; brief gaps inside the level's
    ``ffill_limit_days`` window are bridged by the inner level
    clean step.  The cross-market spread layer never adds its own
    ffill across the join.
    """

    def test_missing_endpoint_date_is_dropped(self):
        """Drop a CONSECUTIVE 7-trading-day block from leg_b
        (strictly wider than ``ffill_limit_days`` = 5).  Even if the
        inner level clean step bridges the first 5 days of the gap,
        the remaining 2+ days CANNOT be carried forward, so the
        spread layer's strict inner-join MUST drop them from
        ``time_series`` rather than synthesise a value on those
        dates.
        """
        legs = _build_legs_for_usd_vs_eur_5y()

        ffill_limit = _BUNDLED_DEFAULTS["ffill_limit_days"]
        gap_size = 7
        # Sanity: gap is strictly wider than the level's ffill
        # window so the un-bridged tail is non-empty.
        assert gap_size > ffill_limit

        # Place the gap inside the displayed window but well before
        # the latest aligned trade_date so the snapshot row falls
        # outside the gap.
        all_dates = sorted(
            legs[("EUR_ZCIS", "5Y")]["trade_date"].tolist(),
        )
        gap_start_idx = len(all_dates) - 130
        gap_dates = all_dates[gap_start_idx : gap_start_idx + gap_size]
        assert len(gap_dates) == gap_size

        df = legs[("EUR_ZCIS", "5Y")]
        legs[("EUR_ZCIS", "5Y")] = (
            df[~df["trade_date"].isin(gap_dates)].reset_index(drop=True)
        )

        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")

        bespoke_dates = [row["date"] for row in out["time_series"]]

        # Chronological order is preserved across the dropped block.
        assert bespoke_dates == sorted(bespoke_dates)

        # The un-bridged tail of the gap (anything past
        # ffill_limit_days) cannot be carried forward by the inner
        # level — the spread inner-join MUST drop those dates.
        unbridged = gap_dates[ffill_limit:]
        assert len(unbridged) >= 2
        for d in unbridged:
            d_str = d.strftime("%Y-%m-%d")
            assert d_str not in bespoke_dates, (
                f"Spread time_series contains un-bridged gap date "
                f"{d_str} — strict inner-join was expected to drop "
                "it (no synthetic spread point)."
            )

        # Snapshot's latest displayed row falls outside the gap, so
        # spread_bps must still be populated.
        assert out["current_metrics"]["spread_bps"] is not None


# ===========================================================================
# 8. Sign-convention test
# ===========================================================================

class TestSignConvention:
    """The cross-market spread is directional; flipping leg_a /
    leg_b inverts the sign exactly.  This pins both:
      a. the YAML sign convention (leg_a - leg_b) flowing through
         compute correctly, AND
      b. the absence of any silent canonicalisation that would
         normalise to a specific direction.
    """

    def test_usd_minus_eur_inverts_to_eur_minus_usd(self):
        legs = _build_legs_for_usd_vs_eur_5y(
            leg_a_base_pct=2.50, leg_b_base_pct=2.20,
            leg_a_drift_pct=0.0, leg_b_drift_pct=0.0,
        )
        usd_eur = _run(
            CrossMarketInflationSwapSpreadInput(
                leg_a_curve_family="USD_ZCIS",
                leg_b_curve_family="EUR_ZCIS",
                tenor="5Y",
                lookback_days=365,
            ),
            legs,
        )
        eur_usd = _run(
            CrossMarketInflationSwapSpreadInput(
                leg_a_curve_family="EUR_ZCIS",
                leg_b_curve_family="USD_ZCIS",
                tenor="5Y",
                lookback_days=365,
            ),
            legs,
        )
        assert "error" not in usd_eur, usd_eur.get("error")
        assert "error" not in eur_usd, eur_usd.get("error")

        usd_eur_bps = usd_eur["current_metrics"]["spread_bps"]
        eur_usd_bps = eur_usd["current_metrics"]["spread_bps"]
        # Sign exactly inverts (within rounding).
        assert usd_eur_bps == pytest.approx(-eur_usd_bps, abs=0.05)
        # Sanity: positive on USD-EUR (USD ZCIS > EUR ZCIS in this
        # fixture).
        assert usd_eur_bps > 0


# ===========================================================================
# 9. Boundary-rounding parity
# ===========================================================================

class TestBoundaryRoundingParity:
    def test_snapshot_bps_matches_bespoke_last_row_strictly(self):
        legs = _build_legs_for_usd_vs_eur_5y()
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        snap = out["current_metrics"]["spread_bps"]
        bespoke_last = out["time_series"][-1]["spread_bps"]
        assert snap == bespoke_last

    def test_snapshot_bps_matches_canonical_last_row_strictly(self):
        legs = _build_legs_for_usd_vs_eur_5y()
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        snap = out["current_metrics"]["spread_bps"]
        canon_last = out["time_series_spread"]["rows"][-1]["value"]
        assert snap == canon_last

    def test_canonical_bps_equals_pct_times_100_per_row(self):
        """Spec requires:
        ``current_metrics.spread_bps == time_series[-1].spread_bps
            == time_series_spread.rows[-1].value
            == time_series[-1].spread_pct * 100``
        bit-for-bit at the YAML rounding decimals.
        """
        legs = _build_legs_for_usd_vs_eur_5y()
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        # Per-row consistency between bespoke spread_pct and
        # canonical spread (BPS).
        bespoke = out["time_series"]
        canon = out["time_series_spread"]["rows"]
        assert len(bespoke) == len(canon)
        for b, c in zip(bespoke, canon):
            assert b["date"] == c["date"]
            # The snapshot rounding is bps_round_decimals (2);
            # round(spread_pct * 100, 2) must match canonical value
            # at that precision.
            assert round(b["spread_pct"] * 100, 2) == round(c["value"], 2)


# ===========================================================================
# 10. Wire-honesty disclosure threading
# ===========================================================================

class TestMethodologyLabelThreading:
    def test_methodology_label_sourced_from_yaml(self):
        legs = _build_legs_for_usd_vs_eur_5y()
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        custom_text = (
            "CUSTOM-CROSS-MARKET-ZCIS-DISCLOSURE-99 same-tenor "
            "cross-market ZCIS spread leg_a_pct - leg_b_pct"
        )
        out = _run(
            params, legs,
            config=_build_config(methodology_what_it_does=custom_text),
        )
        assert (
            out["current_metrics"]["methodology_label"]
            == custom_text.strip()
        )

    def test_bundled_methodology_label_carries_index_family_caveat(self):
        legs = _build_legs_for_usd_vs_eur_5y()
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out = _run(params, legs, config=None)
        label = out["current_metrics"]["methodology_label"].lower()
        assert "zcis" in label
        # Spread formula MUST be visible on the wire.
        assert "leg_a_pct" in label
        assert "leg_b_pct" in label
        # INDEX-FAMILY CAVEAT MUST be visible on the wire.
        assert "us cpi" in label or "us_cpi" in label or "cpi-u" in label
        assert "hicp" in label
        assert "rpi" in label


# ===========================================================================
# 11. Per-leg reference-metadata surfacing
# ===========================================================================

class TestPerLegReferenceMetadataSurfacing:
    def test_per_leg_reference_metadata_threads_to_current_metrics(self):
        legs = _build_legs_for_usd_vs_eur_5y()
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        cm = out["current_metrics"]
        # Each leg's metadata is surfaced separately — different
        # values (USD: CPI-U / Daily; EUR: HICP / Monthly) is the
        # whole point of the index-family caveat.
        assert cm["leg_a_inflation_index_family"] == "US_CPI_URBAN"
        assert cm["leg_b_inflation_index_family"] == "EU_HICP"
        assert cm["leg_a_index_lag"] == "3M"
        assert cm["leg_b_index_lag"] == "3M"
        assert cm["leg_a_interpolation"] == "Daily"
        assert cm["leg_b_interpolation"] == "Monthly"
        assert cm["leg_a_underlying_index"] == "CPURNSA Index"
        assert cm["leg_b_underlying_index"] == "CPTFEMU Index"

    def test_per_leg_metadata_threads_to_canonical_description(self):
        legs = _build_legs_for_usd_vs_eur_5y()
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        desc = out["time_series_spread"]["description"]
        # Both legs' inflation_index_family must appear in the
        # description so the caveat is visible to consumers that
        # only read the canonical TimeSeries.
        assert "US_CPI_URBAN" in desc
        assert "EU_HICP" in desc


# ===========================================================================
# 11b. Derived index-family summary (top-level)
# ===========================================================================

class TestDerivedIndexFamilySummary:
    """``index_families_match`` + ``index_family_caveat`` are
    derived top-level summary fields on current_metrics so the desk
    reader can read the load-bearing index-family caveat from one
    field rather than reconciling two per-leg strings.  The
    catalog's methodology guardrail requires this summary to be
    explicit.
    """

    def test_mismatched_families_flagged_with_caveat(self):
        """USD_ZCIS (US_CPI_URBAN) vs EUR_ZCIS (EU_HICP) — the legs
        reference different inflation indices, so
        ``index_families_match`` must be False and
        ``index_family_caveat`` must name BOTH families verbatim.
        """
        legs = _build_legs_for_usd_vs_eur_5y()
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        cm = out["current_metrics"]
        assert cm["index_families_match"] is False
        caveat = cm["index_family_caveat"]
        assert caveat is not None
        assert "US_CPI_URBAN" in caveat
        assert "EU_HICP" in caveat
        # Caveat must spell out the load-bearing wire warning.
        assert "NOT a clean expected-inflation divergence" in caveat

    def test_matching_families_clears_caveat(self):
        """Synthetic configuration where BOTH legs report the same
        inflation_index_family (e.g. a hypothetical USD_ZCIS vs
        USDX_ZCIS pair both tagged US_CPI_URBAN).  In that case the
        spread is a clean expected-inflation differential, so the
        caveat must be None.  The cross-market invariant
        (leg_a_curve_family != leg_b_curve_family) still holds.
        """
        legs = {
            ("USD_ZCIS", "5Y"): _synthetic_pillar_df(
                days=800, base_pct=2.50, drift_pct=-0.20,
                vendor_ticker="USSWIT5 Curncy",
                inflation_index_family="US_CPI_URBAN",
                index_lag="3M",
                interpolation="Daily",
                underlying_index="CPURNSA Index",
            ),
            ("USDX_ZCIS", "5Y"): _synthetic_pillar_df(
                days=800, base_pct=2.40, drift_pct=-0.05,
                vendor_ticker="USSWITX5 Curncy",
                inflation_index_family="US_CPI_URBAN",
                index_lag="3M",
                interpolation="Daily",
                underlying_index="CPURNSA Index",
            ),
        }
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="USDX_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        cm = out["current_metrics"]
        assert cm["index_families_match"] is True
        assert cm["index_family_caveat"] is None


# ===========================================================================
# 11c. field_name threading
# ===========================================================================

class TestFieldNameThreading:
    """The optional ``field_name`` input must thread into BOTH inner
    ``calculate_inflation_swap_rate_level`` calls so the two legs
    are read off the same Bloomberg field by construction.  The
    sentinel (``None`` or empty string after schema coercion) must
    fall through to the YAML's ``default_zcis_rate_field``.
    """

    def test_explicit_field_name_threads_to_both_inner_calls(self):
        """Explicit ``field_name='PX_LAST'`` must hit both inner
        level calls' ``fetch_zcis_single_pillar`` invocations.
        """
        legs = _build_legs_for_usd_vs_eur_5y()
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
            field_name="PX_LAST",
        )
        captured: list = []

        def _capture_stub(*, engine, curve_family, tenor, field_name, start_date):
            captured.append((curve_family, tenor, field_name))
            stub_legs = _build_legs_for_usd_vs_eur_5y()
            return stub_legs.get(
                (curve_family, tenor),
                pd.DataFrame(columns=[
                    "trade_date", "field_value", "vendor_ticker",
                    "pricing_type", "inflation_index_family",
                    "index_lag", "interpolation", "underlying_index",
                ]),
            )

        with patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar",
            new=_capture_stub,
        ), patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date",
            _FrozenDate,
        ):
            out = calculate_cross_market_inflation_swap_spread(
                engine=None, params=params, config=None,
            )
        assert "error" not in out, out.get("error")
        assert len(captured) == 2
        forwarded = {c[0]: c[2] for c in captured}
        assert forwarded["USD_ZCIS"] == "PX_LAST"
        assert forwarded["EUR_ZCIS"] == "PX_LAST"

    def test_none_field_name_falls_through_to_yaml_default(self):
        """``field_name=None`` (default) must fall through to the
        YAML's ``default_zcis_rate_field`` (PX_MID) on both inner
        calls.
        """
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        captured: list = []

        def _capture_stub(*, engine, curve_family, tenor, field_name, start_date):
            captured.append((curve_family, tenor, field_name))
            stub_legs = _build_legs_for_usd_vs_eur_5y()
            return stub_legs.get(
                (curve_family, tenor),
                pd.DataFrame(columns=[
                    "trade_date", "field_value", "vendor_ticker",
                    "pricing_type", "inflation_index_family",
                    "index_lag", "interpolation", "underlying_index",
                ]),
            )

        with patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar",
            new=_capture_stub,
        ), patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date",
            _FrozenDate,
        ):
            out = calculate_cross_market_inflation_swap_spread(
                engine=None, params=params, config=None,
            )
        assert "error" not in out, out.get("error")
        forwarded = {c[0]: c[2] for c in captured}
        assert forwarded["USD_ZCIS"] == "PX_MID"
        assert forwarded["EUR_ZCIS"] == "PX_MID"

    def test_empty_string_field_name_falls_through_to_yaml_default(self):
        """The schema's ``_coerce_empty_field_name`` validator
        coerces ``""`` to None, which then falls through to the
        YAML's ``default_zcis_rate_field`` on both inner calls.
        """
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
            field_name="",
        )
        # Sanity: the validator already coerced it.
        assert params.field_name is None
        captured: list = []

        def _capture_stub(*, engine, curve_family, tenor, field_name, start_date):
            captured.append((curve_family, tenor, field_name))
            stub_legs = _build_legs_for_usd_vs_eur_5y()
            return stub_legs.get(
                (curve_family, tenor),
                pd.DataFrame(columns=[
                    "trade_date", "field_value", "vendor_ticker",
                    "pricing_type", "inflation_index_family",
                    "index_lag", "interpolation", "underlying_index",
                ]),
            )

        with patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar",
            new=_capture_stub,
        ), patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date",
            _FrozenDate,
        ):
            out = calculate_cross_market_inflation_swap_spread(
                engine=None, params=params, config=None,
            )
        assert "error" not in out, out.get("error")
        forwarded = {c[0]: c[2] for c in captured}
        assert forwarded["USD_ZCIS"] == "PX_MID"
        assert forwarded["EUR_ZCIS"] == "PX_MID"


# ===========================================================================
# 12. Import path stability
# ===========================================================================

class TestImportPathStability:
    def test_function_via_package_init(self):
        from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread import (
            calculate_cross_market_inflation_swap_spread as via_package,
        )
        from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread.compute import (
            calculate_cross_market_inflation_swap_spread as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread import (
            CrossMarketInflationSwapSpreadInput as via_package,
        )
        from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread.schemas import (
            CrossMarketInflationSwapSpreadInput as via_schemas,
        )
        from rates_agent.inflation_swaps.tools.schemas import (
            CrossMarketInflationSwapSpreadInput as via_hub,
        )
        assert via_package is via_schemas is via_hub


# ===========================================================================
# 13. Canonical TimeSeries outputs
# ===========================================================================

class TestCanonicalTimeSeries:
    def test_canonical_spread_uses_bps_units_and_naming(self):
        legs = _build_legs_for_usd_vs_eur_5y()
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        ts = out["time_series_spread"]
        assert ts["units"] == "bps"
        assert (
            ts["series_name"]
            == "usd_zcis_eur_zcis_5y_zcis_cross_market_spread"
        )

    def test_canonical_zscore_uses_z_score_units_and_naming(self):
        legs = _build_legs_for_usd_vs_eur_5y()
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        ts = out["time_series_zscore"]
        assert ts["units"] == "z_score"
        assert (
            ts["series_name"]
            == "usd_zcis_eur_zcis_5y_zcis_cross_market_spread_zscore"
        )

    def test_canonical_lengths_match_bespoke(self):
        legs = _build_legs_for_usd_vs_eur_5y()
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
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
        legs = _build_legs_for_usd_vs_eur_5y()
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        TimeSeries.model_validate(out["time_series_spread"])
        TimeSeries.model_validate(out["time_series_zscore"])

    def test_canonical_dates_chronological(self):
        legs = _build_legs_for_usd_vs_eur_5y()
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family="USD_ZCIS",
            leg_b_curve_family="EUR_ZCIS",
            tenor="5Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        dates = [r["date"] for r in out["time_series_spread"]["rows"]]
        assert dates == sorted(dates)


# ===========================================================================
# 14. No raw market-data SELECTs in this primitive's compute path
# ===========================================================================

class TestNoRawSelects:
    """The four-conjunct SELECT guard lives inside the level
    primitive's compute().  This primitive composes the level call
    twice and inherits the guard transitively.  Any raw SELECT
    against ``v_market_data_daily_enriched`` or
    ``instrument_master`` in this primitive's compute.py would
    bypass the guard — verify by source-grep that none exists.
    """

    def test_compute_py_does_not_select_market_data_directly(self):
        """Per spec: ``grep -E "FROM\\s+(market_data|instrument_master
        |v_market_data_daily_enriched)"`` on the new compute.py MUST
        return ZERO matches.  Docstring mentions of the table names
        are fine — only actual SQL FROM-clauses are forbidden.
        """
        from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread import (
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
            f"({matches!r}) — the four-conjunct guard must live "
            "inside the level primitive's compute, NOT here.  "
            "Composing the level primitive is the only sanctioned "
            "way for this primitive to read the universe."
        )
