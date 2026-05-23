"""
test_policy_futures_futures_calendar_spread_compute.py — Unit tests
                                                          for the
                                                          policy-futures
                                                          calendar-spread
                                                          monitor

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. compute() runs end-to-end against synthetic input with the
     bundled config and returns a well-formed output.
  3. Convention overrides actually change behaviour (z-window, ddof,
     ffill, daily_change_offset_rows, default_price_field, rounding
     decimals).
  4. The honest placeholder for trailing_range_window_days: setting
     it to anything other than 252 raises NotImplementedError.
  5. Schema-layer behaviour: field_name defaults to None (sentinel),
     compute() resolves the sentinel against the YAML's
     default_price_field. as_of_date defaults to None. Validators
     enforce strip_position_short < strip_position_long.
  6. Inverse-pricing rule is metadata-driven (PR8 / P6): the per-leg
     ``inverse_pricing`` flag on the reference dict — NOT a hardcoded
     list in compute — drives the implied-rate spread. The two legs
     must agree on the flag.
  7. Future-anchor guard: as_of_date beyond the universe max on
     EITHER leg returns the controlled-error envelope.
  8. Per-leg reference metadata flows through to the snapshot.
  9. Methodology disclosure is present, required, and mentions the
     sign convention + regime + conversion rule + lookback window +
     scope-limit caveats.
 10. Sign convention: spread_implied_rate_pct = rate_short -
     rate_long; for inverse-priced strips this equals
     -(raw_price_spread).

Tests are fully offline.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.policy_futures.tools.futures_calendar_spread import (
    CONFIG_PATH,
    FuturesCalendarSpreadInput,
    FuturesCalendarSpreadOutput,
    calculate_futures_calendar_spread,
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


def _synthetic_strip_group_df(
    *,
    short_position: int = 1,
    long_position: int = 2,
    short_drift: float = 1.5,
    long_drift: float = 1.0,
    short_start: float = 96.0,
    long_start: float = 95.5,
    days: int = 400,
    frozen_today: date = date(2026, 4, 30),
) -> pd.DataFrame:
    """Build a two-leg long-format DataFrame matching the shape
    ``fetch_strip_group`` returns. Each leg is a linspace from
    ``start`` to ``start + drift`` over ``days`` business days
    ending at ``frozen_today``. Default starts produce a positive
    implied-rate spread (front rate 4% > back rate 4.5% inverted →
    spread negative — flipped to be a realistic inverted-strip
    extreme; the unit tests assert the SIGN convention separately)."""
    bdays = pd.bdate_range(
        frozen_today - timedelta(days=days * 2), frozen_today,
    )
    bdays = bdays[-days:]
    n = len(bdays)
    short_series = np.linspace(short_start, short_start + short_drift, n)
    long_series = np.linspace(long_start, long_start + long_drift, n)
    short_df = pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "strip_position": short_position,
        "field_value": short_series,
    })
    long_df = pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "strip_position": long_position,
        "field_value": long_series,
    })
    return pd.concat(
        [short_df, long_df], ignore_index=True,
    ).sort_values(["trade_date", "strip_position"]).reset_index(drop=True)


def _synthetic_reference(
    *,
    curve_family: str = "SOFR_FUT",
    contract_code: str = "SFR1",
    strip_position: int = 1,
    inverse_pricing: bool = True,
    underlying_contract_code: str = "SFRM26",
    expiry: date = date(2026, 6, 16),
    security_name: str = "SFRM26 COMB",
    contract_size: float = 2500.0,
    tick_size: float = 0.005,
    tick_value: float = 12.5,
) -> dict:
    return {
        "curve_family": curve_family,
        "contract_code": contract_code,
        "strip_position": strip_position,
        "inverse_pricing": inverse_pricing,
        "underlying_contract_code": underlying_contract_code,
        "expiry_date": expiry,
        "security_name": security_name,
        "contract_size": contract_size,
        "tick_size": tick_size,
        "tick_value": tick_value,
    }


def _reference_side_effect_factory(short_ref: dict, long_ref: dict):
    """Return a ``side_effect`` for ``fetch_strip_position_reference``
    that routes by ``strip_position`` to the appropriate per-leg
    reference dict."""

    def _side_effect(*, engine, curve_family, strip_position, as_of_date):
        if strip_position == short_ref["strip_position"]:
            return short_ref
        if strip_position == long_ref["strip_position"]:
            return long_ref
        return None

    return _side_effect


class _FrozenDate(date):
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


_UNSET = object()


_TARGET = "rates_agent.policy_futures.tools.futures_calendar_spread.compute"


def _run(
    params,
    *,
    price_df=None,
    short_reference=_UNSET,
    long_reference=_UNSET,
    config=None,
    universe_max_date=None,
):
    if price_df is None:
        price_df = _synthetic_strip_group_df(
            short_position=params.strip_position_short,
            long_position=params.strip_position_long,
        )
    short_ref = (
        _synthetic_reference(
            curve_family=params.curve_family,
            contract_code=f"STR{params.strip_position_short}",
            strip_position=params.strip_position_short,
        )
        if short_reference is _UNSET
        else short_reference
    )
    long_ref = (
        _synthetic_reference(
            curve_family=params.curve_family,
            contract_code=f"STR{params.strip_position_long}",
            strip_position=params.strip_position_long,
        )
        if long_reference is _UNSET
        else long_reference
    )
    if short_ref is None or long_ref is None:
        # Allow tests to mock a missing reference for either leg.
        def _side(*, engine, curve_family, strip_position, as_of_date):
            if strip_position == params.strip_position_short:
                return short_ref
            if strip_position == params.strip_position_long:
                return long_ref
            return None
        side = _side
    else:
        side = _reference_side_effect_factory(short_ref, long_ref)
    with patch(
        f"{_TARGET}.fetch_strip_group",
        return_value=price_df,
    ), patch(
        f"{_TARGET}.fetch_strip_position_reference",
        side_effect=side,
    ), patch(
        f"{_TARGET}.fetch_strip_position_max_date",
        return_value=universe_max_date,
    ), patch(
        f"{_TARGET}.date",
        _FrozenDate,
    ):
        return calculate_futures_calendar_spread(
            engine=None, params=params, config=config,
        )


def _custom_config(**overrides) -> ToolConfig:
    defaults = {
        "z_score_window_days": 252,
        "z_score_min_periods": 60,
        "z_score_ddof": 1,
        "z_score_buffer_multiplier": 1.5,
        "daily_change_offset_rows": 2,
        "trailing_range_window_days": 252,
        "ffill_limit_days": 5,
        "default_price_field": "PX_LAST",
        "short_rate_regime_map": (
            "SOFR_FUT=RFR,SONIA_FUT=RFR,EUR_SHORT_RATE_FUT=IBOR"
        ),
        "raw_price_round_decimals": 5,
        "implied_rate_round_decimals": 4,
        "z_score_round_decimals": 4,
        "raw_price_high_low_round_decimals": 5,
        "implied_rate_high_low_round_decimals": 4,
        "percentile_round_decimals": 1,
    }
    defaults.update(overrides)
    return ToolConfig(
        tool=ToolMeta(name="t", domain="d", description="x"),
        methodology=MethodologyMeta(what_it_does="x"),
        conventions={
            k: Convention(value=v, source="test", rationale="test")
            for k, v in defaults.items()
        },
    )


# ===========================================================================
# 1. Bundled config.yaml
# ===========================================================================

class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "policy_futures_get_futures_calendar_spread_tool"
        assert cfg.tool.domain == "policy_futures"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "z_score_window_days",
            "z_score_min_periods",
            "z_score_ddof",
            "z_score_buffer_multiplier",
            "daily_change_offset_rows",
            "trailing_range_window_days",
            "ffill_limit_days",
            "default_price_field",
            "short_rate_regime_map",
            "raw_price_round_decimals",
            "implied_rate_round_decimals",
            "z_score_round_decimals",
            "raw_price_high_low_round_decimals",
            "implied_rate_high_low_round_decimals",
            "percentile_round_decimals",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("z_score_window_days") == 252
        assert cfg.convention_value("z_score_min_periods") == 60
        assert cfg.convention_value("z_score_ddof") == 1
        assert cfg.convention_value("z_score_buffer_multiplier") == 1.5
        assert cfg.convention_value("daily_change_offset_rows") == 2
        assert cfg.convention_value("trailing_range_window_days") == 252
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("default_price_field") == "PX_LAST"
        regime_csv = cfg.convention_value("short_rate_regime_map")
        assert "SOFR_FUT=RFR" in regime_csv
        assert "SONIA_FUT=RFR" in regime_csv
        assert "EUR_SHORT_RATE_FUT=IBOR" in regime_csv
        assert cfg.convention_value("raw_price_round_decimals") == 5
        assert cfg.convention_value("implied_rate_round_decimals") == 4
        assert cfg.convention_value("z_score_round_decimals") == 4
        assert cfg.convention_value("raw_price_high_low_round_decimals") == 5
        assert cfg.convention_value("implied_rate_high_low_round_decimals") == 4
        assert cfg.convention_value("percentile_round_decimals") == 1

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 2
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "trailing_range_window_days" in joined
        assert "CTD" in joined


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1,
            strip_position_long=2,
            lookback_days=365,
        )
        out = _run(params)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        for k in (
            "as_of_date", "curve_family",
            "strip_position_short", "strip_position_long",
            "spread_label",
            "contract_code_short", "contract_code_long",
            "underlying_contract_code_short", "underlying_contract_code_long",
            "security_name_short", "security_name_long",
            "expiry_date_short", "expiry_date_long",
            "inverse_priced", "short_rate_regime",
            "raw_price_spread", "spread_implied_rate_pct",
            "daily_change_raw_price_spread",
            "daily_change_spread_implied_rate_pct",
            "z_score_spread_implied_rate",
            "high_252d_spread_implied_rate_pct",
            "low_252d_spread_implied_rate_pct",
            "mid_252d_spread_implied_rate_pct",
            "percentile_252d",
            "rolling_window_days",
            "observation_count",
        ):
            assert k in cm, f"missing {k}"

        # PR14-frozen field-name discipline — guard against silent
        # renames.
        assert "implied_rate" not in cm
        assert "spread_pct" not in cm
        assert "spread_bps" not in cm  # NOT bps; this is in percent points
        assert "current_spread_bps" not in cm

        assert isinstance(cm["raw_price_spread"], float)
        assert isinstance(cm["spread_implied_rate_pct"], float)
        assert cm["inverse_priced"] is True
        assert cm["short_rate_regime"] == "RFR"
        assert cm["rolling_window_days"] == 252

        # Bespoke time_series shape: list of {date, raw_price_spread,
        # spread_implied_rate_pct}.
        ts = out["time_series"]
        assert isinstance(ts, list)
        assert len(ts) > 0
        assert "date" in ts[0]
        assert "raw_price_spread" in ts[0]
        assert "spread_implied_rate_pct" in ts[0]
        # NOT the canonical TimeSeries shape — should not carry
        # series_name / units / description keys.
        assert "value" not in ts[0]
        assert "units" not in ts[0]

    def test_inverse_pricing_sign_convention_holds(self):
        """For inverse-priced strips: spread_implied_rate_pct ==
        -(raw_price_spread) (modulo per-axis rounding)."""
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1,
            strip_position_long=2,
            lookback_days=365,
        )
        out = _run(params)
        cm = out["current_metrics"]
        # raw_price_spread rounds to 5dp; spread_implied_rate_pct to 4dp.
        # The negation identity holds to 4dp.
        expected_rate = round(-cm["raw_price_spread"], 4)
        assert abs(expected_rate - cm["spread_implied_rate_pct"]) < 5e-5

    def test_direct_pricing_sign_convention(self):
        """Direct-priced strip — implied-rate spread equals raw-price
        spread (modulo rounding)."""
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1,
            strip_position_long=2,
            lookback_days=365,
        )
        out = _run(
            params,
            short_reference=_synthetic_reference(
                contract_code="SFR1",
                strip_position=1,
                inverse_pricing=False,
            ),
            long_reference=_synthetic_reference(
                contract_code="SFR2",
                strip_position=2,
                inverse_pricing=False,
                underlying_contract_code="SFRU26",
                expiry=date(2026, 9, 15),
                security_name="SFRU26 COMB",
            ),
        )
        cm = out["current_metrics"]
        assert cm["inverse_priced"] is False
        # raw_price_spread == spread_implied_rate_pct (just rounded to
        # different precisions; 5dp vs 4dp).
        assert abs(cm["raw_price_spread"] - cm["spread_implied_rate_pct"]) < 1e-3

    def test_explicit_config_matches_auto_loaded(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1,
            strip_position_long=2,
            lookback_days=365,
        )
        out_auto = _run(params, config=None)
        out_explicit = _run(params, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit

    def test_per_leg_reference_metadata_flows_to_snapshot(self):
        params = FuturesCalendarSpreadInput(
            curve_family="EUR_SHORT_RATE_FUT",
            strip_position_short=1,
            strip_position_long=4,
            lookback_days=365,
        )
        short_ref = _synthetic_reference(
            curve_family="EUR_SHORT_RATE_FUT",
            contract_code="ER1",
            strip_position=1,
            underlying_contract_code="ERM26",
            security_name="ERM26 Comdty",
            expiry=date(2026, 6, 15),
        )
        long_ref = _synthetic_reference(
            curve_family="EUR_SHORT_RATE_FUT",
            contract_code="ER4",
            strip_position=4,
            underlying_contract_code="ERH27",
            security_name="ERH27 Comdty",
            expiry=date(2027, 3, 15),
        )
        out = _run(params, short_reference=short_ref, long_reference=long_ref)
        cm = out["current_metrics"]
        assert cm["curve_family"] == "EUR_SHORT_RATE_FUT"
        assert cm["contract_code_short"] == "ER1"
        assert cm["contract_code_long"] == "ER4"
        assert cm["strip_position_short"] == 1
        assert cm["strip_position_long"] == 4
        assert cm["spread_label"] == "ER1-ER4"
        assert cm["underlying_contract_code_short"] == "ERM26"
        assert cm["underlying_contract_code_long"] == "ERH27"
        assert cm["security_name_short"] == "ERM26 Comdty"
        assert cm["security_name_long"] == "ERH27 Comdty"
        assert cm["expiry_date_short"] == "2026-06-15"
        assert cm["expiry_date_long"] == "2027-03-15"
        assert cm["short_rate_regime"] == "IBOR"

    def test_snapshot_equals_time_series_last_row_strictly(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1,
            strip_position_long=2,
            lookback_days=365,
        )
        out = _run(params)
        last_row = out["time_series"][-1]
        cm = out["current_metrics"]
        # Strict equality — both go through the SAME rounding
        # conventions.
        assert last_row["raw_price_spread"] == cm["raw_price_spread"]
        assert (
            last_row["spread_implied_rate_pct"]
            == cm["spread_implied_rate_pct"]
        )

    def test_methodology_disclosure_present_and_required(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1,
            strip_position_long=2,
            lookback_days=365,
        )
        out = _run(params)
        disclosure = out["methodology_disclosure"]
        assert isinstance(disclosure, str)
        assert len(disclosure) > 100
        # Schema must require the field — pop and re-validate.
        from pydantic import ValidationError as _VE
        bad = dict(out)
        bad.pop("methodology_disclosure")
        with pytest.raises(_VE):
            FuturesCalendarSpreadOutput.model_validate(bad)

    def test_methodology_disclosure_mentions_required_caveats(self):
        """P5 / ADR 0011 caveats MUST mention the sign convention, the
        rolling-generic strip-spread scope, the regime label, the
        inverse-pricing rule, the z-score window, AND the CTD-of-OIS
        + meeting-by-meeting scope-limits (catalog guardrail)."""
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1,
            strip_position_long=2,
            lookback_days=365,
        )
        out = _run(params)
        disclosure = out["methodology_disclosure"]
        assert "strip_position_short" in disclosure
        assert "strip_position_long" in disclosure
        assert "short_leg minus long_leg" in disclosure.lower() or (
            "short_leg" in disclosure and "long_leg" in disclosure
        )
        assert "RFR" in disclosure  # regime label for SOFR_FUT
        assert "implied_rate_pct = 100 - raw_price" in disclosure
        assert "252" in disclosure  # z window
        assert "CTD" in disclosure
        assert "meeting-by-meeting" in disclosure
        assert "ADR 0011" in disclosure

    def test_spread_label_uses_contract_codes(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1,
            strip_position_long=4,
            lookback_days=365,
        )
        out = _run(
            params,
            short_reference=_synthetic_reference(
                contract_code="SFR1", strip_position=1,
            ),
            long_reference=_synthetic_reference(
                contract_code="SFR4", strip_position=4,
                underlying_contract_code="SFRH27",
                expiry=date(2027, 3, 17),
                security_name="SFRH27 COMB",
            ),
        )
        cm = out["current_metrics"]
        assert cm["spread_label"] == "SFR1-SFR4"


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def test_z_score_window_override_changes_z(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=2,
            lookback_days=365,
        )
        out_default = _run(params, config=_custom_config())
        out_short = _run(
            params, config=_custom_config(z_score_window_days=120),
        )
        assert (
            out_default["current_metrics"]["z_score_spread_implied_rate"]
            != out_short["current_metrics"]["z_score_spread_implied_rate"]
        )

    def test_z_score_ddof_override_changes_z(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=2,
            lookback_days=365,
        )
        out_sample = _run(params, config=_custom_config(z_score_ddof=1))
        out_pop = _run(params, config=_custom_config(z_score_ddof=0))
        assert (
            out_sample["current_metrics"]["z_score_spread_implied_rate"]
            != out_pop["current_metrics"]["z_score_spread_implied_rate"]
        )

    def test_daily_change_offset_override_changes_change_values(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=2,
            lookback_days=365,
        )
        out_default = _run(params, config=_custom_config())
        out_wider = _run(
            params, config=_custom_config(daily_change_offset_rows=5),
        )
        assert (
            out_default["current_metrics"]["daily_change_raw_price_spread"]
            != out_wider["current_metrics"]["daily_change_raw_price_spread"]
        )

    def test_raw_price_round_decimals_propagates_to_time_series(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=2,
            lookback_days=365,
        )
        out = _run(
            params, config=_custom_config(raw_price_round_decimals=2),
        )
        for row in out["time_series"]:
            assert row["raw_price_spread"] == round(row["raw_price_spread"], 2)
        # Snapshot must agree at the latest row.
        assert (
            out["current_metrics"]["raw_price_spread"]
            == out["time_series"][-1]["raw_price_spread"]
        )

    def test_implied_rate_round_decimals_propagates_to_time_series(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=2,
            lookback_days=365,
        )
        out = _run(
            params, config=_custom_config(implied_rate_round_decimals=2),
        )
        for row in out["time_series"]:
            assert (
                row["spread_implied_rate_pct"]
                == round(row["spread_implied_rate_pct"], 2)
            )
        assert (
            out["current_metrics"]["spread_implied_rate_pct"]
            == out["time_series"][-1]["spread_implied_rate_pct"]
        )


# ===========================================================================
# 4. Honest-placeholder guard on trailing_range_window_days
# ===========================================================================

class TestTrailingWindowGuard:
    def test_unsupported_trailing_window_raises(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=2,
            lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            _run(
                params,
                config=_custom_config(trailing_range_window_days=180),
            )
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_spread_implied_rate_pct" in msg
        assert "planned_extensions" in msg

    def test_supported_default_does_not_raise(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=2,
            lookback_days=365,
        )
        out = _run(
            params, config=_custom_config(trailing_range_window_days=252),
        )
        assert "error" not in out


# ===========================================================================
# 5. Schema-layer behaviour + YAML fall-through
# ===========================================================================

class TestSchemaDefaults:
    def test_field_name_default_is_none(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=2,
        )
        assert params.field_name is None

    def test_as_of_date_default_is_none(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=2,
        )
        assert params.as_of_date is None

    def test_strip_position_short_lower_bound_enforced(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            FuturesCalendarSpreadInput(
                curve_family="SOFR_FUT",
                strip_position_short=0, strip_position_long=2,
            )

    def test_strip_position_long_upper_bound_enforced(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            FuturesCalendarSpreadInput(
                curve_family="SOFR_FUT",
                strip_position_short=1, strip_position_long=13,
            )

    def test_same_leg_calendar_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc:
            FuturesCalendarSpreadInput(
                curve_family="SOFR_FUT",
                strip_position_short=2, strip_position_long=2,
            )
        assert "differ" in str(exc.value).lower() or "must" in str(exc.value).lower()

    def test_short_greater_than_long_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc:
            FuturesCalendarSpreadInput(
                curve_family="SOFR_FUT",
                strip_position_short=3, strip_position_long=2,
            )
        assert "strictly less" in str(exc.value)


class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_price_field reaches fetch."""

    def _capture_field_name(self, params, config):
        with patch(
            f"{_TARGET}.fetch_strip_group",
            return_value=_synthetic_strip_group_df(
                short_position=params.strip_position_short,
                long_position=params.strip_position_long,
            ),
        ) as spy, patch(
            f"{_TARGET}.fetch_strip_position_reference",
            side_effect=_reference_side_effect_factory(
                _synthetic_reference(
                    contract_code="A", strip_position=params.strip_position_short,
                ),
                _synthetic_reference(
                    contract_code="B", strip_position=params.strip_position_long,
                ),
            ),
        ), patch(
            f"{_TARGET}.fetch_strip_position_max_date",
            return_value=None,
        ), patch(
            f"{_TARGET}.date", _FrozenDate,
        ):
            calculate_futures_calendar_spread(
                engine=None, params=params, config=config,
            )
        assert spy.call_count == 1
        return spy.call_args.kwargs["field_name"]

    def test_omitted_field_name_uses_yaml_default(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=2,
        )
        passed = self._capture_field_name(
            params, _custom_config(default_price_field="PX_LAST"),
        )
        assert passed == "PX_LAST"

    def test_yaml_override_changes_resolved_field(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=2,
        )
        passed = self._capture_field_name(
            params, _custom_config(default_price_field="PX_BID"),
        )
        assert passed == "PX_BID"

    def test_explicit_field_name_overrides_yaml(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=2,
            field_name="PX_ASK",
        )
        passed = self._capture_field_name(
            params, _custom_config(default_price_field="PX_LAST"),
        )
        assert passed == "PX_ASK"


