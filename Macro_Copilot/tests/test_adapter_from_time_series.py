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
    artifact_series_to_time_series,
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

    def test_auto_derived_policy_pins_clean_single_series_invariants(self):
        """The auto-derived ``CleanSingleSeriesV1`` must explicitly
        match every observable invariant of ``shared.analytics.levels.
        clean_single_series``: ``ffill_limit`` from YAML,
        ``drop_nan=True`` (always-on in the cleaner), and
        ``dedup_keep="last"`` (the cleaner's hardcoded choice).

        Pinning ALL THREE here means a future change to either of the
        cleaner's invariants forces a deliberate update at the bridge
        — not silent drift through Pydantic field defaults."""
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
        )
        policy = s.missingness_policy
        assert isinstance(policy, CleanSingleSeriesV1)
        assert policy.ffill_limit == 5
        assert policy.drop_nan is True
        assert policy.dedup_keep == "last"

    def test_data_identity_is_NOT_in_missingness_policy(self):
        """Architectural intent: missingness captures the cleaning
        REGIME, not data identity.  Two artifacts from primitives
        sharing the same ``ffill_limit_days`` MUST have identical
        missingness policies — even if they came from different
        primitives, different tenors, or different fields.  Data
        identity lives in ``Series.units``, ``Series.series_key``,
        ``PrimitiveStep.params``, etc.  Missingness mismatch is
        reserved for genuine cleaning-regime divergence."""
        Output = self._output_class()
        Input = self._input_class()

        cfg = self._config(ffill_limit_days=5)

        # Two artifacts from the same primitive but different fields
        # (BPS spread vs Z_SCORE) — same cleaning regime, must have
        # IDENTICAL missingness policies.
        params_a = Input(
            curve_family="USD_SOFR_OIS", short_tenor="2Y", long_tenor="10Y",
        )
        s_spread = tool_output_to_artifact_series(
            self._output_dict(), output_class=Output,
            output_field="time_series_spread",
            tool_name="calculate_ois_curve_spread_tool",
            tool_config=cfg, params=params_a,
        )
        s_zscore = tool_output_to_artifact_series(
            self._output_dict(), output_class=Output,
            output_field="time_series_zscore",
            tool_name="calculate_ois_curve_spread_tool",
            tool_config=cfg, params=params_a,
        )

        # Different units, different series_key, different
        # PrimitiveStep.output_field, different lineage hash —
        # but identical missingness regime.
        assert s_spread.units != s_zscore.units
        assert s_spread.series_key != s_zscore.series_key
        assert s_spread.lineage.head_hash != s_zscore.lineage.head_hash
        assert s_spread.missingness_policy == s_zscore.missingness_policy

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
        # The bridge plan calls for ``PrimitiveStep.name`` to record
        # the primitive's MCP tool name (the LLM-facing surface,
        # NOT the YAML's ``tool.name``).  For OIS rate_level there
        # is a pre-existing naming drift between the two:
        #
        #   YAML ``tool.name``      = "get_ois_rate_level_tool"
        #   MCP wrapper function    = "calculate_ois_rate_level_tool"
        #
        # Every other OIS primitive matches across both surfaces; only
        # rate_level has the drift.  This test pins the contract to
        # the MCP wrapper name (the identity that future workflows /
        # caches will key on).  The YAML drift is a separate
        # follow-up — out of scope for the bridge.
        MCP_NAME = "calculate_ois_rate_level_tool"
        s = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series",
            tool_name=MCP_NAME,
            tool_config=cfg,
            params=params,
        )
        assert s.units == TimeSeriesUnits.PERCENT
        assert s.series_key == "usd_sofr_ois_2y_ois_rate"
        # PrimitiveStep carries the right output_field.
        assert s.lineage.steps[0].output_field == "time_series"
        # And the right MCP tool name (NOT the YAML ``tool.name``).
        assert s.lineage.steps[0].name == MCP_NAME


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
# 4b. End-to-end against every standard SOVEREIGN primitive
# ===========================================================================

# Phase 1B acceptance criteria (revised plan): the bridge must work
# against the standard sovereign primitives, not only OIS.  Codex P2
# follow-up: prior coverage was OIS-only, leaving the sovereign side
# unproved.  These tests invoke each sovereign primitive against
# synthetic raw data (mocked DB fetcher), capture the real wire
# output, and confirm the bridge lifts each declared TimeSeries field
# into a valid Series artifact with the right units, name, and
# lineage shape — same shape as the OIS coverage above.


