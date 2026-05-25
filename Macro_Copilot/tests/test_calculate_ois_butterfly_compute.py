"""
test_calculate_ois_butterfly_compute.py — Unit tests for the OIS
butterfly tool (Layer A — fully offline).

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. compute() runs end-to-end against synthetic input with the
     bundled config and returns a well-formed output.
  3. Convention overrides actually change behaviour
     (z_score_window_days, ddof, daily_change_offset_rows,
     ffill_limit_days, bps_round_decimals).
  4. Honest placeholder for trailing_range_window_days: setting it to
     anything other than 252 raises NotImplementedError.
  5. Schema-layer behaviour: field_name defaults to None (sentinel),
     compute() resolves the sentinel against the YAML.
  6. Three import paths still resolve to the same Pydantic class
     (package init, schemas module, OIS schemas hub).
  7. Canonical TimeSeries output: time_series_butterfly (BPS) and
     time_series_zscore (Z_SCORE) match the bespoke list 1-to-1.
  8. Methodology disclosure asserts the fixed-weight choice + names
     the DV01-neutral planned_extension.
  9. Closed-enum (Literal) curve_family whitelist rejects non-OIS
     values at the schema layer.

Tests are fully offline — fetch_tenor_group and date.today() are
patched.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from rates_agent.ois.tools.calculate_ois_butterfly import (
    CONFIG_PATH,
    OISButterflyInput,
    calculate_ois_butterfly,
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
    short_drift: float = -0.40,
    belly_drift: float = -0.20,
    long_drift: float = +0.10,
) -> pd.DataFrame:
    """Build a 3-tenor long-format DataFrame matching the shape
    fetch_tenor_group returns.  Three tenors (2Y, 5Y, 10Y), each with
    its own drift so the butterfly varies across the window."""
    bdays = pd.bdate_range(frozen_today - timedelta(days=days * 2), frozen_today)
    bdays = bdays[-days:]
    n = len(bdays)
    rows = []
    base = {"2Y": 4.5, "5Y": 4.2, "10Y": 4.4}
    drifts = {"2Y": short_drift, "5Y": belly_drift, "10Y": long_drift}
    for tenor, base_v in base.items():
        series = np.linspace(base_v, base_v + drifts[tenor], n)
        for d, v in zip(bdays, series):
            rows.append({"trade_date": d.date(), "tenor": tenor, "field_value": v})
    return pd.DataFrame(rows)


class _FrozenDate(date):
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


def _custom_config(**overrides) -> ToolConfig:
    """Build a ToolConfig from defaults + any overrides.  Defaults
    mirror config.yaml exactly so tests that override one knob are
    measuring a single-variable change."""
    defaults = {
        "z_score_window_days": 252,
        "z_score_min_periods": 60,
        "z_score_ddof": 1,
        "z_score_buffer_multiplier": 1.5,
        "daily_change_offset_rows": 2,
        "trailing_range_window_days": 252,
        "ffill_limit_days": 5,
        "bps_round_decimals": 2,
        "z_score_round_decimals": 4,
        "default_swap_rate_field": "PX_LAST",
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


def _run(params, raw_df, config=None):
    with patch(
        "rates_agent.ois.tools.calculate_ois_butterfly.compute.fetch_tenor_group",
        return_value=raw_df,
    ), patch(
        "rates_agent.ois.tools.calculate_ois_butterfly.compute.date",
        _FrozenDate,
    ):
        return calculate_ois_butterfly(engine=None, params=params, config=config)


def _params(**overrides) -> OISButterflyInput:
    kwargs = dict(
        curve_family="USD_SOFR_OIS",
        short_tenor="2Y",
        belly_tenor="5Y",
        long_tenor="10Y",
        lookback_days=365,
    )
    kwargs.update(overrides)
    return OISButterflyInput(**kwargs)


# ===========================================================================
# 1. Bundled config.yaml
# ===========================================================================

class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "calculate_ois_butterfly_tool"
        assert cfg.tool.domain == "ois"
        assert cfg.tool.category == "desk_invariant_primitive"

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
            "bps_round_decimals",
            "z_score_round_decimals",
            "default_swap_rate_field",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults(self):
        """Bundled defaults are the OIS-domain conventions: PX_LAST
        for the swap-rate field, 252/60/1 for the z-score, 2 for the
        daily-change offset, 252 for the trailing window."""
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("z_score_window_days") == 252
        assert cfg.convention_value("z_score_min_periods") == 60
        assert cfg.convention_value("z_score_ddof") == 1
        assert cfg.convention_value("z_score_buffer_multiplier") == 1.5
        assert cfg.convention_value("daily_change_offset_rows") == 2
        assert cfg.convention_value("trailing_range_window_days") == 252
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("bps_round_decimals") == 2
        assert cfg.convention_value("z_score_round_decimals") == 4
        assert cfg.convention_value("default_swap_rate_field") == "PX_LAST"

    def test_methodology_what_it_does_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.methodology.what_it_does
        assert "OIS" in cfg.methodology.what_it_does
        assert "butterfly" in cfg.methodology.what_it_does.lower()

    def test_methodology_assumptions_disclose_fixed_weights_and_rate_space(self):
        """The methodology assumptions block must surface the fixed-
        weight choice AND the raw OIS rate-space promise — both
        load-bearing P5 disclosures called out in the catalog
        guardrails."""
        cfg = load_tool_config(CONFIG_PATH)
        joined = " | ".join(cfg.methodology.assumptions).lower()
        # Fixed-weight disclosure
        assert "fixed" in joined or "50-50" in joined or "(-1" in joined
        # Raw OIS-rate-space disclosure (NOT bond-equivalent yield)
        assert "raw ois" in joined or "ois par-swap-rate space" in joined
        assert "bond-equivalent" in joined or "bond equivalent" in joined

    def test_methodology_planned_extensions_disclose_dv01_neutral(self):
        """DV01-neutral and PCA-weighted variants must be named in
        planned_extensions per the catalog guardrails — they are NOT
        config knobs on this primitive."""
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 3
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "DV01" in joined
        assert "trailing_range_window_days" in joined

    def test_methodology_planned_extensions_disclose_curve_family_gap(self):
        """The planned_extensions must honestly disclose that the
        catalog references JPY_TONA_OIS / AUD_AONIA_OIS / CAD_CORRA_OIS
        but the playbook uses the shorter form (JPY_OIS / AUD_OIS /
        CAD_OIS).  This primitive uses the playbook's actual names."""
        cfg = load_tool_config(CONFIG_PATH)
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "JPY_TONA_OIS" in joined or "TONA" in joined
        assert "AONIA" in joined or "CORRA" in joined


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        raw_df = _synthetic_raw_df()
        out = _run(_params(), raw_df)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        # Required current_metrics fields present.
        for k in (
            "as_of_date", "curve_family", "butterfly_label",
            "current_butterfly_bps", "daily_change_bps",
            "current_z_score", "rolling_window_days",
            "high_252d_bps", "low_252d_bps", "percentile_252d",
            "wing_short_bps", "wing_long_bps",
            "short_tenor_rate", "belly_tenor_rate", "long_tenor_rate",
        ):
            assert k in cm, f"missing {k}"

        # Wire-frozen field names — explicit check that the rename
        # plan was NOT silently applied.
        assert "high_window_bps" not in cm
        assert "trailing_window_days" not in cm

        # OIS terminology: "rate" not "yield" on the per-leg fields.
        assert "short_tenor_yield" not in cm
        assert "belly_tenor_yield" not in cm
        assert "long_tenor_yield" not in cm

        # Butterfly label is "{short}s{belly}s{long}s" with Y stripped.
        assert cm["butterfly_label"] == "2s5s10s"
        assert cm["rolling_window_days"] == 252

        # time_series exists and rows have the right shape
        ts = out["time_series"]
        assert len(ts) > 0
        assert set(ts[0].keys()) == {"date", "butterfly_bps", "z_score"}

    def test_explicit_config_matches_auto_loaded(self):
        raw_df = _synthetic_raw_df()
        out_auto = _run(_params(), raw_df, config=None)
        out_explicit = _run(_params(), raw_df, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit

    def test_sign_convention_belly_cheap_positive(self):
        """Belly drift downward (belly RATE rising less than wings
        would predict) — but we want positive = belly cheap = belly
        rate HIGH relative to wings.  Construct a series where the
        belly is explicitly high vs the wing avg and verify positive
        butterfly_bps."""
        raw_df = _synthetic_raw_df(
            short_drift=0.0, belly_drift=+0.50, long_drift=0.0,
        )
        out = _run(_params(), raw_df)
        # belly = 4.2 + 0.5 = 4.7; short = 4.5; long = 4.4
        # butterfly = (2 * 4.7 - 4.5 - 4.4) * 100 = 50 bps  (positive)
        assert out["current_metrics"]["current_butterfly_bps"] > 0

    def test_sign_convention_belly_rich_negative(self):
        """Belly rate LOW relative to wings ⇒ negative butterfly."""
        raw_df = _synthetic_raw_df(
            short_drift=0.0, belly_drift=-0.50, long_drift=0.0,
        )
        out = _run(_params(), raw_df)
        # belly drops 0.5; short / long unchanged → negative butterfly
        assert out["current_metrics"]["current_butterfly_bps"] < 0


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def test_z_score_window_override_changes_z(self):
        raw_df = _synthetic_raw_df()
        out_default = _run(_params(), raw_df, _custom_config())
        out_short = _run(_params(), raw_df, _custom_config(z_score_window_days=120))
        assert (
            out_default["current_metrics"]["current_z_score"]
            != out_short["current_metrics"]["current_z_score"]
        )

    def test_z_score_ddof_override_changes_z(self):
        raw_df = _synthetic_raw_df()
        out_sample = _run(_params(), raw_df, _custom_config(z_score_ddof=1))
        out_pop = _run(_params(), raw_df, _custom_config(z_score_ddof=0))
        assert (
            out_sample["current_metrics"]["current_z_score"]
            != out_pop["current_metrics"]["current_z_score"]
        )

    def test_daily_change_offset_override_changes_daily(self):
        raw_df = _synthetic_raw_df()
        out_default = _run(_params(), raw_df, _custom_config())
        out_wider = _run(_params(), raw_df, _custom_config(daily_change_offset_rows=5))
        assert (
            out_default["current_metrics"]["daily_change_bps"]
            != out_wider["current_metrics"]["daily_change_bps"]
        )

    def test_ffill_limit_passed_to_pivot(self):
        from shared.analytics.spreads import pivot_and_align_tenors as real_pivot

        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.ois.tools.calculate_ois_butterfly.compute.fetch_tenor_group",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.calculate_ois_butterfly.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.ois.tools.calculate_ois_butterfly.compute.pivot_and_align_tenors",
            wraps=real_pivot,
        ) as spy:
            calculate_ois_butterfly(
                engine=None, params=_params(),
                config=_custom_config(ffill_limit_days=2),
            )
        assert spy.call_count == 1
        assert spy.call_args.kwargs["ffill_limit"] == 2

    def test_bps_round_decimals_override_changes_precision(self):
        raw_df = _synthetic_raw_df(
            short_drift=-0.43219, belly_drift=-0.12345, long_drift=+0.09876,
        )
        out_lo = _run(_params(), raw_df, _custom_config(bps_round_decimals=2))
        out_hi = _run(_params(), raw_df, _custom_config(bps_round_decimals=4))

        v_lo = out_lo["current_metrics"]["current_butterfly_bps"]
        v_hi = out_hi["current_metrics"]["current_butterfly_bps"]
        # 2-decimal rounding should equal 4-decimal rounding rounded
        # back to 2 decimals.
        assert round(v_hi, 2) == v_lo
        # On a synthetic series with non-round drifts there should be
        # at least one row where the 4-decimal version carries trailing
        # digits beyond 2 decimals.
        ts_lo = out_lo["time_series"]
        ts_hi = out_hi["time_series"]
        differs = any(
            round(hi["butterfly_bps"], 2) == lo["butterfly_bps"]
            and hi["butterfly_bps"] != lo["butterfly_bps"]
            for hi, lo in zip(ts_hi, ts_lo)
        )
        assert differs, (
            "no time_series row exhibited sub-2-decimal precision at "
            "decimals=4 — bps_round_decimals override may not be reaching "
            "the .round() call"
        )


