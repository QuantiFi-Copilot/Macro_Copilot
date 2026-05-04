"""
test_swap_spread_compute.py — Unit tests for the cross-domain swap-spread primitive.

Mirrors the prior cross-market suites' shape
(``test_ois_cross_market_spread_compute.py``,
``test_cross_market_spread_compute.py``).  All tests are offline:
the cross-domain fetcher is mocked, ``date.today()`` is frozen.

Covers:
  1. Bundled config.yaml structurally valid + loads cleanly.
  2. ``calculate_swap_spread`` runs end-to-end against synthetic
     input with the bundled config and returns a well-formed
     output.
  3. Sign convention: positive = sovereign trades cheap to OIS.
  4. Convention overrides (z-window / ddof / period offsets /
     bps + zscore rounding / min_periods spy / ffill spy /
     buffer-multiplier defensive max).
  5. ``z_score_round_decimals`` reaches ALL THREE OUTPUT surfaces
     (snapshot + bespoke + canonical) — day-one P2 fix.
  6. ``bps_round_decimals`` reaches ``period_changes(decimals=...)``.
  7. Honest placeholder for ``trailing_range_window_days``.
  8. Schema-layer behaviour: per-leg field_name sentinel + curves-
     must-differ validator.
  9. YAML field-name fall-through end-to-end (per-leg).
 10. Three import paths still resolve to the same Pydantic class.
 11. Canonical TimeSeries output align point-by-point with bespoke
     wire-frozen rows.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.ois.tools.swap_spread import (
    CONFIG_PATH,
    calculate_swap_spread,
    SwapSpreadInput,
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


def _synthetic_cross_domain_df(
    *,
    days: int = 600,
    frozen_today: date = date(2026, 4, 30),
    sov_init: float = 4.30,
    ois_init: float = 4.10,
    sov_drift: float = +0.20,
    ois_drift: float = -0.10,
    sov_curve: str = "UST",
    ois_curve: str = "USD_SOFR_OIS",
) -> pd.DataFrame:
    """Build a 2-curve long-format DataFrame matching the shape
    ``fetch_cross_domain_pair`` returns.  Sovereign + OIS each get
    their own drift so the spread varies materially across the
    window."""
    bdays = pd.bdate_range(
        frozen_today - timedelta(days=days * 2), frozen_today,
    )[-days:]
    n = len(bdays)
    rs = np.random.RandomState(13)
    rows = []
    for cf, base, drift in (
        (sov_curve, sov_init, sov_drift),
        (ois_curve, ois_init, ois_drift),
    ):
        v = np.linspace(base, base + drift, n) + rs.randn(n) * 0.012
        for d, val in zip(bdays, v):
            rows.append({
                "trade_date": d.date(), "curve_family": cf,
                "field_value": val,
            })
    return pd.DataFrame(rows)


class _FrozenDate(date):
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


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
    "sovereign_leg_default_field": "YLD_YTM_MID",
    "ois_leg_default_field": "PX_LAST",
}


def _build_config(**overrides) -> ToolConfig:
    defaults = dict(_BUNDLED_DEFAULTS)
    defaults.update(overrides)
    return ToolConfig(
        tool=ToolMeta(name="t", domain="d", description="x"),
        methodology=MethodologyMeta(what_it_does="x"),
        conventions={
            k: Convention(value=v, source="test", rationale="test")
            for k, v in defaults.items()
        },
    )


def _run(params, raw_df, config=None):
    with patch(
        "rates_agent.ois.tools.swap_spread.compute.fetch_cross_domain_pair",
        return_value=raw_df,
    ), patch(
        "rates_agent.ois.tools.swap_spread.compute.date",
        _FrozenDate,
    ):
        return calculate_swap_spread(
            engine=None, params=params, config=config,
        )


def _params(**overrides) -> SwapSpreadInput:
    defaults = dict(
        sovereign_curve_family="UST",
        ois_curve_family="USD_SOFR_OIS",
        tenor="10Y",
        lookback_days=365,
    )
    defaults.update(overrides)
    return SwapSpreadInput(**defaults)


# ===========================================================================
# 1. Bundled config.yaml
# ===========================================================================

class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "calculate_swap_spread_tool"
        assert cfg.tool.domain == "ois"

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
            "sovereign_leg_default_field",
            "ois_leg_default_field",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults_match_design(self):
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
        assert cfg.convention_value("sovereign_leg_default_field") == "YLD_YTM_MID"
        assert cfg.convention_value("ois_leg_default_field") == "PX_LAST"

    def test_uses_distinct_per_leg_field_convention_names(self):
        """The cross-domain primitive declares BOTH a sovereign-leg
        and an OIS-leg default field.  Their convention names are
        deliberately different from the sibling-domain conventions
        (``default_field_name`` / ``default_swap_rate_field``) so
        the cross-config lint does not treat the cross-domain
        primitive's two distinct knobs as collisions with the
        single-leg sibling defaults."""
        cfg = load_tool_config(CONFIG_PATH)
        assert "sovereign_leg_default_field" in cfg.conventions
        assert "ois_leg_default_field" in cfg.conventions
        assert "default_field_name" not in cfg.conventions
        assert "default_swap_rate_field" not in cfg.conventions

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 3
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "trailing_range_window_days" in joined
        assert "clipping" in joined
        assert "sign" in joined.lower()


# ===========================================================================
# 2. End-to-end happy path + sign convention
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        out = _run(_params(), _synthetic_cross_domain_df())
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        for k in (
            "as_of_date", "sovereign_curve_family", "ois_curve_family",
            "tenor", "spread_label", "current_spread_bps",
            "daily_change_bps", "weekly_change_bps", "monthly_change_bps",
            "current_z_score", "rolling_window_days",
            "high_252d_bps", "low_252d_bps", "percentile_252d",
            "sovereign_yield_pct", "ois_rate_pct",
        ):
            assert k in cm, f"missing {k}"

        # Wire-frozen field names — explicit check that the rename
        # plan was NOT silently applied.
        assert "high_window_bps" not in cm
        assert "trailing_window_days" not in cm

        assert cm["spread_label"] == "UST-USD_SOFR_OIS 10Y"
        assert cm["rolling_window_days"] == 252

        ts = out["time_series"]
        assert len(ts) > 0
        assert set(ts[0].keys()) == {"date", "spread_bps", "z_score"}

    def test_sign_convention_sovereign_minus_ois(self):
        """Spread = (sovereign_yield − ois_rate) × 100.  When the
        sovereign series is HIGHER than the OIS series, the spread
        must be POSITIVE.  When LOWER, NEGATIVE."""
        # Case A: sovereign > ois → positive spread
        raw_pos = _synthetic_cross_domain_df(
            sov_init=4.50, ois_init=4.00,
            sov_drift=0.0, ois_drift=0.0,
        )
        out_pos = _run(_params(), raw_pos)
        spread_pos = out_pos["current_metrics"]["current_spread_bps"]
        # ~50bps positive (4.50 - 4.00 = 0.50% × 100 = 50bps)
        assert spread_pos > 0
        assert 40 < spread_pos < 60  # noise tolerance

        # Case B: sovereign < ois → negative spread
        raw_neg = _synthetic_cross_domain_df(
            sov_init=4.00, ois_init=4.50,
            sov_drift=0.0, ois_drift=0.0,
        )
        out_neg = _run(_params(), raw_neg)
        spread_neg = out_neg["current_metrics"]["current_spread_bps"]
        assert spread_neg < 0
        assert -60 < spread_neg < -40

    def test_explicit_default_config_matches_auto_loaded(self):
        raw_df = _synthetic_cross_domain_df()
        out_auto = _run(_params(), raw_df, config=None)
        out_explicit = _run(_params(), raw_df, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def test_z_score_window_override_changes_z(self):
        raw_df = _synthetic_cross_domain_df()
        out_default = _run(_params(), raw_df, _build_config())
        out_short = _run(_params(), raw_df, _build_config(z_score_window_days=120))
        assert (
            out_default["current_metrics"]["current_z_score"]
            != out_short["current_metrics"]["current_z_score"]
        )

    def test_z_score_ddof_override_changes_z(self):
        raw_df = _synthetic_cross_domain_df()
        out_sample = _run(_params(), raw_df, _build_config(z_score_ddof=1))
        out_pop = _run(_params(), raw_df, _build_config(z_score_ddof=0))
        assert (
            out_sample["current_metrics"]["current_z_score"]
            != out_pop["current_metrics"]["current_z_score"]
        )

    def test_period_offsets_override_changes_changes(self):
        raw_df = _synthetic_cross_domain_df()
        out_default = _run(_params(), raw_df, _build_config())
        out_wider = _run(
            _params(), raw_df, _build_config(daily_change_offset_rows=5),
        )
        assert (
            out_default["current_metrics"]["daily_change_bps"]
            != out_wider["current_metrics"]["daily_change_bps"]
        )

    def test_ffill_limit_passed_to_pivot(self):
        from shared.analytics.spreads import pivot_and_align_tenors as real_pivot
        raw_df = _synthetic_cross_domain_df()
        with patch(
            "rates_agent.ois.tools.swap_spread.compute.fetch_cross_domain_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.swap_spread.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.ois.tools.swap_spread.compute.pivot_and_align_tenors",
            wraps=real_pivot,
        ) as spy:
            calculate_swap_spread(
                engine=None, params=_params(),
                config=_build_config(ffill_limit_days=2),
            )
        assert spy.call_count == 1
        assert spy.call_args.kwargs["ffill_limit"] == 2

    def test_min_periods_passed_to_rolling_zscore(self):
        from shared.analytics.spreads import rolling_zscore as real_rolling
        raw_df = _synthetic_cross_domain_df()
        with patch(
            "rates_agent.ois.tools.swap_spread.compute.fetch_cross_domain_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.swap_spread.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.ois.tools.swap_spread.compute.rolling_zscore",
            wraps=real_rolling,
        ) as spy:
            calculate_swap_spread(
                engine=None, params=_params(),
                config=_build_config(z_score_min_periods=200),
            )
        assert spy.call_count >= 1
        assert spy.call_args.kwargs["min_periods"] == 200
        assert spy.call_args.kwargs["window"] == 252

    def test_bps_round_decimals_override_changes_precision(self):
        raw_df = _synthetic_cross_domain_df()
        out_lo = _run(_params(), raw_df, _build_config(bps_round_decimals=2))
        out_hi = _run(_params(), raw_df, _build_config(bps_round_decimals=4))

        v_lo = out_lo["current_metrics"]["current_spread_bps"]
        v_hi = out_hi["current_metrics"]["current_spread_bps"]
        assert round(v_hi, 2) == v_lo

        ts_lo = out_lo["time_series"]
        ts_hi = out_hi["time_series"]
        differs = any(
            round(hi["spread_bps"], 2) == lo["spread_bps"]
            and hi["spread_bps"] != lo["spread_bps"]
            for hi, lo in zip(ts_hi, ts_lo)
        )
        assert differs


# ===========================================================================
# 4. bps_round_decimals reaches period_changes
# ===========================================================================

class TestBpsRoundingReachesPeriodChanges:
    def test_period_changes_called_with_decimals(self):
        from shared.analytics import levels as levels_mod
        raw_df = _synthetic_cross_domain_df()
        with patch(
            "rates_agent.ois.tools.swap_spread.compute.fetch_cross_domain_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.swap_spread.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.ois.tools.swap_spread.compute.period_changes",
            wraps=levels_mod.period_changes,
        ) as spy:
            calculate_swap_spread(
                engine=None, params=_params(),
                config=_build_config(bps_round_decimals=4),
            )
        assert spy.call_count == 1
        assert spy.call_args.kwargs["decimals"] == 4
        assert spy.call_args.kwargs["already_bps"] is True


# ===========================================================================
# 5. z_score_round_decimals reaches ALL THREE OUTPUT surfaces
# ===========================================================================

class TestZScoreRoundDecimalsReachesOutput:
    def test_current_z_score_uses_z_round_decimals(self):
        raw_df = _synthetic_cross_domain_df()
        out_hi = _run(_params(), raw_df, _build_config(z_score_round_decimals=6))
        out_lo = _run(_params(), raw_df, _build_config(z_score_round_decimals=4))
        z_hi = out_hi["current_metrics"]["current_z_score"]
        z_lo = out_lo["current_metrics"]["current_z_score"]
        assert z_hi is not None and z_lo is not None
        assert round(z_hi, 4) == z_lo

    def test_bespoke_time_series_z_score_uses_z_round_decimals(self):
        raw_df = _synthetic_cross_domain_df()
        out_hi = _run(_params(), raw_df, _build_config(z_score_round_decimals=6))
        out_lo = _run(_params(), raw_df, _build_config(z_score_round_decimals=4))
        differs = any(
            hi["z_score"] is not None and lo["z_score"] is not None
            and round(hi["z_score"], 4) == lo["z_score"]
            and hi["z_score"] != lo["z_score"]
            for hi, lo in zip(out_hi["time_series"], out_lo["time_series"])
        )
        assert differs

    def test_canonical_time_series_zscore_uses_z_round_decimals(self):
        raw_df = _synthetic_cross_domain_df()
        out_hi = _run(_params(), raw_df, _build_config(z_score_round_decimals=6))
        out_lo = _run(_params(), raw_df, _build_config(z_score_round_decimals=4))

        cz_hi = out_hi["time_series_zscore"]["rows"]
        cz_lo = out_lo["time_series_zscore"]["rows"]
        differs = any(
            hi["value"] is not None and lo["value"] is not None
            and round(hi["value"], 4) == lo["value"]
            and hi["value"] != lo["value"]
            for hi, lo in zip(cz_hi, cz_lo)
        )
        assert differs

        # Cross-surface consistency: bespoke and canonical agree
        # row-by-row at >4 decimals.
        for i, (b, c) in enumerate(zip(out_hi["time_series"], cz_hi)):
            assert b["z_score"] == c["value"], (
                f"row {i}: bespoke {b['z_score']!r} != canonical {c['value']!r}"
            )


# ===========================================================================
# 6. Honest-placeholder guard on trailing_range_window_days
# ===========================================================================

class TestTrailingWindowGuard:
    def test_unsupported_trailing_window_raises(self):
        with pytest.raises(NotImplementedError) as exc_info:
            calculate_swap_spread(
                engine=None, params=_params(),
                config=_build_config(trailing_range_window_days=180),
            )
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_bps" in msg
        assert "planned_extensions" in msg

    def test_supported_default_does_not_raise(self):
        out = _run(_params(), _synthetic_cross_domain_df(),
                   _build_config(trailing_range_window_days=252))
        assert "error" not in out


# ===========================================================================
# 7. Schema-layer behaviour
# ===========================================================================

class TestSchemaBehaviour:
    def test_per_leg_field_names_default_to_none(self):
        params = SwapSpreadInput(
            sovereign_curve_family="UST",
            ois_curve_family="USD_SOFR_OIS", tenor="10Y",
        )
        assert params.sovereign_field_name is None
        assert params.ois_field_name is None

    def test_explicit_per_leg_field_names_pass_through(self):
        params = SwapSpreadInput(
            sovereign_curve_family="UST",
            ois_curve_family="USD_SOFR_OIS", tenor="10Y",
            sovereign_field_name="YLD_BID",
            ois_field_name="PX_BID",
        )
        assert params.sovereign_field_name == "YLD_BID"
        assert params.ois_field_name == "PX_BID"

    def test_curves_must_differ_validator(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc_info:
            SwapSpreadInput(
                sovereign_curve_family="UST",
                ois_curve_family="UST",  # same!
                tenor="10Y",
            )
        msg = str(exc_info.value).lower()
        assert "different" in msg or "must" in msg


class TestPerLegYamlFallthrough:
    """End-to-end proof that BOTH per-leg YAML defaults reach the
    fetcher independently."""

    def _capture_fetcher_kwargs(self, params, config):
        raw_df = _synthetic_cross_domain_df()
        with patch(
            "rates_agent.ois.tools.swap_spread.compute.fetch_cross_domain_pair",
            return_value=raw_df,
        ) as spy, patch(
            "rates_agent.ois.tools.swap_spread.compute.date",
            _FrozenDate,
        ):
            calculate_swap_spread(
                engine=None, params=params, config=config,
            )
        assert spy.call_count == 1
        return spy.call_args.kwargs

    def test_omitted_per_leg_field_names_use_yaml_defaults(self):
        kwargs = self._capture_fetcher_kwargs(
            _params(),
            _build_config(
                sovereign_leg_default_field="YLD_YTM_MID",
                ois_leg_default_field="PX_LAST",
            ),
        )
        assert kwargs["sovereign_field_name"] == "YLD_YTM_MID"
        assert kwargs["ois_field_name"] == "PX_LAST"

    def test_yaml_per_leg_overrides_change_resolved_fields(self):
        kwargs = self._capture_fetcher_kwargs(
            _params(),
            _build_config(
                sovereign_leg_default_field="YLD_BID",
                ois_leg_default_field="PX_BID",
            ),
        )
        assert kwargs["sovereign_field_name"] == "YLD_BID"
        assert kwargs["ois_field_name"] == "PX_BID"

    def test_explicit_per_leg_overrides_yaml(self):
        kwargs = self._capture_fetcher_kwargs(
            _params(
                sovereign_field_name="YLD_ASK",
                ois_field_name="PX_ASK",
            ),
            _build_config(
                sovereign_leg_default_field="YLD_YTM_MID",
                ois_leg_default_field="PX_LAST",
            ),
        )
        assert kwargs["sovereign_field_name"] == "YLD_ASK"
        assert kwargs["ois_field_name"] == "PX_ASK"

    def test_explicit_only_one_leg_other_falls_through(self):
        """Mixing modes: caller passes only sovereign override; OIS
        leg falls through to YAML default."""
        kwargs = self._capture_fetcher_kwargs(
            _params(sovereign_field_name="YLD_BID"),
            _build_config(
                sovereign_leg_default_field="YLD_YTM_MID",
                ois_leg_default_field="PX_LAST",
            ),
        )
        assert kwargs["sovereign_field_name"] == "YLD_BID"
        assert kwargs["ois_field_name"] == "PX_LAST"


# ===========================================================================
# 8. Buffer sizing — defensive max(z_window, trailing_window)
# ===========================================================================

class TestBufferSizing:
    def _capture_start_date(self, params, config):
        raw_df = _synthetic_cross_domain_df()
        with patch(
            "rates_agent.ois.tools.swap_spread.compute.fetch_cross_domain_pair",
            return_value=raw_df,
        ) as spy, patch(
            "rates_agent.ois.tools.swap_spread.compute.date",
            _FrozenDate,
        ):
            calculate_swap_spread(engine=None, params=params, config=config)
        assert spy.call_count == 1
        return spy.call_args.kwargs["start_date"]

    def test_buffer_uses_larger_of_z_window_and_trailing_window(self):
        start_default = self._capture_start_date(_params(), _build_config())
        start_smaller_z = self._capture_start_date(
            _params(), _build_config(z_score_window_days=120),
        )
        # Same buffer ⇒ same start_date when only z_window shrinks
        # (trailing=252 still drives the max).
        assert start_default == start_smaller_z


# ===========================================================================
# 9. Import path backward-compat
# ===========================================================================

class TestImportPathBackwardCompat:
    def test_calculate_swap_spread_via_package_init(self):
        from rates_agent.ois.tools.swap_spread import (
            calculate_swap_spread as via_package,
        )
        from rates_agent.ois.tools.swap_spread.compute import (
            calculate_swap_spread as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.ois.tools.swap_spread import (
            SwapSpreadInput as via_package,
        )
        from rates_agent.ois.tools.swap_spread.schemas import (
            SwapSpreadInput as via_schemas,
        )
        from rates_agent.ois.tools.schemas import (
            SwapSpreadInput as via_hub,
        )
        assert via_package is via_schemas
        assert via_package is via_hub


# ===========================================================================
# 10. Canonical TimeSeries output
# ===========================================================================

class TestCanonicalTimeSeries:
    def test_time_series_spread_field_present(self):
        out = _run(_params(), _synthetic_cross_domain_df())
        assert "time_series_spread" in out
        assert isinstance(out["time_series_spread"], dict)

    def test_time_series_zscore_field_present(self):
        out = _run(_params(), _synthetic_cross_domain_df())
        assert "time_series_zscore" in out
        assert isinstance(out["time_series_zscore"], dict)

    def test_spread_series_uses_BPS(self):
        out = _run(_params(), _synthetic_cross_domain_df())
        assert out["time_series_spread"]["units"] == "bps"

    def test_zscore_series_uses_Z_SCORE(self):
        out = _run(_params(), _synthetic_cross_domain_df())
        assert out["time_series_zscore"]["units"] == "z_score"

    def test_spread_series_name_uses_swap_spread_suffix(self):
        out = _run(_params(), _synthetic_cross_domain_df())
        # ``_swap_spread`` suffix distinguishes from sovereign
        # ``_spread`` and OIS ``_ois_spread`` / ``_ois_cross_spread``.
        assert (
            out["time_series_spread"]["series_name"]
            == "ust_usd_sofr_ois_10y_swap_spread"
        )

    def test_zscore_series_name_uses_swap_spread_zscore_suffix(self):
        out = _run(_params(), _synthetic_cross_domain_df())
        assert (
            out["time_series_zscore"]["series_name"]
            == "ust_usd_sofr_ois_10y_swap_spread_zscore"
        )

    def test_both_series_length_equals_bespoke_length(self):
        out = _run(_params(), _synthetic_cross_domain_df())
        bespoke = out["time_series"]
        assert len(out["time_series_spread"]["rows"]) == len(bespoke)
        assert len(out["time_series_zscore"]["rows"]) == len(bespoke)

    def test_spread_values_match_bespoke_pointwise(self):
        out = _run(_params(), _synthetic_cross_domain_df())
        canonical = out["time_series_spread"]
        bespoke = out["time_series"]
        for i, (c_row, b_row) in enumerate(zip(canonical["rows"], bespoke)):
            assert c_row["date"] == b_row["date"], f"row {i}: date mismatch"
            assert c_row["value"] == b_row["spread_bps"], (
                f"row {i}: value mismatch"
            )

    def test_zscore_values_match_bespoke_pointwise(self):
        out = _run(_params(), _synthetic_cross_domain_df())
        canonical = out["time_series_zscore"]
        bespoke = out["time_series"]
        for i, (c_row, b_row) in enumerate(zip(canonical["rows"], bespoke)):
            assert c_row["date"] == b_row["date"], f"row {i}: date mismatch"
            assert c_row["value"] == b_row["z_score"], (
                f"row {i}: value mismatch"
            )

    def test_both_canonical_series_validate_against_TimeSeries_schema(self):
        from shared.schemas import TimeSeries
        out = _run(_params(), _synthetic_cross_domain_df())
        TimeSeries.model_validate(out["time_series_spread"])
        TimeSeries.model_validate(out["time_series_zscore"])
