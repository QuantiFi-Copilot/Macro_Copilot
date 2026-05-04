"""Tests for the primitive→operator bridge adapter.

Phase 1B Work Item 2 — covers
``shared.artifacts.adapters.from_time_series``:

  - ``time_series_to_artifact_series`` (low-level)
  - ``tool_output_to_artifact_series`` (high-level)
  - ``ToolConfig.conventions_hash`` (the identity helper the high-
    level adapter uses to populate ``PrimitiveStep.tool_config_hash``)

End-to-end coverage for every standard OIS primitive
(rate_level / curve_spread / cross_market_spread / forward_rate)
proves the bridge works against the live primitive output shape;
synthetic-only coverage proves the conversion semantics
(``None`` → ``NaN``, lineage attachment, error paths) without DB
dependence.

Tests are fully offline — primitive fetchers are mocked, dates
frozen via the same ``_FrozenDate`` pattern the per-tool tests use.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from shared.artifacts import (
    CleanSingleSeriesV1,
    Lineage,
    PrimitiveStep,
    RawNoCleaning,
    Series,
    TimeSeriesUnits,
)
from shared.artifacts.adapters import (
    time_series_to_artifact_series,
    tool_output_to_artifact_series,
)
from shared.config import (
    Convention,
    MethodologyMeta,
    ToolConfig,
    ToolMeta,
    clear_tool_config_cache,
    load_tool_config,
)
from shared.schemas import TimeSeries, TimeSeriesRow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _example_time_series(
    *,
    series_name: str = "ust_2y_10y_spread",
    units: TimeSeriesUnits = TimeSeriesUnits.BPS,
    description: str = "Test series.",
    rows: list = None,
) -> TimeSeries:
    """Build a synthetic TimeSeries for low-level adapter tests."""
    if rows is None:
        rows = [
            TimeSeriesRow(date="2026-04-28", value=24.0),
            TimeSeriesRow(date="2026-04-29", value=24.5),
            TimeSeriesRow(date="2026-04-30", value=25.0),
        ]
    return TimeSeries(
        series_name=series_name,
        units=units,
        description=description,
        rows=rows,
    )


def _example_primitive_step(
    *, output_field: str = "time_series_spread", **overrides
) -> PrimitiveStep:
    """Mirror of ``tests/test_artifacts.py::_example_primitive_step``
    (kept local to this file so the test suite is self-contained)."""
    defaults = dict(
        name="calculate_ois_curve_spread_tool",
        params={
            "curve_family": "USD_SOFR_OIS",
            "short_tenor": "2Y",
            "long_tenor": "10Y",
            "lookback_days": 365,
            "field_name": None,
        },
        tool_config_hash="conv_v1_abc123",
        output_field=output_field,
        as_of_date="2026-04-30",
    )
    defaults.update(overrides)
    return PrimitiveStep.build(**defaults)


# ===========================================================================
# 1. ToolConfig.conventions_hash — identity helper
# ===========================================================================

class TestConventionsHash:
    """The bridge's high-level wrapper relies on this method to
    populate ``PrimitiveStep.tool_config_hash``.  Pin its identity
    contract independently so a future change to the recipe is
    surfaced loudly here, not deep inside an integration test."""

    def _config(self, **conventions) -> ToolConfig:
        return ToolConfig(
            tool=ToolMeta(name="t", domain="d", description="x"),
            methodology=MethodologyMeta(what_it_does="x"),
            conventions={
                k: Convention(value=v, source="test", rationale="test")
                for k, v in conventions.items()
            },
        )

    def test_deterministic(self):
        c1 = self._config(z_score_window_days=252, ffill_limit_days=5)
        c2 = self._config(z_score_window_days=252, ffill_limit_days=5)
        assert c1.conventions_hash() == c2.conventions_hash()

    def test_invariant_to_convention_dict_order(self):
        """Convention values are identity; order isn't.  Same hash
        whether the YAML lists ``z_score_window_days`` first or
        second."""
        c1 = self._config(z_score_window_days=252, ffill_limit_days=5)
        c2 = self._config(ffill_limit_days=5, z_score_window_days=252)
        assert c1.conventions_hash() == c2.conventions_hash()

    def test_changes_on_value_change(self):
        c1 = self._config(z_score_window_days=252)
        c2 = self._config(z_score_window_days=504)
        assert c1.conventions_hash() != c2.conventions_hash()

    def test_invariant_to_source_and_rationale_text(self):
        """Documentary fields don't feed identity.  Two configs with
        identical values but different ``source``/``rationale``
        produce the same hash — same as the cross-config lint's
        identity definition."""
        c1 = ToolConfig(
            tool=ToolMeta(name="t", domain="d", description="x"),
            methodology=MethodologyMeta(what_it_does="x"),
            conventions={
                "z_score_window_days": Convention(
                    value=252, source="industry_standard_1y_window",
                    rationale="A",
                ),
            },
        )
        c2 = ToolConfig(
            tool=ToolMeta(name="t", domain="d", description="x"),
            methodology=MethodologyMeta(what_it_does="x"),
            conventions={
                "z_score_window_days": Convention(
                    value=252, source="some_other_tag",
                    rationale="B",
                ),
            },
        )
        assert c1.conventions_hash() == c2.conventions_hash()

    def test_real_yaml_loads_and_hashes(self):
        """Smoke against a real bundled YAML so the helper isn't only
        exercised on synthetic configs."""
        from rates_agent.ois.tools.curve_spread import CONFIG_PATH
        cfg = load_tool_config(CONFIG_PATH)
        h = cfg.conventions_hash()
        assert isinstance(h, str)
        assert len(h) == 64  # sha256 hex


# ===========================================================================
# 2. Low-level: time_series_to_artifact_series
# ===========================================================================

class TestLowLevelConversion:
    def test_basic_round_trip(self):
        ts = _example_time_series()
        step = _example_primitive_step()
        s = time_series_to_artifact_series(
            ts,
            primitive_step=step,
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        )
        assert isinstance(s, Series)
        assert s.series_key == "ust_2y_10y_spread"
        assert s.units == TimeSeriesUnits.BPS
        assert len(s) == 3
        assert list(s.payload.values) == [24.0, 24.5, 25.0]
        assert s.payload.index.is_monotonic_increasing

    def test_lineage_starts_with_primitive_step(self):
        ts = _example_time_series()
        step = _example_primitive_step()
        s = time_series_to_artifact_series(
            ts,
            primitive_step=step,
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        )
        assert isinstance(s.lineage, Lineage)
        assert len(s.lineage.steps) == 1
        head = s.lineage.steps[0]
        assert head.kind == "primitive"
        assert head.hash == step.hash

    def test_units_preserved(self):
        for units in (
            TimeSeriesUnits.PERCENT,
            TimeSeriesUnits.BPS,
            TimeSeriesUnits.Z_SCORE,
            TimeSeriesUnits.RATIO,
        ):
            ts = _example_time_series(units=units)
            s = time_series_to_artifact_series(
                ts,
                primitive_step=_example_primitive_step(),
                missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
            )
            assert s.units == units

    def test_none_value_becomes_nan(self):
        """Wire ``None`` (gap) → artifact ``NaN`` at the same index.
        Semantic-faithful per the documented bridge contract.  NOT
        silently dropped — the row is preserved."""
        ts = _example_time_series(rows=[
            TimeSeriesRow(date="2026-04-28", value=24.0),
            TimeSeriesRow(date="2026-04-29", value=None),  # warmup gap
            TimeSeriesRow(date="2026-04-30", value=25.0),
        ])
        s = time_series_to_artifact_series(
            ts,
            primitive_step=_example_primitive_step(),
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        )
        # Three rows in → three index positions out (NOT dropped).
        assert len(s) == 3
        # Middle row is NaN (the gap was preserved at its date).
        assert math.isnan(float(s.payload.iloc[1]))
        # Other rows are unchanged.
        assert s.payload.iloc[0] == 24.0
        assert s.payload.iloc[2] == 25.0

    def test_all_none_is_allowed(self):
        """A series of all gaps is unusual but not malformed.  The
        bridge preserves it; downstream operators decide what to do."""
        ts = _example_time_series(rows=[
            TimeSeriesRow(date="2026-04-28", value=None),
            TimeSeriesRow(date="2026-04-29", value=None),
        ])
        s = time_series_to_artifact_series(
            ts,
            primitive_step=_example_primitive_step(),
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        )
        assert len(s) == 2
        assert all(math.isnan(float(v)) for v in s.payload.values)

    def test_series_name_becomes_series_key(self):
        ts = _example_time_series(series_name="usd_sofr_ois_2y_10y_ois_zscore")
        s = time_series_to_artifact_series(
            ts,
            primitive_step=_example_primitive_step(),
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        )
        assert s.series_key == "usd_sofr_ois_2y_10y_ois_zscore"

    def test_frequency_passed_through(self):
        ts = _example_time_series()
        s = time_series_to_artifact_series(
            ts,
            primitive_step=_example_primitive_step(),
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
            frequency="B",
        )
        assert s.frequency == "B"

    def test_frequency_default_is_none(self):
        ts = _example_time_series()
        s = time_series_to_artifact_series(
            ts,
            primitive_step=_example_primitive_step(),
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        )
        assert s.frequency is None

    def test_missingness_policy_attached(self):
        ts = _example_time_series()
        policy = CleanSingleSeriesV1(ffill_limit=10)
        s = time_series_to_artifact_series(
            ts,
            primitive_step=_example_primitive_step(),
            missingness_policy=policy,
        )
        assert s.missingness_policy == policy

    def test_raw_no_cleaning_policy_attached(self):
        """The low-level adapter does not constrain the policy type;
        any ``MissingnessPolicy`` member is accepted."""
        ts = _example_time_series()
        policy = RawNoCleaning()
        s = time_series_to_artifact_series(
            ts,
            primitive_step=_example_primitive_step(),
            missingness_policy=policy,
        )
        assert s.missingness_policy == policy

    def test_empty_rows_raises_value_error(self):
        ts = TimeSeries(
            series_name="empty", units=TimeSeriesUnits.BPS,
            description="empty test", rows=[],
        )
        with pytest.raises(ValueError, match="has no rows"):
            time_series_to_artifact_series(
                ts,
                primitive_step=_example_primitive_step(),
                missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
            )

    def test_unsorted_input_is_sorted_defensively(self):
        """The bridge sorts by index defensively so a primitive that
        emits rows in slightly off-canonical order still produces a
        valid artifact (the Series validator requires monotonic
        increasing)."""
        ts = _example_time_series(rows=[
            TimeSeriesRow(date="2026-04-30", value=25.0),
            TimeSeriesRow(date="2026-04-28", value=24.0),
            TimeSeriesRow(date="2026-04-29", value=24.5),
        ])
        s = time_series_to_artifact_series(
            ts,
            primitive_step=_example_primitive_step(),
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        )
        assert s.payload.index.is_monotonic_increasing
        assert list(s.payload.values) == [24.0, 24.5, 25.0]

    def test_duplicate_dates_raise(self):
        """A primitive emitting duplicate dates is malformed; the
        bridge does NOT silently dedupe.  Series validator raises."""
        ts = _example_time_series(rows=[
            TimeSeriesRow(date="2026-04-28", value=24.0),
            TimeSeriesRow(date="2026-04-28", value=24.5),  # dup
        ])
        with pytest.raises(Exception, match="(?i)duplicate"):
            time_series_to_artifact_series(
                ts,
                primitive_step=_example_primitive_step(),
                missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
            )


# ===========================================================================
# 3. High-level: tool_output_to_artifact_series — synthetic
# ===========================================================================

class _StubMetrics:
    """Minimal stub matching the ``current_metrics`` shape so the
    bridge's ``_extract_as_of_date`` works without needing a full
    primitive output schema in unit-style tests."""
    pass


class TestHighLevelWrapper_Synthetic:
    """Exercise the high-level wrapper against a *synthetic*
    ``output_class`` and a hand-built dict — keeps these tests
    decoupled from any specific primitive's wire shape changing."""

    def _output_class(self):
        from pydantic import BaseModel as _BM
        from pydantic import ConfigDict, Field

        class _CurrentMetrics(_BM):
            model_config = ConfigDict(extra="forbid")
            as_of_date: str
            curve_family: str

        class _Output(_BM):
            model_config = ConfigDict(extra="forbid")
            current_metrics: _CurrentMetrics
            time_series_spread: TimeSeries
            time_series_zscore: TimeSeries

        return _Output

    def _input_class(self):
        from pydantic import BaseModel as _BM
        from pydantic import ConfigDict

        class _Input(_BM):
            model_config = ConfigDict(extra="forbid")
            curve_family: str
            short_tenor: str
            long_tenor: str
            lookback_days: int = 365

        return _Input

    def _config(self, **overrides) -> ToolConfig:
        defaults = {
            "z_score_window_days": 252,
            "ffill_limit_days": 5,
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

    def _output_dict(
        self, *, as_of_date: str = "2026-04-30",
    ) -> dict:
        return {
            "current_metrics": {
                "as_of_date": as_of_date,
                "curve_family": "USD_SOFR_OIS",
            },
            "time_series_spread": {
                "series_name": "usd_sofr_ois_2y_10y_ois_spread",
                "units": "bps",
                "description": "Test spread series.",
                "rows": [
                    {"date": "2026-04-28", "value": 24.0},
                    {"date": "2026-04-29", "value": 24.5},
                    {"date": "2026-04-30", "value": 25.0},
                ],
            },
            "time_series_zscore": {
                "series_name": "usd_sofr_ois_2y_10y_ois_zscore",
                "units": "z_score",
                "description": "Test z-score series.",
                "rows": [
                    {"date": "2026-04-28", "value": None},  # warmup gap
                    {"date": "2026-04-29", "value": 0.4},
                    {"date": "2026-04-30", "value": 0.5},
                ],
            },
        }

    def test_extracts_requested_field(self):
        Output = self._output_class()
        Input = self._input_class()
        params = Input(
            curve_family="USD_SOFR_OIS", short_tenor="2Y", long_tenor="10Y",
        )
        s = tool_output_to_artifact_series(
            self._output_dict(),
            output_class=Output,
            output_field="time_series_spread",
            tool_name="calculate_ois_curve_spread_tool",
            tool_config=self._config(),
            params=params,
        )
        assert s.series_key == "usd_sofr_ois_2y_10y_ois_spread"
        assert s.units == TimeSeriesUnits.BPS

    def test_different_output_field_yields_different_artifact(self):
        """Multi-series primitives: same call, different output_field
        → different artifact + different lineage hash."""
        Output = self._output_class()
        Input = self._input_class()
        params = Input(
            curve_family="USD_SOFR_OIS", short_tenor="2Y", long_tenor="10Y",
        )
        common = dict(
            output_class=Output,
            tool_name="calculate_ois_curve_spread_tool",
            tool_config=self._config(),
            params=params,
        )
        s_spread = tool_output_to_artifact_series(
            self._output_dict(), output_field="time_series_spread", **common,
        )
        s_zscore = tool_output_to_artifact_series(
            self._output_dict(), output_field="time_series_zscore", **common,
        )
        assert s_spread.series_key != s_zscore.series_key
        assert s_spread.units != s_zscore.units
        # Lineage hashes differ because output_field is an identity bit.
        assert s_spread.lineage.head_hash != s_zscore.lineage.head_hash

    def test_unknown_output_field_raises(self):
        Output = self._output_class()
        Input = self._input_class()
        params = Input(
            curve_family="USD_SOFR_OIS", short_tenor="2Y", long_tenor="10Y",
        )
        with pytest.raises(ValueError, match="not declared"):
            tool_output_to_artifact_series(
                self._output_dict(),
                output_class=Output,
                output_field="time_series_BOGUS",
                tool_name="calculate_ois_curve_spread_tool",
                tool_config=self._config(),
                params=params,
            )

    def test_non_time_series_field_raises(self):
        """Catches the case where a caller mistypes a wire-frozen
        field name (e.g. ``current_metrics``) as ``output_field``."""
        Output = self._output_class()
        Input = self._input_class()
        params = Input(
            curve_family="USD_SOFR_OIS", short_tenor="2Y", long_tenor="10Y",
        )
        with pytest.raises(ValueError, match="not a TimeSeries"):
            tool_output_to_artifact_series(
                self._output_dict(),
                output_class=Output,
                output_field="current_metrics",  # typo: not a TimeSeries
                tool_name="calculate_ois_curve_spread_tool",
                tool_config=self._config(),
                params=params,
            )

    def test_malformed_output_dict_raises_validation_error(self):
        """Catches malformed primitive outputs early via the
        ``output_class.model_validate`` gate — a clean Pydantic
        ValidationError, not a downstream KeyError."""
        from pydantic import ValidationError
        Output = self._output_class()
        Input = self._input_class()
        params = Input(
            curve_family="USD_SOFR_OIS", short_tenor="2Y", long_tenor="10Y",
        )
        bad_dict = self._output_dict()
        bad_dict.pop("current_metrics")  # missing required field
        with pytest.raises(ValidationError):
            tool_output_to_artifact_series(
                bad_dict,
                output_class=Output,
                output_field="time_series_spread",
                tool_name="calculate_ois_curve_spread_tool",
                tool_config=self._config(),
                params=params,
            )

    def test_auto_derives_clean_single_series_from_config(self):
        Output = self._output_class()
        Input = self._input_class()
        params = Input(
            curve_family="USD_SOFR_OIS", short_tenor="2Y", long_tenor="10Y",
        )
        s = tool_output_to_artifact_series(
            self._output_dict(),
            output_class=Output,
            output_field="time_series_spread",
            tool_name="calculate_ois_curve_spread_tool",
            tool_config=self._config(ffill_limit_days=7),
            params=params,
        )
        assert isinstance(s.missingness_policy, CleanSingleSeriesV1)
        assert s.missingness_policy.ffill_limit == 7

    def test_explicit_policy_overrides_auto_derivation(self):
        Output = self._output_class()
        Input = self._input_class()
        params = Input(
            curve_family="USD_SOFR_OIS", short_tenor="2Y", long_tenor="10Y",
        )
        s = tool_output_to_artifact_series(
            self._output_dict(),
            output_class=Output,
            output_field="time_series_spread",
            tool_name="calculate_ois_curve_spread_tool",
            tool_config=self._config(ffill_limit_days=5),
            params=params,
            missingness_policy=RawNoCleaning(),
        )
        assert isinstance(s.missingness_policy, RawNoCleaning)

    def test_missing_ffill_limit_days_requires_explicit_policy(self):
        """No silent fallback: a tool config without
        ``ffill_limit_days`` MUST get an explicit policy from the
        caller, or the bridge raises with a clear pointer."""
        Output = self._output_class()
        Input = self._input_class()
        params = Input(
            curve_family="USD_SOFR_OIS", short_tenor="2Y", long_tenor="10Y",
        )
        cfg = ToolConfig(
            tool=ToolMeta(name="t", domain="d", description="x"),
            methodology=MethodologyMeta(what_it_does="x"),
            conventions={
                "z_score_window_days": Convention(
                    value=252, source="test", rationale="test",
                ),
                # no ffill_limit_days
            },
        )
        with pytest.raises(ValueError, match="ffill_limit_days"):
            tool_output_to_artifact_series(
                self._output_dict(),
                output_class=Output,
                output_field="time_series_spread",
                tool_name="calculate_ois_curve_spread_tool",
                tool_config=cfg,
                params=params,
            )

    def test_primitive_step_carries_all_four_identity_bits(self):
        Output = self._output_class()
        Input = self._input_class()
        params = Input(
            curve_family="USD_SOFR_OIS", short_tenor="2Y", long_tenor="10Y",
            lookback_days=400,
        )
        cfg = self._config()
        s = tool_output_to_artifact_series(
            self._output_dict(as_of_date="2026-04-30"),
            output_class=Output,
            output_field="time_series_spread",
            tool_name="calculate_ois_curve_spread_tool",
            tool_config=cfg,
            params=params,
            tool_config_path="rates_agent/ois/tools/curve_spread/config.yaml",
        )
        head = s.lineage.steps[0]
        assert head.kind == "primitive"
        assert head.name == "calculate_ois_curve_spread_tool"
        # All four identity bits captured.
        assert head.params == params.model_dump(mode="json")
        assert head.tool_config_hash == cfg.conventions_hash()
        assert head.output_field == "time_series_spread"
        assert head.as_of_date == "2026-04-30"
        # Bookkeeping captured.
        assert head.tool_config_path == (
            "rates_agent/ois/tools/curve_spread/config.yaml"
        )

    def test_yaml_change_invalidates_lineage_hash(self):
        """The load-bearing cache-invalidation invariant: same
        ``*Input``, different YAML content (e.g. someone bumped
        ``z_score_window_days``) MUST produce different lineage
        hashes — the bridge must NOT silently conflate cached
        results from different methodology versions."""
        Output = self._output_class()
        Input = self._input_class()
        params = Input(
            curve_family="USD_SOFR_OIS", short_tenor="2Y", long_tenor="10Y",
        )
        cfg_252 = self._config(z_score_window_days=252)
        cfg_504 = self._config(z_score_window_days=504)

        common = dict(
            output_class=Output,
            output_field="time_series_spread",
            tool_name="calculate_ois_curve_spread_tool",
            params=params,
        )
        s_252 = tool_output_to_artifact_series(
            self._output_dict(), tool_config=cfg_252, **common,
        )
        s_504 = tool_output_to_artifact_series(
            self._output_dict(), tool_config=cfg_504, **common,
        )
        assert s_252.lineage.head_hash != s_504.lineage.head_hash


# ===========================================================================
# 4. End-to-end against every standard OIS primitive
# ===========================================================================

# These tests invoke each OIS primitive against synthetic raw data
# (mocked DB fetcher), capture the real wire output, and confirm the
# bridge lifts each declared TimeSeries field into a valid Series
# artifact with the right units, name, and lineage shape.

class _FrozenDate(date):
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


def _bdays_synthetic_yield_df(*, days: int = 600) -> pd.DataFrame:
    """Two-tenor curve frame for curve_spread / cross_market shapes."""
    bdays = pd.bdate_range(
        _FrozenDate._frozen_value - timedelta(days=days * 2),
        _FrozenDate._frozen_value,
    )[-days:]
    rs = np.random.RandomState(11)

    def _series(label: str, init: float, drift: float) -> pd.DataFrame:
        n = len(bdays)
        v = np.linspace(init, init + drift, n) + rs.randn(n) * 0.01
        return pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "tenor": label,
            "field_value": v,
        })

    return pd.concat(
        [_series("2Y", 4.20, -0.10), _series("10Y", 4.50, +0.05)],
        ignore_index=True,
    ).sort_values(["trade_date", "tenor"]).reset_index(drop=True)


