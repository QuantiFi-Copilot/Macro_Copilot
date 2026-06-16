"""
test_cross_country_breakeven_spread_simple_compute.py — Unit tests
for the linker cross_country_breakeven_spread_simple primitive.

Mirrors ``test_breakeven_curve_spread_compute.py`` — both primitives
compose ``breakeven_inflation_simple`` twice and inherit the spot
guards transitively.  This module differs from the curve-spread
sibling in two ways:

  1. The two composed legs come from DIFFERENT countries (cross-
     country object) rather than two tenors of the same country.
  2. The cross-country invariant is enforced HERE in this primitive's
     input schema validators (no DB lookup); the per-leg same-country
     invariant continues to live INSIDE breakeven_inflation_simple.

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. compute() runs end-to-end against synthetic input with the
     bundled config and returns a well-formed output.
  3. Explicit ``config=`` vs auto-load parity (byte-identical).
  4. Convention overrides change behaviour (z-window, ddof,
     period offsets, bps rounding, default_field_name).
  5. Honest-placeholder guards: trailing_range_window_days != 252
     raises NotImplementedError.
  6. Schema-layer behaviour: field_name defaults to None;
     compute() resolves the sentinel against the YAML.  Cross-
     country validators reject same-country pairs.
  7. Composition guard inheritance: leg-internal cross-country
     pollution (e.g. country_a_nominal=UST + country_a_linker=
     EUR_FR_LINKER) surfaces a controlled error envelope via the
     spot primitive's same-country guard firing transitively
     before any market-data SELECT.
  8. Per-trade-date alignment: dates where one country leg has no
     data are dropped from the spread series.
  9. Spread formula correctness AND sign convention on synthetic
     input (country_a minus country_b; flipping inputs flips the
     sign).
 10. Boundary-rounding parity: snapshot ``current_spread_bps``
     equals ``time_series[-1].spread_bps`` AND
     ``time_series_spread.rows[-1].value`` bit-for-bit.
 11. Wire-honesty: ``methodology_label`` is sourced from the YAML.
 12. Three import paths still resolve to the same Pydantic class.
 13. Canonical TimeSeries outputs: BPS for the spread, Z_SCORE
     for the rolling z-score, with the documented series_name
     pattern.

Tests are fully offline (DB-backed grounding lives in the SQL-
validation runner — see
``tests/test_cross_country_breakeven_spread_simple_sql_validation.py``).
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple import (
    CONFIG_PATH,
    CrossCountryBreakevenSpreadSimpleInput,
    calculate_cross_country_breakeven_spread_simple,
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


# Default DB-free country/currency lookup table.  The spot primitive's
# compute path issues a real instrument_master SELECT before either
# market-data fetch — composing it twice (per country) means this
# lookup runs four times in total (two legs × two countries).
_DEFAULT_COUNTRY_CURRENCY = {
    # nominal sovereigns
    ("UST",         "sovereign_benchmark"): ("US", "USD"),
    ("UK_GILT",     "sovereign_benchmark"): ("UK", "GBP"),
    ("FR_OAT",      "sovereign_benchmark"): ("France", "EUR"),
    ("DE_BUND",     "sovereign_benchmark"): ("Germany", "EUR"),
    ("IT_BTP",      "sovereign_benchmark"): ("Italy", "EUR"),
    ("CANADA_GOVT", "sovereign_benchmark"): ("Canada", "CAD"),
    # linkers
    ("USD_TIPS",      "inflation_linker"): ("US", "USD"),
    ("GBP_LINKER",    "inflation_linker"): ("UK", "GBP"),
    ("EUR_FR_LINKER", "inflation_linker"): ("France", "EUR"),
    ("EUR_DE_LINKER", "inflation_linker"): ("Germany", "EUR"),
    ("CAD_RRB",       "inflation_linker"): ("Canada", "CAD"),
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
        "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute._fetch_curve_family_country_currency",
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
        end_date=None,
    ):
        key = (curve_family, tenor, instrument_type)
        if key in legs:
            return legs[key]
        # Unknown combinations surface as "no rows" so a typo fails fast.
        return pd.DataFrame(columns=["trade_date", "field_value"])
    return _stub


class _FrozenDate(date):
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


def _build_legs_for_us_vs_eur_fr_10y(
    *,
    us_nominal: float = 4.40,
    us_linker: float = 2.00,
    eur_nominal: float = 3.20,
    eur_linker: float = 1.10,
    drift_pct: float = -0.30,
) -> dict:
    """Default canonical fixture for UST/USD_TIPS vs FR_OAT/EUR_FR_LINKER
    at 10Y.

    The four legs drift by different amounts so the resulting
    cross-country breakeven spread is NOT constant — necessary for
    z-score / period-change override tests to actually surface a
    convention-driven difference.

    Approx levels:
      US 10Y BE = (us_nominal - us_linker) * 100 = 240 bps
      EUR-FR 10Y BE = (eur_nominal - eur_linker) * 100 = 210 bps
      XC spread = 240 - 210 = 30 bps
    """
    days = 800
    return {
        ("UST", "10Y", "sovereign_benchmark"): _synthetic_single_leg(
            days=days, base_pct=us_nominal, drift_pct=drift_pct,
        ),
        ("USD_TIPS", "10Y", "inflation_linker"): _synthetic_single_leg(
            days=days, base_pct=us_linker, drift_pct=drift_pct - 0.20,
        ),
        ("FR_OAT", "10Y", "sovereign_benchmark"): _synthetic_single_leg(
            days=days, base_pct=eur_nominal, drift_pct=drift_pct + 0.10,
        ),
        ("EUR_FR_LINKER", "10Y", "inflation_linker"): _synthetic_single_leg(
            days=days, base_pct=eur_linker, drift_pct=drift_pct + 0.30,
        ),
    }


# Default-convention dict shared across the override-style tests below.
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
    "window_years_round_decimals": 4,
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
        "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
        new=_patched_fetch_factory(legs),
    ), patch(
        "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
        _FrozenDate,
    ):
        return calculate_cross_country_breakeven_spread_simple(
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
        assert (
            cfg.tool.name
            == "calculate_cross_country_breakeven_spread_simple_tool"
        )
        assert cfg.tool.domain == "inflation_indexed_bonds"
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
            "window_years_round_decimals",
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
        assert cfg.convention_value("bps_round_decimals") == 2
        assert cfg.convention_value("z_score_round_decimals") == 4
        assert cfg.convention_value("yield_round_decimals") == 4
        assert cfg.convention_value("window_years_round_decimals") == 4
        assert cfg.convention_value("default_field_name") == "YLD_YTM_MID"

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        # The prompt requires four planned extensions explicitly:
        # currency-hedged, IRP/liquidity-adjusted, forward
        # cross-country, inflation-swap-based.
        assert len(cfg.methodology.planned_extensions) >= 4
        joined = " | ".join(cfg.methodology.planned_extensions).lower()
        assert "currency-hedged" in joined or "currency hedged" in joined
        assert "liquidity" in joined and "irp" in joined.replace(
            "irp-", "irp",
        )
        assert "forward" in joined
        assert "swap" in joined

    def test_methodology_what_it_does_carries_disclosure(self):
        cfg = load_tool_config(CONFIG_PATH)
        text = cfg.methodology.what_it_does.lower()
        # Spread formula MUST be visible on the wire-disclosure.
        assert "breakeven_a_bps" in text and "breakeven_b_bps" in text
        # Sign convention MUST be visible on the wire-disclosure.
        assert "country_a minus country_b" in text
        # Inflation-compensation caveat MUST be visible.
        assert "compensation" in text
        # Index-family caveat MUST be visible (the wire disclosure
        # mentions the caveat by name; specific indices like CPI-U /
        # HICP live in the methodology.assumptions block to keep
        # what_it_does concise).
        assert "index-family" in text or "index family" in text

    def test_methodology_assumptions_carry_index_family_caveat(self):
        cfg = load_tool_config(CONFIG_PATH)
        joined = " | ".join(cfg.methodology.assumptions).lower()
        # Index-family caveat MUST be present in assumptions.
        assert "index" in joined and ("cpi" in joined or "hicp" in joined)
        # Generic-breakeven caveat (inherited from spot primitive)
        # MUST be present.
        assert "compensation" in joined and "expected inflation" in joined


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        legs = _build_legs_for_us_vs_eur_fr_10y()
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        for k in (
            "as_of_date",
            "country_a_nominal_pair",
            "country_a_linker_pair",
            "country_b_nominal_pair",
            "country_b_linker_pair",
            "tenor",
            "spread_label",
            "current_spread_bps",
            "daily_change_bps",
            "weekly_change_bps",
            "monthly_change_bps",
            "current_z_score",
            "rolling_window_days",
            "high_252d_bps",
            "low_252d_bps",
            "percentile_252d",
            "breakeven_a_bps",
            "breakeven_b_bps",
            "tenor_years",
            "methodology_label",
        ):
            assert k in cm, f"missing {k}"

        assert cm["tenor_years"] == 10.0
        assert cm["tenor"] == "10Y"
        assert cm["spread_label"] == (
            "UST/USD_TIPS - FR_OAT/EUR_FR_LINKER 10Y XC breakeven"
        )

    def test_spread_formula_is_correct(self):
        """Hard-pin the spread formula with constant input series so
        the per-country breakevens are deterministic.

          BE_a (US) = (4.50 - 2.00) * 100 = 250 bps
          BE_b (FR) = (3.20 - 1.10) * 100 = 210 bps
          spread = 250 - 210 = 40 bps
        """
        days = 800
        legs = {
            ("UST", "10Y", "sovereign_benchmark"): _synthetic_single_leg(
                days=days, base_pct=4.50, drift_pct=0.0,
            ),
            ("USD_TIPS", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=2.00, drift_pct=0.0,
            ),
            ("FR_OAT", "10Y", "sovereign_benchmark"): _synthetic_single_leg(
                days=days, base_pct=3.20, drift_pct=0.0,
            ),
            ("EUR_FR_LINKER", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=1.10, drift_pct=0.0,
            ),
        }
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["breakeven_a_bps"] == pytest.approx(250.0, abs=0.05)
        assert cm["breakeven_b_bps"] == pytest.approx(210.0, abs=0.05)
        assert cm["current_spread_bps"] == pytest.approx(40.0, abs=0.05)

    def test_sign_convention_country_a_minus_country_b(self):
        """Flipping the inputs (a↔b) MUST flip the displayed sign.
        The tool never silently flips internally."""
        days = 800
        legs = {
            ("UST", "10Y", "sovereign_benchmark"): _synthetic_single_leg(
                days=days, base_pct=4.50, drift_pct=0.0,
            ),
            ("USD_TIPS", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=2.00, drift_pct=0.0,
            ),
            ("FR_OAT", "10Y", "sovereign_benchmark"): _synthetic_single_leg(
                days=days, base_pct=3.20, drift_pct=0.0,
            ),
            ("EUR_FR_LINKER", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=1.10, drift_pct=0.0,
            ),
        }
        params_us_minus_eur = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        params_eur_minus_us = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="FR_OAT",
            country_a_linker_pair="EUR_FR_LINKER",
            country_b_nominal_pair="UST",
            country_b_linker_pair="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out_us_first = _run(params_us_minus_eur, legs)
        out_eur_first = _run(params_eur_minus_us, legs)

        a = out_us_first["current_metrics"]["current_spread_bps"]
        b = out_eur_first["current_metrics"]["current_spread_bps"]
        # Sign MUST flip exactly (within rounding).
        assert a == pytest.approx(-b, abs=0.05)
        # And label reflects the chosen direction:
        assert "UST/USD_TIPS - FR_OAT" in out_us_first["current_metrics"]["spread_label"]
        assert "FR_OAT/EUR_FR_LINKER - UST" in out_eur_first["current_metrics"]["spread_label"]

    def test_explicit_config_matches_auto_loaded(self):
        legs = _build_legs_for_us_vs_eur_fr_10y()
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out_auto = _run(params, legs, config=None)
        out_explicit = _run(
            params, legs, config=load_tool_config(CONFIG_PATH),
        )
        assert out_auto == out_explicit

    def test_eur_zone_pair_is_allowed_when_different_countries(self):
        """DE_BUND/EUR_DE_LINKER vs FR_OAT/EUR_FR_LINKER — same
        currency, different countries — is a legitimate cross-country
        pair and should compute end-to-end."""
        days = 800
        legs = {
            ("DE_BUND", "10Y", "sovereign_benchmark"): _synthetic_single_leg(
                days=days, base_pct=3.10, drift_pct=0.0,
            ),
            ("EUR_DE_LINKER", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=1.05, drift_pct=0.0,
            ),
            ("FR_OAT", "10Y", "sovereign_benchmark"): _synthetic_single_leg(
                days=days, base_pct=3.30, drift_pct=0.0,
            ),
            ("EUR_FR_LINKER", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=1.10, drift_pct=0.0,
            ),
        }
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="DE_BUND",
            country_a_linker_pair="EUR_DE_LINKER",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        # DE BE = (3.10 - 1.05)*100 = 205 bps
        # FR BE = (3.30 - 1.10)*100 = 220 bps
        # spread = 205 - 220 = -15 bps
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["current_spread_bps"] == pytest.approx(-15.0, abs=0.05)


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def test_z_score_window_override_changes_z(self):
        legs = _build_legs_for_us_vs_eur_fr_10y()
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
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
        legs = _build_legs_for_us_vs_eur_fr_10y()
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
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
        legs = _build_legs_for_us_vs_eur_fr_10y()
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
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

    def test_bps_round_decimals_override_changes_precision(self):
        legs = _build_legs_for_us_vs_eur_fr_10y(
            us_nominal=4.512345, us_linker=2.012345,
            eur_nominal=3.212345, eur_linker=1.112345,
        )
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out_2 = _run(
            params, legs, config=_build_config(bps_round_decimals=2),
        )
        out_4 = _run(
            params, legs, config=_build_config(bps_round_decimals=4),
        )
        v2 = out_2["current_metrics"]["current_spread_bps"]
        v4 = out_4["current_metrics"]["current_spread_bps"]
        assert v2 == round(v2, 2)
        assert v4 == round(v4, 4)


# ===========================================================================
# 4. Honest-placeholder guards
# ===========================================================================

class TestPlaceholderGuards:
    def test_unsupported_trailing_window_raises(self):
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            calculate_cross_country_breakeven_spread_simple(
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
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
        )
        assert params.field_name is None

    def test_nominal_pairs_must_differ(self):
        """country_a_nominal_pair == country_b_nominal_pair is rejected
        — that is what same-country tools are for."""
        with pytest.raises(Exception) as exc_info:
            CrossCountryBreakevenSpreadSimpleInput(
                country_a_nominal_pair="UST",
                country_a_linker_pair="USD_TIPS",
                country_b_nominal_pair="UST",
                country_b_linker_pair="USD_TIPS",
                tenor="10Y",
            )
        msg = str(exc_info.value).lower()
        assert "country_a_nominal_pair" in msg
        assert "country_b_nominal_pair" in msg

    def test_linker_pairs_must_differ(self):
        with pytest.raises(Exception):
            CrossCountryBreakevenSpreadSimpleInput(
                country_a_nominal_pair="UST",
                country_a_linker_pair="USD_TIPS",
                country_b_nominal_pair="DE_BUND",  # different nominal
                country_b_linker_pair="USD_TIPS",  # but same linker
                tenor="10Y",
            )

    def test_extra_fields_rejected(self):
        """``extra='forbid'`` ensures unknown kwargs surface as a
        ValidationError rather than being silently ignored."""
        with pytest.raises(Exception):
            CrossCountryBreakevenSpreadSimpleInput(
                country_a_nominal_pair="UST",
                country_a_linker_pair="USD_TIPS",
                country_b_nominal_pair="FR_OAT",
                country_b_linker_pair="EUR_FR_LINKER",
                tenor="10Y",
                bogus_param=123,
            )


# ===========================================================================
# 6. field_name → YAML default fall-through
# ===========================================================================

class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_field_name reaches all FOUR
    inner fetches (two countries × two legs each)."""

    def _capture_fetch_calls(self, params, config, legs):
        captured = []

        def _stub(**kwargs):
            captured.append(kwargs)
            key = (kwargs["curve_family"], kwargs["tenor"], kwargs["instrument_type"])
            return legs.get(
                key, pd.DataFrame(columns=["trade_date", "field_value"]),
            )

        with patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_stub,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ):
            calculate_cross_country_breakeven_spread_simple(
                engine=None, params=params, config=config,
            )
        return captured

    def test_omitted_field_name_uses_yaml_default(self):
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_field_name="YLD_YTM_MID"),
            _build_legs_for_us_vs_eur_fr_10y(),
        )
        # Two countries × two legs each = 4 fetches.
        assert len(captured) == 4
        for call in captured:
            assert call["field_name"] == "YLD_YTM_MID"

    def test_yaml_override_changes_resolved_field(self):
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_field_name="YLD_YTM_BID"),
            _build_legs_for_us_vs_eur_fr_10y(),
        )
        for call in captured:
            assert call["field_name"] == "YLD_YTM_BID"

    def test_explicit_field_name_overrides_yaml(self):
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
            field_name="YLD_YTM_ASK",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_field_name="YLD_YTM_MID"),
            _build_legs_for_us_vs_eur_fr_10y(),
        )
        for call in captured:
            assert call["field_name"] == "YLD_YTM_ASK"


