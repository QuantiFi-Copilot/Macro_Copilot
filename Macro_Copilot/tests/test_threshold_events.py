"""Tests for shared.operators.threshold_events.

Covers:

  - schema validation (rule + basis combinations, rolling-arg
    requirements, abs_above non-negativity)
  - rule branches (above / below / abs_above) on raw_value basis
  - rolling_zscore basis: math, min_periods warmup, ddof choice
  - LOOKAHEAD-SAFE ENFORCEMENT (load-bearing): the rolling stats at
    time t must use only data up to t-1 in default mode.  Verified
    against an independently hand-computed shifted reference, NOT
    just by introspecting operator state.
  - lookahead-unsafe opt-in: produces a different event set, recorded
    in lineage
  - per-event metadata correctness (raw_value + zscore_value)
  - lineage propagation through the operator step
  - error envelope phrases
  - bundled config loads + operator wires through to it
  - finance-blindness (operator runs unchanged on Z_SCORE inputs)
  - composition: align_series → series_arithmetic → threshold_events
    over a synthetic swap-spread, producing a non-empty EventSet with
    full multi-step lineage (fetch + adapter + align + arithmetic +
    threshold).
"""

from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd
import pytest

from shared.artifacts import (
    AdapterStep,
    CleanSingleSeriesV1,
    EventSet,
    FetchStep,
    Lineage,
    RawNoCleaning,
    Series,
    TimeSeriesUnits,
)
from shared.config.operator_config import (
    clear_operator_config_cache,
    load_operator_config,
)
from shared.operators.align_series import align_series, AlignSeriesParams
from shared.operators.series_arithmetic import series_arithmetic
from shared.operators.threshold_events import (
    CONFIG_PATH,
    ThresholdEventsParams,
    threshold_events,
)
from shared.operators.threshold_events.operator import ThresholdEventsError


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_operator_config_cache()
    yield
    clear_operator_config_cache()


# ===========================================================================
# Helpers
# ===========================================================================


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
# 1. Schema validation
# ===========================================================================


class TestSchemaValidation:
    def test_rolling_zscore_requires_window(self):
        with pytest.raises(ValueError, match="rolling_window"):
            ThresholdEventsParams(
                rule="above", threshold=1.5,
                threshold_basis="rolling_zscore",
            )

    def test_raw_value_rejects_rolling_args(self):
        with pytest.raises(ValueError, match="does not use"):
            ThresholdEventsParams(
                rule="above", threshold=4.0,
                threshold_basis="raw_value",
                rolling_window=252,
            )

    def test_abs_above_rejects_negative_threshold(self):
        with pytest.raises(ValueError, match="every observation"):
            ThresholdEventsParams(rule="abs_above", threshold=-1.0)

    def test_min_periods_cannot_exceed_rolling_window(self):
        with pytest.raises(ValueError, match="cannot exceed"):
            ThresholdEventsParams(
                rule="above", threshold=2.0,
                threshold_basis="rolling_zscore",
                rolling_window=10, min_periods=20,
            )

    def test_rolling_window_below_two_rejected(self):
        with pytest.raises(ValueError):
            ThresholdEventsParams(
                rule="above", threshold=2.0,
                threshold_basis="rolling_zscore",
                rolling_window=1,
            )


# ===========================================================================
# 2. Raw-value rule branches
# ===========================================================================


class TestRawValueRules:
    def _build(self):
        return _series(
            "x",
            dates=["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07",
                   "2026-01-08"],
            values=[1.0, 3.0, 5.0, -2.0, 4.0],
        )

    def test_above_strict(self):
        s = self._build()
        es = threshold_events(s, ThresholdEventsParams(rule="above", threshold=3.0))
        # values > 3 → indices 2 and 4
        assert es.n_events == 2
        assert [d.strftime("%Y-%m-%d") for d in es.event_dates] == [
            "2026-01-06", "2026-01-08",
        ]

    def test_below_strict(self):
        s = self._build()
        es = threshold_events(s, ThresholdEventsParams(rule="below", threshold=0.0))
        # values < 0 → index 3 only (-2.0)
        assert es.n_events == 1
        assert es.event_dates[0].strftime("%Y-%m-%d") == "2026-01-07"

    def test_abs_above(self):
        s = self._build()
        es = threshold_events(s, ThresholdEventsParams(rule="abs_above", threshold=2.5))
        # |values| > 2.5 → indices 1 (3), 2 (5), 3 (-2)? No, |-2|=2 not > 2.5.
        # So indices 1 (3), 2 (5), 4 (4) all qualify (|3|>2.5, |5|>2.5, |4|>2.5).
        assert es.n_events == 3

    def test_strict_inequality_no_event_at_threshold(self):
        """rule='above' is strict: a value exactly equal to threshold
        does NOT fire an event.  Pin this contract."""
        s = _series(
            "x", dates=["2026-01-02", "2026-01-05"],
            values=[5.0, 5.0001],
        )
        es = threshold_events(s, ThresholdEventsParams(rule="above", threshold=5.0))
        assert es.n_events == 1
        assert es.event_dates[0].strftime("%Y-%m-%d") == "2026-01-05"


