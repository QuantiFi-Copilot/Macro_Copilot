"""Regression: SeriesSet-emitting operators must be consumable DOWNSTREAM.

Every SeriesSet an operator emits carries ``upstream_lineage_by_key`` — the
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

The closed family of SeriesSet emitters is exactly the nine operators whose
registry ``output.artifact_type`` is ``SeriesSet`` — confirmed against the
live ``OPERATOR_REGISTRY`` (== the nine that build ``upstream_lineage_by_key``):

    pca_decompose, rolling_pca, fit_kalman,            # whole-input-derived
    align_series, rolling_regression,                  # whole-input-derived
    cross_sectional_rank, cross_sectional_zscore,      # member-preserving
    demean_cross_section, top_n                        # member-preserving

``_EMITTERS`` below parametrizes over ALL NINE — so "EVERY SeriesSet emitter"
in this header is literally true, not aspirational.  Add the registry's tenth
SeriesSet emitter here the day it lands.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.artifacts.lineage import AdapterStep, FetchStep, Lineage
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel, Series, SeriesSet
from shared.artifacts.units import TimeSeriesUnits
from shared.operators.align_series import align_series
from shared.operators.align_series.schemas import AlignSeriesParams
from shared.operators.cross_sectional_rank import cross_sectional_rank
from shared.operators.cross_sectional_rank.schemas import (
    CrossSectionalRankParams,
)
from shared.operators.cross_sectional_zscore import cross_sectional_zscore
from shared.operators.cross_sectional_zscore.schemas import (
    CrossSectionalZscoreParams,
)
from shared.operators.demean_cross_section import demean_cross_section
from shared.operators.demean_cross_section.schemas import (
    DemeanCrossSectionParams,
)
from shared.operators.fit_kalman import fit_kalman
from shared.operators.fit_kalman.schemas import FitKalmanParams
from shared.operators.pca_decompose import pca_decompose
from shared.operators.pca_decompose.schemas import PcaDecomposeParams
from shared.operators.rolling_pca import rolling_pca
from shared.operators.rolling_pca.schemas import RollingPcaParams
from shared.operators.rolling_regression import rolling_regression
from shared.operators.rolling_regression.schemas import (
    RollingRegressionParams,
)
from shared.operators.top_n import top_n
from shared.operators.top_n.schemas import TopNParams


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


def _series(key: str, n=300, seed=0) -> Series:
    rng = np.random.RandomState(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    return Series(
        series_key=key, payload=pd.Series(rng.randn(n), index=idx),
        units=TimeSeriesUnits.RATIO, frequency="B",
        missingness_policy=RawNoCleaning(), lineage=_lineage(key),
    )


def _aligned_set(seed0=1) -> SeriesSet:
    """A *real* align_series output — the only contract-valid SeriesSet to
    feed the member-preserving cross_sectional / top_n emitters.  A
    hand-built SeriesSet whose ``lineage`` head is NOT a genuine emitting
    step (whose ``input_hashes`` connect to each member's upstream head)
    would break ART9 connectivity inside those operators' per-member
    ``upstream[k].append(input_head_step)``, which is precisely why the
    fixture composes through align_series rather than constructing by hand.
    """
    return align_series(
        [_series("a", seed=seed0), _series("b", seed=seed0 + 1),
         _series("c", seed=seed0 + 2)],
        params=AlignSeriesParams(),
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


def _align_series_set():
    return "align_series", _aligned_set()


def _rolling_regression_set():
    return "rolling_regression", rolling_regression(
        _series("lhs", seed=1), _series("rhs", seed=2),
        params=RollingRegressionParams(window=40))


def _cross_sectional_rank_set():
    return "cross_sectional_rank", cross_sectional_rank(
        _aligned_set(), params=CrossSectionalRankParams())


def _cross_sectional_zscore_set():
    return "cross_sectional_zscore", cross_sectional_zscore(
        _aligned_set(), params=CrossSectionalZscoreParams())


def _demean_cross_section_set():
    return "demean_cross_section", demean_cross_section(
        _aligned_set(), params=DemeanCrossSectionParams())


def _top_n_set():
    return "top_n", top_n(_aligned_set(), params=TopNParams(n=2))


# ALL NINE SeriesSet emitters (registry output.artifact_type == SeriesSet).
# The first three are whole-input-derived fits; align_series /
# rolling_regression are whole-input-derived; the four cross_sectional /
# top_n are member-preserving.  Keep this list == the registry's SeriesSet
# emitter set (see module docstring + test_emitters_match_registry below).
_EMITTERS = [
    _pca_set, _rolling_pca_set, _fit_kalman_set,
    _align_series_set, _rolling_regression_set,
    _cross_sectional_rank_set, _cross_sectional_zscore_set,
    _demean_cross_section_set, _top_n_set,
]


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
        # once, in the middle — no duplication propagated).  When the
        # producer IS cross_sectional_rank, the step legitimately appears
        # twice (rank → rank), so the no-duplication count is producer-aware.
        expected = 2 if op_name == "cross_sectional_rank" else 1
        for key in ranked.series_by_key:
            member = ranked.get_series(key)
            names = [s.name for s in member.lineage.steps]
            assert names[-1] == "cross_sectional_rank"
            assert names.count(op_name) == expected, (
                f"{op_name}: appears {names.count(op_name)}x (expected "
                f"{expected}) in ranked.get_series('{key}') lineage {names}")


def test_emitters_cover_every_seriesset_emitter_in_the_registry():
    """The header claims EVERY SeriesSet emitter — prove it against the live
    registry so the parametrization can never silently fall behind a newly
    registered SeriesSet emitter (the exact blind spot m48 re-opened).
    """
    from shared.workflow.registry import OPERATOR_REGISTRY

    registry_emitters = {
        name for name, spec in OPERATOR_REGISTRY.items()
        if spec.output.artifact_type.value == "SeriesSet"
    }
    parametrized = {emitter()[0] for emitter in _EMITTERS}
    assert parametrized == registry_emitters, (
        "the SeriesSet-emitter set drifted: "
        f"registry-only={sorted(registry_emitters - parametrized)}, "
        f"test-only={sorted(parametrized - registry_emitters)}")
    # The closed family is exactly nine today (ADR 0016 / live registry).
    assert len(registry_emitters) == 9


def test_three_deep_chain_each_step_appears_exactly_once():
    """The ART9 LIN-2 invariant the original bug violated, end-to-end.

    Build a real 3-deep registered chain
    ``align_series → cross_sectional_rank → top_n`` and walk the TERMINAL
    SeriesSet's per-member lineage: each op's step must appear EXACTLY ONCE
    — no double-append (the original duplication bug) and no missing step
    (a dropped producer).  The three ops are distinct, so the count is a
    clean ``== 1`` for each.
    """
    aligned = align_series(
        [_series("a", seed=1), _series("b", seed=2), _series("c", seed=3)],
        params=AlignSeriesParams())
    ranked = cross_sectional_rank(aligned, params=CrossSectionalRankParams())
    selected = top_n(ranked, params=TopNParams(n=2))

    assert isinstance(selected, SeriesSet)
    assert set(selected.series_by_key) == set(aligned.series_by_key)

    chain_ops = ("align_series", "cross_sectional_rank", "top_n")
    for key in selected.series_by_key:
        member = selected.get_series(key)  # constructs + validates lineage
        names = [s.name for s in member.lineage.steps]
        for op in chain_ops:
            assert names.count(op) == 1, (
                f"3-deep chain: '{op}' appears {names.count(op)}x in "
                f"get_series('{key}') lineage {names} — ART9 LIN-2 violated")
        # The chain ops appear in composition order, terminal step is top_n.
        positions = [names.index(op) for op in chain_ops]
        assert positions == sorted(positions), (
            f"chain ops out of order in {names}")
        assert names[-1] == "top_n"
        # The member's terminal step IS the SeriesSet's emitting step.
        assert member.lineage.head_hash == selected.lineage.head_hash