# ===========================================================================
# 7. Composition guard inheritance
# ===========================================================================

class TestCompositionGuards:
    """Cross-country composes the spot primitive twice; the spot's
    no-proxy guard (instrument_type filter on each leg) AND
    same-country invariant must fire on each country leg and surface
    as a leg-attributed controlled error envelope when violated.
    """

    def test_missing_country_a_returns_error_envelope(self):
        legs = _build_legs_for_us_vs_eur_fr_10y()
        legs[("USD_TIPS", "10Y", "inflation_linker")] = pd.DataFrame(
            columns=["trade_date", "field_value"],
        )
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "country_a leg failed" in out["error"]

    def test_missing_country_b_returns_error_envelope(self):
        legs = _build_legs_for_us_vs_eur_fr_10y()
        legs[("FR_OAT", "10Y", "sovereign_benchmark")] = pd.DataFrame(
            columns=["trade_date", "field_value"],
        )
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "country_b leg failed" in out["error"]

    def test_leg_internal_cross_country_pollution_a_refused(self):
        """country_a_nominal=UST + country_a_linker=EUR_FR_LINKER
        violates the per-leg same-country invariant inside the spot
        primitive — must be refused with a controlled error envelope
        attributed to country_a."""
        legs: dict = {}
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="EUR_FR_LINKER",  # leg-A pollution
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        # Leg-attributed prefix
        assert "country_a leg failed" in out["error"]
        # Inner same-country invariant message must propagate.
        assert "same-country" in out["error"].lower()

    def test_leg_internal_cross_country_pollution_b_refused(self):
        """Symmetric: country_b_nominal=FR_OAT + country_b_linker=
        GBP_LINKER violates the per-leg same-country invariant for
        country_b's leg — must be refused with a controlled error
        envelope attributed to country_b.  Country_a is valid
        (UST + USD_TIPS, both US/USD) so the spot primitive
        succeeds for the first call; country_b's invariant fails
        before any country_b market-data SELECT."""
        # Provide valid country_a legs so the country_a inner call
        # actually computes; the country_b leg's same-country
        # invariant must be the failing layer.
        legs = {
            ("UST", "10Y", "sovereign_benchmark"): _synthetic_single_leg(
                days=800, base_pct=4.50, drift_pct=-0.30,
            ),
            ("USD_TIPS", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=800, base_pct=2.00, drift_pct=-0.50,
            ),
        }
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="GBP_LINKER",  # leg-B pollution
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "country_b leg failed" in out["error"]
        assert "same-country" in out["error"].lower()

    def test_per_leg_same_country_guard_runs_before_any_fetch(self):
        """Leg-internal cross-country pollution must be refused
        BEFORE any market-data SELECT (verified by recording fetch
        calls)."""
        fetch_calls = []

        def _record_fetch(**kwargs):
            fetch_calls.append(kwargs)
            return pd.DataFrame(columns=["trade_date", "field_value"])

        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="EUR_FR_LINKER",  # leg-A pollution
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_record_fetch,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ):
            out = calculate_cross_country_breakeven_spread_simple(
                engine=None, params=params,
            )
        assert "error" in out
        assert fetch_calls == [], (
            "Leg-internal cross-country pollution must be refused "
            "BEFORE any market-data fetch fires.  Got "
            f"{len(fetch_calls)} fetch call(s)."
        )


