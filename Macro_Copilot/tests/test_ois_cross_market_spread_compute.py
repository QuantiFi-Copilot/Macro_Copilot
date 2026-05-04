"""
test_ois_cross_market_spread_compute.py — Unit tests for the OIS
cross_market_spread migration.

Mirrors ``test_cross_market_spread_compute.py`` (the sovereign analog)
and the existing OIS migration tests
(``test_ois_rate_level_compute.py``,
``test_ois_curve_spread_compute.py``).

Covers:
  1. Bundled config.yaml structurally valid + loads cleanly.
  2. ``calculate_ois_cross_market_spread`` runs end-to-end against
     synthetic input with the bundled config and returns a well-
     formed output.
  3. Convention overrides actually change behaviour
     (z-window / ddof / period offsets / bps+zscore rounding /
     min_periods spy / ffill spy / buffer-multiplier defensive max).
  4. ``bps_round_decimals`` reaches ``period_changes(decimals=...)``.
  5. ``z_score_round_decimals`` reaches the OUTPUT boundary
     (snapshot + bespoke ts + canonical ts) — day-one P2 fix from
     OIS curve_spread (PR #63).
  6. Honest placeholder for ``trailing_range_window_days`` raises
     NotImplementedError on values != 252.
  7. Schema-layer behaviour: field_name defaults to None (sentinel),
     compute() resolves it against ``default_swap_rate_field``;
     curves-must-differ validator enforced.
  8. Three import paths still resolve to the same Pydantic class;
     legacy ``schemas.cross_market`` shim is gone.
  9. Buffer sizing uses ``max(z_window, trailing_window)`` (defensive
     for future decoupling).
 10. Canonical TimeSeries output (BPS spread + Z_SCORE rolling) align
     point-by-point with the bespoke wire-frozen ``time_series`` rows.

Tests are fully offline — DB fetcher mocked, ``date.today()`` frozen.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.ois.tools.cross_market_spread import (
    CONFIG_PATH,
    calculate_ois_cross_market_spread,
    OISCrossMarketSpreadInput,
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


def _synthetic_raw_df(
    *,
    days: int = 600,
    frozen_today: date = date(2026, 4, 30),
    cf1_drift: float = -0.30,
    cf2_drift: float = +0.10,
    cf1_base: float = 4.50,
    cf2_base: float = 4.20,
    cf1: str = "USD_SOFR_OIS",
    cf2: str = "EUR_ESTR_OIS",
) -> pd.DataFrame:
    """Build a 2-curve long-format DataFrame matching the shape
    fetch_cross_market_pair returns.  Two OIS curve_family values,
    each with its own drift so the spread varies across the window.
    """
    bdays = pd.bdate_range(frozen_today - timedelta(days=days * 2), frozen_today)
    bdays = bdays[-days:]
    n = len(bdays)
    rows = []
    base = {cf1: cf1_base, cf2: cf2_base}
    drifts = {cf1: cf1_drift, cf2: cf2_drift}
    for cf, base_v in base.items():
        series = np.linspace(base_v, base_v + drifts[cf], n)
        for d, v in zip(bdays, series):
            rows.append({
                "trade_date": d.date(), "curve_family": cf, "field_value": v,
            })
    return pd.DataFrame(rows)


class _FrozenDate(date):
    """Patches ``compute.date`` so the fetch ``start_date`` is
    deterministic.  The OIS tool's display cutoff is anchored to the
    data's last index date (not ``date.today()``), so the frozen-today
    value only matters for the fetch-window computation (which is
    mocked away in these tests anyway).
    """
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
    "default_swap_rate_field": "PX_LAST",
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
        "rates_agent.ois.tools.cross_market_spread.compute.fetch_cross_market_pair",
        return_value=raw_df,
    ), patch(
        "rates_agent.ois.tools.cross_market_spread.compute.date",
        _FrozenDate,
    ):
        return calculate_ois_cross_market_spread(
            engine=None, params=params, config=config,
        )


def _params(**overrides) -> OISCrossMarketSpreadInput:
    defaults = dict(
        curve_family_1="USD_SOFR_OIS",
        curve_family_2="EUR_ESTR_OIS",
        tenor="2Y",
        lookback_days=365,
    )
    defaults.update(overrides)
    return OISCrossMarketSpreadInput(**defaults)


# ===========================================================================
# 1. Bundled config.yaml
# ===========================================================================

class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "calculate_ois_cross_market_spread_tool"
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
            "default_swap_rate_field",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults_match_legacy(self):
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
        assert cfg.convention_value("default_swap_rate_field") == "PX_LAST"

    def test_no_default_field_name_collision_with_sovereign(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert "default_swap_rate_field" in cfg.conventions
        assert "default_field_name" not in cfg.conventions, (
            "OIS cross_market_spread must not redeclare the sovereign "
            "convention name — would clash with sovereign value "
            "YLD_YTM_MID in the cross-config lint."
        )

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 4
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "trailing_range_window_days" in joined
        assert "clipping" in joined
        assert "anchoring" in joined.lower()
        assert "direction" in joined.lower()


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        raw_df = _synthetic_raw_df()
        out = _run(_params(), raw_df)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        for k in (
            "as_of_date", "curve_family_1", "curve_family_2", "tenor",
            "spread_label", "current_spread_bps",
            "daily_change_bps", "weekly_change_bps", "monthly_change_bps",
            "current_z_score", "rolling_window_days",
            "high_252d_bps", "low_252d_bps", "percentile_252d",
            "curve_family_1_rate", "curve_family_2_rate",
        ):
            assert k in cm, f"missing {k}"

        # OIS-specific snapshot fields use "rate" (not "yield").
        assert "curve_family_1_yield" not in cm
        assert "curve_family_2_yield" not in cm

        # Wire-frozen field names — explicit check that the rename
        # plan was NOT silently applied.
        assert "high_window_bps" not in cm
        assert "trailing_window_days" not in cm

        assert cm["spread_label"] == "USD_SOFR_OIS-EUR_ESTR_OIS 2Y"
        assert cm["rolling_window_days"] == 252

        ts = out["time_series"]
        assert len(ts) > 0
        assert set(ts[0].keys()) == {"date", "spread_bps", "z_score"}

    def test_explicit_config_matches_auto_loaded(self):
        raw_df = _synthetic_raw_df()
        out_auto = _run(_params(), raw_df, config=None)
        out_explicit = _run(_params(), raw_df, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def test_z_score_window_override_changes_z(self):
        raw_df = _synthetic_raw_df()
        out_default = _run(_params(), raw_df, _build_config())
        out_short = _run(_params(), raw_df, _build_config(z_score_window_days=120))
        assert (
            out_default["current_metrics"]["current_z_score"]
            != out_short["current_metrics"]["current_z_score"]
        )

    def test_z_score_ddof_override_changes_z(self):
        raw_df = _synthetic_raw_df()
        out_sample = _run(_params(), raw_df, _build_config(z_score_ddof=1))
        out_pop = _run(_params(), raw_df, _build_config(z_score_ddof=0))
        assert (
            out_sample["current_metrics"]["current_z_score"]
            != out_pop["current_metrics"]["current_z_score"]
        )

    def test_period_offsets_override_changes_changes(self):
        raw_df = _synthetic_raw_df()
        out_default = _run(_params(), raw_df, _build_config())
        out_wider = _run(_params(), raw_df, _build_config(daily_change_offset_rows=5))
        assert (
            out_default["current_metrics"]["daily_change_bps"]
            != out_wider["current_metrics"]["daily_change_bps"]
        )

    def test_ffill_limit_passed_to_pivot(self):
        from shared.analytics.spreads import pivot_and_align_tenors as real_pivot
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.ois.tools.cross_market_spread.compute.fetch_cross_market_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.cross_market_spread.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.ois.tools.cross_market_spread.compute.pivot_and_align_tenors",
            wraps=real_pivot,
        ) as spy:
            calculate_ois_cross_market_spread(
                engine=None, params=_params(),
                config=_build_config(ffill_limit_days=2),
            )
        assert spy.call_count == 1
        assert spy.call_args.kwargs["ffill_limit"] == 2

    def test_min_periods_passed_to_rolling_zscore(self):
        from shared.analytics.spreads import rolling_zscore as real_rolling
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.ois.tools.cross_market_spread.compute.fetch_cross_market_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.cross_market_spread.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.ois.tools.cross_market_spread.compute.rolling_zscore",
            wraps=real_rolling,
        ) as spy:
            calculate_ois_cross_market_spread(
                engine=None, params=_params(),
                config=_build_config(z_score_min_periods=200),
            )
        assert spy.call_count >= 1
        assert spy.call_args.kwargs["min_periods"] == 200
        assert spy.call_args.kwargs["window"] == 252

    def test_bps_round_decimals_override_changes_precision(self):
        raw_df = _synthetic_raw_df(cf1_drift=-0.32193, cf2_drift=+0.10987)
        out_lo = _run(_params(), raw_df, _build_config(bps_round_decimals=2))
        out_hi = _run(_params(), raw_df, _build_config(bps_round_decimals=4))

        v_lo = out_lo["current_metrics"]["current_spread_bps"]
        v_hi = out_hi["current_metrics"]["current_spread_bps"]
        # 2-decimal == 4-decimal rounded back to 2
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
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.ois.tools.cross_market_spread.compute.fetch_cross_market_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.cross_market_spread.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.ois.tools.cross_market_spread.compute.period_changes",
            wraps=levels_mod.period_changes,
        ) as spy:
            calculate_ois_cross_market_spread(
                engine=None, params=_params(),
                config=_build_config(bps_round_decimals=4),
            )
        assert spy.call_count == 1
        assert spy.call_args.kwargs["decimals"] == 4
        assert spy.call_args.kwargs["already_bps"] is True


# ===========================================================================
# 5. z_score_round_decimals reaches the OUTPUT boundary
#    (the same P2 fix Codex caught on OIS curve_spread — applied
#    day one this time, across all three exposed surfaces)
# ===========================================================================

class TestZScoreRoundDecimalsReachesOutput:
    def test_current_z_score_uses_z_round_decimals(self):
        raw_df = _synthetic_raw_df(cf1_drift=-0.32193, cf2_drift=+0.10987)
        out_hi = _run(_params(), raw_df, _build_config(z_score_round_decimals=6))
        out_lo = _run(_params(), raw_df, _build_config(z_score_round_decimals=4))

        z_hi = out_hi["current_metrics"]["current_z_score"]
        z_lo = out_lo["current_metrics"]["current_z_score"]
        assert z_hi is not None and z_lo is not None
        # Contract: at decimals=6, value rounded back to 4 == value at decimals=4
        assert round(z_hi, 4) == z_lo

    def test_bespoke_time_series_z_score_uses_z_round_decimals(self):
        raw_df = _synthetic_raw_df(cf1_drift=-0.32193, cf2_drift=+0.10987)
        out_hi = _run(_params(), raw_df, _build_config(z_score_round_decimals=6))
        out_lo = _run(_params(), raw_df, _build_config(z_score_round_decimals=4))

        differs = any(
            hi["z_score"] is not None and lo["z_score"] is not None
            and round(hi["z_score"], 4) == lo["z_score"]
            and hi["z_score"] != lo["z_score"]
            for hi, lo in zip(out_hi["time_series"], out_lo["time_series"])
        )
        assert differs, (
            "no bespoke time_series row exhibited sub-4-decimal "
            "precision at z_score_round_decimals=6 — safe_float() at "
            "the output boundary may be ignoring the YAML override"
        )

    def test_canonical_time_series_zscore_uses_z_round_decimals(self):
        """Codex P2 follow-up from OIS curve_spread: the canonical
        builder must thread z_round_decimals through to safe_float so
        the canonical surface honours >4dp precision too."""
        raw_df = _synthetic_raw_df(cf1_drift=-0.32193, cf2_drift=+0.10987)
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
        assert differs, (
            "no canonical time_series_zscore row exhibited sub-4-decimal "
            "precision at z_score_round_decimals=6 — the canonical "
            "builder may not be passing decimals=z_round_decimals to "
            "safe_float"
        )

        # Cross-surface consistency: bespoke and canonical must still
        # agree row-by-row at >4 decimals.
        for i, (b_row, c_row) in enumerate(
            zip(out_hi["time_series"], cz_hi)
        ):
            assert b_row["z_score"] == c_row["value"], (
                f"row {i}: bespoke z_score {b_row['z_score']!r} != "
                f"canonical value {c_row['value']!r} at "
                "z_score_round_decimals=6"
            )


# ===========================================================================
# 6. Honest-placeholder guard on trailing_range_window_days
# ===========================================================================

class TestTrailingWindowGuard:
    def test_unsupported_trailing_window_raises(self):
        with pytest.raises(NotImplementedError) as exc_info:
            calculate_ois_cross_market_spread(
                engine=None, params=_params(),
                config=_build_config(trailing_range_window_days=180),
            )
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_bps" in msg
        assert "planned_extensions" in msg

    def test_supported_default_does_not_raise(self):
        raw_df = _synthetic_raw_df()
        out = _run(_params(), raw_df, _build_config(trailing_range_window_days=252))
        assert "error" not in out


# ===========================================================================
# 7. Schema-layer field_name behaviour (sentinel pattern)
# ===========================================================================

class TestFieldNameSchemaBehaviour:
    def test_field_name_default_is_none(self):
        params = OISCrossMarketSpreadInput(
            curve_family_1="USD_SOFR_OIS",
            curve_family_2="EUR_ESTR_OIS",
            tenor="2Y",
        )
        assert params.field_name is None

    def test_explicit_field_name_passes_through(self):
        params = OISCrossMarketSpreadInput(
            curve_family_1="USD_SOFR_OIS",
            curve_family_2="EUR_ESTR_OIS",
            tenor="2Y",
            field_name="PX_BID",
        )
        assert params.field_name == "PX_BID"

    def test_curves_must_differ_validator(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc_info:
            OISCrossMarketSpreadInput(
                curve_family_1="USD_SOFR_OIS",
                curve_family_2="USD_SOFR_OIS",
                tenor="2Y",
            )
        msg = str(exc_info.value).lower()
        assert "different" in msg or "must" in msg


class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_swap_rate_field reaches fetch."""

    def _capture_field_name(self, params, config):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.ois.tools.cross_market_spread.compute.fetch_cross_market_pair",
            return_value=raw_df,
        ) as spy, patch(
            "rates_agent.ois.tools.cross_market_spread.compute.date",
            _FrozenDate,
        ):
            calculate_ois_cross_market_spread(
                engine=None, params=params, config=config,
            )
        assert spy.call_count == 1
        return spy.call_args.kwargs["field_name"]

    def test_omitted_field_name_uses_yaml_default(self):
        passed = self._capture_field_name(
            _params(), _build_config(default_swap_rate_field="PX_LAST"),
        )
        assert passed == "PX_LAST"

    def test_yaml_override_changes_resolved_field(self):
        passed = self._capture_field_name(
            _params(), _build_config(default_swap_rate_field="PX_BID"),
        )
        assert passed == "PX_BID"

    def test_explicit_field_name_overrides_yaml(self):
        passed = self._capture_field_name(
            _params(field_name="PX_ASK"),
            _build_config(default_swap_rate_field="PX_LAST"),
        )
        assert passed == "PX_ASK"