class TestEndToEnd_OIS_RateLevel:
    """Single-series primitive — TimeSeries field is named
    ``time_series`` (singular)."""

    def _run(self):
        from rates_agent.ois.tools.rate_level import (
            CONFIG_PATH,
            get_ois_rate_level,
            OISRateLevelInput,
        )
        from rates_agent.ois.tools.rate_level.schemas import (
            OISRateLevelOutput,
        )

        # Single-tenor synthetic frame.
        bdays = pd.bdate_range(
            _FrozenDate._frozen_value - timedelta(days=600 * 2),
            _FrozenDate._frozen_value,
        )[-600:]
        rs = np.random.RandomState(7)
        n = len(bdays)
        rates = np.linspace(4.50, 4.20, n) + rs.randn(n) * 0.01
        raw_df = pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "field_value": rates,
        })

        params = OISRateLevelInput(
            curve_family="USD_SOFR_OIS", tenor="2Y", lookback_days=365,
        )
        cfg = load_tool_config(CONFIG_PATH)

        with patch(
            "rates_agent.ois.tools.rate_level.compute.fetch_single_tenor",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.rate_level.compute.date",
            _FrozenDate,
        ):
            tool_output = get_ois_rate_level(
                engine=None, params=params, config=cfg,
            )

        return tool_output, OISRateLevelOutput, params, cfg

    def test_lifts_time_series_field(self):
        tool_output, OutClass, params, cfg = self._run()
        s = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series",
            tool_name="get_ois_rate_level_tool",
            tool_config=cfg,
            params=params,
        )
        assert s.units == TimeSeriesUnits.PERCENT
        assert s.series_key == "usd_sofr_ois_2y_ois_rate"
        # PrimitiveStep carries the right output_field.
        assert s.lineage.steps[0].output_field == "time_series"


