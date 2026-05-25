"""
test_policy_futures_futures_cross_market_spread_compute.py — Unit
                                                              tests
                                                              for the
                                                              policy-
                                                              futures
                                                              cross-
                                                              market-
                                                              spread
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
     default_price_field. as_of_date defaults to None. Validator
     enforces curve_family_a != curve_family_b.
  6. Inverse-pricing rule is metadata-driven (PR8 / P6): the per-
     leg ``inverse_pricing`` flag on each reference dict — NOT a
     hardcoded list in compute — drives the per-leg implied-rate
     conversion. Each leg's flag is read INDEPENDENTLY.
  7. Future-anchor guard: as_of_date beyond the universe max on
     EITHER leg returns the controlled-error envelope.
  8. Per-leg reference metadata flows through to the snapshot.
  9. Methodology disclosure is present, required, and mentions the
     wire-frozen A − B sign convention, the per-leg regime labels,
     the RAW-differential label, the pack-average refusal, the
     z window, and the scope-limit caveats.
 10. Sign convention: spread_value_pct = implied_rate_pct_a -
     implied_rate_pct_b. Swapping inputs flips the sign.
 11. Same-regime AND mixed-regime pairs both produce honest
     snapshots; mixed-regime pairs preserve BOTH labels on the
     snapshot (NO pack-average collapse).

Tests are fully offline.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.policy_futures.tools.futures_cross_market_spread import (
    CONFIG_PATH,
    FuturesCrossMarketSpreadInput,
    FuturesCrossMarketSpreadOutput,
    calculate_futures_cross_market_spread,
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


def _synthetic_cross_market_df(
    *,
    curve_family_a: str = "SOFR_FUT",
    curve_family_b: str = "SONIA_FUT",
    a_start: float = 96.0,
    b_start: float = 95.5,
    a_drift: float = 1.5,
    b_drift: float = 1.0,
    days: int = 400,
    frozen_today: date = date(2026, 4, 30),
    add_oscillation: bool = True,
) -> pd.DataFrame:
    """Build a two-leg long-format DataFrame matching the shape
    ``fetch_cross_market_strip`` returns (columns:
    ``['trade_date', 'curve_family', 'field_value']``).

    Defaults are tuned so the spread is non-degenerate:
      - a_drift != b_drift ⇒ spread series has non-zero drift
      - small oscillation on B breaks linearity so rolling std is
        responsive under ddof overrides.
    """
    bdays = pd.bdate_range(
        frozen_today - timedelta(days=days * 2), frozen_today,
    )
    bdays = bdays[-days:]
    n = len(bdays)

    def _leg(name: str, start: float, drift: float, add_osc: bool) -> pd.DataFrame:
        series = np.linspace(start, start + drift, n)
        if add_osc:
            series = series + 0.05 * np.sin(np.linspace(0, 6 * np.pi, n))
        return pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "curve_family": name,
            "field_value": series,
        })

    parts = [
        _leg(curve_family_a, a_start, a_drift, add_oscillation),
        _leg(curve_family_b, b_start, b_drift, False),
    ]
    return pd.concat(parts, ignore_index=True).sort_values(
        ["trade_date", "curve_family"]
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


class _FrozenDate(date):
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


_UNSET = object()


_TARGET = "rates_agent.policy_futures.tools.futures_cross_market_spread.compute"


def _run(
    params,
    *,
    price_df=None,
    reference_a=_UNSET,
    reference_b=_UNSET,
    config=None,
    universe_max_date=None,
):
    if price_df is None:
        price_df = _synthetic_cross_market_df(
            curve_family_a=params.curve_family_a,
            curve_family_b=params.curve_family_b,
        )

    def _default_ref(curve_family: str, contract_code: str) -> dict:
        return _synthetic_reference(
            curve_family=curve_family,
            contract_code=contract_code,
            strip_position=params.strip_position,
        )

    ref_a = (
        _default_ref(
            params.curve_family_a,
            f"{params.curve_family_a[:3]}{params.strip_position}",
        )
        if reference_a is _UNSET
        else reference_a
    )
    ref_b = (
        _default_ref(
            params.curve_family_b,
            f"{params.curve_family_b[:3]}{params.strip_position}",
        )
        if reference_b is _UNSET
        else reference_b
    )

    def _side(*, engine, curve_family, strip_position, as_of_date):
        if curve_family == params.curve_family_a:
            return ref_a
        if curve_family == params.curve_family_b:
            return ref_b
        return None

    def _max_side(*, engine, curve_family, strip_position, field_name):
        return universe_max_date

    with patch(
        f"{_TARGET}.fetch_cross_market_strip",
        return_value=price_df,
    ), patch(
        f"{_TARGET}.fetch_strip_position_reference",
        side_effect=_side,
    ), patch(
        f"{_TARGET}.fetch_strip_position_max_date",
        side_effect=_max_side,
    ), patch(
        f"{_TARGET}.date",
        _FrozenDate,
    ):
        return calculate_futures_cross_market_spread(
            engine=None, params=params, config=config,
        )


def _custom_config(**overrides) -> ToolConfig:
    defaults = {
        "cross_market_pair_orientation": "A_minus_B",
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
        "spread_value_round_decimals": 4,
        "implied_rate_round_decimals": 4,
        "z_score_round_decimals": 4,
        "spread_value_high_low_round_decimals": 4,
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
            "policy_futures_get_futures_cross_market_spread_tool"
        )
        assert cfg.tool.domain == "policy_futures"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "cross_market_pair_orientation",
            "z_score_window_days",
            "z_score_min_periods",
            "z_score_ddof",
            "z_score_buffer_multiplier",
            "daily_change_offset_rows",
            "trailing_range_window_days",
            "ffill_limit_days",
            "default_price_field",
            "short_rate_regime_map",
            "spread_value_round_decimals",
            "implied_rate_round_decimals",
            "z_score_round_decimals",
            "spread_value_high_low_round_decimals",
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
        assert (
            cfg.convention_value("cross_market_pair_orientation")
            == "A_minus_B"
        )
        regime_csv = cfg.convention_value("short_rate_regime_map")
        assert "SOFR_FUT=RFR" in regime_csv
        assert "SONIA_FUT=RFR" in regime_csv
        assert "EUR_SHORT_RATE_FUT=IBOR" in regime_csv
        assert cfg.convention_value("spread_value_round_decimals") == 4
        assert cfg.convention_value("implied_rate_round_decimals") == 4
        assert cfg.convention_value("z_score_round_decimals") == 4
        assert (
            cfg.convention_value("spread_value_high_low_round_decimals")
            == 4
        )
        assert cfg.convention_value("percentile_round_decimals") == 1

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 2
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "trailing_range_window_days" in joined
        # Basis-adjusted / beta-adjusted variants are explicitly
        # listed as PR11 planned-extension territory per the catalog
        # guardrail.
        assert "Basis-adjusted" in joined or "basis-adjusted" in joined
        assert "Beta-adjusted" in joined or "beta-adjusted" in joined


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT",
            curve_family_b="SONIA_FUT",
            strip_position=1,
            lookback_days=365,
        )
        out = _run(params)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        for k in (
            "as_of_date", "curve_family_a", "curve_family_b",
            "strip_position",
            "spread_label",
            "contract_code_a", "contract_code_b",
            "underlying_contract_code_a", "underlying_contract_code_b",
            "security_name_a", "security_name_b",
            "expiry_date_a", "expiry_date_b",
            "inverse_priced_a", "inverse_priced_b",
            "short_rate_regime_a", "short_rate_regime_b",
            "implied_rate_pct_a", "implied_rate_pct_b",
            "spread_value_pct",
            "daily_change_spread_value_pct",
            "z_score_spread",
            "high_252d_spread_value_pct",
            "low_252d_spread_value_pct",
            "mid_252d_spread_value_pct",
            "percentile_252d",
            "rolling_window_days",
            "observation_count",
        ):
            assert k in cm, f"missing {k}"

        # PR14-frozen field-name discipline — guard against silent
        # renames.
        assert "spread_bps" not in cm  # NOT bps; PERCENT POINTS
        assert "current_spread_bps" not in cm
        assert "spread_pct" not in cm or "spread_value_pct" in cm

        assert isinstance(cm["spread_value_pct"], float)
        assert isinstance(cm["implied_rate_pct_a"], float)
        assert isinstance(cm["implied_rate_pct_b"], float)
        assert cm["inverse_priced_a"] is True
        assert cm["inverse_priced_b"] is True
        assert cm["short_rate_regime_a"] == "RFR"
        assert cm["short_rate_regime_b"] == "RFR"
        assert cm["rolling_window_days"] == 252

        # Bespoke time_series shape: list of {date, spread_value_pct,
        # z_score}.
        ts = out["time_series"]
        assert isinstance(ts, list)
        assert len(ts) > 0
        assert "date" in ts[0]
        assert "spread_value_pct" in ts[0]
        assert "z_score" in ts[0]

        # Canonical TimeSeries shapes
        tss = out["time_series_spread"]
        assert tss["units"] == "percent"
        assert tss["series_name"] == "sofr_fut_sonia_fut_1_spread"
        assert len(tss["rows"]) > 0
        tsz = out["time_series_zscore"]
        assert tsz["units"] == "z_score"
        assert tsz["series_name"] == "sofr_fut_sonia_fut_1_zscore"

    def test_a_minus_b_sign_convention(self):
        """For inverse-priced strips:
            spread = (100 - price_a) - (100 - price_b)
                   = price_b - price_a
        The per-leg implied rates are surfaced — verify the snapshot's
        spread_value_pct equals A − B from those."""
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT",
            curve_family_b="SONIA_FUT",
            strip_position=1,
            lookback_days=365,
        )
        out = _run(params)
        cm = out["current_metrics"]
        expected = round(
            cm["implied_rate_pct_a"] - cm["implied_rate_pct_b"],
            4,
        )
        assert abs(expected - cm["spread_value_pct"]) < 5e-5

    def test_swap_a_b_flips_sign(self):
        """Swapping A and B flips the sign of spread_value_pct — the
        orientation is wire-frozen by construction."""
        params_ab = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT",
            curve_family_b="SONIA_FUT",
            strip_position=1,
            lookback_days=365,
        )
        out_ab = _run(params_ab)
        params_ba = FuturesCrossMarketSpreadInput(
            curve_family_a="SONIA_FUT",
            curve_family_b="SOFR_FUT",
            strip_position=1,
            lookback_days=365,
        )
        # Same synthetic series for both runs — the curve_family
        # label in the synthetic frame matches each run's A/B
        # assignment via _run's default _synthetic_cross_market_df.
        out_ba = _run(
            params_ba,
            price_df=_synthetic_cross_market_df(
                curve_family_a="SONIA_FUT",
                curve_family_b="SOFR_FUT",
                # SONIA_FUT now plays leg A; mirror the same numerics
                # so the sign-flip test is exact.
                a_start=95.5, a_drift=1.0,
                b_start=96.0, b_drift=1.5,
            ),
        )
        # The two snapshots should be exact negatives at the rounded
        # 4-dp precision.
        s_ab = out_ab["current_metrics"]["spread_value_pct"]
        s_ba = out_ba["current_metrics"]["spread_value_pct"]
        assert abs(s_ab + s_ba) < 5e-5, (
            f"sign-flip test: AB={s_ab}, BA={s_ba}, sum={s_ab + s_ba}"
        )

    def test_direct_pricing_changes_spread_sign(self):
        """Flipping the per-leg inverse_pricing flag changes the
        per-leg implied rate from (100 - price) to price; doing this
        on both legs flips the spread sign in the inverse-priced
        case (the differential reverses)."""
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT",
            curve_family_b="SONIA_FUT",
            strip_position=1,
            lookback_days=365,
        )
        out_inv = _run(params)
        out_dir = _run(
            params,
            reference_a=_synthetic_reference(
                curve_family="SOFR_FUT", contract_code="SFR1",
                strip_position=1, inverse_pricing=False,
            ),
            reference_b=_synthetic_reference(
                curve_family="SONIA_FUT", contract_code="SFI1",
                strip_position=1, inverse_pricing=False,
            ),
        )
        assert out_inv["current_metrics"]["inverse_priced_a"] is True
        assert out_inv["current_metrics"]["inverse_priced_b"] is True
        assert out_dir["current_metrics"]["inverse_priced_a"] is False
        assert out_dir["current_metrics"]["inverse_priced_b"] is False
        # For inverse: (100-a) - (100-b) = b - a; for direct:
        # a - b. So inverse_spread = -direct_spread.
        assert abs(
            out_inv["current_metrics"]["spread_value_pct"]
            + out_dir["current_metrics"]["spread_value_pct"]
        ) < 1e-3

    def test_explicit_config_matches_auto_loaded(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT",
            curve_family_b="SONIA_FUT",
            strip_position=1,
            lookback_days=365,
        )
        out_auto = _run(params, config=None)
        out_explicit = _run(params, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit

    def test_per_leg_reference_metadata_flows_to_snapshot(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT",
            curve_family_b="EUR_SHORT_RATE_FUT",
            strip_position=4,
            lookback_days=365,
        )
        out = _run(
            params,
            price_df=_synthetic_cross_market_df(
                curve_family_a="SOFR_FUT",
                curve_family_b="EUR_SHORT_RATE_FUT",
            ),
            reference_a=_synthetic_reference(
                curve_family="SOFR_FUT",
                contract_code="SFR4",
                strip_position=4,
                underlying_contract_code="SFRH27",
                security_name="SFRH27 COMB",
                expiry=date(2027, 3, 17),
            ),
            reference_b=_synthetic_reference(
                curve_family="EUR_SHORT_RATE_FUT",
                contract_code="ER4",
                strip_position=4,
                underlying_contract_code="ERH27",
                security_name="ERH27 Comdty",
                expiry=date(2027, 3, 15),
            ),
        )
        cm = out["current_metrics"]
        assert cm["curve_family_a"] == "SOFR_FUT"
        assert cm["curve_family_b"] == "EUR_SHORT_RATE_FUT"
        assert cm["contract_code_a"] == "SFR4"
        assert cm["contract_code_b"] == "ER4"
        assert cm["spread_label"] == "SFR4-ER4"
        assert cm["strip_position"] == 4
        assert cm["underlying_contract_code_a"] == "SFRH27"
        assert cm["underlying_contract_code_b"] == "ERH27"
        assert cm["security_name_a"] == "SFRH27 COMB"
        assert cm["security_name_b"] == "ERH27 Comdty"
        assert cm["expiry_date_a"] == "2027-03-17"
        assert cm["expiry_date_b"] == "2027-03-15"
        # Mixed-regime pair: A=RFR, B=IBOR — BOTH labels preserved.
        assert cm["short_rate_regime_a"] == "RFR"
        assert cm["short_rate_regime_b"] == "IBOR"

    def test_mixed_regime_pair_preserves_both_labels(self):
        """Catalog guardrail: mixed RFR/IBOR pairs are NOT collapsed
        into a single regime label. Both labels are surfaced even
        when they differ."""
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SONIA_FUT",
            curve_family_b="EUR_SHORT_RATE_FUT",
            strip_position=1,
            lookback_days=365,
        )
        out = _run(
            params,
            price_df=_synthetic_cross_market_df(
                curve_family_a="SONIA_FUT",
                curve_family_b="EUR_SHORT_RATE_FUT",
            ),
            reference_a=_synthetic_reference(
                curve_family="SONIA_FUT", contract_code="SFI1",
                strip_position=1,
            ),
            reference_b=_synthetic_reference(
                curve_family="EUR_SHORT_RATE_FUT", contract_code="ER1",
                strip_position=1,
            ),
        )
        cm = out["current_metrics"]
        assert cm["short_rate_regime_a"] == "RFR"
        assert cm["short_rate_regime_b"] == "IBOR"
        # The methodology card explicitly marks the pair as mixed-
        # regime AND refuses pack-average collapse.
        disclosure = out["methodology_disclosure"]
        assert "MIXED-REGIME" in disclosure
        assert "pack-average" in disclosure

    def test_snapshot_equals_time_series_last_row_strictly(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT",
            curve_family_b="SONIA_FUT",
            strip_position=1,
            lookback_days=365,
        )
        out = _run(params)
        last_row = out["time_series"][-1]
        cm = out["current_metrics"]
        assert last_row["spread_value_pct"] == cm["spread_value_pct"]
        # z_score may be None during warmup but otherwise must match
        if last_row["z_score"] is not None:
            assert last_row["z_score"] == cm["z_score_spread"]

    def test_canonical_time_series_matches_bespoke(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT",
            curve_family_b="SONIA_FUT",
            strip_position=1,
            lookback_days=365,
        )
        out = _run(params)
        bespoke = out["time_series"]
        canon_s = out["time_series_spread"]["rows"]
        bespoke_dates = [r["date"] for r in bespoke]
        canon_dates = [r["date"] for r in canon_s]
        assert bespoke_dates == canon_dates
        for b, c in zip(bespoke, canon_s):
            assert b["spread_value_pct"] == c["value"]

    def test_methodology_disclosure_present_and_required(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT",
            curve_family_b="SONIA_FUT",
            strip_position=1,
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
            FuturesCrossMarketSpreadOutput.model_validate(bad)

    def test_methodology_disclosure_mentions_required_caveats(self):
        """P5 / ADR 0013 + catalog guardrails MUST mention the wire-
        frozen A − B sign convention with specific labels, the
        per-leg regime labels, the RAW-differential label, the pack-
        average refusal, the inverse-pricing rule, AND the meeting-
        by-meeting / CTD scope-limits."""
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT",
            curve_family_b="SONIA_FUT",
            strip_position=1,
            lookback_days=365,
        )
        out = _run(params)
        disclosure = out["methodology_disclosure"]
        # Wire-frozen orientation
        assert "WIRE-FROZEN" in disclosure or "wire-frozen" in disclosure
        assert "A minus B" in disclosure or "A − B" in disclosure
        # A and B specific labels echoed back
        assert "SOFR_FUT" in disclosure
        assert "SONIA_FUT" in disclosure
        # Regime labels (BOTH legs are RFR here so the same-regime
        # branch fires)
        assert "RFR" in disclosure
        # Inverse-pricing rule
        assert "implied_rate_pct = 100 - raw_price" in disclosure
        # Z window
        assert "252" in disclosure
        # RAW-differential + planned-extension labels
        assert "RAW" in disclosure
        assert "basis-adjusted" in disclosure
        assert "beta-adjusted" in disclosure
        assert "PR11" in disclosure
        # Pack-average refusal
        assert "pack-average" in disclosure
        # CTD + meeting-by-meeting scope-limits
        assert "CTD" in disclosure
        assert "meeting-by-meeting" in disclosure
        assert "ADR 0013" in disclosure

    def test_spread_label_uses_contract_codes(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT",
            curve_family_b="SONIA_FUT",
            strip_position=2,
            lookback_days=365,
        )
        out = _run(
            params,
            reference_a=_synthetic_reference(
                curve_family="SOFR_FUT", contract_code="SFR2",
                strip_position=2,
            ),
            reference_b=_synthetic_reference(
                curve_family="SONIA_FUT", contract_code="SFI2",
                strip_position=2,
            ),
        )
        cm = out["current_metrics"]
        assert cm["spread_label"] == "SFR2-SFI2"


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def test_z_score_window_override_changes_z(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=1, lookback_days=365,
        )
        out_default = _run(params, config=_custom_config())
        out_short = _run(
            params, config=_custom_config(z_score_window_days=120),
        )
        assert (
            out_default["current_metrics"]["z_score_spread"]
            != out_short["current_metrics"]["z_score_spread"]
        )

    def test_z_score_ddof_override_changes_z(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=1, lookback_days=365,
        )
        out_sample = _run(params, config=_custom_config(z_score_ddof=1))
        out_pop = _run(params, config=_custom_config(z_score_ddof=0))
        assert (
            out_sample["current_metrics"]["z_score_spread"]
            != out_pop["current_metrics"]["z_score_spread"]
        )

    def test_daily_change_offset_override_changes_change_value(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=1, lookback_days=365,
        )
        out_default = _run(params, config=_custom_config())
        out_wider = _run(
            params, config=_custom_config(daily_change_offset_rows=5),
        )
        assert (
            out_default["current_metrics"]["daily_change_spread_value_pct"]
            != out_wider["current_metrics"]["daily_change_spread_value_pct"]
        )

    def test_spread_round_decimals_propagates_to_time_series(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=1, lookback_days=365,
        )
        out = _run(
            params, config=_custom_config(spread_value_round_decimals=2),
        )
        for row in out["time_series"]:
            assert row["spread_value_pct"] == round(
                row["spread_value_pct"], 2,
            )
        assert (
            out["current_metrics"]["spread_value_pct"]
            == out["time_series"][-1]["spread_value_pct"]
        )


# ===========================================================================
# 4. Honest-placeholder guard on trailing_range_window_days
# ===========================================================================

class TestTrailingWindowGuard:
    def test_unsupported_trailing_window_raises(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=1, lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            _run(
                params,
                config=_custom_config(trailing_range_window_days=180),
            )
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_spread_value_pct" in msg
        assert "planned_extensions" in msg

    def test_supported_default_does_not_raise(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=1, lookback_days=365,
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
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=1,
        )
        assert params.field_name is None

    def test_as_of_date_default_is_none(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=1,
        )
        assert params.as_of_date is None

    def test_strip_position_lower_bound_enforced(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            FuturesCrossMarketSpreadInput(
                curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
                strip_position=0,
            )

    def test_strip_position_upper_bound_enforced(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            FuturesCrossMarketSpreadInput(
                curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
                strip_position=13,
            )

    def test_same_curve_family_rejected(self):
        """Cross-field invariant: curve_family_a != curve_family_b."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc:
            FuturesCrossMarketSpreadInput(
                curve_family_a="SOFR_FUT",
                curve_family_b="SOFR_FUT",
                strip_position=1,
            )
        msg = str(exc.value).lower()
        assert "different" in msg or "must" in msg


