"""
test_inflation_swap_rate_level_compute.py — Unit tests for the
zero-coupon inflation swap (ZCIS) rate-level primitive.

Mirrors ``test_real_yield_level_compute.py`` (the structurally
equivalent linker analog) and ``test_ois_rate_level_compute.py`` /
``test_yield_levels_compute.py`` so the four level surfaces evolve
together.

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. Convention defaults align with sibling level tools (the cross-
     config lint enforces this; the test pins values explicitly).
  3. Convention overrides actually change behaviour (z-window, ddof,
     period offsets, ffill, default_zcis_rate_field).
  4. Honest placeholder for trailing_range_window_days: setting it to
     anything other than 252 raises NotImplementedError.
  5. Schema-layer behaviour: field_name defaults to None (sentinel),
     compute() resolves the sentinel against the YAML.
  6. Two import paths still resolve to the same Pydantic class.
  7. Canonical TimeSeries output (units = PERCENT, snapshot ==
     time_series.rows[-1].value strictly, ``_zcis_rate`` series-name
     suffix).
  8. ZCIS instrument_type + pricing_type guard: nominal / non-ZCIS
     curve families surface a controlled error envelope.
  9. Reference-metadata surfacing: inflation_index_family / index_lag
     / interpolation / underlying_index reach current_metrics, and
     methodology_label is threaded from YAML.
 10. Ambiguous-ticker guard: more than one vendor_ticker per
     (curve_family, tenor) surfaces a controlled error envelope.

Tests are fully offline (DB-backed grounding lives in the SQL-
validation runner — see
``tests/test_inflation_swap_rate_level_sql_validation.py``).
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.inflation_swaps.tools.inflation_swap_rate_level import (
    CONFIG_PATH,
    InflationSwapRateLevelInput,
    calculate_inflation_swap_rate_level,
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
    drift_pct: float = -0.30,
    days: int = 400,
    frozen_today: date = date(2026, 4, 30),
    base_pct: float = 2.50,
    vendor_ticker: str = "USSWIT5 Curncy",
    inflation_index_family: str = "US_CPI_URBAN",
    index_lag: str = "3M",
    interpolation: str = "Daily",
    underlying_index: str = "CPURNSA Index",
    pricing_type: str = "zero_coupon_breakeven",
) -> pd.DataFrame:
    """Build a long-format DataFrame matching the shape
    fetch_zcis_single_pillar returns.  Linspace from base_pct to
    base_pct+drift_pct over ``days`` business days ending at
    ``frozen_today``.

    ZCIS rates can be negative across parts of the post-2020 EUR /
    GBP history; the level math is unchanged — base_pct is
    parameterised so the fixture can probe both regimes.

    The tool anchors observation_count to the data's latest date
    (NOT to date.today()), so this fixture's last business day is
    the effective ``as_of_date`` regardless of frozen-today
    patching.
    """
    bdays = pd.bdate_range(frozen_today - timedelta(days=days * 2), frozen_today)
    bdays = bdays[-days:]
    n = len(bdays)
    series = np.linspace(base_pct, base_pct + drift_pct, n)
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": series,
        "vendor_ticker": [vendor_ticker] * n,
        "pricing_type": [pricing_type] * n,
        "inflation_index_family": [inflation_index_family] * n,
        "index_lag": [index_lag] * n,
        "interpolation": [interpolation] * n,
        "underlying_index": [underlying_index] * n,
    })


class _FrozenDate(date):
    """Patches ``compute.date`` so the fetch ``start_date`` is
    deterministic across machines.  The observation-count cutoff
    inside compute() is anchored to the data's last index date
    (``rates.index[-1]``), not to ``date.today()``, so the frozen
    today value only matters for the fetch-window computation
    (which is mocked away in these tests anyway).
    """
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


# Default-convention dict shared across the override-style tests
# below.  Mirrors the bundled config.yaml exactly except per-test
# overrides applied via ``_build_config(**overrides)``.
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
    "default_zcis_rate_field": "PX_MID",
    "yield_round_decimals": 4,
    "z_score_round_decimals": 4,
    "high_low_round_decimals": 4,
}


_DEFAULT_METHODOLOGY_LABEL = "test methodology label"


def _build_config(
    *,
    methodology_label: str = _DEFAULT_METHODOLOGY_LABEL,
    **overrides,
) -> ToolConfig:
    defaults = dict(_BUNDLED_DEFAULTS)
    defaults.update(overrides)
    return ToolConfig(
        tool=ToolMeta(name="t", domain="d", description="x"),
        methodology=MethodologyMeta(what_it_does=methodology_label),
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
        assert cfg.tool.name == "calculate_inflation_swap_rate_level_tool"
        assert cfg.tool.domain == "inflation_swaps"
        assert cfg.tool.category == "desk_invariant_primitive"

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
            "default_zcis_rate_field",
            "yield_round_decimals",
            "z_score_round_decimals",
            "high_low_round_decimals",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults_align_with_sibling_level_tools(self):
        """Cross-tool convention values MUST match sovereign yield_levels,
        OIS rate_level, and linker real_yield_level — the cross-config
        lint enforces this, and the test pins the values explicitly so
        a YAML edit drifting one of these silently breaks here too."""
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
        assert cfg.convention_value("yield_round_decimals") == 4
        assert cfg.convention_value("z_score_round_decimals") == 4
        assert cfg.convention_value("high_low_round_decimals") == 4

    def test_uses_dedicated_zcis_field_convention_name(self):
        """ZCIS rates are stored under Bloomberg ``PX_MID`` — different
        from sovereign / linker ``YLD_YTM_MID`` and OIS ``PX_LAST``.
        The convention is renamed to ``default_zcis_rate_field`` so the
        cross-config lint stays clean (sharing the
        ``default_field_name`` convention name with a different value
        would flag a value-drift)."""
        cfg = load_tool_config(CONFIG_PATH)
        assert "default_zcis_rate_field" in cfg.conventions
        assert cfg.convention_value("default_zcis_rate_field") == "PX_MID"
        # Reusing the linker / sovereign convention name with a
        # different value would conflict; this test pins that we did
        # NOT do that.
        assert "default_field_name" not in cfg.conventions
        # And we did NOT reuse the OIS-specific name either.
        assert "default_swap_rate_field" not in cfg.conventions

    def test_methodology_what_it_does_describes_zcis_filters(self):
        cfg = load_tool_config(CONFIG_PATH)
        what = cfg.methodology.what_it_does
        # The disclosure must spell out the load-bearing SELECT
        # filters so a reader can trace them to compute().
        assert "instrument_type='inflation_swap'" in what
        assert "pricing_type='zero_coupon_breakeven'" in what
        # And it must mention the reference-metadata surface so the
        # cross-curve comparability caveat is visible at the YAML
        # layer too.
        assert "inflation_index_family" in what
        assert "index_lag" in what
        assert "interpolation" in what

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        # Catalog requires the four follow-on primitives (curve
        # spread, forward, cross-market spread, swap-breakeven basis)
        # PLUS the trailing-window configurability extension — five
        # entries minimum.
        assert len(cfg.methodology.planned_extensions) >= 5
        joined = " | ".join(cfg.methodology.planned_extensions).lower()
        assert "curve spread" in joined
        assert "forward" in joined
        assert "cross-market" in joined
        assert "swap-breakeven basis" in joined
        assert "trailing_range_window_days" in joined


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def _run(self, params, raw_df, config=None):
        with patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar",
            return_value=raw_df,
        ), patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date",
            _FrozenDate,
        ):
            return calculate_inflation_swap_rate_level(
                engine=None, params=params, config=config,
            )

    def test_default_config_returns_well_formed_output(self):
        raw_df = _synthetic_raw_df()
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        out = self._run(params, raw_df)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        for k in (
            "as_of_date", "curve_family", "tenor", "zcis_rate_pct",
            "daily_change_bps", "weekly_change_bps", "monthly_change_bps",
            "z_score", "high_252d_pct", "low_252d_pct", "percentile_252d",
            "observation_count",
            # Reference metadata surfaced on the wire.
            "inflation_index_family", "index_lag", "interpolation",
            "underlying_index",
            # Wire-honesty disclosure.
            "methodology_label",
        ):
            assert k in cm, f"missing {k}"

        # Wire-frozen field names — explicit check that the rename
        # plan was NOT silently applied.
        assert "high_window_pct" not in cm
        assert "trailing_window_days" not in cm

        # The wire field MUST be the ZCIS-specific name, not the
        # sovereign / OIS / linker name.  Operator panels rely on
        # this distinction.
        assert "zcis_rate_pct" in cm
        assert "current_yield_pct" not in cm
        assert "current_rate_pct" not in cm
        assert "real_yield_pct" not in cm

        assert isinstance(cm["zcis_rate_pct"], float)

    def test_handles_negative_zcis_rates(self):
        """ZCIS rates were negative across parts of the post-2020 EUR
        / GBP history; the level math is unchanged but a regression
        guard here pins that the wire types tolerate negative
        values."""
        raw_df = _synthetic_raw_df(base_pct=-0.10, drift_pct=0.20)
        params = InflationSwapRateLevelInput(
            curve_family="EUR_ZCIS", tenor="5Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        assert "error" not in out
        # Latest value should be base_pct + drift_pct = 0.10
        # (positive); spot-check the shape only.
        assert out["current_metrics"]["zcis_rate_pct"] is not None

    def test_explicit_config_matches_auto_loaded(self):
        raw_df = _synthetic_raw_df()
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        out_auto = self._run(params, raw_df, config=None)
        out_explicit = self._run(
            params, raw_df, config=load_tool_config(CONFIG_PATH),
        )
        assert out_auto == out_explicit


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def _run(self, params, raw_df, config):
        with patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar",
            return_value=raw_df,
        ), patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date",
            _FrozenDate,
        ):
            return calculate_inflation_swap_rate_level(
                engine=None, params=params, config=config,
            )

    def test_z_score_window_override_changes_z(self):
        raw_df = _synthetic_raw_df()
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        out_default = self._run(params, raw_df, _build_config())
        out_short = self._run(
            params, raw_df, _build_config(z_score_window_days=120),
        )
        assert (
            out_default["current_metrics"]["z_score"]
            != out_short["current_metrics"]["z_score"]
        )

    def test_z_score_ddof_override_changes_z(self):
        raw_df = _synthetic_raw_df()
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        out_sample = self._run(params, raw_df, _build_config(z_score_ddof=1))
        out_pop = self._run(params, raw_df, _build_config(z_score_ddof=0))
        assert (
            out_sample["current_metrics"]["z_score"]
            != out_pop["current_metrics"]["z_score"]
        )

    def test_period_offsets_override_changes_changes(self):
        raw_df = _synthetic_raw_df()
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        out_default = self._run(params, raw_df, _build_config())
        out_wider = self._run(
            params, raw_df, _build_config(daily_change_offset_rows=5),
        )
        assert (
            out_default["current_metrics"]["daily_change_bps"]
            != out_wider["current_metrics"]["daily_change_bps"]
        )

    def test_ffill_limit_passed_to_clean(self):
        from shared.analytics.levels import clean_single_series as real_clean

        raw_df = _synthetic_raw_df()
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        with patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar",
            return_value=raw_df,
        ), patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.clean_single_series",
            wraps=real_clean,
        ) as spy:
            calculate_inflation_swap_rate_level(
                engine=None, params=params,
                config=_build_config(ffill_limit_days=2),
            )
        assert spy.call_count == 1
        assert spy.call_args.kwargs["ffill_limit"] == 2


# ===========================================================================
# 4. Honest-placeholder guard on trailing_range_window_days
# ===========================================================================

class TestTrailingWindowGuard:
    def test_unsupported_trailing_window_raises(self):
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            calculate_inflation_swap_rate_level(
                engine=None, params=params,
                config=_build_config(trailing_range_window_days=180),
            )
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_pct" in msg
        assert "planned_extensions" in msg

    def test_supported_default_does_not_raise(self):
        raw_df = _synthetic_raw_df()
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        with patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar",
            return_value=raw_df,
        ), patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date",
            _FrozenDate,
        ):
            out = calculate_inflation_swap_rate_level(
                engine=None, params=params,
                config=_build_config(trailing_range_window_days=252),
            )
        assert "error" not in out


# ===========================================================================
# 5. Schema-layer field_name behaviour
# ===========================================================================

class TestFieldNameSchemaBehaviour:
    def test_field_name_default_is_none(self):
        params = InflationSwapRateLevelInput(curve_family="USD_ZCIS", tenor="5Y")
        assert params.field_name is None, (
            "schema default must be None so compute() can fall through "
            "to the YAML's default_zcis_rate_field"
        )

    def test_explicit_field_name_passes_through(self):
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", field_name="PX_BID",
        )
        assert params.field_name == "PX_BID"


class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_zcis_rate_field reaches
    fetch."""

    def _capture_field_name(self, params, config):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar",
            return_value=raw_df,
        ) as spy, patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date",
            _FrozenDate,
        ):
            calculate_inflation_swap_rate_level(
                engine=None, params=params, config=config,
            )
        assert spy.call_count == 1
        return spy.call_args.kwargs["field_name"]

    def test_omitted_field_name_uses_yaml_default(self):
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y",
        )  # no field_name
        passed = self._capture_field_name(
            params, _build_config(default_zcis_rate_field="PX_MID"),
        )
        assert passed == "PX_MID"

    def test_yaml_override_changes_resolved_field(self):
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y",
        )
        passed = self._capture_field_name(
            params, _build_config(default_zcis_rate_field="PX_BID"),
        )
        assert passed == "PX_BID"

    def test_explicit_field_name_overrides_yaml(self):
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", field_name="PX_ASK",
        )
        passed = self._capture_field_name(
            params, _build_config(default_zcis_rate_field="PX_MID"),
        )
        assert passed == "PX_ASK"


