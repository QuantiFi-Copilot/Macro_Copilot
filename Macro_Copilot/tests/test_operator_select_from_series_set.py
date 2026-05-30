"""tests/test_operator_select_from_series_set.py — Phase 2A operator.

Covers:

  1. Bundled config.yaml loads + name-checks.
  2. Happy path: extract by key, lineage chain extends through the
     upstream chain + SeriesSet-producing step + this select step.
  3. Unknown-key refusal with available-keys diagnostic.
  4. Per-key metadata (units / missingness / frequency) propagates
     1:1 from the SeriesSet.
  5. Registry entry: operator is registered with input_slots /
     output_type matching the spec.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

import pandas as pd
import pytest

from shared.artifacts.lineage import Lineage, OperatorStep, PrimitiveStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Series, SeriesSet
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    load_operator_config,
)
from shared.operators.align_series import align_series, AlignSeriesParams
from shared.operators.select_from_series_set import (
    CONFIG_PATH,
    select_from_series_set,
    SelectFromSeriesSetError,
    SelectFromSeriesSetParams,
)
from shared.workflow.registry import OPERATOR_REGISTRY


def _primitive_lineage(series_key: str) -> Lineage:
    """Build a minimal lineage rooted at a synthetic primitive call."""
    step = PrimitiveStep.build(
        name="synthetic_primitive",
        version="1.0.0",
        params={"series_key": series_key},
        tool_config_hash="test_config_hash",
        output_field="time_series",
        as_of_date="2025-01-01",
    )
    return Lineage.from_steps([step])


def _make_series(
    series_key: str,
    *,
    n: int = 50,
    units: TimeSeriesUnits = TimeSeriesUnits.PERCENT,
) -> Series:
    idx = pd.bdate_range("2025-01-01", periods=n)
    payload = pd.Series(
        [float(i) for i in range(n)], index=idx, name=series_key,
    )
    return Series(
        series_key=series_key,
        payload=payload,
        units=units,
        frequency="B",
        missingness_policy=RawNoCleaning(),
        lineage=_primitive_lineage(series_key),
    )


def _make_series_set() -> Tuple[SeriesSet, Series, Series]:
    """Convenience: align two synthetic Series, return the SeriesSet
    and the originals (so tests can compare metadata 1:1)."""
    a = _make_series("alpha", units=TimeSeriesUnits.PERCENT)
    b = _make_series("beta", units=TimeSeriesUnits.BPS)
    set_ = align_series([a, b], params=AlignSeriesParams(
        require_matching_frequency=True,
        require_matching_missingness=True,
    ))
    return set_, a, b


# ===========================================================================
# 1. Bundled config.yaml
# ===========================================================================


class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads_with_correct_name(self):
        cfg = load_operator_config(CONFIG_PATH)
        assert cfg.operator.name == "select_from_series_set"
        assert cfg.operator.method_family == "mapping"

    def test_no_required_conventions(self):
        """The select operator has no consequential conventions —
        ``defaults`` must be empty so future drift is caught."""
        cfg = load_operator_config(CONFIG_PATH)
        assert cfg.defaults == {}


# ===========================================================================
# 2. Happy path + lineage propagation
# ===========================================================================


class TestHappyPath:
    def test_extracts_named_series(self):
        set_, a, _b = _make_series_set()
        out = select_from_series_set(
            set_, params=SelectFromSeriesSetParams(series_key="alpha"),
        )
        assert isinstance(out, Series)
        assert out.series_key == "alpha"
        # Payload index is the SeriesSet's common_index — alignment
        # already happened, so the Series payload index == common_index.
        assert out.payload.index.equals(set_.common_index)

    def test_lineage_chain_includes_select_step(self):
        """The output Series's lineage head must be the select step,
        i.e. the most-recent step the workflow recorded."""
        set_, _a, _b = _make_series_set()
        out = select_from_series_set(
            set_, params=SelectFromSeriesSetParams(series_key="alpha"),
        )
        # Last step is the select step we just added.
        head = out.lineage.steps[-1]
        assert isinstance(head, OperatorStep)
        assert head.name == "select_from_series_set"
        assert head.params["series_key"] == "alpha"

    def test_lineage_chain_includes_align_step(self):
        """The Series's lineage must transitively include the
        SeriesSet-producing alignment step (between upstream
        primitive and the new select step)."""
        set_, _a, _b = _make_series_set()
        out = select_from_series_set(
            set_, params=SelectFromSeriesSetParams(series_key="alpha"),
        )
        names = [step.name for step in out.lineage.steps]
        assert "align_series" in names

    def test_lineage_chain_includes_upstream_primitive_step(self):
        """The original Series's PrimitiveStep must survive into the
        post-select lineage."""
        set_, _a, _b = _make_series_set()
        out = select_from_series_set(
            set_, params=SelectFromSeriesSetParams(series_key="alpha"),
        )
        names = [step.name for step in out.lineage.steps]
        assert "synthetic_primitive" in names

    def test_lineage_step_order_is_primitive_then_align_then_select(self):
        set_, _a, _b = _make_series_set()
        out = select_from_series_set(
            set_, params=SelectFromSeriesSetParams(series_key="alpha"),
        )
        names = [step.name for step in out.lineage.steps]
        # Each name appears exactly once in this order; if other
        # steps appear (none expected here), only the relative order
        # among these three matters.
        idx_prim = names.index("synthetic_primitive")
        idx_align = names.index("align_series")
        idx_select = names.index("select_from_series_set")
        assert idx_prim < idx_align < idx_select


# ===========================================================================
# 3. Unknown-key refusal
# ===========================================================================


class TestUnknownKey:
    def test_unknown_key_raises_with_available_keys(self):
        set_, _a, _b = _make_series_set()
        with pytest.raises(SelectFromSeriesSetError) as exc_info:
            select_from_series_set(
                set_,
                params=SelectFromSeriesSetParams(series_key="ghost"),
            )
        msg = str(exc_info.value)
        assert "ghost" in msg
        # Diagnostic must list available keys so template authors
        # can spot typos quickly.
        assert "alpha" in msg
        assert "beta" in msg

    def test_select_error_subclasses_value_error(self):
        """SelectFromSeriesSetError must be a ValueError subclass —
        substrate-level error-handling discipline parity with
        align_series."""
        assert issubclass(SelectFromSeriesSetError, ValueError)


# ===========================================================================
# 4. Per-key metadata propagation
# ===========================================================================


class TestPerKeyMetadataPropagation:
    def test_units_propagate_per_key(self):
        set_, a, b = _make_series_set()
        out_a = select_from_series_set(
            set_, params=SelectFromSeriesSetParams(series_key="alpha"),
        )
        out_b = select_from_series_set(
            set_, params=SelectFromSeriesSetParams(series_key="beta"),
        )
        # Each extracted series carries its OWN units, not a
        # collapsed set-wide value.
        assert out_a.units == a.units == TimeSeriesUnits.PERCENT
        assert out_b.units == b.units == TimeSeriesUnits.BPS

    def test_frequency_propagates_from_set(self):
        """Frequency on the output Series comes from the SeriesSet's
        resolved common frequency (the alignment step decides this)."""
        set_, _a, _b = _make_series_set()
        out = select_from_series_set(
            set_, params=SelectFromSeriesSetParams(series_key="alpha"),
        )
        assert out.frequency == set_.frequency

    def test_missingness_propagates_per_key(self):
        set_, _a, _b = _make_series_set()
        out = select_from_series_set(
            set_, params=SelectFromSeriesSetParams(series_key="alpha"),
        )
        assert (
            out.missingness_policy
            == set_.missingness_by_key["alpha"]
        )


# ===========================================================================
# 5. Config-discipline parity
# ===========================================================================


class TestConfigDiscipline:
    def test_wrong_config_name_raises(self, tmp_path: Path):
        from shared.operators.align_series import (
            CONFIG_PATH as OTHER_CONFIG_PATH,
        )
        align_cfg = load_operator_config(OTHER_CONFIG_PATH)
        set_, _a, _b = _make_series_set()
        with pytest.raises(OperatorConfigError, match="config name mismatch"):
            select_from_series_set(
                set_,
                params=SelectFromSeriesSetParams(series_key="alpha"),
                config=align_cfg,
            )


# ===========================================================================
# 6. Registry entry
# ===========================================================================


class TestRegistryEntry:
    def test_registered(self):
        assert "select_from_series_set" in OPERATOR_REGISTRY

    def test_input_slots(self):
        # PART B refactor: input_slots values are SlotDescriptor instances.
        spec = OPERATOR_REGISTRY["select_from_series_set"]
        assert set(spec.input_slots) == {"series_set"}
        assert spec.input_slots["series_set"].artifact_type == "SeriesSet"

    def test_output_type(self):
        # PART B refactor: output is an OutputDescriptor.
        spec = OPERATOR_REGISTRY["select_from_series_set"]
        assert spec.output.artifact_type == "Series"

    def test_callable_resolves_to_operator(self):
        spec = OPERATOR_REGISTRY["select_from_series_set"]
        assert spec.callable is select_from_series_set

    def test_params_class_resolves(self):
        spec = OPERATOR_REGISTRY["select_from_series_set"]
        assert spec.params_class is SelectFromSeriesSetParams


class TestTypedInputGuard:
    """A non-SeriesSet input must fail with the operator's own typed
    error (OPR9/OPR13), not a raw AttributeError on ``.series_by_key``."""

    def test_non_seriesset_input_raises_typed(self):
        not_a_set = _make_series("x")  # a Series, not a SeriesSet
        with pytest.raises(SelectFromSeriesSetError, match="SeriesSet"):
            select_from_series_set(
                not_a_set,
                params=SelectFromSeriesSetParams(series_key="x"),
            )
