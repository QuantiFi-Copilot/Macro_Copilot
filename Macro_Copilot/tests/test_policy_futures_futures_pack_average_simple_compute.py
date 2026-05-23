"""
test_policy_futures_futures_pack_average_simple_compute.py — Unit
tests for the policy-futures pack-average monitor.

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. compute() runs end-to-end against synthetic input with the
     bundled config and returns a well-formed output.
  3. Convention overrides actually change behaviour (z-window,
     ddof, ffill, daily_change_offset_rows, default_price_field,
     rounding decimals).
  4. The honest placeholder for trailing_range_window_days: setting
     it to anything other than 252 raises NotImplementedError.
  5. Schema-layer behaviour: field_name defaults to None
     (sentinel), compute() resolves the sentinel against the YAML's
     default_price_field. as_of_date defaults to None.
  6. Inverse-pricing rule is metadata-driven (PR8 / P6): the
     per-leg ``inverse_pricing`` flag on the reference dict — NOT a
     hardcoded list in compute — drives the implied-rate pack
     average. All four legs must agree on the flag.
  7. Future-anchor guard: as_of_date beyond the universe max on
     ANY leg returns the controlled-error envelope.
  8. Per-leg reference metadata flows through to the snapshot.
  9. Methodology disclosure is present, required, and mentions the
     arithmetic-mean weighting + regime + conversion rule +
     lookback window + scope-limit caveats.
 10. Arithmetic-mean formula: pack_average_implied_rate_pct = mean
     of the four per-leg implied rates.
 11. EUR_SHORT_RATE_FUT refusal: ADR 0011 V1 gate raises
     NotImplementedError with delivery_month_type + ADR 0011 in the
     message.

Tests are fully offline.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.policy_futures.tools.futures_pack_average_simple import (
    CONFIG_PATH,
    FuturesPackAverageSimpleInput,
    FuturesPackAverageSimpleOutput,
    calculate_futures_pack_average_simple,
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
    strip_positions=(1, 2, 3, 4),
    starts=(96.5, 96.3, 96.1, 95.9),
    drifts=(0.5, 0.7, 0.9, 1.1),
    days: int = 400,
    frozen_today: date = date(2026, 4, 30),
    add_oscillation: bool = True,
) -> pd.DataFrame:
    """Build a four-leg long-format DataFrame matching
    ``fetch_strip_group`` output.

    Defaults are tuned so the per-leg implied rates differ on every
    bar — the pack average has non-degenerate movement so daily-
    change / z-score / direct-vs-inverse-sign tests can distinguish
    branches.
    """
    bdays = pd.bdate_range(
        frozen_today - timedelta(days=days * 2), frozen_today,
    )
    bdays = bdays[-days:]
    n = len(bdays)

    parts = []
    for i, (pos, start, drift) in enumerate(zip(strip_positions, starts, drifts)):
        series = np.linspace(start, start + drift, n)
        if add_oscillation:
            # Per-leg sinusoidal kick at distinct phases so the pack
            # average is not a strict linear function.
            series = series + 0.05 * np.sin(
                np.linspace(0, 6 * np.pi, n) + i * np.pi / 4
            )
        parts.append(pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "strip_position": pos,
            "field_value": series,
        }))
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


class _FrozenDate(date):
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


_UNSET = object()


_TARGET = (
    "rates_agent.policy_futures.tools.futures_pack_average_simple.compute"
)


def _whites_positions() -> tuple[int, ...]:
    return (1, 2, 3, 4)


def _reds_positions() -> tuple[int, ...]:
    return (5, 6, 7, 8)


def _default_references(
    *,
    curve_family: str = "SOFR_FUT",
    contract_prefix: str = "SFR",
    positions: tuple[int, ...] = (1, 2, 3, 4),
    inverse_pricing: bool = True,
) -> dict[int, dict]:
    return {
        pos: _synthetic_reference(
            curve_family=curve_family,
            contract_code=f"{contract_prefix}{pos}",
            strip_position=pos,
            inverse_pricing=inverse_pricing,
            underlying_contract_code=f"{contract_prefix}U{pos}",
            security_name=f"{contract_prefix}U{pos} COMB",
            expiry=date(2026, 6, 16) + timedelta(days=90 * (pos - 1)),
        )
        for pos in positions
    }


def _run(
    params,
    *,
    price_df=None,
    references: dict[int, dict] | None = None,
    drop_inverse_for_position: int | None = None,
    flip_inverse_for_position: int | None = None,
    drop_reference_for_position: int | None = None,
    config=None,
    universe_max_date=None,
):
    positions = (
        _whites_positions() if params.pack == "whites" else _reds_positions()
    )
    if price_df is None:
        price_df = _synthetic_strip_group_df(strip_positions=positions)

    if references is None:
        contract_prefix = {
            "SOFR_FUT": "SFR",
            "SONIA_FUT": "SFI",
            "EUR_SHORT_RATE_FUT": "ER",
        }.get(params.curve_family, "STR")
        references = _default_references(
            curve_family=params.curve_family,
            contract_prefix=contract_prefix,
            positions=positions,
        )

    if drop_inverse_for_position is not None:
        ref_copy = dict(references[drop_inverse_for_position])
        ref_copy.pop("inverse_pricing", None)
        references = {**references, drop_inverse_for_position: ref_copy}

    if flip_inverse_for_position is not None:
        ref_copy = dict(references[flip_inverse_for_position])
        ref_copy["inverse_pricing"] = not ref_copy["inverse_pricing"]
        references = {**references, flip_inverse_for_position: ref_copy}

    if drop_reference_for_position is not None:
        references = {
            k: v for k, v in references.items()
            if k != drop_reference_for_position
        }

    def _side(*, engine, curve_family, strip_position, as_of_date):
        return references.get(strip_position)

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
        return calculate_futures_pack_average_simple(
            engine=None, params=params, config=config,
        )


def _custom_config(**overrides) -> ToolConfig:
    defaults = {
        "whites_strip_positions": "1,2,3,4",
        "reds_strip_positions": "5,6,7,8",
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
        "pack_average_round_decimals": 4,
        "implied_rate_round_decimals": 4,
        "z_score_round_decimals": 4,
        "pack_average_high_low_round_decimals": 4,
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
            "policy_futures_get_futures_pack_average_simple_tool"
        )
        assert cfg.tool.domain == "policy_futures"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "whites_strip_positions",
            "reds_strip_positions",
            "z_score_window_days",
            "z_score_min_periods",
            "z_score_ddof",
            "z_score_buffer_multiplier",
            "daily_change_offset_rows",
            "trailing_range_window_days",
            "ffill_limit_days",
            "default_price_field",
            "short_rate_regime_map",
            "pack_average_round_decimals",
            "implied_rate_round_decimals",
            "z_score_round_decimals",
            "pack_average_high_low_round_decimals",
            "percentile_round_decimals",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("whites_strip_positions") == "1,2,3,4"
        assert cfg.convention_value("reds_strip_positions") == "5,6,7,8"
        assert cfg.convention_value("z_score_window_days") == 252
        assert cfg.convention_value("z_score_min_periods") == 60
        assert cfg.convention_value("z_score_ddof") == 1
        assert cfg.convention_value("trailing_range_window_days") == 252
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("default_price_field") == "PX_LAST"
        regime_csv = cfg.convention_value("short_rate_regime_map")
        assert "SOFR_FUT=RFR" in regime_csv
        assert "SONIA_FUT=RFR" in regime_csv
        assert "EUR_SHORT_RATE_FUT=IBOR" in regime_csv
        assert cfg.convention_value("pack_average_round_decimals") == 4
        assert cfg.convention_value("implied_rate_round_decimals") == 4
        assert cfg.convention_value("z_score_round_decimals") == 4
        assert cfg.convention_value(
            "pack_average_high_low_round_decimals"
        ) == 4
        assert cfg.convention_value("percentile_round_decimals") == 1

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 2
        joined = " | ".join(cfg.methodology.planned_extensions)
        # The EUR Buba-mix unblock entry MUST name the missing
        # delivery_month_type playbook metadata.
        assert "delivery_month_type" in joined
        assert "EUR_SHORT_RATE_FUT" in joined or "Euribor" in joined
        # Duration-weighted / DV01-weighted variants documented as
        # planned extensions per catalog guardrails.
        assert (
            "DV01" in joined
            or "duration-weighted" in joined.lower()
            or "Duration-weighted" in joined
        )


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
            lookback_days=365,
        )
        out = _run(params)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        for k in (
            "as_of_date", "curve_family", "pack", "strip_positions",
            "pack_label",
            "contract_codes", "underlying_contract_codes",
            "security_names", "expiry_dates",
            "inverse_priced", "short_rate_regime",
            "implied_rates_pct",
            "pack_average_implied_rate_pct",
            "daily_change_pack_average_implied_rate_pct",
            "z_score_pack_average",
            "high_252d_pack_average_implied_rate_pct",
            "low_252d_pack_average_implied_rate_pct",
            "mid_252d_pack_average_implied_rate_pct",
            "percentile_252d",
            "rolling_window_days",
            "observation_count",
        ):
            assert k in cm, f"missing {k}"

        # PR14-frozen field-name discipline — guard against silent
        # renames.
        assert "pack_average_bps" not in cm
        assert "implied_rate_pct" in str(
            cm["pack_average_implied_rate_pct"]
        ) or isinstance(
            cm["pack_average_implied_rate_pct"], float
        )

        assert isinstance(cm["pack_average_implied_rate_pct"], float)
        assert cm["inverse_priced"] is True
        assert cm["short_rate_regime"] == "RFR"
        assert cm["rolling_window_days"] == 252
        assert cm["strip_positions"] == [1, 2, 3, 4]
        assert len(cm["contract_codes"]) == 4
        assert len(cm["implied_rates_pct"]) == 4

        # Bespoke time_series shape: list of {date,
        # pack_average_implied_rate_pct, z_score}.
        ts = out["time_series"]
        assert isinstance(ts, list)
        assert len(ts) > 0
        assert "date" in ts[0]
        assert "pack_average_implied_rate_pct" in ts[0]
        assert "z_score" in ts[0]

        # Canonical TimeSeries shapes
        tspa = out["time_series_pack_average"]
        assert tspa["units"] == "percent"
        assert tspa["series_name"] == "sofr_fut_whites_pack_average"
        assert len(tspa["rows"]) > 0
        tsz = out["time_series_zscore"]
        assert tsz["units"] == "z_score"
        assert tsz["series_name"] == "sofr_fut_whites_zscore"

    def test_pack_average_formula_arithmetic_mean(self):
        """pack_average_implied_rate_pct = mean of the four per-leg
        implied_rate_pct values."""
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
            lookback_days=365,
        )
        out = _run(params)
        cm = out["current_metrics"]
        expected = round(sum(cm["implied_rates_pct"]) / 4.0, 4)
        assert abs(expected - cm["pack_average_implied_rate_pct"]) < 1e-4

    def test_reds_pack_uses_positions_5_through_8(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="reds",
            lookback_days=365,
        )
        out = _run(params)
        assert "error" not in out
        cm = out["current_metrics"]
        assert cm["pack"] == "reds"
        assert cm["strip_positions"] == [5, 6, 7, 8]
        # contract_codes follow the SFR<n> stems for SOFR_FUT
        assert cm["contract_codes"] == ["SFR5", "SFR6", "SFR7", "SFR8"]

    def test_direct_pricing_changes_pack_sign(self):
        """Flipping inverse_pricing changes the pack-average value
        because each per-leg series goes from `100 - price` to
        `price`."""
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
            lookback_days=365,
        )
        refs_inverse = _default_references(
            curve_family="SOFR_FUT",
            contract_prefix="SFR",
            positions=(1, 2, 3, 4),
            inverse_pricing=True,
        )
        refs_direct = _default_references(
            curve_family="SOFR_FUT",
            contract_prefix="SFR",
            positions=(1, 2, 3, 4),
            inverse_pricing=False,
        )
        out_inv = _run(params, references=refs_inverse)
        out_dir = _run(params, references=refs_direct)
        assert out_inv["current_metrics"]["inverse_priced"] is True
        assert out_dir["current_metrics"]["inverse_priced"] is False
        # Inverse pack ≈ 100 - direct pack (per-leg negation
        # propagates linearly through the mean).
        assert abs(
            (
                out_inv["current_metrics"]["pack_average_implied_rate_pct"]
                + out_dir["current_metrics"]["pack_average_implied_rate_pct"]
            )
            - 100.0
        ) < 1e-3

    def test_explicit_config_matches_auto_loaded(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
            lookback_days=365,
        )
        out_auto = _run(params, config=None)
        out_explicit = _run(params, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit

    def test_per_leg_reference_metadata_flows_to_snapshot(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SONIA_FUT",
            pack="whites",
            lookback_days=365,
        )
        out = _run(params)
        cm = out["current_metrics"]
        assert cm["curve_family"] == "SONIA_FUT"
        assert cm["short_rate_regime"] == "RFR"
        assert cm["contract_codes"] == ["SFI1", "SFI2", "SFI3", "SFI4"]
        # Every leg's underlying contract / security / expiry is
        # surfaced.
        assert all(
            uc is not None for uc in cm["underlying_contract_codes"]
        )
        assert all(sn is not None for sn in cm["security_names"])
        assert all(ed is not None for ed in cm["expiry_dates"])

    def test_snapshot_equals_time_series_last_row_strictly(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
            lookback_days=365,
        )
        out = _run(params)
        last_row = out["time_series"][-1]
        cm = out["current_metrics"]
        assert (
            last_row["pack_average_implied_rate_pct"]
            == cm["pack_average_implied_rate_pct"]
        )
        if last_row["z_score"] is not None:
            assert last_row["z_score"] == cm["z_score_pack_average"]

    def test_canonical_time_series_matches_bespoke(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
            lookback_days=365,
        )
        out = _run(params)
        bespoke = out["time_series"]
        canon = out["time_series_pack_average"]["rows"]
        bespoke_dates = [r["date"] for r in bespoke]
        canon_dates = [r["date"] for r in canon]
        assert bespoke_dates == canon_dates
        for b, c in zip(bespoke, canon):
            assert b["pack_average_implied_rate_pct"] == c["value"]

    def test_methodology_disclosure_present_and_required(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
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
            FuturesPackAverageSimpleOutput.model_validate(bad)

    def test_methodology_disclosure_mentions_required_caveats(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
            lookback_days=365,
        )
        out = _run(params)
        disclosure = out["methodology_disclosure"]
        # Weighting + arithmetic mean
        assert "arithmetic" in disclosure.lower()
        assert "0.25" in disclosure or "1/4" in disclosure
        # Regime + inverse pricing
        assert "RFR" in disclosure
        assert "implied_rate_pct = 100 - raw_price" in disclosure
        # Z window
        assert "252" in disclosure
        # Scope refusals
        assert "CTD" in disclosure
        assert "meeting-by-meeting" in disclosure
        assert (
            "duration-weighted" in disclosure.lower()
            or "DV01" in disclosure
        )
        assert "ADR 0011" in disclosure


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def test_z_score_window_override_changes_z(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
            lookback_days=365,
        )
        out_default = _run(params, config=_custom_config())
        out_short = _run(
            params, config=_custom_config(z_score_window_days=120),
        )
        assert (
            out_default["current_metrics"]["z_score_pack_average"]
            != out_short["current_metrics"]["z_score_pack_average"]
        )

    def test_z_score_ddof_override_changes_z(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
            lookback_days=365,
        )
        out_sample = _run(params, config=_custom_config(z_score_ddof=1))
        out_pop = _run(params, config=_custom_config(z_score_ddof=0))
        assert (
            out_sample["current_metrics"]["z_score_pack_average"]
            != out_pop["current_metrics"]["z_score_pack_average"]
        )

    def test_daily_change_offset_override_changes_change_value(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
            lookback_days=365,
        )
        out_default = _run(params, config=_custom_config())
        out_wider = _run(
            params, config=_custom_config(daily_change_offset_rows=5),
        )
        assert (
            out_default["current_metrics"][
                "daily_change_pack_average_implied_rate_pct"
            ]
            != out_wider["current_metrics"][
                "daily_change_pack_average_implied_rate_pct"
            ]
        )

    def test_pack_average_round_decimals_propagates(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
            lookback_days=365,
        )
        out = _run(
            params,
            config=_custom_config(pack_average_round_decimals=2),
        )
        for row in out["time_series"]:
            assert (
                row["pack_average_implied_rate_pct"]
                == round(row["pack_average_implied_rate_pct"], 2)
            )
        assert (
            out["current_metrics"]["pack_average_implied_rate_pct"]
            == out["time_series"][-1]["pack_average_implied_rate_pct"]
        )


# ===========================================================================
# 4. Honest-placeholder guard on trailing_range_window_days
# ===========================================================================

class TestTrailingWindowGuard:
    def test_unsupported_trailing_window_raises(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
            lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            _run(
                params,
                config=_custom_config(trailing_range_window_days=180),
            )
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_pack_average_implied_rate_pct" in msg
        assert "planned_extensions" in msg

    def test_supported_default_does_not_raise(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
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
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
        )
        assert params.field_name is None

    def test_as_of_date_default_is_none(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
        )
        assert params.as_of_date is None

    def test_unknown_curve_family_rejected_at_schema_layer(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            FuturesPackAverageSimpleInput(
                curve_family="UNKNOWN_FUT",
                pack="whites",
            )

    def test_unknown_pack_rejected_at_schema_layer(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            FuturesPackAverageSimpleInput(
                curve_family="SOFR_FUT",
                pack="greens",
            )


class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_price_field reaches
    fetch."""

    def _capture_field_name(self, params, config):
        positions = (
            _whites_positions() if params.pack == "whites"
            else _reds_positions()
        )
        refs = _default_references(
            curve_family=params.curve_family,
            contract_prefix="SFR",
            positions=positions,
        )

        def _side(*, engine, curve_family, strip_position, as_of_date):
            return refs.get(strip_position)

        with patch(
            f"{_TARGET}.fetch_strip_group",
            return_value=_synthetic_strip_group_df(
                strip_positions=positions,
            ),
        ) as spy, patch(
            f"{_TARGET}.fetch_strip_position_reference",
            side_effect=_side,
        ), patch(
            f"{_TARGET}.fetch_strip_position_max_date",
            return_value=None,
        ), patch(
            f"{_TARGET}.date", _FrozenDate,
        ):
            calculate_futures_pack_average_simple(
                engine=None, params=params, config=config,
            )
        assert spy.call_count == 1
        return spy.call_args.kwargs["field_name"]

    def test_omitted_field_name_uses_yaml_default(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT", pack="whites",
        )
        passed = self._capture_field_name(
            params, _custom_config(default_price_field="PX_LAST"),
        )
        assert passed == "PX_LAST"

    def test_yaml_override_changes_resolved_field(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT", pack="whites",
        )
        passed = self._capture_field_name(
            params, _custom_config(default_price_field="PX_BID"),
        )
        assert passed == "PX_BID"

    def test_explicit_field_name_overrides_yaml(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT", pack="whites", field_name="PX_ASK",
        )
        passed = self._capture_field_name(
            params, _custom_config(default_price_field="PX_LAST"),
        )
        assert passed == "PX_ASK"

    def test_empty_string_sentinel_must_be_translated_externally(self):
        """The schema does NOT treat empty-string as None — that's
        the MCP wrapper's job. Confirms the input model preserves an
        empty string verbatim (the wiring test then asserts the
        wrapper translates it to None)."""
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT", pack="whites", field_name="",
        )
        # Empty string is preserved — the MCP wrapper must translate
        # before constructing the input. Documented in the schema.
        assert params.field_name == ""