# ===========================================================================
# 3. Rolling z-score basis — math + warmup + LOOKAHEAD-SAFE enforcement
# ===========================================================================


class TestRollingZScoreLookaheadSafe:
    """Build plan v5 + operator architecture: lookahead-safe is the
    load-bearing knob.  At time t with default look_ahead_safe=True,
    the rolling stats must come from data ≤ t-1.  Verified against
    an independent hand-computed reference."""

    def _build_long_series(self, n_days=30, seed=7):
        """Synthetic series with enough length to exceed the rolling
        window; controlled noise so events are deterministic."""
        rng = np.random.default_rng(seed)
        idx = pd.bdate_range("2026-01-02", periods=n_days)
        vals = 4.0 + np.cumsum(rng.normal(0, 0.05, n_days))
        return _series("x", dates=list(idx), values=list(vals))

    def test_lookahead_safe_matches_independent_reference(self):
        """Independent reference: at index t, the rolling mean / std
        used should be over [t-window, t-1] inclusive.  The operator
        must agree with that reference cell-by-cell."""
        s = self._build_long_series(n_days=20)
        window = 5
        params = ThresholdEventsParams(
            rule="abs_above", threshold=1.0,
            threshold_basis="rolling_zscore",
            rolling_window=window,
            look_ahead_safe=True,
        )
        es = threshold_events(s, params)

        # Hand-compute reference z-scores with the lookahead-safe shift.
        x = s.payload
        ref_mean = x.rolling(window=window, min_periods=window).mean().shift(1)
        ref_std = x.rolling(window=window, min_periods=window).std(ddof=1).shift(1)
        ref_z = (x - ref_mean) / ref_std
        ref_mask = (ref_z.abs() > 1.0).fillna(False)

        # Operator's mask must equal the reference mask exactly.
        np.testing.assert_array_equal(
            es.mask.to_numpy(),
            ref_mask.to_numpy(),
        )
        # And per-event metadata's zscore_value must match the
        # reference's standardised value at each event date.
        for ts, meta in zip(es.event_dates, es.per_event_metadata):
            ref_at_t = ref_z.loc[ts]
            assert abs(meta["zscore_value"] - float(ref_at_t)) < 1e-12

    def test_lookahead_unsafe_differs_from_lookahead_safe(self):
        """Disabling the safety must produce a DIFFERENT event set
        (otherwise the knob is silently doing nothing).  This is the
        regression guard against accidentally short-circuiting the
        shift in a future refactor."""
        s = self._build_long_series(n_days=30, seed=42)
        params_safe = ThresholdEventsParams(
            rule="abs_above", threshold=1.0,
            threshold_basis="rolling_zscore",
            rolling_window=5,
            look_ahead_safe=True,
        )
        params_unsafe = params_safe.model_copy(update={"look_ahead_safe": False})

        es_safe = threshold_events(s, params_safe)
        es_unsafe = threshold_events(s, params_unsafe)

        # Both produce SOME events on this seed (sanity).
        assert es_safe.n_events > 0
        assert es_unsafe.n_events > 0
        # But the events should differ — the unsafe variant uses
        # in-sample data.
        assert es_safe.mask.to_numpy().tolist() != es_unsafe.mask.to_numpy().tolist()

    def test_lookahead_safe_recorded_in_lineage(self):
        s = self._build_long_series(n_days=15)
        es = threshold_events(s, ThresholdEventsParams(
            rule="above", threshold=2.0,
            threshold_basis="rolling_zscore",
            rolling_window=5,
            look_ahead_safe=True,
        ))
        params = es.lineage.steps[-1].params
        assert params["look_ahead_safe"] is True
        assert params["lookahead_shift_applied"] is True

    def test_lookahead_unsafe_choice_recorded(self):
        s = self._build_long_series(n_days=15)
        es = threshold_events(s, ThresholdEventsParams(
            rule="above", threshold=2.0,
            threshold_basis="rolling_zscore",
            rolling_window=5,
            look_ahead_safe=False,
        ))
        params = es.lineage.steps[-1].params
        assert params["look_ahead_safe"] is False
        assert params["lookahead_shift_applied"] is False