# ===========================================================================
# 6. Inverse-pricing rule is metadata-driven (PR8 / P6)
# ===========================================================================

class TestInversePricingMetadataDriven:
    def test_inverse_flag_drives_conversion(self):
        """Flipping the metadata flag (NOT a hardcoded list in compute)
        changes the implied-rate spread sign. This is the load-bearing
        PR8 guarantee."""
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=2,
            lookback_days=365,
        )
        out_inverse = _run(params)
        out_direct = _run(
            params,
            short_reference=_synthetic_reference(
                contract_code="SFR1", strip_position=1,
                inverse_pricing=False,
            ),
            long_reference=_synthetic_reference(
                contract_code="SFR2", strip_position=2,
                inverse_pricing=False,
            ),
        )
        # For inverse-priced strips, the implied-rate spread equals
        # -(raw-price spread); for direct-priced, the two are equal.
        assert (
            out_inverse["current_metrics"]["spread_implied_rate_pct"]
            != out_direct["current_metrics"]["spread_implied_rate_pct"]
        )
        # Same raw data; raw_price_spread should be identical across
        # both branches.
        assert (
            out_inverse["current_metrics"]["raw_price_spread"]
            == out_direct["current_metrics"]["raw_price_spread"]
        )

    def test_missing_inverse_flag_returns_error_envelope(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=2,
            lookback_days=365,
        )
        short_ref = _synthetic_reference(
            contract_code="SFR1", strip_position=1,
        )
        del short_ref["inverse_pricing"]
        out = _run(
            params,
            short_reference=short_ref,
            long_reference=_synthetic_reference(
                contract_code="SFR2", strip_position=2,
            ),
        )
        assert "error" in out
        assert "inverse_pricing" in out["error"]

    def test_mixed_inverse_flags_returns_error_envelope(self):
        """Both legs of a same-curve calendar spread must share the
        inverse_pricing flag. A mixed setup is metadata-broken and
        produces the controlled-error envelope."""
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=2,
            lookback_days=365,
        )
        out = _run(
            params,
            short_reference=_synthetic_reference(
                contract_code="SFR1", strip_position=1,
                inverse_pricing=True,
            ),
            long_reference=_synthetic_reference(
                contract_code="SFR2", strip_position=2,
                inverse_pricing=False,
            ),
        )
        assert "error" in out
        assert "inverse_pricing" in out["error"]
        assert "mixed-convention" in out["error"].lower() or (
            "refusing" in out["error"].lower()
        )