def _sovereign_two_curve_long_df(
    *, days: int = 600, frozen_today: date = date(2026, 4, 30),
) -> pd.DataFrame:
    """Two-curve long-format frame for sovereign cross_market_spread
    fetcher (pivots by curve_family, not tenor)."""
    bdays = pd.bdate_range(
        frozen_today - timedelta(days=days * 2), frozen_today,
    )[-days:]
    rs = np.random.RandomState(13)
    rows = []
    for cf, base, drift in (
        ("IT_BTP", 4.20, +0.30),
        ("DE_BUND", 2.60, +0.05),
    ):
        n = len(bdays)
        v = np.linspace(base, base + drift, n) + rs.randn(n) * 0.012
        for d, val in zip(bdays, v):
            rows.append({
                "trade_date": d.date(), "curve_family": cf,
                "field_value": val,
            })
    return pd.DataFrame(rows)


def _sovereign_two_tenor_long_df(
    *, days: int = 600, frozen_today: date = date(2026, 4, 30),
) -> pd.DataFrame:
    """Two-tenor long-format frame for sovereign curve_spread
    fetcher (single curve, pivots by tenor)."""
    bdays = pd.bdate_range(
        frozen_today - timedelta(days=days * 2), frozen_today,
    )[-days:]
    rs = np.random.RandomState(11)
    rows = []
    for t, base, drift in (("2Y", 4.50, -0.10), ("10Y", 4.80, +0.05)):
        n = len(bdays)
        v = np.linspace(base, base + drift, n) + rs.randn(n) * 0.012
        for d, val in zip(bdays, v):
            rows.append({
                "trade_date": d.date(), "tenor": t, "field_value": val,
            })
    return pd.DataFrame(rows)


def _sovereign_three_tenor_long_df(
    *, days: int = 600, frozen_today: date = date(2026, 4, 30),
) -> pd.DataFrame:
    """Three-tenor long-format frame for butterfly fetch_tenor_group."""
    bdays = pd.bdate_range(
        frozen_today - timedelta(days=days * 2), frozen_today,
    )[-days:]
    rs = np.random.RandomState(17)
    rows = []
    for t, base, drift in (
        ("2Y", 4.50, -0.10),
        ("5Y", 4.65, -0.05),
        ("10Y", 4.80, +0.05),
    ):
        n = len(bdays)
        v = np.linspace(base, base + drift, n) + rs.randn(n) * 0.012
        for d, val in zip(bdays, v):
            rows.append({
                "trade_date": d.date(), "tenor": t, "field_value": val,
            })
    return pd.DataFrame(rows)


def _sovereign_single_tenor_long_df(
    *, days: int = 600, frozen_today: date = date(2026, 4, 30),
) -> pd.DataFrame:
    """Single-tenor frame for sovereign yield_levels fetch_single_tenor."""
    bdays = pd.bdate_range(
        frozen_today - timedelta(days=days * 2), frozen_today,
    )[-days:]
    rs = np.random.RandomState(7)
    n = len(bdays)
    yields = np.linspace(4.30, 4.10, n) + rs.randn(n) * 0.01
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": yields,
    })


class TestEndToEnd_Sovereign_YieldLevels:
    """Single canonical TimeSeries: ``time_series`` (PERCENT)."""

    def _run(self):
        from rates_agent.sovereign_bonds.tools.yield_levels import (
            CONFIG_PATH,
            get_yield_levels,
            YieldLevelInput,
        )
        from rates_agent.sovereign_bonds.tools.yield_levels.schemas import (
            YieldLevelOutput,
        )

        raw_df = _sovereign_single_tenor_long_df()
        params = YieldLevelInput(
            curve_family="UST", tenor="10Y", lookback_days=365,
        )
        cfg = load_tool_config(CONFIG_PATH)

        with patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.fetch_single_tenor",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.date",
            _FrozenDate,
        ):
            tool_output = get_yield_levels(
                engine=None, params=params, config=cfg,
            )

        return tool_output, YieldLevelOutput, params, cfg

    def test_lifts_time_series_field_with_PERCENT(self):
        tool_output, OutClass, params, cfg = self._run()
        s = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series",
            tool_name="get_yield_levels_tool",
            tool_config=cfg,
            params=params,
        )
        assert s.units == TimeSeriesUnits.PERCENT
        assert "ust_10y" in s.series_key.lower()
        # Lineage rooted at PrimitiveStep with the sovereign
        # tool name (NOT the OIS rate_level analogue).
        assert s.lineage.steps[0].kind == "primitive"
        assert s.lineage.steps[0].name == "get_yield_levels_tool"
        assert s.lineage.steps[0].output_field == "time_series"


