"""
test_breakeven_curve_spread_compute.py — Unit tests for the linker
breakeven_curve_spread primitive.

Mirrors ``test_forward_breakeven_simple_compute.py`` — both
primitives compose ``breakeven_inflation_simple`` twice and
inherit the spot guards transitively.  This module differs in
the math under test (per-trade-date difference, NOT a year-
weighted forward).

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
     compute() resolves the sentinel against the YAML.  Tenor /
     curve-family validators reject structurally invalid pairs.
  7. Composition guard inheritance: cross-country pairs (DE_BUND
     vs EUR_FR_LINKER), cross-currency pairs (UK_GILT vs
     USD_TIPS), and pollution probes (nominal cf in linker slot,
     linker cf in nominal slot) all surface controlled error
     envelopes via the spot primitive's guards firing transitively.
  8. Per-trade-date alignment: dates where one endpoint has no
     data are dropped from the spread series.
  9. Spread formula correctness on synthetic input.
 10. Boundary-rounding parity:
     ``current_metrics.current_spread_bps`` equals
     ``time_series[-1].spread_bps`` AND
     ``time_series_spread.rows[-1].value`` bit-for-bit.
 11. Wire-honesty: ``methodology_label`` is sourced from the YAML.
 12. Three import paths still resolve to the same Pydantic class.
 13. Canonical TimeSeries outputs: BPS for the spread, Z_SCORE
     for the rolling z-score, with the documented series_name
     pattern.

Tests are fully offline (DB-backed grounding lives in the SQL-
validation runner — see
``tests/test_breakeven_curve_spread_sql_validation.py``).
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.inflation_indexed_bonds.tools.breakeven_curve_spread import (
    CONFIG_PATH,
    BreakevenCurveSpreadInput,
    calculate_breakeven_curve_spread,
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
# market-data fetch — composing it twice means this lookup runs twice
# (once per endpoint).
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
        # Unknown combinations surface as "no rows" so a typo fails fast.
        return pd.DataFrame(columns=["trade_date", "field_value"])
    return _stub


class _FrozenDate(date):
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


def _build_legs_for_us_2s10s(
    *,
    nominal_short: float = 4.20,
    linker_short: float = 1.85,
    nominal_long: float = 4.40,
    linker_long: float = 2.05,
    drift_pct: float = -0.30,
) -> dict:
    """Default canonical four-leg fixture for UST/USD_TIPS 2s10s.

    The four legs drift by different amounts so the resulting
    breakeven curve spread is NOT constant — necessary for the
    z-score / period-change override tests to actually surface a
    convention-driven difference (constant input → constant
    spread → undefined z-score and zero daily change).
    """
    days = 800
    # Differentiated drifts produce a spread series that varies
    # through time:
    #   short_be drift = (drift_pct) - (drift_pct - 0.20)  =  0.20 pct
    #   long_be drift  = (drift_pct + 0.40) - (drift_pct + 0.10) = 0.30 pct
    #   → spread drifts by 0.10 pct over the window (≈10 bps).
    return {
        ("UST", "2Y", "sovereign_benchmark"): _synthetic_single_leg(
            days=days, base_pct=nominal_short, drift_pct=drift_pct,
        ),
        ("UST", "10Y", "sovereign_benchmark"): _synthetic_single_leg(
            days=days, base_pct=nominal_long, drift_pct=drift_pct + 0.40,
        ),
        ("USD_TIPS", "2Y", "inflation_linker"): _synthetic_single_leg(
            days=days, base_pct=linker_short, drift_pct=drift_pct - 0.20,
        ),
        ("USD_TIPS", "10Y", "inflation_linker"): _synthetic_single_leg(
            days=days, base_pct=linker_long, drift_pct=drift_pct + 0.10,
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
        return calculate_breakeven_curve_spread(
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
        assert cfg.tool.name == "calculate_breakeven_curve_spread_tool"
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
        # Cross-country curve-spread is documented as a planned
        # extension — guards against a future refactor that
        # silently relaxes the same-country invariant.
        assert "ross-country" in joined or "cross_country" in joined

    def test_methodology_what_it_does_carries_disclosure(self):
        cfg = load_tool_config(CONFIG_PATH)
        text = cfg.methodology.what_it_does.lower()
        assert "compensation" in text
        # Spread formula MUST be visible on the wire-disclosure.
        assert "long_breakeven_bps" in text and "short_breakeven_bps" in text


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        legs = _build_legs_for_us_2s10s()
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
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
            "long_tenor",
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
            "short_breakeven_bps",
            "long_breakeven_bps",
            "short_years",
            "long_years",
            "methodology_label",
        ):
            assert k in cm, f"missing {k}"

        assert cm["short_years"] == 2.0
        assert cm["long_years"] == 10.0
        assert cm["short_tenor"] == "2Y"
        assert cm["long_tenor"] == "10Y"
        assert cm["spread_label"] == "UST/USD_TIPS 2s10s breakeven"

    def test_spread_formula_is_correct(self):
        """Hard-pin the spread formula on a synthetic fixture with
        constant endpoint breakevens.  With BE_short=200bps,
        BE_long=300bps, the expected spread = 100bps.
        """
        days = 800
        legs = {
            ("UST", "2Y", "sovereign_benchmark"): _synthetic_single_leg(
                days=days, base_pct=4.0, drift_pct=0.0,
            ),
            ("UST", "10Y", "sovereign_benchmark"): _synthetic_single_leg(
                days=days, base_pct=5.0, drift_pct=0.0,
            ),
            ("USD_TIPS", "2Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=2.0, drift_pct=0.0,
            ),
            ("USD_TIPS", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=2.0, drift_pct=0.0,
            ),
        }
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        # Endpoint breakevens
        assert cm["short_breakeven_bps"] == pytest.approx(200.0, abs=0.05)
        assert cm["long_breakeven_bps"] == pytest.approx(300.0, abs=0.05)
        # spread = long - short = 100bps
        assert cm["current_spread_bps"] == pytest.approx(100.0, abs=0.05)

    def test_explicit_config_matches_auto_loaded(self):
        legs = _build_legs_for_us_2s10s()
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out_auto = _run(params, legs, config=None)
        out_explicit = _run(
            params, legs, config=load_tool_config(CONFIG_PATH),
        )
        assert out_auto == out_explicit

    def test_non_integer_year_fractions_handled(self):
        """Boundary case: 6M/2Y curve.  short_years=0.5, long_years=2.0.
        Verify the validator + display handles non-integer years."""
        days = 800
        legs = {
            ("UST", "6M", "sovereign_benchmark"): _synthetic_single_leg(
                days=days, base_pct=4.0, drift_pct=0.0,
            ),
            ("UST", "2Y", "sovereign_benchmark"): _synthetic_single_leg(
                days=days, base_pct=4.5, drift_pct=0.0,
            ),
            ("USD_TIPS", "6M", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=2.0, drift_pct=0.0,
            ),
            ("USD_TIPS", "2Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=2.0, drift_pct=0.0,
            ),
        }
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="6M",
            long_tenor="2Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["short_years"] == pytest.approx(0.5, abs=1e-4)
        assert cm["long_years"] == 2.0
        # BE_short = (4.0-2.0)*100 = 200 bps; BE_long = (4.5-2.0)*100 = 250 bps
        # spread = 250 - 200 = 50 bps
        assert cm["current_spread_bps"] == pytest.approx(50.0, abs=0.05)


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def test_z_score_window_override_changes_z(self):
        legs = _build_legs_for_us_2s10s()
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
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
        legs = _build_legs_for_us_2s10s()
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
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
        legs = _build_legs_for_us_2s10s()
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
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
        legs = _build_legs_for_us_2s10s(
            nominal_short=4.234567, linker_short=1.876543,
            nominal_long=4.421357, linker_long=2.142857,
        )
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
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
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            calculate_breakeven_curve_spread(
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
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
        )
        assert params.field_name is None

    def test_curve_families_must_differ(self):
        with pytest.raises(Exception):
            BreakevenCurveSpreadInput(
                nominal_curve_family="USD_TIPS",
                linker_curve_family="USD_TIPS",
                short_tenor="2Y",
                long_tenor="10Y",
            )

    def test_tenors_must_differ(self):
        with pytest.raises(Exception):
            BreakevenCurveSpreadInput(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                short_tenor="2Y",
                long_tenor="2Y",
            )

    def test_short_must_precede_long_tenor(self):
        """Inverted tenor pair MUST be rejected at the schema layer
        with a precise error pointing at the year-fraction comparison."""
        with pytest.raises(Exception) as exc_info:
            BreakevenCurveSpreadInput(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                short_tenor="10Y",
                long_tenor="2Y",
            )
        msg = str(exc_info.value).lower()
        assert "strictly larger" in msg or "must map" in msg

    def test_extra_fields_rejected(self):
        """``extra='forbid'`` ensures unknown kwargs surface as a
        ValidationError rather than being silently ignored — protects
        the wire from typo-pollution."""
        with pytest.raises(Exception):
            BreakevenCurveSpreadInput(
                nominal_curve_family="UST",
                linker_curve_family="USD_TIPS",
                short_tenor="2Y",
                long_tenor="10Y",
                # Surprise field — extra='forbid' should reject this.
                bogus_param=123,
            )


# ===========================================================================
# 6. field_name → YAML default fall-through
# ===========================================================================

class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_field_name reaches all FOUR
    inner fetches."""

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
            calculate_breakeven_curve_spread(
                engine=None, params=params, config=config,
            )
        return captured

    def test_omitted_field_name_uses_yaml_default(self):
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_field_name="YLD_YTM_MID"),
            _build_legs_for_us_2s10s(),
        )
        # Two endpoints × two legs each = 4 fetches.
        assert len(captured) == 4
        for call in captured:
            assert call["field_name"] == "YLD_YTM_MID"

    def test_yaml_override_changes_resolved_field(self):
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_field_name="YLD_YTM_BID"),
            _build_legs_for_us_2s10s(),
        )
        for call in captured:
            assert call["field_name"] == "YLD_YTM_BID"

    def test_explicit_field_name_overrides_yaml(self):
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
            field_name="YLD_YTM_ASK",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_field_name="YLD_YTM_MID"),
            _build_legs_for_us_2s10s(),
        )
        for call in captured:
            assert call["field_name"] == "YLD_YTM_ASK"


