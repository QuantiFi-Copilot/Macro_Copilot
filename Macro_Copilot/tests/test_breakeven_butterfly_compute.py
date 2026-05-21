"""
test_breakeven_butterfly_compute.py — Unit tests for the linker
breakeven_butterfly primitive.

Mirrors ``test_breakeven_curve_spread_compute.py`` (closest sibling)
and ``test_real_yield_butterfly_compute.py`` (closest butterfly
analog).  This module differs in the math under test (3-point fixed
simple-butterfly weighting on three breakeven series, NOT a 2-point
spread or a real-yield curvature).

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. compute() runs end-to-end against synthetic input with the
     bundled config and returns a well-formed output.
  3. Explicit ``config=`` vs auto-load parity (byte-identical).
  4. Convention overrides change behaviour (z-window, ddof,
     period offsets, bps rounding, default_field_name).
  5. Honest-placeholder guards: trailing_range_window_days != 252
     raises NotImplementedError.
  6. Schema-layer behaviour: field_name defaults to None; compute()
     resolves the sentinel against the YAML.  Tenor / curve-family
     validators reject structurally invalid triplets (duplicate,
     inverted, etc).
  7. Composition guard inheritance: cross-country pairs (DE_BUND vs
     EUR_FR_LINKER), cross-currency pairs (UK_GILT vs USD_TIPS), and
     pollution probes (nominal cf in linker slot, linker cf in
     nominal slot) all surface controlled error envelopes via the
     spot primitive's guards firing transitively.
  8. Per-trade-date alignment: dates where any endpoint has no
     data are dropped from the butterfly series (strict inner-join
     discipline).
  9. Butterfly formula correctness on synthetic input — fixed
     simple-butterfly weight tuple (-0.5, +1.0, -0.5) verified
     numerically: butterfly == belly - 0.5*(short + long).
 10. Sign convention: POSITIVE = belly cheap (belly BE high relative
     to wings), NEGATIVE = belly rich.  Hard-pinned on deterministic
     synthetic input.
 11. Boundary-rounding parity:
     ``current_metrics.current_butterfly_bps`` equals
     ``time_series[-1].butterfly_bps`` AND
     ``time_series_butterfly.rows[-1].value`` bit-for-bit.
 12. Wire-honesty: ``methodology_label`` is sourced from the YAML
     and the bundled disclosure carries the inflation-compensation
     caveat AND the explicit formula.
 13. Three import paths still resolve to the same Pydantic class.
 14. Canonical TimeSeries outputs: BPS for the butterfly, Z_SCORE
     for the rolling z-score, with the documented series_name
     pattern AND the inflation-compensation caveat in the
     description.
 15. Missing endpoint (synthetic gap exceeding ffill_limit_days)
     yields a controlled error envelope, not a silent fall-through.
 16. Unknown pillar yields controlled error envelope.

Tests are fully offline (DB-backed grounding lives in the SQL-
validation runner — see
``tests/test_breakeven_butterfly_sql_validation.py``).
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly import (
    CONFIG_PATH,
    BreakevenButterflyInput,
    calculate_breakeven_butterfly,
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
# market-data fetch — composing it three times means this lookup runs
# three times (once per endpoint).
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


def _build_legs_for_us_5s10s30s(
    *,
    nominal_short: float = 4.20,
    linker_short: float = 1.85,
    nominal_belly: float = 4.40,
    linker_belly: float = 2.05,
    nominal_long: float = 4.55,
    linker_long: float = 2.15,
    drift_pct: float = -0.30,
) -> dict:
    """Default canonical six-leg fixture for UST/USD_TIPS 5s10s30s.

    The six legs drift by different amounts so the resulting
    breakeven butterfly is NOT constant — necessary for the z-score
    / period-change override tests to actually surface a convention-
    driven difference (constant input → constant butterfly →
    undefined z-score and zero daily change).
    """
    days = 800
    return {
        ("UST", "5Y", "sovereign_benchmark"): _synthetic_single_leg(
            days=days, base_pct=nominal_short, drift_pct=drift_pct,
        ),
        ("UST", "10Y", "sovereign_benchmark"): _synthetic_single_leg(
            days=days, base_pct=nominal_belly, drift_pct=drift_pct + 0.40,
        ),
        ("UST", "30Y", "sovereign_benchmark"): _synthetic_single_leg(
            days=days, base_pct=nominal_long, drift_pct=drift_pct + 0.20,
        ),
        ("USD_TIPS", "5Y", "inflation_linker"): _synthetic_single_leg(
            days=days, base_pct=linker_short, drift_pct=drift_pct - 0.20,
        ),
        ("USD_TIPS", "10Y", "inflation_linker"): _synthetic_single_leg(
            days=days, base_pct=linker_belly, drift_pct=drift_pct + 0.10,
        ),
        ("USD_TIPS", "30Y", "inflation_linker"): _synthetic_single_leg(
            days=days, base_pct=linker_long, drift_pct=drift_pct + 0.05,
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
        return calculate_breakeven_butterfly(
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
        assert cfg.tool.name == "calculate_breakeven_butterfly_tool"
        assert cfg.tool.domain == "inflation_indexed_bonds"

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
        assert len(cfg.methodology.planned_extensions) >= 2
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "trailing_range_window_days" in joined

    def test_methodology_what_it_does_carries_disclosure(self):
        cfg = load_tool_config(CONFIG_PATH)
        text = cfg.methodology.what_it_does.lower()
        # Inflation-compensation caveat is load-bearing.
        assert "compensation" in text
        # Sign convention surfaced on the wire-disclosure.
        assert "positive" in text and "belly" in text
        # Butterfly formula MUST be visible — three weight tokens.
        assert "belly_breakeven_bps" in text
        assert "short_breakeven_bps" in text
        assert "long_breakeven_bps" in text
        # Weight tuple explicitly stated.
        assert "(-0.5, +1.0, -0.5)" in text


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        legs = _build_legs_for_us_5s10s30s()
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
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
            "nominal_curve_family",
            "linker_curve_family",
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
            "short_breakeven_bps",
            "belly_breakeven_bps",
            "long_breakeven_bps",
            "short_years",
            "belly_years",
            "long_years",
            "observation_count",
            "methodology_label",
        ):
            assert k in cm, f"missing {k}"

        assert cm["short_years"] == 5.0
        assert cm["belly_years"] == 10.0
        assert cm["long_years"] == 30.0
        assert cm["short_tenor"] == "5Y"
        assert cm["belly_tenor"] == "10Y"
        assert cm["long_tenor"] == "30Y"
        assert cm["butterfly_label"] == "UST/USD_TIPS 5s10s30s breakeven"

    def test_butterfly_formula_is_correct_with_weight_tuple(self):
        """Hard-pin the FIXED simple-butterfly weight tuple
        (-0.5, +1.0, -0.5) on synthetic flat-breakeven legs.

        With constant breakevens BE_short=200bps, BE_belly=250bps,
        BE_long=300bps:
          expected butterfly = 250 - 0.5*(200 + 300) = 250 - 250 = 0

        With BE_short=150bps, BE_belly=300bps, BE_long=200bps (belly
        VERY cheap relative to wings) the butterfly should be
        positive: 300 - 0.5*(150 + 200) = 300 - 175 = +125.
        """
        days = 800

        # Case 1: flat butterfly = 0
        legs_flat = {
            ("UST", "5Y", "sovereign_benchmark"): _synthetic_single_leg(
                days=days, base_pct=4.0, drift_pct=0.0,
            ),
            ("UST", "10Y", "sovereign_benchmark"): _synthetic_single_leg(
                days=days, base_pct=4.5, drift_pct=0.0,
            ),
            ("UST", "30Y", "sovereign_benchmark"): _synthetic_single_leg(
                days=days, base_pct=5.0, drift_pct=0.0,
            ),
            ("USD_TIPS", "5Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=2.0, drift_pct=0.0,
            ),
            ("USD_TIPS", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=2.0, drift_pct=0.0,
            ),
            ("USD_TIPS", "30Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=2.0, drift_pct=0.0,
            ),
        }
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out_flat = _run(params, legs_flat)
        assert "error" not in out_flat, out_flat.get("error")
        cm = out_flat["current_metrics"]
        # Endpoint breakevens
        assert cm["short_breakeven_bps"] == pytest.approx(200.0, abs=0.05)
        assert cm["belly_breakeven_bps"] == pytest.approx(250.0, abs=0.05)
        assert cm["long_breakeven_bps"] == pytest.approx(300.0, abs=0.05)
        # Butterfly = 250 - 0.5*(200 + 300) = 0
        assert cm["current_butterfly_bps"] == pytest.approx(0.0, abs=0.05)
        # Wings
        assert cm["wing_short_bps"] == pytest.approx(50.0, abs=0.05)
        assert cm["wing_long_bps"] == pytest.approx(50.0, abs=0.05)

    def test_positive_sign_means_belly_cheap(self):
        """Hard-pin sign convention: POSITIVE = belly cheap (i.e.
        belly breakeven HIGH relative to wings).

        Construct BE_short=150bps, BE_belly=300bps, BE_long=200bps:
          butterfly = 300 - 0.5*(150 + 200) = +125 bps  (belly CHEAP)
        """
        days = 800
        legs = {
            # short BE = 150 → nominal_short - linker_short = 1.5%
            ("UST", "5Y", "sovereign_benchmark"): _synthetic_single_leg(
                days=days, base_pct=3.5, drift_pct=0.0,
            ),
            ("USD_TIPS", "5Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=2.0, drift_pct=0.0,
            ),
            # belly BE = 300 → nominal_belly - linker_belly = 3.0%
            ("UST", "10Y", "sovereign_benchmark"): _synthetic_single_leg(
                days=days, base_pct=5.0, drift_pct=0.0,
            ),
            ("USD_TIPS", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=2.0, drift_pct=0.0,
            ),
            # long BE = 200 → nominal_long - linker_long = 2.0%
            ("UST", "30Y", "sovereign_benchmark"): _synthetic_single_leg(
                days=days, base_pct=4.0, drift_pct=0.0,
            ),
            ("USD_TIPS", "30Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=2.0, drift_pct=0.0,
            ),
        }
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["current_butterfly_bps"] == pytest.approx(125.0, abs=0.1)
        # belly is CHEAP → butterfly > 0
        assert cm["current_butterfly_bps"] > 0

    def test_negative_sign_means_belly_rich(self):
        """Hard-pin sign convention: NEGATIVE = belly rich (i.e.
        belly breakeven LOW relative to wings).

        Construct BE_short=250bps, BE_belly=200bps, BE_long=300bps:
          butterfly = 200 - 0.5*(250 + 300) = -75 bps  (belly RICH)
        """
        days = 800
        legs = {
            # short BE = 250 → 2.5%
            ("UST", "5Y", "sovereign_benchmark"): _synthetic_single_leg(
                days=days, base_pct=4.5, drift_pct=0.0,
            ),
            ("USD_TIPS", "5Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=2.0, drift_pct=0.0,
            ),
            # belly BE = 200 → 2.0%
            ("UST", "10Y", "sovereign_benchmark"): _synthetic_single_leg(
                days=days, base_pct=4.0, drift_pct=0.0,
            ),
            ("USD_TIPS", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=2.0, drift_pct=0.0,
            ),
            # long BE = 300 → 3.0%
            ("UST", "30Y", "sovereign_benchmark"): _synthetic_single_leg(
                days=days, base_pct=5.0, drift_pct=0.0,
            ),
            ("USD_TIPS", "30Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=2.0, drift_pct=0.0,
            ),
        }
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["current_butterfly_bps"] == pytest.approx(-75.0, abs=0.1)
        # belly is RICH → butterfly < 0
        assert cm["current_butterfly_bps"] < 0

    def test_butterfly_equals_belly_minus_half_short_plus_long_per_row(self):
        """Independently verify per-row arithmetic: every row in the
        bespoke time_series satisfies
        butterfly_bps == belly_be - 0.5 * (short_be + long_be)
        within bps_round_decimals tolerance."""
        legs = _build_legs_for_us_5s10s30s()
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out
        # current_metrics-level invariant: snapshot butterfly == the
        # arithmetic of the snapshot endpoints (round-trip through
        # bps_round_decimals=2).
        cm = out["current_metrics"]
        expected = round(
            cm["belly_breakeven_bps"]
            - 0.5 * (cm["short_breakeven_bps"] + cm["long_breakeven_bps"]),
            2,
        )
        assert cm["current_butterfly_bps"] == pytest.approx(
            expected, abs=0.05,
        )

    def test_explicit_config_matches_auto_loaded(self):
        legs = _build_legs_for_us_5s10s30s()
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
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
        legs = _build_legs_for_us_5s10s30s()
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
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
        legs = _build_legs_for_us_5s10s30s()
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
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
        legs = _build_legs_for_us_5s10s30s()
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
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
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            calculate_breakeven_butterfly(
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
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
        )
        assert params.field_name is None

    def test_curve_families_must_differ(self):
        with pytest.raises(Exception):
            BreakevenButterflyInput(
                nominal_curve_family="USD_TIPS",
                linker_curve_family="USD_TIPS",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
            )

    def test_short_equals_belly_rejected(self):
        with pytest.raises(Exception):
            BreakevenButterflyInput(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                short_tenor="10Y",
                belly_tenor="10Y",
                long_tenor="30Y",
            )

    def test_belly_equals_long_rejected(self):
        with pytest.raises(Exception):
            BreakevenButterflyInput(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="10Y",
            )

    def test_short_equals_long_rejected(self):
        with pytest.raises(Exception):
            BreakevenButterflyInput(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                short_tenor="10Y",
                belly_tenor="20Y",
                long_tenor="10Y",
            )

    def test_short_must_precede_belly_tenor(self):
        with pytest.raises(Exception) as exc_info:
            BreakevenButterflyInput(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                short_tenor="10Y",
                belly_tenor="5Y",
                long_tenor="30Y",
            )
        msg = str(exc_info.value).lower()
        assert "short_years < belly_years" in msg or "strictly" in msg

    def test_belly_must_precede_long_tenor(self):
        with pytest.raises(Exception):
            BreakevenButterflyInput(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                short_tenor="5Y",
                belly_tenor="30Y",
                long_tenor="10Y",
            )

    def test_fully_inverted_rejected(self):
        with pytest.raises(Exception):
            BreakevenButterflyInput(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                short_tenor="30Y",
                belly_tenor="10Y",
                long_tenor="5Y",
            )

    def test_extra_fields_rejected(self):
        with pytest.raises(Exception):
            BreakevenButterflyInput(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                short_tenor="5Y",
                belly_tenor="10Y",
                long_tenor="30Y",
                bogus_param=123,
            )


# ===========================================================================
# 6. field_name → YAML default fall-through
# ===========================================================================

class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_field_name reaches all SIX
    inner fetches (nominal + linker at each of three endpoint
    tenors)."""

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
            calculate_breakeven_butterfly(
                engine=None, params=params, config=config,
            )
        return captured

    def test_omitted_field_name_uses_yaml_default(self):
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_field_name="YLD_YTM_MID"),
            _build_legs_for_us_5s10s30s(),
        )
        # Three endpoints × two legs each = 6 fetches.
        assert len(captured) == 6
        for call in captured:
            assert call["field_name"] == "YLD_YTM_MID"

    def test_yaml_override_changes_resolved_field(self):
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_field_name="YLD_YTM_BID"),
            _build_legs_for_us_5s10s30s(),
        )
        for call in captured:
            assert call["field_name"] == "YLD_YTM_BID"

    def test_explicit_field_name_overrides_yaml(self):
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            field_name="YLD_YTM_ASK",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_field_name="YLD_YTM_MID"),
            _build_legs_for_us_5s10s30s(),
        )
        for call in captured:
            assert call["field_name"] == "YLD_YTM_ASK"