# ===========================================================================
# 7. Future-anchor guard (PR8 + PR16) — per leg
# ===========================================================================

class TestFutureAnchorGuard:
    def test_as_of_beyond_universe_max_returns_error(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=2,
            lookback_days=365,
            as_of_date=date(2030, 1, 1),
        )
        out = _run(params, universe_max_date=date(2026, 4, 8))
        assert "error" in out
        assert "no scoreable strip" in out["error"]
        assert "2030-01-01" in out["error"]
        assert "2026-04-08" in out["error"]
        # No snapshot should be returned when the guard fires.
        assert "current_metrics" not in out
        assert "time_series" not in out

    def test_as_of_within_universe_proceeds(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=2,
            lookback_days=365,
            as_of_date=date(2026, 4, 8),
        )
        out = _run(params, universe_max_date=date(2026, 4, 30))
        assert "error" not in out, out.get("error")
        assert "current_metrics" in out

    def test_as_of_none_skips_guard(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=2,
            lookback_days=365,
        )
        out = _run(params, universe_max_date=date(1900, 1, 1))
        assert "error" not in out, out.get("error")


# ===========================================================================
# 8. Reference miss + missing regime label → error envelope
# ===========================================================================

class TestErrorEnvelopes:
    def test_missing_short_reference_returns_error(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=11, strip_position_long=12,
            lookback_days=365,
        )
        out = _run(params, short_reference=None)
        assert "error" in out
        assert "strip_position_short" in out["error"]

    def test_missing_long_reference_returns_error(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=12,
            lookback_days=365,
        )
        out = _run(params, long_reference=None)
        assert "error" in out
        assert "strip_position_long" in out["error"]

    def test_missing_regime_label_returns_error(self):
        params = FuturesCalendarSpreadInput(
            curve_family="UNKNOWN_FUT",
            strip_position_short=1, strip_position_long=2,
            lookback_days=365,
        )
        out = _run(
            params,
            short_reference=_synthetic_reference(
                curve_family="UNKNOWN_FUT",
                contract_code="UNK1", strip_position=1,
            ),
            long_reference=_synthetic_reference(
                curve_family="UNKNOWN_FUT",
                contract_code="UNK2", strip_position=2,
            ),
        )
        assert "error" in out
        assert "short_rate_regime_map" in out["error"]

    def test_empty_price_history_returns_error(self):
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=2,
            lookback_days=365,
        )
        empty_df = pd.DataFrame(
            columns=["trade_date", "strip_position", "field_value"],
        )
        out = _run(params, price_df=empty_df)
        assert "error" in out

    def test_missing_one_leg_returns_error(self):
        """If the strip-group fetch returns rows for only ONE leg,
        the controlled-error envelope must name the missing leg."""
        params = FuturesCalendarSpreadInput(
            curve_family="SOFR_FUT",
            strip_position_short=1, strip_position_long=2,
            lookback_days=365,
        )
        # Build a one-leg-only price frame.
        single_leg_df = _synthetic_strip_group_df(
            short_position=1, long_position=2,
        )
        single_leg_df = single_leg_df[
            single_leg_df["strip_position"] == 1
        ].reset_index(drop=True)
        out = _run(params, price_df=single_leg_df)
        assert "error" in out
        assert "[2]" in out["error"] or "Missing strip_position" in out["error"]


