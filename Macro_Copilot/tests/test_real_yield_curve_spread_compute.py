"""
test_real_yield_curve_spread_compute.py — Unit tests for the
linker real_yield_curve_spread primitive.

Mirrors ``test_breakeven_curve_spread_compute.py`` (the closest
sibling composition pattern in this domain) and
``test_inflation_swap_curve_spread_compute.py`` (the closest
same-curve-family compose pattern).  Differs in the math under
test: per-trade-date difference of two real-yield levels,
reported in PERCENT (NOT bps) — distinct from the BPS convention
sibling curve-spread primitives use, because real yields are not
multiplied by 100.

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
     compute() resolves the sentinel against the YAML.  Tenor
     validators reject structurally invalid pairs.
  7. Composition guard inheritance: non-linker curve_family
     (e.g. 'UST') is refused at compute time by the post-fetch
     instrument_master guard BEFORE any inner level fetch fires.
  8. Per-trade-date alignment: dates where one endpoint has no
     data are dropped from the spread series.
  9. Spread formula correctness on synthetic input.
 10. Boundary-rounding parity:
     ``current_metrics.current_spread_pct`` equals
     ``time_series[-1].spread_pct`` AND
     ``time_series_spread.rows[-1].value`` bit-for-bit.
 11. Wire-honesty: ``methodology_label`` is sourced from the YAML.
 12. Three import paths still resolve to the same Pydantic class.
 13. Canonical TimeSeries outputs: PERCENT for the spread,
     Z_SCORE for the rolling z-score, with the documented
     series_name pattern.

Tests are fully offline (DB-backed grounding lives in the SQL-
validation runner — see
``tests/test_real_yield_curve_spread_sql_validation.py``).
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread import (
    CONFIG_PATH,
    RealYieldCurveSpreadInput,
    calculate_real_yield_curve_spread,
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
# ``_fetch_curve_family_country_currency`` to enforce the same-curve-
# family identity guard before either inner level call fires.
_DEFAULT_COUNTRY_CURRENCY = {
    ("USD_TIPS",      "inflation_linker"): ("US", "USD"),
    ("GBP_LINKER",    "inflation_linker"): ("UK", "GBP"),
    ("EUR_FR_LINKER", "inflation_linker"): ("France", "EUR"),
    ("CAD_RRB",       "inflation_linker"): ("Canada", "CAD"),
    # Nominal-sovereign curve families intentionally absent under
    # the inflation_linker key so the guard refuses them.
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
        "rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread.compute._fetch_curve_family_country_currency",
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


def _build_legs_for_usd_tips_5s10s(
    *,
    short_real: float = 1.85,
    long_real: float = 2.05,
    short_drift: float = -0.30,
    long_drift: float = -0.10,
) -> dict:
    """Default canonical two-leg fixture for USD_TIPS 5s10s
    real-yield.  Differentiated drifts produce a spread series
    that varies through time — necessary for the z-score / period-
    change override tests to surface a convention-driven
    difference.
    """
    days = 800
    return {
        ("USD_TIPS", "5Y", "inflation_linker"): _synthetic_single_leg(
            days=days, base_pct=short_real, drift_pct=short_drift,
        ),
        ("USD_TIPS", "10Y", "inflation_linker"): _synthetic_single_leg(
            days=days, base_pct=long_real, drift_pct=long_drift,
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
        return calculate_real_yield_curve_spread(
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
        assert cfg.tool.name == "calculate_real_yield_curve_spread_tool"
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
        assert "long_real_yield_pct" in text
        assert "short_real_yield_pct" in text
        # Concept framing — distinct from breakeven curve spread.
        assert "real" in text and "yield" in text


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        legs = _build_legs_for_usd_tips_5s10s()
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        for k in (
            "as_of_date",
            "curve_family",
            "short_tenor",
            "long_tenor",
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
            "short_real_yield_pct",
            "long_real_yield_pct",
            "short_years",
            "long_years",
            "observation_count",
            "country",
            "currency",
            "methodology_label",
        ):
            assert k in cm, f"missing {k}"

        assert cm["short_years"] == 5.0
        assert cm["long_years"] == 10.0
        assert cm["short_tenor"] == "5Y"
        assert cm["long_tenor"] == "10Y"
        assert cm["spread_label"] == "USD_TIPS 5s10s real-yield"
        assert cm["country"] == "US"
        assert cm["currency"] == "USD"

    def test_spread_formula_is_correct(self):
        """Hard-pin the spread formula on a synthetic fixture with
        constant endpoint real yields.  With short=1.50%, long=2.20%,
        the expected spread = 0.70 pct.
        """
        days = 800
        legs = {
            ("USD_TIPS", "5Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=1.50, drift_pct=0.0,
            ),
            ("USD_TIPS", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=2.20, drift_pct=0.0,
            ),
        }
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["short_real_yield_pct"] == pytest.approx(1.50, abs=1e-4)
        assert cm["long_real_yield_pct"] == pytest.approx(2.20, abs=1e-4)
        # spread = long - short = 0.70 pct
        assert cm["current_spread_pct"] == pytest.approx(0.70, abs=1e-4)

    def test_negative_spread_handled(self):
        """Real yields can be negative across parts of post-2008 /
        2020-21 history and curve spread can be negative too (long
        below short).  The wire types must tolerate negatives."""
        days = 800
        legs = {
            ("USD_TIPS", "5Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=0.80, drift_pct=0.0,
            ),
            ("USD_TIPS", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=0.30, drift_pct=0.0,
            ),
        }
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")
        # spread = long - short = -0.50 pct
        assert out["current_metrics"]["current_spread_pct"] < 0

    def test_explicit_config_matches_auto_loaded(self):
        legs = _build_legs_for_usd_tips_5s10s()
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
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
        legs = _build_legs_for_usd_tips_5s10s()
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
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
        legs = _build_legs_for_usd_tips_5s10s()
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
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
        legs = _build_legs_for_usd_tips_5s10s()
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
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

    def test_yield_round_decimals_override_changes_precision(self):
        legs = _build_legs_for_usd_tips_5s10s(
            short_real=1.8765432, long_real=2.0876543,
        )
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
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
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            calculate_real_yield_curve_spread(
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
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
        )
        assert params.field_name is None

    def test_tenors_must_differ(self):
        with pytest.raises(Exception):
            RealYieldCurveSpreadInput(
                curve_family="USD_TIPS",
                short_tenor="5Y",
                long_tenor="5Y",
            )

    def test_short_must_precede_long_tenor(self):
        """Inverted tenor pair MUST be rejected at the schema layer
        with a precise error pointing at the year-fraction
        comparison."""
        with pytest.raises(Exception) as exc_info:
            RealYieldCurveSpreadInput(
                curve_family="USD_TIPS",
                short_tenor="10Y",
                long_tenor="5Y",
            )
        msg = str(exc_info.value).lower()
        assert "strictly larger" in msg or "must map" in msg

    def test_extra_fields_rejected(self):
        """``extra='forbid'`` ensures unknown kwargs surface as a
        ValidationError rather than being silently ignored — protects
        the wire from typo-pollution and from cross-curve-attempt
        encoding via input-shape variations."""
        with pytest.raises(Exception):
            RealYieldCurveSpreadInput(
                curve_family="USD_TIPS",
                short_tenor="5Y",
                long_tenor="10Y",
                # Surprise field — extra='forbid' should reject this.
                second_curve_family="GBP_LINKER",
            )

    def test_missing_curve_family_rejected(self):
        """The single-curve discriminator is load-bearing — refusing
        construction without it defends the same-curve invariant."""
        with pytest.raises(Exception):
            RealYieldCurveSpreadInput(
                short_tenor="5Y",
                long_tenor="10Y",
            )


# ===========================================================================
# 6. field_name → YAML default fall-through
# ===========================================================================

class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_field_name reaches BOTH
    inner level calls."""

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
            calculate_real_yield_curve_spread(
                engine=None, params=params, config=config,
            )
        return captured

    def test_omitted_field_name_uses_yaml_default(self):
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_field_name="YLD_YTM_MID"),
            _build_legs_for_usd_tips_5s10s(),
        )
        # Two endpoints = 2 fetches.
        assert len(captured) == 2
        for call in captured:
            assert call["field_name"] == "YLD_YTM_MID"

    def test_yaml_override_changes_resolved_field(self):
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_field_name="YLD_YTM_BID"),
            _build_legs_for_usd_tips_5s10s(),
        )
        for call in captured:
            assert call["field_name"] == "YLD_YTM_BID"

    def test_explicit_field_name_overrides_yaml(self):
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            field_name="YLD_YTM_ASK",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(default_field_name="YLD_YTM_MID"),
            _build_legs_for_usd_tips_5s10s(),
        )
        for call in captured:
            assert call["field_name"] == "YLD_YTM_ASK"

    def test_instrument_type_filter_applied_on_every_fetch(self):
        """The level primitive's no-proxy guard is inherited
        transitively: every inner fetch is constrained to
        ``instrument_type='inflation_linker'``."""
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
        )
        captured = self._capture_fetch_calls(
            params,
            _build_config(),
            _build_legs_for_usd_tips_5s10s(),
        )
        assert len(captured) == 2
        for call in captured:
            assert call["instrument_type"] == "inflation_linker"