class TestRollingZScoreWarmup:
    def test_warmup_emits_no_events(self):
        """Until min_periods observations have passed (with the
        lookahead shift, that's the ``window+1``th index), no event
        can fire because the threshold series is NaN."""
        s = _series(
            "x",
            dates=list(pd.bdate_range("2026-01-02", periods=10)),
            values=[10.0] * 5 + [100.0, 100.0, 100.0, 100.0, 100.0],
        )
        es = threshold_events(s, ThresholdEventsParams(
            rule="abs_above", threshold=0.5,
            threshold_basis="rolling_zscore",
            rolling_window=5,
            look_ahead_safe=True,
        ))
        # Warmup (window=5) + lookahead shift = first 5 indices NaN
        # rolling stats; index 5's stat uses [0..4] (constant 10), std=0,
        # so z-score at 5 is inf → NaN → no event.  Index 6's stat
        # window contains the [10,10,10,10,100] split — std nonzero,
        # zscore well-defined.  We verify the WARMUP-period rows have
        # no events (the first 5 indices).
        warmup_dates = list(s.payload.index[:5])
        for d in warmup_dates:
            assert not bool(es.mask.loc[d]), (
                f"warmup day {d} should have no event"
            )

    def test_min_periods_default_equals_window(self):
        """When min_periods is omitted, it defaults to rolling_window
        — the strict choice (no partial-window stats).  Pinned in
        the lineage step."""
        s = _series(
            "x",
            dates=list(pd.bdate_range("2026-01-02", periods=15)),
            values=list(range(15)),
        )
        es = threshold_events(s, ThresholdEventsParams(
            rule="above", threshold=1.0,
            threshold_basis="rolling_zscore",
            rolling_window=7,
        ))
        assert es.lineage.steps[-1].params["min_periods_used"] == 7


# ===========================================================================
# 4. Per-event metadata
# ===========================================================================


class TestPerEventMetadata:
    def test_raw_value_metadata_includes_raw_only(self):
        s = _series("x", dates=["2026-01-02", "2026-01-05"], values=[1.0, 5.0])
        es = threshold_events(s, ThresholdEventsParams(rule="above", threshold=2.0))
        assert es.n_events == 1
        meta = es.per_event_metadata[0]
        assert meta["rule"] == "above"
        assert meta["threshold"] == 2.0
        assert meta["threshold_basis"] == "raw_value"
        assert meta["raw_value"] == 5.0
        assert "zscore_value" not in meta  # not applicable

    def test_rolling_zscore_metadata_includes_zscore_value(self):
        s = _series(
            "x",
            dates=list(pd.bdate_range("2026-01-02", periods=10)),
            values=[1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 100.0, 1.0, 1.0, 1.0],
        )
        # Threshold chosen against a hand-computed reference:
        #
        # At index 7, the lookahead-safe rolling window covers indices
        # 2..6 = [1,1,1,1,100].  mean=20.8, std≈44.27 (sample, ddof=1).
        # zscore at idx 7 = (1 - 20.8) / 44.27 ≈ -0.447  → |z| ≈ 0.447.
        # A threshold of 0.3 fires there; 1.0 would not.  This pins
        # both that an event is correctly detected AND that the
        # per-event metadata exposes the standardised value.
        es = threshold_events(s, ThresholdEventsParams(
            rule="abs_above", threshold=0.3,
            threshold_basis="rolling_zscore",
            rolling_window=5,
        ))
        assert es.n_events >= 1
        for meta in es.per_event_metadata:
            assert "zscore_value" in meta
            assert "raw_value" in meta
            assert meta["threshold"] == 0.3
            assert meta["threshold_basis"] == "rolling_zscore"