# ===========================================================================
# 7. Composition guard inheritance
# ===========================================================================

class TestCompositionGuards:
    """Curve-spread composes the spot primitive twice; the spot's
    no-proxy guard (instrument_type filter on each leg) AND
    same-country invariant must fire on each endpoint and surface
    as a controlled error envelope when violated.
    """

    def test_missing_short_endpoint_returns_error_envelope(self):
        legs = _build_legs_for_us_2s10s()
        legs[("USD_TIPS", "2Y", "inflation_linker")] = pd.DataFrame(
            columns=["trade_date", "field_value"],
        )
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "short-tenor" in out["error"].lower()
        assert "2Y" in out["error"]
        # Inner error context is propagated.
        assert "inflation_linker" in out["error"]

    def test_missing_long_endpoint_returns_error_envelope(self):
        legs = _build_legs_for_us_2s10s()
        legs[("UST", "10Y", "sovereign_benchmark")] = pd.DataFrame(
            columns=["trade_date", "field_value"],
        )
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "long-tenor" in out["error"].lower()
        assert "10Y" in out["error"]
        assert "sovereign_benchmark" in out["error"]

    def test_nominal_cf_in_linker_slot_returns_error_envelope(self):
        legs: dict = {}
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="DE_BUND",   # nominal in linker slot
            short_tenor="2Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out

    def test_linker_cf_in_nominal_slot_returns_error_envelope(self):
        legs: dict = {}
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="USD_TIPS",  # linker in nominal slot
            linker_curve_family="GBP_LINKER",
            short_tenor="2Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out

    def test_cross_country_same_currency_pair_refused(self):
        """DE_BUND (Germany/EUR) + EUR_FR_LINKER (France/EUR) — same
        currency, different country.  The same-country invariant
        inherited from the spot primitive must fire BEFORE any
        market-data fetch."""
        legs: dict = {}
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="DE_BUND",
            linker_curve_family="EUR_FR_LINKER",
            short_tenor="2Y",
            long_tenor="10Y",
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
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UK_GILT",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
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

        params = BreakevenCurveSpreadInput(
            nominal_curve_family="DE_BUND",
            linker_curve_family="EUR_FR_LINKER",
            short_tenor="2Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_record_fetch,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ):
            out = calculate_breakeven_curve_spread(
                engine=None, params=params,
            )
        assert "error" in out
        assert fetch_calls == [], (
            "Cross-country pair must be refused BEFORE any market-"
            f"data fetch fires.  Got {len(fetch_calls)} fetch call(s)."
        )


# ===========================================================================
# 8. Per-trade-date alignment
# ===========================================================================

class TestPerTradeDateAlignment:
    """Inner-join discipline: dates where one endpoint has no data
    are dropped from the spread series, NOT carried forward."""

    def test_missing_endpoint_date_is_dropped(self):
        """Drop a single business day from one of the long-tenor legs
        (within the spot primitive's ffill_limit so the spot doesn't
        ffill-bridge it for both legs).  Verify the spread series
        on that date is missing."""
        days = 800
        # Build the canonical fixture, then drop one day from
        # USD_TIPS 10Y AND UST 10Y so the long-tenor breakeven is
        # truly missing on that date (no synthetic ffill from the
        # spot primitive can produce it).
        legs = _build_legs_for_us_2s10s()

        # Pick a trade_date in the middle of the displayed window.
        all_dates = sorted(legs[("UST", "10Y", "sovereign_benchmark")]["trade_date"].tolist())
        mid_date = all_dates[len(all_dates) // 2]
        for key in (
            ("UST", "10Y", "sovereign_benchmark"),
            ("USD_TIPS", "10Y", "inflation_linker"),
        ):
            df = legs[key]
            legs[key] = df[df["trade_date"] != mid_date].reset_index(drop=True)

        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")

        # Sanity: bespoke time_series should not contain the
        # missing date AT ALL — strict inner join discipline.
        bespoke_dates = {row["date"] for row in out["time_series"]}
        assert mid_date.strftime("%Y-%m-%d") not in bespoke_dates


# ===========================================================================
# 9. Boundary-rounding parity
# ===========================================================================

class TestBoundaryRoundingParity:
    def test_snapshot_bps_matches_bespoke_last_row_strictly(self):
        legs = _build_legs_for_us_2s10s()
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        snap = out["current_metrics"]["current_spread_bps"]
        bespoke_last = out["time_series"][-1]["spread_bps"]
        assert snap == bespoke_last

    def test_snapshot_bps_matches_canonical_last_row_strictly(self):
        legs = _build_legs_for_us_2s10s()
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        snap = out["current_metrics"]["current_spread_bps"]
        canon_last = out["time_series_spread"]["rows"][-1]["value"]
        assert snap == canon_last

    def test_bespoke_z_score_matches_canonical_z_score_per_row(self):
        legs = _build_legs_for_us_2s10s()
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
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
        legs = _build_legs_for_us_2s10s()
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        custom_text = (
            "CUSTOM-CURVE-SPREAD-DISCLOSURE-99 inflation compensation "
            "term structure long_breakeven_bps - short_breakeven_bps"
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
        legs = _build_legs_for_us_2s10s()
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs, config=None)
        label = out["current_metrics"]["methodology_label"].lower()
        assert "compensation" in label
        # Spread formula MUST be visible on the wire.
        assert "long_breakeven_bps" in label
        assert "short_breakeven_bps" in label


# ===========================================================================
# 11. Import path stability
# ===========================================================================

class TestImportPathStability:
    def test_function_via_package_init(self):
        from rates_agent.inflation_indexed_bonds.tools.breakeven_curve_spread import (
            calculate_breakeven_curve_spread as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.breakeven_curve_spread.compute import (
            calculate_breakeven_curve_spread as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.inflation_indexed_bonds.tools.breakeven_curve_spread import (
            BreakevenCurveSpreadInput as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.breakeven_curve_spread.schemas import (
            BreakevenCurveSpreadInput as via_schemas,
        )
        from rates_agent.inflation_indexed_bonds.tools.schemas import (
            BreakevenCurveSpreadInput as via_hub,
        )
        assert via_package is via_schemas is via_hub


# ===========================================================================
# 12. Canonical TimeSeries outputs
# ===========================================================================

class TestCanonicalTimeSeries:
    def test_canonical_spread_uses_bps_units_and_naming(self):
        legs = _build_legs_for_us_2s10s()
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        ts = out["time_series_spread"]
        assert ts["units"] == "bps"
        assert ts["series_name"] == "ust_usd_tips_2y_10y_breakeven_spread"

    def test_canonical_zscore_uses_z_score_units_and_naming(self):
        legs = _build_legs_for_us_2s10s()
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        ts = out["time_series_zscore"]
        assert ts["units"] == "z_score"
        assert (
            ts["series_name"]
            == "ust_usd_tips_2y_10y_breakeven_spread_zscore"
        )

    def test_canonical_lengths_match_bespoke(self):
        legs = _build_legs_for_us_2s10s()
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
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
        legs = _build_legs_for_us_2s10s()
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        TimeSeries.model_validate(out["time_series_spread"])
        TimeSeries.model_validate(out["time_series_zscore"])

    def test_canonical_dates_chronological(self):
        legs = _build_legs_for_us_2s10s()
        params = BreakevenCurveSpreadInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            short_tenor="2Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        dates = [r["date"] for r in out["time_series_spread"]["rows"]]
        assert dates == sorted(dates)