# ===========================================================================
# 7. Composition guard inheritance
# ===========================================================================

class TestCompositionGuards:
    """The post-fetch instrument_master identity guard must fire
    BEFORE any inner level fetch when the curve_family is non-
    linker or unknown."""

    def test_non_linker_curve_family_returns_error_envelope(self):
        legs: dict = {}
        params = RealYieldCurveSpreadInput(
            curve_family="UST",  # nominal sovereign — not in linker table
            short_tenor="2Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        # Error envelope must mention the curve_family AND the
        # nominal-vs-linker routing hint.
        err = out["error"]
        assert "UST" in err
        assert "inflation_linker" in err

    def test_identity_guard_runs_before_any_fetch(self):
        """A non-linker curve_family must be refused BEFORE any
        market-data SELECT (verified by recording fetch calls)."""
        fetch_calls = []

        def _record_fetch(**kwargs):
            fetch_calls.append(kwargs)
            return pd.DataFrame(columns=["trade_date", "field_value"])

        params = RealYieldCurveSpreadInput(
            curve_family="UST",
            short_tenor="2Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.fetch_single_tenor",
            new=_record_fetch,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.date",
            _FrozenDate,
        ):
            out = calculate_real_yield_curve_spread(
                engine=None, params=params,
            )
        assert "error" in out
        assert fetch_calls == [], (
            "Non-linker curve_family must be refused BEFORE any "
            f"market-data fetch fires.  Got {len(fetch_calls)} "
            "fetch call(s)."
        )

    def test_missing_short_endpoint_returns_error_envelope(self):
        legs = _build_legs_for_usd_tips_5s10s()
        # Empty out the short-tenor leg so the inner level call fails.
        legs[("USD_TIPS", "5Y", "inflation_linker")] = pd.DataFrame(
            columns=["trade_date", "field_value"],
        )
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        err = out["error"].lower()
        assert "short-tenor" in err or "short_tenor" in err
        assert "5y" in err

    def test_missing_long_endpoint_returns_error_envelope(self):
        legs = _build_legs_for_usd_tips_5s10s()
        legs[("USD_TIPS", "10Y", "inflation_linker")] = pd.DataFrame(
            columns=["trade_date", "field_value"],
        )
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        err = out["error"].lower()
        assert "long-tenor" in err or "long_tenor" in err
        assert "10y" in err

    def test_identity_guard_surfaces_ambiguous_metadata(self):
        """If instrument_master returned multiple distinct
        (country, currency) tuples for a curve_family, the
        helper would surface a controlled error rather than
        silently picking one."""
        ambiguous_table = dict(_DEFAULT_COUNTRY_CURRENCY)
        ambiguous_table[("USD_TIPS", "inflation_linker")] = (
            "Ambiguous instrument_master metadata for "
            "curve_family='USD_TIPS', "
            "instrument_type='inflation_linker': "
            "multiple distinct (country, currency) rows observed: "
            "[('US', 'USD'), ('PR', 'USD')].  This tool's "
            "same-country / same-curve-family invariant requires a "
            "single (country, currency) identity per (curve_family, "
            "instrument_type) pair and refuses to pick one silently."
        )
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        legs = _build_legs_for_usd_tips_5s10s()
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread.compute._fetch_curve_family_country_currency",
            new=_stub_country_currency_factory(ambiguous_table),
        ):
            out = _run(params, legs)
        assert "error" in out
        assert "current_metrics" not in out
        assert "Ambiguous" in out["error"] or "ambiguous" in out["error"].lower()