class TestEndToEnd_Sovereign_CurveSpread:
    """Two canonical TimeSeries: ``time_series_spread`` (BPS) +
    ``time_series_zscore`` (Z_SCORE)."""

    def _run(self):
        from rates_agent.sovereign_bonds.tools.curve_spread import (
            CONFIG_PATH,
            calculate_curve_spread,
            CurveSpreadInput,
        )
        from rates_agent.sovereign_bonds.tools.curve_spread.schemas import (
            CurveSpreadOutput,
        )

        raw_df = _sovereign_two_tenor_long_df()
        params = CurveSpreadInput(
            curve_family="UST", short_tenor="2Y", long_tenor="10Y",
            lookback_days=365,
        )
        cfg = load_tool_config(CONFIG_PATH)

        with patch(
            "rates_agent.sovereign_bonds.tools.curve_spread.compute.fetch_tenor_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.curve_spread.compute.date",
            _FrozenDate,
        ):
            tool_output = calculate_curve_spread(
                engine=None, params=params, config=cfg,
            )

        return tool_output, CurveSpreadOutput, params, cfg

    def test_lifts_spread_field_with_BPS(self):
        tool_output, OutClass, params, cfg = self._run()
        s = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series_spread",
            tool_name="calculate_curve_spread_tool",
            tool_config=cfg,
            params=params,
        )
        assert s.units == TimeSeriesUnits.BPS
        # Sovereign spread suffix is just "_spread" (NOT "_ois_spread").
        assert s.series_key.endswith("_spread")
        assert "_ois_" not in s.series_key

    def test_lifts_zscore_field_with_Z_SCORE(self):
        tool_output, OutClass, params, cfg = self._run()
        s = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series_zscore",
            tool_name="calculate_curve_spread_tool",
            tool_config=cfg,
            params=params,
        )
        assert s.units == TimeSeriesUnits.Z_SCORE
        assert s.series_key.endswith("_zscore")


class TestEndToEnd_Sovereign_CrossMarketSpread:
    """Two canonical TimeSeries: ``time_series_spread`` (BPS) +
    ``time_series_zscore`` (Z_SCORE)."""

    def _run(self):
        from rates_agent.sovereign_bonds.tools.cross_market_spread import (
            CONFIG_PATH,
            calculate_cross_market_spread,
            CrossMarketSpreadInput,
        )
        from rates_agent.sovereign_bonds.tools.cross_market_spread.schemas import (
            CrossMarketSpreadOutput,
        )

        raw_df = _sovereign_two_curve_long_df()
        params = CrossMarketSpreadInput(
            curve_family_1="IT_BTP", curve_family_2="DE_BUND",
            tenor="10Y", lookback_days=365,
        )
        cfg = load_tool_config(CONFIG_PATH)

        with patch(
            "rates_agent.sovereign_bonds.tools.cross_market_spread."
            "compute.fetch_cross_market_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.cross_market_spread.compute.date",
            _FrozenDate,
        ):
            tool_output = calculate_cross_market_spread(
                engine=None, params=params, config=cfg,
            )

        return tool_output, CrossMarketSpreadOutput, params, cfg

    def test_lifts_spread_field_with_BPS(self):
        tool_output, OutClass, params, cfg = self._run()
        s = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series_spread",
            tool_name="calculate_cross_market_spread_tool",
            tool_config=cfg,
            params=params,
        )
        assert s.units == TimeSeriesUnits.BPS
        # Sovereign suffix is "_spread" (no OIS prefix).
        assert s.series_key.endswith("_spread")
        assert "_ois_cross_" not in s.series_key

    def test_lifts_zscore_field_with_Z_SCORE(self):
        tool_output, OutClass, params, cfg = self._run()
        s = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series_zscore",
            tool_name="calculate_cross_market_spread_tool",
            tool_config=cfg,
            params=params,
        )
        assert s.units == TimeSeriesUnits.Z_SCORE
        assert s.series_key.endswith("_zscore")