# ===========================================================================
# 6. Import path stability
# ===========================================================================

class TestImportPathStability:
    def test_calculate_via_package_init(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_rate_level import (
            calculate_inflation_swap_rate_level as via_package,
        )
        from rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute import (
            calculate_inflation_swap_rate_level as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.inflation_swaps.tools.inflation_swap_rate_level import (
            InflationSwapRateLevelInput as via_package,
        )
        from rates_agent.inflation_swaps.tools.inflation_swap_rate_level.schemas import (
            InflationSwapRateLevelInput as via_schemas,
        )
        from rates_agent.inflation_swaps.tools.schemas import (
            InflationSwapRateLevelInput as via_hub,
        )
        assert via_package is via_schemas is via_hub


# ===========================================================================
# 7. Canonical TimeSeries output
# ===========================================================================

class TestCanonicalTimeSeries:
    def _run(self, params, raw_df, *, config=None):
        with patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar",
            return_value=raw_df,
        ), patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date",
            _FrozenDate,
        ):
            return calculate_inflation_swap_rate_level(
                engine=None, params=params, config=config,
            )

    def test_time_series_field_present(self):
        raw_df = _synthetic_raw_df()
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        assert "time_series" in out
        # Singular TimeSeries object, not a list — matches v6 pattern.
        assert isinstance(out["time_series"], dict)

    def test_time_series_uses_closed_enum_units(self):
        raw_df = _synthetic_raw_df()
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        ts = out["time_series"]
        assert ts["units"] == "percent"

    def test_time_series_name_carries_zcis_rate_suffix(self):
        """``_zcis_rate`` suffix distinguishes from sovereign nominal
        yield series (``_yield``), OIS rate series (``_ois_rate``),
        and linker real-yield series (``_real_yield``) when they end
        up in the same operator panel downstream."""
        raw_df = _synthetic_raw_df()
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        ts = out["time_series"]
        assert ts["series_name"] == "usd_zcis_5y_zcis_rate"

    def test_time_series_description_surfaces_reference_metadata(self):
        """The TimeSeries description must echo
        inflation_index_family / index_lag / interpolation so the
        cross-curve comparability caveat is visible to callers that
        consume only the time_series payload."""
        raw_df = _synthetic_raw_df(
            inflation_index_family="US_CPI_URBAN",
            index_lag="3M",
            interpolation="Daily",
        )
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        desc = out["time_series"]["description"]
        assert "US_CPI_URBAN" in desc
        assert "3M" in desc
        assert "Daily" in desc

    def test_time_series_length_matches_observation_count(self):
        raw_df = _synthetic_raw_df()
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        ts = out["time_series"]
        assert len(ts["rows"]) == out["current_metrics"]["observation_count"]

    def test_time_series_last_value_matches_snapshot_STRICTLY(self):
        """Latest row in the canonical series MUST equal
        ``zcis_rate_pct`` STRICTLY (not just within tolerance) — both
        go through the same ``yield_round_decimals`` convention
        applied via ``compute_level_metrics`` and the canonical
        builder."""
        raw_df = _synthetic_raw_df()
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        ts = out["time_series"]
        last_row_value = ts["rows"][-1]["value"]
        snapshot_value = out["current_metrics"]["zcis_rate_pct"]
        assert last_row_value == snapshot_value

    def test_yaml_yield_round_decimals_change_propagates_to_time_series(self):
        raw_df = _synthetic_raw_df()
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        out = self._run(
            params, raw_df, config=_build_config(yield_round_decimals=2),
        )
        ts = out["time_series"]
        for row in ts["rows"]:
            v = row["value"]
            if v is not None:
                assert v == round(v, 2)

    def test_time_series_dates_chronological(self):
        raw_df = _synthetic_raw_df()
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        ts = out["time_series"]
        dates = [r["date"] for r in ts["rows"]]
        assert dates == sorted(dates)

    def test_time_series_validates_against_TimeSeries_schema(self):
        from shared.schemas import TimeSeries
        raw_df = _synthetic_raw_df()
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        TimeSeries.model_validate(out["time_series"])