# ===========================================================================
# 8. Per-trade-date alignment
# ===========================================================================

class TestPerTradeDateAlignment:
    """Inner-join discipline: dates where one endpoint has no data
    are dropped from the spread series, NOT carried forward."""

    def test_missing_endpoint_date_is_dropped(self):
        """Drop a single business day from the long-tenor leg far
        beyond the ffill_limit of the inner level primitive (drop
        a *batch* so the gap survives ffill).  Verify that an
        early portion of the spread series only contains dates
        present in BOTH endpoints."""
        days = 800
        legs = _build_legs_for_usd_tips_5s10s()

        # Drop ALL dates from the long-tenor leg before a cutoff so
        # those dates can't be aligned; the inner level primitive's
        # 5-day ffill cannot bridge a multi-month gap, so the spread
        # series for those dates is genuinely missing.
        long_df = legs[("USD_TIPS", "10Y", "inflation_linker")]
        cutoff_date = long_df["trade_date"].iloc[100]
        legs[("USD_TIPS", "10Y", "inflation_linker")] = long_df[
            long_df["trade_date"] >= cutoff_date
        ].reset_index(drop=True)

        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")

        # The bespoke time_series must not contain any date strictly
        # before the long-tenor leg's first observation.
        ts_dates = sorted(row["date"] for row in out["time_series"])
        first_long_date = cutoff_date.strftime("%Y-%m-%d")
        for d in ts_dates:
            assert d >= first_long_date, (
                f"Spread series contains date {d!r} that predates "
                f"the long-tenor leg's first observation "
                f"({first_long_date!r}) — inner-join discipline broke."
            )


