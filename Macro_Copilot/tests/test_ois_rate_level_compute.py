"""
test_ois_rate_level_compute.py — Unit tests for the OIS rate_level migration

Mirrors ``test_yield_levels_compute.py`` (the sovereign analog) so the
two surfaces evolve together.

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. compute() runs end-to-end against synthetic input with the
     bundled config and returns a well-formed output.
  3. Convention overrides actually change behaviour (z-window,
     ddof, period offsets, ffill, default_swap_rate_field).
  4. Honest placeholder for trailing_range_window_days: setting it to
     anything other than 252 raises NotImplementedError.
  5. Schema-layer behaviour: field_name defaults to None (sentinel),
     compute() resolves the sentinel against the YAML.
  6. Three import paths still resolve to the same Pydantic class.
  7. Canonical TimeSeries output (units = PERCENT, snapshot ==
     time_series.rows[-1].value strictly).

Tests are fully offline.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.ois.tools.rate_level import (
    CONFIG_PATH,
    get_ois_rate_level,
    OISRateLevelInput,
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
    drift_pct: float = -0.50,
    days: int = 400,
    frozen_today: date = date(2026, 4, 30),
) -> pd.DataFrame:
    """Build a single-tenor long-format DataFrame matching the shape
    fetch_single_tenor returns.  Linspace from 4.5 to 4.5+drift_pct
    over `days` business days ending at frozen_today.

    The OIS tool anchors observation_count to the data's latest date
    (NOT to date.today()), so this fixture's last business day is the
    effective ``as_of_date`` regardless of frozen-today patching.
    """
    bdays = pd.bdate_range(frozen_today - timedelta(days=days * 2), frozen_today)
    bdays = bdays[-days:]
    n = len(bdays)
    series = np.linspace(4.5, 4.5 + drift_pct, n)
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": series,
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
# overrides applied via ``_custom_config(**overrides)``.
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
    "default_swap_rate_field": "PX_LAST",
    "yield_round_decimals": 4,
    "z_score_round_decimals": 4,
    "high_low_round_decimals": 4,
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


# ===========================================================================
# 1. Bundled config.yaml
# ===========================================================================

class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "get_ois_rate_level_tool"
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
            "default_swap_rate_field",
            "yield_round_decimals",
            "z_score_round_decimals",
            "high_low_round_decimals",
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
        assert cfg.convention_value("default_swap_rate_field") == "PX_LAST"
        assert cfg.convention_value("yield_round_decimals") == 4
        assert cfg.convention_value("z_score_round_decimals") == 4
        assert cfg.convention_value("high_low_round_decimals") == 4

    def test_no_default_field_name_collision_with_sovereign(self):
        """The OIS tool MUST use ``default_swap_rate_field`` (not
        ``default_field_name``) because sovereign tools all set
        ``default_field_name: YLD_YTM_MID`` while OIS wants
        PX_LAST — same convention name with different values would
        trip the cross-config lint."""
        cfg = load_tool_config(CONFIG_PATH)
        assert "default_swap_rate_field" in cfg.conventions
        assert "default_field_name" not in cfg.conventions, (
            "OIS rate_level must not redeclare the sovereign convention "
            "name — would clash with sovereign value YLD_YTM_MID in the "
            "cross-config lint.  See module docstring."
        )

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 2
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "trailing_range_window_days" in joined
        assert "observation_count" in joined  # the sovereign-vs-OIS anchoring follow-up


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def _run(self, params, raw_df, config=None):
        with patch(
            "rates_agent.ois.tools.rate_level.compute.fetch_single_tenor",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.rate_level.compute.date",
            _FrozenDate,
        ):
            return get_ois_rate_level(engine=None, params=params, config=config)

    def test_default_config_returns_well_formed_output(self):
        raw_df = _synthetic_raw_df()
        params = OISRateLevelInput(
            curve_family="USD_SOFR_OIS", tenor="2Y", lookback_days=365,
        )
        out = self._run(params, raw_df)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        # Required snapshot fields present.
        for k in (
            "as_of_date", "curve_family", "tenor", "current_rate_pct",
            "daily_change_bps", "weekly_change_bps", "monthly_change_bps",
            "z_score", "high_252d_pct", "low_252d_pct", "percentile_252d",
            "observation_count",
        ):
            assert k in cm, f"missing {k}"

        # Wire-frozen field names — explicit check that the rename
        # plan was NOT silently applied.
        assert "high_window_pct" not in cm
        assert "trailing_window_days" not in cm

        # current_rate_pct should be the latest value (rounded).
        assert isinstance(cm["current_rate_pct"], float)

    def test_explicit_config_matches_auto_loaded(self):
        raw_df = _synthetic_raw_df()
        params = OISRateLevelInput(
            curve_family="USD_SOFR_OIS", tenor="2Y", lookback_days=365,
        )
        out_auto = self._run(params, raw_df, config=None)
        out_explicit = self._run(params, raw_df, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def _run(self, params, raw_df, config):
        with patch(
            "rates_agent.ois.tools.rate_level.compute.fetch_single_tenor",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.rate_level.compute.date",
            _FrozenDate,
        ):
            return get_ois_rate_level(engine=None, params=params, config=config)

    def test_z_score_window_override_changes_z(self):
        raw_df = _synthetic_raw_df()
        params = OISRateLevelInput(
            curve_family="USD_SOFR_OIS", tenor="2Y", lookback_days=365,
        )
        out_default = self._run(params, raw_df, _build_config())
        out_short = self._run(params, raw_df, _build_config(z_score_window_days=120))
        assert (
            out_default["current_metrics"]["z_score"]
            != out_short["current_metrics"]["z_score"]
        )

    def test_z_score_ddof_override_changes_z(self):
        raw_df = _synthetic_raw_df()
        params = OISRateLevelInput(
            curve_family="USD_SOFR_OIS", tenor="2Y", lookback_days=365,
        )
        out_sample = self._run(params, raw_df, _build_config(z_score_ddof=1))
        out_pop = self._run(params, raw_df, _build_config(z_score_ddof=0))
        assert (
            out_sample["current_metrics"]["z_score"]
            != out_pop["current_metrics"]["z_score"]
        )

    def test_period_offsets_override_changes_changes(self):
        raw_df = _synthetic_raw_df()
        params = OISRateLevelInput(
            curve_family="USD_SOFR_OIS", tenor="2Y", lookback_days=365,
        )
        out_default = self._run(params, raw_df, _build_config())
        out_wider = self._run(params, raw_df, _build_config(daily_change_offset_rows=5))
        assert (
            out_default["current_metrics"]["daily_change_bps"]
            != out_wider["current_metrics"]["daily_change_bps"]
        )

    def test_ffill_limit_passed_to_clean(self):
        from shared.analytics.levels import clean_single_series as real_clean

        raw_df = _synthetic_raw_df()
        params = OISRateLevelInput(
            curve_family="USD_SOFR_OIS", tenor="2Y", lookback_days=365,
        )
        with patch(
            "rates_agent.ois.tools.rate_level.compute.fetch_single_tenor",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.rate_level.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.ois.tools.rate_level.compute.clean_single_series",
            wraps=real_clean,
        ) as spy:
            get_ois_rate_level(
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
        params = OISRateLevelInput(
            curve_family="USD_SOFR_OIS", tenor="2Y", lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            get_ois_rate_level(
                engine=None, params=params,
                config=_build_config(trailing_range_window_days=180),
            )
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_pct" in msg
        assert "planned_extensions" in msg

    def test_supported_default_does_not_raise(self):
        raw_df = _synthetic_raw_df()
        params = OISRateLevelInput(
            curve_family="USD_SOFR_OIS", tenor="2Y", lookback_days=365,
        )
        with patch(
            "rates_agent.ois.tools.rate_level.compute.fetch_single_tenor",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.rate_level.compute.date",
            _FrozenDate,
        ):
            out = get_ois_rate_level(
                engine=None, params=params,
                config=_build_config(trailing_range_window_days=252),
            )
        assert "error" not in out


# ===========================================================================
# 5. Schema-layer field_name behaviour
# ===========================================================================

class TestFieldNameSchemaBehaviour:
    def test_field_name_default_is_none(self):
        params = OISRateLevelInput(curve_family="USD_SOFR_OIS", tenor="2Y")
        assert params.field_name is None, (
            "schema default must be None so compute() can fall through "
            "to the YAML's default_swap_rate_field"
        )

    def test_explicit_field_name_passes_through(self):
        params = OISRateLevelInput(
            curve_family="USD_SOFR_OIS", tenor="2Y", field_name="PX_BID",
        )
        assert params.field_name == "PX_BID"


class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_swap_rate_field reaches fetch."""

    def _capture_field_name(self, params, config):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.ois.tools.rate_level.compute.fetch_single_tenor",
            return_value=raw_df,
        ) as spy, patch(
            "rates_agent.ois.tools.rate_level.compute.date",
            _FrozenDate,
        ):
            get_ois_rate_level(engine=None, params=params, config=config)
        assert spy.call_count == 1
        return spy.call_args.kwargs["field_name"]

    def test_omitted_field_name_uses_yaml_default(self):
        params = OISRateLevelInput(
            curve_family="USD_SOFR_OIS", tenor="2Y",
        )  # no field_name
        passed = self._capture_field_name(
            params, _build_config(default_swap_rate_field="PX_LAST"),
        )
        assert passed == "PX_LAST"

    def test_yaml_override_changes_resolved_field(self):
        params = OISRateLevelInput(curve_family="USD_SOFR_OIS", tenor="2Y")
        passed = self._capture_field_name(
            params, _build_config(default_swap_rate_field="PX_BID"),
        )
        assert passed == "PX_BID"

    def test_explicit_field_name_overrides_yaml(self):
        params = OISRateLevelInput(
            curve_family="USD_SOFR_OIS", tenor="2Y", field_name="PX_ASK",
        )
        passed = self._capture_field_name(
            params, _build_config(default_swap_rate_field="PX_LAST"),
        )
        assert passed == "PX_ASK"


