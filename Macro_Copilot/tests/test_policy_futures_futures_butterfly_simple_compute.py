"""
test_policy_futures_futures_butterfly_simple_compute.py — Unit tests
                                                            for the
                                                            policy-futures
                                                            simple-
                                                            butterfly
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
     enforce wing_short < body < wing_long AND distinct positions.
  6. Inverse-pricing rule is metadata-driven (PR8 / P6): the per-leg
     ``inverse_pricing`` flag on the reference dict — NOT a hardcoded
     list in compute — drives the implied-rate butterfly. All three
     legs must agree on the flag.
  7. Future-anchor guard: as_of_date beyond the universe max on
     ANY leg returns the controlled-error envelope.
  8. Per-leg reference metadata flows through to the snapshot.
  9. Methodology disclosure is present, required, and mentions the
     sign convention + fixed 50-50 weighting + regime + conversion
     rule + lookback window + scope-limit caveats.
 10. Sign convention: butterfly_value_pct = rate_body - 0.5 *
     (rate_wing_short + rate_wing_long).

Tests are fully offline.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.policy_futures.tools.futures_butterfly_simple import (
    CONFIG_PATH,
    FuturesButterflySimpleInput,
    FuturesButterflySimpleOutput,
    calculate_futures_butterfly_simple,
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
    wing_short_position: int = 1,
    body_position: int = 2,
    wing_long_position: int = 3,
    # Chosen so that:
    #   wing average drift = (2.0 + 0.5) / 2 = 1.25 != body_drift (0.5)
    #   ⇒ butterfly drift is non-zero (= -0.75 across the window)
    #   ⇒ daily-change AND z-score AND direct-vs-inverse-sign tests
    #   all see non-degenerate movement at 4-dp precision.
    wing_short_drift: float = 2.0,
    body_drift: float = 0.5,
    wing_long_drift: float = 0.5,
    wing_short_start: float = 96.5,
    body_start: float = 95.7,
    wing_long_start: float = 95.0,
    days: int = 400,
    frozen_today: date = date(2026, 4, 30),
    add_oscillation: bool = True,
) -> pd.DataFrame:
    """Build a three-leg long-format DataFrame matching the shape
    ``fetch_strip_group`` returns.

    The defaults are tuned so the butterfly value is non-degenerate:
    wing-average drift != body drift (otherwise the butterfly stays
    flat and the z-score / daily-change / direct-vs-inverse tests
    cannot distinguish branches). A small sinusoidal overlay on the
    body further breaks any residual rounding-induced collisions in
    the rolling z-score under ddof overrides.
    """
    bdays = pd.bdate_range(
        frozen_today - timedelta(days=days * 2), frozen_today,
    )
    bdays = bdays[-days:]
    n = len(bdays)

    def _leg(position: int, start: float, drift: float, body: bool = False) -> pd.DataFrame:
        series = np.linspace(start, start + drift, n)
        if body and add_oscillation:
            # Small sinusoidal kick on the body so the butterfly is
            # not a strict linear series — keeps the rolling std
            # responsive to ddof under different windows.
            series = series + 0.05 * np.sin(np.linspace(0, 6 * np.pi, n))
        return pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "strip_position": position,
            "field_value": series,
        })

    parts = [
        _leg(wing_short_position, wing_short_start, wing_short_drift),
        _leg(body_position, body_start, body_drift, body=True),
        _leg(wing_long_position, wing_long_start, wing_long_drift),
    ]
    return pd.concat(parts, ignore_index=True).sort_values(
        ["trade_date", "strip_position"]
    ).reset_index(drop=True)


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


def _reference_side_effect_factory(
    wing_short_ref: dict, body_ref: dict, wing_long_ref: dict,
):
    def _side_effect(*, engine, curve_family, strip_position, as_of_date):
        if strip_position == wing_short_ref["strip_position"]:
            return wing_short_ref
        if strip_position == body_ref["strip_position"]:
            return body_ref
        if strip_position == wing_long_ref["strip_position"]:
            return wing_long_ref
        return None
    return _side_effect


class _FrozenDate(date):
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


_UNSET = object()


_TARGET = "rates_agent.policy_futures.tools.futures_butterfly_simple.compute"


def _run(
    params,
    *,
    price_df=None,
    wing_short_reference=_UNSET,
    body_reference=_UNSET,
    wing_long_reference=_UNSET,
    config=None,
    universe_max_date=None,
):
    if price_df is None:
        price_df = _synthetic_strip_group_df(
            wing_short_position=params.strip_position_wing_short,
            body_position=params.strip_position_body,
            wing_long_position=params.strip_position_wing_long,
        )

    def _default_ref(label: str, position: int) -> dict:
        return _synthetic_reference(
            curve_family=params.curve_family,
            contract_code=f"STR{position}",
            strip_position=position,
        )

    refs = {
        "wing_short": (
            _default_ref("wing_short", params.strip_position_wing_short)
            if wing_short_reference is _UNSET
            else wing_short_reference
        ),
        "body": (
            _default_ref("body", params.strip_position_body)
            if body_reference is _UNSET
            else body_reference
        ),
        "wing_long": (
            _default_ref("wing_long", params.strip_position_wing_long)
            if wing_long_reference is _UNSET
            else wing_long_reference
        ),
    }

    def _side(*, engine, curve_family, strip_position, as_of_date):
        if strip_position == params.strip_position_wing_short:
            return refs["wing_short"]
        if strip_position == params.strip_position_body:
            return refs["body"]
        if strip_position == params.strip_position_wing_long:
            return refs["wing_long"]
        return None

    with patch(
        f"{_TARGET}.fetch_strip_group",
        return_value=price_df,
    ), patch(
        f"{_TARGET}.fetch_strip_position_reference",
        side_effect=_side,
    ), patch(
        f"{_TARGET}.fetch_strip_position_max_date",
        return_value=universe_max_date,
    ), patch(
        f"{_TARGET}.date",
        _FrozenDate,
    ):
        return calculate_futures_butterfly_simple(
            engine=None, params=params, config=config,
        )


def _custom_config(**overrides) -> ToolConfig:
    defaults = {
        "butterfly_weighting": (
            "body=1.0,wing_short=-0.5,wing_long=-0.5"
        ),
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
        "butterfly_value_round_decimals": 4,
        "implied_rate_round_decimals": 4,
        "z_score_round_decimals": 4,
        "butterfly_value_high_low_round_decimals": 4,
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
        assert cfg.tool.name == (
            "policy_futures_get_futures_butterfly_simple_tool"
        )
        assert cfg.tool.domain == "policy_futures"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "butterfly_weighting",
            "z_score_window_days",
            "z_score_min_periods",
            "z_score_ddof",
            "z_score_buffer_multiplier",
            "daily_change_offset_rows",
            "trailing_range_window_days",
            "ffill_limit_days",
            "default_price_field",
            "short_rate_regime_map",
            "butterfly_value_round_decimals",
            "implied_rate_round_decimals",
            "z_score_round_decimals",
            "butterfly_value_high_low_round_decimals",
            "percentile_round_decimals",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("z_score_window_days") == 252
        assert cfg.convention_value("z_score_min_periods") == 60
        assert cfg.convention_value("z_score_ddof") == 1
        assert cfg.convention_value("trailing_range_window_days") == 252
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("default_price_field") == "PX_LAST"
        weighting = cfg.convention_value("butterfly_weighting")
        assert "body=1.0" in weighting
        assert "wing_short=-0.5" in weighting
        assert "wing_long=-0.5" in weighting
        regime_csv = cfg.convention_value("short_rate_regime_map")
        assert "SOFR_FUT=RFR" in regime_csv
        assert "SONIA_FUT=RFR" in regime_csv
        assert "EUR_SHORT_RATE_FUT=IBOR" in regime_csv
        assert cfg.convention_value("butterfly_value_round_decimals") == 4
        assert cfg.convention_value("implied_rate_round_decimals") == 4
        assert cfg.convention_value("z_score_round_decimals") == 4
        assert cfg.convention_value(
            "butterfly_value_high_low_round_decimals"
        ) == 4
        assert cfg.convention_value("percentile_round_decimals") == 1

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 2
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "trailing_range_window_days" in joined
        # DV01-neutral / regression-fitted / meeting-by-meeting are
        # documented as planned extensions per catalog guardrails.
        assert "DV01-neutral" in joined or "DV01" in joined


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
        )
        out = _run(params)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        for k in (
            "as_of_date", "curve_family",
            "strip_position_wing_short", "strip_position_body",
            "strip_position_wing_long",
            "butterfly_label",
            "contract_code_wing_short", "contract_code_body",
            "contract_code_wing_long",
            "underlying_contract_code_wing_short",
            "underlying_contract_code_body",
            "underlying_contract_code_wing_long",
            "security_name_wing_short", "security_name_body",
            "security_name_wing_long",
            "expiry_date_wing_short", "expiry_date_body",
            "expiry_date_wing_long",
            "inverse_priced", "short_rate_regime",
            "implied_rate_pct_wing_short", "implied_rate_pct_body",
            "implied_rate_pct_wing_long",
            "butterfly_value_pct",
            "daily_change_butterfly_value_pct",
            "z_score_butterfly",
            "high_252d_butterfly_value_pct",
            "low_252d_butterfly_value_pct",
            "mid_252d_butterfly_value_pct",
            "percentile_252d",
            "rolling_window_days",
            "observation_count",
        ):
            assert k in cm, f"missing {k}"

        # PR14-frozen field-name discipline — guard against silent
        # renames.
        assert "butterfly_bps" not in cm  # NOT bps; this stays in pct points
        assert "butterfly_value_bps" not in cm
        assert "value_pct" not in cm or "butterfly_value_pct" in cm

        assert isinstance(cm["butterfly_value_pct"], float)
        assert isinstance(cm["implied_rate_pct_body"], float)
        assert cm["inverse_priced"] is True
        assert cm["short_rate_regime"] == "RFR"
        assert cm["rolling_window_days"] == 252

        # Bespoke time_series shape: list of {date,
        # butterfly_value_pct, z_score}.
        ts = out["time_series"]
        assert isinstance(ts, list)
        assert len(ts) > 0
        assert "date" in ts[0]
        assert "butterfly_value_pct" in ts[0]
        assert "z_score" in ts[0]

        # Canonical TimeSeries shapes
        tsb = out["time_series_butterfly"]
        assert tsb["units"] == "percent"
        assert tsb["series_name"] == "sofr_fut_1_2_3_butterfly"
        assert len(tsb["rows"]) > 0
        tsz = out["time_series_zscore"]
        assert tsz["units"] == "z_score"
        assert tsz["series_name"] == "sofr_fut_1_2_3_zscore"

    def test_butterfly_formula_matches_50_50_weighting(self):
        """For inverse-priced strips, butterfly_value_pct = (100 -
        price_body) - 0.5 * ((100 - price_wing_short) + (100 -
        price_wing_long)) = 0.5 * (price_wing_short + price_wing_long)
        - price_body."""
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
        )
        out = _run(params)
        cm = out["current_metrics"]
        # The per-leg rates are surfaced — compute the simple butterfly
        # from them and verify it matches the snapshot.
        expected = round(
            cm["implied_rate_pct_body"]
            - 0.5 * (
                cm["implied_rate_pct_wing_short"]
                + cm["implied_rate_pct_wing_long"]
            ),
            4,
        )
        assert abs(expected - cm["butterfly_value_pct"]) < 5e-5

    def test_direct_pricing_changes_butterfly_sign(self):
        """Flipping the inverse_pricing flag changes the butterfly
        sign — proves the metadata-driven conversion."""
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
        )
        out_inv = _run(params)
        out_dir = _run(
            params,
            wing_short_reference=_synthetic_reference(
                contract_code="SFR1", strip_position=1,
                inverse_pricing=False,
            ),
            body_reference=_synthetic_reference(
                contract_code="SFR2", strip_position=2,
                inverse_pricing=False,
            ),
            wing_long_reference=_synthetic_reference(
                contract_code="SFR3", strip_position=3,
                inverse_pricing=False,
            ),
        )
        assert out_inv["current_metrics"]["inverse_priced"] is True
        assert out_dir["current_metrics"]["inverse_priced"] is False
        # The two butterflies should differ (signs flip on the per-leg
        # series transformation 100 - x; the butterfly value flips
        # sign because the body weight is +1 and wings sum to -1).
        assert (
            out_inv["current_metrics"]["butterfly_value_pct"]
            != out_dir["current_metrics"]["butterfly_value_pct"]
        )
        # For direct-priced: butterfly = price_body - 0.5*(
        # price_wing_short + price_wing_long). For inverse-priced:
        # butterfly = (100-price_body) - 0.5*((100-price_wing_short)
        # + (100-price_wing_long)) = -1 * direct-priced butterfly.
        assert abs(
            out_inv["current_metrics"]["butterfly_value_pct"]
            + out_dir["current_metrics"]["butterfly_value_pct"]
        ) < 1e-3

    def test_explicit_config_matches_auto_loaded(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
        )
        out_auto = _run(params, config=None)
        out_explicit = _run(params, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit

    def test_per_leg_reference_metadata_flows_to_snapshot(self):
        params = FuturesButterflySimpleInput(
            curve_family="EUR_SHORT_RATE_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=4,
            lookback_days=365,
        )
        out = _run(
            params,
            wing_short_reference=_synthetic_reference(
                curve_family="EUR_SHORT_RATE_FUT",
                contract_code="ER1",
                strip_position=1,
                underlying_contract_code="ERM26",
                security_name="ERM26 Comdty",
                expiry=date(2026, 6, 15),
            ),
            body_reference=_synthetic_reference(
                curve_family="EUR_SHORT_RATE_FUT",
                contract_code="ER2",
                strip_position=2,
                underlying_contract_code="ERU26",
                security_name="ERU26 Comdty",
                expiry=date(2026, 9, 15),
            ),
            wing_long_reference=_synthetic_reference(
                curve_family="EUR_SHORT_RATE_FUT",
                contract_code="ER4",
                strip_position=4,
                underlying_contract_code="ERH27",
                security_name="ERH27 Comdty",
                expiry=date(2027, 3, 15),
            ),
        )
        cm = out["current_metrics"]
        assert cm["curve_family"] == "EUR_SHORT_RATE_FUT"
        assert cm["contract_code_wing_short"] == "ER1"
        assert cm["contract_code_body"] == "ER2"
        assert cm["contract_code_wing_long"] == "ER4"
        assert cm["butterfly_label"] == "ER1-ER2-ER4"
        assert cm["underlying_contract_code_wing_short"] == "ERM26"
        assert cm["underlying_contract_code_body"] == "ERU26"
        assert cm["underlying_contract_code_wing_long"] == "ERH27"
        assert cm["security_name_body"] == "ERU26 Comdty"
        assert cm["expiry_date_body"] == "2026-09-15"
        assert cm["short_rate_regime"] == "IBOR"

    def test_snapshot_equals_time_series_last_row_strictly(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
        )
        out = _run(params)
        last_row = out["time_series"][-1]
        cm = out["current_metrics"]
        assert last_row["butterfly_value_pct"] == cm["butterfly_value_pct"]
        # z_score may be None during warmup but otherwise must match
        if last_row["z_score"] is not None:
            assert last_row["z_score"] == cm["z_score_butterfly"]

    def test_canonical_time_series_matches_bespoke(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
        )
        out = _run(params)
        bespoke = out["time_series"]
        canon_b = out["time_series_butterfly"]["rows"]
        # Canonical butterfly only carries non-NaN values; bespoke
        # also skips NaN by construction. Their dates must align.
        bespoke_dates = [r["date"] for r in bespoke]
        canon_dates = [r["date"] for r in canon_b]
        assert bespoke_dates == canon_dates
        for b, c in zip(bespoke, canon_b):
            assert b["butterfly_value_pct"] == c["value"]

    def test_methodology_disclosure_present_and_required(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
        )
        out = _run(params)
        disclosure = out["methodology_disclosure"]
        assert isinstance(disclosure, str)
        assert len(disclosure) > 100
        from pydantic import ValidationError as _VE
        bad = dict(out)
        bad.pop("methodology_disclosure")
        with pytest.raises(_VE):
            FuturesButterflySimpleOutput.model_validate(bad)

    def test_methodology_disclosure_mentions_required_caveats(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
        )
        out = _run(params)
        disclosure = out["methodology_disclosure"]
        # Sign convention
        assert "body" in disclosure.lower()
        assert "wing" in disclosure.lower()
        assert "0.5" in disclosure
        # Fixed weighting
        assert "FIXED 50-50" in disclosure or "fixed 50-50" in disclosure.lower()
        assert "wing_short=-0.5" in disclosure or "-0.5" in disclosure
        # Regime + inverse pricing
        assert "RFR" in disclosure
        assert "implied_rate_pct = 100 - raw_price" in disclosure
        # Z window
        assert "252" in disclosure
        # Scope refusals
        assert "CTD" in disclosure
        assert "meeting-by-meeting" in disclosure
        assert "DV01" in disclosure
        assert "ADR 0011" in disclosure


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def test_z_score_window_override_changes_z(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
        )
        out_default = _run(params, config=_custom_config())
        out_short = _run(
            params, config=_custom_config(z_score_window_days=120),
        )
        assert (
            out_default["current_metrics"]["z_score_butterfly"]
            != out_short["current_metrics"]["z_score_butterfly"]
        )

    def test_z_score_ddof_override_changes_z(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
        )
        out_sample = _run(params, config=_custom_config(z_score_ddof=1))
        out_pop = _run(params, config=_custom_config(z_score_ddof=0))
        assert (
            out_sample["current_metrics"]["z_score_butterfly"]
            != out_pop["current_metrics"]["z_score_butterfly"]
        )

    def test_daily_change_offset_override_changes_change_value(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
        )
        out_default = _run(params, config=_custom_config())
        out_wider = _run(
            params, config=_custom_config(daily_change_offset_rows=5),
        )
        assert (
            out_default["current_metrics"]["daily_change_butterfly_value_pct"]
            != out_wider["current_metrics"]["daily_change_butterfly_value_pct"]
        )

    def test_butterfly_round_decimals_propagates_to_time_series(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
        )
        out = _run(
            params,
            config=_custom_config(butterfly_value_round_decimals=2),
        )
        for row in out["time_series"]:
            assert (
                row["butterfly_value_pct"]
                == round(row["butterfly_value_pct"], 2)
            )
        assert (
            out["current_metrics"]["butterfly_value_pct"]
            == out["time_series"][-1]["butterfly_value_pct"]
        )


# ===========================================================================
# 4. Honest-placeholder guard on trailing_range_window_days
# ===========================================================================

class TestTrailingWindowGuard:
    def test_unsupported_trailing_window_raises(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            _run(
                params,
                config=_custom_config(trailing_range_window_days=180),
            )
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_butterfly_value_pct" in msg
        assert "planned_extensions" in msg

    def test_supported_default_does_not_raise(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
        )
        out = _run(
            params,
            config=_custom_config(trailing_range_window_days=252),
        )
        assert "error" not in out


# ===========================================================================
# 5. Schema-layer behaviour + YAML fall-through
# ===========================================================================

class TestSchemaDefaults:
    def test_field_name_default_is_none(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
        )
        assert params.field_name is None

    def test_as_of_date_default_is_none(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
        )
        assert params.as_of_date is None

    def test_strip_position_lower_bound_enforced(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            FuturesButterflySimpleInput(
                curve_family="SOFR_FUT",
                strip_position_wing_short=0,
                strip_position_body=2,
                strip_position_wing_long=3,
            )

    def test_strip_position_upper_bound_enforced(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            FuturesButterflySimpleInput(
                curve_family="SOFR_FUT",
                strip_position_wing_short=1,
                strip_position_body=2,
                strip_position_wing_long=13,
            )

    def test_repeated_leg_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc:
            FuturesButterflySimpleInput(
                curve_family="SOFR_FUT",
                strip_position_wing_short=1,
                strip_position_body=2,
                strip_position_wing_long=2,
            )
        assert "distinct" in str(exc.value).lower() or "degenerate" in str(exc.value).lower()

    def test_unordered_positions_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc:
            FuturesButterflySimpleInput(
                curve_family="SOFR_FUT",
                strip_position_wing_short=3,
                strip_position_body=2,
                strip_position_wing_long=4,
            )
        assert "must hold" in str(exc.value) or "<" in str(exc.value)


class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_price_field reaches fetch."""

    def _capture_field_name(self, params, config):
        with patch(
            f"{_TARGET}.fetch_strip_group",
            return_value=_synthetic_strip_group_df(
                wing_short_position=params.strip_position_wing_short,
                body_position=params.strip_position_body,
                wing_long_position=params.strip_position_wing_long,
            ),
        ) as spy, patch(
            f"{_TARGET}.fetch_strip_position_reference",
            side_effect=_reference_side_effect_factory(
                _synthetic_reference(
                    contract_code="A",
                    strip_position=params.strip_position_wing_short,
                ),
                _synthetic_reference(
                    contract_code="B",
                    strip_position=params.strip_position_body,
                ),
                _synthetic_reference(
                    contract_code="C",
                    strip_position=params.strip_position_wing_long,
                ),
            ),
        ), patch(
            f"{_TARGET}.fetch_strip_position_max_date",
            return_value=None,
        ), patch(
            f"{_TARGET}.date", _FrozenDate,
        ):
            calculate_futures_butterfly_simple(
                engine=None, params=params, config=config,
            )
        assert spy.call_count == 1
        return spy.call_args.kwargs["field_name"]

    def test_omitted_field_name_uses_yaml_default(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
        )
        passed = self._capture_field_name(
            params, _custom_config(default_price_field="PX_LAST"),
        )
        assert passed == "PX_LAST"

    def test_yaml_override_changes_resolved_field(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
        )
        passed = self._capture_field_name(
            params, _custom_config(default_price_field="PX_BID"),
        )
        assert passed == "PX_BID"

    def test_explicit_field_name_overrides_yaml(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
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
    def test_missing_inverse_flag_returns_error_envelope(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
        )
        body_ref = _synthetic_reference(
            contract_code="SFR2", strip_position=2,
        )
        del body_ref["inverse_pricing"]
        out = _run(params, body_reference=body_ref)
        assert "error" in out
        assert "inverse_pricing" in out["error"]

    def test_mixed_inverse_flags_returns_error_envelope(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
        )
        out = _run(
            params,
            wing_short_reference=_synthetic_reference(
                contract_code="SFR1", strip_position=1,
                inverse_pricing=True,
            ),
            body_reference=_synthetic_reference(
                contract_code="SFR2", strip_position=2,
                inverse_pricing=False,
            ),
            wing_long_reference=_synthetic_reference(
                contract_code="SFR3", strip_position=3,
                inverse_pricing=True,
            ),
        )
        assert "error" in out
        assert "inverse_pricing" in out["error"]
        assert "disagree" in out["error"].lower() or (
            "mixed-convention" in out["error"].lower()
        )


# ===========================================================================
# 7. Future-anchor guard (PR8 + PR16) — per leg
# ===========================================================================

class TestFutureAnchorGuard:
    def test_as_of_beyond_universe_max_returns_error(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
            as_of_date=date(2030, 1, 1),
        )
        out = _run(params, universe_max_date=date(2026, 4, 8))
        assert "error" in out
        assert "no scoreable strip" in out["error"]
        assert "2030-01-01" in out["error"]
        assert "2026-04-08" in out["error"]
        assert "current_metrics" not in out
        assert "time_series" not in out

    def test_as_of_within_universe_proceeds(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
            as_of_date=date(2026, 4, 8),
        )
        out = _run(params, universe_max_date=date(2026, 4, 30))
        assert "error" not in out, out.get("error")
        assert "current_metrics" in out

    def test_as_of_none_skips_guard(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
        )
        out = _run(params, universe_max_date=date(1900, 1, 1))
        assert "error" not in out, out.get("error")


# ===========================================================================
# 8. Reference miss + missing regime label → error envelope
# ===========================================================================

class TestErrorEnvelopes:
    def test_missing_wing_short_reference_returns_error(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=10,
            strip_position_body=11,
            strip_position_wing_long=12,
            lookback_days=365,
        )
        out = _run(params, wing_short_reference=None)
        assert "error" in out
        assert "wing_short" in out["error"]

    def test_missing_body_reference_returns_error(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=11,
            strip_position_wing_long=12,
            lookback_days=365,
        )
        out = _run(params, body_reference=None)
        assert "error" in out
        assert "body" in out["error"]

    def test_missing_regime_label_returns_error(self):
        params = FuturesButterflySimpleInput(
            curve_family="UNKNOWN_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
        )
        out = _run(
            params,
            wing_short_reference=_synthetic_reference(
                curve_family="UNKNOWN_FUT",
                contract_code="UNK1", strip_position=1,
            ),
            body_reference=_synthetic_reference(
                curve_family="UNKNOWN_FUT",
                contract_code="UNK2", strip_position=2,
            ),
            wing_long_reference=_synthetic_reference(
                curve_family="UNKNOWN_FUT",
                contract_code="UNK3", strip_position=3,
            ),
        )
        assert "error" in out
        assert "short_rate_regime_map" in out["error"]

    def test_empty_price_history_returns_error(self):
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
        )
        empty_df = pd.DataFrame(
            columns=["trade_date", "strip_position", "field_value"],
        )
        out = _run(params, price_df=empty_df)
        assert "error" in out

    def test_missing_one_leg_returns_error(self):
        """If the strip-group fetch returns rows for only TWO legs,
        the controlled-error envelope must name the missing leg."""
        params = FuturesButterflySimpleInput(
            curve_family="SOFR_FUT",
            strip_position_wing_short=1,
            strip_position_body=2,
            strip_position_wing_long=3,
            lookback_days=365,
        )
        df = _synthetic_strip_group_df(
            wing_short_position=1, body_position=2, wing_long_position=3,
        )
        # Drop the body leg
        df = df[df["strip_position"] != 2].reset_index(drop=True)
        out = _run(params, price_df=df)
        assert "error" in out
        assert "[2]" in out["error"] or "Missing strip_position" in out["error"]