# ===========================================================================
# 9. Boundary-rounding parity
# ===========================================================================

class TestBoundaryRoundingParity:
    def test_snapshot_pct_matches_bespoke_last_row_strictly(self):
        legs = _build_legs_for_usd_tips_5s10s()
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        snap = out["current_metrics"]["current_spread_pct"]
        bespoke_last = out["time_series"][-1]["spread_pct"]
        assert snap == bespoke_last

    def test_snapshot_pct_matches_canonical_last_row_strictly(self):
        legs = _build_legs_for_usd_tips_5s10s()
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        snap = out["current_metrics"]["current_spread_pct"]
        canon_last = out["time_series_spread"]["rows"][-1]["value"]
        assert snap == canon_last

    def test_bespoke_z_score_matches_canonical_z_score_per_row(self):
        legs = _build_legs_for_usd_tips_5s10s()
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
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
        legs = _build_legs_for_usd_tips_5s10s()
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        custom_text = (
            "CUSTOM-REAL-YIELD-CURVE-DISCLOSURE-77 long_real_yield_pct "
            "- short_real_yield_pct real-yield curve-shape object"
        )
        out = _run(
            params, legs,
            config=_build_config(methodology_what_it_does=custom_text),
        )
        assert (
            out["current_metrics"]["methodology_label"]
            == custom_text.strip()
        )

    def test_bundled_methodology_label_carries_formula(self):
        legs = _build_legs_for_usd_tips_5s10s()
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs, config=None)
        label = out["current_metrics"]["methodology_label"].lower()
        # Spread formula MUST be visible on the wire.
        assert "long_real_yield_pct" in label
        assert "short_real_yield_pct" in label


# ===========================================================================
# 11. Import path stability
# ===========================================================================