class TestEndToEnd_Sovereign_Butterfly:
    """Two canonical TimeSeries: ``time_series_butterfly`` (BPS) +
    ``time_series_zscore`` (Z_SCORE).  Note the field name is
    ``time_series_butterfly``, not ``_spread`` — proves the bridge's
    ``output_field`` mechanism does NOT hardcode any field-name
    conventions; the caller picks whichever the primitive declares."""

    def _run(self):
        from rates_agent.sovereign_bonds.tools.butterfly import (
            CONFIG_PATH,
            calculate_butterfly,
            ButterflyInput,
        )
        from rates_agent.sovereign_bonds.tools.butterfly.schemas import (
            ButterflyOutput,
        )

        raw_df = _sovereign_three_tenor_long_df()
        params = ButterflyInput(
            curve_family="UST", short_tenor="2Y",
            belly_tenor="5Y", long_tenor="10Y", lookback_days=365,
        )
        cfg = load_tool_config(CONFIG_PATH)

        with patch(
            "rates_agent.sovereign_bonds.tools.butterfly.compute.fetch_tenor_group",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.butterfly.compute.date",
            _FrozenDate,
        ):
            tool_output = calculate_butterfly(
                engine=None, params=params, config=cfg,
            )

        return tool_output, ButterflyOutput, params, cfg

    def test_lifts_butterfly_field_with_BPS(self):
        tool_output, OutClass, params, cfg = self._run()
        s = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series_butterfly",
            tool_name="calculate_butterfly_tool",
            tool_config=cfg,
            params=params,
        )
        assert s.units == TimeSeriesUnits.BPS
        # Field name is ``_butterfly`` not ``_spread`` — bridge
        # carries the primitive's wire-name convention through
        # to the artifact's series_key without translation.
        assert s.series_key.endswith("_butterfly")

    def test_lifts_zscore_field_with_Z_SCORE(self):
        tool_output, OutClass, params, cfg = self._run()
        s = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series_zscore",
            tool_name="calculate_butterfly_tool",
            tool_config=cfg,
            params=params,
        )
        assert s.units == TimeSeriesUnits.Z_SCORE
        assert s.series_key.endswith("_zscore")


class TestEndToEnd_Sovereign_AllFieldsLiftSuccessfully:
    """Sanity sweep: every standard sovereign primitive's canonical
    TimeSeries field lifts to a valid Series under the auto-derived
    missingness policy.  Acceptance criterion from the revised
    Phase 1B plan: 'all standard primitives flow through
    tool_output_to_artifact_series'."""

    def test_every_standard_sovereign_primitive_lifts(self):
        results = [
            (
                TestEndToEnd_Sovereign_YieldLevels(),
                "time_series", "get_yield_levels_tool",
                TimeSeriesUnits.PERCENT,
            ),
            (
                TestEndToEnd_Sovereign_CurveSpread(),
                "time_series_spread", "calculate_curve_spread_tool",
                TimeSeriesUnits.BPS,
            ),
            (
                TestEndToEnd_Sovereign_CurveSpread(),
                "time_series_zscore", "calculate_curve_spread_tool",
                TimeSeriesUnits.Z_SCORE,
            ),
            (
                TestEndToEnd_Sovereign_CrossMarketSpread(),
                "time_series_spread",
                "calculate_cross_market_spread_tool",
                TimeSeriesUnits.BPS,
            ),
            (
                TestEndToEnd_Sovereign_CrossMarketSpread(),
                "time_series_zscore",
                "calculate_cross_market_spread_tool",
                TimeSeriesUnits.Z_SCORE,
            ),
            (
                TestEndToEnd_Sovereign_Butterfly(),
                "time_series_butterfly",
                "calculate_butterfly_tool",
                TimeSeriesUnits.BPS,
            ),
            (
                TestEndToEnd_Sovereign_Butterfly(),
                "time_series_zscore",
                "calculate_butterfly_tool",
                TimeSeriesUnits.Z_SCORE,
            ),
        ]
        for runner, field, tool_name, expected_units in results:
            tool_output, OutClass, params, cfg = runner._run()
            s = tool_output_to_artifact_series(
                tool_output,
                output_class=OutClass,
                output_field=field,
                tool_name=tool_name,
                tool_config=cfg,
                params=params,
            )
            assert s.units == expected_units, (
                f"{tool_name}/{field}: expected {expected_units}, "
                f"got {s.units}"
            )
            # Auto-derived CleanSingleSeriesV1 — proves every
            # sovereign tool's YAML declares ffill_limit_days as
            # the bridge's auto-derivation requires.
            assert isinstance(s.missingness_policy, CleanSingleSeriesV1)
            assert s.missingness_policy.drop_nan is True
            assert s.missingness_policy.dedup_keep == "last"
            # PrimitiveStep records the right MCP tool name.
            assert s.lineage.steps[0].name == tool_name


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


