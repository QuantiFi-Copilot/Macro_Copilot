"""
test_scan_policy_futures_extremes_compute.py — Unit tests for the
                                                policy-futures
                                                universe-wide
                                                extremes scan

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. compute() runs end-to-end against synthetic universe data with
     the bundled config and returns a well-formed output.
  3. Convention overrides actually change behaviour (z-window, ddof,
     ffill, min_periods, threshold rounding).
  4. Per-metric top-N + min_abs_z_score filtering behaves correctly.
  5. Deterministic ordering: ties on |z| broken by curve_family
     asc then strip_position asc.
  6. Schema-layer behaviour: closed-family whitelist refusal, no
     ``field_name`` input accepted (input-schema-overreach guard),
     closed ScanMetric enum, refusal on empty curve_families /
     metrics lists.
  7. Methodology disclosure: present on EVERY row AND on the
     response, contains the z-score lookback verbatim, contains
     the universe-wide-strip-scan + RFR-vs-IBOR-per-curve_family +
     inverse-pricing + rolling-generic-strip caveats.
  8. Edge cases: empty universe, all stems below min_periods,
     threshold too high, missing field, NaN stems, single-stem
     universe, mixed inverse_pricing universe refused.
  9. Per-row regime disclosure: SOFR_FUT / SONIA_FUT rows carry
     ``short_rate_regime='RFR'``; EUR_SHORT_RATE_FUT rows carry
     ``short_rate_regime='IBOR'``.
 10. Inverse-pricing rule: implied_rate_pct = 100 - raw_price on
     inverse-priced stems; raw_price direct otherwise.
 11. Metrics subset: when ``metrics=['implied_rate_level']`` is
     supplied, only that block appears in results.
 12. Fetcher YAML defaults reach the three universe-fetcher calls.

Tests are fully offline. The new
``fetch_scan_universe_strip_position`` /
``fetch_scan_universe_policy_future_reference`` /
``fetch_scan_universe_strip_position_max_date`` helpers are also
exercised at the compute layer via mocked engines.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Tuple
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.policy_futures.tools.scan_policy_futures_extremes import (
    CONFIG_PATH,
    ScanPolicyFuturesExtremesInput,
    ScanPolicyFuturesExtremesOutput,
    calculate_scan_policy_futures_extremes,
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
# Test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


_FROZEN_TODAY = date(2026, 4, 8)


class _FrozenDate(date):
    _frozen_value: date = _FROZEN_TODAY

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


# Default V1 universe — 24 stems = 8 strip positions × 3 curve
# families per the policy_futures playbook. ``contract_code`` is the
# enriched-view per-window underlying (e.g. SFRH6 COMB) for the
# market-data rows; the master stem (SFR1 / ER1 / SFI1) lives on
# instrument_master and is sourced via the reference helper.
_DEFAULT_STEMS: List[Tuple[str, int, str]] = []
for cf, master_prefix, ec_prefix in [
    ("SOFR_FUT", "SFR", "SFRH6"),
    ("EUR_SHORT_RATE_FUT", "ER", "ERH6"),
    ("SONIA_FUT", "SFI", "SFIH6"),
]:
    for sp in range(1, 9):
        _DEFAULT_STEMS.append(
            (cf, sp, f"{ec_prefix}")
        )


def _stem_series(
    *,
    drift: float,
    start: float,
    days: int,
    frozen_today: date,
) -> pd.Series:
    """Synthetic business-day series of length `days` ending at
    frozen_today."""
    bdays = pd.bdate_range(
        frozen_today - timedelta(days=days * 2), frozen_today,
    )
    bdays = bdays[-days:]
    n = len(bdays)
    values = np.linspace(start, start + drift, n)
    return pd.Series(values, index=bdays)


def _build_universe_field_df(
    *,
    stems: List[Tuple[str, int, str]],
    per_stem_drift: Dict[Tuple[str, int], float],
    base: float,
    days: int,
    frozen_today: date,
) -> pd.DataFrame:
    """Assemble a long-format DataFrame matching
    ``fetch_scan_universe_strip_position`` shape across the
    universe."""
    rows = []
    for (cf, sp, contract_code) in stems:
        drift = per_stem_drift.get((cf, sp), 0.0)
        series = _stem_series(
            drift=drift, start=base, days=days, frozen_today=frozen_today,
        )
        for ts, value in series.items():
            rows.append(
                {
                    "trade_date": ts.date(),
                    "curve_family": cf,
                    "strip_position": int(sp),
                    "contract_code": contract_code,
                    "field_value": float(value),
                }
            )
    return pd.DataFrame(rows)


def _build_reference_df(
    *,
    stems: List[Tuple[str, int, str]],
    inverse_pricing: bool = True,
    per_stem_override: Dict[Tuple[str, int], Dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """Assemble a reference DataFrame matching
    ``fetch_scan_universe_policy_future_reference`` shape — one row
    per (curve_family, strip_position)."""
    per_stem_override = per_stem_override or {}
    master_by_cf = {
        "SOFR_FUT": "SFR",
        "EUR_SHORT_RATE_FUT": "ER",
        "SONIA_FUT": "SFI",
    }
    rows = []
    for (cf, sp, contract_code) in stems:
        master_prefix = master_by_cf.get(cf, "X")
        override = per_stem_override.get((cf, sp), {})
        row = {
            "curve_family": cf,
            "contract_code": f"{master_prefix}{sp}",
            "strip_position": int(sp),
            "inverse_pricing": override.get(
                "inverse_pricing", inverse_pricing,
            ),
            "underlying_contract_code": override.get(
                "underlying_contract_code", contract_code,
            ),
            "expiry_date": override.get(
                "expiry_date", date(2026, 6, 15),
            ),
            "security_name": override.get(
                "security_name", f"{contract_code} sec",
            ),
            "tick_size": override.get("tick_size", 0.005),
            "tick_value": override.get("tick_value", 12.5),
            "contract_size": override.get("contract_size", 1_000_000.0),
        }
        rows.append(row)
    return pd.DataFrame(rows)


def _default_field_data(
    stems=None,
    days: int = 400,
    frozen_today: date = _FROZEN_TODAY,
):
    """Produce three universe-wide DataFrames (price, volume, OI)
    with enough cross-stem drift dispersion that each metric has at
    least one stem with |z| >= 1.5 on the latest aligned row."""
    if stems is None:
        stems = _DEFAULT_STEMS

    # Raw-price drifts. With inverse pricing the implied-rate sign
    # flips so SFR1 (price up → rate down → big negative z on rate
    # level) is distinguishable from ER8 (price down → rate up →
    # positive z).
    price_drifts = {
        ("SOFR_FUT", 1): -2.0,
        ("SOFR_FUT", 2): -1.5,
        ("SOFR_FUT", 3): 0.5,
        ("SOFR_FUT", 4): 1.5,
        ("SOFR_FUT", 5): 0.2,
        ("SOFR_FUT", 6): -0.5,
        ("SOFR_FUT", 7): 2.5,   # large up drift on raw price
        ("SOFR_FUT", 8): -3.0,  # large down drift on raw price
        ("EUR_SHORT_RATE_FUT", 1): 1.0,
        ("EUR_SHORT_RATE_FUT", 2): -0.5,
        ("EUR_SHORT_RATE_FUT", 3): 2.0,
        ("EUR_SHORT_RATE_FUT", 4): 0.0,
        ("EUR_SHORT_RATE_FUT", 5): -2.5,
        ("EUR_SHORT_RATE_FUT", 6): 1.2,
        ("EUR_SHORT_RATE_FUT", 7): -0.8,
        ("EUR_SHORT_RATE_FUT", 8): 0.4,
        ("SONIA_FUT", 1): -1.0,
        ("SONIA_FUT", 2): 1.5,
        ("SONIA_FUT", 3): -0.5,
        ("SONIA_FUT", 4): 0.8,
        ("SONIA_FUT", 5): 2.0,
        ("SONIA_FUT", 6): -1.5,
        ("SONIA_FUT", 7): 0.3,
        ("SONIA_FUT", 8): -2.2,
    }
    # Volume drifts (in CONTRACTS).
    volume_drifts = {
        ("SOFR_FUT", 1): 200000.0,  # high z on volume
        ("SOFR_FUT", 2): 50000.0,
        ("SOFR_FUT", 3): -10000.0,
        ("SOFR_FUT", 4): 5000.0,
        ("SOFR_FUT", 5): 80000.0,
        ("SOFR_FUT", 6): 1000.0,
        ("SOFR_FUT", 7): 30000.0,
        ("SOFR_FUT", 8): -5000.0,
        ("EUR_SHORT_RATE_FUT", 1): 60000.0,
        ("EUR_SHORT_RATE_FUT", 2): -150000.0,  # large neg vol z
        ("EUR_SHORT_RATE_FUT", 3): 40000.0,
        ("EUR_SHORT_RATE_FUT", 4): 20000.0,
        ("EUR_SHORT_RATE_FUT", 5): 100000.0,
        ("EUR_SHORT_RATE_FUT", 6): -10000.0,
        ("EUR_SHORT_RATE_FUT", 7): 70000.0,
        ("EUR_SHORT_RATE_FUT", 8): 5000.0,
        ("SONIA_FUT", 1): 30000.0,
        ("SONIA_FUT", 2): -5000.0,
        ("SONIA_FUT", 3): 50000.0,
        ("SONIA_FUT", 4): 8000.0,
        ("SONIA_FUT", 5): 12000.0,
        ("SONIA_FUT", 6): -20000.0,
        ("SONIA_FUT", 7): 25000.0,
        ("SONIA_FUT", 8): 1000.0,
    }
    # OI drifts (in CONTRACTS).
    oi_drifts = {
        ("SOFR_FUT", 1): 800000.0,
        ("SOFR_FUT", 2): 500000.0,
        ("SOFR_FUT", 3): 100000.0,
        ("SOFR_FUT", 4): 50000.0,
        ("SOFR_FUT", 5): 60000.0,
        ("SOFR_FUT", 6): 30000.0,
        ("SOFR_FUT", 7): 40000.0,
        ("SOFR_FUT", 8): 1500000.0,  # massive OI z on SFR8
        ("EUR_SHORT_RATE_FUT", 1): 600000.0,
        ("EUR_SHORT_RATE_FUT", 2): 200000.0,
        ("EUR_SHORT_RATE_FUT", 3): -300000.0,
        ("EUR_SHORT_RATE_FUT", 4): 100000.0,
        ("EUR_SHORT_RATE_FUT", 5): 50000.0,
        ("EUR_SHORT_RATE_FUT", 6): 80000.0,
        ("EUR_SHORT_RATE_FUT", 7): 90000.0,
        ("EUR_SHORT_RATE_FUT", 8): 70000.0,
        ("SONIA_FUT", 1): 200000.0,
        ("SONIA_FUT", 2): 80000.0,
        ("SONIA_FUT", 3): 60000.0,
        ("SONIA_FUT", 4): 30000.0,
        ("SONIA_FUT", 5): 90000.0,
        ("SONIA_FUT", 6): 25000.0,
        ("SONIA_FUT", 7): 40000.0,
        ("SONIA_FUT", 8): 70000.0,
    }
    price_df = _build_universe_field_df(
        stems=stems, per_stem_drift=price_drifts, base=96.5,
        days=days, frozen_today=frozen_today,
    )
    volume_df = _build_universe_field_df(
        stems=stems, per_stem_drift=volume_drifts, base=300000.0,
        days=days, frozen_today=frozen_today,
    )
    oi_df = _build_universe_field_df(
        stems=stems, per_stem_drift=oi_drifts, base=600000.0,
        days=days, frozen_today=frozen_today,
    )
    return price_df, volume_df, oi_df


_UNSET = object()


def _run(
    params,
    *,
    price_df=None,
    volume_df=None,
    oi_df=None,
    reference_df=None,
    config=None,
    universe_max_date=_UNSET,
):
    """Patch the three universe fetchers + max-date probe + date,
    and invoke compute.

    ``universe_max_date`` controls the value the future-anchor
    guard's DB probe returns. By default it returns
    ``_FROZEN_TODAY`` so an in-data ``as_of_date <= _FROZEN_TODAY``
    passes the guard.
    """
    if price_df is None or volume_df is None or oi_df is None:
        d_price, d_volume, d_oi = _default_field_data()
        if price_df is None:
            price_df = d_price
        if volume_df is None:
            volume_df = d_volume
        if oi_df is None:
            oi_df = d_oi
    if reference_df is None:
        reference_df = _build_reference_df(stems=_DEFAULT_STEMS)

    captured_calls: list[dict] = []

    def _strip_universe_side_effect(
        *, engine, instrument_type, field_name, start_date,
        curve_families=None, end_date=None,
    ):
        captured_calls.append(
            {
                "instrument_type": instrument_type,
                "curve_families": (
                    list(curve_families)
                    if curve_families is not None else None
                ),
                "field_name": field_name,
                "start_date": start_date,
                "end_date": end_date,
            }
        )
        # Defaults per config: PX_LAST / PX_VOLUME / OPEN_INT.
        if field_name == "PX_LAST":
            base = price_df
        elif field_name == "PX_VOLUME":
            base = volume_df
        elif field_name == "OPEN_INT":
            base = oi_df
        else:
            return pd.DataFrame(
                columns=[
                    "trade_date", "curve_family", "strip_position",
                    "contract_code", "field_value",
                ]
            )
        df = base.copy()
        if curve_families is not None:
            df = df[df["curve_family"].isin(list(curve_families))]
        return df

    captured_ref_calls: list[dict] = []

    def _reference_side_effect(*, engine, as_of_date, curve_families=None):
        captured_ref_calls.append(
            {
                "as_of_date": as_of_date,
                "curve_families": (
                    list(curve_families)
                    if curve_families is not None else None
                ),
            }
        )
        df = reference_df.copy()
        if curve_families is not None:
            df = df[df["curve_family"].isin(list(curve_families))]
        return df

    resolved_max = (
        _FROZEN_TODAY if universe_max_date is _UNSET else universe_max_date
    )

    def _max_date_side_effect(*, engine, instrument_type, curve_families=None):
        return resolved_max

    target = (
        "rates_agent.policy_futures.tools.scan_policy_futures_extremes.compute"
    )
    with patch(
        f"{target}.fetch_scan_universe_strip_position",
        side_effect=_strip_universe_side_effect,
    ), patch(
        f"{target}.fetch_scan_universe_policy_future_reference",
        side_effect=_reference_side_effect,
    ), patch(
        f"{target}.fetch_scan_universe_strip_position_max_date",
        side_effect=_max_date_side_effect,
    ), patch(
        f"{target}.date", _FrozenDate,
    ):
        result = calculate_scan_policy_futures_extremes(
            engine=None, params=params, config=config,
        )
    return result, captured_calls, captured_ref_calls


def _custom_config(**overrides) -> ToolConfig:
    defaults = {
        "z_score_window_days": 252,
        "z_score_min_periods": 60,
        "z_score_ddof": 1,
        "z_score_buffer_multiplier": 1.5,
        "ffill_limit_days": 5,
        "daily_change_offset_rows": 2,
        "default_price_field": "PX_LAST",
        "default_volume_field": "PX_VOLUME",
        "default_open_interest_field": "OPEN_INT",
        "policy_futures_curve_families": (
            "SOFR_FUT,EUR_SHORT_RATE_FUT,SONIA_FUT"
        ),
        "short_rate_regime_map": (
            "SOFR_FUT=RFR,SONIA_FUT=RFR,EUR_SHORT_RATE_FUT=IBOR"
        ),
        "raw_price_round_decimals": 5,
        "implied_rate_round_decimals": 4,
        "bps_change_round_decimals": 2,
        "volume_round_decimals": 0,
        "oi_round_decimals": 0,
        "z_score_round_decimals": 4,
        "default_top_n": 5,
        "default_min_abs_z_score": 1.5,
        "default_metrics": (
            "implied_rate_level,implied_rate_change,volume_level,"
            "open_interest_level"
        ),
    }
    defaults.update(overrides)
    return ToolConfig(
        tool=ToolMeta(
            name="policy_futures_scan_policy_futures_extremes_tool",
            domain="policy_futures",
            description="test",
        ),
        methodology=MethodologyMeta(what_it_does="test"),
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
        assert (
            cfg.tool.name
            == "policy_futures_scan_policy_futures_extremes_tool"
        )
        assert cfg.tool.domain == "policy_futures"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "z_score_window_days",
            "z_score_min_periods",
            "z_score_ddof",
            "z_score_buffer_multiplier",
            "ffill_limit_days",
            "daily_change_offset_rows",
            "default_price_field",
            "default_volume_field",
            "default_open_interest_field",
            "policy_futures_curve_families",
            "short_rate_regime_map",
            "raw_price_round_decimals",
            "implied_rate_round_decimals",
            "bps_change_round_decimals",
            "volume_round_decimals",
            "oi_round_decimals",
            "z_score_round_decimals",
            "default_top_n",
            "default_min_abs_z_score",
            "default_metrics",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("z_score_window_days") == 252
        assert cfg.convention_value("z_score_min_periods") == 60
        assert cfg.convention_value("z_score_ddof") == 1
        assert cfg.convention_value("z_score_buffer_multiplier") == 1.5
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("daily_change_offset_rows") == 2
        assert cfg.convention_value("default_price_field") == "PX_LAST"
        assert cfg.convention_value("default_volume_field") == "PX_VOLUME"
        assert (
            cfg.convention_value("default_open_interest_field") == "OPEN_INT"
        )
        assert cfg.convention_value("default_top_n") == 5
        assert cfg.convention_value("default_min_abs_z_score") == 1.5
        wl = cfg.convention_value("policy_futures_curve_families")
        for cf in ["SOFR_FUT", "EUR_SHORT_RATE_FUT", "SONIA_FUT"]:
            assert cf in wl
        # Bond-futures curves must NOT be in the whitelist.
        for cf in ["UST_FUT", "DE_FUT", "UK_FUT"]:
            assert cf not in wl
        rmap = cfg.convention_value("short_rate_regime_map")
        assert "SOFR_FUT=RFR" in rmap
        assert "SONIA_FUT=RFR" in rmap
        assert "EUR_SHORT_RATE_FUT=IBOR" in rmap
        metrics = cfg.convention_value("default_metrics")
        for m in (
            "implied_rate_level", "implied_rate_change",
            "volume_level", "open_interest_level",
        ):
            assert m in metrics

    def test_z_score_window_source_is_registered_tag(self):
        cfg = load_tool_config(CONFIG_PATH)
        src = cfg.conventions["z_score_window_days"].source
        assert src == "industry_standard_1y_window"

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        joined = " | ".join(cfg.methodology.planned_extensions)
        # CTD-of-futures-of-OIS extension must be named per ADR 0011.
        assert "CTD" in joined or "OIS" in joined


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        params = ScanPolicyFuturesExtremesInput()
        out, calls, ref_calls = _run(params)

        assert "error" not in out, out.get("error")
        assert "scan_summary" in out
        assert "results" in out
        assert "methodology_disclosure" in out
        assert len(out["results"]) > 0

        ScanPolicyFuturesExtremesOutput.model_validate(out)

        # Three universe fetcher calls — PX_LAST / PX_VOLUME / OPEN_INT.
        assert len(calls) == 3
        fields_called = sorted(c["field_name"] for c in calls)
        assert fields_called == ["OPEN_INT", "PX_LAST", "PX_VOLUME"]
        # All three carried instrument_type='policy_future' (the
        # code-level invariant, NOT YAML).
        for call in calls:
            assert call["instrument_type"] == "policy_future"

        # Exactly one reference fetch (single round-trip).
        assert len(ref_calls) == 1

    def test_explicit_config_matches_auto_loaded(self):
        params = ScanPolicyFuturesExtremesInput(
            as_of_date=_FROZEN_TODAY,
        )
        out_auto, _, _ = _run(params, config=None)
        out_explicit, _, _ = _run(
            params, config=load_tool_config(CONFIG_PATH),
        )
        assert out_auto == out_explicit

    def test_output_row_structure(self):
        params = ScanPolicyFuturesExtremesInput()
        out, _, _ = _run(params)
        for row in out["results"]:
            for k in (
                "rank", "metric", "curve_family", "strip_position",
                "contract_code", "underlying_contract_code",
                "security_name", "expiry_date", "contract_size",
                "inverse_priced", "short_rate_regime", "quote_units",
                "as_of_date", "current_raw_price", "implied_rate_pct",
                "daily_change_implied_rate_bps", "current_volume",
                "current_open_interest", "delta_open_interest_1d",
                "z_score", "signal", "methodology_disclosure",
            ):
                assert k in row, f"missing {k}"
            assert row["metric"] in (
                "implied_rate_level", "implied_rate_change",
                "volume_level", "open_interest_level",
            )
            assert row["signal"] in ("EXTREME_HIGH", "EXTREME_LOW")
            assert row["rank"] >= 1
            assert row["curve_family"] in (
                "SOFR_FUT", "EUR_SHORT_RATE_FUT", "SONIA_FUT",
            )
            assert 1 <= row["strip_position"] <= 8

    def test_per_metric_blocks_ordered(self):
        params = ScanPolicyFuturesExtremesInput(
            top_n=10, min_abs_z_score=0.0,
        )
        out, _, _ = _run(params)
        metrics_seen = [row["metric"] for row in out["results"]]
        order = [
            "implied_rate_level", "implied_rate_change",
            "volume_level", "open_interest_level",
        ]
        last_idx = -1
        for m in metrics_seen:
            idx = order.index(m)
            assert idx >= last_idx, (
                f"out-of-order block: {metrics_seen}"
            )
            last_idx = idx


# ===========================================================================
# 3. Methodology disclosure
# ===========================================================================

class TestMethodologyDisclosure:
    def test_disclosure_present_on_response_and_every_row(self):
        params = ScanPolicyFuturesExtremesInput()
        out, _, _ = _run(params)
        md_response = out["methodology_disclosure"]
        # Z-score lookback explicit.
        assert "252" in md_response
        # Universe-wide-strip-scan label.
        assert "Universe-wide" in md_response or "universe-wide" in md_response.lower()
        # Per-curve_family RFR/IBOR labels surfaced at the response
        # level — every requested curve_family must appear with its
        # regime tag.
        assert "SOFR_FUT=RFR" in md_response
        assert "SONIA_FUT=RFR" in md_response
        assert "EUR_SHORT_RATE_FUT=IBOR" in md_response
        # Inverse-pricing rule disclosed.
        assert "100 - raw_price" in md_response
        # Rolling-generic / rolls-quarterly caveat.
        assert "Rolling-generic" in md_response or "rolling-generic" in md_response.lower()
        # Morning-screen-not-tactical-signal.
        assert "morning screen" in md_response.lower()
        # NOT pack-average / NOT curve-shape / NOT CTD.
        lower = md_response.lower()
        assert "not a pack-average" in lower
        assert "not a tenor-anchored" in lower or "not a tenor" in lower
        # Per-row disclosure matches.
        for row in out["results"]:
            assert row["methodology_disclosure"] == md_response

    def test_disclosure_required_in_schema(self):
        from pydantic import ValidationError as _VE
        params = ScanPolicyFuturesExtremesInput()
        out, _, _ = _run(params)
        bad = dict(out)
        bad.pop("methodology_disclosure")
        with pytest.raises(_VE):
            ScanPolicyFuturesExtremesOutput.model_validate(bad)

    def test_disclosure_tracks_config_window(self):
        """If a custom config (post-guard) sets z_score_window_days
        to 180, the disclosure must surface 180 — never a hardcoded
        252. The PR14 guard normally refuses this; bypass via a
        patched window via the runtime _validate_window_252d
        callsite by temporarily monkey-patching the guard."""
        from rates_agent.policy_futures.tools.scan_policy_futures_extremes import (
            compute as _scan_compute,
        )
        # Patch the wire-freeze guard so this test can prove the
        # disclosure picks up the YAML's window value.
        with patch.object(_scan_compute, "_validate_window_252d", lambda z: None):
            out, _, _ = _run(
                ScanPolicyFuturesExtremesInput(min_abs_z_score=0.0),
                config=_custom_config(
                    z_score_window_days=180, z_score_min_periods=60,
                ),
            )
        assert "= 180" in out["methodology_disclosure"]
        assert "= 252" not in out["methodology_disclosure"]


# ===========================================================================
# 4. Filtering + top_n + ranking
# ===========================================================================

class TestRankingAndFiltering:
    def test_top_n_per_metric_respected(self):
        params = ScanPolicyFuturesExtremesInput(
            top_n=2, min_abs_z_score=0.0,
        )
        out, _, _ = _run(params)
        per_metric: Dict[str, List[Dict[str, Any]]] = {}
        for row in out["results"]:
            per_metric.setdefault(row["metric"], []).append(row)
        for metric, rows in per_metric.items():
            assert len(rows) <= 2, (
                f"metric {metric} returned {len(rows)} rows > top_n=2"
            )

    def test_high_threshold_drops_rows(self):
        out_low, _, _ = _run(
            ScanPolicyFuturesExtremesInput(
                top_n=5, min_abs_z_score=0.0,
            )
        )
        out_high, _, _ = _run(
            ScanPolicyFuturesExtremesInput(
                top_n=5, min_abs_z_score=10.0,
            )
        )
        # Unreachable threshold → error.
        assert "error" in out_high
        # Threshold=0 admits everyone (subject to top_n cap).
        assert "error" not in out_low

    def test_ranking_by_abs_zscore_desc(self):
        params = ScanPolicyFuturesExtremesInput(
            top_n=10, min_abs_z_score=0.0,
        )
        out, _, _ = _run(params)
        per_metric: Dict[str, List[Dict[str, Any]]] = {}
        for row in out["results"]:
            per_metric.setdefault(row["metric"], []).append(row)
        for metric, rows in per_metric.items():
            rows_sorted = sorted(rows, key=lambda r: r["rank"])
            abs_zs = [abs(r["z_score"]) for r in rows_sorted]
            assert abs_zs == sorted(abs_zs, reverse=True), (
                f"metric {metric}: rank order not monotonic on |z|: "
                f"{abs_zs}"
            )

    def test_signal_matches_zscore_sign(self):
        params = ScanPolicyFuturesExtremesInput(
            top_n=10, min_abs_z_score=0.0,
        )
        out, _, _ = _run(params)
        for row in out["results"]:
            if row["z_score"] is None:
                continue
            if row["z_score"] > 0:
                assert row["signal"] == "EXTREME_HIGH"
            elif row["z_score"] < 0:
                assert row["signal"] == "EXTREME_LOW"


# ===========================================================================
# 5. Determinism on ties
# ===========================================================================

class TestDeterministicTieBreaking:
    def test_ties_broken_by_curve_family_then_strip_position(self):
        """Two stems with identical |z| must order by curve_family
        asc then strip_position asc."""
        frozen = _FROZEN_TODAY
        # Three stems with IDENTICAL drift across all three fields.
        stems = [
            ("SOFR_FUT", 1, "SFRH6"),
            ("SOFR_FUT", 2, "SFRM6"),
            ("EUR_SHORT_RATE_FUT", 1, "ERH6"),
        ]
        identical_drift = -2.0
        price_df = _build_universe_field_df(
            stems=stems, per_stem_drift={
                ("SOFR_FUT", 1): identical_drift,
                ("SOFR_FUT", 2): identical_drift,
                ("EUR_SHORT_RATE_FUT", 1): identical_drift,
            },
            base=96.5, days=400, frozen_today=frozen,
        )
        volume_df = _build_universe_field_df(
            stems=stems, per_stem_drift={
                ("SOFR_FUT", 1): 50000.0,
                ("SOFR_FUT", 2): 50000.0,
                ("EUR_SHORT_RATE_FUT", 1): 50000.0,
            },
            base=300000.0, days=400, frozen_today=frozen,
        )
        oi_df = _build_universe_field_df(
            stems=stems, per_stem_drift={
                ("SOFR_FUT", 1): 100000.0,
                ("SOFR_FUT", 2): 100000.0,
                ("EUR_SHORT_RATE_FUT", 1): 100000.0,
            },
            base=600000.0, days=400, frozen_today=frozen,
        )
        ref_df = _build_reference_df(stems=stems)
        out, _, _ = _run(
            ScanPolicyFuturesExtremesInput(
                top_n=10, min_abs_z_score=0.0,
            ),
            price_df=price_df, volume_df=volume_df, oi_df=oi_df,
            reference_df=ref_df,
        )
        rate_rows = [
            r for r in out["results"]
            if r["metric"] == "implied_rate_level"
        ]
        # Expect EUR_SHORT_RATE_FUT/1 first (curve_family asc), then
        # SOFR_FUT/1, then SOFR_FUT/2 (strip_position asc).
        keys = [(r["curve_family"], r["strip_position"]) for r in rate_rows]
        assert keys == sorted(keys), (
            f"expected curve_family-then-strip_position ascending "
            f"tie-break, got {keys}"
        )


# ===========================================================================
# 6. Schema-layer behaviour
# ===========================================================================

class TestSchemaBehaviour:
    def test_full_universe_default(self):
        params = ScanPolicyFuturesExtremesInput()
        assert params.curve_families is None
        assert params.top_n is None
        assert params.min_abs_z_score is None
        assert params.metrics is None
        assert params.as_of_date is None

    def test_lookback_days_no_longer_an_input(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ScanPolicyFuturesExtremesInput(
                lookback_days=365,  # type: ignore[call-arg]
            )

    def test_as_of_date_accepted(self):
        params = ScanPolicyFuturesExtremesInput(
            as_of_date=date(2026, 4, 8),
        )
        assert params.as_of_date == date(2026, 4, 8)

    def test_as_of_date_iso_string_coerced(self):
        params = ScanPolicyFuturesExtremesInput(
            as_of_date="2026-04-08",  # type: ignore[arg-type]
        )
        assert params.as_of_date == date(2026, 4, 8)

    def test_as_of_date_malformed_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ScanPolicyFuturesExtremesInput(
                as_of_date="not-a-date",  # type: ignore[arg-type]
            )

    def test_curve_families_subset_accepted(self):
        params = ScanPolicyFuturesExtremesInput(
            curve_families=["SOFR_FUT", "SONIA_FUT"],
        )
        assert params.curve_families == ["SOFR_FUT", "SONIA_FUT"]

    def test_bond_futures_curve_family_refused(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc_info:
            ScanPolicyFuturesExtremesInput(
                curve_families=["SOFR_FUT", "UST_FUT"],
            )
        msg = str(exc_info.value)
        assert "UST_FUT" in msg

    def test_sovereign_curve_family_refused(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ScanPolicyFuturesExtremesInput(
                curve_families=["UST"],
            )

    def test_inflation_curve_family_refused(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ScanPolicyFuturesExtremesInput(
                curve_families=["USD_TIPS"],
            )

    def test_empty_curve_families_list_refused(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ScanPolicyFuturesExtremesInput(curve_families=[])

    def test_no_field_name_input_accepted(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ScanPolicyFuturesExtremesInput(
                field_name="PX_LAST",  # type: ignore[call-arg]
            )

    def test_invalid_metric_refused(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ScanPolicyFuturesExtremesInput(
                metrics=["NOT_A_METRIC"],  # type: ignore[list-item]
            )

    def test_empty_metrics_list_refused(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ScanPolicyFuturesExtremesInput(metrics=[])

    def test_top_n_bounded(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ScanPolicyFuturesExtremesInput(top_n=0)
        with pytest.raises(ValidationError):
            ScanPolicyFuturesExtremesInput(top_n=51)

    def test_min_abs_z_score_negative_refused(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ScanPolicyFuturesExtremesInput(min_abs_z_score=-0.5)


# ===========================================================================
# 7. Curve_families scoping reaches the fetcher
# ===========================================================================

class TestCurveFamiliesScoping:
    def test_full_universe_calls_with_whitelist(self):
        params = ScanPolicyFuturesExtremesInput()
        out, calls, _ = _run(params)
        assert "error" not in out
        expected = {"SOFR_FUT", "EUR_SHORT_RATE_FUT", "SONIA_FUT"}
        for call in calls:
            assert set(call["curve_families"]) == expected

    def test_subset_curve_families_reaches_fetcher(self):
        params = ScanPolicyFuturesExtremesInput(
            curve_families=["SOFR_FUT", "EUR_SHORT_RATE_FUT"],
        )
        out, calls, ref_calls = _run(params)
        assert "error" not in out, out.get("error")
        for call in calls:
            assert call["curve_families"] == [
                "SOFR_FUT", "EUR_SHORT_RATE_FUT",
            ]
        for rc in ref_calls:
            assert rc["curve_families"] == [
                "SOFR_FUT", "EUR_SHORT_RATE_FUT",
            ]


# ===========================================================================
# 8. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def test_zscore_window_override_changes_zs(self):
        from rates_agent.policy_futures.tools.scan_policy_futures_extremes import (
            compute as _scan_compute,
        )
        params = ScanPolicyFuturesExtremesInput(
            top_n=5, min_abs_z_score=0.0,
        )
        with patch.object(_scan_compute, "_validate_window_252d", lambda z: None):
            out_default, _, _ = _run(params, config=_custom_config())
            out_short, _, _ = _run(
                params,
                config=_custom_config(z_score_window_days=120),
            )
        z_default = next(
            (
                r["z_score"] for r in out_default["results"]
                if r["metric"] == "implied_rate_level"
            ),
            None,
        )
        z_short = next(
            (
                r["z_score"] for r in out_short["results"]
                if r["metric"] == "implied_rate_level"
            ),
            None,
        )
        assert z_default != z_short

    def test_ddof_override_changes_zs(self):
        params = ScanPolicyFuturesExtremesInput(
            top_n=5, min_abs_z_score=0.0,
        )
        out_sample, _, _ = _run(
            params, config=_custom_config(z_score_ddof=1),
        )
        out_pop, _, _ = _run(
            params, config=_custom_config(z_score_ddof=0),
        )
        z_sample = next(
            (
                r["z_score"] for r in out_sample["results"]
                if r["metric"] == "implied_rate_level"
            ),
            None,
        )
        z_pop = next(
            (
                r["z_score"] for r in out_pop["results"]
                if r["metric"] == "implied_rate_level"
            ),
            None,
        )
        assert z_sample != z_pop


# ===========================================================================
# 9. PR14 wire-freeze guard
# ===========================================================================

class TestPR14WireFreeze:
    def test_window_252_passes(self):
        from rates_agent.policy_futures.tools.scan_policy_futures_extremes.compute import (
            _validate_window_252d,
        )
        _validate_window_252d(252)  # no raise

    def test_non_252_window_raises_notimplementederror(self):
        from rates_agent.policy_futures.tools.scan_policy_futures_extremes.compute import (
            _validate_window_252d,
        )
        with pytest.raises(NotImplementedError):
            _validate_window_252d(180)


# ===========================================================================
# 10. Inverse-pricing conversion + regime disclosure
# ===========================================================================

class TestInversePricingAndRegime:
    def test_implied_rate_pct_equals_100_minus_raw_price_when_inverse(self):
        params = ScanPolicyFuturesExtremesInput(
            top_n=10, min_abs_z_score=0.0,
        )
        out, _, _ = _run(params)
        for row in out["results"]:
            if (
                row["current_raw_price"] is not None
                and row["implied_rate_pct"] is not None
                and row["inverse_priced"]
            ):
                expected = round(
                    100.0 - row["current_raw_price"], 4,
                )
                assert abs(row["implied_rate_pct"] - expected) < 1e-6, (
                    f"inverse-pricing rule broken: raw_price="
                    f"{row['current_raw_price']} implied_rate_pct="
                    f"{row['implied_rate_pct']} expected={expected}"
                )

    def test_implied_rate_pct_equals_raw_price_when_direct(self):
        """A direct-priced (inverse=False) stem must surface
        implied_rate_pct = raw_price."""
        stems = [("SOFR_FUT", 1, "SFRH6")]
        ref = _build_reference_df(
            stems=stems, inverse_pricing=False,
        )
        price_df = _build_universe_field_df(
            stems=stems, per_stem_drift={("SOFR_FUT", 1): 2.0},
            base=4.5,  # direct-priced rate (~4.5%)
            days=400, frozen_today=_FROZEN_TODAY,
        )
        volume_df = _build_universe_field_df(
            stems=stems, per_stem_drift={("SOFR_FUT", 1): 100000.0},
            base=300000.0, days=400, frozen_today=_FROZEN_TODAY,
        )
        oi_df = _build_universe_field_df(
            stems=stems, per_stem_drift={("SOFR_FUT", 1): 200000.0},
            base=600000.0, days=400, frozen_today=_FROZEN_TODAY,
        )
        out, _, _ = _run(
            ScanPolicyFuturesExtremesInput(
                top_n=1, min_abs_z_score=0.0,
                curve_families=["SOFR_FUT"],
            ),
            price_df=price_df, volume_df=volume_df, oi_df=oi_df,
            reference_df=ref,
        )
        assert "error" not in out, out.get("error")
        for row in out["results"]:
            if (
                row["current_raw_price"] is not None
                and row["implied_rate_pct"] is not None
            ):
                assert not row["inverse_priced"]
                # direct → implied_rate_pct == raw_price (modulo
                # different rounding precisions).
                assert (
                    abs(row["implied_rate_pct"]
                        - row["current_raw_price"]) < 0.001
                )
                assert row["quote_units"] == "rate (%)"

    def test_per_row_regime_disclosure(self):
        """SOFR / SONIA rows must carry RFR; EUR rows must carry IBOR."""
        params = ScanPolicyFuturesExtremesInput(
            top_n=10, min_abs_z_score=0.0,
        )
        out, _, _ = _run(params)
        for row in out["results"]:
            if row["curve_family"] in ("SOFR_FUT", "SONIA_FUT"):
                assert row["short_rate_regime"] == "RFR", (
                    f"{row['curve_family']} row must carry "
                    f"short_rate_regime='RFR', got "
                    f"{row['short_rate_regime']}"
                )
            elif row["curve_family"] == "EUR_SHORT_RATE_FUT":
                assert row["short_rate_regime"] == "IBOR"

    def test_mixed_inverse_pricing_universe_refused(self):
        """A scan with mixed inverse_pricing flags across stems must
        return the controlled-error envelope."""
        stems = [
            ("SOFR_FUT", 1, "SFRH6"),
            ("SOFR_FUT", 2, "SFRM6"),
        ]
        # Mixed: SFR1 inverse, SFR2 direct.
        ref = _build_reference_df(
            stems=stems,
            inverse_pricing=True,
            per_stem_override={
                ("SOFR_FUT", 2): {"inverse_pricing": False},
            },
        )
        price_df = _build_universe_field_df(
            stems=stems, per_stem_drift={
                ("SOFR_FUT", 1): -1.0, ("SOFR_FUT", 2): 0.5,
            },
            base=96.5, days=400, frozen_today=_FROZEN_TODAY,
        )
        volume_df = _build_universe_field_df(
            stems=stems, per_stem_drift={
                ("SOFR_FUT", 1): 50000.0, ("SOFR_FUT", 2): 50000.0,
            },
            base=300000.0, days=400, frozen_today=_FROZEN_TODAY,
        )
        oi_df = _build_universe_field_df(
            stems=stems, per_stem_drift={
                ("SOFR_FUT", 1): 100000.0, ("SOFR_FUT", 2): 100000.0,
            },
            base=600000.0, days=400, frozen_today=_FROZEN_TODAY,
        )
        out, _, _ = _run(
            ScanPolicyFuturesExtremesInput(
                top_n=5, min_abs_z_score=0.0,
                curve_families=["SOFR_FUT"],
            ),
            price_df=price_df, volume_df=volume_df, oi_df=oi_df,
            reference_df=ref,
        )
        assert "error" in out
        assert "mixed inverse_pricing" in out["error"].lower()

    def test_missing_inverse_pricing_flag_refused(self):
        """A stem missing the inverse_pricing flag must return the
        controlled-error envelope (P6 — no hidden methodology in
        code)."""
        stems = [("SOFR_FUT", 1, "SFRH6")]
        ref = _build_reference_df(stems=stems)
        ref = ref.copy()
        # Cast the bool column to object first so we can store None
        # (pandas refuses to upcast a bool column to nullable
        # automatically — same shape the live-DB SCD2 join would
        # produce when the JSONB flag is missing).
        ref["inverse_pricing"] = ref["inverse_pricing"].astype(object)
        ref.loc[ref.index[0], "inverse_pricing"] = None
        price_df, volume_df, oi_df = _default_field_data(stems=stems)
        out, _, _ = _run(
            ScanPolicyFuturesExtremesInput(
                top_n=5, min_abs_z_score=0.0,
                curve_families=["SOFR_FUT"],
            ),
            price_df=price_df, volume_df=volume_df, oi_df=oi_df,
            reference_df=ref,
        )
        assert "error" in out
        assert "inverse_pricing" in out["error"]


# ===========================================================================
# 11. Metrics subset
# ===========================================================================

class TestMetricsSubset:
    def test_rate_only_screen(self):
        params = ScanPolicyFuturesExtremesInput(
            top_n=5, min_abs_z_score=0.0,
            metrics=["implied_rate_level"],
        )
        out, _, _ = _run(params)
        assert "error" not in out, out.get("error")
        metrics_seen = {row["metric"] for row in out["results"]}
        assert metrics_seen == {"implied_rate_level"}

    def test_rate_and_change_screen(self):
        params = ScanPolicyFuturesExtremesInput(
            top_n=5, min_abs_z_score=0.0,
            metrics=["implied_rate_level", "implied_rate_change"],
        )
        out, _, _ = _run(params)
        assert "error" not in out, out.get("error")
        metrics_seen = {row["metric"] for row in out["results"]}
        # Both expected; volume / OI absent.
        assert "implied_rate_level" in metrics_seen
        assert "implied_rate_change" in metrics_seen
        assert "volume_level" not in metrics_seen
        assert "open_interest_level" not in metrics_seen

    def test_default_metrics_yaml_fallback(self):
        """When ``metrics`` is None, compute uses the YAML
        ``default_metrics`` CSV — must produce all four metric
        blocks on a default-config run."""
        params = ScanPolicyFuturesExtremesInput(
            top_n=5, min_abs_z_score=0.0,
        )
        out, _, _ = _run(params)
        metrics_seen = {row["metric"] for row in out["results"]}
        for m in (
            "implied_rate_level", "implied_rate_change",
            "volume_level", "open_interest_level",
        ):
            assert m in metrics_seen, (
                f"expected default metric {m} in results but got "
                f"only {metrics_seen}"
            )


# ===========================================================================
# 12. Threshold YAML fallback
# ===========================================================================

class TestThresholdYamlFallback:
    def test_top_n_none_falls_through_to_yaml(self):
        params = ScanPolicyFuturesExtremesInput(min_abs_z_score=0.0)
        out, _, _ = _run(
            params, config=_custom_config(default_top_n=2),
        )
        assert "error" not in out, out.get("error")
        per_metric: Dict[str, List[Dict[str, Any]]] = {}
        for row in out["results"]:
            per_metric.setdefault(row["metric"], []).append(row)
        for metric, rows in per_metric.items():
            assert len(rows) <= 2

    def test_top_n_explicit_overrides_yaml(self):
        params = ScanPolicyFuturesExtremesInput(
            top_n=4, min_abs_z_score=0.0,
        )
        out, _, _ = _run(
            params, config=_custom_config(default_top_n=2),
        )
        assert "error" not in out, out.get("error")
        per_metric: Dict[str, List[Dict[str, Any]]] = {}
        for row in out["results"]:
            per_metric.setdefault(row["metric"], []).append(row)
        max_block_size = max(len(rows) for rows in per_metric.values())
        assert max_block_size > 2, (
            f"explicit top_n=4 was overridden by YAML default — "
            f"max block size {max_block_size}"
        )
        for metric, rows in per_metric.items():
            assert len(rows) <= 4

    def test_min_abs_z_score_none_falls_through_to_yaml(self):
        params = ScanPolicyFuturesExtremesInput()
        out, _, _ = _run(
            params,
            config=_custom_config(default_min_abs_z_score=10.0),
        )
        assert "error" in out
        assert "passed |z| >= 10" in out["error"]

    def test_min_abs_z_score_explicit_overrides_yaml(self):
        params = ScanPolicyFuturesExtremesInput(min_abs_z_score=0.0)
        out, _, _ = _run(
            params,
            config=_custom_config(default_min_abs_z_score=10.0),
        )
        assert "error" not in out, out.get("error")
        assert len(out["results"]) > 0

    def test_default_metrics_yaml_fallback_resolves(self):
        params = ScanPolicyFuturesExtremesInput(min_abs_z_score=0.0)
        out, _, _ = _run(
            params,
            config=_custom_config(
                default_metrics="volume_level",
            ),
        )
        assert "error" not in out, out.get("error")
        metrics_seen = {row["metric"] for row in out["results"]}
        # YAML default was narrowed to volume_level only.
        assert metrics_seen == {"volume_level"}


# ===========================================================================
# 13. Edge cases
# ===========================================================================

class TestEdgeCases:
    def test_empty_universe_returns_error(self):
        empty = pd.DataFrame(
            columns=[
                "trade_date", "curve_family", "strip_position",
                "contract_code", "field_value",
            ]
        )
        params = ScanPolicyFuturesExtremesInput()
        out, _, _ = _run(
            params,
            price_df=empty, volume_df=empty, oi_df=empty,
        )
        assert "error" in out
        assert "policy-futures universe data" in out["error"]

    def test_all_stems_below_min_periods_returns_error(self):
        price_df, volume_df, oi_df = _default_field_data(days=30)
        params = ScanPolicyFuturesExtremesInput()
        out, _, _ = _run(
            params,
            price_df=price_df, volume_df=volume_df, oi_df=oi_df,
        )
        assert "error" in out
        assert (
            "aligned observations" in out["error"]
            or "scoreable" in out["error"]
        )

    def test_single_stem_universe(self):
        stems = [("SOFR_FUT", 1, "SFRH6")]
        price_df = _build_universe_field_df(
            stems=stems, per_stem_drift={("SOFR_FUT", 1): -2.0},
            base=96.5, days=400, frozen_today=_FROZEN_TODAY,
        )
        volume_df = _build_universe_field_df(
            stems=stems, per_stem_drift={("SOFR_FUT", 1): 100000.0},
            base=300000.0, days=400, frozen_today=_FROZEN_TODAY,
        )
        oi_df = _build_universe_field_df(
            stems=stems, per_stem_drift={("SOFR_FUT", 1): 200000.0},
            base=600000.0, days=400, frozen_today=_FROZEN_TODAY,
        )
        ref = _build_reference_df(stems=stems)
        out, _, _ = _run(
            ScanPolicyFuturesExtremesInput(
                top_n=5, min_abs_z_score=0.0,
                curve_families=["SOFR_FUT"],
            ),
            price_df=price_df, volume_df=volume_df, oi_df=oi_df,
            reference_df=ref,
        )
        assert "error" not in out, out.get("error")
        assert len(out["results"]) >= 1

    def test_empty_reference_returns_error(self):
        empty_ref = pd.DataFrame(
            columns=[
                "curve_family", "contract_code", "strip_position",
                "inverse_pricing", "underlying_contract_code",
                "expiry_date", "security_name", "tick_size",
                "tick_value", "contract_size",
            ]
        )
        out, _, _ = _run(
            ScanPolicyFuturesExtremesInput(),
            reference_df=empty_ref,
        )
        assert "error" in out
        assert "instrument_master" in out["error"]


# ===========================================================================
# 14. Future-anchor guard
# ===========================================================================

class TestFutureAnchorGuard:
    def test_future_as_of_refused(self):
        future_date = _FROZEN_TODAY + timedelta(days=10)
        params = ScanPolicyFuturesExtremesInput(
            as_of_date=future_date, top_n=5, min_abs_z_score=0.0,
        )
        out, _, _ = _run(
            params,
            universe_max_date=_FROZEN_TODAY,
        )
        assert "error" in out
        assert "no scoreable stems" in out["error"]
        assert future_date.isoformat() in out["error"]

    def test_within_data_anchor_passes(self):
        params = ScanPolicyFuturesExtremesInput(
            as_of_date=_FROZEN_TODAY,
            top_n=5, min_abs_z_score=0.0,
        )
        out, _, _ = _run(params)
        assert "error" not in out, out.get("error")

    def test_no_as_of_skips_guard(self):
        """When ``as_of_date`` is None, the future-anchor probe is
        not called (the resolver uses the data's max trade_date)."""
        params = ScanPolicyFuturesExtremesInput()

        def _max_date_should_not_be_called(*args, **kwargs):
            raise AssertionError(
                "max_date probe should not be called when as_of_date "
                "is None"
            )

        target = (
            "rates_agent.policy_futures.tools."
            "scan_policy_futures_extremes.compute"
        )
        price_df, volume_df, oi_df = _default_field_data()
        ref_df = _build_reference_df(stems=_DEFAULT_STEMS)

        def _strip_side_effect(
            *, engine, instrument_type, field_name, start_date,
            curve_families=None, end_date=None,
        ):
            if field_name == "PX_LAST":
                return price_df.copy()
            if field_name == "PX_VOLUME":
                return volume_df.copy()
            if field_name == "OPEN_INT":
                return oi_df.copy()
            return pd.DataFrame()

        with patch(
            f"{target}.fetch_scan_universe_strip_position",
            side_effect=_strip_side_effect,
        ), patch(
            f"{target}.fetch_scan_universe_policy_future_reference",
            return_value=ref_df,
        ), patch(
            f"{target}.fetch_scan_universe_strip_position_max_date",
            side_effect=_max_date_should_not_be_called,
        ), patch(
            f"{target}.date", _FrozenDate,
        ):
            out = calculate_scan_policy_futures_extremes(
                engine=None, params=params,
            )
        assert "error" not in out, out.get("error")


# ===========================================================================
# 15. Fetcher helpers — unit smoke (mocked engine)
# ===========================================================================

class TestFetcherHelpers:
    """Layer-A unit tests for the new
    ``fetch_scan_universe_strip_position`` and
    ``fetch_scan_universe_policy_future_reference`` helpers in
    ``shared.analytics.rates_fetch``. Validate the SQL is
    parameterised, the bind-params route correctly through the
    filtered vs unfiltered paths, and the returned DataFrame has
    the expected columns.

    Also re-verifies the EXISTING tenor-keyed
    ``fetch_scan_universe`` / ``fetch_scan_universe_reference``
    contract is unchanged — backward-compatibility for the
    bond_futures / inflation_linkers / inflation_swaps scanners.
    """

    def test_fetch_scan_universe_strip_position_unfiltered_uses_all_sql(self):
        from shared.analytics import rates_fetch
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_result.keys.return_value = [
            "trade_date", "curve_family", "strip_position",
            "contract_code", "field_value",
        ]
        mock_conn.execute.return_value = mock_result

        df = rates_fetch.fetch_scan_universe_strip_position(
            engine=mock_engine,
            instrument_type="policy_future",
            field_name="PX_LAST",
            start_date=date(2024, 1, 1),
            curve_families=None,
            end_date=date(2026, 4, 8),
        )
        # SQL was passed via mock_conn.execute.
        called_args = mock_conn.execute.call_args
        sql_arg = called_args.args[0]
        # The unfiltered SQL must NOT have a curve_families bind.
        rendered = str(sql_arg)
        assert "ANY(:curve_families)" not in rendered, (
            "unfiltered path must not bind curve_families"
        )
        bind_params = called_args.args[1]
        assert bind_params["instrument_type"] == "policy_future"
        assert bind_params["field_name"] == "PX_LAST"
        assert bind_params["start_date"] == "2024-01-01"
        assert bind_params["end_date"] == "2026-04-08"
        # Returned shape correct.
        assert list(df.columns) == [
            "trade_date", "curve_family", "strip_position",
            "contract_code", "field_value",
        ]

    def test_fetch_scan_universe_strip_position_filtered_uses_curve_bind(self):
        from shared.analytics import rates_fetch
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_result.keys.return_value = [
            "trade_date", "curve_family", "strip_position",
            "contract_code", "field_value",
        ]
        mock_conn.execute.return_value = mock_result

        rates_fetch.fetch_scan_universe_strip_position(
            engine=mock_engine,
            instrument_type="policy_future",
            field_name="PX_LAST",
            start_date=date(2024, 1, 1),
            curve_families=["SOFR_FUT", "EUR_SHORT_RATE_FUT"],
        )
        called_args = mock_conn.execute.call_args
        sql_arg = called_args.args[0]
        bind_params = called_args.args[1]
        # The filtered SQL must use curve_families bind.
        rendered = str(sql_arg)
        assert "ANY(:curve_families)" in rendered
        assert bind_params["curve_families"] == [
            "SOFR_FUT", "EUR_SHORT_RATE_FUT",
        ]
        assert bind_params["end_date"] is None  # default

    def test_fetch_scan_universe_policy_future_reference_unfiltered(self):
        from shared.analytics import rates_fetch
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_result.keys.return_value = [
            "curve_family", "contract_code", "strip_position",
            "inverse_pricing", "underlying_contract_code",
            "expiry_date", "security_name", "tick_size",
            "tick_value", "contract_size",
        ]
        mock_conn.execute.return_value = mock_result

        df = rates_fetch.fetch_scan_universe_policy_future_reference(
            engine=mock_engine,
            as_of_date=date(2026, 4, 8),
        )
        called_args = mock_conn.execute.call_args
        sql_arg = called_args.args[0]
        bind_params = called_args.args[1]
        rendered = str(sql_arg)
        assert "ANY(:curve_families)" not in rendered
        assert bind_params["as_of_date"] == "2026-04-08"
        assert list(df.columns) == [
            "curve_family", "contract_code", "strip_position",
            "inverse_pricing", "underlying_contract_code",
            "expiry_date", "security_name", "tick_size",
            "tick_value", "contract_size",
        ]

    def test_fetch_scan_universe_policy_future_reference_filtered(self):
        from shared.analytics import rates_fetch
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_result.keys.return_value = [
            "curve_family", "contract_code", "strip_position",
            "inverse_pricing", "underlying_contract_code",
            "expiry_date", "security_name", "tick_size",
            "tick_value", "contract_size",
        ]
        mock_conn.execute.return_value = mock_result

        rates_fetch.fetch_scan_universe_policy_future_reference(
            engine=mock_engine,
            as_of_date=date(2026, 4, 8),
            curve_families=["SOFR_FUT"],
        )
        called_args = mock_conn.execute.call_args
        rendered = str(called_args.args[0])
        bind_params = called_args.args[1]
        assert "ANY(:curve_families)" in rendered
        assert bind_params["curve_families"] == ["SOFR_FUT"]
        assert bind_params["as_of_date"] == "2026-04-08"

    def test_fetch_scan_universe_strip_position_max_date_filtered(self):
        from shared.analytics import rates_fetch
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_row = MagicMock()
        mock_row.__getitem__.return_value = date(2026, 4, 8)
        mock_result = MagicMock()
        mock_result.first.return_value = mock_row
        mock_conn.execute.return_value = mock_result

        out = rates_fetch.fetch_scan_universe_strip_position_max_date(
            engine=mock_engine,
            instrument_type="policy_future",
            curve_families=["SOFR_FUT"],
        )
        assert out == date(2026, 4, 8)
        called_args = mock_conn.execute.call_args
        bind_params = called_args.args[1]
        assert bind_params["instrument_type"] == "policy_future"
        assert bind_params["curve_families"] == ["SOFR_FUT"]

    def test_fetch_scan_universe_reference_legacy_projection_unchanged(self):
        """Backward-compat smoke test: the EXISTING tenor-keyed
        ``fetch_scan_universe_reference`` must continue to expose
        its 7-column projection for the inflation_linkers /
        inflation_swaps / bond_futures scanner callers."""
        from shared.analytics import rates_fetch
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_result.keys.return_value = [
            "curve_family", "tenor", "contract_code",
            "maturity_date", "country", "vendor_ticker",
            "underlying_index",
        ]
        mock_conn.execute.return_value = mock_result

        df = rates_fetch.fetch_scan_universe_reference(
            engine=mock_engine,
            instrument_type="inflation_swap",
        )
        # The legacy projection MUST still expose these columns —
        # widening / narrowing would break the existing callers.
        for col in (
            "curve_family", "tenor", "contract_code",
            "maturity_date", "country", "vendor_ticker",
            "underlying_index",
        ):
            assert col in df.columns
        # And MUST NOT (yet) expose any policy_futures-specific
        # columns — those are exposed only by the new helper.
        assert "strip_position" not in df.columns
        assert "inverse_pricing" not in df.columns
