"""Tests for shared.operators.series_arithmetic.

Covers:

  - strict unit-algebra contract (every cell in the table from the
    operator's module docstring)
  - arity checks (unary vs binary; index alignment requirement;
    Series-vs-scalar discrimination)
  - payload correctness for every op
  - lineage propagation through the operator step
  - error envelope phrases
  - bundled config loads + the operator wires through to it
  - finance-blindness (operator runs unchanged on synthetic non-rates
    units like Z_SCORE)
  - composition test: align_series → series_arithmetic produces the
    canonical "swap-spread" shape (UST yield − OIS rate, both in
    PERCENT, output in PERCENT) with full lineage threading

Per the v5 plan: unit-incompatibility is a CONTROLLED error
(SeriesArithmeticError, subclass of ValueError) — NOT a silent
coercion.  These tests are written FIRST per the build-plan exit
criterion so a regression to silent coercion fails immediately.
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
from shared.operators.align_series import (
    AlignSeriesParams,
    align_series,
)
from shared.operators.series_arithmetic import (
    CONFIG_PATH,
    SeriesArithmeticParams,
    series_arithmetic,
)
from shared.operators.series_arithmetic.operator import SeriesArithmeticError


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
) -> Series:
    """Build a Series artifact with a synthesised fetch+adapter lineage."""
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
        frequency=None,
        missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        lineage=Lineage.from_steps([fetch, adapter]),
    )


# ===========================================================================
# 1. add / subtract — strict same-unit binary ops
# ===========================================================================


class TestAddSubtract:
    def test_add_matching_units_preserves_units(self):
        a = _series("a", dates=["2026-01-02", "2026-01-05"], values=[1.0, 2.0])
        b = _series("b", dates=["2026-01-02", "2026-01-05"], values=[10.0, 20.0])
        out = series_arithmetic(a, "add", b)
        assert out.units == TimeSeriesUnits.PERCENT
        assert list(out.payload.values) == [11.0, 22.0]

    def test_subtract_matching_units_preserves_units(self):
        a = _series("a", dates=["2026-01-02", "2026-01-05"], values=[10.0, 20.0])
        b = _series("b", dates=["2026-01-02", "2026-01-05"], values=[1.0, 2.0])
        out = series_arithmetic(a, "subtract", b)
        assert out.units == TimeSeriesUnits.PERCENT
        assert list(out.payload.values) == [9.0, 18.0]

    def test_add_different_units_raises(self):
        a = _series("a", dates=["2026-01-02"], values=[1.0],
                    units=TimeSeriesUnits.PERCENT)
        b = _series("b", dates=["2026-01-02"], values=[10.0],
                    units=TimeSeriesUnits.BPS)
        with pytest.raises(SeriesArithmeticError, match="incompatible units"):
            series_arithmetic(a, "add", b)

    def test_subtract_percent_minus_bps_raises(self):
        """Canonical v5-plan failure mode: subtracting BPS from PERCENT
        is exactly the unit-incoherent operation the strict algebra
        must refuse."""
        a = _series("a", dates=["2026-01-02"], values=[4.10],
                    units=TimeSeriesUnits.PERCENT)
        b = _series("b", dates=["2026-01-02"], values=[100.0],
                    units=TimeSeriesUnits.BPS)
        with pytest.raises(SeriesArithmeticError, match="incompatible units"):
            series_arithmetic(a, "subtract", b)

    def test_add_with_scalar_right_raises(self):
        """add/subtract require a Series on the right (a scalar has no
        unit under strict algebra)."""
        a = _series("a", dates=["2026-01-02"], values=[1.0])
        with pytest.raises(SeriesArithmeticError, match="requires a right Series"):
            series_arithmetic(a, "add", 1.0)
        with pytest.raises(SeriesArithmeticError, match="requires a right Series"):
            series_arithmetic(a, "subtract", 5)

    def test_add_with_misaligned_index_raises(self):
        """Operator does NOT align — that's align_series' job.
        Misaligned indexes must produce a controlled error."""
        a = _series("a", dates=["2026-01-02", "2026-01-05"], values=[1.0, 2.0])
        b = _series("b", dates=["2026-01-02", "2026-01-06"], values=[10.0, 20.0])
        with pytest.raises(
            SeriesArithmeticError, match="share an identical DatetimeIndex"
        ):
            series_arithmetic(a, "add", b)


# ===========================================================================
# 2. multiply
# ===========================================================================


class TestMultiply:
    def test_multiply_by_scalar_preserves_units(self):
        a = _series(
            "a", dates=["2026-01-02", "2026-01-05"], values=[1.0, 2.0],
            units=TimeSeriesUnits.PERCENT,
        )
        out = series_arithmetic(a, "multiply", 100)
        # Output units stay PERCENT — strict v1 algebra forbids the
        # "× 100 means percent → bps" silent reinterpretation.
        assert out.units == TimeSeriesUnits.PERCENT
        assert list(out.payload.values) == [100.0, 200.0]

    def test_multiply_by_float_scalar(self):
        a = _series("a", dates=["2026-01-02"], values=[2.5])
        out = series_arithmetic(a, "multiply", 0.5)
        assert out.payload.iloc[0] == 1.25

    def test_multiply_by_series_raises(self):
        """v1 explicitly does not support Series × Series — no unit
        composition rules until ``convert_units`` lands."""
        a = _series("a", dates=["2026-01-02"], values=[1.0])
        b = _series("b", dates=["2026-01-02"], values=[2.0])
        with pytest.raises(
            SeriesArithmeticError, match="two Series is not supported"
        ):
            series_arithmetic(a, "multiply", b)

    def test_multiply_by_non_numeric_raises(self):
        a = _series("a", dates=["2026-01-02"], values=[1.0])
        with pytest.raises(SeriesArithmeticError, match="must be a Series or numeric"):
            series_arithmetic(a, "multiply", "not a number")  # type: ignore[arg-type]


# ===========================================================================
# 3. divide
# ===========================================================================


class TestDivide:
    def test_divide_same_unit_series_yields_ratio(self):
        a = _series(
            "a", dates=["2026-01-02"], values=[100.0],
            units=TimeSeriesUnits.PERCENT,
        )
        b = _series(
            "b", dates=["2026-01-02"], values=[50.0],
            units=TimeSeriesUnits.PERCENT,
        )
        out = series_arithmetic(a, "divide", b)
        # Same-unit division is a unitless RATIO.
        assert out.units == TimeSeriesUnits.RATIO
        assert out.payload.iloc[0] == 2.0

    def test_divide_different_unit_series_raises(self):
        a = _series("a", dates=["2026-01-02"], values=[1.0],
                    units=TimeSeriesUnits.PERCENT)
        b = _series("b", dates=["2026-01-02"], values=[2.0],
                    units=TimeSeriesUnits.BPS)
        with pytest.raises(SeriesArithmeticError, match="matching units"):
            series_arithmetic(a, "divide", b)

    def test_divide_by_scalar_preserves_units(self):
        a = _series(
            "a", dates=["2026-01-02"], values=[100.0],
            units=TimeSeriesUnits.PERCENT,
        )
        out = series_arithmetic(a, "divide", 4)
        assert out.units == TimeSeriesUnits.PERCENT
        assert out.payload.iloc[0] == 25.0

    def test_divide_by_zero_scalar_raises(self):
        a = _series("a", dates=["2026-01-02"], values=[1.0])
        with pytest.raises(SeriesArithmeticError, match="divide. by scalar 0"):
            series_arithmetic(a, "divide", 0)


# ===========================================================================
# 4. diff (unary, units preserved)
# ===========================================================================


class TestDiff:
    def test_diff_default_period_one(self):
        a = _series(
            "a",
            dates=["2026-01-02", "2026-01-05", "2026-01-06"],
            values=[1.0, 3.0, 6.0],
            units=TimeSeriesUnits.PERCENT,
        )
        out = series_arithmetic(a, "diff")
        # diff(1) of [1, 3, 6] is [NaN, 2, 3]
        assert out.units == TimeSeriesUnits.PERCENT
        assert pd.isna(out.payload.iloc[0])
        assert out.payload.iloc[1] == 2.0
        assert out.payload.iloc[2] == 3.0

    def test_diff_explicit_period(self):
        a = _series(
            "a",
            dates=["2026-01-02", "2026-01-05", "2026-01-06"],
            values=[1.0, 3.0, 6.0],
        )
        out = series_arithmetic(
            a, "diff", params=SeriesArithmeticParams(op="diff", period=2),
        )
        # diff(2): rows 0,1 NaN; row 2 = 6 - 1 = 5
        assert pd.isna(out.payload.iloc[0])
        assert pd.isna(out.payload.iloc[1])
        assert out.payload.iloc[2] == 5.0

    def test_diff_with_right_argument_raises(self):
        a = _series("a", dates=["2026-01-02"], values=[1.0])
        with pytest.raises(SeriesArithmeticError, match="is unary"):
            series_arithmetic(a, "diff", 1.0)


# ===========================================================================
# 5. pct_change (unary, output is unitless RATIO)
# ===========================================================================


class TestPctChange:
    def test_pct_change_yields_ratio_units(self):
        a = _series(
            "a",
            dates=["2026-01-02", "2026-01-05", "2026-01-06"],
            values=[100.0, 110.0, 121.0],
            units=TimeSeriesUnits.PERCENT,
        )
        out = series_arithmetic(a, "pct_change")
        # Output units must be RATIO regardless of input units.
        assert out.units == TimeSeriesUnits.RATIO
        assert pd.isna(out.payload.iloc[0])
        # 110/100 - 1 = 0.10
        assert abs(out.payload.iloc[1] - 0.10) < 1e-12
        # 121/110 - 1 = 0.10
        assert abs(out.payload.iloc[2] - 0.10) < 1e-12


# ===========================================================================
# 6. Lineage propagation
# ===========================================================================


class TestLineagePropagation:
    def test_lineage_appends_operator_step(self):
        a = _series("a", dates=["2026-01-02"], values=[1.0])
        b = _series("b", dates=["2026-01-02"], values=[10.0])
        out = series_arithmetic(a, "subtract", b)
        kinds = [s.kind for s in out.lineage.steps]
        # left.lineage was [fetch, adapter]; output is left + this step.
        assert kinds[-1] == "operator"
        assert out.lineage.steps[-1].name == "series_arithmetic"
        assert out.lineage.steps[-1].params["op"] == "subtract"
        assert out.lineage.steps[-1].params["right_kind"] == "series"

    def test_lineage_records_unit_resolution(self):
        a = _series("a", dates=["2026-01-02"], values=[100.0],
                    units=TimeSeriesUnits.PERCENT)
        b = _series("b", dates=["2026-01-02"], values=[50.0],
                    units=TimeSeriesUnits.PERCENT)
        out = series_arithmetic(a, "divide", b)
        params = out.lineage.steps[-1].params
        assert params["left_units"] == "percent"
        assert params["right_units"] == "percent"
        assert params["output_units"] == "ratio"

    def test_lineage_records_scalar_right(self):
        a = _series("a", dates=["2026-01-02"], values=[1.0])
        out = series_arithmetic(a, "multiply", 100)
        params = out.lineage.steps[-1].params
        assert params["right_kind"] == "scalar"
        assert params["right_scalar"] == 100.0

    def test_hash_changes_with_op(self):
        a = _series("a", dates=["2026-01-02"], values=[1.0])
        b = _series("b", dates=["2026-01-02"], values=[10.0])
        h_add = series_arithmetic(a, "add", b).lineage.head_hash
        h_sub = series_arithmetic(a, "subtract", b).lineage.head_hash
        assert h_add != h_sub

    def test_lineage_json_roundtrip(self):
        a = _series("a", dates=["2026-01-02"], values=[100.0],
                    units=TimeSeriesUnits.PERCENT)
        b = _series("b", dates=["2026-01-02"], values=[50.0],
                    units=TimeSeriesUnits.PERCENT)
        out = series_arithmetic(a, "divide", b)
        as_dict = out.lineage.model_dump(mode="json")
        rec = Lineage.model_validate(as_dict)
        assert rec.head_hash == out.lineage.head_hash
        assert len(rec.steps) == len(out.lineage.steps)


# ===========================================================================
# 7. Bundled config + admission
# ===========================================================================


class TestBundledConfig:
    def test_config_path_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads_with_correct_identity(self):
        cfg = load_operator_config(CONFIG_PATH)
        assert cfg.operator.name == "series_arithmetic"
        assert cfg.operator.method_family == "arithmetic"

    def test_operator_uses_bundled_config_when_omitted(self):
        a = _series(
            "a",
            dates=["2026-01-02", "2026-01-05", "2026-01-06"],
            values=[1.0, 3.0, 6.0],
        )
        # diff with no params — period must default to 1 from the YAML.
        out = series_arithmetic(a, "diff")
        params = out.lineage.steps[-1].params
        assert params["period"] == 1


class TestParamsArgInteraction:
    def test_positional_op_must_match_params_op(self):
        a = _series("a", dates=["2026-01-02"], values=[1.0])
        with pytest.raises(SeriesArithmeticError, match="disagrees"):
            series_arithmetic(
                a, "diff",
                params=SeriesArithmeticParams(op="pct_change"),
            )


# ===========================================================================
# 8. Finance-blindness (admission criterion)
# ===========================================================================


class TestFinanceBlindness:
    def test_runs_unchanged_on_z_score_input(self):
        """``Z_SCORE`` is a non-rates-y unit; the operator's
        same-units-must-match rule cleanly accepts two Z_SCORE series."""
        a = _series(
            "a", dates=["2026-01-02", "2026-01-05"], values=[1.5, 2.0],
            units=TimeSeriesUnits.Z_SCORE,
        )
        b = _series(
            "b", dates=["2026-01-02", "2026-01-05"], values=[0.5, 1.0],
            units=TimeSeriesUnits.Z_SCORE,
        )
        out = series_arithmetic(a, "subtract", b)
        assert out.units == TimeSeriesUnits.Z_SCORE
        assert list(out.payload.values) == [1.0, 1.0]


# ===========================================================================
# 9. Composition: align_series → series_arithmetic (canonical Q1 step)
# ===========================================================================


class TestComposition:
    def test_align_then_subtract_canonical_swap_spread_shape(self):
        """The literal first-half of Q1's data path: align two series,
        subtract, get a swap-spread Series in PERCENT with full
        lineage covering both upstream chains."""
        ust_2y = _series(
            "ust_2y",
            dates=["2026-01-02", "2026-01-05", "2026-01-06"],
            values=[4.50, 4.55, 4.52],
            units=TimeSeriesUnits.PERCENT,
        )
        ois_2y = _series(
            "ois_2y",
            dates=["2026-01-02", "2026-01-05", "2026-01-06"],
            values=[4.30, 4.32, 4.31],
            units=TimeSeriesUnits.PERCENT,
        )
        aligned = align_series([ust_2y, ois_2y], AlignSeriesParams())
        spread = series_arithmetic(
            aligned.get_series("ust_2y"),
            "subtract",
            aligned.get_series("ois_2y"),
        )
        # Math
        np.testing.assert_allclose(
            list(spread.payload.values),
            [0.20, 0.23, 0.21],
            rtol=1e-9,
        )
        # Units preserved
        assert spread.units == TimeSeriesUnits.PERCENT
        # Lineage covers fetch + adapter (left) + alignment (from
        # get_series) + arithmetic.  This is what makes a
        # programmatic methodology summary possible later.
        kinds = [s.kind for s in spread.lineage.steps]
        assert kinds == ["fetch", "adapter", "operator", "operator"]
        assert spread.lineage.steps[-2].name == "align_series"
        assert spread.lineage.steps[-1].name == "series_arithmetic"

    def test_align_then_pct_change_unitless_output(self):
        a = _series(
            "a",
            dates=["2026-01-02", "2026-01-05", "2026-01-06"],
            values=[100.0, 110.0, 121.0],
        )
        b = _series(
            "b",
            dates=["2026-01-02", "2026-01-05", "2026-01-06"],
            values=[50.0, 55.0, 60.5],
        )
        aligned = align_series([a, b])
        out = series_arithmetic(aligned.get_series("a"), "pct_change")
        assert out.units == TimeSeriesUnits.RATIO
        # Series methodology summary should expose the unit transition.
        params = out.lineage.steps[-1].params
        assert params["left_units"] == "percent"
        assert params["output_units"] == "ratio"