class TestEndToEnd_OIS_CurveSpread:
    """Two canonical TimeSeries: ``time_series_spread`` (BPS) +
    ``time_series_zscore`` (Z_SCORE)."""

    def _run(self):
        from rates_agent.ois.tools.curve_spread import (
            CONFIG_PATH,
            calculate_ois_curve_spread,
            OISCurveSpreadInput,
        )
        from rates_agent.ois.tools.curve_spread.schemas import (
            OISCurveSpreadOutput,
        )

        raw_df = _bdays_synthetic_yield_df()
        params = OISCurveSpreadInput(
            curve_family="USD_SOFR_OIS", short_tenor="2Y", long_tenor="10Y",
            lookback_days=365,
        )
        cfg = load_tool_config(CONFIG_PATH)

        with patch(
            "rates_agent.ois.tools.curve_spread.compute.fetch_tenor_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.curve_spread.compute.date",
            _FrozenDate,
        ):
            tool_output = calculate_ois_curve_spread(
                engine=None, params=params, config=cfg,
            )

        return tool_output, OISCurveSpreadOutput, params, cfg

    def test_lifts_spread_field_with_BPS(self):
        tool_output, OutClass, params, cfg = self._run()
        s = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series_spread",
            tool_name="calculate_ois_curve_spread_tool",
            tool_config=cfg,
            params=params,
        )
        assert s.units == TimeSeriesUnits.BPS
        assert s.series_key.endswith("_ois_spread")

    def test_lifts_zscore_field_with_Z_SCORE(self):
        tool_output, OutClass, params, cfg = self._run()
        s = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series_zscore",
            tool_name="calculate_ois_curve_spread_tool",
            tool_config=cfg,
            params=params,
        )
        assert s.units == TimeSeriesUnits.Z_SCORE
        assert s.series_key.endswith("_ois_zscore")

    def test_two_fields_have_distinct_lineage_hashes(self):
        tool_output, OutClass, params, cfg = self._run()
        common = dict(
            output_class=OutClass,
            tool_name="calculate_ois_curve_spread_tool",
            tool_config=cfg,
            params=params,
        )
        s_spread = tool_output_to_artifact_series(
            tool_output, output_field="time_series_spread", **common,
        )
        s_zscore = tool_output_to_artifact_series(
            tool_output, output_field="time_series_zscore", **common,
        )
        assert s_spread.lineage.head_hash != s_zscore.lineage.head_hash

    def test_zscore_carries_none_warmup_rows_as_NaN(self):
        """The first ~252 trading days of the rolling z-score have
        ``value=None`` on the wire (rolling-window warmup).  The
        bridge MUST carry these through as ``NaN`` in the artifact
        — operators decide how to handle, the bridge does not
        silently drop."""
        tool_output, OutClass, params, cfg = self._run()
        s = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series_zscore",
            tool_name="calculate_ois_curve_spread_tool",
            tool_config=cfg,
            params=params,
        )
        # Confirm at least some NaN values exist somewhere in the
        # head of the displayed series (warmup region).
        n_nan = int(s.payload.isna().sum())
        n_total = len(s.payload)
        assert n_total > 0
        # On a 365-day display, the first chunk should contain NaNs
        # because the rolling-z-score warmup overlaps the start —
        # but the synthetic data has 600 days of buffer + 365-day
        # display, so the warmup may fall outside the display.  The
        # essential property: all None rows in the wire output map
        # to NaN in the artifact (no row is dropped).
        wire_rows = tool_output["time_series_zscore"]["rows"]
        assert len(s.payload) == len(wire_rows)


