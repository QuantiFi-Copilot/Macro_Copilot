"""Regression: SeriesSet-emitting operators must be consumable DOWNSTREAM.

Every SeriesSet a operator emits carries ``upstream_lineage_by_key`` — the
per-member provenance WITHOUT that operator's own step.  ``SeriesSet.
get_series`` (and every downstream SeriesSet transformer:
cross_sectional_*, top_n, demean_cross_section) appends
``self.lineage.steps[-1]`` (the emitting operator's step) to it.  If an
emitter stored the lineage WITH its own step, get_series would DUPLICATE it
and break the ART9 LIN-2 connectivity check the moment the SeriesSet is
consumed downstream.

The per-operator tests only exercised these operators as the TERMINAL node,
so the duplication slipped through until the §2 North-Star demo composed
``pca_decompose → cross_sectional_rank``.  This locks the invariant for
EVERY SeriesSet emitter.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.artifacts.lineage import AdapterStep, FetchStep, Lineage
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel, Series, SeriesSet
from shared.artifacts.units import TimeSeriesUnits
from shared.operators.cross_sectional_rank import cross_sectional_rank
from shared.operators.cross_sectional_rank.schemas import (
    CrossSectionalRankParams,
)
from shared.operators.fit_kalman import fit_kalman
from shared.operators.fit_kalman.schemas import FitKalmanParams
from shared.operators.pca_decompose import pca_decompose
from shared.operators.pca_decompose.schemas import PcaDecomposeParams
from shared.operators.rolling_pca import rolling_pca
from shared.operators.rolling_pca.schemas import RollingPcaParams


def _lineage(key: str) -> Lineage:
    f = FetchStep.build(name="fetch_single_tenor", version="1.0.0",
                        params={"series_key": key})
    a = AdapterStep.build(name="raw_dataframe_to_artifact_series",
                          version="1.0.0",
                          params={"series_key": key, "units": "ratio"},
                          input_hashes=(f.hash,))
    return Lineage.from_steps([f, a])


def _panel(n=300, seed=0, cols=("a", "b", "c", "d")) -> Panel:
    rng = np.random.RandomState(seed)
    drv = np.cumsum(rng.randn(n))
    df = pd.DataFrame(
        np.column_stack([drv + 0.3 * rng.randn(n), drv + 0.3 * rng.randn(n),
                         rng.randn(n), 0.5 * drv + rng.randn(n)]),
        index=pd.bdate_range("2020-01-01", periods=n), columns=list(cols),
    )
    return Panel(payload=df,
                 units_by_column={c: TimeSeriesUnits.RATIO for c in df.columns},
                 missingness_policy=RawNoCleaning(), lineage=_lineage("p"))


def _series_set(n=600, seed=0) -> SeriesSet:
    rng = np.random.RandomState(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    x1, x2 = rng.randn(n), rng.randn(n)
    beta = np.where(np.arange(n) < n // 2, 0.5, 2.0)
    y = beta * x1 + 0.3 * x2 + 0.05 * rng.randn(n)
    frame = {"y": pd.Series(y, index=idx), "x1": pd.Series(x1, index=idx),
             "x2": pd.Series(x2, index=idx)}
    return SeriesSet(
        series_by_key=frame,
        units_by_key={k: TimeSeriesUnits.RATIO for k in frame},
        missingness_by_key={k: RawNoCleaning() for k in frame},
        upstream_lineage_by_key={k: _lineage(k) for k in frame},
        common_index=idx, frequency="B", lineage=_lineage("set"),
    )


# (operator label, the SeriesSet it produces) — built lazily per test.
def _pca_set():
    return "pca_decompose", pca_decompose(
        _panel(), params=PcaDecomposeParams(n_components=2))


def _rolling_pca_set():
    return "rolling_pca", rolling_pca(
        _panel(), params=RollingPcaParams(window=40, n_components=2))


def _fit_kalman_set():
    return "fit_kalman", fit_kalman(
        _series_set(), params=FitKalmanParams(
            target_key="y", signal_to_noise_ratio=0.05, basis="raw_value"))


_EMITTERS = [_pca_set, _rolling_pca_set, _fit_kalman_set]


@pytest.mark.parametrize("emitter", _EMITTERS,
                         ids=lambda e: e.__name__)
class TestSeriesSetDownstreamLineage:
    def test_get_series_is_connected_and_step_appears_once(self, emitter):
        op_name, ss = emitter()
        for key in ss.series_by_key:
            member = ss.get_series(key)  # constructs + validates the lineage
            assert isinstance(member, Series)
            names = [s.name for s in member.lineage.steps]
            # The emitting operator's step appears EXACTLY ONCE (not
            # duplicated) and is the head.
            assert names.count(op_name) == 1, (
                f"{op_name}: step appears {names.count(op_name)}x in "
                f"get_series('{key}') lineage {names}")
            assert names[-1] == op_name
            # And the member's head_hash equals the set's head_hash (the
            # member's last step IS the set's emitting step).
            assert member.lineage.head_hash == ss.lineage.head_hash

    def test_consumable_by_downstream_cross_sectional_rank(self, emitter):
        op_name, ss = emitter()
        ranked = cross_sectional_rank(ss, params=CrossSectionalRankParams())
        assert isinstance(ranked, SeriesSet)
        assert set(ranked.series_by_key) == set(ss.series_by_key)
        # The ranked output's per-member lineage is the full connected chain
        # ending in cross_sectional_rank (and the producer's step appears
        # once, in the middle — no duplication propagated).
        for key in ranked.series_by_key:
            member = ranked.get_series(key)
            names = [s.name for s in member.lineage.steps]
            assert names[-1] == "cross_sectional_rank"
            assert names.count(op_name) == 1
