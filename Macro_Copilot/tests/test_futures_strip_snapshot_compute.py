"""
test_futures_strip_snapshot_compute.py — Unit tests for the
                                          policy-futures whole-strip
                                          snapshot monitor

Covers (per the catalog v2.1 build_order 27 spec):
  1. Bundled config.yaml loads + required conventions present.
  2. compute() runs end-to-end against synthetic input with the
     bundled config and returns a well-formed output (one row per
     configured strip position, intersection-of-trading-days
     alignment, per-row methodology card).
  3. Explicit config vs auto-load parity.
  4. Implied-rate inversion: SFR-style sample at price=95.00 →
     implied_rate_pct=5.00; direct-quoted family at price=2.50 →
     implied_rate_pct=2.50 (metadata-driven via the per-leg
     ``inverse_pricing`` flag).
  5. Z-score sanity on synthetic data (rolling window respected).
  6. Δ across two synthetic trading days (raw subtraction on the
     implied-rate axis).
  7. Per-row methodology card present for every row + output-level
     ``methodology_disclosure`` carries the required caveats.
  8. Schema refusal: unknown ``curve_family`` raises a Pydantic
     validator error at the schema layer (NOT silently passed through
     to compute()).
  9. Exempt-snapshot output shape: rows = strip positions, each with
     (strip_position, contract_code, security_name, raw_price,
     implied_rate_pct, daily_change_implied_rate_pct,
     z_score_implied_rate, open_interest, row_methodology_card).
 10. YAML-drift guard: a config with ``strip_positions='1,2,3'``
     still works (honest truncation — the snapshot returns only those
     three rows). Mirrors the futures_butterfly_simple
     ``butterfly_weighting`` knob style of YAML-driven configuration.
 11. Mixed-flag ``inverse_pricing`` across strip positions returns
     the controlled-error envelope.
 12. Future-anchor guard: ``as_of_date`` beyond ANY leg's universe max
     returns the controlled-error envelope.
 13. Import path discipline + canonical schema re-export.

Tests are fully offline.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.policy_futures.tools.futures_strip_snapshot import (
    CONFIG_PATH,
    FuturesStripSnapshotInput,
    FuturesStripSnapshotOutput,
    calculate_futures_strip_snapshot,
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
    strip_positions=(1, 2, 3, 4, 5, 6, 7, 8),
    # Per-position price drift, default chosen so that the whole strip
    # is non-degenerate (each leg has a distinct path so the per-leg
    # z-score / 1-day-change tests can distinguish branches).
    base_prices=None,
    drifts=None,
    days: int = 400,
    frozen_today: date = date(2026, 4, 30),
) -> pd.DataFrame:
    """Build a long-format DataFrame matching the shape
    ``fetch_strip_group`` returns. One block per strip position,
    sorted by (trade_date, strip_position)."""
    bdays = pd.bdate_range(
        frozen_today - timedelta(days=days * 2), frozen_today,
    )
    bdays = bdays[-days:]
    n = len(bdays)

    if base_prices is None:
        # Front-loaded inverted-strip: high price (low rate) at front,
        # lower price (higher rate) further down the strip.
        base_prices = {
            p: 96.5 - 0.1 * (p - 1) for p in strip_positions
        }
    if drifts is None:
        drifts = {p: 0.05 + 0.02 * p for p in strip_positions}

    parts = []
    for p in strip_positions:
        series = np.linspace(base_prices[p], base_prices[p] + drifts[p], n)
        # Small per-leg sinusoidal kick so the rolling std is responsive
        # to ddof under different windows.
        series = series + 0.03 * np.sin(np.linspace(0, 4 * np.pi, n) + p)
        parts.append(pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "strip_position": p,
            "field_value": series,
        }))
    return pd.concat(parts, ignore_index=True).sort_values(
        ["trade_date", "strip_position"]
    ).reset_index(drop=True)


def _synthetic_oi_group_df(
    *,
    strip_positions=(1, 2, 3, 4, 5, 6, 7, 8),
    days: int = 400,
    frozen_today: date = date(2026, 4, 30),
    base_oi: int = 1_000_000,
) -> pd.DataFrame:
    """Synthetic OPEN_INT long-format DataFrame; whole-contract counts."""
    bdays = pd.bdate_range(
        frozen_today - timedelta(days=days * 2), frozen_today,
    )
    bdays = bdays[-days:]
    n = len(bdays)
    parts = []
    for p in strip_positions:
        # Per-leg OI step pattern; integer-valued.
        series = (
            base_oi
            - (p - 1) * 80_000
            + np.round(50_000 * np.sin(np.linspace(0, 3 * np.pi, n) + p))
        )
        parts.append(pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "strip_position": p,
            "field_value": series.astype(float),
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


_TARGET = "rates_agent.policy_futures.tools.futures_strip_snapshot.compute"


def _run(
    params,
    *,
    price_df=None,
    oi_df=None,
    references=None,
    config=None,
    universe_max_date=None,
    inverse_pricing=True,
):
    strip_positions = tuple(range(1, 9))
    if config is not None:
        csv_value = config.convention_value("strip_positions")
        strip_positions = tuple(
            int(x.strip()) for x in csv_value.split(",") if x.strip()
        )

    if price_df is None:
        price_df = _synthetic_strip_group_df(strip_positions=strip_positions)
    if oi_df is None:
        oi_df = _synthetic_oi_group_df(strip_positions=strip_positions)

    if references is None:
        def _side(*, engine, curve_family, strip_position, as_of_date):
            return _synthetic_reference(
                curve_family=curve_family,
                contract_code=f"STR{strip_position}",
                strip_position=strip_position,
                inverse_pricing=inverse_pricing,
                underlying_contract_code=(
                    f"STR{strip_position}M26"
                ),
                security_name=f"STR{strip_position}M26 COMB",
            )
        ref_side_effect = _side
    else:
        def _side(*, engine, curve_family, strip_position, as_of_date):
            return references.get(strip_position)
        ref_side_effect = _side

    # fetch_strip_group is called TWICE per compute(): once for price,
    # once for OI. We must return different frames per field_name.
    def _strip_group_side(*, engine, curve_family, strip_positions, field_name, start_date):
        if field_name in ("PX_LAST", "PX_BID", "PX_ASK"):
            return price_df
        return oi_df

    with patch(
        f"{_TARGET}.fetch_strip_group",
        side_effect=_strip_group_side,
    ), patch(
        f"{_TARGET}.fetch_strip_position_reference",
        side_effect=ref_side_effect,
    ), patch(
        f"{_TARGET}.fetch_strip_position_max_date",
        return_value=universe_max_date,
    ), patch(
        f"{_TARGET}.date",
        _FrozenDate,
    ):
        return calculate_futures_strip_snapshot(
            engine=None, params=params, config=config,
        )


def _custom_config(**overrides) -> ToolConfig:
    defaults = {
        "z_score_window_days": 252,
        "z_score_min_periods": 60,
        "z_score_ddof": 1,
        "z_score_buffer_multiplier": 1.5,
        "daily_change_offset_rows": 2,
        "ffill_limit_days": 5,
        "default_price_field": "PX_LAST",
        "default_open_interest_field": "OPEN_INT",
        "strip_positions": "1,2,3,4,5,6,7,8",
        "short_rate_regime_map": (
            "SOFR_FUT=RFR,SONIA_FUT=RFR,EUR_SHORT_RATE_FUT=IBOR"
        ),
        "raw_price_round_decimals": 5,
        "implied_rate_round_decimals": 4,
        "z_score_round_decimals": 4,
        "open_interest_round_decimals": 0,
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
            "policy_futures_get_futures_strip_snapshot_tool"
        )
        assert cfg.tool.domain == "policy_futures"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "z_score_window_days",
            "z_score_min_periods",
            "z_score_ddof",
            "z_score_buffer_multiplier",
            "daily_change_offset_rows",
            "ffill_limit_days",
            "default_price_field",
            "default_open_interest_field",
            "strip_positions",
            "short_rate_regime_map",
            "raw_price_round_decimals",
            "implied_rate_round_decimals",
            "z_score_round_decimals",
            "open_interest_round_decimals",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("z_score_window_days") == 252
        assert cfg.convention_value("z_score_min_periods") == 60
        assert cfg.convention_value("z_score_ddof") == 1
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("default_price_field") == "PX_LAST"
        assert cfg.convention_value("default_open_interest_field") == "OPEN_INT"
        # Default strip_positions = full V1 universe (positions 1..8).
        sp_csv = cfg.convention_value("strip_positions")
        assert sp_csv == "1,2,3,4,5,6,7,8"
        regime_csv = cfg.convention_value("short_rate_regime_map")
        assert "SOFR_FUT=RFR" in regime_csv
        assert "SONIA_FUT=RFR" in regime_csv
        assert "EUR_SHORT_RATE_FUT=IBOR" in regime_csv
        assert cfg.convention_value("raw_price_round_decimals") == 5
        assert cfg.convention_value("implied_rate_round_decimals") == 4
        assert cfg.convention_value("z_score_round_decimals") == 4
        assert cfg.convention_value("open_interest_round_decimals") == 0

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 2
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "CTD" in joined or "cross-CB" in joined.lower()


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        out = _run(params)
        assert "error" not in out, out.get("error")

        # Output-level fields
        for k in (
            "as_of_date", "curve_family", "inverse_priced",
            "short_rate_regime", "quote_units", "strip_positions",
            "snapshot", "observation_count", "methodology_disclosure",
        ):
            assert k in out, f"missing {k}"

        # One row per configured strip position; ordering matches
        # strip_positions list.
        assert out["strip_positions"] == [1, 2, 3, 4, 5, 6, 7, 8]
        assert len(out["snapshot"]) == 8
        for row, expected_position in zip(out["snapshot"], out["strip_positions"]):
            assert row["strip_position"] == expected_position

        # Output-level shared flag + regime.
        assert out["inverse_priced"] is True
        assert out["short_rate_regime"] == "RFR"
        assert out["quote_units"] == "100 - rate"

        # Per-row keys
        row0 = out["snapshot"][0]
        for k in (
            "strip_position", "contract_code", "underlying_contract_code",
            "security_name", "expiry_date", "contract_size",
            "raw_price", "implied_rate_pct",
            "daily_change_implied_rate_pct",
            "z_score_implied_rate", "open_interest",
            "row_methodology_card",
        ):
            assert k in row0, f"missing per-row {k}"

        # PR14-frozen field names — guard against silent renames.
        for row in out["snapshot"]:
            assert "implied_rate_pct" in row
            assert "implied_rate" not in row or "implied_rate_pct" in row
            assert "rate_pct" not in row or "implied_rate_pct" in row
            assert "implied_rate_percent" not in row

    def test_explicit_config_matches_auto_loaded(self):
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        out_auto = _run(params, config=None)
        out_explicit = _run(params, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit

    def test_inverse_priced_conversion_at_price_95(self):
        """SFR-style sample row at price=95.00 → implied_rate_pct=5.00.

        Build a synthetic price frame where every leg is constant at
        95.00; verify the snapshot's implied_rate_pct is 5.00 on
        every row (since inverse_pricing=True per default mock).
        """
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        # Build a price DataFrame where every strip position holds
        # price=95.00 across the entire window.
        bdays = pd.bdate_range(
            date(2026, 4, 30) - timedelta(days=800), date(2026, 4, 30),
        )[-400:]
        rows = []
        for p in range(1, 9):
            for d in bdays:
                rows.append({
                    "trade_date": d.date(),
                    "strip_position": p,
                    "field_value": 95.00,
                })
        flat_price_df = pd.DataFrame(rows)
        out = _run(params, price_df=flat_price_df)
        assert "error" not in out
        for row in out["snapshot"]:
            assert row["raw_price"] == pytest.approx(95.00, abs=1e-9)
            assert row["implied_rate_pct"] == pytest.approx(5.00, abs=1e-9)

    def test_direct_pricing_at_rate_2_5(self):
        """Direct-quoted family at price=2.50 → implied_rate_pct=2.50.

        Mock all references with inverse_pricing=False; verify the
        snapshot returns raw_price == implied_rate_pct on every row.
        """
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        bdays = pd.bdate_range(
            date(2026, 4, 30) - timedelta(days=800), date(2026, 4, 30),
        )[-400:]
        rows = []
        for p in range(1, 9):
            for d in bdays:
                rows.append({
                    "trade_date": d.date(),
                    "strip_position": p,
                    "field_value": 2.50,
                })
        flat_price_df = pd.DataFrame(rows)
        out = _run(params, price_df=flat_price_df, inverse_pricing=False)
        assert "error" not in out
        assert out["inverse_priced"] is False
        assert out["quote_units"] == "rate (%)"
        for row in out["snapshot"]:
            assert row["raw_price"] == pytest.approx(2.50, abs=1e-9)
            assert row["implied_rate_pct"] == pytest.approx(2.50, abs=1e-9)

    def test_inverse_priced_z_score_sanity_constant_series_is_none(self):
        """A constant series has zero stddev — the rolling z-score
        must return None per the rolling_zscore contract."""
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        bdays = pd.bdate_range(
            date(2026, 4, 30) - timedelta(days=800), date(2026, 4, 30),
        )[-400:]
        rows = []
        for p in range(1, 9):
            for d in bdays:
                rows.append({
                    "trade_date": d.date(),
                    "strip_position": p,
                    "field_value": 95.00,
                })
        out = _run(params, price_df=pd.DataFrame(rows))
        for row in out["snapshot"]:
            assert row["z_score_implied_rate"] is None

    def test_inverse_priced_daily_change_two_synthetic_days(self):
        """Build a price series where the LATEST trading day's price
        differs from the previous day's by a known amount; verify the
        snapshot's daily_change_implied_rate_pct equals -(price delta)
        for inverse-priced legs."""
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        bdays = pd.bdate_range(
            date(2026, 4, 30) - timedelta(days=800), date(2026, 4, 30),
        )[-400:]
        # Constant 95.00 for the first n-1 days; bump strip 1 to 95.25
        # on the last day. Implied rate goes from 5.00 -> 4.75; the
        # daily change should be -0.25.
        rows = []
        for p in range(1, 9):
            for i, d in enumerate(bdays):
                if p == 1 and i == len(bdays) - 1:
                    field_value = 95.25
                else:
                    field_value = 95.00
                rows.append({
                    "trade_date": d.date(),
                    "strip_position": p,
                    "field_value": field_value,
                })
        out = _run(params, price_df=pd.DataFrame(rows))
        assert "error" not in out
        strip1 = next(r for r in out["snapshot"] if r["strip_position"] == 1)
        # Implied rate moved from 5.00 to 4.75 → delta = -0.25
        assert strip1["daily_change_implied_rate_pct"] == pytest.approx(
            -0.25, abs=1e-4
        )
        # Every other strip unchanged → delta = 0.0
        for row in out["snapshot"]:
            if row["strip_position"] != 1:
                assert row["daily_change_implied_rate_pct"] == pytest.approx(
                    0.0, abs=1e-4
                )

    def test_per_row_methodology_card_present_on_every_row(self):
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        out = _run(params)
        for row in out["snapshot"]:
            card = row["row_methodology_card"]
            assert isinstance(card, str)
            assert len(card) > 40
            assert "SOFR_FUT" in card
            # Regime + conversion-rule keywords required.
            assert "RFR" in card or "IBOR" in card
            assert "implied_rate_pct" in card

    def test_output_level_methodology_disclosure(self):
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        out = _run(params)
        disclosure = out["methodology_disclosure"]
        assert isinstance(disclosure, str)
        assert len(disclosure) > 100
        # Required caveats per catalog
        assert "SOFR_FUT" in disclosure
        assert "RFR" in disclosure
        assert "100 - raw_price" in disclosure or "100 - rate" in disclosure
        assert "252" in disclosure
        assert "CTD" in disclosure  # rolling-generic strip caveat
        assert "ADR 0011" in disclosure
        # Pydantic enforces required field
        from pydantic import ValidationError as _VE
        bad = dict(out)
        bad.pop("methodology_disclosure")
        with pytest.raises(_VE):
            FuturesStripSnapshotOutput.model_validate(bad)

    def test_methodology_disclosure_names_eur_ibor_regime(self):
        """ADR 0011 — EUR Euribor must NOT auto-collapse with SOFR-
        style RFR strips. The snapshot's methodology disclosure must
        surface the IBOR regime label when curve_family is Euribor."""
        params = FuturesStripSnapshotInput(
            curve_family="EUR_SHORT_RATE_FUT",
        )
        out = _run(params)
        assert "error" not in out
        assert out["short_rate_regime"] == "IBOR"
        assert "IBOR" in out["methodology_disclosure"]
        assert "EUR_SHORT_RATE_FUT" in out["methodology_disclosure"]
        for row in out["snapshot"]:
            assert "IBOR" in row["row_methodology_card"]
            assert "EUR_SHORT_RATE_FUT" in row["row_methodology_card"]

    def test_open_interest_surfaced_per_row(self):
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        out = _run(params)
        for row in out["snapshot"]:
            assert row["open_interest"] is not None
            # Whole-contract count (open_interest_round_decimals=0)
            assert row["open_interest"] == int(row["open_interest"])
            assert row["open_interest"] > 0


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def test_z_score_window_override_changes_z(self):
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        out_default = _run(params, config=_custom_config())
        out_short = _run(
            params, config=_custom_config(z_score_window_days=120),
        )
        # Per-row z-scores should differ between configs.
        # (Non-degenerate synthetic data ensures rolling stats are
        # sensitive to the window length.)
        diffs = 0
        for r_def, r_short in zip(
            out_default["snapshot"], out_short["snapshot"],
        ):
            if r_def["z_score_implied_rate"] != r_short["z_score_implied_rate"]:
                diffs += 1
        assert diffs > 0, (
            "z_score_window_days override did not change ANY per-leg "
            "z-score; rolling stat is not honoring the convention"
        )

    def test_raw_price_round_decimals_propagates(self):
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        out = _run(
            params, config=_custom_config(raw_price_round_decimals=2),
        )
        for row in out["snapshot"]:
            assert row["raw_price"] == round(row["raw_price"], 2)

    def test_strip_positions_subset_truncates_snapshot(self):
        """YAML-drift guard: a config with strip_positions='1,2,3'
        still works (truncates honestly to 3 rows). The output's
        ``strip_positions`` and ``snapshot`` lists agree on length and
        order. Mirrors the futures_butterfly_simple weighting-knob
        style of YAML-driven configuration."""
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        # Build matching synthetic price / OI frames for just
        # strip positions 1, 2, 3 so the fetcher mock returns the
        # subset.
        sub_price = _synthetic_strip_group_df(strip_positions=(1, 2, 3))
        sub_oi = _synthetic_oi_group_df(strip_positions=(1, 2, 3))
        out = _run(
            params,
            price_df=sub_price,
            oi_df=sub_oi,
            config=_custom_config(strip_positions="1,2,3"),
        )
        assert "error" not in out
        assert out["strip_positions"] == [1, 2, 3]
        assert len(out["snapshot"]) == 3
        assert [r["strip_position"] for r in out["snapshot"]] == [1, 2, 3]


# ===========================================================================
# 4. Schema-layer behaviour + YAML fall-through
# ===========================================================================

class TestSchemaDefaults:
    def test_field_name_defaults_are_none(self):
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        assert params.last_price_field_name is None
        assert params.open_interest_field_name is None

    def test_as_of_date_default_is_none(self):
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        assert params.as_of_date is None

    def test_unknown_curve_family_raises_validator_error(self):
        """Schema refusal — closed Literal mirroring the playbook
        universe; unknown values are rejected at the schema layer
        (NOT silently passed through to compute)."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc:
            FuturesStripSnapshotInput(curve_family="USD_OIS")
        msg = str(exc.value).lower()
        assert "literal" in msg or "input should be" in msg

    def test_curve_family_accepts_all_three_v1_values(self):
        # All three V1 families pass the Literal — paranoia guard
        # so a typo in the Literal would break this test.
        for cf in ("SOFR_FUT", "EUR_SHORT_RATE_FUT", "SONIA_FUT"):
            FuturesStripSnapshotInput(curve_family=cf)