# ===========================================================================
# 5. Lineage propagation
# ===========================================================================


class TestLineagePropagation:
    def test_appends_operator_step(self):
        s = _series("x", dates=["2026-01-02", "2026-01-05"], values=[1.0, 5.0])
        es = threshold_events(s, ThresholdEventsParams(rule="above", threshold=2.0))
        kinds = [step.kind for step in es.lineage.steps]
        assert kinds == ["fetch", "adapter", "operator"]
        assert es.lineage.steps[-1].name == "threshold_events"

    def test_step_records_input_hash(self):
        s = _series("x", dates=["2026-01-02"], values=[1.0])
        es = threshold_events(s, ThresholdEventsParams(rule="above", threshold=0.0))
        op_step = es.lineage.steps[-1]
        assert op_step.input_hashes == (s.lineage.head_hash,)

    def test_step_records_n_events_and_source_key(self):
        s = _series("x", dates=["2026-01-02", "2026-01-05"], values=[1.0, 5.0])
        es = threshold_events(s, ThresholdEventsParams(rule="above", threshold=2.0))
        params = es.lineage.steps[-1].params
        assert params["n_events"] == 1
        assert params["source_series_key"] == "x"

    def test_lineage_json_roundtrip(self):
        s = _series(
            "x",
            dates=list(pd.bdate_range("2026-01-02", periods=10)),
            values=list(range(10)),
        )
        es = threshold_events(s, ThresholdEventsParams(
            rule="abs_above", threshold=1.0,
            threshold_basis="rolling_zscore", rolling_window=5,
        ))
        as_dict = es.lineage.model_dump(mode="json")
        rec = Lineage.model_validate(as_dict)
        assert rec.head_hash == es.lineage.head_hash
        assert len(rec.steps) == len(es.lineage.steps)


# ===========================================================================
# 6. Error envelope + structural validation
# ===========================================================================


class TestErrorEnvelope:
    def test_non_series_input_raises(self):
        with pytest.raises(ThresholdEventsError, match="must be a Series"):
            threshold_events("not a series", ThresholdEventsParams(  # type: ignore[arg-type]
                rule="above", threshold=0.0,
            ))

    def test_empty_series_raises(self):
        # Build a minimal Series with an empty payload.  Series'
        # validators allow empty (no duplicates, sorted ascending —
        # vacuously true), so we exercise the operator's own check.
        idx = pd.DatetimeIndex([])
        payload = pd.Series([], index=idx, dtype=float)
        fetch = FetchStep.build(name="fetch_single_tenor", version="1", params={})
        ad = AdapterStep.build(
            name="raw_dataframe_to_artifact_series", version="1",
            params={"series_key": "x", "units": "percent"},
            input_hashes=(fetch.hash,),
        )
        s = Series(
            series_key="x", payload=payload,
            units=TimeSeriesUnits.PERCENT, frequency=None,
            missingness_policy=RawNoCleaning(),
            lineage=Lineage.from_steps([fetch, ad]),
        )
        with pytest.raises(ThresholdEventsError, match="empty"):
            threshold_events(s, ThresholdEventsParams(rule="above", threshold=0.0))


# ===========================================================================
# 7. Bundled config
# ===========================================================================


class TestBundledConfig:
    def test_config_path_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads_with_correct_identity(self):
        cfg = load_operator_config(CONFIG_PATH)
        assert cfg.operator.name == "threshold_events"
        assert cfg.operator.method_family == "masking"

    def test_config_defaults_are_strict(self):
        cfg = load_operator_config(CONFIG_PATH)
        # threshold_basis default is raw_value — least surprising
        assert cfg.default_value("threshold_basis") == "raw_value"
        # look_ahead_safe default is True — strict
        assert cfg.default_value("look_ahead_safe") is True


# ===========================================================================
# 8. Finance-blindness
# ===========================================================================