class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_price_field reaches fetch."""

    def _capture_field_name(self, params, config):
        with patch(
            f"{_TARGET}.fetch_cross_market_strip",
            return_value=_synthetic_cross_market_df(
                curve_family_a=params.curve_family_a,
                curve_family_b=params.curve_family_b,
            ),
        ) as spy, patch(
            f"{_TARGET}.fetch_strip_position_reference",
            side_effect=lambda *, engine, curve_family, strip_position, as_of_date: (
                _synthetic_reference(
                    curve_family=curve_family,
                    contract_code=f"{curve_family[:3]}{strip_position}",
                    strip_position=strip_position,
                )
            ),
        ), patch(
            f"{_TARGET}.fetch_strip_position_max_date",
            return_value=None,
        ), patch(
            f"{_TARGET}.date", _FrozenDate,
        ):
            calculate_futures_cross_market_spread(
                engine=None, params=params, config=config,
            )
        assert spy.call_count == 1
        return spy.call_args.kwargs["field_name"]

    def test_omitted_field_name_uses_yaml_default(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=1,
        )
        passed = self._capture_field_name(
            params, _custom_config(default_price_field="PX_LAST"),
        )
        assert passed == "PX_LAST"

    def test_yaml_override_changes_resolved_field(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=1,
        )
        passed = self._capture_field_name(
            params, _custom_config(default_price_field="PX_BID"),
        )
        assert passed == "PX_BID"

    def test_explicit_field_name_overrides_yaml(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=1, field_name="PX_ASK",
        )
        passed = self._capture_field_name(
            params, _custom_config(default_price_field="PX_LAST"),
        )
        assert passed == "PX_ASK"


# ===========================================================================
# 6. Inverse-pricing rule is metadata-driven (PR8 / P6)
# ===========================================================================

class TestInversePricingMetadataDriven:
    def test_per_leg_inverse_flag_drives_conversion(self):
        """Flipping each per-leg metadata flag (NOT a hardcoded list
        in compute) changes the per-leg implied rate and therefore
        the spread."""
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=1, lookback_days=365,
        )
        out_inv = _run(params)
        out_direct = _run(
            params,
            reference_a=_synthetic_reference(
                curve_family="SOFR_FUT", contract_code="SFR1",
                strip_position=1, inverse_pricing=False,
            ),
            reference_b=_synthetic_reference(
                curve_family="SONIA_FUT", contract_code="SFI1",
                strip_position=1, inverse_pricing=False,
            ),
        )
        assert (
            out_inv["current_metrics"]["spread_value_pct"]
            != out_direct["current_metrics"]["spread_value_pct"]
        )
        # The per-leg implied rates should also differ across the
        # branches.
        assert (
            out_inv["current_metrics"]["implied_rate_pct_a"]
            != out_direct["current_metrics"]["implied_rate_pct_a"]
        )

    def test_missing_inverse_flag_leg_a_returns_error_envelope(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=1, lookback_days=365,
        )
        ref_a_bad = _synthetic_reference(
            curve_family="SOFR_FUT", contract_code="SFR1",
            strip_position=1,
        )
        del ref_a_bad["inverse_pricing"]
        out = _run(
            params,
            reference_a=ref_a_bad,
            reference_b=_synthetic_reference(
                curve_family="SONIA_FUT", contract_code="SFI1",
                strip_position=1,
            ),
        )
        assert "error" in out
        assert "inverse_pricing" in out["error"]
        assert "leg A" in out["error"]

    def test_missing_inverse_flag_leg_b_returns_error_envelope(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=1, lookback_days=365,
        )
        ref_b_bad = _synthetic_reference(
            curve_family="SONIA_FUT", contract_code="SFI1",
            strip_position=1,
        )
        del ref_b_bad["inverse_pricing"]
        out = _run(
            params,
            reference_a=_synthetic_reference(
                curve_family="SOFR_FUT", contract_code="SFR1",
                strip_position=1,
            ),
            reference_b=ref_b_bad,
        )
        assert "error" in out
        assert "inverse_pricing" in out["error"]
        assert "leg B" in out["error"]

    def test_mixed_inverse_flags_supported_independently(self):
        """Unlike the same-curve calendar_spread / butterfly_simple
        tools (which REFUSE mixed flags because all legs share one
        curve_family), the cross-market spread reads each leg's
        flag INDEPENDENTLY and proceeds — a future direct-priced
        family pairing with an inverse-priced family integrates
        without code changes. (In V1 all three universe members
        happen to share the flag.)"""
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=1, lookback_days=365,
        )
        out = _run(
            params,
            reference_a=_synthetic_reference(
                curve_family="SOFR_FUT", contract_code="SFR1",
                strip_position=1, inverse_pricing=True,
            ),
            reference_b=_synthetic_reference(
                curve_family="SONIA_FUT", contract_code="SFI1",
                strip_position=1, inverse_pricing=False,
            ),
        )
        # No refusal: per-leg metadata is honoured independently.
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["inverse_priced_a"] is True
        assert cm["inverse_priced_b"] is False


# ===========================================================================
# 7. Future-anchor guard (PR8 + PR16) — per leg
# ===========================================================================

class TestFutureAnchorGuard:
    def test_as_of_beyond_leg_a_max_returns_error(self):
        """Per-leg future-anchor guard: the binding (more restrictive)
        leg gates the controlled error."""
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=1, lookback_days=365,
            as_of_date=date(2030, 1, 1),
        )
        # Both legs share the same universe_max in this synthetic
        # setup; either could fire — what matters is the controlled-
        # error envelope is returned, names the as_of_date, and the
        # universe max, and skips the snapshot.
        out = _run(params, universe_max_date=date(2026, 4, 8))
        assert "error" in out
        assert "no scoreable strip" in out["error"]
        assert "2030-01-01" in out["error"]
        assert "2026-04-08" in out["error"]
        assert "current_metrics" not in out
        assert "time_series" not in out

    def test_as_of_within_universe_proceeds(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=1, lookback_days=365,
            as_of_date=date(2026, 4, 8),
        )
        out = _run(params, universe_max_date=date(2026, 4, 30))
        assert "error" not in out, out.get("error")
        assert "current_metrics" in out

    def test_as_of_none_skips_guard(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=1, lookback_days=365,
        )
        out = _run(params, universe_max_date=date(1900, 1, 1))
        assert "error" not in out, out.get("error")


# ===========================================================================
# 8. Reference miss + missing regime label → error envelope
# ===========================================================================

class TestErrorEnvelopes:
    def test_missing_reference_a_returns_error(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=11, lookback_days=365,
        )
        out = _run(params, reference_a=None)
        assert "error" in out
        assert "curve_family_a" in out["error"]

    def test_missing_reference_b_returns_error(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=11, lookback_days=365,
        )
        out = _run(params, reference_b=None)
        assert "error" in out
        assert "curve_family_b" in out["error"]

    def test_missing_regime_label_a_returns_error(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="UNKNOWN_FUT", curve_family_b="SONIA_FUT",
            strip_position=1, lookback_days=365,
        )
        out = _run(
            params,
            reference_a=_synthetic_reference(
                curve_family="UNKNOWN_FUT", contract_code="UNK1",
                strip_position=1,
            ),
            reference_b=_synthetic_reference(
                curve_family="SONIA_FUT", contract_code="SFI1",
                strip_position=1,
            ),
        )
        assert "error" in out
        assert "short_rate_regime_map" in out["error"]
        assert "curve_family_a" in out["error"]

    def test_missing_regime_label_b_returns_error(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="UNKNOWN_FUT",
            strip_position=1, lookback_days=365,
        )
        out = _run(
            params,
            reference_a=_synthetic_reference(
                curve_family="SOFR_FUT", contract_code="SFR1",
                strip_position=1,
            ),
            reference_b=_synthetic_reference(
                curve_family="UNKNOWN_FUT", contract_code="UNK1",
                strip_position=1,
            ),
        )
        assert "error" in out
        assert "short_rate_regime_map" in out["error"]
        assert "curve_family_b" in out["error"]

    def test_empty_price_history_returns_error(self):
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=1, lookback_days=365,
        )
        empty_df = pd.DataFrame(
            columns=["trade_date", "curve_family", "field_value"],
        )
        out = _run(params, price_df=empty_df)
        assert "error" in out

    def test_missing_one_leg_returns_error(self):
        """If the cross-market-strip fetch returns rows for only ONE
        leg, the controlled-error envelope must name the missing
        curve_family."""
        params = FuturesCrossMarketSpreadInput(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
            strip_position=1, lookback_days=365,
        )
        single_leg_df = _synthetic_cross_market_df(
            curve_family_a="SOFR_FUT", curve_family_b="SONIA_FUT",
        )
        single_leg_df = single_leg_df[
            single_leg_df["curve_family"] == "SOFR_FUT"
        ].reset_index(drop=True)
        out = _run(params, price_df=single_leg_df)
        assert "error" in out
        assert "SONIA_FUT" in out["error"] or "Missing curve_family" in out["error"]


# ===========================================================================
# 9. Import path discipline
# ===========================================================================

class TestImportPathBackwardCompat:
    def test_calculate_via_package_init(self):
        from rates_agent.policy_futures.tools.futures_cross_market_spread import (
            calculate_futures_cross_market_spread as via_package,
        )
        from rates_agent.policy_futures.tools.futures_cross_market_spread.compute import (
            calculate_futures_cross_market_spread as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.policy_futures.tools.futures_cross_market_spread import (
            FuturesCrossMarketSpreadInput as via_package,
        )
        from rates_agent.policy_futures.tools.futures_cross_market_spread.schemas import (
            FuturesCrossMarketSpreadInput as via_schemas,
        )
        from rates_agent.policy_futures.tools.schemas import (
            FuturesCrossMarketSpreadInput as via_hub,
        )
        assert via_package is via_schemas is via_hub

    def test_output_schema_via_three_paths(self):
        from rates_agent.policy_futures.tools.futures_cross_market_spread import (
            FuturesCrossMarketSpreadOutput as via_package,
        )
        from rates_agent.policy_futures.tools.futures_cross_market_spread.schemas import (
            FuturesCrossMarketSpreadOutput as via_schemas,
        )
        from rates_agent.policy_futures.tools.schemas import (
            FuturesCrossMarketSpreadOutput as via_hub,
        )
        assert via_package is via_schemas is via_hub