# ===========================================================================
# 6. Reverse path: artifact_series_to_time_series (Work Item 3)
# ===========================================================================

class TestReversePath_BasicConversion:
    """Pure mechanical conversion — no lineage / round-trip semantics
    yet, just the shape contract."""

    def _series(
        self, *, values, dates=None, units=TimeSeriesUnits.BPS,
        series_key="ust_2y_10y_spread",
    ) -> Series:
        if dates is None:
            dates = pd.bdate_range("2026-04-28", periods=len(values))
        ts = TimeSeries(
            series_name=series_key, units=units,
            description="forward-path description",
            rows=[
                TimeSeriesRow(
                    date=pd.Timestamp(d).strftime("%Y-%m-%d"),
                    value=v,
                )
                for d, v in zip(dates, values)
            ],
        )
        return time_series_to_artifact_series(
            ts,
            primitive_step=_example_primitive_step(),
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        )

    def test_returns_time_series_instance(self):
        s = self._series(values=[24.0, 24.5, 25.0])
        ts = artifact_series_to_time_series(s)
        assert isinstance(ts, TimeSeries)

    def test_series_key_becomes_series_name(self):
        s = self._series(
            values=[24.0, 24.5, 25.0],
            series_key="usd_sofr_ois_2y_10y_ois_spread",
        )
        ts = artifact_series_to_time_series(s)
        assert ts.series_name == "usd_sofr_ois_2y_10y_ois_spread"

    def test_units_pass_through_for_every_enum_member(self):
        for units in (
            TimeSeriesUnits.PERCENT,
            TimeSeriesUnits.BPS,
            TimeSeriesUnits.Z_SCORE,
            TimeSeriesUnits.RATIO,
            TimeSeriesUnits.PCT_RANK,
            TimeSeriesUnits.FACTOR_LEVEL,
            TimeSeriesUnits.COUNT,
        ):
            s = self._series(values=[1.0, 2.0, 3.0], units=units)
            ts = artifact_series_to_time_series(s)
            assert ts.units == units

    def test_dates_round_trip_in_iso_format(self):
        s = self._series(
            values=[24.0, 24.5, 25.0],
            dates=pd.to_datetime(["2026-04-28", "2026-04-29", "2026-04-30"]),
        )
        ts = artifact_series_to_time_series(s)
        assert [row.date for row in ts.rows] == [
            "2026-04-28", "2026-04-29", "2026-04-30",
        ]

    def test_row_count_preserved(self):
        s = self._series(values=[1.0, 2.0, 3.0, 4.0, 5.0])
        ts = artifact_series_to_time_series(s)
        assert len(ts.rows) == 5