class TestZScoreRoundDecimalsReachesOutput:
    """z_score_round_decimals must apply at the OUTPUT boundary, not
    just to the internal rolling_zscore() call.  Same boundary-
    shadowing class of bug as the field_name fix in b2605ee and the
    sovereign butterfly's z-score-rounding fix.  Pins the wiring
    end-to-end."""

    def test_current_z_score_uses_z_round_decimals(self):
        raw_df = _synthetic_raw_df(
            short_drift=-0.43219, belly_drift=-0.12345, long_drift=+0.09876,
        )
        out_hi = _run(_params(), raw_df, _custom_config(z_score_round_decimals=6))
        out_lo = _run(_params(), raw_df, _custom_config(z_score_round_decimals=4))
        z_hi = out_hi["current_metrics"]["current_z_score"]
        z_lo = out_lo["current_metrics"]["current_z_score"]
        assert z_hi is not None and z_lo is not None
        # Contract: at decimals=6, value rounded back to 4 == value at decimals=4
        assert round(z_hi, 4) == z_lo

    def test_time_series_z_score_uses_z_round_decimals(self):
        raw_df = _synthetic_raw_df(
            short_drift=-0.43219, belly_drift=-0.12345, long_drift=+0.09876,
        )
        out_hi = _run(_params(), raw_df, _custom_config(z_score_round_decimals=6))
        out_lo = _run(_params(), raw_df, _custom_config(z_score_round_decimals=4))
        differs = any(
            hi["z_score"] is not None and lo["z_score"] is not None
            and round(hi["z_score"], 4) == lo["z_score"]
            and hi["z_score"] != lo["z_score"]
            for hi, lo in zip(out_hi["time_series"], out_lo["time_series"])
        )
        assert differs, (
            "no time_series row exhibited sub-4-decimal precision at "
            "z_score_round_decimals=6 — safe_float() at the output "
            "boundary may be ignoring the YAML override"
        )