# ===========================================================================
# 6. Inverse-pricing rule is metadata-driven (PR8 / P6)
# ===========================================================================

class TestInversePricingMetadataDriven:
    def test_missing_inverse_flag_returns_error_envelope(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
            lookback_days=365,
        )
        out = _run(params, drop_inverse_for_position=2)
        assert "error" in out
        assert "inverse_pricing" in out["error"]

    def test_mixed_inverse_flags_returns_error_envelope(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
            lookback_days=365,
        )
        out = _run(params, flip_inverse_for_position=3)
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
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
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
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
            lookback_days=365,
            as_of_date=date(2026, 4, 8),
        )
        out = _run(params, universe_max_date=date(2026, 4, 30))
        assert "error" not in out, out.get("error")
        assert "current_metrics" in out

    def test_as_of_none_skips_guard(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
            lookback_days=365,
        )
        out = _run(params, universe_max_date=date(1900, 1, 1))
        assert "error" not in out, out.get("error")


# ===========================================================================
# 8. Reference miss + missing regime label → error envelope
# ===========================================================================

class TestErrorEnvelopes:
    def test_missing_leg_reference_returns_error(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
            lookback_days=365,
        )
        out = _run(params, drop_reference_for_position=2)
        assert "error" in out
        assert "strip_position=2" in out["error"]

    def test_empty_price_history_returns_error(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
            lookback_days=365,
        )
        empty_df = pd.DataFrame(
            columns=["trade_date", "strip_position", "field_value"],
        )
        out = _run(params, price_df=empty_df)
        assert "error" in out

    def test_missing_one_leg_returns_error(self):
        """If the strip-group fetch returns rows for only THREE
        legs, the controlled-error envelope must name the missing
        leg."""
        params = FuturesPackAverageSimpleInput(
            curve_family="SOFR_FUT",
            pack="whites",
            lookback_days=365,
        )
        df = _synthetic_strip_group_df(strip_positions=(1, 2, 3, 4))
        df = df[df["strip_position"] != 3].reset_index(drop=True)
        out = _run(params, price_df=df)
        assert "error" in out
        assert (
            "[3]" in out["error"]
            or "Missing strip_position" in out["error"]
        )