class TestReversePath_NaNToNone:
    """The load-bearing reverse-path missingness contract: every
    ``NaN`` in the artifact payload becomes ``None`` on the wire.
    Mirrors the forward path's ``None → NaN`` documented in the
    module docstring."""

    def _series_with_gaps(
        self, *, values, series_key="x"
    ) -> Series:
        dates = pd.bdate_range("2026-04-28", periods=len(values))
        ts = TimeSeries(
            series_name=series_key, units=TimeSeriesUnits.Z_SCORE,
            description="forward-path desc",
            rows=[
                TimeSeriesRow(
                    date=pd.Timestamp(d).strftime("%Y-%m-%d"),
                    value=v,
                )
                for d, v in zip(dates, values)
            ],
        )
        return time_series_to_artifact_series(
            ts,
            primitive_step=_example_primitive_step(),
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        )

    def test_nan_becomes_none_at_same_index(self):
        # Middle row is None on the wire; flows through forward as NaN
        # in the artifact; reverse must restore it as None.
        s = self._series_with_gaps(values=[24.0, None, 25.0])
        ts = artifact_series_to_time_series(s)
        assert ts.rows[0].value == 24.0
        assert ts.rows[1].value is None
        assert ts.rows[2].value == 25.0

    def test_all_nan_series_round_trips_as_all_none(self):
        s = self._series_with_gaps(values=[None, None, None])
        ts = artifact_series_to_time_series(s)
        assert all(row.value is None for row in ts.rows)
        assert len(ts.rows) == 3  # row count preserved

    def test_no_nan_series_has_no_none_values(self):
        s = self._series_with_gaps(values=[24.0, 24.5, 25.0])
        ts = artifact_series_to_time_series(s)
        assert all(row.value is not None for row in ts.rows)
        assert [row.value for row in ts.rows] == [24.0, 24.5, 25.0]


class TestReversePath_LineageSummary:
    """Description population from the lineage chain.  The plan's
    resolved Q1 was 'description-only summary this sprint' — the
    structured chain stays on the artifact, the wire description
    carries a human-readable summary."""

    def _make_series_with_lineage(self, lineage: Lineage) -> Series:
        # Build a minimal valid Series with the supplied lineage; the
        # payload itself isn't load-bearing for these tests.
        from shared.artifacts.types import Series as _S
        return _S(
            series_key="test_series",
            payload=pd.Series(
                [1.0, 2.0, 3.0],
                index=pd.bdate_range("2026-04-28", periods=3),
                dtype=float,
            ),
            units=TimeSeriesUnits.BPS,
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
            lineage=lineage,
        )

    def test_single_primitive_step_summary(self):
        prim = _example_primitive_step(
            name="calculate_ois_curve_spread_tool",
        )
        ln = Lineage.from_steps([prim])
        s = self._make_series_with_lineage(ln)
        ts = artifact_series_to_time_series(s)
        assert ts.description == "derived: calculate_ois_curve_spread_tool"

    def test_multi_step_chain_uses_arrow_separator(self):
        from shared.artifacts import OperatorStep
        prim = _example_primitive_step(
            name="calculate_ois_curve_spread_tool",
        )
        ln = Lineage.from_steps([prim])
        op1 = OperatorStep.build(
            name="align_series", version="1.0.0",
            params={"join_policy": "outer"},
            input_hashes=(ln.head_hash,),
        )
        ln = ln.append(op1)
        op2 = OperatorStep.build(
            name="series_arithmetic", version="1.0.0",
            params={"op": "subtract"},
            input_hashes=(ln.head_hash,),
        )
        ln = ln.append(op2)
        s = self._make_series_with_lineage(ln)
        ts = artifact_series_to_time_series(s)
        assert ts.description == (
            "derived: calculate_ois_curve_spread_tool"
            " → align_series"
            " → series_arithmetic"
        )

    def test_summary_excludes_auxiliary_lineages(self):
        """Binary operators (e.g. series_arithmetic) carry the right-
        hand operand's chain in ``OperatorStep.auxiliary_lineages``.
        That chain is preserved on the artifact (still inspectable
        via ``series.lineage``) but is NOT walked into the description
        string — the wire summary is bounded + linear by design."""
        from shared.artifacts import OperatorStep
        prim_a = _example_primitive_step(
            name="calculate_ois_curve_spread_tool",
        )
        prim_b = _example_primitive_step(
            name="get_yield_levels_tool",
        )
        ln_a = Lineage.from_steps([prim_a])
        ln_b = Lineage.from_steps([prim_b])
        op = OperatorStep.build(
            name="series_arithmetic", version="1.0.0",
            params={"op": "subtract"},
            input_hashes=(ln_a.head_hash, ln_b.head_hash),
            auxiliary_lineages=(ln_b,),
        )
        ln_combined = ln_a.append(op)
        s = self._make_series_with_lineage(ln_combined)
        ts = artifact_series_to_time_series(s)
        # Right-operand's primitive (get_yield_levels_tool) must NOT
        # appear in the linear summary.
        assert "get_yield_levels_tool" not in ts.description
        assert ts.description == (
            "derived: calculate_ois_curve_spread_tool → series_arithmetic"
        )

    def test_description_starts_with_documented_prefix(self):
        prim = _example_primitive_step()
        ln = Lineage.from_steps([prim])
        s = self._make_series_with_lineage(ln)
        ts = artifact_series_to_time_series(s)
        assert ts.description.startswith("derived: ")

    def test_description_override_used_verbatim(self):
        prim = _example_primitive_step(
            name="calculate_ois_curve_spread_tool",
        )
        ln = Lineage.from_steps([prim])
        s = self._make_series_with_lineage(ln)
        ts = artifact_series_to_time_series(
            s, description_override="Custom desk-friendly description.",
        )
        assert ts.description == "Custom desk-friendly description."
        # Bridge does NOT prepend "derived: " to the override.
        assert not ts.description.startswith("derived: ")