# ===========================================================================
# 8. Per-trade-date alignment
# ===========================================================================

class TestPerTradeDateAlignment:
    """Inner-join discipline across the cross-country pair: dates
    where one country has no breakeven data are dropped from the
    spread series, NOT carried forward."""

    def test_missing_country_date_is_dropped(self):
        """Drop a single business day from country_b's nominal AND
        linker legs so the country_b breakeven is truly missing on
        that date.  Verify the spread series on that date is missing."""
        legs = _build_legs_for_us_vs_eur_fr_10y()

        all_dates = sorted(
            legs[("FR_OAT", "10Y", "sovereign_benchmark")]["trade_date"].tolist()
        )
        mid_date = all_dates[len(all_dates) // 2]
        for key in (
            ("FR_OAT", "10Y", "sovereign_benchmark"),
            ("EUR_FR_LINKER", "10Y", "inflation_linker"),
        ):
            df = legs[key]
            legs[key] = df[df["trade_date"] != mid_date].reset_index(drop=True)

        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")

        # Strict inner join: the missing date should not be in the
        # bespoke time_series at all.
        bespoke_dates = {row["date"] for row in out["time_series"]}
        assert mid_date.strftime("%Y-%m-%d") not in bespoke_dates


# ===========================================================================
# 9. Boundary-rounding parity
# ===========================================================================

class TestBoundaryRoundingParity:
    def test_snapshot_bps_matches_bespoke_last_row_strictly(self):
        legs = _build_legs_for_us_vs_eur_fr_10y()
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        snap = out["current_metrics"]["current_spread_bps"]
        bespoke_last = out["time_series"][-1]["spread_bps"]
        assert snap == bespoke_last

    def test_snapshot_bps_matches_canonical_last_row_strictly(self):
        legs = _build_legs_for_us_vs_eur_fr_10y()
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        snap = out["current_metrics"]["current_spread_bps"]
        canon_last = out["time_series_spread"]["rows"][-1]["value"]
        assert snap == canon_last

    def test_bespoke_z_score_matches_canonical_z_score_per_row(self):
        legs = _build_legs_for_us_vs_eur_fr_10y()
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
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
        legs = _build_legs_for_us_vs_eur_fr_10y()
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        custom_text = (
            "CUSTOM-XC-DISCLOSURE-77 cross-country breakeven "
            "differential breakeven_a_bps - breakeven_b_bps "
            "country_a minus country_b CPI-U HICP index-family"
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
        legs = _build_legs_for_us_vs_eur_fr_10y()
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs, config=None)
        label = out["current_metrics"]["methodology_label"].lower()
        # Spread formula MUST be visible on the wire.
        assert "breakeven_a_bps" in label
        assert "breakeven_b_bps" in label
        # Sign convention MUST be visible on the wire.
        assert "country_a minus country_b" in label
        # Inflation-compensation caveat MUST be visible.
        assert "compensation" in label
        # Index-family caveat MUST be named in the disclosure — and
        # the load-bearing detail (named indices + no harmonisation
        # + consequence framing) MUST reach the wire, not just live
        # in the YAML's methodology.assumptions block.
        assert "index-family" in label or "index family" in label
        # Named-index detail per country MUST reach the wire.
        assert "cpi-u" in label, (
            "USD TIPS reference (CPI-U) must reach the wire"
        )
        assert "hicp" in label, (
            "EUR-area linker reference (HICP) must reach the wire"
        )
        assert "rpi" in label, (
            "UK linker reference (RPI) must reach the wire"
        )
        assert "cpi" in label, (
            "Canada CPI reference must reach the wire"
        )
        # Canada-specific named-index check (note 'cpi' alone is
        # implied by 'cpi-u', so we additionally require an
        # explicit Canadian RRBs reference).
        assert "rrbs" in label, (
            "Canadian RRBs (Canada CPI) reference must reach the wire"
        )
        # No-harmonisation guarantee MUST reach the wire.
        assert (
            "not attempt to harmonise" in label
            or "does not harmonise" in label
            or "no harmonisation" in label
        ), (
            "the 'no harmonisation' guarantee must reach the wire"
        )


# ===========================================================================
# 11. Import path stability
# ===========================================================================

class TestImportPathStability:
    def test_function_via_package_init(self):
        from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple import (
            calculate_cross_country_breakeven_spread_simple as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple.compute import (
            calculate_cross_country_breakeven_spread_simple as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple import (
            CrossCountryBreakevenSpreadSimpleInput as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple.schemas import (
            CrossCountryBreakevenSpreadSimpleInput as via_schemas,
        )
        from rates_agent.inflation_indexed_bonds.tools.schemas import (
            CrossCountryBreakevenSpreadSimpleInput as via_hub,
        )
        assert via_package is via_schemas is via_hub


# ===========================================================================
# 12. Canonical TimeSeries outputs
# ===========================================================================

class TestCanonicalTimeSeries:
    def test_canonical_spread_uses_bps_units_and_naming(self):
        legs = _build_legs_for_us_vs_eur_fr_10y()
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        ts = out["time_series_spread"]
        assert ts["units"] == "bps"
        assert ts["series_name"] == (
            "ust_usd_tips_fr_oat_eur_fr_linker_10y_xc_breakeven_spread"
        )

    def test_canonical_zscore_uses_z_score_units_and_naming(self):
        legs = _build_legs_for_us_vs_eur_fr_10y()
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        ts = out["time_series_zscore"]
        assert ts["units"] == "z_score"
        assert ts["series_name"] == (
            "ust_usd_tips_fr_oat_eur_fr_linker_10y_xc_breakeven_spread_zscore"
        )

    def test_canonical_lengths_match_bespoke(self):
        legs = _build_legs_for_us_vs_eur_fr_10y()
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
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
        legs = _build_legs_for_us_vs_eur_fr_10y()
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        TimeSeries.model_validate(out["time_series_spread"])
        TimeSeries.model_validate(out["time_series_zscore"])

    def test_canonical_dates_chronological(self):
        legs = _build_legs_for_us_vs_eur_fr_10y()
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair="UST",
            country_a_linker_pair="USD_TIPS",
            country_b_nominal_pair="FR_OAT",
            country_b_linker_pair="EUR_FR_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        dates = [r["date"] for r in out["time_series_spread"]["rows"]]
        assert dates == sorted(dates)