class TestFinanceBlindness:
    def test_runs_unchanged_on_z_score_input(self):
        """The operator does not assume the input is a yield or a
        spread; it just consumes a numeric Series.  ``Z_SCORE`` units
        are explicitly outside the rates domain — the operator should
        run without any unit-aware logic."""
        s = _series(
            "ny_temp",
            dates=list(pd.bdate_range("2026-01-02", periods=10)),
            values=[10.0, 11.0, 12.0, 13.0, 14.0, 50.0, 13.0, 12.0, 11.0, 10.0],
            units=TimeSeriesUnits.Z_SCORE,
        )
        es = threshold_events(s, ThresholdEventsParams(rule="above", threshold=20.0))
        assert es.n_events == 1
        assert es.event_dates[0].strftime("%Y-%m-%d") == (
            s.payload.index[5].strftime("%Y-%m-%d")
        )


# ===========================================================================
# 9. Composition (Q1 source path: align → arithmetic → threshold)
# ===========================================================================


class TestComposition:
    def test_align_arithmetic_threshold_endtoend(self):
        """Build the literal first three operators of Q1's DAG: align
        a rates pair, subtract them, threshold the resulting spread.
        Every step's lineage must thread through to the final
        ``EventSet``.

        This is the strongest composition test in the operator suite
        so far — it exercises align_series + series_arithmetic +
        threshold_events together with the new auxiliary_lineages
        contract from PR #51 (right-hand provenance preservable on
        the arithmetic step) AND the lineage shape derived from
        SeriesSet.get_series."""
        # Construct two synthetic rate series with a deliberate spike
        # on a known date so the threshold catches a real event.
        idx = list(pd.bdate_range("2026-01-02", periods=20))
        # ust_2y: stable around 4.50 with a spike on day 15
        ust_vals = [4.50] * 14 + [4.80, 4.55, 4.50, 4.50, 4.50, 4.50]
        ois_vals = [4.30] * 20
        ust_2y = _series("ust_2y", dates=idx, values=ust_vals)
        ois_2y = _series("ois_2y", dates=idx, values=ois_vals)

        aligned = align_series([ust_2y, ois_2y], AlignSeriesParams())
        spread = series_arithmetic(
            aligned.get_series("ust_2y"),
            "subtract",
            aligned.get_series("ois_2y"),
        )
        # Spread is ~0.20 normally with a spike to 0.50 on day 14.
        es = threshold_events(spread, ThresholdEventsParams(
            rule="above", threshold=0.30,
        ))
        assert es.n_events == 1
        assert es.event_dates[0] == idx[14]

        # Lineage threads through every operator.  Per the v5 design,
        # the head Series's lineage holds the LEFT chain plus each
        # operator step in sequence; right-hand chains live inside
        # auxiliary_lineages on the relevant arithmetic step.
        kinds = [step.kind for step in es.lineage.steps]
        # fetch (left ust_2y) → adapter → align → arithmetic → threshold
        assert kinds == ["fetch", "adapter", "operator", "operator", "operator"]
        names = [
            step.name for step in es.lineage.steps if step.kind == "operator"
        ]
        assert names == ["align_series", "series_arithmetic", "threshold_events"]

        # The arithmetic step must carry the right-hand (ois_2y)
        # lineage chain inside its auxiliary_lineages — proves the
        # PR #51 contract still holds when threshold_events is
        # appended on top.
        arithmetic_step = es.lineage.steps[3]
        assert len(arithmetic_step.auxiliary_lineages) == 1
        right_chain = arithmetic_step.auxiliary_lineages[0]
        assert right_chain.steps[0].kind == "fetch"

    def test_composition_lineage_json_roundtrips(self):
        idx = list(pd.bdate_range("2026-01-02", periods=10))
        a = _series("a", dates=idx, values=[1.0 + i * 0.1 for i in range(10)])
        b = _series("b", dates=idx, values=[0.5] * 10)
        aligned = align_series([a, b])
        diff = series_arithmetic(
            aligned.get_series("a"), "subtract", aligned.get_series("b"),
        )
        es = threshold_events(diff, ThresholdEventsParams(
            rule="above", threshold=0.5,
        ))
        as_dict = es.lineage.model_dump(mode="json")
        rec = Lineage.model_validate(as_dict)
        assert rec.head_hash == es.lineage.head_hash
        # Auxiliary lineages survive byte-identically through the
        # composed chain.
        rec_arithmetic_step = rec.steps[3]
        assert len(rec_arithmetic_step.auxiliary_lineages) == 1