# ===========================================================================
# 8. ZCIS instrument_type + pricing_type guard
# ===========================================================================

class TestZcisInstrumentTypeGuard:
    """The compute path filters the SELECT by
    ``instrument_type='inflation_swap'`` AND
    ``pricing_type='zero_coupon_breakeven'`` AND
    ``curve_family=<user>`` AND ``tenor=<user>``.  Without that, a
    non-ZCIS instrument that happened to share a curve_family /
    tenor label would silently flow through under a zcis_rate_pct
    label.  These tests pin the guarantee at two layers: zero rows
    surface a controlled error envelope mentioning both filters.
    """

    def test_no_rows_returns_controlled_error_envelope(self):
        empty_df = pd.DataFrame(columns=[
            "trade_date", "field_value", "vendor_ticker",
            "pricing_type", "inflation_index_family", "index_lag",
            "interpolation", "underlying_index",
        ])
        params = InflationSwapRateLevelInput(
            curve_family="UST", tenor="10Y", lookback_days=365,
        )
        with patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar",
            return_value=empty_df,
        ), patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date",
            _FrozenDate,
        ):
            out = calculate_inflation_swap_rate_level(
                engine=None, params=params,
            )
        assert "error" in out
        assert "current_metrics" not in out
        # The error message must mention BOTH filters so an operator
        # can tell the difference between "wrong instrument_type",
        # "wrong pricing_type", and "wrong curve_family / tenor".
        assert "inflation_swap" in out["error"]
        assert "zero_coupon_breakeven" in out["error"]