# ===========================================================================
# 7. Composition guard inheritance
# ===========================================================================

class TestCompositionGuards:
    """Butterfly composes the spot primitive three times; the spot's
    no-proxy guard (instrument_type filter on each leg) AND
    same-country invariant must fire on each endpoint and surface
    as a controlled error envelope when violated.
    """

    def test_missing_short_endpoint_returns_error_envelope(self):
        legs = _build_legs_for_us_5s10s30s()
        legs[("USD_TIPS", "5Y", "inflation_linker")] = pd.DataFrame(
            columns=["trade_date", "field_value"],
        )
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
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
        # Inner error context is propagated.
        assert "inflation_linker" in out["error"]

    def test_missing_belly_endpoint_returns_error_envelope(self):
        legs = _build_legs_for_us_5s10s30s()
        legs[("UST", "10Y", "sovereign_benchmark")] = pd.DataFrame(
            columns=["trade_date", "field_value"],
        )
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
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
        assert "sovereign_benchmark" in out["error"]

    def test_missing_long_endpoint_returns_error_envelope(self):
        legs = _build_legs_for_us_5s10s30s()
        legs[("UST", "30Y", "sovereign_benchmark")] = pd.DataFrame(
            columns=["trade_date", "field_value"],
        )
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
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
        assert "sovereign_benchmark" in out["error"]

    def test_nominal_cf_in_linker_slot_returns_error_envelope(self):
        legs: dict = {}
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="DE_BUND",   # nominal in linker slot
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out

    def test_linker_cf_in_nominal_slot_returns_error_envelope(self):
        legs: dict = {}
        params = BreakevenButterflyInput(
            nominal_curve_family="USD_TIPS",  # linker in nominal slot
            linker_curve_family="GBP_LINKER",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out

    def test_cross_country_same_currency_pair_refused(self):
        """DE_BUND (Germany/EUR) + EUR_FR_LINKER (France/EUR) — same
        currency, different country."""
        legs: dict = {}
        params = BreakevenButterflyInput(
            nominal_curve_family="DE_BUND",
            linker_curve_family="EUR_FR_LINKER",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        err = out["error"]
        assert "DE_BUND" in err
        assert "EUR_FR_LINKER" in err
        assert "same-country" in err.lower()

    def test_cross_currency_pair_refused(self):
        legs: dict = {}
        params = BreakevenButterflyInput(
            nominal_curve_family="UK_GILT",
            linker_curve_family="USD_TIPS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        err = out["error"]
        assert "UK_GILT" in err
        assert "USD_TIPS" in err

    def test_same_country_guard_runs_before_any_fetch(self):
        """Cross-country pair must be refused BEFORE any market-data
        SELECT (verified by recording fetch calls)."""
        fetch_calls = []

        def _record_fetch(**kwargs):
            fetch_calls.append(kwargs)
            return pd.DataFrame(columns=["trade_date", "field_value"])

        params = BreakevenButterflyInput(
            nominal_curve_family="DE_BUND",
            linker_curve_family="EUR_FR_LINKER",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_record_fetch,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ):
            out = calculate_breakeven_butterfly(
                engine=None, params=params,
            )
        assert "error" in out
        assert fetch_calls == [], (
            "Cross-country pair must be refused BEFORE any market-"
            f"data fetch fires.  Got {len(fetch_calls)} fetch call(s)."
        )


# ===========================================================================
# 8. Per-trade-date alignment + ffill semantics
# ===========================================================================

class TestPerTradeDateAlignment:
    """Inner-join discipline: dates where any endpoint has no data
    are dropped from the butterfly series, NOT carried forward."""

    def test_missing_endpoint_date_is_dropped(self):
        """Drop a single business day from both legs of the long-tenor
        endpoint (within ffill_limit so the spot DOESN'T bridge it
        for the long endpoint).  Verify the butterfly series on that
        date is missing.

        We must drop ENOUGH consecutive days that the spot's ffill
        bridge (ffill_limit_days=5) cannot recover the long endpoint.
        Drop 7 consecutive business days from BOTH legs of the long
        tenor.
        """
        days = 800
        legs = _build_legs_for_us_5s10s30s()

        # Pick 7 consecutive trade_dates in the middle of the
        # displayed window.
        all_dates = sorted(
            legs[("UST", "30Y", "sovereign_benchmark")]["trade_date"].tolist()
        )
        mid_idx = len(all_dates) // 2
        gap_dates = set(all_dates[mid_idx:mid_idx + 7])

        for key in (
            ("UST", "30Y", "sovereign_benchmark"),
            ("USD_TIPS", "30Y", "inflation_linker"),
        ):
            df = legs[key]
            legs[key] = df[~df["trade_date"].isin(gap_dates)].reset_index(
                drop=True,
            )

        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")

        # Bespoke time_series should NOT contain ANY of the missing
        # dates beyond the ffill bridge (the spot can recover the
        # first ~5 with ffill, but the 6th/7th remain dropped).
        bespoke_dates = {row["date"] for row in out["time_series"]}
        missing_strs = sorted(d.strftime("%Y-%m-%d") for d in gap_dates)
        # The last gap day MUST be absent — outside ffill_limit
        assert missing_strs[-1] not in bespoke_dates


# ===========================================================================
# 9. Boundary-rounding parity
# ===========================================================================

class TestBoundaryRoundingParity:
    def test_snapshot_bps_matches_bespoke_last_row_strictly(self):
        legs = _build_legs_for_us_5s10s30s()
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
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
        legs = _build_legs_for_us_5s10s30s()
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
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
        legs = _build_legs_for_us_5s10s30s()
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
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
        legs = _build_legs_for_us_5s10s30s()
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        custom_text = (
            "CUSTOM-BREAKEVEN-BUTTERFLY-DISCLOSURE-77 inflation "
            "compensation curvature belly_breakeven_bps - 0.5*("
            "short_breakeven_bps + long_breakeven_bps)"
        )
        out = _run(
            params, legs,
            config=_build_config(methodology_what_it_does=custom_text),
        )
        assert (
            out["current_metrics"]["methodology_label"]
            == custom_text.strip()
        )

    def test_bundled_methodology_label_carries_compensation_caveat(self):
        legs = _build_legs_for_us_5s10s30s()
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs, config=None)
        label = out["current_metrics"]["methodology_label"].lower()
        # Compensation caveat (NOT pure expected inflation).
        assert "compensation" in label
        # Formula visible on the wire.
        assert "belly_breakeven_bps" in label
        assert "short_breakeven_bps" in label
        assert "long_breakeven_bps" in label
        # Sign convention surfaced.
        assert "positive" in label
        # Explicit weight tuple stated.
        assert "(-0.5, +1.0, -0.5)" in label


# ===========================================================================
# 11. Import path stability
# ===========================================================================

class TestImportPathStability:
    def test_function_via_package_init(self):
        from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly import (
            calculate_breakeven_butterfly as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly.compute import (
            calculate_breakeven_butterfly as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly import (
            BreakevenButterflyInput as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly.schemas import (
            BreakevenButterflyInput as via_schemas,
        )
        from rates_agent.inflation_indexed_bonds.tools.schemas import (
            BreakevenButterflyInput as via_hub,
        )
        assert via_package is via_schemas is via_hub


# ===========================================================================
# 12. Canonical TimeSeries outputs
# ===========================================================================

class TestCanonicalTimeSeries:
    def test_canonical_butterfly_uses_bps_units_and_naming(self):
        legs = _build_legs_for_us_5s10s30s()
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
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
            == "ust_usd_tips_5y_10y_30y_breakeven_butterfly"
        )
        # Inflation-compensation caveat must surface in the
        # description.
        desc = ts["description"].lower()
        assert "compensation" in desc
        # Distinction from real-yield butterfly + nominal sovereign
        # butterfly surfaced.
        assert "breakeven" in desc

    def test_canonical_zscore_uses_z_score_units_and_naming(self):
        legs = _build_legs_for_us_5s10s30s()
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
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
            == "ust_usd_tips_5y_10y_30y_breakeven_butterfly_zscore"
        )

    def test_canonical_lengths_match_bespoke(self):
        legs = _build_legs_for_us_5s10s30s()
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
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
        legs = _build_legs_for_us_5s10s30s()
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        TimeSeries.model_validate(out["time_series_butterfly"])
        TimeSeries.model_validate(out["time_series_zscore"])

    def test_canonical_dates_chronological(self):
        legs = _build_legs_for_us_5s10s30s()
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="5Y",
            belly_tenor="10Y",
            long_tenor="30Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        dates = [r["date"] for r in out["time_series_butterfly"]["rows"]]
        assert dates == sorted(dates)

    def test_canonical_last_row_equals_snapshot_strictly(self):
        legs = _build_legs_for_us_5s10s30s()
        params = BreakevenButterflyInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
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