class TestImportPathStability:
    def test_function_via_package_init(self):
        from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread import (
            calculate_real_yield_curve_spread as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread.compute import (
            calculate_real_yield_curve_spread as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread import (
            RealYieldCurveSpreadInput as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread.schemas import (
            RealYieldCurveSpreadInput as via_schemas,
        )
        from rates_agent.inflation_indexed_bonds.tools.schemas import (
            RealYieldCurveSpreadInput as via_hub,
        )
        assert via_package is via_schemas is via_hub


# ===========================================================================
# 12. Canonical TimeSeries outputs
# ===========================================================================

class TestCanonicalTimeSeries:
    def test_canonical_spread_uses_percent_units_and_naming(self):
        legs = _build_legs_for_usd_tips_5s10s()
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        ts = out["time_series_spread"]
        # PERCENT — distinct from the BPS convention sibling
        # curve-spread primitives use.
        assert ts["units"] == "percent"
        assert (
            ts["series_name"]
            == "usd_tips_5y_10y_real_yield_curve_spread"
        )

    def test_canonical_zscore_uses_z_score_units_and_naming(self):
        legs = _build_legs_for_usd_tips_5s10s()
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        ts = out["time_series_zscore"]
        assert ts["units"] == "z_score"
        assert (
            ts["series_name"]
            == "usd_tips_5y_10y_real_yield_curve_spread_zscore"
        )

    def test_canonical_lengths_match_bespoke(self):
        legs = _build_legs_for_usd_tips_5s10s()
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
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
        legs = _build_legs_for_usd_tips_5s10s()
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        TimeSeries.model_validate(out["time_series_spread"])
        TimeSeries.model_validate(out["time_series_zscore"])

    def test_canonical_dates_chronological(self):
        legs = _build_legs_for_usd_tips_5s10s()
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        dates = [r["date"] for r in out["time_series_spread"]["rows"]]
        assert dates == sorted(dates)


# ===========================================================================
# 13. ffill semantics: gap > ffill_limit_days surfaces as dropped row
# ===========================================================================

class TestFfillSemantics:
    """Inner level primitive applies an ffill within ffill_limit_days;
    a synthetic gap exceeding that limit must surface as a dropped
    row on the spread series (no synthetic carry-forward).
    """

    def test_long_gap_drops_spread_rows(self):
        days = 800
        legs = _build_legs_for_usd_tips_5s10s()

        # Carve out a ~20-business-day hole in the long-tenor leg
        # (well beyond the 5-day ffill_limit) so the inner level
        # primitive cannot bridge it.
        long_df = legs[("USD_TIPS", "10Y", "inflation_linker")]
        gap_start_idx = 300
        gap_end_idx = 320
        gap_dates = long_df["trade_date"].iloc[gap_start_idx:gap_end_idx].tolist()
        legs[("USD_TIPS", "10Y", "inflation_linker")] = long_df[
            ~long_df["trade_date"].isin(gap_dates)
        ].reset_index(drop=True)

        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        assert "error" not in out, out.get("error")

        ts_dates = {row["date"] for row in out["time_series"]}
        # The carved-out dates that fell inside the displayed
        # lookback should be absent from the spread series.
        observable_window_min = (
            date(2026, 4, 30) - timedelta(days=365)
        )
        missing_dates_in_window = [
            d for d in gap_dates
            if d >= observable_window_min
        ]
        # If any gap dates fell into the displayed window, at least
        # one must be absent from the bespoke spread series.
        for d in missing_dates_in_window[5:]:  # skip first 5 (ffill bridges)
            assert d.strftime("%Y-%m-%d") not in ts_dates, (
                f"Long-gap date {d!r} survived in the spread series — "
                "inner-join discipline must have broken."
            )


# ===========================================================================
# 14. Z-score basics on deterministic synthetic series
# ===========================================================================

class TestZScoreBasics:
    def test_z_score_finite_after_warmup(self):
        """With min_periods=60, the z-score must be a finite number
        after the rolling window has warmed up."""
        legs = _build_legs_for_usd_tips_5s10s()
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        z = out["current_metrics"]["current_z_score"]
        assert z is not None
        assert isinstance(z, float)
        assert not (z != z)  # NaN check

    def test_z_score_none_when_spread_constant(self):
        """A constant spread has zero variance → z-score must be
        None (no synthetic 0.0 sneaking through)."""
        days = 800
        legs = {
            ("USD_TIPS", "5Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=1.50, drift_pct=0.0,
            ),
            ("USD_TIPS", "10Y", "inflation_linker"): _synthetic_single_leg(
                days=days, base_pct=2.00, drift_pct=0.0,
            ),
        }
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS",
            short_tenor="5Y",
            long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, legs)
        # spread = 0.50 every day → z-score undefined (None).
        assert out["current_metrics"]["current_z_score"] is None


# ===========================================================================
# Pydantic Input overrides — Phase-1 methodology-exposure surface
# ===========================================================================
#
# Pins the Phase-1 exposure decisions recorded in
# rates_agent/inflation_indexed_bonds/tools/real_yield_curve_spread/config.yaml
# (per docs_revamped/03_standards/methodology_exposure.md).  Three
# rolling-z-score conventions are now exposed as Pydantic Input fields
# with per-call overrides; the None sentinel falls through to the YAML
# default.  The overrides apply to the SPREAD's own rolling z-score (the
# inner endpoint level calls use the YAML default, their z-score not
# consumed).  Mirrors the sibling real_yield_level TestInputOverrides.

class TestInputOverrides:
    """Per-call Input overrides for the three Phase-1 exposed conventions
    (``z_score_window_days``, ``z_score_min_periods``, ``z_score_ddof``).
    """

    # ------------------------------------------------------------------
    # (a) Override path — explicit Input changes the output.
    # ------------------------------------------------------------------

    def test_z_window_input_override_changes_z(self):
        legs = _build_legs_for_usd_tips_5s10s()
        base = dict(
            curve_family="USD_TIPS", short_tenor="5Y", long_tenor="10Y",
            lookback_days=365,
        )
        out_default = _run(
            RealYieldCurveSpreadInput(**base), legs, config=_build_config(),
        )
        out_override = _run(
            RealYieldCurveSpreadInput(**base, z_score_window_days=120),
            legs, config=_build_config(),
        )
        assert (
            out_default["current_metrics"]["current_z_score"]
            != out_override["current_metrics"]["current_z_score"]
        )

    def test_z_ddof_input_override_changes_z(self):
        legs = _build_legs_for_usd_tips_5s10s()
        base = dict(
            curve_family="USD_TIPS", short_tenor="5Y", long_tenor="10Y",
            lookback_days=365,
        )
        out_sample = _run(
            RealYieldCurveSpreadInput(**base, z_score_ddof=1),
            legs, config=_build_config(),
        )
        out_pop = _run(
            RealYieldCurveSpreadInput(**base, z_score_ddof=0),
            legs, config=_build_config(),
        )
        assert (
            out_sample["current_metrics"]["current_z_score"]
            != out_pop["current_metrics"]["current_z_score"]
        )

    # ------------------------------------------------------------------
    # (b) Sentinel fallback — None Input → YAML default.
    # ------------------------------------------------------------------

    def test_none_input_falls_through_to_yaml(self):
        legs = _build_legs_for_usd_tips_5s10s()
        base = dict(
            curve_family="USD_TIPS", short_tenor="5Y", long_tenor="10Y",
            lookback_days=365,
        )
        out_omit = _run(
            RealYieldCurveSpreadInput(**base), legs, config=_build_config(),
        )
        out_explicit_none = _run(
            RealYieldCurveSpreadInput(
                **base, z_score_window_days=None,
                z_score_min_periods=None, z_score_ddof=None,
            ),
            legs, config=_build_config(),
        )
        assert out_omit == out_explicit_none

    def test_omitted_z_window_resolves_to_yaml_default(self):
        from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread.compute import (
            _conventions_from_config,
        )
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS", short_tenor="5Y", long_tenor="10Y",
        )
        cfg = load_tool_config(CONFIG_PATH)
        kw = _conventions_from_config(cfg, params)
        assert kw["z_window"] == 252
        assert kw["z_min_periods"] == 60
        assert kw["z_ddof"] == 1

    # ------------------------------------------------------------------
    # (c) Precedence — Input wins over YAML.
    # ------------------------------------------------------------------

    def test_input_wins_over_yaml(self):
        from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread.compute import (
            _conventions_from_config,
        )
        cfg = _build_config(z_score_window_days=999)
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS", short_tenor="5Y", long_tenor="10Y",
            z_score_window_days=120,
        )
        kw = _conventions_from_config(cfg, params)
        assert kw["z_window"] == 120

    def test_yaml_wins_when_input_is_none(self):
        from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread.compute import (
            _conventions_from_config,
        )
        cfg = _build_config(z_score_window_days=180)
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS", short_tenor="5Y", long_tenor="10Y",
            z_score_window_days=None,
        )
        kw = _conventions_from_config(cfg, params)
        assert kw["z_window"] == 180

    # ------------------------------------------------------------------
    # (d) Pydantic constraint enforcement (ge/le bounds).
    # ------------------------------------------------------------------

    def test_z_window_bounds(self):
        for bad in (30, 2000):
            with pytest.raises(Exception):
                RealYieldCurveSpreadInput(
                    curve_family="USD_TIPS", short_tenor="5Y", long_tenor="10Y",
                    z_score_window_days=bad,
                )

    def test_z_min_periods_bounds(self):
        for bad in (10, 400):
            with pytest.raises(Exception):
                RealYieldCurveSpreadInput(
                    curve_family="USD_TIPS", short_tenor="5Y", long_tenor="10Y",
                    z_score_min_periods=bad,
                )

    def test_z_ddof_bounds(self):
        for bad in (-1, 2):
            with pytest.raises(Exception):
                RealYieldCurveSpreadInput(
                    curve_family="USD_TIPS", short_tenor="5Y", long_tenor="10Y",
                    z_score_ddof=bad,
                )

    def test_schema_defaults_all_three_exposures_to_none(self):
        params = RealYieldCurveSpreadInput(
            curve_family="USD_TIPS", short_tenor="5Y", long_tenor="10Y",
        )
        assert params.z_score_window_days is None
        assert params.z_score_min_periods is None
        assert params.z_score_ddof is None


