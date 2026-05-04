"""
test_curve_spread_compute.py — Unit tests for the new config-driven curve_spread tool
======================================================================================

Verifies the seam introduced in commit 3 of the tool-config pilot:

  1. The bundled ``config.yaml`` is structurally valid and loads
     cleanly via ``shared.config.load_tool_config``.
  2. ``calculate_curve_spread`` runs end-to-end against synthetic
     input with the bundled config and returns a well-formed output.
  3. Passing a custom ``ToolConfig`` with a different convention
     value actually changes the output — proves every convention is
     wired through to the underlying primitive call.
  4. The legacy import paths
     (``...tools.curve_spread import ...`` and
      ``...tools.schemas import CurveSpreadInput``) still resolve to
     the same Pydantic class.

These tests are fully offline — the DB fetcher is mocked and
``date.today()`` is frozen.  They complement the parity test from
commit 0 (which freezes real production behaviour byte-for-byte once
fixtures are captured); the parity test asserts "math is unchanged",
the tests below assert "config seam is wired".
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.sovereign_bonds.tools.curve_spread import calculate_curve_spread
from rates_agent.sovereign_bonds.tools.curve_spread.compute import CONFIG_PATH
from rates_agent.sovereign_bonds.tools.curve_spread.schemas import CurveSpreadInput
from shared.config import (
    Convention,
    MethodologyMeta,
    ToolConfig,
    ToolMeta,
    clear_tool_config_cache,
    load_tool_config,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _synthetic_raw_df(years: int = 5, frozen_today: date = date(2026, 4, 30)) -> pd.DataFrame:
    """Build a synthetic long-format raw DataFrame matching the shape
    fetch_tenor_pair returns for two tenors of one curve."""
    rs = np.random.RandomState(11)
    bdays = pd.bdate_range(frozen_today - timedelta(days=years * 365), frozen_today)

    def _series(tenor: str, mu: float, vol: float, init: float, seed: int) -> pd.DataFrame:
        rs2 = np.random.RandomState(seed)
        n = len(bdays)
        v = np.empty(n, dtype=float)
        v[0] = init
        for i in range(1, n):
            v[i] = v[i - 1] + 0.005 * (mu - v[i - 1]) + rs2.randn() * vol
        return pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "tenor": tenor,
            "field_value": v,
        })

    return pd.concat(
        [
            _series("2Y", 4.30, 0.045, 4.20, 11),
            _series("10Y", 4.55, 0.040, 4.50, 12),
        ],
        ignore_index=True,
    ).sort_values(["trade_date", "tenor"]).reset_index(drop=True)


class _FrozenDate(date):
    """date subclass with today() returning a fixed value."""
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


# ===========================================================================
# 1. Bundled config.yaml structurally valid
# ===========================================================================

class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file(), (
            f"Bundled config not found at {CONFIG_PATH}"
        )

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "calculate_curve_spread_tool"
        assert cfg.tool.domain == "sovereign_bonds"

    def test_required_conventions_present(self):
        """Every convention compute.py reads must be in the YAML.  If
        a convention is renamed in YAML without updating compute.py
        this test fails before any tool call does."""
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "z_score_window_days",
            "z_score_min_periods",
            "z_score_ddof",
            "z_score_buffer_multiplier",
            "ffill_limit_days",
            "spread_bps_round_decimals",
            "z_score_round_decimals",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing conventions: {sorted(missing)}"

    def test_convention_defaults_match_legacy_constants(self):
        """The bundled defaults must reproduce the pre-commit-3 values
        exactly so the parity fixture remains valid when re-captured."""
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("z_score_window_days") == 252
        assert cfg.convention_value("z_score_min_periods") == 60
        assert cfg.convention_value("z_score_ddof") == 1
        assert cfg.convention_value("z_score_buffer_multiplier") == 1.5
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("spread_bps_round_decimals") == 2
        assert cfg.convention_value("z_score_round_decimals") == 4

    def test_methodology_block_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.methodology.what_it_does
        assert len(cfg.methodology.assumptions) > 0


# ===========================================================================
# 2. End-to-end happy path with the bundled config
# ===========================================================================

class TestComputeHappyPath:
    """Exercises the full compute() pipeline with synthetic data and
    the bundled config — proves every wiring step works end-to-end."""

    def _run(self, params: CurveSpreadInput, config: ToolConfig | None = None):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.sovereign_bonds.tools.curve_spread.compute.fetch_tenor_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.curve_spread.compute.date",
            _FrozenDate,
        ):
            return calculate_curve_spread(engine=None, params=params, config=config)

    def test_default_config_returns_well_formed_output(self):
        params = CurveSpreadInput(
            curve_family="UST", short_tenor="2Y", long_tenor="10Y",
            lookback_days=365, field_name="YLD_YTM_MID",
        )
        out = self._run(params)

        assert "error" not in out, f"unexpected error: {out.get('error')!r}"
        assert "current_metrics" in out
        assert "time_series" in out

        cm = out["current_metrics"]
        assert cm["curve_family"] == "UST"
        assert cm["spread_label"] == "2s10s"
        assert cm["rolling_window_days"] == 252
        assert isinstance(cm["current_spread_bps"], float)
        # Z-score buffer is large enough that the latest z-score is
        # populated (not None).
        assert cm["current_z_score"] is not None

        ts = out["time_series"]
        assert len(ts) > 0
        assert all("date" in row and "spread_bps" in row for row in ts)

    def test_explicit_default_config_matches_auto_loaded(self):
        """Passing config=load_tool_config(CONFIG_PATH) explicitly must
        produce identical output to passing config=None."""
        params = CurveSpreadInput(
            curve_family="UST", short_tenor="2Y", long_tenor="10Y",
            lookback_days=365, field_name="YLD_YTM_MID",
        )
        out_auto = self._run(params, config=None)
        out_explicit = self._run(params, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit


# ===========================================================================
# 3. Convention overrides actually change output (wiring proof)
# ===========================================================================

class TestConventionOverrides:
    """For every convention compute.py reads, prove the override path
    is wired through.

    For conventions whose change has an observable impact on the
    output of the synthetic-data run (window, ddof, the two
    round_decimals), we override and compare outputs.

    For conventions whose change can be silent on a well-buffered
    synthetic series (``z_score_min_periods``,
    ``z_score_buffer_multiplier``, ``ffill_limit_days``), we use
    ``wraps=``-style spies on the underlying primitive / fetcher to
    assert the override value was actually passed down — a wiring
    proof rather than an output proof.

    Together the tests below cover all seven conventions declared in
    the bundled ``config.yaml``.
    """

    def _custom_config(self, **overrides) -> ToolConfig:
        """Build a ToolConfig with a default convention block plus
        overrides applied on top."""
        defaults = {
            "z_score_window_days": 252,
            "z_score_min_periods": 60,
            "z_score_ddof": 1,
            "z_score_buffer_multiplier": 1.5,
            "ffill_limit_days": 5,
            "spread_bps_round_decimals": 2,
            "z_score_round_decimals": 4,
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

    def _run(self, params: CurveSpreadInput, config: ToolConfig):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.sovereign_bonds.tools.curve_spread.compute.fetch_tenor_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.curve_spread.compute.date",
            _FrozenDate,
        ):
            return calculate_curve_spread(engine=None, params=params, config=config)

    @pytest.fixture
    def params(self):
        return CurveSpreadInput(
            curve_family="UST", short_tenor="2Y", long_tenor="10Y",
            lookback_days=365, field_name="YLD_YTM_MID",
        )

    def test_z_score_window_override_changes_z(self, params):
        """A different window produces a different rolling z-score."""
        out_default = self._run(params, self._custom_config())
        out_override = self._run(params, self._custom_config(z_score_window_days=120))
        # current_spread_bps depends only on the latest two yields; that
        # should be unchanged.
        assert out_default["current_metrics"]["current_spread_bps"] == \
               out_override["current_metrics"]["current_spread_bps"]
        # current_z_score depends on the rolling window — must differ.
        assert out_default["current_metrics"]["current_z_score"] != \
               out_override["current_metrics"]["current_z_score"]

    def test_z_score_ddof_override_changes_z(self, params):
        """Population vs sample std produces different z-scores."""
        out_sample = self._run(params, self._custom_config(z_score_ddof=1))
        out_pop = self._run(params, self._custom_config(z_score_ddof=0))
        assert out_sample["current_metrics"]["current_z_score"] != \
               out_pop["current_metrics"]["current_z_score"]

    def test_spread_round_decimals_override_changes_precision(self, params):
        """Rounding to 4 dp instead of 2 dp must change at least one
        time_series row's spread_bps."""
        out_2dp = self._run(params, self._custom_config(spread_bps_round_decimals=2))
        out_4dp = self._run(params, self._custom_config(spread_bps_round_decimals=4))

        ts_2dp = out_2dp["time_series"]
        ts_4dp = out_4dp["time_series"]
        assert len(ts_2dp) == len(ts_4dp)

        # At least one row must differ — synthetic data has enough
        # variation that rounding precision is observable.
        differs = any(
            ts_2dp[i]["spread_bps"] != ts_4dp[i]["spread_bps"]
            for i in range(len(ts_2dp))
        )
        assert differs, "spread_bps_round_decimals override had no effect"

    def test_zscore_round_decimals_override_changes_precision(self, params):
        out_4 = self._run(params, self._custom_config(z_score_round_decimals=4))
        out_2 = self._run(params, self._custom_config(z_score_round_decimals=2))
        # 4dp z-score has at most 4 decimals, 2dp has at most 2;
        # the most-recent z-scores must differ on at least one
        # observation in the time_series.
        ts_4 = out_4["time_series"]
        ts_2 = out_2["time_series"]
        differs = any(
            ts_4[i].get("z_score") != ts_2[i].get("z_score")
            for i in range(len(ts_4))
            if ts_4[i].get("z_score") is not None and ts_2[i].get("z_score") is not None
        )
        assert differs, "z_score_round_decimals override had no effect"

    def test_rolling_window_days_appears_in_metrics(self, params):
        """The rolling_window_days field in current_metrics should
        reflect the convention value, not a hardcoded 252."""
        out = self._run(params, self._custom_config(z_score_window_days=180))
        assert out["current_metrics"]["rolling_window_days"] == 180

    # ---------------------------------------------------------------
    # Wiring proofs — for conventions whose override has no observable
    # output diff on this synthetic data, spy on the underlying
    # primitive / fetcher to assert the value was passed through.
    # ---------------------------------------------------------------

    def test_min_periods_passed_to_rolling_zscore(self, params):
        """``z_score_min_periods`` flows from config → rolling_zscore.

        For a well-buffered synthetic series the displayed z-scores
        are populated regardless of min_periods (every displayed row
        has full window-warmup behind it), so the only reliable
        wiring proof is to inspect the kwargs the primitive was
        invoked with.
        """
        from shared.analytics.spreads import rolling_zscore as real_rolling

        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.sovereign_bonds.tools.curve_spread.compute.fetch_tenor_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.curve_spread.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.sovereign_bonds.tools.curve_spread.compute.rolling_zscore",
            wraps=real_rolling,
        ) as spy:
            calculate_curve_spread(
                engine=None,
                params=params,
                config=self._custom_config(z_score_min_periods=200),
            )

        assert spy.call_count >= 1
        # min_periods kwarg must reflect the override.
        assert spy.call_args.kwargs["min_periods"] == 200
        # And the other conventions should be at their default (sanity).
        assert spy.call_args.kwargs["window"] == 252

    def test_buffer_multiplier_changes_fetch_start_date(self, params):
        """``z_score_buffer_multiplier`` flows into ``int(window * mult)``
        and shifts the ``start_date`` passed to ``fetch_tenor_pair``.

        We capture ``start_date`` from the mock under default vs
        overridden config and assert the override fetches further
        back in time, by exactly the expected number of days.
        """
        raw_df = _synthetic_raw_df()

        # Default config: buffer_mult=1.5, window=252 → buffer=378d.
        with patch(
            "rates_agent.sovereign_bonds.tools.curve_spread.compute.fetch_tenor_pair",
            return_value=raw_df,
        ) as spy_default, patch(
            "rates_agent.sovereign_bonds.tools.curve_spread.compute.date",
            _FrozenDate,
        ):
            calculate_curve_spread(
                engine=None, params=params, config=self._custom_config(),
            )
        default_start = spy_default.call_args.kwargs["start_date"]

        # Override: buffer_mult=2.0, window=252 → buffer=504d.
        with patch(
            "rates_agent.sovereign_bonds.tools.curve_spread.compute.fetch_tenor_pair",
            return_value=raw_df,
        ) as spy_override, patch(
            "rates_agent.sovereign_bonds.tools.curve_spread.compute.date",
            _FrozenDate,
        ):
            calculate_curve_spread(
                engine=None, params=params,
                config=self._custom_config(z_score_buffer_multiplier=2.0),
            )
        override_start = spy_override.call_args.kwargs["start_date"]

        # Larger buffer must fetch from EARLIER.
        assert override_start < default_start
        # The exact diff: int(252*2.0) - int(252*1.5) = 504 - 378 = 126
        # calendar days.  If the wiring drifts (e.g. compute multiplies
        # by the wrong key, or the cast to int changes), this fails
        # with a precise number rather than a vague "differs".
        diff_days = (default_start - override_start).days
        assert diff_days == 126, (
            f"expected 126-day shift from buffer_mult 1.5→2.0, got {diff_days}"
        )

    def test_ffill_limit_passed_to_pivot(self, params):
        """``ffill_limit_days`` flows from config → pivot_and_align_tenors.

        On the synthetic series there are no holiday gaps, so the
        ffill_limit is unobservable in the output; spy on the
        primitive to verify the value was passed.
        """
        from shared.analytics.spreads import pivot_and_align_tenors as real_pivot

        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.sovereign_bonds.tools.curve_spread.compute.fetch_tenor_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.curve_spread.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.sovereign_bonds.tools.curve_spread.compute.pivot_and_align_tenors",
            wraps=real_pivot,
        ) as spy:
            calculate_curve_spread(
                engine=None,
                params=params,
                config=self._custom_config(ffill_limit_days=2),
            )

        assert spy.call_count >= 1
        assert spy.call_args.kwargs["ffill_limit"] == 2