class TestBpsRoundingReachesDailyChange:
    """delta_bps used to be hardcoded to 2 decimals, so a bumped
    bps_round_decimals would silently NOT apply to daily_change_bps.
    This test pins the wiring on the OIS butterfly."""

    def test_delta_bps_decimals_reaches_daily_change(self):
        from shared.analytics import levels as levels_mod

        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.ois.tools.calculate_ois_butterfly.compute.fetch_tenor_group",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.calculate_ois_butterfly.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.ois.tools.calculate_ois_butterfly.compute.delta_bps",
            wraps=levels_mod.delta_bps,
        ) as spy:
            calculate_ois_butterfly(
                engine=None, params=_params(),
                config=_custom_config(bps_round_decimals=4),
            )
        assert spy.call_count == 1
        assert spy.call_args.kwargs["decimals"] == 4


# ===========================================================================
# 4. Honest-placeholder guard on trailing_range_window_days
# ===========================================================================

class TestTrailingWindowGuard:
    def test_unsupported_trailing_window_raises(self):
        with pytest.raises(NotImplementedError) as exc_info:
            calculate_ois_butterfly(
                engine=None, params=_params(),
                config=_custom_config(trailing_range_window_days=180),
            )
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_bps" in msg
        assert "planned_extensions" in msg

    def test_supported_default_does_not_raise(self):
        raw_df = _synthetic_raw_df()
        out = _run(_params(), raw_df, _custom_config(trailing_range_window_days=252))
        assert "error" not in out