# ===========================================================================
# 8. Buffer sizing — defensive max(z_window, trailing_window)
# ===========================================================================

class TestBufferSizing:
    """When the YAML decouples z_window from trailing_window in a
    future migration, the fetch buffer must already be sized off the
    LARGER of the two so neither stat starves."""

    def _capture_start_date(self, params, config):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.ois.tools.cross_market_spread.compute.fetch_cross_market_pair",
            return_value=raw_df,
        ) as spy, patch(
            "rates_agent.ois.tools.cross_market_spread.compute.date",
            _FrozenDate,
        ):
            calculate_ois_cross_market_spread(
                engine=None, params=params, config=config,
            )
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
    def test_calculate_ois_cross_market_spread_via_package_init(self):
        from rates_agent.ois.tools.cross_market_spread import (
            calculate_ois_cross_market_spread as via_package,
        )
        from rates_agent.ois.tools.cross_market_spread.compute import (
            calculate_ois_cross_market_spread as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.ois.tools.cross_market_spread import (
            OISCrossMarketSpreadInput as via_package,
        )
        from rates_agent.ois.tools.cross_market_spread.schemas import (
            OISCrossMarketSpreadInput as via_schemas,
        )
        from rates_agent.ois.tools.schemas import (
            OISCrossMarketSpreadInput as via_hub,
        )
        assert via_package is via_schemas
        assert via_package is via_hub

    def test_legacy_single_file_path_is_deleted(self):
        """The legacy single-file
        ``rates_agent/ois/tools/cross_market_spread.py`` and
        ``rates_agent/ois/tools/schemas/cross_market.py`` must NOT be
        importable as modules — the per-tool-folder package now owns
        those names."""
        import importlib
        import rates_agent.ois.tools.cross_market_spread as pkg
        assert hasattr(pkg, "__path__"), (
            "cross_market_spread must be a package (folder), not a "
            "single-file module"
        )
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(
                "rates_agent.ois.tools.schemas.cross_market"
            )


# ===========================================================================
# 10. Canonical TimeSeries output
# ===========================================================================

class TestCanonicalTimeSeries:
    """Pin the canonical TimeSeries contract on the OIS
    cross_market_spread surface.  Two canonical fields
    (``time_series_spread`` BPS + ``time_series_zscore`` Z_SCORE) must
    align point-by-point with the bespoke wire-frozen ``time_series``
    rows so downstream operators consuming the canonical payloads
    cannot drift from the snapshot/UI surface.
    """

    def test_time_series_spread_field_present(self):
        out = _run(_params(), _synthetic_raw_df())
        assert "time_series_spread" in out
        assert isinstance(out["time_series_spread"], dict)

    def test_time_series_zscore_field_present(self):
        out = _run(_params(), _synthetic_raw_df())
        assert "time_series_zscore" in out
        assert isinstance(out["time_series_zscore"], dict)

    def test_spread_series_uses_BPS(self):
        out = _run(_params(), _synthetic_raw_df())
        assert out["time_series_spread"]["units"] == "bps"

    def test_zscore_series_uses_Z_SCORE(self):
        out = _run(_params(), _synthetic_raw_df())
        assert out["time_series_zscore"]["units"] == "z_score"

    def test_spread_series_name_follows_convention(self):
        out = _run(_params(), _synthetic_raw_df())
        assert (
            out["time_series_spread"]["series_name"]
            == "usd_sofr_ois_eur_estr_ois_2y_ois_cross_spread"
        )

    def test_zscore_series_name_follows_convention(self):
        out = _run(_params(), _synthetic_raw_df())
        assert (
            out["time_series_zscore"]["series_name"]
            == "usd_sofr_ois_eur_estr_ois_2y_ois_cross_zscore"
        )

    def test_both_series_length_equals_bespoke_length(self):
        out = _run(_params(), _synthetic_raw_df())
        bespoke = out["time_series"]
        assert len(out["time_series_spread"]["rows"]) == len(bespoke)
        assert len(out["time_series_zscore"]["rows"]) == len(bespoke)

    def test_spread_values_match_bespoke_pointwise(self):
        out = _run(_params(), _synthetic_raw_df())
        canonical = out["time_series_spread"]
        bespoke = out["time_series"]
        for i, (c_row, b_row) in enumerate(zip(canonical["rows"], bespoke)):
            assert c_row["date"] == b_row["date"], f"row {i}: date mismatch"
            assert c_row["value"] == b_row["spread_bps"], (
                f"row {i}: value mismatch"
            )

    def test_zscore_values_match_bespoke_pointwise(self):
        out = _run(_params(), _synthetic_raw_df())
        canonical = out["time_series_zscore"]
        bespoke = out["time_series"]
        for i, (c_row, b_row) in enumerate(zip(canonical["rows"], bespoke)):
            assert c_row["date"] == b_row["date"], f"row {i}: date mismatch"
            assert c_row["value"] == b_row["z_score"], (
                f"row {i}: value mismatch"
            )

    def test_both_canonical_series_validate_against_TimeSeries_schema(self):
        from shared.schemas import TimeSeries
        out = _run(_params(), _synthetic_raw_df())
        TimeSeries.model_validate(out["time_series_spread"])
        TimeSeries.model_validate(out["time_series_zscore"])