# ===========================================================================
# 9. Import path discipline
# ===========================================================================

class TestImportPathBackwardCompat:
    def test_calculate_via_package_init(self):
        from rates_agent.policy_futures.tools.futures_calendar_spread import (
            calculate_futures_calendar_spread as via_package,
        )
        from rates_agent.policy_futures.tools.futures_calendar_spread.compute import (
            calculate_futures_calendar_spread as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.policy_futures.tools.futures_calendar_spread import (
            FuturesCalendarSpreadInput as via_package,
        )
        from rates_agent.policy_futures.tools.futures_calendar_spread.schemas import (
            FuturesCalendarSpreadInput as via_schemas,
        )
        from rates_agent.policy_futures.tools.schemas import (
            FuturesCalendarSpreadInput as via_hub,
        )
        assert via_package is via_schemas is via_hub

    def test_output_schema_via_three_paths(self):
        from rates_agent.policy_futures.tools.futures_calendar_spread import (
            FuturesCalendarSpreadOutput as via_package,
        )
        from rates_agent.policy_futures.tools.futures_calendar_spread.schemas import (
            FuturesCalendarSpreadOutput as via_schemas,
        )
        from rates_agent.policy_futures.tools.schemas import (
            FuturesCalendarSpreadOutput as via_hub,
        )
        assert via_package is via_schemas is via_hub
