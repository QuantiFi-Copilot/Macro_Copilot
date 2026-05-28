"""tests/test_operator_cross_sectional_rank.py — ranking-family operator.

Covers the ``cross_sectional_rank`` operator (finance-blind ranking of
the columns of a typed Panel within each row / date):

  1. Bundled config loads + name-checks; method_family = ranking;
     defaults match design (method=rank, ascending=True, tie=average).
  2. Known-value rank: row [10, 30, 20] ascending → [1, 3, 2];
     descending → [3, 1, 2].
  3. Output units per method: rank→COUNT, percentile→PCT_RANK
     (values in [0,100]), normalized→RATIO (values in [0,1]).
  4. NaN cells stay unranked (na_option='keep') and don't consume a
     rank slot.
  5. FINANCE-BLINDNESS: an FX-like panel (EURUSD/USDJPY/GBPUSD) and a
     Rates-like panel (US_2Y/US_10Y/US_30Y) with identical numeric
     content produce numerically identical ranks.
  6. Lineage: output head = cross_sectional_rank step; output_unit +
     input_units recorded in step params; parent preserved.
  7. Degenerate inputs: < 2 columns and zero rows raise
     CrossSectionalRankError.
  8. Config name-mismatch → OperatorConfigError.

Synthetic Panel fixtures only — NO dependency on any DB or the
un-merged FX stack.  Proves asset-agnosticism explicitly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.artifacts.lineage import Lineage, PrimitiveStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfigError,
    load_operator_config,
)
from shared.operators.cross_sectional_rank import (
    CONFIG_PATH,
    cross_sectional_rank,
    CrossSectionalRankError,
    CrossSectionalRankParams,
)


def _panel(df: pd.DataFrame, units: dict | None = None) -> Panel:
    cols = list(df.columns)
    units_by_column = units or {c: TimeSeriesUnits.PERCENT for c in cols}
    step = PrimitiveStep.build(
        name="synthetic_panel",
        version="1.0.0",
        params={"cols": cols},
        tool_config_hash="test_config_hash",
        output_field="panel",
        as_of_date="2025-01-01",
    )
    return Panel(
        payload=df,
        units_by_column=units_by_column,
        missingness_policy=RawNoCleaning(),
        lineage=Lineage.from_steps([step]),
    )


def _bdays(n: int, start: str = "2024-01-01") -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=n)


# ---------------------------------------------------------------------------
# 1. Config
# ---------------------------------------------------------------------------


def test_config_loads_and_name_checks():
    cfg = load_operator_config(CONFIG_PATH)
    assert cfg.operator.name == "cross_sectional_rank"
    assert cfg.operator.method_family == "ranking"
    assert cfg.default_value("method") == "rank"
    assert cfg.default_value("ascending") is True
    assert cfg.default_value("tie_method") == "average"


# ---------------------------------------------------------------------------
# 2. Known-value rank — direction
# ---------------------------------------------------------------------------


def test_rank_ascending_and_descending():
    df = pd.DataFrame({"A": [10.0], "B": [30.0], "C": [20.0]}, index=_bdays(1))

    asc = cross_sectional_rank(
        _panel(df), CrossSectionalRankParams(method="rank", ascending=True)
    ).payload.iloc[0]
    assert (asc["A"], asc["B"], asc["C"]) == (1.0, 3.0, 2.0)

    desc = cross_sectional_rank(
        _panel(df), CrossSectionalRankParams(method="rank", ascending=False)
    ).payload.iloc[0]
    assert (desc["A"], desc["B"], desc["C"]) == (3.0, 1.0, 2.0)


# ---------------------------------------------------------------------------
# 3. Units per method + value ranges
# ---------------------------------------------------------------------------


def test_method_units_and_ranges():
    df = pd.DataFrame({"A": [10.0], "B": [30.0], "C": [20.0]}, index=_bdays(1))

    out_rank = cross_sectional_rank(_panel(df), CrossSectionalRankParams(method="rank"))
    assert set(out_rank.units_by_column.values()) == {TimeSeriesUnits.COUNT}

    out_pct = cross_sectional_rank(_panel(df), CrossSectionalRankParams(method="percentile"))
    assert set(out_pct.units_by_column.values()) == {TimeSeriesUnits.PCT_RANK}
    assert out_pct.payload.iloc[0].between(0, 100).all()

    out_norm = cross_sectional_rank(_panel(df), CrossSectionalRankParams(method="normalized"))
    assert set(out_norm.units_by_column.values()) == {TimeSeriesUnits.RATIO}
    assert out_norm.payload.iloc[0].between(0, 1).all()


# ---------------------------------------------------------------------------
# 4. NaN handling
# ---------------------------------------------------------------------------


def test_nan_cells_unranked():
    df = pd.DataFrame({"A": [10.0], "B": [np.nan], "C": [20.0]}, index=_bdays(1))
    out = cross_sectional_rank(_panel(df), CrossSectionalRankParams(method="rank", ascending=True)).payload.iloc[0]
    assert out["A"] == 1.0
    assert np.isnan(out["B"])
    assert out["C"] == 2.0


# ---------------------------------------------------------------------------
# 5. Finance-blindness
# ---------------------------------------------------------------------------


def test_finance_blind_fx_like_equals_rates_like():
    mat = np.array([[10.0, 30.0, 20.0], [40.0, 10.0, 25.0]])
    idx = _bdays(2)
    params = CrossSectionalRankParams(method="rank", ascending=False)

    fx = cross_sectional_rank(
        _panel(pd.DataFrame(mat, columns=["EURUSD", "USDJPY", "GBPUSD"], index=idx)), params
    )
    rates = cross_sectional_rank(
        _panel(pd.DataFrame(mat, columns=["US_2Y", "US_10Y", "US_30Y"], index=idx)), params
    )
    np.testing.assert_allclose(
        fx.payload.to_numpy(), rates.payload.to_numpy(), equal_nan=True
    )


# ---------------------------------------------------------------------------
# 6. Lineage
# ---------------------------------------------------------------------------


def test_lineage_head_and_provenance():
    df = pd.DataFrame({"A": [10.0, 40], "B": [30.0, 10], "C": [20.0, 25]}, index=_bdays(2))
    in_panel = _panel(df, units={"A": TimeSeriesUnits.PERCENT, "B": TimeSeriesUnits.PERCENT, "C": TimeSeriesUnits.PERCENT})
    parent_head = in_panel.lineage.head_hash
    out = cross_sectional_rank(in_panel, CrossSectionalRankParams(method="rank"))
    steps = out.lineage.steps
    assert steps[-1].kind == "operator"
    assert steps[-1].name == "cross_sectional_rank"
    assert steps[-1].params["output_unit"] == "count"
    assert parent_head in [s.hash for s in steps[:-1]]


# ---------------------------------------------------------------------------
# 7. Degenerate inputs
# ---------------------------------------------------------------------------


def test_single_column_raises():
    df = pd.DataFrame({"X": [1.0, 2.0, 3.0]}, index=_bdays(3))
    with pytest.raises(CrossSectionalRankError):
        cross_sectional_rank(_panel(df), CrossSectionalRankParams())


def test_zero_rows_raises():
    df = pd.DataFrame({"A": [], "B": []}, index=pd.DatetimeIndex([]))
    with pytest.raises(CrossSectionalRankError):
        cross_sectional_rank(_panel(df), CrossSectionalRankParams())


# ---------------------------------------------------------------------------
# 8. Config name-mismatch
# ---------------------------------------------------------------------------


def test_wrong_config_name_raises(tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text(
        "operator:\n"
        "  name: not_cross_sectional_rank\n"
        "  method_family: ranking\n"
        '  version: "1.0.0"\n'
        "  description: wrong operator\n"
        "defaults: {}\n"
        "methodology:\n"
        "  what_it_does: wrong\n"
    )
    cfg = load_operator_config(bad)
    df = pd.DataFrame({"A": [1.0], "B": [2.0]}, index=_bdays(1))
    with pytest.raises(OperatorConfigError):
        cross_sectional_rank(_panel(df), CrossSectionalRankParams(), config=cfg)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