# ===========================================================================
# 5. Schema-layer behaviour
# ===========================================================================

class TestSchemaLayer:
    def test_field_name_default_is_none(self):
        p = _params()
        assert p.field_name is None, (
            "schema default must be None so compute() can fall through "
            "to the YAML's default_swap_rate_field"
        )

    def test_explicit_field_name_passes_through(self):
        p = _params(field_name="PX_BID")
        assert p.field_name == "PX_BID"

    def test_tenors_must_all_differ(self):
        with pytest.raises(ValidationError):
            OISButterflyInput(
                curve_family="USD_SOFR_OIS",
                short_tenor="5Y", belly_tenor="5Y", long_tenor="10Y",
            )

    def test_curve_family_closed_enum_rejects_non_ois(self):
        """Sovereign curve_family names must be rejected at schema time
        — closed-enum P8 + PR8 discipline."""
        with pytest.raises(ValidationError):
            OISButterflyInput(
                curve_family="UST",
                short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
            )

    def test_curve_family_closed_enum_rejects_tona_alias(self):
        """The catalog references JPY_TONA_OIS but the playbook uses
        JPY_OIS.  This primitive must REFUSE the catalog alias rather
        than silently relabelling — see config.yaml's planned_extensions
        for the honest disclosure."""
        with pytest.raises(ValidationError):
            OISButterflyInput(
                curve_family="JPY_TONA_OIS",
                short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
            )

    def test_curve_family_closed_enum_accepts_all_playbook_families(self):
        for cf in (
            "USD_SOFR_OIS", "EUR_ESTR_OIS", "GBP_SONIA_OIS",
            "JPY_OIS", "AUD_OIS", "CAD_OIS",
        ):
            # Should not raise
            OISButterflyInput(
                curve_family=cf, short_tenor="2Y",
                belly_tenor="5Y", long_tenor="10Y",
            )