# ===========================================================================
# 9. EUR_SHORT_RATE_FUT refusal (ADR 0011 V1)
# ===========================================================================

class TestEurShortRateFutRefusedPr11:
    """The Euribor strip mixes serial and quarterly contracts at the
    front; ``policy_futures.yml`` does not yet annotate per-row
    ``delivery_month_type``. The pack-average primitive REFUSES
    EUR_SHORT_RATE_FUT at compute time with NotImplementedError
    citing both the missing metadata and ADR 0011 — the canonical
    "honest until data lands" pattern."""

    def test_eur_short_rate_fut_refused_pr11(self):
        params = FuturesPackAverageSimpleInput(
            curve_family="EUR_SHORT_RATE_FUT",
            pack="whites",
            lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            _run(params)
        msg = str(exc_info.value)
        assert "EUR_SHORT_RATE_FUT" in msg
        # The refusal MUST name the missing playbook metadata so the
        # desk reader knows the unblock path.
        assert "delivery_month_type" in msg
        # AND it MUST cite ADR 0011 so the disclosure surface is
        # auditable.
        assert "ADR 0011" in msg

    def test_eur_short_rate_fut_refused_on_reds_too(self):
        """The refusal is per-curve_family, not per-pack — reds also
        hits the gate."""
        params = FuturesPackAverageSimpleInput(
            curve_family="EUR_SHORT_RATE_FUT",
            pack="reds",
            lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            _run(params)
        msg = str(exc_info.value)
        assert "EUR_SHORT_RATE_FUT" in msg
        assert "delivery_month_type" in msg

    def test_eur_refusal_does_not_touch_fetchers(self):
        """The refusal happens BEFORE any DB I/O — the gate is the
        first thing compute() does after curve_family checks. This
        test confirms no fetcher mock has to fire."""
        # Build a target patched to BLOW UP if called — confirms the
        # gate runs first.
        params = FuturesPackAverageSimpleInput(
            curve_family="EUR_SHORT_RATE_FUT",
            pack="whites",
            lookback_days=365,
        )
        sentinel = RuntimeError("fetcher should NOT be called")
        with patch(
            f"{_TARGET}.fetch_strip_group", side_effect=sentinel,
        ), patch(
            f"{_TARGET}.fetch_strip_position_reference",
            side_effect=sentinel,
        ), patch(
            f"{_TARGET}.fetch_strip_position_max_date",
            side_effect=sentinel,
        ):
            with pytest.raises(NotImplementedError):
                calculate_futures_pack_average_simple(
                    engine=None, params=params, config=None,
                )


# ===========================================================================
# 10. Import path discipline
# ===========================================================================

class TestImportPathBackwardCompat:
    def test_calculate_via_package_init(self):
        from rates_agent.policy_futures.tools.futures_pack_average_simple import (
            calculate_futures_pack_average_simple as via_package,
        )
        from rates_agent.policy_futures.tools.futures_pack_average_simple.compute import (
            calculate_futures_pack_average_simple as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.policy_futures.tools.futures_pack_average_simple import (
            FuturesPackAverageSimpleInput as via_package,
        )
        from rates_agent.policy_futures.tools.futures_pack_average_simple.schemas import (
            FuturesPackAverageSimpleInput as via_schemas,
        )
        from rates_agent.policy_futures.tools.schemas import (
            FuturesPackAverageSimpleInput as via_hub,
        )
        assert via_package is via_schemas is via_hub

    def test_output_schema_via_three_paths(self):
        from rates_agent.policy_futures.tools.futures_pack_average_simple import (
            FuturesPackAverageSimpleOutput as via_package,
        )
        from rates_agent.policy_futures.tools.futures_pack_average_simple.schemas import (
            FuturesPackAverageSimpleOutput as via_schemas,
        )
        from rates_agent.policy_futures.tools.schemas import (
            FuturesPackAverageSimpleOutput as via_hub,
        )
        assert via_package is via_schemas is via_hub