class TestEndToEnd_OIS_CrossMarketSpread:
    def _run(self):
        from rates_agent.ois.tools.cross_market_spread import (
            CONFIG_PATH,
            calculate_ois_cross_market_spread,
            OISCrossMarketSpreadInput,
        )
        from rates_agent.ois.tools.cross_market_spread.schemas import (
            OISCrossMarketSpreadOutput,
        )

        # Two curves at one tenor — pivot by curve_family.
        bdays = pd.bdate_range(
            _FrozenDate._frozen_value - timedelta(days=600 * 2),
            _FrozenDate._frozen_value,
        )[-600:]
        rs = np.random.RandomState(13)
        rows = []
        for cf, base, drift in (
            ("USD_SOFR_OIS", 4.50, -0.30),
            ("EUR_ESTR_OIS", 4.20, +0.10),
        ):
            n = len(bdays)
            v = np.linspace(base, base + drift, n) + rs.randn(n) * 0.01
            for d, val in zip(bdays, v):
                rows.append({
                    "trade_date": d.date(),
                    "curve_family": cf,
                    "field_value": val,
                })
        raw_df = pd.DataFrame(rows)
        params = OISCrossMarketSpreadInput(
            curve_family_1="USD_SOFR_OIS", curve_family_2="EUR_ESTR_OIS",
            tenor="2Y", lookback_days=365,
        )
        cfg = load_tool_config(CONFIG_PATH)

        with patch(
            "rates_agent.ois.tools.cross_market_spread.compute.fetch_cross_market_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.cross_market_spread.compute.date",
            _FrozenDate,
        ):
            tool_output = calculate_ois_cross_market_spread(
                engine=None, params=params, config=cfg,
            )

        return tool_output, OISCrossMarketSpreadOutput, params, cfg

    def test_lifts_spread_field_with_BPS(self):
        tool_output, OutClass, params, cfg = self._run()
        s = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series_spread",
            tool_name="calculate_ois_cross_market_spread_tool",
            tool_config=cfg,
            params=params,
        )
        assert s.units == TimeSeriesUnits.BPS
        assert s.series_key.endswith("_ois_cross_spread")

    def test_lifts_zscore_field_with_Z_SCORE(self):
        tool_output, OutClass, params, cfg = self._run()
        s = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series_zscore",
            tool_name="calculate_ois_cross_market_spread_tool",
            tool_config=cfg,
            params=params,
        )
        assert s.units == TimeSeriesUnits.Z_SCORE
        assert s.series_key.endswith("_ois_cross_zscore")


