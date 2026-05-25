"""
test_policy_futures_futures_price_level_compute.py — Unit tests for
                                                      the policy-futures
                                                      strip-position
                                                      price-level monitor

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. compute() runs end-to-end against synthetic input with the
     bundled config and returns a well-formed output.
  3. Convention overrides actually change behaviour (z-window, ddof,
     ffill, default_price_field, raw_price_round_decimals,
     implied_rate_round_decimals).
  4. The honest placeholder for trailing_range_window_days: setting
     it to anything other than 252 raises NotImplementedError.
  5. Schema-layer behaviour: field_name defaults to None (sentinel),
     compute() resolves the sentinel against the YAML's
     default_price_field. as_of_date defaults to None.
  6. Inverse-pricing rule is metadata-driven (PR8 / P6): the
     ``inverse_pricing`` flag on the reference dict — NOT a
     hardcoded list in compute — drives implied_rate_pct.
  7. Future-anchor guard: as_of_date beyond the universe max returns
     the controlled-error envelope.
  8. Reference metadata flows through to the snapshot.
  9. Methodology disclosure is present, required, and mentions the
     regime + conversion rule + lookback window.

Tests are fully offline.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.policy_futures.tools.futures_price_level import (
    CONFIG_PATH,
    FuturesPriceLevelInput,
    FuturesPriceLevelOutput,
    calculate_futures_price_level,
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


def _synthetic_price_df(
    *,
    drift: float = 1.5,
    start_price: float = 96.0,
    days: int = 400,
    frozen_today: date = date(2026, 4, 30),
) -> pd.DataFrame:
    """Build a single-instrument long-format DataFrame matching the
    shape ``fetch_strip_position`` returns. Linspace from
    ``start_price`` to ``start_price + drift`` over ``days`` business
    days ending at ``frozen_today``. Default starts at 96.0 (an
    inverse-priced STIR-like quote — implied rate ≈ 4%)."""
    bdays = pd.bdate_range(frozen_today - timedelta(days=days * 2), frozen_today)
    bdays = bdays[-days:]
    n = len(bdays)
    series = np.linspace(start_price, start_price + drift, n)
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": series,
    })


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


class _FrozenDate(date):
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


_UNSET = object()


_TARGET = "rates_agent.policy_futures.tools.futures_price_level.compute"


def _run(
    params,
    *,
    price_df=None,
    reference=_UNSET,
    config=None,
    universe_max_date=None,
):
    if price_df is None:
        price_df = _synthetic_price_df()
    ref_value = _synthetic_reference() if reference is _UNSET else reference
    with patch(
        f"{_TARGET}.fetch_strip_position",
        return_value=price_df,
    ), patch(
        f"{_TARGET}.fetch_strip_position_reference",
        return_value=ref_value,
    ), patch(
        f"{_TARGET}.fetch_strip_position_max_date",
        return_value=universe_max_date,
    ), patch(
        f"{_TARGET}.date",
        _FrozenDate,
    ):
        return calculate_futures_price_level(engine=None, params=params, config=config)


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
        assert cfg.tool.name == "policy_futures_get_futures_price_level_tool"
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
        # Should contain all three V1 curve families.
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
        # CTD-of-futures-of-OIS path is documented-deferred.
        assert "CTD" in joined


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out = _run(params)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        for k in (
            "as_of_date", "curve_family", "strip_position",
            "contract_code", "underlying_contract_code", "security_name",
            "expiry_date", "contract_size", "tick_size", "tick_value",
            "inverse_priced", "short_rate_regime", "quote_units",
            "raw_price", "implied_rate_pct",
            "daily_change_raw_price", "daily_change_implied_rate_pct",
            "z_score_implied_rate",
            "high_252d_implied_rate_pct", "low_252d_implied_rate_pct",
            "mid_252d_implied_rate_pct",
            "high_252d_raw_price", "low_252d_raw_price",
            "mid_252d_raw_price",
            "percentile_252d", "observation_count",
        ):
            assert k in cm, f"missing {k}"

        # PR14-frozen field name discipline — guard against silent
        # renames.
        assert "implied_rate" not in cm  # bare 'implied_rate' (no _pct)
        assert "implied_rate_percent" not in cm
        assert "rate_pct" not in cm
        assert "current_price" not in cm  # must be raw_price
        assert "current_yield_pct" not in cm  # not a yield primitive

        assert isinstance(cm["raw_price"], float)
        assert isinstance(cm["implied_rate_pct"], float)
        assert cm["inverse_priced"] is True
        assert cm["short_rate_regime"] == "RFR"

        # Bespoke time_series shape: list of {date, raw_price,
        # implied_rate_pct}.
        ts = out["time_series"]
        assert isinstance(ts, list)
        assert len(ts) > 0
        assert "date" in ts[0]
        assert "raw_price" in ts[0]
        assert "implied_rate_pct" in ts[0]
        # NOT the canonical TimeSeries shape — should not carry
        # series_name / units / description keys.
        assert "value" not in ts[0]

    def test_inverse_pricing_conversion_holds(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out = _run(params)
        cm = out["current_metrics"]
        # 100 - raw_price == implied_rate_pct (modulo independent
        # rounding precision: raw_price=5dp, implied_rate=4dp).
        expected = round(100.0 - cm["raw_price"], 4)
        assert abs(expected - cm["implied_rate_pct"]) < 5e-5

    def test_direct_pricing_conversion_path(self):
        """Direct-priced strip — set inverse_pricing=False on the
        reference; implied_rate_pct must equal raw_price (modulo
        rounding) and quote_units must be 'rate (%)'."""
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        ref = _synthetic_reference(inverse_pricing=False)
        out = _run(params, reference=ref)
        cm = out["current_metrics"]
        assert cm["inverse_priced"] is False
        assert cm["quote_units"] == "rate (%)"
        # raw_price and implied_rate_pct are the same number, just
        # rounded to different precisions.
        assert abs(cm["raw_price"] - cm["implied_rate_pct"]) < 1e-3

    def test_explicit_config_matches_auto_loaded(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out_auto = _run(params, config=None)
        out_explicit = _run(params, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit

    def test_reference_metadata_flows_to_snapshot(self):
        params = FuturesPriceLevelInput(
            curve_family="EUR_SHORT_RATE_FUT", strip_position=2,
            lookback_days=365,
        )
        ref = _synthetic_reference(
            curve_family="EUR_SHORT_RATE_FUT",
            contract_code="ER2",
            strip_position=2,
            inverse_pricing=True,
            underlying_contract_code="ERM26",
            security_name="ERM26 Comdty",
            expiry=date(2026, 9, 15),
            contract_size=2500.0,
            tick_size=0.005,
            tick_value=12.5,
        )
        out = _run(params, reference=ref)
        cm = out["current_metrics"]
        assert cm["curve_family"] == "EUR_SHORT_RATE_FUT"
        assert cm["contract_code"] == "ER2"
        assert cm["strip_position"] == 2
        assert cm["underlying_contract_code"] == "ERM26"
        assert cm["security_name"] == "ERM26 Comdty"
        assert cm["expiry_date"] == "2026-09-15"
        assert cm["contract_size"] == 2500.0
        assert cm["tick_size"] == 0.005
        assert cm["tick_value"] == 12.5
        assert cm["short_rate_regime"] == "IBOR"

    def test_snapshot_equals_time_series_last_row_strictly(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out = _run(params)
        last_row = out["time_series"][-1]
        cm = out["current_metrics"]
        # Strict equality — both go through the SAME rounding
        # conventions.
        assert last_row["raw_price"] == cm["raw_price"]
        assert last_row["implied_rate_pct"] == cm["implied_rate_pct"]

    def test_methodology_disclosure_present_and_required(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out = _run(params)
        disclosure = out["methodology_disclosure"]
        assert isinstance(disclosure, str)
        assert len(disclosure) > 50
        # Schema must require the field — pop and re-validate.
        from pydantic import ValidationError as _VE
        bad = dict(out)
        bad.pop("methodology_disclosure")
        with pytest.raises(_VE):
            FuturesPriceLevelOutput.model_validate(bad)

    def test_methodology_disclosure_mentions_required_caveats(self):
        """P5 / ADR 0013 caveats MUST mention the rolling-generic
        strip-read scope, the regime label, the inverse-pricing rule,
        the z-score window, and the CTD-of-OIS scope-limit."""
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out = _run(params)
        disclosure = out["methodology_disclosure"]
        assert "strip_position" in disclosure
        assert "implied rate" in disclosure.lower()
        assert "RFR" in disclosure  # the regime label for SOFR_FUT
        assert "100 - raw_price" in disclosure
        assert "252" in disclosure  # z window
        assert "CTD" in disclosure  # CTD-of-OIS scope-limit
        assert "ADR 0013" in disclosure


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def test_z_score_window_override_changes_z(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out_default = _run(params, config=_custom_config())
        out_short = _run(params, config=_custom_config(z_score_window_days=120))
        assert (
            out_default["current_metrics"]["z_score_implied_rate"]
            != out_short["current_metrics"]["z_score_implied_rate"]
        )

    def test_z_score_ddof_override_changes_z(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out_sample = _run(params, config=_custom_config(z_score_ddof=1))
        out_pop = _run(params, config=_custom_config(z_score_ddof=0))
        assert (
            out_sample["current_metrics"]["z_score_implied_rate"]
            != out_pop["current_metrics"]["z_score_implied_rate"]
        )

    def test_daily_change_offset_override_changes_change_values(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out_default = _run(params, config=_custom_config())
        out_wider = _run(params, config=_custom_config(daily_change_offset_rows=5))
        assert (
            out_default["current_metrics"]["daily_change_raw_price"]
            != out_wider["current_metrics"]["daily_change_raw_price"]
        )

    def test_raw_price_round_decimals_propagates_to_time_series(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out = _run(params, config=_custom_config(raw_price_round_decimals=2))
        for row in out["time_series"]:
            assert row["raw_price"] == round(row["raw_price"], 2)
        # Snapshot must agree at the latest row.
        assert (
            out["current_metrics"]["raw_price"]
            == out["time_series"][-1]["raw_price"]
        )

    def test_implied_rate_round_decimals_propagates_to_time_series(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out = _run(params, config=_custom_config(implied_rate_round_decimals=2))
        for row in out["time_series"]:
            assert row["implied_rate_pct"] == round(row["implied_rate_pct"], 2)
        assert (
            out["current_metrics"]["implied_rate_pct"]
            == out["time_series"][-1]["implied_rate_pct"]
        )

    def test_ffill_limit_passed_to_clean(self):
        from shared.analytics.levels import clean_single_series as real_clean
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        with patch(
            f"{_TARGET}.fetch_strip_position",
            return_value=_synthetic_price_df(),
        ), patch(
            f"{_TARGET}.fetch_strip_position_reference",
            return_value=_synthetic_reference(),
        ), patch(
            f"{_TARGET}.fetch_strip_position_max_date",
            return_value=None,
        ), patch(
            f"{_TARGET}.date", _FrozenDate,
        ), patch(
            f"{_TARGET}.clean_single_series",
            wraps=real_clean,
        ) as spy:
            calculate_futures_price_level(
                engine=None, params=params,
                config=_custom_config(ffill_limit_days=2),
            )
        assert spy.call_count == 1
        assert spy.call_args.kwargs["ffill_limit"] == 2


# ===========================================================================
# 4. Honest-placeholder guard on trailing_range_window_days
# ===========================================================================

class TestTrailingWindowGuard:
    def test_unsupported_trailing_window_raises(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            _run(params, config=_custom_config(trailing_range_window_days=180))
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_implied_rate_pct" in msg
        assert "planned_extensions" in msg

    def test_supported_default_does_not_raise(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out = _run(params, config=_custom_config(trailing_range_window_days=252))
        assert "error" not in out


# ===========================================================================
# 5. Schema-layer behaviour + YAML fall-through
# ===========================================================================

class TestSchemaDefaults:
    def test_field_name_default_is_none(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1,
        )
        assert params.field_name is None, (
            "schema default must be None so compute() can fall through "
            "to the YAML's default_price_field"
        )

    def test_as_of_date_default_is_none(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1,
        )
        assert params.as_of_date is None, (
            "schema default must be None so compute() anchors at the "
            "post-fetch data-max date"
        )

    def test_explicit_field_name_passes_through(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, field_name="PX_BID",
        )
        assert params.field_name == "PX_BID"

    def test_strip_position_lower_bound_enforced(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            FuturesPriceLevelInput(curve_family="SOFR_FUT", strip_position=0)

    def test_strip_position_upper_bound_enforced(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            FuturesPriceLevelInput(curve_family="SOFR_FUT", strip_position=13)


class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_price_field reaches fetch."""

    def _capture_field_name(self, params, config):
        with patch(
            f"{_TARGET}.fetch_strip_position",
            return_value=_synthetic_price_df(),
        ) as spy, patch(
            f"{_TARGET}.fetch_strip_position_reference",
            return_value=_synthetic_reference(),
        ), patch(
            f"{_TARGET}.fetch_strip_position_max_date",
            return_value=None,
        ), patch(
            f"{_TARGET}.date", _FrozenDate,
        ):
            calculate_futures_price_level(engine=None, params=params, config=config)
        assert spy.call_count == 1
        return spy.call_args.kwargs["field_name"]

    def test_omitted_field_name_uses_yaml_default(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1,
        )
        passed = self._capture_field_name(
            params, _custom_config(default_price_field="PX_LAST"),
        )
        assert passed == "PX_LAST"

    def test_yaml_override_changes_resolved_field(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1,
        )
        passed = self._capture_field_name(
            params, _custom_config(default_price_field="PX_BID"),
        )
        assert passed == "PX_BID"

    def test_explicit_field_name_overrides_yaml(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, field_name="PX_ASK",
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
        """Verify that flipping the metadata flag (NOT a hardcoded
        list in compute) changes the implied-rate conversion. This is
        the load-bearing PR8 guarantee."""
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out_inverse = _run(params, reference=_synthetic_reference(inverse_pricing=True))
        out_direct = _run(params, reference=_synthetic_reference(inverse_pricing=False))
        # Inverse: implied_rate ≈ 100 - 96 = 4.
        # Direct: implied_rate ≈ 96.
        assert out_inverse["current_metrics"]["implied_rate_pct"] < 10
        assert out_direct["current_metrics"]["implied_rate_pct"] > 90

    def test_missing_inverse_flag_returns_error_envelope(self):
        """A reference dict WITHOUT the inverse_pricing flag must
        surface as a metadata gap (controlled-error envelope) —
        compute MUST NOT silently default to inverse."""
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        ref = _synthetic_reference()
        del ref["inverse_pricing"]
        out = _run(params, reference=ref)
        assert "error" in out
        assert "inverse_pricing" in out["error"]
        assert "metadata" in out["error"].lower()

    def test_quote_units_reflects_inverse_flag(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out_inverse = _run(params, reference=_synthetic_reference(inverse_pricing=True))
        out_direct = _run(params, reference=_synthetic_reference(inverse_pricing=False))
        assert out_inverse["current_metrics"]["quote_units"] == "100 - rate"
        assert out_direct["current_metrics"]["quote_units"] == "rate (%)"


# ===========================================================================
# 7. Future-anchor guard (PR8 + PR16)
# ===========================================================================

class TestFutureAnchorGuard:
    def test_as_of_beyond_universe_max_returns_error(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
            as_of_date=date(2030, 1, 1),
        )
        out = _run(
            params,
            universe_max_date=date(2026, 4, 8),
        )
        assert "error" in out
        assert "no scoreable strip" in out["error"]
        assert "2030-01-01" in out["error"]
        assert "2026-04-08" in out["error"]
        # No snapshot should be returned when the guard fires.
        assert "current_metrics" not in out
        assert "time_series" not in out

    def test_as_of_within_universe_proceeds(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
            as_of_date=date(2026, 4, 8),
        )
        out = _run(
            params,
            universe_max_date=date(2026, 4, 30),
        )
        assert "error" not in out, out.get("error")
        assert "current_metrics" in out

    def test_as_of_none_skips_guard(self):
        """When as_of_date is None, the future-anchor probe is skipped —
        the post-fetch data-max anchor is honest by construction."""
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out = _run(params, universe_max_date=date(1900, 1, 1))
        # Should NOT return the future-anchor error even though
        # universe_max is in 1900 (the guard is skipped when as_of
        # is None).
        assert "error" not in out, out.get("error")


# ===========================================================================
# 8. Reference miss + missing regime label → error envelope
# ===========================================================================

class TestErrorEnvelopes:
    def test_missing_reference_returns_error(self):
        # strip_position=12 is in-bound (schema cap) but assumed
        # absent from the universe (V1 carries positions 1..8) — the
        # mocked reference=None branch is what we are exercising.
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=12, lookback_days=365,
        )
        out = _run(params, reference=None)
        assert "error" in out
        assert "strip_position=12" in out["error"]

    def test_missing_regime_label_returns_error(self):
        """A curve_family not in the YAML's regime map produces the
        documented controlled-error envelope (P5 — no silent default)."""
        params = FuturesPriceLevelInput(
            curve_family="UNKNOWN_FUT", strip_position=1, lookback_days=365,
        )
        ref = _synthetic_reference(curve_family="UNKNOWN_FUT", contract_code="UNK1")
        out = _run(params, reference=ref)
        assert "error" in out
        assert "short_rate_regime_map" in out["error"]

    def test_empty_price_history_returns_error(self):
        params = FuturesPriceLevelInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        empty_df = pd.DataFrame({"trade_date": [], "field_value": []})
        out = _run(params, price_df=empty_df)
        assert "error" in out
        assert "strip_position=1" in out["error"]


# ===========================================================================
# 9. Import path discipline
# ===========================================================================

class TestImportPathBackwardCompat:
    def test_calculate_via_package_init(self):
        from rates_agent.policy_futures.tools.futures_price_level import (
            calculate_futures_price_level as via_package,
        )
        from rates_agent.policy_futures.tools.futures_price_level.compute import (
            calculate_futures_price_level as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.policy_futures.tools.futures_price_level import (
            FuturesPriceLevelInput as via_package,
        )
        from rates_agent.policy_futures.tools.futures_price_level.schemas import (
            FuturesPriceLevelInput as via_schemas,
        )
        from rates_agent.policy_futures.tools.schemas import (
            FuturesPriceLevelInput as via_hub,
        )
        assert via_package is via_schemas is via_hub

    def test_output_schema_via_three_paths(self):
        from rates_agent.policy_futures.tools.futures_price_level import (
            FuturesPriceLevelOutput as via_package,
        )
        from rates_agent.policy_futures.tools.futures_price_level.schemas import (
            FuturesPriceLevelOutput as via_schemas,
        )
        from rates_agent.policy_futures.tools.schemas import (
            FuturesPriceLevelOutput as via_hub,
        )
        assert via_package is via_schemas is via_hub