# ===========================================================================
# 9. Import path discipline
# ===========================================================================

class TestImportPathBackwardCompat:
    def test_calculate_via_package_init(self):
        from rates_agent.policy_futures.tools.futures_butterfly_simple import (
            calculate_futures_butterfly_simple as via_package,
        )
        from rates_agent.policy_futures.tools.futures_butterfly_simple.compute import (
            calculate_futures_butterfly_simple as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.policy_futures.tools.futures_butterfly_simple import (
            FuturesButterflySimpleInput as via_package,
        )
        from rates_agent.policy_futures.tools.futures_butterfly_simple.schemas import (
            FuturesButterflySimpleInput as via_schemas,
        )
        from rates_agent.policy_futures.tools.schemas import (
            FuturesButterflySimpleInput as via_hub,
        )
        assert via_package is via_schemas is via_hub

    def test_output_schema_via_three_paths(self):
        from rates_agent.policy_futures.tools.futures_butterfly_simple import (
            FuturesButterflySimpleOutput as via_package,
        )
        from rates_agent.policy_futures.tools.futures_butterfly_simple.schemas import (
            FuturesButterflySimpleOutput as via_schemas,
        )
        from rates_agent.policy_futures.tools.schemas import (
            FuturesButterflySimpleOutput as via_hub,
        )
        assert via_package is via_schemas is via_hub