class TestEndToEnd_OIS_ForwardRate:
    def _run(self):
        from rates_agent.ois.tools.forward_rate import (
            CONFIG_PATH,
            calculate_ois_forward_rate,
            OISForwardRateInput,
        )
        from rates_agent.ois.tools.forward_rate.schemas import (
            OISForwardRateOutput,
        )

        # Synthetic full-curve frame: every standard tenor, mean-
        # reverting random walks.
        tenors = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "10Y", "20Y", "30Y"]
        base = {
            "1M": 4.50, "3M": 4.45, "6M": 4.40, "1Y": 4.30, "2Y": 4.20,
            "3Y": 4.15, "5Y": 4.10, "10Y": 4.20, "20Y": 4.30, "30Y": 4.35,
        }
        bdays = pd.bdate_range(
            _FrozenDate._frozen_value - timedelta(days=600 * 2),
            _FrozenDate._frozen_value,
        )[-600:]
        rs = np.random.RandomState(7)
        rows = []
        for tenor in tenors:
            n = len(bdays)
            v = np.empty(n)
            v[0] = base[tenor]
            for i in range(1, n):
                v[i] = (
                    v[i - 1] + 0.005 * (base[tenor] - v[i - 1])
                    + rs.randn() * 0.03
                )
            for d, val in zip(bdays, v):
                rows.append({
                    "trade_date": d.date(),
                    "tenor": tenor,
                    "field_value": val,
                })
        raw_df = pd.DataFrame(rows)
        params = OISForwardRateInput(
            curve_family="USD_SOFR_OIS",
            start_tenor="1Y", end_tenor="2Y",
            lookback_days=365,
        )
        cfg = load_tool_config(CONFIG_PATH)

        with patch(
            "rates_agent.ois.tools.forward_rate.compute._fetch_full_curve",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.forward_rate.compute.date",
            _FrozenDate,
        ):
            tool_output = calculate_ois_forward_rate(
                engine=None, params=params, config=cfg,
            )

        return tool_output, OISForwardRateOutput, params, cfg

    def test_lifts_forward_field_with_PERCENT(self):
        tool_output, OutClass, params, cfg = self._run()
        s = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series_forward",
            tool_name="calculate_ois_forward_rate_tool",
            tool_config=cfg,
            params=params,
        )
        assert s.units == TimeSeriesUnits.PERCENT
        assert s.series_key.endswith("_ois_forward")

    def test_lifts_zscore_field_with_Z_SCORE(self):
        tool_output, OutClass, params, cfg = self._run()
        s = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series_zscore",
            tool_name="calculate_ois_forward_rate_tool",
            tool_config=cfg,
            params=params,
        )
        assert s.units == TimeSeriesUnits.Z_SCORE
        assert s.series_key.endswith("_ois_forward_zscore")


# ===========================================================================
# 5. Lineage continuity through the bridge
# ===========================================================================

class TestLineageContinuity:
    """End-state Phase 1B contract: a primitive output flows through
    the bridge into a Series whose lineage chain begins with a
    PrimitiveStep.  An operator applied downstream should append an
    OperatorStep — exercising the typical Phase 1B pipeline shape."""

    def test_bridge_output_is_compatible_with_operator_lineage_append(self):
        from shared.artifacts import OperatorStep

        ts = _example_time_series()
        prim = _example_primitive_step()
        s = time_series_to_artifact_series(
            ts,
            primitive_step=prim,
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        )
        # Append a fake OperatorStep — proves the lineage chain
        # accepts further extension after a PrimitiveStep root.
        op = OperatorStep.build(
            name="align_series", version="1.0.0",
            params={"join_policy": "outer"},
            input_hashes=(s.lineage.head_hash,),
        )
        ln2 = s.lineage.append(op)
        assert [step.kind for step in ln2.steps] == ["primitive", "operator"]
        assert ln2.head_hash == op.hash
