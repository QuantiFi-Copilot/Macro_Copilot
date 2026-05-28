"""tests/test_operator_rolling_zscore_panel.py — rolling-normalization operator.

Covers the ``rolling_zscore_panel`` operator (finance-blind rolling
z-score over a typed Panel):

  1. Bundled config loads + name-checks; defaults match design
     (min_periods=60, ddof=1).
  2. Schema validators: window >= min_periods, ddof < window.
  3. Known-value z-score: window=3, ddof=1, column [10, 20, 30] →
     last row z = (30 - 20) / 10 = 1.0 exactly; earlier rows NaN
     (min_periods gating).
  4. Output Panel shape: same index + columns; units = Z_SCORE for
     every column regardless of input unit.
  5. Zero-variance column → NaN at constant rows (never +/-inf).
  6. FINANCE-BLINDNESS: an FX-like panel (EURUSD/USDJPY) and a
     Rates-like panel (US_2Y/US_10Y) with IDENTICAL numeric content
     produce numerically identical z-scores — the operator does not
     know or care about asset class.
  7. Lineage: output head = rolling_zscore_panel step; input lineage
     preserved as the parent; input_units recorded in step params.
  8. Empty panel (zero columns / zero rows) → RollingZscorePanelError.
  9. Config name-mismatch → OperatorConfigError.

Synthetic Panel fixtures only — NO dependency on any agent's DB or on
the un-merged FX stack (fx_vol_panel etc. are NOT on `build`).  The
fixtures deliberately use both an FX-like and a Rates-like column set
to prove the operator is asset-agnostic.
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
from shared.operators.rolling_zscore_panel import (
    CONFIG_PATH,
    rolling_zscore_panel,
    RollingZscorePanelError,
    RollingZscorePanelParams,
)


# ---------------------------------------------------------------------------
# Helpers — synthetic Panel construction (no DB, no FX-stack dependency)
# ---------------------------------------------------------------------------


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
    assert cfg.operator.name == "rolling_zscore_panel"
    assert cfg.operator.method_family == "aggregation"
    assert cfg.default_value("min_periods") == 60
    assert cfg.default_value("ddof") == 1


# ---------------------------------------------------------------------------
# 2. Schema validators
# ---------------------------------------------------------------------------


def test_window_must_be_ge_min_periods():
    with pytest.raises(ValueError):
        RollingZscorePanelParams(window=10, min_periods=20)


def test_ddof_must_be_lt_window():
    with pytest.raises(ValueError):
        RollingZscorePanelParams(window=2, min_periods=2, ddof=2)


# ---------------------------------------------------------------------------
# 3. Known-value z-score
# ---------------------------------------------------------------------------


def test_known_value_zscore_last_row():
    idx = _bdays(3)
    df = pd.DataFrame({"X": [10.0, 20.0, 30.0]}, index=idx)
    out = rolling_zscore_panel(_panel(df), RollingZscorePanelParams(window=3, min_periods=3, ddof=1))
    z = out.payload["X"]
    # window [10,20,30]: mean=20, sample std (ddof=1)=10 → z=(30-20)/10=1.0
    assert pytest.approx(z.iloc[-1], abs=1e-9) == 1.0
    # first 2 rows below min_periods → NaN
    assert np.isnan(z.iloc[0])
    assert np.isnan(z.iloc[1])


# ---------------------------------------------------------------------------
# 4. Output Panel shape + units
# ---------------------------------------------------------------------------


def test_output_units_all_zscore_and_shape_preserved():
    idx = _bdays(80)
    rng = np.random.default_rng(7)
    df = pd.DataFrame(
        {"A": rng.normal(0, 1, 80).cumsum(), "B": rng.normal(0, 1, 80).cumsum()},
        index=idx,
    )
    # mixed input units to prove they're discarded → Z_SCORE
    out = rolling_zscore_panel(
        _panel(df, units={"A": TimeSeriesUnits.PERCENT, "B": TimeSeriesUnits.BPS}),
        RollingZscorePanelParams(window=20, min_periods=20, ddof=1),
    )
    assert list(out.payload.columns) == ["A", "B"]
    assert out.payload.index.equals(idx)
    assert out.units_by_column == {
        "A": TimeSeriesUnits.Z_SCORE,
        "B": TimeSeriesUnits.Z_SCORE,
    }


# ---------------------------------------------------------------------------
# 5. Zero-variance column → NaN, never inf
# ---------------------------------------------------------------------------


def test_constant_column_yields_nan_not_inf():
    idx = _bdays(10)
    df = pd.DataFrame({"FLAT": [5.0] * 10}, index=idx)
    out = rolling_zscore_panel(_panel(df), RollingZscorePanelParams(window=5, min_periods=5, ddof=1))
    z = out.payload["FLAT"]
    assert not np.isinf(z.to_numpy()).any()
    # every in-window row has std==0 → NaN
    assert z.iloc[-1] != z.iloc[-1] or np.isnan(z.iloc[-1])


# ---------------------------------------------------------------------------
# 6. Finance-blindness — FX-like vs Rates-like identical content
# ---------------------------------------------------------------------------


def test_finance_blind_fx_like_equals_rates_like():
    idx = _bdays(120)
    rng = np.random.default_rng(42)
    a = rng.normal(0, 1, 120).cumsum()
    b = rng.normal(0, 1, 120).cumsum()
    params = RollingZscorePanelParams(window=60, min_periods=60, ddof=1)

    fx_df = pd.DataFrame({"EURUSD": a, "USDJPY": b}, index=idx)
    rates_df = pd.DataFrame({"US_2Y": a, "US_10Y": b}, index=idx)

    fx_out = rolling_zscore_panel(_panel(fx_df), params)
    rates_out = rolling_zscore_panel(_panel(rates_df), params)

    # Same numeric content under different (asset-class) column names →
    # numerically identical z-scores. The operator is asset-blind.
    np.testing.assert_allclose(
        fx_out.payload["EURUSD"].to_numpy(),
        rates_out.payload["US_2Y"].to_numpy(),
        equal_nan=True,
    )
    np.testing.assert_allclose(
        fx_out.payload["USDJPY"].to_numpy(),
        rates_out.payload["US_10Y"].to_numpy(),
        equal_nan=True,
    )


# ---------------------------------------------------------------------------
# 7. Lineage
# ---------------------------------------------------------------------------


def test_lineage_head_is_operator_step_and_parent_preserved():
    idx = _bdays(80)
    df = pd.DataFrame({"X": np.arange(80, dtype=float)}, index=idx)
    in_panel = _panel(df, units={"X": TimeSeriesUnits.PERCENT})
    parent_head = in_panel.lineage.head_hash
    out = rolling_zscore_panel(in_panel, RollingZscorePanelParams(window=20, min_periods=20))
    steps = out.lineage.steps
    assert steps[-1].kind == "operator"
    assert steps[-1].name == "rolling_zscore_panel"
    # input units recorded for provenance
    assert steps[-1].params["input_units_by_column"] == {"X": "percent"}
    # parent lineage preserved beneath the operator step
    assert parent_head in [s.hash for s in steps[:-1]]


# ---------------------------------------------------------------------------
# 8. Empty panel → typed error
# ---------------------------------------------------------------------------


def test_empty_columns_raises():
    idx = _bdays(5)
    df = pd.DataFrame(index=idx)
    with pytest.raises(RollingZscorePanelError):
        rolling_zscore_panel(_panel(df), RollingZscorePanelParams(window=3, min_periods=3))


def test_empty_rows_raises():
    df = pd.DataFrame({"X": []}, index=pd.DatetimeIndex([]))
    with pytest.raises(RollingZscorePanelError):
        rolling_zscore_panel(_panel(df), RollingZscorePanelParams(window=3, min_periods=3))


# ---------------------------------------------------------------------------
# 9. Config name-mismatch
# ---------------------------------------------------------------------------


def test_wrong_config_name_raises(tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text(
        "operator:\n"
        "  name: not_rolling_zscore_panel\n"
        "  method_family: aggregation\n"
        '  version: "1.0.0"\n'
        "  description: wrong operator\n"
        "defaults: {}\n"
        "methodology:\n"
        "  what_it_does: wrong\n"
    )
    cfg = load_operator_config(bad)
    idx = _bdays(5)
    df = pd.DataFrame({"X": [1.0, 2, 3, 4, 5]}, index=idx)
    with pytest.raises(OperatorConfigError):
        rolling_zscore_panel(_panel(df), RollingZscorePanelParams(window=3, min_periods=3), config=cfg)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