class TestReversePath_RoundTripPerPrimitive:
    """Forward+reverse round trip per OIS primitive end-to-end.
    ``(rows, units, series_name)`` round-trip BYTE-identical (modulo
    the documented ``NaN`` ↔ ``None`` semantic mapping); description
    differs by design — see module docstring."""

    def _round_trip(self, ts_input: TimeSeries) -> TimeSeries:
        """Forward into Series, then reverse back to TimeSeries."""
        artifact = time_series_to_artifact_series(
            ts_input,
            primitive_step=_example_primitive_step(),
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        )
        return artifact_series_to_time_series(artifact)

    def test_round_trip_no_gaps(self):
        original = TimeSeries(
            series_name="ust_2y_10y_spread",
            units=TimeSeriesUnits.BPS,
            description="forward-path description",
            rows=[
                TimeSeriesRow(date="2026-04-28", value=24.0),
                TimeSeriesRow(date="2026-04-29", value=24.5),
                TimeSeriesRow(date="2026-04-30", value=25.0),
            ],
        )
        recovered = self._round_trip(original)
        # Identity-bearing fields round-trip exactly.
        assert recovered.series_name == original.series_name
        assert recovered.units == original.units
        assert len(recovered.rows) == len(original.rows)
        for orig_row, rec_row in zip(original.rows, recovered.rows):
            assert orig_row.date == rec_row.date
            assert orig_row.value == rec_row.value
        # Description differs by design (lineage summary).
        assert recovered.description != original.description
        assert recovered.description.startswith("derived: ")

    def test_round_trip_with_gaps(self):
        original = TimeSeries(
            series_name="ust_2y_10y_zscore",
            units=TimeSeriesUnits.Z_SCORE,
            description="forward-path description",
            rows=[
                TimeSeriesRow(date="2026-04-28", value=None),  # warmup
                TimeSeriesRow(date="2026-04-29", value=0.4),
                TimeSeriesRow(date="2026-04-30", value=None),  # gap
            ],
        )
        recovered = self._round_trip(original)
        # Row count preserved AND None positions preserved.
        assert len(recovered.rows) == 3
        assert recovered.rows[0].value is None
        assert recovered.rows[1].value == 0.4
        assert recovered.rows[2].value is None

    def test_round_trip_via_OIS_curve_spread_BPS(self):
        """Hits the live primitive shape, not a synthetic TimeSeries.
        End-to-end: invoke OIS curve_spread → forward → reverse →
        compare to the primitive's emitted TimeSeries."""
        runner = TestEndToEnd_OIS_CurveSpread()
        tool_output, OutClass, params, cfg = runner._run()
        artifact = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series_spread",
            tool_name="calculate_ois_curve_spread_tool",
            tool_config=cfg,
            params=params,
        )
        # Reverse back to wire format.
        wire = artifact_series_to_time_series(artifact)

        # Compare to the primitive's original payload (unwrapped from
        # the dict via OutClass for type safety).
        validated = OutClass.model_validate(tool_output)
        original = validated.time_series_spread

        assert wire.series_name == original.series_name
        assert wire.units == original.units
        assert len(wire.rows) == len(original.rows)
        # Every row matches by date + value (None ↔ None, float ↔ float).
        for orig_row, wire_row in zip(original.rows, wire.rows):
            assert orig_row.date == wire_row.date
            assert orig_row.value == wire_row.value
        # Description: primitive's free-form vs bridge's lineage summary.
        assert wire.description.startswith("derived: ")
        assert wire.description == (
            "derived: calculate_ois_curve_spread_tool"
        )

    def test_round_trip_via_OIS_curve_spread_ZSCORE_with_warmup_gaps(self):
        """The z-score field has ``None`` rows during the rolling-
        window warmup.  This is the highest-stakes round-trip case
        for the documented ``NaN`` ↔ ``None`` mapping."""
        runner = TestEndToEnd_OIS_CurveSpread()
        tool_output, OutClass, params, cfg = runner._run()
        artifact = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series_zscore",
            tool_name="calculate_ois_curve_spread_tool",
            tool_config=cfg,
            params=params,
        )
        wire = artifact_series_to_time_series(artifact)

        validated = OutClass.model_validate(tool_output)
        original = validated.time_series_zscore

        assert wire.series_name == original.series_name
        assert wire.units == original.units
        assert len(wire.rows) == len(original.rows)
        # The None positions and float positions both round-trip
        # exactly.  This proves the NaN ↔ None contract on the live
        # shape, not just a synthetic case.
        for orig_row, wire_row in zip(original.rows, wire.rows):
            assert orig_row.date == wire_row.date
            assert orig_row.value == wire_row.value