class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_swap_rate_field reaches fetch."""

    def _capture_field_name(self, params, config):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.ois.tools.calculate_ois_butterfly.compute.fetch_tenor_group",
            return_value=raw_df,
        ) as spy, patch(
            "rates_agent.ois.tools.calculate_ois_butterfly.compute.date",
            _FrozenDate,
        ):
            calculate_ois_butterfly(engine=None, params=params, config=config)
        assert spy.call_count == 1
        return spy.call_args.kwargs["field_name"]

    def test_omitted_field_name_uses_yaml_default(self):
        passed = self._capture_field_name(
            _params(), _custom_config(default_swap_rate_field="PX_LAST"),
        )
        assert passed == "PX_LAST"

    def test_yaml_override_changes_resolved_field(self):
        passed = self._capture_field_name(
            _params(), _custom_config(default_swap_rate_field="PX_MID"),
        )
        assert passed == "PX_MID"

    def test_explicit_field_name_overrides_yaml(self):
        passed = self._capture_field_name(
            _params(field_name="PX_BID"),
            _custom_config(default_swap_rate_field="PX_LAST"),
        )
        assert passed == "PX_BID"


# ===========================================================================
# 6. Buffer sizing — defensive max(z_window, trailing_window)
# ===========================================================================

class TestBufferSizing:
    """When the YAML decouples z_window from trailing_window in a
    future migration, the fetch buffer must already be sized off the
    LARGER of the two so neither stat starves."""

    def _capture_start_date(self, params, config):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.ois.tools.calculate_ois_butterfly.compute.fetch_tenor_group",
            return_value=raw_df,
        ) as spy, patch(
            "rates_agent.ois.tools.calculate_ois_butterfly.compute.date",
            _FrozenDate,
        ):
            calculate_ois_butterfly(engine=None, params=params, config=config)
        assert spy.call_count == 1
        return spy.call_args.kwargs["start_date"]

    def test_buffer_uses_larger_of_z_window_and_trailing_window(self):
        start_default = self._capture_start_date(_params(), _custom_config())
        start_smaller_z = self._capture_start_date(
            _params(), _custom_config(z_score_window_days=120),
        )
        # Same buffer ⇒ same start_date when only z_window shrinks.
        assert start_default == start_smaller_z


# ===========================================================================
# 7. Import path backward-compat
# ===========================================================================

class TestImportPathBackwardCompat:
    def test_calculate_ois_butterfly_via_package_init(self):
        from rates_agent.ois.tools.calculate_ois_butterfly import (
            calculate_ois_butterfly as via_package,
        )
        from rates_agent.ois.tools.calculate_ois_butterfly.compute import (
            calculate_ois_butterfly as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.ois.tools.calculate_ois_butterfly import (
            OISButterflyInput as via_package,
        )
        from rates_agent.ois.tools.calculate_ois_butterfly.schemas import (
            OISButterflyInput as via_schemas,
        )
        from rates_agent.ois.tools.schemas import (
            OISButterflyInput as via_hub,
        )
        assert via_package is via_schemas is via_hub


# ===========================================================================
# 8. Canonical TimeSeries output
# ===========================================================================

class TestCanonicalTimeSeries:
    """Pin the canonical-TimeSeries contract: OIS butterfly emits TWO
    canonical ``TimeSeries`` fields (``time_series_butterfly`` in BPS
    and ``time_series_zscore`` in Z_SCORE) alongside its wire-frozen
    bespoke ``time_series: List[OISButterflyTimeSeriesRow]`` array."""

    def test_time_series_butterfly_field_present(self):
        out = _run(_params(), _synthetic_raw_df())
        assert "time_series_butterfly" in out
        assert isinstance(out["time_series_butterfly"], dict)

    def test_time_series_zscore_field_present(self):
        out = _run(_params(), _synthetic_raw_df())
        assert "time_series_zscore" in out
        assert isinstance(out["time_series_zscore"], dict)

    def test_butterfly_series_uses_BPS(self):
        out = _run(_params(), _synthetic_raw_df())
        assert out["time_series_butterfly"]["units"] == "bps"

    def test_zscore_series_uses_Z_SCORE(self):
        out = _run(_params(), _synthetic_raw_df())
        assert out["time_series_zscore"]["units"] == "z_score"

    def test_butterfly_series_name_follows_convention(self):
        out = _run(_params(), _synthetic_raw_df())
        assert (
            out["time_series_butterfly"]["series_name"]
            == "usd_sofr_ois_2y_5y_10y_ois_butterfly"
        )

    def test_zscore_series_name_follows_convention(self):
        out = _run(_params(), _synthetic_raw_df())
        assert (
            out["time_series_zscore"]["series_name"]
            == "usd_sofr_ois_2y_5y_10y_ois_zscore"
        )

    def test_both_series_length_equals_bespoke_length(self):
        out = _run(_params(), _synthetic_raw_df())
        bespoke = out["time_series"]
        assert len(out["time_series_butterfly"]["rows"]) == len(bespoke)
        assert len(out["time_series_zscore"]["rows"]) == len(bespoke)

    def test_butterfly_values_match_bespoke_pointwise(self):
        out = _run(_params(), _synthetic_raw_df())
        canonical = out["time_series_butterfly"]
        bespoke = out["time_series"]
        for i, (c_row, b_row) in enumerate(zip(canonical["rows"], bespoke)):
            assert c_row["date"] == b_row["date"], f"row {i}: date mismatch"
            assert c_row["value"] == b_row["butterfly_bps"], (
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
        TimeSeries.model_validate(out["time_series_butterfly"])
        TimeSeries.model_validate(out["time_series_zscore"])


# ===========================================================================
# 9. Error envelopes
# ===========================================================================

class TestErrorEnvelopes:
    def test_empty_db_returns_error_envelope(self):
        raw_df = pd.DataFrame(columns=["trade_date", "tenor", "field_value"])
        out = _run(_params(), raw_df)
        assert "error" in out
        assert "USD_SOFR_OIS" in out["error"]
        assert "current_metrics" not in out

    def test_missing_tenor_returns_error_envelope(self):
        # Only 2 of the 3 required tenors present.
        raw_df = _synthetic_raw_df()
        raw_df = raw_df[raw_df["tenor"] != "10Y"]
        out = _run(_params(), raw_df)
        assert "error" in out
        assert "10Y" in out["error"]