class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML defaults reach fetch_strip_group."""

    def _capture_field_name(self, params, config, *, field_to_check="PX_LAST"):
        captured_calls = []

        def _strip_group_side(*, engine, curve_family, strip_positions, field_name, start_date):
            captured_calls.append(field_name)
            if field_name in ("PX_LAST", "PX_BID", "PX_ASK"):
                return _synthetic_strip_group_df()
            return _synthetic_oi_group_df()

        def _ref_side(*, engine, curve_family, strip_position, as_of_date):
            return _synthetic_reference(
                curve_family=curve_family,
                contract_code=f"STR{strip_position}",
                strip_position=strip_position,
            )

        with patch(
            f"{_TARGET}.fetch_strip_group",
            side_effect=_strip_group_side,
        ), patch(
            f"{_TARGET}.fetch_strip_position_reference",
            side_effect=_ref_side,
        ), patch(
            f"{_TARGET}.fetch_strip_position_max_date",
            return_value=None,
        ), patch(
            f"{_TARGET}.date", _FrozenDate,
        ):
            calculate_futures_strip_snapshot(
                engine=None, params=params, config=config,
            )
        return captured_calls

    def test_omitted_field_names_use_yaml_defaults(self):
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        calls = self._capture_field_name(
            params, _custom_config(
                default_price_field="PX_LAST",
                default_open_interest_field="OPEN_INT",
            ),
        )
        assert "PX_LAST" in calls
        assert "OPEN_INT" in calls

    def test_yaml_override_changes_resolved_field(self):
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        calls = self._capture_field_name(
            params, _custom_config(default_price_field="PX_BID"),
        )
        assert "PX_BID" in calls
        assert "PX_LAST" not in calls

    def test_explicit_field_name_overrides_yaml(self):
        params = FuturesStripSnapshotInput(
            curve_family="SOFR_FUT",
            last_price_field_name="PX_ASK",
        )
        calls = self._capture_field_name(
            params, _custom_config(default_price_field="PX_LAST"),
        )
        assert "PX_ASK" in calls
        assert "PX_LAST" not in calls


# ===========================================================================
# 5. Inverse-pricing rule is metadata-driven (PR8 / P6)
# ===========================================================================

class TestInversePricingMetadataDriven:
    def test_missing_inverse_flag_returns_error_envelope(self):
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        # Build references where one leg is missing inverse_pricing.
        refs = {}
        for p in range(1, 9):
            ref = _synthetic_reference(
                curve_family="SOFR_FUT",
                contract_code=f"SFR{p}",
                strip_position=p,
            )
            if p == 3:
                del ref["inverse_pricing"]
            refs[p] = ref
        out = _run(params, references=refs)
        assert "error" in out
        assert "inverse_pricing" in out["error"]

    def test_mixed_inverse_flags_returns_error_envelope(self):
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        refs = {}
        for p in range(1, 9):
            refs[p] = _synthetic_reference(
                curve_family="SOFR_FUT",
                contract_code=f"SFR{p}",
                strip_position=p,
                # Mixed: position 4 is direct, all others inverse.
                inverse_pricing=(p != 4),
            )
        out = _run(params, references=refs)
        assert "error" in out
        assert "inverse_pricing" in out["error"]
        assert "disagree" in out["error"].lower()


# ===========================================================================
# 6. Future-anchor guard (PR8 + PR16)
# ===========================================================================

class TestFutureAnchorGuard:
    def test_as_of_beyond_universe_max_returns_error(self):
        params = FuturesStripSnapshotInput(
            curve_family="SOFR_FUT",
            as_of_date=date(2030, 1, 1),
        )
        out = _run(params, universe_max_date=date(2026, 4, 8))
        assert "error" in out
        assert "no scoreable strip" in out["error"]
        assert "2030-01-01" in out["error"]
        assert "2026-04-08" in out["error"]
        # Snapshot envelope keys are NOT present on error.
        assert "snapshot" not in out
        assert "methodology_disclosure" not in out

    def test_as_of_within_universe_proceeds(self):
        params = FuturesStripSnapshotInput(
            curve_family="SOFR_FUT",
            as_of_date=date(2026, 4, 8),
        )
        out = _run(params, universe_max_date=date(2026, 4, 30))
        assert "error" not in out, out.get("error")
        assert "snapshot" in out

    def test_as_of_none_skips_guard(self):
        """When as_of_date is None, the future-anchor probe is not
        consulted — the post-fetch data-max anchor is honest by
        construction."""
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        out = _run(params, universe_max_date=date(1900, 1, 1))
        assert "error" not in out, out.get("error")


# ===========================================================================
# 7. Reference miss + missing regime label → error envelope
# ===========================================================================

class TestErrorEnvelopes:
    def test_missing_reference_returns_error(self):
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        refs = {}
        for p in range(1, 9):
            if p == 5:
                refs[p] = None
            else:
                refs[p] = _synthetic_reference(
                    curve_family="SOFR_FUT",
                    contract_code=f"SFR{p}",
                    strip_position=p,
                )
        out = _run(params, references=refs)
        assert "error" in out
        assert "strip_position=5" in out["error"]

    def test_empty_price_history_returns_error(self):
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        empty_df = pd.DataFrame(
            columns=["trade_date", "strip_position", "field_value"],
        )
        out = _run(params, price_df=empty_df)
        assert "error" in out

    def test_missing_one_strip_returns_error(self):
        """If the strip-group fetch returns rows for only seven of the
        eight configured positions, the controlled-error envelope
        names the missing position."""
        params = FuturesStripSnapshotInput(curve_family="SOFR_FUT")
        df = _synthetic_strip_group_df(strip_positions=(1, 2, 3, 4, 5, 6, 7, 8))
        # Drop strip_position=4
        df = df[df["strip_position"] != 4].reset_index(drop=True)
        out = _run(params, price_df=df)
        assert "error" in out
        assert "4" in out["error"]


# ===========================================================================
# 8. Import path discipline
# ===========================================================================

class TestImportPathDiscipline:
    def test_calculate_via_package_init(self):
        from rates_agent.policy_futures.tools.futures_strip_snapshot import (
            calculate_futures_strip_snapshot as via_package,
        )
        from rates_agent.policy_futures.tools.futures_strip_snapshot.compute import (
            calculate_futures_strip_snapshot as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.policy_futures.tools.futures_strip_snapshot import (
            FuturesStripSnapshotInput as via_package,
        )
        from rates_agent.policy_futures.tools.futures_strip_snapshot.schemas import (
            FuturesStripSnapshotInput as via_schemas,
        )
        from rates_agent.policy_futures.tools.schemas import (
            FuturesStripSnapshotInput as via_hub,
        )
        assert via_package is via_schemas is via_hub

    def test_output_schema_via_three_paths(self):
        from rates_agent.policy_futures.tools.futures_strip_snapshot import (
            FuturesStripSnapshotOutput as via_package,
        )
        from rates_agent.policy_futures.tools.futures_strip_snapshot.schemas import (
            FuturesStripSnapshotOutput as via_schemas,
        )
        from rates_agent.policy_futures.tools.schemas import (
            FuturesStripSnapshotOutput as via_hub,
        )
        assert via_package is via_schemas is via_hub
