"""Tests for shared.operators.convert_units — the sole conversion site (D4)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.artifacts.lineage import Lineage, PrimitiveStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Series
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import load_operator_config
from shared.operators.convert_units import (
    CONFIG_PATH,
    convert_units,
    ConvertUnitsError,
    ConvertUnitsParams,
)
from shared.workflow.registry import OPERATOR_REGISTRY


def _lin(key: str = "x") -> Lineage:
    step = PrimitiveStep.build(
        name="synthetic", version="1.0.0", params={"series_key": key},
        tool_config_hash="h", output_field="time_series",
        as_of_date="2025-01-01",
    )
    return Lineage.from_steps([step])


def _series(values, units) -> Series:
    idx = pd.bdate_range("2025-01-01", periods=len(values))
    return Series(
        series_key="x", payload=pd.Series(values, index=idx, dtype=float),
        units=units, frequency="B", missingness_policy=RawNoCleaning(),
        lineage=_lin(),
    )


def test_percent_to_bps_scales_by_100():
    s = _series([1.0, 2.0, 3.0], TimeSeriesUnits.PERCENT)
    out = convert_units(s, params=ConvertUnitsParams(target_units=TimeSeriesUnits.BPS))
    assert out.units == TimeSeriesUnits.BPS
    assert list(out.payload.values) == [100.0, 200.0, 300.0]
    head = out.lineage.steps[-1].params
    assert head["factor"] == 100.0
    assert head["source_units"] == TimeSeriesUnits.PERCENT.value
    assert head["target_units"] == TimeSeriesUnits.BPS.value


def test_bps_to_percent_scales_by_0_01():
    s = _series([100.0, 250.0], TimeSeriesUnits.BPS)
    out = convert_units(s, params=ConvertUnitsParams(target_units=TimeSeriesUnits.PERCENT))
    assert out.units == TimeSeriesUnits.PERCENT
    assert list(out.payload.values) == [1.0, 2.5]


def test_identity_conversion_is_noop():
    s = _series([1.0, 2.0], TimeSeriesUnits.PERCENT)
    out = convert_units(s, params=ConvertUnitsParams(target_units=TimeSeriesUnits.PERCENT))
    assert out.units == TimeSeriesUnits.PERCENT
    assert list(out.payload.values) == [1.0, 2.0]
    assert out.lineage.steps[-1].params["factor"] == 1.0


def test_unsupported_pair_raises_notimplemented():
    s = _series([1.0, 2.0], TimeSeriesUnits.PERCENT)
    with pytest.raises(NotImplementedError, match="no conversion"):
        convert_units(s, params=ConvertUnitsParams(target_units=TimeSeriesUnits.RATIO))


def test_non_series_input_raises_typed():
    with pytest.raises(ConvertUnitsError, match="Series artifact"):
        convert_units(
            "not a series",
            params=ConvertUnitsParams(target_units=TimeSeriesUnits.BPS),
        )


def test_missing_params_raises_typed():
    s = _series([1.0], TimeSeriesUnits.PERCENT)
    with pytest.raises(ConvertUnitsError, match="requires explicit params"):
        convert_units(s)


def test_nan_preserved_through_conversion():
    s = _series([1.0, np.nan, 3.0], TimeSeriesUnits.PERCENT)
    out = convert_units(s, params=ConvertUnitsParams(target_units=TimeSeriesUnits.BPS))
    assert bool(np.isnan(out.payload.iloc[1]))
    assert out.payload.iloc[0] == 100.0


def test_registered_and_config_loads():
    # PART B refactor: input_slots values are SlotDescriptor instances and
    # output is an OutputDescriptor (ArtifactTypeName is a str-Enum, so
    # == "Series" still compares equal to the string).
    assert "convert_units" in OPERATOR_REGISTRY
    spec = OPERATOR_REGISTRY["convert_units"]
    assert set(spec.input_slots) == {"series"}
    assert spec.input_slots["series"].artifact_type == "Series"
    assert spec.output.artifact_type == "Series"
    cfg = load_operator_config(CONFIG_PATH)
    assert cfg.operator.name == "convert_units"
    assert cfg.operator.method_family == "unit_conversion"
