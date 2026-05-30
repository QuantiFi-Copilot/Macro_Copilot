"""Phase 1+2 step 7 — unified multi-operator metadata-flag algebra.

Locks M3 (event_windows exposes BOTH require_matching_* flags) and M5
(a lenient missingness opt-out emits an honest CombinedMissingnessV1
instead of silently keeping one input's policy).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.artifacts.lineage import Lineage, PrimitiveStep
from shared.artifacts.missingness import (
    CleanSingleSeriesV1,
    CombinedMissingnessV1,
    RawNoCleaning,
)
from shared.artifacts.types import Series
from shared.artifacts.units import TimeSeriesUnits
from shared.operators.event_windows.schemas import EventWindowsParams
from shared.operators.rolling_regression import (
    rolling_regression,
    RollingRegressionParams,
)


def _lin(key: str = "x") -> Lineage:
    step = PrimitiveStep.build(
        name="synthetic", version="1.0.0", params={"series_key": key},
        tool_config_hash="h", output_field="time_series",
        as_of_date="2025-01-01",
    )
    return Lineage.from_steps([step])


def _series(key, values, policy) -> Series:
    idx = pd.bdate_range("2025-01-01", periods=len(values))
    return Series(
        series_key=key, payload=pd.Series(values, index=idx, dtype=float),
        units=TimeSeriesUnits.PERCENT, frequency="B",
        missingness_policy=policy, lineage=_lin(key),
    )


# --- M3: event_windows exposes BOTH OPR11 flags -------------------------


def test_event_windows_exposes_both_metadata_flags():
    p = EventWindowsParams()
    assert p.require_matching_frequency is True
    assert p.require_matching_missingness is True
    assert EventWindowsParams(require_matching_missingness=False).require_matching_missingness is False


# --- M5: CombinedMissingnessV1 closed-family membership + round-trip -----


def test_combined_missingness_round_trips():
    combined = CombinedMissingnessV1(
        components=(CleanSingleSeriesV1(ffill_limit=5), RawNoCleaning())
    )
    restored = CombinedMissingnessV1.model_validate(combined.model_dump())
    assert restored == combined
    assert restored.kind == "combined_missingness_v1"


def test_combined_missingness_usable_as_series_policy():
    combined = CombinedMissingnessV1(
        components=(CleanSingleSeriesV1(ffill_limit=5), RawNoCleaning())
    )
    s = _series("z", [1.0, 2.0, 3.0], combined)
    assert isinstance(s.missingness_policy, CombinedMissingnessV1)


def test_combined_missingness_requires_two_components():
    with pytest.raises(Exception):  # pydantic ValidationError (min_length=2)
        CombinedMissingnessV1(components=(RawNoCleaning(),))


# --- M5: rolling_regression emits a combined policy on mismatch ---------


def test_rolling_regression_combined_policy_on_mismatch():
    rng = np.random.default_rng(3)
    x = np.cumsum(rng.normal(size=80))
    y = 1.2 * x + rng.normal(0, 0.1, size=80)
    lhs = _series("y", y, CleanSingleSeriesV1(ffill_limit=5))
    rhs = _series("x", x, RawNoCleaning())  # different policy
    # rolling_regression defaults require_matching_missingness=False (lenient).
    out = rolling_regression(lhs, rhs, params=RollingRegressionParams(window=30))
    pol = out.missingness_by_key["beta"]
    assert isinstance(pol, CombinedMissingnessV1)
    assert {c.kind for c in pol.components} == {
        "clean_single_series_v1", "raw_no_cleaning",
    }