# ===========================================================================
# Exposure-block contract — pin the per-convention exposure decisions
# ===========================================================================

class TestExposureBlockContract:
    EXPECTED_EXPOSED: set[str] = {
        "z_score_window_days",
        "z_score_min_periods",
        "z_score_ddof",
        "default_field_name",
    }

    def test_yaml_exposure_blocks_match_expected_set(self):
        cfg = load_tool_config(CONFIG_PATH)
        exposed_yaml = {
            name for name, conv in cfg.conventions.items()
            if conv.exposure is not None and conv.exposure.expose
        }
        assert exposed_yaml == self.EXPECTED_EXPOSED, (
            f"YAML exposure set drift.  Expected {sorted(self.EXPECTED_EXPOSED)}; "
            f"saw {sorted(exposed_yaml)}."
        )

    def test_every_convention_has_an_exposure_block(self):
        cfg = load_tool_config(CONFIG_PATH)
        missing = [
            name for name, conv in cfg.conventions.items()
            if conv.exposure is None
        ]
        assert not missing, f"Conventions missing exposure: block: {missing}."

    def test_every_exposure_decision_has_a_rationale(self):
        cfg = load_tool_config(CONFIG_PATH)
        empty = [
            name for name, conv in cfg.conventions.items()
            if conv.exposure is not None and not conv.exposure.rationale.strip()
        ]
        assert not empty, f"Conventions with empty exposure rationale: {empty}"

    def test_expose_true_conventions_have_propagation_fields(self):
        cfg = load_tool_config(CONFIG_PATH)
        for name, conv in cfg.conventions.items():
            if conv.exposure is None or not conv.exposure.expose:
                continue
            for field in (
                "input_field", "pydantic_type",
                "default_source", "promoted_from_yaml_in_pr",
            ):
                assert getattr(conv.exposure, field), (
                    f"{name}.exposure.{field} is empty"
                )

    def test_input_field_names_match_pydantic_class(self):
        cfg = load_tool_config(CONFIG_PATH)
        pydantic_fields = set(RealYieldCurveSpreadInput.model_fields.keys())
        for name, conv in cfg.conventions.items():
            if conv.exposure is None or not conv.exposure.expose:
                continue
            assert conv.exposure.input_field in pydantic_fields, (
                f"YAML says {name!r} is exposed via Input field "
                f"{conv.exposure.input_field!r}, not on "
                f"RealYieldCurveSpreadInput"
            )
