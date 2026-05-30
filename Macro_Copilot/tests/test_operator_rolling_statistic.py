"""Tests for shared.operators.rolling_statistic.

Covers the contract surface every v2.0 operator must satisfy:

  - happy path for each statistic (mean / std / min / max / sum) against
    an independent NumPy / pandas reference
  - look-ahead-safe vs in-sample mechanics (one-period shift)
  - OPR2 constant output type (always Series; input unit preserved)
  - OPR8 config-default resolution (params=None matches explicit defaults)
  - OPR8 schema-default mirrors the YAML default (no silent divergence)
  - OPR9 typed I/O (non-Series input refused)
  - OPR10 lineage extension (N -> N+1; one OperatorStep) + OPR14 determinism
  - OPR11 single-input operator (does NOT carry require_matching_* flags)
  - OPR13 typed RollingStatisticError on every degenerate input
  - Honest refusal path: a Literal-bypassing statistic raises
    NotImplementedError (defensive; not reachable through *Params)
  - Unit PRESERVATION across each statistic (no coercion)
  - OPR6 finance-blindness (runs on non-rates synthetic data, any unit)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.artifacts import (
    AdapterStep,
    CleanSingleSeriesV1,
    FetchStep,
    Lineage,
    Series,
    TimeSeriesUnits,
)
from shared.config.operator_config import (
    clear_operator_config_cache,
    load_operator_config,
)
from shared.operators.rolling_statistic import (
    CONFIG_PATH,
    RollingStatisticParams,
    rolling_statistic,
)
from shared.operators.rolling_statistic.operator import RollingStatisticError


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_operator_config_cache()
    yield
    clear_operator_config_cache()


def _series(
    series_key: str,
    *,
    dates,
    values,
    units: TimeSeriesUnits = TimeSeriesUnits.PERCENT,
    frequency=None,
    missingness_policy=None,
) -> Series:
    fetch = FetchStep.build(
        name="fetch_single_tenor", version="1.0.0",
        params={"series_key": series_key},
    )
    adapter = AdapterStep.build(
        name="raw_dataframe_to_artifact_series", version="1.0.0",
        params={"series_key": series_key, "units": units.value},
        input_hashes=(fetch.hash,),
    )
    payload = pd.Series(values, index=pd.DatetimeIndex(dates), dtype=float)
    return Series(
        series_key=series_key,
        payload=payload,
        units=units,
        frequency=frequency,
        missingness_policy=missingness_policy or CleanSingleSeriesV1(ffill_limit=5),
        lineage=Lineage.from_steps([fetch, adapter]),
    )


# ===========================================================================
# 1. Happy path — every statistic matches pandas + numpy reference
# ===========================================================================


class TestHappyPath:
    @pytest.mark.parametrize("statistic", ["mean", "std", "min", "max", "sum"])
    def test_matches_pandas_rolling(self, statistic):
        rng = np.random.RandomState(3)
        values = list(rng.randn(30) * 5 + 100)
        dates = pd.bdate_range("2026-01-01", periods=30)
        s = _series("synth", dates=dates, values=values)

        window = 5
        out = rolling_statistic(
            s,
            params=RollingStatisticParams(
                statistic=statistic, window=window, look_ahead_safe=False,
            ),
        )
        ref_payload = pd.Series(values, index=pd.DatetimeIndex(dates), dtype=float)
        roller = ref_payload.rolling(window=window, min_periods=window)
        if statistic == "mean":
            expected = roller.mean()
        elif statistic == "std":
            expected = roller.std(ddof=1)
        elif statistic == "min":
            expected = roller.min()
        elif statistic == "max":
            expected = roller.max()
        else:
            expected = roller.sum()
        actual = out.payload
        # Match element-wise on the non-NaN positions.
        ok = expected.notna()
        np.testing.assert_allclose(
            actual.loc[ok].to_numpy(),
            expected.loc[ok].to_numpy(),
            rtol=1e-12, atol=1e-12,
        )
        # Warmup positions are NaN
        assert actual.loc[~ok].isna().all()

    def test_look_ahead_safe_shifts_by_one(self):
        dates = pd.bdate_range("2026-01-01", periods=20)
        values = list(np.arange(20, dtype=float))
        s = _series("x", dates=dates, values=values)
        window = 5

        out_nosafe = rolling_statistic(
            s,
            params=RollingStatisticParams(
                statistic="mean", window=window, look_ahead_safe=False,
            ),
        )
        out_safe = rolling_statistic(
            s,
            params=RollingStatisticParams(
                statistic="mean", window=window, look_ahead_safe=True,
            ),
        )
        # safe = nosafe shifted by 1 (the value at t under safe is the
        # value at t-1 under not-safe).
        ref = out_nosafe.payload.shift(1)
        ok = ref.notna()
        np.testing.assert_allclose(
            out_safe.payload.loc[ok].to_numpy(),
            ref.loc[ok].to_numpy(),
            rtol=1e-12, atol=1e-12,
        )

    def test_window_one_mean_equals_input(self):
        """A rolling mean with window=1 IS the input series."""
        dates = pd.bdate_range("2026-01-01", periods=10)
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        s = _series("x", dates=dates, values=values)
        out = rolling_statistic(
            s,
            params=RollingStatisticParams(
                statistic="mean", window=1, look_ahead_safe=False,
            ),
        )
        np.testing.assert_allclose(out.payload.to_numpy(), values)

    @pytest.mark.parametrize(
        "input_units",
        [TimeSeriesUnits.PERCENT, TimeSeriesUnits.BPS, TimeSeriesUnits.Z_SCORE,
         TimeSeriesUnits.RATIO, TimeSeriesUnits.COUNT],
    )
    @pytest.mark.parametrize(
        "statistic", ["mean", "std", "min", "max", "sum"],
    )
    def test_unit_preservation(self, input_units, statistic):
        """OPR6 / ADR 0016 Decision 4: rolling stats PRESERVE the input
        unit — a rolling mean of bps is in bps, a rolling sum of percent
        is in percent.  No coercion."""
        dates = pd.bdate_range("2026-01-01", periods=15)
        values = list(np.linspace(1, 10, 15))
        s = _series("x", dates=dates, values=values, units=input_units)
        out = rolling_statistic(
            s,
            params=RollingStatisticParams(
                statistic=statistic, window=5, look_ahead_safe=False,
            ),
        )
        assert out.units == input_units


# ===========================================================================
# 2. OPR8 — config defaults + schema-default mirror
# ===========================================================================


class TestParamsAndConfig:
    def test_params_none_resolves_from_config(self):
        rng = np.random.RandomState(5)
        values = list(rng.randn(60).cumsum())
        dates = pd.bdate_range("2026-01-01", periods=60)
        s = _series("x", dates=dates, values=values)

        cfg = load_operator_config(CONFIG_PATH)
        explicit = RollingStatisticParams(
            statistic=cfg.default_value("statistic"),
            window=int(cfg.default_value("window")),
            min_periods=None,
            ddof=int(cfg.default_value("ddof")),
            look_ahead_safe=bool(cfg.default_value("look_ahead_safe")),
        )
        out_a = rolling_statistic(s, params=None)
        out_b = rolling_statistic(s, params=explicit)
        assert out_a.lineage.head_hash == out_b.lineage.head_hash

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = RollingStatisticParams()
        assert p.statistic == cfg.default_value("statistic")
        assert p.window == int(cfg.default_value("window"))
        assert p.ddof == int(cfg.default_value("ddof"))
        assert p.look_ahead_safe == bool(cfg.default_value("look_ahead_safe"))
        assert p.min_periods is None and cfg.default_value("min_periods") is None


# ===========================================================================
# 3. OPR13 — typed refusals; OPR8 honest refusal
# ===========================================================================


class TestRefusals:
    def test_non_series_input_raises(self):
        with pytest.raises(RollingStatisticError, match="must be a Series"):
            rolling_statistic({"not": "a series"})  # type: ignore[arg-type]

    def test_empty_series_raises(self):
        s = _series("empty", dates=[], values=[])
        with pytest.raises(RollingStatisticError, match="empty"):
            rolling_statistic(s, params=RollingStatisticParams(window=3))

    def test_window_larger_than_series_raises(self):
        dates = pd.bdate_range("2026-01-01", periods=4)
        s = _series("x", dates=dates, values=[1.0, 2.0, 3.0, 4.0])
        with pytest.raises(RollingStatisticError, match="all-NaN"):
            rolling_statistic(s, params=RollingStatisticParams(window=10))

    def test_std_with_window_one_raises_at_schema(self):
        with pytest.raises(ValueError, match="std' requires window >= 2"):
            RollingStatisticParams(statistic="std", window=1)

    def test_min_periods_greater_than_window_raises_at_schema(self):
        with pytest.raises(ValueError, match="min_periods"):
            RollingStatisticParams(window=5, min_periods=10)

    def test_unbuilt_statistic_refuses_cleanly(self):
        """Defensive path: a statistic bypassing the schema's Literal
        must hit the NotImplementedError branch (OPR8 honest refusal).

        Construct the params via model_construct (bypasses validation)
        so the test can exercise the dispatch guard."""
        bogus = RollingStatisticParams.model_construct(
            statistic="median", window=5, min_periods=None,
            ddof=1, look_ahead_safe=False,
        )
        dates = pd.bdate_range("2026-01-01", periods=20)
        s = _series("x", dates=dates, values=list(np.linspace(0, 5, 20)))
        with pytest.raises(NotImplementedError, match="median"):
            rolling_statistic(s, params=bogus)


# ===========================================================================
# 4. OPR11 — single-input operator carries NO require_matching_* flags
# ===========================================================================


def test_no_require_matching_flags():
    fields = set(RollingStatisticParams.model_fields)
    assert "require_matching_frequency" not in fields
    assert "require_matching_missingness" not in fields


# ===========================================================================
# 5. OPR10 lineage + OPR14 determinism
# ===========================================================================


class TestLineageAndDeterminism:
    def test_lineage_extends_by_one_step(self):
        dates = pd.bdate_range("2026-01-01", periods=20)
        s = _series("x", dates=dates, values=list(np.linspace(0, 5, 20)))
        out = rolling_statistic(s, params=RollingStatisticParams(window=5))
        assert len(out.lineage.steps) == len(s.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "rolling_statistic"
        assert head.version == "1.0.0"
        assert head.auxiliary_lineages == ()
        assert head.params["statistic"] == "mean"
        assert head.params["input_units"] == "percent"

    def test_different_statistics_yield_different_hashes(self):
        """Two different statistics on the same input → different hashes."""
        dates = pd.bdate_range("2026-01-01", periods=15)
        values = list(np.linspace(1, 5, 15) ** 2)
        s = _series("x", dates=dates, values=values)
        out_mean = rolling_statistic(
            s, params=RollingStatisticParams(statistic="mean", window=5),
        )
        out_sum = rolling_statistic(
            s, params=RollingStatisticParams(statistic="sum", window=5),
        )
        assert out_mean.lineage.head_hash != out_sum.lineage.head_hash

    def test_rerun_is_deterministic(self):
        dates = pd.bdate_range("2026-01-01", periods=40)
        values = list(np.cos(np.linspace(0, 8, 40)))
        s = _series("x", dates=dates, values=values)
        out1 = rolling_statistic(s, params=RollingStatisticParams(window=10))
        out2 = rolling_statistic(s, params=RollingStatisticParams(window=10))
        assert out1.lineage.head_hash == out2.lineage.head_hash


# ===========================================================================
# 6. OPR6 — finance-blindness
# ===========================================================================


def test_runs_on_synthetic_zscore_data():
    rng = np.random.RandomState(2026)
    values = list(rng.randn(40).cumsum())
    dates = pd.bdate_range("2026-01-01", periods=40)
    s = _series("synth", dates=dates, values=values, units=TimeSeriesUnits.Z_SCORE)
    out = rolling_statistic(
        s, params=RollingStatisticParams(statistic="std", window=10),
    )
    assert out.units == TimeSeriesUnits.Z_SCORE
    assert out.payload.notna().any()