# ===========================================================================
# 9. Reference-metadata + methodology_label surfacing
# ===========================================================================

class TestReferenceMetadataAndMethodologyLabel:
    def _run(self, params, raw_df, *, config=None):
        with patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar",
            return_value=raw_df,
        ), patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date",
            _FrozenDate,
        ):
            return calculate_inflation_swap_rate_level(
                engine=None, params=params, config=config,
            )

    def test_reference_metadata_threads_to_current_metrics(self):
        raw_df = _synthetic_raw_df(
            inflation_index_family="EU_HICP",
            index_lag="3M",
            interpolation="Monthly",
            underlying_index="CPTFEMU Index",
            vendor_ticker="EUSWI5 Curncy",
        )
        params = InflationSwapRateLevelInput(
            curve_family="EUR_ZCIS", tenor="5Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        cm = out["current_metrics"]
        assert cm["inflation_index_family"] == "EU_HICP"
        assert cm["index_lag"] == "3M"
        assert cm["interpolation"] == "Monthly"
        assert cm["underlying_index"] == "CPTFEMU Index"

    def test_methodology_label_threaded_from_yaml(self):
        raw_df = _synthetic_raw_df()
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        custom_label = "test methodology label DEADBEEF"
        out = self._run(
            params, raw_df,
            config=_build_config(methodology_label=custom_label),
        )
        assert (
            out["current_metrics"]["methodology_label"] == custom_label
        )

    def test_methodology_label_uses_bundled_yaml_when_no_override(self):
        raw_df = _synthetic_raw_df()
        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        out = self._run(params, raw_df)  # auto-load bundled config
        cfg = load_tool_config(CONFIG_PATH)
        # Strip whitespace for comparison; the compute path strips it
        # too.
        assert (
            out["current_metrics"]["methodology_label"]
            == cfg.methodology.what_it_does.strip()
        )


# ===========================================================================
# 10. Ambiguous-ticker guard
# ===========================================================================

class TestAmbiguousTickerGuard:
    """If the SELECT spans more than one vendor_ticker for the same
    (curve_family, tenor) pair, the tool MUST refuse to silently
    average across them and instead surface a controlled error
    envelope.  The playbook canonically yields exactly one ticker
    per pillar; multiple tickers means a real ambiguity in the
    underlying data.
    """

    def test_two_tickers_yields_controlled_error(self):
        df_a = _synthetic_raw_df(vendor_ticker="USSWIT5 Curncy")
        df_b = _synthetic_raw_df(vendor_ticker="USSWIT5_OLD Curncy")
        merged = pd.concat([df_a, df_b], ignore_index=True)

        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        with patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar",
            return_value=merged,
        ), patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date",
            _FrozenDate,
        ):
            out = calculate_inflation_swap_rate_level(
                engine=None, params=params,
            )
        assert "error" in out
        assert "current_metrics" not in out
        assert "Ambiguous instrument_master resolution" in out["error"]
        assert "USSWIT5 Curncy" in out["error"]
        assert "USSWIT5_OLD Curncy" in out["error"]

    def test_mixed_inflation_index_family_yields_controlled_error(self):
        """If the SELECT returns rows tagged with two different
        inflation_index_family values for the same ticker, the tool
        MUST refuse to silently pick one."""
        df_a = _synthetic_raw_df(inflation_index_family="US_CPI_URBAN")
        df_b = _synthetic_raw_df(inflation_index_family="US_CPI_OLD")
        # Same ticker, different metadata — the realistic shape of a
        # data error.
        merged = pd.concat([df_a, df_b], ignore_index=True)

        params = InflationSwapRateLevelInput(
            curve_family="USD_ZCIS", tenor="5Y", lookback_days=365,
        )
        with patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.fetch_zcis_single_pillar",
            return_value=merged,
        ), patch(
            "rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute.date",
            _FrozenDate,
        ):
            out = calculate_inflation_swap_rate_level(
                engine=None, params=params,
            )
        assert "error" in out
        assert "Ambiguous reference metadata" in out["error"]
        assert "inflation_index_family" in out["error"]