# ===========================================================================
# 6. Import path backward-compat
# ===========================================================================

class TestImportPathBackwardCompat:
    def test_get_ois_rate_level_via_package_init(self):
        from rates_agent.ois.tools.rate_level import (
            get_ois_rate_level as via_package,
        )
        from rates_agent.ois.tools.rate_level.compute import (
            get_ois_rate_level as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.ois.tools.rate_level import (
            OISRateLevelInput as via_package,
        )
        from rates_agent.ois.tools.rate_level.schemas import (
            OISRateLevelInput as via_schemas,
        )
        from rates_agent.ois.tools.schemas import (
            OISRateLevelInput as via_hub,
        )
        assert via_package is via_schemas is via_hub

    def test_legacy_single_file_path_is_deleted(self):
        """The legacy single-file ``rates_agent/ois/tools/rate_level.py``
        and ``rates_agent/ois/tools/schemas/rate_level.py`` must NOT
        be importable as modules — the per-tool-folder package now
        owns those names."""
        import importlib
        import rates_agent.ois.tools.rate_level as pkg
        # The package must resolve to a folder, not a single file —
        # ``__path__`` exists only on packages.
        assert hasattr(pkg, "__path__"), (
            "rate_level must be a package (folder), not a single-file "
            "module — migration is incomplete otherwise"
        )
        # The legacy ``schemas/rate_level`` shim must be gone.
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(
                "rates_agent.ois.tools.schemas.rate_level"
            )


# ===========================================================================
# 7. Canonical TimeSeries output
# ===========================================================================

class TestCanonicalTimeSeries:
    """Pin the canonical TimeSeries contract on the OIS rate_level
    surface.  Field name is ``time_series`` (singular ``TimeSeries``
    value), matching the v6 sovereign primitive convention used by
    ``yield_levels`` / ``zscore_custom``.  Each row is rounded with
    the YAML-controlled ``yield_round_decimals`` convention so the
    snapshot's ``current_rate_pct`` equals
    ``time_series.rows[-1].value`` STRICTLY (not just within
    tolerance).
    """

    def _run(self, params, raw_df, *, config=None):
        with patch(
            "rates_agent.ois.tools.rate_level.compute.fetch_single_tenor",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.rate_level.compute.date",
            _FrozenDate,
        ):
            return get_ois_rate_level(engine=None, params=params, config=config)

    def test_time_series_field_present(self):
        raw_df = _synthetic_raw_df()
        params = OISRateLevelInput(
            curve_family="USD_SOFR_OIS", tenor="2Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        assert "time_series" in out
        # Singular TimeSeries object, not a list — matches v6 pattern.
        assert isinstance(out["time_series"], dict)

    def test_time_series_uses_closed_enum_units(self):
        raw_df = _synthetic_raw_df()
        params = OISRateLevelInput(
            curve_family="USD_SOFR_OIS", tenor="2Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        ts = out["time_series"]
        assert ts["units"] == "percent"

    def test_time_series_name_follows_convention(self):
        raw_df = _synthetic_raw_df()
        params = OISRateLevelInput(
            curve_family="USD_SOFR_OIS", tenor="2Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        ts = out["time_series"]
        assert ts["series_name"] == "usd_sofr_ois_2y_ois_rate"

    def test_time_series_length_matches_observation_count(self):
        """The canonical series covers the same display window the
        snapshot's observation_count was computed from — length must
        match exactly."""
        raw_df = _synthetic_raw_df()
        params = OISRateLevelInput(
            curve_family="USD_SOFR_OIS", tenor="2Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        ts = out["time_series"]
        assert len(ts["rows"]) == out["current_metrics"]["observation_count"]

    def test_time_series_last_value_matches_snapshot_STRICTLY(self):
        """Latest row in the canonical series MUST equal
        ``current_rate_pct`` STRICTLY (not just within tolerance) —
        both go through the same ``yield_round_decimals`` convention
        applied via ``compute_level_metrics`` and the canonical
        builder."""
        raw_df = _synthetic_raw_df()
        params = OISRateLevelInput(
            curve_family="USD_SOFR_OIS", tenor="2Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        ts = out["time_series"]
        last_row_value = ts["rows"][-1]["value"]
        snapshot_value = out["current_metrics"]["current_rate_pct"]
        # Strict equality — proves the YAML rounding convention is
        # threaded through both paths.
        assert last_row_value == snapshot_value

    def test_yaml_yield_round_decimals_change_propagates_to_time_series(self):
        """Tweaking the YAML's ``yield_round_decimals`` MUST change
        the precision of the canonical series.  Regression guard
        against the canonical builder hardcoding a default instead
        of reading the YAML."""
        raw_df = _synthetic_raw_df()
        params = OISRateLevelInput(
            curve_family="USD_SOFR_OIS", tenor="2Y", lookback_days=365,
        )
        out = self._run(params, raw_df, config=_build_config(yield_round_decimals=2))
        ts = out["time_series"]
        for row in ts["rows"]:
            v = row["value"]
            if v is not None:
                assert v == round(v, 2)

    def test_time_series_dates_chronological(self):
        raw_df = _synthetic_raw_df()
        params = OISRateLevelInput(
            curve_family="USD_SOFR_OIS", tenor="2Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        ts = out["time_series"]
        dates = [r["date"] for r in ts["rows"]]
        assert dates == sorted(dates)

    def test_time_series_validates_against_TimeSeries_schema(self):
        """The output dict must round-trip cleanly through the
        canonical ``shared.schemas.TimeSeries`` model — guards against
        the bespoke shape silently leaking back in."""
        from shared.schemas import TimeSeries
        raw_df = _synthetic_raw_df()
        params = OISRateLevelInput(
            curve_family="USD_SOFR_OIS", tenor="2Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        TimeSeries.model_validate(out["time_series"])