# ===========================================================================
# 4. Import-path resolution after the per-tool-folder migration
# ===========================================================================

class TestImportPathBackwardCompat:
    """Commit 3 of the tool-config pilot replaced ``tools/curve_spread.py``
    with a package and added a thin ``tools/schemas/spread.py``
    re-export shim for the schemas.  Commit 5 deletes that shim;
    callers must use one of the two surviving paths.

    The tests below lock in:
      - ``calculate_curve_spread`` resolves to the same callable via
        the package init and via the .compute submodule;
      - ``CurveSpreadInput`` resolves to the same class via the three
        canonical paths;
      - the deleted shim path now raises ``ModuleNotFoundError``.

    If a future caller restores the legacy
    ``...tools.schemas.spread`` shim, the third test below fails
    loudly — the deletion is now a load-bearing test."""

    def test_calculate_curve_spread_via_package_init(self):
        from rates_agent.sovereign_bonds.tools.curve_spread import (
            calculate_curve_spread as via_package,
        )
        from rates_agent.sovereign_bonds.tools.curve_spread.compute import (
            calculate_curve_spread as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        """Three canonical paths still resolve to the same class."""
        from rates_agent.sovereign_bonds.tools.curve_spread import (
            CurveSpreadInput as via_package,
        )
        from rates_agent.sovereign_bonds.tools.curve_spread.schemas import (
            CurveSpreadInput as via_schemas,
        )
        from rates_agent.sovereign_bonds.tools.schemas import (
            CurveSpreadInput as via_hub,
        )
        assert via_package is via_schemas
        assert via_package is via_hub

    def test_legacy_shim_path_is_deleted(self):
        """``rates_agent.sovereign_bonds.tools.schemas.spread`` was a
        re-export shim; commit 5 deleted it.  The import must fail
        with ``ModuleNotFoundError``.

        If this test ever flips to "passes the import", the shim has
        been restored without removing this test — review whether
        that's intentional."""
        with pytest.raises(ModuleNotFoundError):
            import rates_agent.sovereign_bonds.tools.schemas.spread  # noqa: F401


# ===========================================================================
# Canonical TimeSeries output (legacy-TimeSeries cleanup)
# ===========================================================================


class TestCanonicalTimeSeries:
    """Pin the legacy-TimeSeries cleanup contract: curve_spread emits
    TWO canonical ``TimeSeries`` fields (``time_series_spread`` in BPS
    and ``time_series_zscore`` in Z_SCORE) alongside its wire-frozen
    bespoke ``time_series: List[CurveSpreadTimeSeriesRow]`` array.
    Tests verify both canonical series align point-by-point with the
    bespoke rows so the three cannot drift."""

    def _run(self, params: CurveSpreadInput, config=None):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.sovereign_bonds.tools.curve_spread.compute.fetch_tenor_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.curve_spread.compute.date",
            _FrozenDate,
        ):
            return calculate_curve_spread(
                engine=None, params=params, config=config,
            )

    def _params(self):
        return CurveSpreadInput(
            curve_family="UST", short_tenor="2Y", long_tenor="10Y",
            lookback_days=365, field_name="YLD_YTM_MID",
        )

    def test_canonical_time_series_legacy_field_NOT_present(self):
        """Regression guard: the transitional ``canonical_time_series``
        name from PR #58 must be gone after the rename to v6 fields."""
        out = self._run(self._params())
        assert "canonical_time_series" not in out

    def test_time_series_spread_field_present(self):
        out = self._run(self._params())
        assert "time_series_spread" in out
        assert isinstance(out["time_series_spread"], dict)

    def test_time_series_zscore_field_present(self):
        out = self._run(self._params())
        assert "time_series_zscore" in out
        assert isinstance(out["time_series_zscore"], dict)

    def test_spread_series_uses_BPS(self):
        out = self._run(self._params())
        assert out["time_series_spread"]["units"] == "bps"

    def test_zscore_series_uses_Z_SCORE(self):
        out = self._run(self._params())
        assert out["time_series_zscore"]["units"] == "z_score"

    def test_spread_series_name_follows_convention(self):
        out = self._run(self._params())
        assert out["time_series_spread"]["series_name"] == "ust_2y_10y_spread"

    def test_zscore_series_name_follows_convention(self):
        out = self._run(self._params())
        assert out["time_series_zscore"]["series_name"] == "ust_2y_10y_zscore"

    def test_both_series_length_equals_bespoke_length(self):
        out = self._run(self._params())
        spread = out["time_series_spread"]
        zscore = out["time_series_zscore"]
        bespoke = out["time_series"]
        assert len(spread["rows"]) == len(bespoke)
        assert len(zscore["rows"]) == len(bespoke)

    def test_spread_values_match_bespoke_pointwise(self):
        """Every spread row MUST equal the bespoke row's spread_bps at
        the same index — proves they share the same display_df."""
        out = self._run(self._params())
        spread = out["time_series_spread"]
        bespoke = out["time_series"]
        for i, (c_row, b_row) in enumerate(zip(spread["rows"], bespoke)):
            assert c_row["date"] == b_row["date"], f"row {i}: date mismatch"
            assert c_row["value"] == b_row["spread_bps"], (
                f"row {i}: value mismatch"
            )

    def test_zscore_values_match_bespoke_pointwise(self):
        """Z-score canonical series must also align with the bespoke
        z_score column — Codex P1 follow-up: prior cleanup canonicalized
        only the primary value series, leaving the z-score historical
        signal as legacy-only.  This pins the fix."""
        out = self._run(self._params())
        zscore = out["time_series_zscore"]
        bespoke = out["time_series"]
        for i, (c_row, b_row) in enumerate(zip(zscore["rows"], bespoke)):
            assert c_row["date"] == b_row["date"], f"row {i}: date mismatch"
            assert c_row["value"] == b_row["z_score"], (
                f"row {i}: value mismatch"
            )

    def test_both_canonical_series_validate_against_TimeSeries_schema(self):
        from shared.schemas import TimeSeries
        out = self._run(self._params())
        TimeSeries.model_validate(out["time_series_spread"])
        TimeSeries.model_validate(out["time_series_zscore"])