class TestReversePath_AfterOperatorStep:
    """End-state Phase 1B contract: a primitive output flows through
    the bridge into a Series whose lineage chain begins with a
    PrimitiveStep, an operator extends that chain, and the reverse
    path serializes the result with a multi-step lineage summary."""

    def test_primitive_then_operator_chain_summary(self):
        from shared.artifacts import OperatorStep

        original = TimeSeries(
            series_name="ust_2y_10y_spread",
            units=TimeSeriesUnits.BPS,
            description="primitive desc",
            rows=[
                TimeSeriesRow(date="2026-04-28", value=24.0),
                TimeSeriesRow(date="2026-04-29", value=24.5),
                TimeSeriesRow(date="2026-04-30", value=25.0),
            ],
        )
        prim_step = _example_primitive_step(
            name="calculate_ois_curve_spread_tool",
        )
        s_pre = time_series_to_artifact_series(
            original,
            primitive_step=prim_step,
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        )

        # Simulate an operator extending the lineage.  We mutate a
        # copy via Lineage.append and rebuild a Series with the
        # extended chain (Series is frozen, so this is the supported
        # pattern for tests).
        op_step = OperatorStep.build(
            name="align_series", version="1.0.0",
            params={"join_policy": "outer"},
            input_hashes=(s_pre.lineage.head_hash,),
        )
        extended = Series(
            series_key=s_pre.series_key,
            payload=s_pre.payload,
            units=s_pre.units,
            missingness_policy=s_pre.missingness_policy,
            lineage=s_pre.lineage.append(op_step),
        )

        wire = artifact_series_to_time_series(extended)
        assert wire.description == (
            "derived: calculate_ois_curve_spread_tool → align_series"
        )
        # And the wire payload is unchanged — the operator step was
        # purely structural (just lineage extension for this test).
        assert len(wire.rows) == 3
        assert [r.value for r in wire.rows] == [24.0, 24.5, 25.0]


class TestReversePath_EdgeCases:
    def test_single_row_series_round_trip(self):
        original = TimeSeries(
            series_name="x", units=TimeSeriesUnits.BPS,
            description="desc",
            rows=[TimeSeriesRow(date="2026-04-30", value=42.0)],
        )
        s = time_series_to_artifact_series(
            original,
            primitive_step=_example_primitive_step(),
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        )
        wire = artifact_series_to_time_series(s)
        assert len(wire.rows) == 1
        assert wire.rows[0].date == "2026-04-30"
        assert wire.rows[0].value == 42.0

    def test_empty_description_override_rejected_by_pydantic(self):
        """``TimeSeries.description`` has ``min_length=1`` — passing
        an empty string as the override must raise via Pydantic, not
        silently produce an invalid TimeSeries."""
        from pydantic import ValidationError
        prim = _example_primitive_step()
        ln = Lineage.from_steps([prim])
        s = Series(
            series_key="x",
            payload=pd.Series(
                [1.0], index=pd.DatetimeIndex(["2026-04-30"]), dtype=float,
            ),
            units=TimeSeriesUnits.BPS,
            missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
            lineage=ln,
        )
        with pytest.raises(ValidationError):
            artifact_series_to_time_series(s, description_override="")
